"""The event's declarations and session state: each read only for an event a rule asks it of and built once per event,
a fault of either one refusal naming its file, never a pass, and the state of every claude process that has ended swept
at SessionStart. Each case has a home folder, a repository, a sessions folder and a decoy of its own in the temporary
folder, the decoy standing in for the state module's live places, so no case reaches the real state. The cases call
core.answer in this process, but those of Light, which run dispatcher/dispatch.py as the harness does."""

import errno
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NewType
from unittest import mock

import state.session
from declarations.lookup import Declarations
from dispatcher.core import FAULT_TAIL, Answer, Event, EventBytes, FolderPath, Payload, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import KitFault, Prose
from dispatcher.words import Environment, VarName
from state.session import FileStem, Places, StateFault, boot_of, process_key, rows_in
from tests.test_declarations import NONE_DECLARED, TomlSource, declaring

KIT = Path(__file__).resolve().parents[1]
ENTRY = KIT / "dispatcher" / "dispatch.py"
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
PROC = Path("/proc")
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
RuleFile = NewType("RuleFile", str)
RuleSource = NewType("RuleSource", str)
Field = NewType("Field", str)
type Fields = dict[Field, ShellText | dict[Field, ShellText]]

HEAD = "import sys\nfrom pathlib import Path\n\nfrom dispatcher.verdict import Prose, context\n"


def rule(body: ShellText) -> RuleSource:
    """A rule judging PreToolUse and SessionStart, for any tool, whose judge runs ``body``."""
    return RuleSource(f'{HEAD}EVENTS = frozenset({{"PreToolUse", "SessionStart"}})\nTOOLS = None\n\n'
                      f"def judge(event):\n{body}")


DECLARED = rule(ShellText("    facts = event.declaration(Path(event.cwd))\n"
                          '    return context(Prose(" ".join(map(str, facts.records))))\n'))
RECORDS = rule(ShellText("    session = event.state()\n"
                         '    session.record("run", "r1", {"log": "l"})\n'
                         '    return context(Prose(" ".join(f"{r.ident}:{r.agent}" for r in session.records("run"))))\n'))
READS_STATE = rule(ShellText('    return context(Prose(str(len(event.state().records("run")))))\n'))
ASKS_BOTH = rule(ShellText("    _ = event.declaration(Path(event.cwd)), event.state()\n    return None\n"))
LOADED = rule(ShellText('    held = [name for name in ("declarations.lookup", "state.session") if name in sys.modules]\n'
                        '    return context(Prose(" ".join(held) or "none"))\n'))
PASSES = rule(ShellText("    return None\n"))

PROJECT = TomlSource('schema = 1\n[records]\npaths = ["kept"]\n')
MACHINE = declaring(records=TomlSource('[records]\npaths = ["~/logs"]\n'))
OTHER_BOOT = FileStem("1-1-" + "0" * 32)


def git(cwd: Path, *words: ShellText) -> None:
    """Run git in ``cwd`` with no user or system configuration."""
    _ = subprocess.run(["git", "-c", "init.defaultBranch=main", *words], cwd=cwd, env=GIT_ENV, check=True,
                       capture_output=True)


def event(cwd: Path, name: ShellText = ShellText("PreToolUse"), **more: ShellText) -> EventBytes:
    """A hook event's bytes at ``name`` in ``cwd``, a Bash call of `true` at PreToolUse, with ``more`` fields."""
    fields: Fields = {Field("hook_event_name"): name, Field("cwd"): ShellText(str(cwd)),
                      Field("session_id"): ShellText("s")}
    if name == "PreToolUse":
        fields[Field("tool_name")] = ShellText("Bash")
        fields[Field("tool_input")] = {Field("command"): ShellText("true")}
    fields.update({Field(key): value for key, value in more.items()})
    return EventBytes(json.dumps(fields).encode())


def refusal(reason: Prose) -> Answer:
    """The one refusal naming the kit's fault ``reason``."""
    return Answer(2, Prose(""), Prose(f"guard kit: {reason}. {FAULT_TAIL}\n"))


