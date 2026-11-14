"""The launch-guard rule, through the dispatcher with the kit's own rules: a Workflow launch is handed to the old
launch guard run as a child, and the guard's verdict is the kit's. The guard is a stand-in script in a scratch folder
put in place of LAUNCH_GUARD, which records its stdin, its cwd and its environment and answers a chosen exit and
text; no case runs the real guard, whose passing run appends to the live launch ledger."""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import FunctionType
from typing import Literal, cast
from unittest import mock

from dispatcher import core
from dispatcher.core import (AgentId, Answer, EventBytes, FolderPath, HookEvent, Payload, Rule, Settings, ToolInput,
                             ToolName, answer, fault_text)
from dispatcher.gate_run import OUTPUT_LIMIT, ExitCode
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose, RuleName
from dispatcher.words import Directory, VarName
from rules import launch_guard
from tests.test_declarations import NONE_DECLARED

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
NAME = RuleName("launch-guard")
LOADED = core.loaded
MARK = VarName("LAUNCH_GUARD_TEST_MARK")
PASSED = Answer(0, Prose(""), Prose(""))
BLOCKED = Prose("BLOCKED: a Workflow launch names one or two briefs.\n  * its args name no briefs file → name one\n")
STAND_IN = """import json, os, sys, time
from pathlib import Path

here = Path(__file__).parent
(here / "stdin").write_bytes(sys.stdin.buffer.read())
(here / "cwd").write_text(os.getcwd())
(here / "env").write_text(json.dumps(dict(os.environ)))
sys.stdout.write("the stand-in's stdout\\n")
sys.stdout.flush()
sys.stderr.buffer.write({text!r}.encode("utf-8", "surrogateescape"))
sys.stderr.flush()
if {forever!r}:
    time.sleep(3600)
sys.exit({code!r})
"""
type Constant = Literal["LAUNCH_GUARD", "INTERPRETER", "LAUNCH_TIMEOUT"]
type Record = Literal["stdin", "cwd", "env"]


class GuardCase(unittest.TestCase):
    """A scratch home, the event's cwd, a switch folder with every rule but launch-guard off, and the stand-in."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.base)
        self.home, self.work, self.off = self.base / "home", self.base / "work", self.base / "off"
        for folder in (self.home / ".config" / "claude-guard", self.work, self.off, self.base / "guard"):
            folder.mkdir(parents=True)
        _ = (self.home / ".config" / "claude-guard" / "machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
        for module in (KIT / "rules").glob("*.py"):
            if not module.stem.startswith("_") and module.stem != "launch_guard":
                (self.off / module.stem.replace("_", "-")).touch()
        self.guard = self.base / "guard" / "stand_in.py"
        self.swaps: dict[Constant, Path | Seconds] = {"LAUNCH_GUARD": self.guard}
        self.fields: Payload = {}

    def stand(self, code: ExitCode, text: Prose, forever: bool = False) -> None:
        """Put the stand-in in place: it records what it is handed, writes ``text`` on stderr in UTF-8 (a lone
        surrogate as the byte it escapes) and exits ``code``, or never ends where ``forever``."""
        _ = self.guard.write_text(STAND_IN.format(text=text, forever=forever, code=code), encoding="utf-8")

    def load(self, path: FolderPath, name: RuleName) -> Rule:
        """The rule the dispatcher loads from ``path``, launch-guard's constants replaced by the case's."""
        rule = LOADED(path, name)
        if name == NAME:
            cast(FunctionType, rule.judge).__globals__.update(self.swaps)
        return rule

    def judged(self, tool: ToolName = ToolName("Workflow"), event: HookEvent = "PreToolUse",
               cwd: Path | None = None, agent: bool = False) -> Answer:
        """The dispatcher's answer to ``event`` of a ``tool`` call from ``cwd``, the case's work folder where None
        and no cwd at all where the case's base."""
        env = {VarName("HOME"): ShellText(str(self.home)), MARK: ShellText("marked")}
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), home=self.home)
        given: ToolInput = ({"command": ShellText("true")} if tool == "Bash"
                            else {"description": Prose("review the plan")})
        self.fields = {"hook_event_name": event, "tool_name": tool, "tool_input": given}
        if cwd != self.base:
            self.fields["cwd"] = Directory(str(cwd or self.work))
        if agent:
            self.fields["agent_id"] = AgentId("a1")
        with mock.patch.object(core, "loaded", self.load):
            return answer(EventBytes(json.dumps(self.fields).encode()), settings)

    def recorded(self, name: Record) -> Path:
        """The file the stand-in wrote what it was handed to."""
        return self.guard.parent / name

    def fault(self, said: Prose, interpreter: Path = launch_guard.INTERPRETER) -> Answer:
        """The refusal of a kit fault naming the guard and what it ``said``."""
        reason = Prose(f"the launch guard {interpreter} {self.guard} {said}")
        return Answer(2, Prose(""), Prose(fault_text(reason) + "\n"))


