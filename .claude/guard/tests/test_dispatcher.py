"""The dispatcher's answer to one hook event: the rules that name it run, their verdicts combined, every fault and
every call the kit cannot read refused, each switch obeyed. The cases of Combining, Faults, Switches and Gaps call
core.answer in this process with rules written to a scratch folder; those of Entry run dispatcher/dispatch.py as the
harness runs it, a child process reading the event on stdin."""

import json
import os
import resource
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType
from unittest import mock

from declarations.lookup import MACHINE_FILE, Declarations
from dispatcher.core import (Answer, EventBytes, FolderPath, RuleEntry, RuleFile, Settings, answer, fault_text,
                             rule_files)
from dispatcher.model import FOREIGN_FORM, GAP_FORM, ReadSettings
from dispatcher.tree import PARSE_FORM, ReaderPath, Seconds, ShellText
from dispatcher.verdict import KitFault, Prose, RuleName
from dispatcher.words import VarName
from tests.test_declarations import NONE_DECLARED, TomlSource, machine_text

KIT = Path(__file__).resolve().parents[1]
ENTRY = KIT / "dispatcher" / "dispatch.py"
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RuleSource = NewType("RuleSource", str)
ExitCode = NewType("ExitCode", int)
Field = NewType("Field", str)
type Fields = dict[Field, ShellText | bool | dict[Field, ShellText]]

HEAD = "from dispatcher.verdict import Prose, Verdict, context, refuse\n"


def rule(events: ShellText, tools: ShellText, body: ShellText) -> RuleSource:
    """A rule module judging ``events`` and ``tools`` (Python expressions) whose judge runs ``body``."""
    return RuleSource(f"{HEAD}EVENTS = frozenset({events})\nTOOLS = {tools}\n\ndef judge(event):\n    {body}\n")


REFUSES = rule(ShellText('{"PreToolUse"}'), ShellText("None"), ShellText('return refuse(Prose("no, says R"))'))
NOTES = rule(ShellText('{"PreToolUse", "Stop"}'), ShellText("None"), ShellText('return context(Prose("a note"))'))
PASSES = rule(ShellText('{"PreToolUse"}'), ShellText("None"), ShellText("return None"))
NEEDS_MODEL = rule(ShellText('{"PreToolUse", "PostToolUse"}'), ShellText('frozenset({"Bash"})'),
                   ShellText("return None if event.call() else None"))
ASKS_ANY_TOOL = rule(ShellText('{"PreToolUse"}'), ShellText("None"), ShellText("return None if event.call() else None"))


PRE_TOOL_USE = ShellText("PreToolUse")
BASH = ShellText("Bash")
TRUE = ShellText("true")
READ_TIMEOUT = Seconds(5.0)
PASSED = Answer(0, Prose(""), Prose(""))
# The switch of the route check, which refuses every Bash and Monitor call whose environment names no claude-call
# wrapper: the cases running the kit's own rules hold the dispatcher, not the route, so they switch it off.
ROUTE_CHECK = "route-check"


def event(name: ShellText = PRE_TOOL_USE, tool: ShellText | None = BASH, command: ShellText = TRUE,
          **more: ShellText | bool) -> EventBytes:
    """A hook event's bytes: a ``tool`` call of ``command`` at ``name``, with ``more`` fields."""
    fields: Fields = {Field("hook_event_name"): name, Field("cwd"): ShellText("/r"), Field("session_id"): ShellText("s")}
    if tool is not None:
        fields[Field("tool_name")] = tool
        fields[Field("tool_input")] = {Field("command"): command}
    fields.update({Field(key): value for key, value in more.items()})
    return EventBytes(json.dumps(fields).encode())


def file_call(tool: ShellText, path: Path) -> EventBytes:
    """A PreToolUse event's bytes: a ``tool`` call naming the file ``path``, from /r."""
    fields: Fields = {Field("hook_event_name"): PRE_TOOL_USE, Field("cwd"): ShellText("/r"),
                      Field("session_id"): ShellText("s"), Field("tool_name"): tool,
                      Field("tool_input"): {Field("file_path"): ShellText(str(path))}}
    return EventBytes(json.dumps(fields).encode())