def noted(got: Answer) -> Prose:
    """The notes an answer of notes alone carries, the stderr of any other."""
    return Prose(json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"]) if got.status == 0 else got.stderr


class WiringCase(unittest.TestCase):
    """A case with a home, a git repository, a rules folder, a sessions folder and a decoy live state of its own."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("home", "repo", "rules", "off", "sessions", "decoy"):
            (self.root / name).mkdir()
        self.home, self.repo, self.rules = self.root / "home", self.root / "repo", self.root / "rules"
        git(self.repo, ShellText("init"), ShellText("-q"))
        _ = self.write(self.home / ".config" / "claude-guard" / "machine.toml", NONE_DECLARED)
        self.places = Places(self.root / "sessions", PROC)
        self.decoy = Places(self.root / "decoy", PROC)
        _ = self.enterContext(mock.patch.object(state.session, "LIVE", self.decoy))
        self.env: Environment = {VarName("CLAUDE_PROJECT_DIR"): ShellText(str(self.repo)),
                                 VarName("HOME"): ShellText(str(self.home)),
                                 VarName("CLAUDE_PID"): ShellText(str(os.getpid()))}

    def write(self, path: Path, text: TomlSource) -> Path:
        """Write ``text`` at ``path``, its folders made."""
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")
        return path

    def given(self, rules: dict[RuleFile, RuleSource]) -> None:
        """Put ``rules`` in the rules folder."""
        for name, source in rules.items():
            _ = (self.rules / name).write_text(source, encoding="utf-8")

    def reading(self) -> ReadSettings:
        """What reading a call needs, the case's environment its own."""
        return ReadSettings(READER, Seconds(5.0), self.env)

    def judge(self, raw: EventBytes, places: Places | None = None, *, live: bool = False) -> Answer:
        """The dispatcher's answer to ``raw``, the state in ``places``, the case's own by default, or in the live
        places, here the decoy, where ``live``."""
        kept = None if live else places or self.places
        settings = Settings(FolderPath(str(self.rules)), FolderPath(str(self.root / "off")), self.reading(),
                            self.home, kept)
        return answer(raw, settings)

    def made(self, payload: Payload) -> Event:
        """An event of ``payload`` as the dispatcher makes it for this case."""
        return Event(payload, self.reading(), self.home, self.places)

    def folder(self) -> Path:
        """The folder of this process's state in the case's sessions folder."""
        return self.places.sessions / process_key(self.env, PROC).name


class Declared(WiringCase):
    """A rule reads the declaration holding for a path, one Declarations per event, a faulty file one refusal."""

    def test_a_rule_reads_the_declaration_for_a_path(self) -> None:
        _ = self.write(self.repo / ".claude" / "guard.toml", PROJECT)
        _ = self.write(self.home / ".config" / "claude-guard" / "machine.toml", MACHINE)
        self.given({RuleFile("d.py"): DECLARED})
        got = self.judge(event(self.repo))
        self.assertEqual((got.status, noted(got)), (0, f"guard d: {self.home / 'logs'} {self.repo / 'kept'}"))

    def test_one_declarations_per_event(self) -> None:
        declaration = self.write(self.repo / ".claude" / "guard.toml", PROJECT)
        once = self.made(Payload(hook_event_name="PreToolUse"))
        first = once.declaration(self.repo)
        _ = self.write(declaration, TomlSource("schema = = 1\n"))
        self.assertEqual(once.declaration(self.repo), first)
        with self.assertRaises(KitFault):
            _ = self.made(Payload(hook_event_name="PreToolUse")).declaration(self.repo)

    def test_a_faulty_declaration_is_one_refusal_naming_it(self) -> None:
        self.given({RuleFile("d.py"): DECLARED, RuleFile("e.py"): DECLARED})
        declaration = self.repo / ".claude" / "guard.toml"
        for text, says in ((TomlSource("schema = = 1\n"), "is not TOML"),
                           (TomlSource("schema = 1\nnope = 1\n"), "is refused at `nope`: no such key")):
            with self.subTest(says):
                _ = self.write(declaration, text)
                with self.assertRaises(KitFault) as caught:
                    _ = Declarations(home=self.home).for_path(self.repo)
                self.assertIn(f"the declaration {declaration} {says}", caught.exception.reason)
                self.assertEqual(self.judge(event(self.repo)), refusal(caught.exception.reason))


class State(WiringCase):
    """A rule keeps and reads the state of its claude process as its agent, one Session per event, a fault refused."""

    def test_a_rule_keeps_the_state_of_its_process(self) -> None:
        self.given({RuleFile("s.py"): RECORDS})
        main = self.judge(event(self.repo))
        self.assertEqual((main.status, noted(main), self.files()), (0, "guard s: r1:None", ["main.json"]))
        agent = self.judge(event(self.repo, agent_id=ShellText("a1")))
        self.assertEqual((agent.status, noted(agent), self.files()),
                         (0, "guard s: r1:a1 r1:None", ["agent-6131.json", "main.json"]))
        self.assertEqual(os.listdir(self.decoy.sessions), [])

    def files(self) -> list[FileStem]:
        """The state files of this process's folder in the case's sessions folder."""
        return sorted(FileStem(name) for name in os.listdir(self.folder()) if name.endswith(".json"))

    def test_one_session_per_event(self) -> None:
        once = self.made(Payload(hook_event_name="PreToolUse"))
        self.assertIs(once.state(), once.state())

    def test_a_process_with_no_key_is_one_refusal(self) -> None:
        self.given({RuleFile("s.py"): RECORDS, RuleFile("t.py"): RECORDS})
        for spelled in ("", "none"):
            with self.subTest(spelled):
                self.env[VarName("CLAUDE_PID")] = ShellText(spelled)
                with self.assertRaises(StateFault) as caught:
                    _ = process_key(self.env, PROC)
                self.assertEqual(self.judge(event(self.repo)), refusal(caught.exception.reason))
                self.assertEqual(self.judge(event(self.repo), live=True), refusal(caught.exception.reason))

    def test_an_unreadable_state_file_is_one_refusal_naming_it(self) -> None:
        self.given({RuleFile("r.py"): READS_STATE, RuleFile("s.py"): READS_STATE})
        file = self.folder() / "main.json"
        file.parent.mkdir(parents=True)
        _ = file.write_text("{", encoding="utf-8")
        with self.assertRaises(StateFault) as caught:
            _ = rows_in(file)
        self.assertIn(str(file), caught.exception.reason)
        self.assertEqual(self.judge(event(self.repo)), refusal(caught.exception.reason))


class Sweep(WiringCase):
    """SessionStart, and no other event, removes the state of every claude process that has ended."""

    def standing(self, sessions: Path) -> tuple[FileStem, FileStem]:
        """An ended process's folder and this one's, made in ``sessions``; their names."""
        live = process_key(self.env, PROC).name
        for name in (OTHER_BOOT, live):
            (sessions / name).mkdir()
        return OTHER_BOOT, live

    def test_session_start_sweeps_ended_processes(self) -> None:
        self.given({RuleFile("p.py"): PASSES})
        ended, live = self.standing(self.places.sessions)
        self.assertEqual(self.judge(event(self.repo, ShellText("PreToolUse"))), Answer(0, Prose(""), Prose("")))
        self.assertEqual(sorted(os.listdir(self.places.sessions)), sorted([ended, live]))
        self.assertEqual(self.judge(event(self.repo, ShellText("SessionStart"))), Answer(0, Prose(""), Prose("")))
        self.assertEqual(os.listdir(self.places.sessions), [live])

    def test_the_live_state_is_swept_where_no_places_are_given(self) -> None:
        _, live = self.standing(self.decoy.sessions)
        self.assertEqual(self.judge(event(self.repo, ShellText("SessionStart")), live=True).status, 0)
        self.assertEqual(os.listdir(self.decoy.sessions), [live])

    def test_a_sweep_that_cannot_run_is_a_refusal(self) -> None:
        no_proc = self.root / "no-proc"
        no_proc.mkdir()
        with self.assertRaises(StateFault) as caught:
            _ = boot_of(no_proc)
        no_boot = self.judge(event(self.repo, ShellText("SessionStart")), Places(self.places.sessions, no_proc))
        self.assertEqual(no_boot, refusal(caught.exception.reason))
        a_file = self.root / "a-file"
        a_file.touch()
        listed = NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(a_file))
        not_listed = self.judge(event(self.repo, ShellText("SessionStart")), Places(a_file, PROC))
        self.assertEqual(not_listed, refusal(Prose(f"the session state cannot be swept: {listed}")))


