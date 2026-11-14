"""A trivial event starts at most a fixed number of child processes and runs no gate (USER RULING 2026-11-14).

The rule: while core.answer judges one trivial event over the kit's own rules, every process the kit starts is counted
by the program it names; each case holds each program to the bound written beside it and every program it does not name
to none. The work tree declares both gates a project can declare, a stand-in that no case names but the last exception,
so a trivial event runs no gate. A count does not move with machine load, so this gate never flakes: the wall time of a
trivial call is measured and printed at each close, never gated. A rule that adds work to every call turns a case red,
and a bound rises only by an edit here.

The cases, each bound measured once and written as an upper bound: PreToolUse and PostToolUse of a Bash `true` and of
a Read, a PreToolUse Write of a `.md` file and a Stop, in a work tree with no dribble; and a PostToolUse Bash `true` in
a work tree whose commit dribble is pending, which the two rules reading it parse once for the event.

Each case has its session state, a proc tree naming one claude process, its switch folder, its home and a git work
tree whose one commit is dated 2020-01-01 and which holds the file read in a temporary folder, and counts every
subprocess.Popen started while core.answer runs.

EXCEPTIONS (3), each with the failure it holds:
  * route-check and launch-guard are switched off: route-check refuses every Bash call whose shell is not the route
    wrapper, which no test environment names, and launch-guard runs the old guard's file, which the kit replaces.
  * the gate rules' own events, a written file handed to its gate, are no case: a gate run is their work, and they
    are not trivial.
  * a PostToolUse Read asks the determinism gate once which of the paths it read are tests: rules/test_determinism.py
    gives a test read its rule as a note, and only the gate decides what a test is.
"""

import collections
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NewType
from unittest import mock

from dispatcher.core import EventBytes, FolderPath, HookEvent, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import Count, ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from state.session import Pid, Places, StartTicks
from tests.test_dispatcher import machine_home
from tests.test_entry_points import GIT, HEAD
from tests.test_precise_hints import Json, JsonKey, JsonText
from tests.test_session_state import proc_tree, stat_line

Program = NewType("Program", str)
"""The file name of the program a started process runs."""
Word = NewType("Word", str)
"""One word of a started process's argument list."""
type Bounds = dict[Program, Count]
type Fields = dict[JsonKey, Json]

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
SHELL_READER, GIT_PROGRAM, GATE = Program("shell-reader"), Program("git"), Program("stand-in-gate")
OFF = ("route-check", "launch-guard")
DRIBBLE = ".commands-to-run.sh"
NOTES = "notes.md"
COMMIT = "git commit -S -m c\n"
PID = Pid(10)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")
DECLARED = """schema = 1
[gates.precise_hints]
run = [{gate}]
protocol = "hints-1"
suffixes = [".py"]
rule_source = "the hints page"
[gates.test_determinism]
run = [{gate}]
protocol = "determinism-1"
rule_source = "the determinism page"
"""

PRE_BASH: Bounds = {SHELL_READER: Count(1), GIT_PROGRAM: Count(2)}
POST_BASH: Bounds = {SHELL_READER: Count(1), GIT_PROGRAM: Count(1)}
PRE_READ: Bounds = {GIT_PROGRAM: Count(2)}
POST_READ: Bounds = {GIT_PROGRAM: Count(1), GATE: Count(1)}
PRE_WRITE: Bounds = {GIT_PROGRAM: Count(2)}
STOP: Bounds = {GIT_PROGRAM: Count(1)}
POST_BASH_DRIBBLE: Bounds = {SHELL_READER: Count(3), GIT_PROGRAM: Count(3)}


def program(args: list[Word] | ShellText) -> Program:
    """The file name of the program a Popen of ``args`` runs: its first word's."""
    first = args[0] if isinstance(args, list) else shlex.split(args)[0]
    return Program(Path(first).name)