def machine_home(case: unittest.TestCase, text: TomlSource | None = NONE_DECLARED) -> Path:
    """A home folder of the case's own, ``text`` its machine file, none where None."""
    home = Path(tempfile.mkdtemp()).resolve()
    case.addCleanup(lambda: subprocess.run(["rm", "-rf", str(home)], check=True))
    if text is not None:
        (home / MACHINE_FILE).parent.mkdir(parents=True)
        _ = (home / MACHINE_FILE).write_text(text, encoding="utf-8")
    return home


class Kit(NamedTuple):
    """A scratch folder holding a rules folder and a switch folder, and the home its declarations are read from."""

    rules: Path
    off: Path
    home: Path

    def settings(self, reader: ReaderPath = READER, timeout: Seconds = READ_TIMEOUT) -> Settings:
        """Settings reading this kit's rules, switches and home, the project root /r."""
        env = {VarName("CLAUDE_PROJECT_DIR"): ShellText("/r"), VarName("HOME"): ShellText("/home/u")}
        return Settings(FolderPath(str(self.rules)), FolderPath(str(self.off)), ReadSettings(reader, timeout, env),
                        self.home)

    def judge(self, raw: EventBytes, reader: ReaderPath = READER) -> Answer:
        """The dispatcher's answer to ``raw`` under this kit."""
        return answer(raw, self.settings(reader))

    def switch(self, name: RuleFile) -> None:
        """Switch ``name`` off."""
        (self.off / name).touch()


class KitCase(unittest.TestCase):
    """A case with a scratch kit of its own."""

    def make(self, rules: dict[RuleFile, RuleSource]) -> Kit:
        """A kit holding ``rules``, its home holding a machine file declaring every fact absent."""
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(scratch)], check=True))
        kit = Kit(scratch / "rules", scratch / "off", machine_home(self))
        kit.rules.mkdir()
        kit.off.mkdir()
        for name, source in rules.items():
            _ = (kit.rules / name).write_text(source, encoding="utf-8")
        return kit


class Combining(KitCase):
    """Every rule that names the event and the tool runs, and their verdicts make one answer."""

    def test_a_refusal(self) -> None:
        got = self.make({RuleFile("r.py"): REFUSES, RuleFile("p.py"): PASSES}).judge(event())
        self.assertEqual(got, Answer(2, Prose(""), Prose("guard r: no, says R\n")))

    def test_notes_alone(self) -> None:
        got = self.make({RuleFile("a.py"): NOTES, RuleFile("b.py"): NOTES}).judge(event())
        self.assertEqual((got.status, got.stderr), (0, ""))
        self.assertEqual(json.loads(got.stdout), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "additionalContext": "guard a: a note\n\nguard b: a note"}})

    def test_a_refusal_carries_the_notes(self) -> None:
        got = self.make({RuleFile("a.py"): NOTES, RuleFile("r.py"): REFUSES}).judge(event())
        self.assertEqual(got, Answer(2, Prose(""), Prose("guard r: no, says R\n\nguard a: a note\n")))

    def test_nothing_to_say(self) -> None:
        self.assertEqual(self.make({RuleFile("p.py"): PASSES}).judge(event()), Answer(0, Prose(""), Prose("")))

    def test_a_rule_runs_only_for_its_events_and_tools(self) -> None:
        kit = self.make({
            RuleFile("later.py"): rule(ShellText('{"PostToolUse"}'), ShellText("None"),
                                       ShellText('return refuse(Prose("x"))')),
            RuleFile("writes.py"): rule(ShellText('{"PreToolUse"}'), ShellText('frozenset({"Write"})'),
                                        ShellText('return refuse(Prose("x"))')),
            RuleFile("bash.py"): rule(ShellText('{"PreToolUse"}'), ShellText('frozenset({"Bash"})'),
                                      ShellText('return refuse(Prose("x"))'))})
        self.assertEqual(kit.judge(event()).stderr, "guard bash: x\n")
        self.assertEqual(kit.judge(event(ShellText("PostToolUse"))).stderr, "guard later: x\n")

    def test_a_note_names_its_event(self) -> None:
        notes = rule(ShellText('{"PostToolUse"}'), ShellText("None"), ShellText('return context(Prose("a note"))'))
        got = self.make({RuleFile("a.py"): notes}).judge(event(ShellText("PostToolUse")))
        self.assertEqual(json.loads(got.stdout), {"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": "guard a: a note"}})

    def test_tools_none_judges_an_event_with_no_tool(self) -> None:
        got = self.make({RuleFile("a.py"): NOTES}).judge(event(ShellText("Stop"), None))
        self.assertEqual(json.loads(got.stdout), {"systemMessage": "guard a: a note"})

    def test_an_agents_event_is_held_alike(self) -> None:
        got = self.make({RuleFile("r.py"): REFUSES}).judge(event(agent_id=ShellText("a1"),
                                                                 agent_type=ShellText("workflow-subagent")))
        self.assertEqual(got.status, 2)


