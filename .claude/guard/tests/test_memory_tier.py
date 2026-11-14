"""The memory-tier rule, through the dispatcher with every other rule switched off: at PreToolUse a Bash or Monitor call
of any agent appends to its claude process's tier file the line of its command's digest, heavy where a command of the
call runs heavy under its declaration or cannot be named, light otherwise; a Bash call the machine's rewrite hook
rewrites gets a second line of that tier under the text the hook names; a hook whose answer cannot be read and a file
that cannot be written are each a note naming it and no refusal; no other event writes a line.

Each case has a home whose machine file declares every fact absent, a repository declaring aletheia's example, and a
sessions folder under a proc tree of its own naming one claude process, so no case reaches the real state. A case
declaring a rewrite hook writes the machine file again, naming a stand-in of its own."""

import collections
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import FunctionType
from typing import Literal, NewType, cast
from unittest import mock

from dispatcher import core
from dispatcher.core import AgentId, Answer, EventBytes, FolderPath, HookEvent, Rule, Settings, ToolName, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose, RuleName
from dispatcher.words import VarName
from state import route, tiers
from state.session import Pid, Places, StartTicks, process_key
from state.tiers import Tier
from tests.test_declarations import declaring
from tests.test_heavy import EXAMPLE, TomlSource, repository
from tests.test_session_state import BOOT, claude_pid, proc_tree, stat_line

HookScript = NewType("HookScript", str)
"""The text of a stand-in rewrite hook."""
Printed = NewType("Printed", str)
"""What a stand-in rewrite hook prints on its stdout."""
Program = NewType("Program", str)
"""The file name of the program a started process runs."""
type Constant = Literal["HOOK_TIMEOUT"]

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = "memory_tier"
NAME = RuleName("memory-tier")
LOADED = core.loaded
PID = Pid(4242)
PASSED = Answer(0, Prose(""), Prose(""))
BUILD = ShellText("cabal run shake -- build")
STATUS = ShellText("git status")
COUNT = ShellText("cabal run shake -- count-modules")  # light by the example, heavy by the kit's cabal row
UNNAMED = ShellText('"$TOOL" build')
CAT, READ, LIST = ShellText("cat x"), ShellText("rtk read x"), ShellText("ls")
REWRITES = {CAT: READ, BUILD: ShellText(f"rtk {BUILD}"), LIST: LIST}
HOOK = "rewrite-hook"
STAND_IN = HookScript("""#!{python}
import json, os, sys

payload = json.load(sys.stdin)
command = payload["tool_input"]["command"]
rewrites = {rewrites!r}
sys.stderr.write("the stand-in's stderr\\n")
if (sys.argv[1:] == ["hook", "claude"] and os.getcwd() == payload["cwd"] == {cwd!r}
        and os.environ.get("HOME") == {home!r} and command in rewrites):
    print(json.dumps({{"hookSpecificOutput": {{"hookEventName": "PreToolUse",
                                               "updatedInput": {{"command": rewrites[command]}}}}}}))
""")
NOTE = ("the rewrite hook {hook} {fault}, so a text it runs in place of this call's finds no tier line and claude-call "
        "caps it at heavy_max.")
UNREAD = (Printed("rewritten"), Printed("[1]"), Printed('{"hookSpecificOutput": 1}'),
          Printed('{"hookSpecificOutput": {"updatedInput": 1}}'),
          Printed('{"hookSpecificOutput": {"updatedInput": {"command": 3}}}'),
          Printed('{"updatedInput": {"command": "ls"}}'))


def printing(text: Printed) -> HookScript:
    """A stand-in that prints ``text`` and a newline and exits 0."""
    return HookScript(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(text)}\n")