class Answered(GuardCase):
    """The guard's exit is the verdict: 0 passes, 2 refuses with its text, anything else is a fault naming it."""

    def test_exit_0_passes(self) -> None:
        self.stand(ExitCode(0), Prose("a note\n"))
        self.assertEqual(self.judged(), PASSED)
        self.assertTrue(self.recorded("stdin").exists())

    def test_exit_2_refuses_with_the_guards_text_whole(self) -> None:
        self.stand(ExitCode(2), BLOCKED)
        self.assertEqual(self.judged(), Answer(2, Prose(""), Prose(f"guard launch-guard: {BLOCKED}")))

    def test_a_text_that_is_not_utf_8_refuses_with_each_bad_byte_replaced(self) -> None:
        self.stand(ExitCode(2), Prose("BLOCKED \udcff\n"))
        self.assertEqual(self.judged(), Answer(2, Prose(""), Prose("guard launch-guard: BLOCKED \ufffd\n")))

    def test_exit_1_and_exit_3_are_faults_naming_the_guard(self) -> None:
        said = Prose("workflow-launch-guard: internal error, the call goes ahead: KeyError: 'x'\n")
        for code, text, words in ((ExitCode(1), said, said.strip()), (ExitCode(3), Prose(""), "silent")):
            with self.subTest(code=code):
                self.stand(code, text)
                self.assertEqual(self.judged(), self.fault(Prose(f"exited {code}: {words}")))

    def test_a_guard_that_never_ends_is_a_fault(self) -> None:
        self.swaps["LAUNCH_TIMEOUT"] = Seconds(0.5)
        self.stand(ExitCode(0), Prose(""), forever=True)
        with mock.patch.object(subprocess, "run", wraps=subprocess.run) as ran:
            self.assertEqual(self.judged(), self.fault(Prose("outran its 0.5 s limit")))
        self.assertEqual(ran.call_args.kwargs["timeout"], 0.5)

    def test_a_guard_that_cannot_start_is_a_fault(self) -> None:
        interpreter = self.base / "guard" / "not-executable"
        _ = interpreter.write_text("", encoding="utf-8")
        self.swaps["INTERPRETER"] = interpreter
        self.stand(ExitCode(0), Prose(""))
        self.assertEqual(self.judged(), self.fault(Prose("cannot start: Permission denied"), interpreter))

    def test_a_missing_guard_file_is_refused_naming_it(self) -> None:
        missing = self.base / "nowhere.py"
        self.swaps["LAUNCH_GUARD"] = missing
        got = self.judged()
        self.assertEqual(got.status, 2, got)
        self.assertTrue(got.stderr.startswith("guard launch-guard: "), got.stderr)
        self.assertIn(str(missing), got.stderr)

    def test_the_text_is_cut_past_64_kib_with_a_line_saying_so(self) -> None:
        cut = "\n(the launch guard's text is cut here, at 64 KiB)"
        for extra, tail in ((0, ""), (1, cut)):
            with self.subTest(extra=extra):
                self.stand(ExitCode(2), Prose("x" * (OUTPUT_LIMIT + extra)))
                said = Prose(f"guard launch-guard: {'x' * OUTPUT_LIMIT}{tail}\n")
                self.assertEqual(self.judged(), Answer(2, Prose(""), said))


class Handed(GuardCase):
    """The guard is handed the event's JSON on stdin, the event's cwd and the dispatcher's environment."""

    def setUp(self) -> None:
        super().setUp()
        self.stand(ExitCode(0), Prose(""))

    def test_the_payload_arrives_as_the_events_json(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        self.assertEqual(json.loads(self.recorded("stdin").read_bytes()), self.fields)

    def test_the_guard_runs_in_the_events_cwd(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        self.assertEqual(self.recorded("cwd").read_text(encoding="utf-8"), str(self.work))

    def test_an_event_naming_no_cwd_runs_the_guard_in_the_kits_folder(self) -> None:
        _ = self.enterContext(contextlib.chdir(self.base))
        self.assertEqual(self.judged(cwd=self.base), PASSED)
        self.assertNotIn("cwd", self.fields)
        self.assertEqual(self.recorded("cwd").read_text(encoding="utf-8"), str(KIT))

    def test_the_guard_gets_the_dispatchers_environment(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        env: dict[VarName, ShellText] = json.loads(self.recorded("env").read_text(encoding="utf-8"))
        self.assertEqual((env.get(MARK), env.get(VarName("HOME"))), ("marked", str(self.home)))

    def test_the_guards_stdout_never_reaches_the_hooks_stdout(self) -> None:
        with tempfile.TemporaryFile() as sink:
            sys.stdout.flush()
            saved = os.dup(1)
            _ = os.dup2(sink.fileno(), 1)
            try:
                got = self.judged()
            finally:
                _ = os.dup2(saved, 1)
                os.close(saved)
            _ = sink.seek(0)
            self.assertEqual((got, sink.read()), (PASSED, b""))


class Judged(GuardCase):
    """Every agent's launch is judged alike; a Bash call and a PostToolUse of Workflow are not judged."""

    def test_an_agents_launch_is_judged_alike(self) -> None:
        self.stand(ExitCode(2), BLOCKED)
        refused = Answer(2, Prose(""), Prose(f"guard launch-guard: {BLOCKED}"))
        self.assertEqual((self.judged(), self.judged(agent=True)), (refused, refused))

    def test_a_bash_call_and_a_post_tool_use_of_workflow_are_not_judged(self) -> None:
        self.stand(ExitCode(2), BLOCKED)
        cases: tuple[tuple[ToolName, HookEvent], ...] = ((ToolName("Bash"), "PreToolUse"),
                                                         (ToolName("Workflow"), "PostToolUse"))
        for tool, event in cases:
            with self.subTest(tool=tool, event=event):
                self.assertEqual(self.judged(tool, event), PASSED)
                self.assertFalse(self.recorded("stdin").exists())


class Constants(unittest.TestCase):
    """The guard the rule runs and its limit."""

    def test_the_guard_and_its_interpreter_are_executable_files_here(self) -> None:
        """LAUNCH_GUARD and INTERPRETER name executable files on this machine; this case moves with the guard at the
        cutover, when the guard's file takes its new place."""
        for path in (launch_guard.LAUNCH_GUARD, launch_guard.INTERPRETER):
            with self.subTest(path=path):
                self.assertTrue(path.is_file() and os.access(path, os.X_OK), path)

    def test_the_guard_runs_for_six_seconds_at_most(self) -> None:
        self.assertEqual(launch_guard.LAUNCH_TIMEOUT, 6.0)