class Faults(KitCase):
    """A fault of the kit, a rule's or the reader's, and a call the kit cannot read, each refuse, naming it; at a Stop
    or SubagentStop a Stop hook has kept going, each is a note instead."""

    def test_a_fault_at_a_stop_refuses_once_then_is_a_note(self) -> None:
        stops = ShellText('{"Stop", "SubagentStop"}')
        kit = self.make({RuleFile("boom.py"): rule(stops, ShellText("None"), ShellText("raise ValueError('bad')"))})
        said = fault_text(Prose("the rule boom failed: ValueError: bad"))
        for name in (ShellText("Stop"), ShellText("SubagentStop")):
            for active in (False, ShellText("true"), None):
                with self.subTest(name=name, active=active):
                    more = {} if active is None else {"stop_hook_active": active}
                    self.assertEqual(kit.judge(event(name, None, **more)), Answer(2, Prose(""), Prose(f"{said}\n")))
            with self.subTest(name=name, active=True):
                got = kit.judge(event(name, None, stop_hook_active=True))
                self.assertEqual((got.status, got.stderr), (0, ""))
                self.assertEqual(json.loads(got.stdout), {"systemMessage": said})

    def test_every_fault_at_a_stop_kept_going_is_in_its_one_message(self) -> None:
        stops = ShellText('{"Stop"}')
        kit = self.make({RuleFile("one.py"): rule(stops, ShellText("None"), ShellText("raise ValueError('a')")),
                         RuleFile("two.py"): rule(stops, ShellText("None"), ShellText("raise ValueError('b')"))})
        got = kit.judge(event(ShellText("Stop"), None, stop_hook_active=True))
        said = [fault_text(Prose(f"the rule {name} failed: ValueError: {text}")) for name, text in (("one", "a"),
                                                                                                    ("two", "b"))]
        self.assertEqual((got.status, json.loads(got.stdout)), (0, {"systemMessage": "\n\n".join(said)}))

    def test_a_rule_that_cannot_load_or_is_unreadable_at_a_stop_kept_going_is_a_note(self) -> None:
        unreadable = ShellText("from dispatcher.verdict import Unreadable\n    raise Unreadable(Prose('g'), Prose('f'))")
        for source, said in {RuleSource("def judge(:\n"): "the rule bad cannot be loaded: SyntaxError",
                             rule(ShellText('{"Stop"}'), ShellText("None"), unreadable):
                                 "guard kit: this call cannot be read: g.\nf"}.items():
            with self.subTest(said=said):
                got = self.make({RuleFile("bad.py"): source}).judge(event(ShellText("Stop"), None,
                                                                          stop_hook_active=True))
                self.assertEqual((got.status, got.stderr), (0, ""))
                self.assertIn(said, json.loads(got.stdout)["systemMessage"])

    def test_a_rules_refusal_at_a_stop_kept_going_still_refuses(self) -> None:
        stop = ShellText('{"Stop"}')
        kit = self.make({RuleFile("boom.py"): rule(stop, ShellText("None"), ShellText("raise ValueError('bad')")),
                         RuleFile("r.py"): rule(stop, ShellText("None"), ShellText('return refuse(Prose("no, says R"))'))})
        said = fault_text(Prose("the rule boom failed: ValueError: bad"))
        self.assertEqual(kit.judge(event(ShellText("Stop"), None, stop_hook_active=True)),
                         Answer(2, Prose(""), Prose(f"guard r: no, says R\n\n{said}\n")))

    def test_a_fault_at_pre_tool_use_refuses_whatever_stop_hook_active_says(self) -> None:
        kit = self.make({RuleFile("boom.py"): rule(ShellText('{"PreToolUse"}'), ShellText("None"),
                                                   ShellText("raise ValueError('bad')"))})
        got = kit.judge(event(stop_hook_active=True))
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: the rule boom failed: ValueError: bad.", got.stderr)

    def test_a_rule_that_raises(self) -> None:
        kit = self.make({RuleFile("boom.py"): rule(ShellText('{"PreToolUse"}'), ShellText("None"),
                                                   ShellText("raise ValueError('bad')")),
                         RuleFile("r.py"): REFUSES})
        got = kit.judge(event())
        self.assertEqual(got.status, 2)
        self.assertIn("guard r: no, says R", got.stderr)
        self.assertIn("guard kit: the rule boom failed: ValueError: bad. The call is refused", got.stderr)

    def test_a_rule_that_cannot_load_or_declares_wrongly(self) -> None:
        cases = {
            RuleSource("def judge(:\n"): "the rule bad cannot be loaded: SyntaxError",
            RuleSource("TOOLS = None\ndef judge(event):\n    return None\n"): "does not declare EVENTS",
            RuleSource('EVENTS = frozenset({"Nope"})\nTOOLS = None\ndef judge(e):\n    return None\n'):
                "does not declare EVENTS",
            RuleSource('EVENTS = frozenset({"PreToolUse"})\nTOOLS = ["Bash"]\ndef judge(e):\n    return None\n'):
                "does not declare EVENTS",
            RuleSource('EVENTS = frozenset({"PreToolUse"})\nTOOLS = None\njudge = 1\n'): "does not declare EVENTS",
            rule(ShellText('{"PreToolUse"}'), ShellText("None"), ShellText('return "no"')): "not a Verdict",
            rule(ShellText('{"PreToolUse"}'), ShellText("None"), ShellText('return Verdict("x", Prose("y"))')):
                "not a Verdict",
        }
        for source, said in cases.items():
            with self.subTest(source=source):
                got = self.make({RuleFile("bad.py"): source}).judge(event())
                self.assertEqual(got.status, 2)
                self.assertIn(said, got.stderr)

    def test_a_reserved_name(self) -> None:
        got = self.make({RuleFile("all.py"): PASSES, RuleFile("_helper.py"): RuleSource("raise ImportError\n"),
                         RuleFile("notes.txt"): RuleSource("x")}).judge(event())
        self.assertEqual((got.status, got.stderr.count("guard kit:")), (2, 1))
        self.assertIn("the rule file all.py takes a switch name the kit reserves", got.stderr)

    def test_an_event_that_cannot_be_read(self) -> None:
        kit = self.make({RuleFile("p.py"): PASSES})
        for raw, said in ((b"not json", "the hook event is not JSON"), (b"[1]", "names no hook event"),
                          (b'{"hook_event_name": "Nope"}', "names no hook event"),
                          (b'{"hook_event_name": "PreToolUse", "tool_name": "Bash"}', "no command text")):
            with self.subTest(raw=raw):
                got = kit.judge(EventBytes(raw))
                self.assertEqual(got.status, 2)
                self.assertIn(said, got.stderr)

    def test_an_unreadable_call_is_refused_with_no_rule(self) -> None:
        for tool in (ShellText("Bash"), ShellText("Monitor")):
            with self.subTest(tool=tool):
                got = self.make({}).judge(event(tool=tool, command=ShellText("echo ${=x}")))
                self.assertEqual(got.status, 2)
                self.assertIn("guard kit: this call cannot be read: bash cannot parse it: ", got.stderr)
                self.assertIn(PARSE_FORM, got.stderr)

    def test_the_model_is_built_only_on_ask_after_the_call(self) -> None:
        kit = self.make({RuleFile("p.py"): PASSES})
        self.assertEqual(kit.judge(event(ShellText("PostToolUse"), command=ShellText("echo ${=x}"))).status, 0)
        kit = self.make({RuleFile("m.py"): NEEDS_MODEL})
        got = kit.judge(event(ShellText("PostToolUse"), command=ShellText("echo ${=x}")))
        self.assertEqual(got.stderr.count("this call cannot be read"), 1)

    def test_the_unreadable_refusal_comes_once(self) -> None:
        got = self.make({RuleFile("m.py"): NEEDS_MODEL, RuleFile("n.py"): NEEDS_MODEL}).judge(
            event(ShellText("PostToolUse"), command=ShellText("echo ${=x}")))
        self.assertEqual((got.status, got.stderr.count("guard kit:")), (2, 1))

    def test_a_reader_fault_is_named_once(self) -> None:
        kit = self.make({RuleFile("m.py"): NEEDS_MODEL, RuleFile("n.py"): NEEDS_MODEL})
        got = kit.judge(event(), ReaderPath(str(KIT / "no-such-reader")))
        self.assertEqual((got.status, got.stderr.count("guard kit:")), (2, 1))
        self.assertIn("guard kit: the shell reader", got.stderr)
        self.assertIn("cannot run", got.stderr)

    def test_the_model_is_built_once(self) -> None:
        kit = self.make({RuleFile("m.py"): NEEDS_MODEL, RuleFile("n.py"): NEEDS_MODEL})
        runs = kit.rules.parent / "runs"
        reader = kit.rules.parent / "reader"
        _ = reader.write_text(f"#!/bin/sh\necho run >> {runs}\nexec {READER} \"$@\"\n", encoding="utf-8")
        reader.chmod(0o755)
        self.assertEqual(kit.judge(event(), ReaderPath(str(reader))).status, 0)
        self.assertEqual(runs.read_text(encoding="utf-8"), "run\n")

    def test_a_tool_with_no_shell_has_no_model(self) -> None:
        got = self.make({RuleFile("asks.py"): ASKS_ANY_TOOL}).judge(event(tool=ShellText("Write")))
        self.assertEqual(got.status, 2)
        self.assertIn("the Write call carries no command text the kit can read", got.stderr)