class Trivial(unittest.TestCase):
    """Each trivial event, judged by core.answer over the kit's rules, starts at most its bound of each program."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        off = self.root / "off"
        off.mkdir()
        for name in OFF:
            (off / name).touch()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_DATE": f"@{HEAD} +0000", "GIT_COMMITTER_DATE": f"@{HEAD} +0000"}
        for line in ("init -q", "commit -q --allow-empty -m c"):
            _ = subprocess.run([*GIT, "-C", str(self.repo), *shlex.split(line)], env=env, check=True,
                               capture_output=True, timeout=30)
        gate = self.root / GATE
        _ = gate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gate.chmod(0o755)
        (self.repo / ".claude").mkdir()
        _ = (self.repo / ".claude" / "guard.toml").write_text(DECLARED.format(gate=json.dumps(str(gate))),
                                                              encoding="utf-8")
        _ = (self.repo / NOTES).write_text("# Notes\n", encoding="utf-8")
        self.transcript = self.root / "transcript.jsonl"
        _ = self.transcript.write_text("", encoding="utf-8")
        places = Places(self.root / "sessions", proc_tree(self.root, BOOT, {PID: stat_line(PID, START)}))
        home = machine_home(self)
        reading = ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(home)),
                                                      VarName("CLAUDE_PID"): ShellText(str(PID))})
        self.settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(off)), reading, home, places)

    def counted(self, fields: Fields, bounds: Bounds) -> None:
        """Judge the event of ``fields`` in the work tree, which passes, and hold each program it starts to
        ``bounds``, any program the bounds do not name to none."""
        payload: Fields = {JsonKey("session_id"): JsonText("s"), JsonKey("cwd"): JsonText(str(self.repo)),
                           JsonKey("transcript_path"): JsonText(str(self.transcript)), **fields}
        with mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen) as started:
            got = answer(EventBytes(json.dumps(payload).encode()), self.settings)
        self.assertEqual(got.status, 0, got.stderr)
        counts = collections.Counter(program(call.args[0] if call.args else call.kwargs["args"])
                                     for call in started.call_args_list)
        over = {name: count for name, count in counts.items() if count > bounds.get(name, 0)}
        self.assertEqual(over, {}, f"started {dict(counts)}, bounds {bounds}")

    def bash(self, event: HookEvent) -> Fields:
        """The fields of a ``event`` of a Bash `true`."""
        fields: Fields = {JsonKey("hook_event_name"): JsonText(event), JsonKey("tool_name"): JsonText("Bash"),
                          JsonKey("tool_input"): {JsonKey("command"): JsonText("true")}}
        if event == "PostToolUse":
            fields[JsonKey("tool_response")] = {JsonKey("stdout"): JsonText(""), JsonKey("stderr"): JsonText(""),
                                                JsonKey("interrupted"): False}
        return fields

    def read(self, event: HookEvent) -> Fields:
        """The fields of a ``event`` of a Read of a file in the work tree."""
        fields: Fields = {JsonKey("hook_event_name"): JsonText(event), JsonKey("tool_name"): JsonText("Read"),
                          JsonKey("tool_input"): {JsonKey("file_path"): JsonText(str(self.repo / NOTES))}}
        if event == "PostToolUse":
            fields[JsonKey("tool_response")] = {}
        return fields

    def test_a_pretooluse_bash_true(self) -> None:
        self.counted(self.bash("PreToolUse"), PRE_BASH)

    def test_a_posttooluse_bash_true(self) -> None:
        self.counted(self.bash("PostToolUse"), POST_BASH)

    def test_a_pretooluse_read(self) -> None:
        self.counted(self.read("PreToolUse"), PRE_READ)

    def test_a_posttooluse_read(self) -> None:
        self.counted(self.read("PostToolUse"), POST_READ)

    def test_a_pretooluse_write_of_a_markdown_file(self) -> None:
        self.counted({JsonKey("hook_event_name"): JsonText("PreToolUse"), JsonKey("tool_name"): JsonText("Write"),
                      JsonKey("tool_input"): {JsonKey("file_path"): JsonText(str(self.repo / NOTES)),
                                              JsonKey("content"): JsonText("# Notes\n")}}, PRE_WRITE)

    def test_a_stop(self) -> None:
        self.counted({JsonKey("hook_event_name"): JsonText("Stop"), JsonKey("stop_hook_active"): False,
                      JsonKey("last_assistant_message"): JsonText("Done.")}, STOP)

    def test_a_posttooluse_bash_true_with_a_pending_dribble(self) -> None:
        _ = (self.repo / DRIBBLE).write_text(COMMIT, encoding="utf-8")
        self.counted(self.bash("PostToolUse"), POST_BASH_DRIBBLE)


if __name__ == "__main__":
    _ = unittest.main()