class Case(unittest.TestCase):
    """A fresh home, switch folder, repository and state per test, every rule but memory-tier switched off."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.base)
        self.home, self.off, self.work = self.base / "home", self.base / "off", self.base / "aletheia"
        for folder in (self.home / ".config" / "claude-guard", self.off):
            folder.mkdir(parents=True)
        self.machine = self.home / ".config" / "claude-guard" / "machine.toml"
        _ = self.machine.write_text(declaring(), encoding="utf-8")
        repository(self.work, TomlSource(EXAMPLE.read_text(encoding="utf-8")))
        for module in (KIT / "rules").glob("*.py"):
            if not module.stem.startswith("_") and module.stem != RULE:
                (self.off / module.stem.replace("_", "-")).touch()
        proc = proc_tree(self.base, BOOT, {PID: stat_line(PID, StartTicks(777))})
        self.places = Places(self.base / "sessions", proc)
        self.tiers = tiers.tiers(self.places, process_key(claude_pid(PID), proc))
        self.hook = self.base / HOOK
        self.swaps: dict[Constant, Seconds] = {}

    def rewriting(self, script: HookScript | None) -> None:
        """Declare the stand-in at the case's hook path as the machine's rewrite hook, given rtk's words `hook claude`,
        ``script`` its text, no file where None."""
        hook = TomlSource(f'[rewrite]\nhook = [{json.dumps(str(self.hook))}, "hook", "claude"]\n')
        _ = self.machine.write_text(declaring(rewrite=hook), encoding="utf-8")
        if script is not None:
            _ = self.hook.write_text(script, encoding="utf-8")
            self.hook.chmod(0o755)

    def faithful(self) -> None:
        """Declare the stand-in that rewrites as rtk does: a REWRITES command, read from its stdin, where it is given
        `hook claude` and runs in the work tree under the case's home, and nothing printed for any other; it writes a
        line on its stderr either way."""
        self.rewriting(HookScript(STAND_IN.format(python=sys.executable, rewrites=REWRITES, cwd=str(self.work),
                                                  home=str(self.home))))

    def load(self, path: FolderPath, name: RuleName) -> Rule:
        """The rule the dispatcher loads from ``path``, memory-tier's constants replaced by the case's."""
        rule = LOADED(path, name)
        if name == NAME:
            cast(FunctionType, rule.judge).__globals__.update(self.swaps)
        return rule

    def judged(self, command: ShellText, tool: ToolName = ToolName("Bash"), event: HookEvent = "PreToolUse",
               agent: AgentId | None = None) -> Answer:
        """The dispatcher's answer to ``event`` for a ``tool`` call of ``agent`` (the main thread's where None)
        running ``command`` in the repository, the hook's environment naming the case's claude process."""
        env = {VarName("HOME"): ShellText(str(self.home))} | claude_pid(PID)
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), home=self.home, places=self.places)
        fields = {"hook_event_name": event, "tool_name": tool, "cwd": str(self.work), "tool_input": {"command": command},
                  "session_id": "s1"}
        if agent is not None:
            fields["agent_id"] = agent
        with mock.patch.object(core, "loaded", self.load):
            return answer(EventBytes(json.dumps(fields).encode()), settings)

    def started(self, command: ShellText, tool: ToolName = ToolName("Bash")) -> collections.Counter[Program]:
        """The programs a PreToolUse of a ``tool`` call running ``command`` starts, by file name; the call passes
        unremarked."""
        with mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen) as popen:
            self.assertEqual(self.judged(command, tool), PASSED)
        return collections.Counter(Program(Path((call.args[0] if call.args else call.kwargs["args"])[0]).name)
                                   for call in popen.call_args_list)

    def written(self, *lines: tuple[ShellText, Tier]) -> None:
        """The tier file holds a line per (command, tier) of ``lines``, in order, and nothing else."""
        self.assertEqual(self.tiers.read_bytes(), b"".join(tiers.line(route.digest(text), tier) for text, tier in lines))

    def noted(self, got: Answer, fault: Prose) -> None:
        """``got`` passes with the note naming the case's hook and ``fault``."""
        self.assertEqual((got.status, got.stderr), (0, ""))
        note = json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(note, "guard memory-tier: " + NOTE.format(hook=f"{self.hook} hook claude", fault=fault))


class Writes(Case):
    """A Bash or Monitor call of any agent writes its line and passes."""

    def test_a_heavy_call_writes_heavy(self) -> None:
        self.assertEqual(self.judged(BUILD), PASSED)
        self.written((BUILD, "heavy"))

    def test_a_light_call_writes_light_after_the_lines_before_it(self) -> None:
        self.assertEqual(self.judged(BUILD), PASSED)
        self.assertEqual(self.judged(STATUS), PASSED)
        self.written((BUILD, "heavy"), (STATUS, "light"))

    def test_a_command_its_declaration_calls_light_writes_light(self) -> None:
        self.assertEqual(self.judged(COUNT), PASSED)
        self.written((COUNT, "light"))

    def test_a_call_naming_a_program_the_kit_cannot_name_writes_heavy(self) -> None:
        self.assertEqual(self.judged(UNNAMED), PASSED)
        self.written((UNNAMED, "heavy"))

    def test_a_heavy_command_anywhere_in_the_call_writes_heavy(self) -> None:
        call = ShellText(f"{STATUS} && {BUILD}")
        self.assertEqual(self.judged(call), PASSED)
        self.written((call, "heavy"))

    def test_a_monitor_call_writes_alike(self) -> None:
        self.assertEqual(self.judged(BUILD, tool=ToolName("Monitor")), PASSED)
        self.written((BUILD, "heavy"))

    def test_an_agents_call_writes_alike(self) -> None:
        self.assertEqual(self.judged(STATUS, agent=AgentId("a1")), PASSED)
        self.written((STATUS, "light"))