class Switches(KitCase):
    """Each rule, the unreadable refusal and the whole kit have a switch file under off/."""

    def test_a_rule_switched_off(self) -> None:
        kit = self.make({RuleFile("refuses_all.py"): REFUSES, RuleFile("other.py"): NOTES})
        kit.switch(RuleFile("refuses-all"))
        self.assertEqual(kit.judge(event()).status, 0)

    def test_a_broken_rule_switched_off_is_not_loaded(self) -> None:
        kit = self.make({RuleFile("bad.py"): RuleSource("raise SystemExit(9)\n")})
        kit.switch(RuleFile("bad"))
        self.assertEqual(kit.judge(event()), Answer(0, Prose(""), Prose("")))

    def test_the_whole_kit_switched_off(self) -> None:
        kit = self.make({RuleFile("r.py"): REFUSES, RuleFile("bad.py"): RuleSource("def (")})
        kit.switch(RuleFile("all"))
        for raw in (event(), event(command=ShellText("echo ${=x}")), EventBytes(b"not json")):
            with self.subTest(raw=raw):
                self.assertEqual(kit.judge(raw), Answer(0, Prose(""), Prose("")))

    def test_the_unreadable_refusal_switched_off(self) -> None:
        kit = self.make({RuleFile("m.py"): NEEDS_MODEL, RuleFile("a.py"): NOTES})
        kit.switch(RuleFile("unreadable"))
        got = kit.judge(event(command=ShellText("echo ${=x}")))
        self.assertEqual((got.status, json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"]),
                         (0, "guard a: a note"))


def unknown(holder: Prose, text: ShellText) -> Prose:
    """The refusal's naming of a gap: the text ``holder`` runs is not known from the call ``text``."""
    return Prose(f"the text {holder} runs is not known from the call: `{text}`")


class RuleFiles(unittest.TestCase):
    """A rules folder's rule files come in name order whatever order the folder lists them in, each with its name."""

    def test_rule_files_come_in_name_order(self) -> None:
        listed = ["b_rule.py", "_helper.py", "notes.txt", "a.py"]
        with mock.patch("dispatcher.core.os.listdir", return_value=listed):
            got = rule_files(FolderPath("/rules"))
        self.assertEqual(got, [RuleEntry(RuleFile("a.py"), RuleName("a")),
                               RuleEntry(RuleFile("b_rule.py"), RuleName("b-rule"))])


class Gaps(KitCase):
    """A text `bash -c`, `sh -c` or `eval` runs that the call does not make known, and any command named zsh or
    fish, is a call the kit cannot read, refused before any rule runs, naming its holder and the forms the kit
    reads; a bash or sh text the call writes out or makes known by an assignment it holds passes."""

    def test_a_text_the_call_does_not_make_known_is_refused_with_no_rule(self) -> None:
        cases = {ShellText('bash -c "$CMD"'): Prose("bash -c"), ShellText('eval "$(sed -n 1,9p notes.sh)"'):
                 Prose("eval"), ShellText('sh -c "$X"'): Prose("sh -c")}
        for tool in (ShellText("Bash"), ShellText("Monitor")):
            for text, holder in cases.items():
                with self.subTest(tool=tool, text=text):
                    said = Prose(f"guard kit: this call cannot be read: {unknown(holder, text)}.\n{GAP_FORM}\n")
                    self.assertEqual(self.make({}).judge(event(tool=tool, command=text)), Answer(2, Prose(""), said))

    def test_a_zsh_or_fish_command_is_refused_naming_the_bash_form(self) -> None:
        cases = {ShellText("zsh -c 'print -r -- hi'"): (ShellText("zsh"), ShellText("zsh -c 'print -r -- hi'")),
                 ShellText("X='ls'; zsh -c \"$X\""): (ShellText("zsh"), ShellText('zsh -c "$X"')),
                 ShellText("fish -c 'echo hi'"): (ShellText("fish"), ShellText("fish -c 'echo hi'")),
                 ShellText("zsh --version"): (ShellText("zsh"), ShellText("zsh --version")),
                 ShellText("ls; env fish"): (ShellText("fish"), ShellText("env fish"))}
        for text, (shell, held) in cases.items():
            with self.subTest(text=text):
                said = Prose(f"guard kit: this call cannot be read: it runs {shell}, which the kit does not read: "
                             f"`{held}`.\n{FOREIGN_FORM}\n")
                self.assertEqual(self.make({}).judge(event(command=text)), Answer(2, Prose(""), said))

    def test_each_form_is_named_once(self) -> None:
        got = self.make({}).judge(event(command=ShellText("eval \"$A\"; zsh -c a; fish -c b; bash -c \"$B\"")))
        self.assertEqual(got.stderr.splitlines()[1:], [f"{GAP_FORM} {FOREIGN_FORM}"])

    def test_each_gap_is_named_in_one_refusal_whatever_the_rules_say(self) -> None:
        kit = self.make({RuleFile("m.py"): NEEDS_MODEL, RuleFile("n.py"): NEEDS_MODEL, RuleFile("r.py"): REFUSES})
        first, second = ShellText('eval "$A"'), ShellText('bash -c "$B"')
        got = kit.judge(event(command=ShellText(f"{first}; {second}")))
        gaps = f"{unknown(Prose('eval'), first)}; {unknown(Prose('bash -c'), second)}"
        said = f"guard r: no, says R\n\nguard kit: this call cannot be read: {gaps}.\n{GAP_FORM}\n"
        self.assertEqual(got, Answer(2, Prose(""), Prose(said)))

    def test_a_text_the_call_writes_out_or_assigns_passes(self) -> None:
        kit = self.make({RuleFile("m.py"): NEEDS_MODEL})
        for text in ("bash -c 'echo hi'", "eval 'echo hi'", "sh -c 'true'", "X='echo hi'; bash -c \"$X\"",
                     "X='echo hi'; eval \"$X\"", "export X=true && sh -c \"$X\""):
            with self.subTest(text=text):
                self.assertEqual(kit.judge(event(command=ShellText(text))), Answer(0, Prose(""), Prose("")))

    def test_the_unreadable_switch_covers_it(self) -> None:
        kit = self.make({RuleFile("m.py"): NEEDS_MODEL, RuleFile("a.py"): NOTES})
        kit.switch(RuleFile("unreadable"))
        got = kit.judge(event(command=ShellText('bash -c "$CMD"')))
        self.assertEqual((got.status, json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"]),
                         (0, "guard a: a note"))


class Ran(NamedTuple):
    """A child run of the entry: its exit status, stdout and stderr."""

    status: ExitCode
    out: Prose
    err: Prose


def entry(raw: EventBytes, *options: ShellText, home: Path, rules: Path | None = None) -> Ran:
    """Run dispatch.py with ``options`` on ``raw``, HOME ``home``, the rules folder ``rules`` where given."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": "/r", "HOME": str(home)}
    argv = [str(ENTRY), *options, *([f"--rules={rules}"] if rules is not None else [])]
    done = subprocess.run(argv, input=raw, capture_output=True, env=env, check=False, timeout=20)
    return Ran(ExitCode(done.returncode), Prose(done.stdout.decode()), Prose(done.stderr.decode()))


class MachineFile(KitCase):
    """A machine file missing or breaking the schema refuses every Bash and Monitor call whatever rules run, and no
    call of any other tool, so the file its refusal names can still be written."""

    def test_a_missing_or_incomplete_machine_file(self) -> None:
        empty = self.make({})
        (empty.off / ROUTE_CHECK).touch()
        ls = ShellText("ls")
        for case, text in {"missing": None, "incomplete": machine_text(slice=TomlSource(""))}.items():
            home = machine_home(self, text)
            with self.assertRaises(KitFault) as caught:
                _ = Declarations(home=home).machine()
            refused = Answer(2, Prose(""), Prose(f"{fault_text(caught.exception.reason)}\n"))
            calls = {"Bash": (event(command=ls), refused),
                     "Monitor": (event(tool=ShellText("Monitor"), command=ls), refused),
                     "Write": (file_call(ShellText("Write"), home / MACHINE_FILE), PASSED),
                     "Read": (file_call(ShellText("Read"), home / MACHINE_FILE), PASSED),
                     "Bash at PostToolUse": (event(ShellText("PostToolUse"), command=ls), PASSED)}
            for rules, folder in {"the kit's own": KIT / "rules", "none": empty.rules}.items():
                for call, (raw, want) in calls.items():
                    with self.subTest(case=case, rules=rules, call=call):
                        self.assertEqual(Kit(folder, empty.off, home).judge(raw), want)


class Entry(KitCase):
    """dispatch.py as the harness runs it: the kit's own rules by default, the limits, the answer written; its home
    holds a machine file declaring every fact absent."""

    def setUp(self) -> None:
        self.home = machine_home(self)
        (self.home / ".config" / "claude-guard" / "off").mkdir(parents=True)
        (self.home / ".config" / "claude-guard" / "off" / ROUTE_CHECK).touch()

    def test_a_trivial_call_passes_in_silence(self) -> None:
        for name in (ShellText("PreToolUse"), ShellText("PostToolUse")):
            with self.subTest(name=name):
                self.assertEqual(entry(event(name), home=self.home), Ran(ExitCode(0), Prose(""), Prose("")))

    def test_the_kits_own_rules_refuse(self) -> None:
        got = entry(event(command=ShellText("cd /elsewhere")), home=self.home)
        self.assertEqual(got.status, 2)
        self.assertIn("guard shared-shell:", got.err)

    def test_the_switch_folder_is_under_home(self) -> None:
        (self.home / ".config" / "claude-guard" / "off" / "shared-shell").touch()
        self.assertEqual(entry(event(command=ShellText("cd /elsewhere")), home=self.home).status, 0)

    def test_an_event_too_long(self) -> None:
        got = entry(EventBytes(b" " * ((8 << 20) + 1)), home=self.home)
        self.assertEqual(got.status, 2)
        self.assertIn("the hook event is longer than 8 MiB", got.err)

    def test_a_wrong_argument(self) -> None:
        got = entry(event(), ShellText("--nope=1"), home=self.home)
        self.assertEqual(got.status, 2)
        self.assertIn("the dispatcher's arguments are wrong", got.err)

    def test_the_wall_time_limit_passes_a_rules_own_handlers(self) -> None:
        kit = self.make({RuleFile("spins.py"): rule(
            ShellText('{"PreToolUse"}'), ShellText("None"),
            ShellText("while True:\n        try:\n            pass\n        except Exception:\n            pass"))})
        got = entry(event(), ShellText("--wall=0.5"), home=self.home, rules=kit.rules)
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: the dispatcher outran its 0.5 s limit", got.err)

    def test_the_wall_time_limit_at_a_stop_kept_going_is_a_note(self) -> None:
        kit = self.make({RuleFile("spins.py"): rule(
            ShellText('{"Stop", "SubagentStop"}'), ShellText("None"),
            ShellText("while True:\n        try:\n            pass\n        except Exception:\n            pass"))})
        said = fault_text(Prose("the dispatcher outran its 0.5 s limit"))
        for name in (ShellText("Stop"), ShellText("SubagentStop")):
            with self.subTest(name=name):
                got = entry(event(name, None, stop_hook_active=True), ShellText("--wall=0.5"), home=self.home,
                            rules=kit.rules)
                self.assertEqual((got.status, got.err), (0, ""))
                self.assertEqual(json.loads(got.out), {"systemMessage": said})
        got = entry(event(ShellText("Stop"), None, stop_hook_active=False), ShellText("--wall=0.5"), home=self.home,
                    rules=kit.rules)
        self.assertEqual(got, Ran(ExitCode(2), Prose(""), Prose(f"{said}\n")))

    def test_the_wall_time_limit_covers_reading_the_event(self) -> None:
        with tempfile.TemporaryFile() as err:
            child = subprocess.Popen([str(ENTRY), "--wall=0.5"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=err)
            try:
                status = child.wait(timeout=20)
            finally:
                child.kill()
                _ = child.wait()
                if child.stdin is not None:
                    child.stdin.close()
            _ = err.seek(0)
            said = err.read()
        self.assertEqual(status, 2)
        self.assertIn(b"outran its 0.5 s limit", said)

    def test_the_address_space_cap(self) -> None:
        hungry = "[bytearray(64 << 20) for _ in range(12)]"
        for source in (rule(ShellText('{"Stop"}'), ShellText("None"), ShellText(f"return {hungry}")),
                       RuleSource(f"HELD = {hungry}\n")):
            with self.subTest(source=source):
                kit = self.make({RuleFile("hungry.py"): source})
                got = entry(event(ShellText("Stop"), None), ShellText(f"--cap={512 << 20}"), home=self.home,
                            rules=kit.rules)
                self.assertEqual(got.status, 2)
                self.assertIn("the dispatcher ran out of its 512 MiB address space", got.err)

    def test_the_cap_never_raises_a_lower_limit(self) -> None:
        kit = self.make({RuleFile("hungry.py"): rule(ShellText('{"Stop"}'), ShellText("None"),
                                                     ShellText("return [bytearray(64 << 20) for _ in range(16)]"))})
        hard = resource.getrlimit(resource.RLIMIT_AS)[1]
        ceiling = "unlimited" if hard == resource.RLIM_INFINITY else str(hard)
        argv = ["/usr/bin/prlimit", f"--as={768 << 20}:{ceiling}", "--", str(ENTRY), f"--rules={kit.rules}"]
        done = subprocess.run(argv, input=event(ShellText("Stop"), None), capture_output=True, check=False,
                              timeout=20)
        self.assertEqual(done.returncode, 2)
        self.assertIn(b"the dispatcher ran out of its 768 MiB address space", done.stderr)

    def test_an_answer_that_cannot_be_written(self) -> None:
        kit = self.make({RuleFile("a.py"): NOTES})
        reading, writing = os.pipe()
        os.close(reading)
        try:
            done = subprocess.run([str(ENTRY), f"--rules={kit.rules}"], input=event(), stdout=writing,
                                  stderr=subprocess.PIPE, env={**os.environ, "HOME": str(self.home)}, check=False,
                                  timeout=20)
        finally:
            os.close(writing)
        self.assertEqual((done.returncode, done.stderr), (2, b""))

    def test_an_exit_inside_a_rule_still_refuses(self) -> None:
        kit = self.make({RuleFile("exits.py"): rule(ShellText('{"Stop"}'), ShellText("None"),
                                                    ShellText("raise SystemExit(0)"))})
        got = entry(event(ShellText("Stop"), None), home=self.home, rules=kit.rules)
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: the dispatcher failed: SystemExit", got.err)

    def test_a_dispatcher_that_cannot_load(self) -> None:
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(scratch)], check=True))
        _ = subprocess.run(["cp", "-r", str(KIT / "dispatcher"), str(scratch / "dispatcher")], check=True)
        _ = (scratch / "dispatcher" / "core.py").write_text("raise ImportError('broken core')\n", encoding="utf-8")
        done = subprocess.run([str(scratch / "dispatcher" / "dispatch.py")], input=event(), capture_output=True,
                              check=False, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn(b"guard kit: the dispatcher cannot load: ImportError: broken core", done.stderr)


if __name__ == "__main__":
    unittest.main()