class Light(WiringCase):
    """dispatch.py imports neither module for an event no rule asks it of, the declarations for a Bash call, whose
    machine facts it asks itself, and each one a rule asks."""

    def entry(self, tool: ShellText) -> Prose:
        """The notes dispatch.py gives a PreToolUse event of a ``tool`` call under the case's rules."""
        env = {**os.environ, "HOME": str(self.home), "CLAUDE_PROJECT_DIR": str(self.repo),
               "CLAUDE_PID": str(os.getpid())}
        done = subprocess.run([str(ENTRY), f"--rules={self.rules}"], input=event(self.repo, tool_name=tool),
                              capture_output=True, env=env, check=False, timeout=20)
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        return Prose(json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_neither_module_is_imported_unasked(self) -> None:
        self.given({RuleFile("z.py"): LOADED})
        self.assertEqual(self.entry(ShellText("Read")), "guard z: none")
        self.given({RuleFile("a.py"): ASKS_BOTH})
        self.assertEqual(self.entry(ShellText("Read")), "guard z: declarations.lookup state.session")

    def test_a_bash_call_imports_the_declarations_alone(self) -> None:
        self.given({RuleFile("z.py"): LOADED})
        self.assertEqual(self.entry(ShellText("Bash")), "guard z: declarations.lookup")


if __name__ == "__main__":
    _ = unittest.main()