class Rewrites(Case):
    """A Bash call the rewrite hook rewrites gets a second line of its tier under the hook's text; a kept call, a
    Monitor call and a machine declaring no hook get one line, and the last two start no hook."""

    def test_a_rewritten_call_writes_its_tier_under_both_texts(self) -> None:
        self.faithful()
        self.assertEqual(self.judged(CAT), PASSED)
        self.assertEqual(self.judged(BUILD), PASSED)
        self.written((CAT, "light"), (READ, "light"), (BUILD, "heavy"), (REWRITES[BUILD], "heavy"))

    def test_a_kept_call_writes_one_line(self) -> None:
        self.faithful()
        self.assertEqual(self.judged(STATUS), PASSED)  # the stand-in prints nothing
        self.assertEqual(self.judged(LIST), PASSED)  # the stand-in names the call's own text
        self.written((STATUS, "light"), (LIST, "light"))

    def test_the_hooks_stderr_goes_nowhere(self) -> None:
        self.faithful()
        with tempfile.TemporaryFile() as caught:
            kept = os.dup(2)
            os.dup2(caught.fileno(), 2)
            try:
                got = self.judged(CAT)
            finally:
                os.dup2(kept, 2)
                os.close(kept)
            _ = caught.seek(0)
            self.assertEqual((got, caught.read()), (PASSED, b""))
        self.written((CAT, "light"), (READ, "light"))

    def test_a_monitor_call_starts_no_hook(self) -> None:
        self.faithful()
        self.assertEqual(self.started(BUILD, ToolName("Monitor"))[Program(HOOK)], 0)
        self.written((BUILD, "heavy"))

    def test_a_machine_declaring_no_hook_starts_none(self) -> None:
        started = self.started(CAT)
        self.assertLessEqual(set(started), {Program("shell-reader"), Program("git")}, started)
        self.written((CAT, "light"))

    def test_the_hook_runs_for_two_seconds_at_most(self) -> None:
        rule = LOADED(FolderPath(str(KIT / "rules" / f"{RULE}.py")), NAME)
        self.assertEqual(cast(FunctionType, rule.judge).__globals__["HOOK_TIMEOUT"], 2.0)


class Passes(Case):
    """A hook whose answer cannot be read or a line that cannot be written is a note naming it, the call's own line
    written where it can be, and no other event writes one."""

    def test_a_hook_that_exits_non_zero_is_a_note(self) -> None:
        self.rewriting(HookScript("#!/bin/sh\nexit 1\n"))
        self.noted(self.judged(CAT), Prose("exited 1"))
        self.written((CAT, "light"))

    def test_a_hook_printing_no_command_is_a_note(self) -> None:
        """Each of UNREAD, and a line of white space alone."""
        for text in (*UNREAD, Printed(" ")):
            with self.subTest(text=text):
                self.rewriting(printing(text))
                self.noted(self.judged(CAT), Prose("printed no hookSpecificOutput.updatedInput.command"))
                self.written((CAT, "light"))
                self.tiers.unlink()

    def test_a_hook_that_cannot_start_is_a_note(self) -> None:
        self.rewriting(None)
        self.noted(self.judged(CAT), Prose("cannot start (No such file or directory)"))
        self.written((CAT, "light"))

    def test_a_hook_that_outruns_its_limit_is_a_note(self) -> None:
        self.swaps["HOOK_TIMEOUT"] = Seconds(0.5)
        self.rewriting(HookScript("#!/bin/sh\nexec sleep 10\n"))
        self.noted(self.judged(CAT), Prose("outran its 0.5 s limit"))
        self.written((CAT, "light"))

    def test_a_file_that_cannot_be_written_is_a_note_naming_it_and_no_refusal(self) -> None:
        _ = self.places.sessions.write_text("", encoding="utf-8")
        got = self.judged(BUILD)
        self.assertEqual((got.status, got.stderr), (0, ""))
        note = json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(note, f"guard memory-tier: the tier file {self.tiers} cannot be written (Not a directory), so"
                               " claude-call finds no line and caps this call at heavy_max.")

    def test_a_post_tool_use_writes_nothing(self) -> None:
        self.assertEqual(self.judged(BUILD, event="PostToolUse"), PASSED)
        self.assertFalse(self.tiers.exists())
