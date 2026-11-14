"""The Bash tool's shell as bin/route/bash takes it: Claude Code's own call line goes to the kit's claude-call, every
other start to /usr/bin/bash. Each case copies state/route.py's WRAPPER into a kit layout of its own in the temporary
folder, beside a stand-in call/claude-call that records its arguments and environment, and runs the copy as Claude
Code runs CLAUDE_CODE_SHELL: the path itself, with its arguments. A decoy bash stands first on PATH and as SHELL, so a
start that reaches any bash but /usr/bin/bash shows."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict

from dispatcher.tree import ShellText
from dispatcher.words import VarName
from state import route

KIT = Path(__file__).resolve().parents[1]
Source = NewType("Source", str)
Output = NewType("Output", str)
Mode = NewType("Mode", int)
ExitStatus = NewType("ExitStatus", int)

PYTHON = ShellText("/home/nicolas/.local/bin/python3.14")
# The line Claude Code 2.1.325 handed its shell for a Bash call of `true`.
LINE_325 = ShellText(
    r"source /home/nicolas/.claude/shell-snapshots/snapshot-bash-1794640632155-gqwqsq.sh 2>/dev/null || true && "
    r"\builtin shopt -u extglob 2>/dev/null || true && { \builtin unalias -- 'which'; \builtin unset -f -- 'which'; } "
    r">/dev/null 2>&1 || true && { \builtin unalias -- 'unsetenv'; \builtin unset -f -- 'unsetenv'; } >/dev/null 2>&1 "
    r"|| true && eval true < /dev/null && pwd -P >| /tmp/cc-3391419-4873b120/claude-5ca9-cwd")
# Claude Code 2.1.319 wrote the extglob element without its \builtin, the rest alike.
EXTGLOB_325, EXTGLOB_319 = ShellText(r"\builtin shopt -u extglob"), ShellText("shopt -u extglob")
SNAPSHOT = ShellText("/home/nicolas/.claude/shell-snapshots/snapshot-bash-1794640632155-gqwqsq.sh")
CWD_FILE = ShellText("/tmp/cc-3391419-4873b120/claude-5ca9-cwd")
STAND_IN = Source(f"#!{PYTHON} -I\nimport json, os, sys\n"
                  "with open(sys.argv[0] + '.json', 'w', encoding='utf-8') as out:\n"
                  "    json.dump({'argv': sys.argv[1:], 'env': dict(os.environ)}, out)\n"
                  "sys.exit(7)\n")
DECOY = Source("#!/bin/sh\necho decoy\nexit 97\n")
CALLED = ExitStatus(7)
EXECUTABLE, PLAIN = Mode(0o755), Mode(0o644)
C = ShellText("-c")
OTHER = ShellText("echo other")


class Called(TypedDict):
    """What the stand-in claude-call recorded: the arguments after its path, and its environment."""

    argv: list[ShellText]
    env: dict[VarName, ShellText]


class Ran(NamedTuple):
    """A start of the wrapper copy: its exit status and what it wrote."""

    status: ExitStatus
    stdout: Output
    stderr: Output


class Wrapper(unittest.TestCase):
    """A case with a kit holding a copy of the wrapper and a stand-in claude-call, a home holding the line's snapshot,
    and a decoy bash, every path in the case's own folder."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.wrapper = self.root / "kit" / "bin" / "route" / "bash"
        self.wrapper.parent.mkdir(parents=True)
        _ = shutil.copy(route.WRAPPER, self.wrapper)
        self.call = self.write(self.root / "kit" / "call" / "claude-call", STAND_IN, EXECUTABLE)
        decoy = self.write(self.root / "decoys" / "bash", DECOY, EXECUTABLE)
        self.home = self.root / "home"
        self.snapshot = self.write(self.home / ".claude" / "shell-snapshots" / Path(SNAPSHOT).name, Source(":\n"),
                                   PLAIN)
        self.cwd_file = self.root / Path(CWD_FILE).name
        self.env: dict[VarName, ShellText] = {
            VarName("PATH"): ShellText(f"{decoy.parent}:/usr/bin:/bin"), VarName("SHELL"): ShellText(str(decoy)),
            VarName("HOME"): ShellText(str(self.home)), VarName("CLAUDECODE"): ShellText("1"),
            VarName("MARK"): ShellText("route-wrapper")}

    def write(self, path: Path, text: Source, mode: Mode) -> Path:
        """Write ``text`` at ``path`` with ``mode``, its folders made; its path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def line(self, text: ShellText = LINE_325) -> ShellText:
        """``text`` with its snapshot in the case's home and its cwd file in the case's folder."""
        self.assertEqual((text.count(SNAPSHOT), text.count(CWD_FILE)), (1, 1))
        return ShellText(text.replace(SNAPSHOT, str(self.snapshot)).replace(CWD_FILE, str(self.cwd_file)))

    def ran(self, *args: ShellText, stdin: Source = Source("")) -> Ran:
        """The wrapper copy run with ``args`` in the case's folder, ``stdin`` its input."""
        got = subprocess.run([str(self.wrapper), *args], env=dict(self.env), cwd=self.root, input=stdin,
                             capture_output=True, text=True, timeout=30, check=False)
        return Ran(ExitStatus(got.returncode), Output(got.stdout), Output(got.stderr))

    def called(self) -> Called | None:
        """What the stand-in recorded, None where it never ran."""
        record = self.call.with_name(f"{self.call.name}.json")
        if not record.exists():
            return None
        got: Called = json.loads(record.read_text(encoding="utf-8"))
        return got

    def assert_wrapped(self, line: ShellText) -> None:
        """``-c line`` reaches the stand-in whole, its status the wrapper's, and never runs in bash."""
        got = self.ran(C, line)
        called = self.called()
        assert called is not None, got.stderr
        self.assertEqual((got.status, called["argv"], called["env"].get(VarName("CLAUDECODE")), self.cwd_file.exists()),
                         (CALLED, [line], "1", False))

    def assert_in_bash(self, *args: ShellText) -> None:
        """The start ``args`` runs the case's line in /usr/bin/bash: its cwd file written, the stand-in never run."""
        got = self.ran(*args)
        written = self.cwd_file.read_text(encoding="utf-8") if self.cwd_file.exists() else None
        self.assertEqual((got.status, got.stdout, written, self.called()), (0, "", f"{self.root}\n", None))

    def test_the_2_1_325_line_reaches_claude_call_whole(self) -> None:
        self.assert_wrapped(self.line())

    def test_the_2_1_319_line_reaches_claude_call_whole(self) -> None:
        self.assertEqual(LINE_325.count(EXTGLOB_325), 1)
        self.assert_wrapped(self.line(ShellText(LINE_325.replace(EXTGLOB_325, EXTGLOB_319))))

    def test_env_runs_in_bash(self) -> None:
        got = self.ran(C, ShellText("env"))
        self.assertEqual((got.status, "MARK=route-wrapper" in got.stdout.splitlines(), self.called()), (0, True, None))

    def test_the_snapshot_start_runs_in_bash(self) -> None:
        made = self.root / "snapshot.sh"
        body = ShellText(f'SNAPSHOT_FILE={made}\n  echo "# Snapshot file" >| "$SNAPSHOT_FILE"\n')
        got = self.ran(C, ShellText("-l"), body)
        written = made.read_text(encoding="utf-8") if made.exists() else None
        self.assertEqual((got.status, written, self.called()), (0, "# Snapshot file\n", None))

    def test_a_line_of_another_shape_runs_in_bash(self) -> None:
        start = ShellText(f"source {self.snapshot} 2>/dev/null || true")
        for line in (OTHER, ShellText(f"{start} && {OTHER}"), ShellText(f"{OTHER} && pwd -P >| {self.cwd_file}")):
            with self.subTest(line=line):
                got = self.ran(C, line)
                self.assertEqual((got.status, got.stdout, self.called()), (0, "other\n", None))

    def test_without_claudecode_the_line_runs_in_bash(self) -> None:
        del self.env[VarName("CLAUDECODE")]
        self.assert_in_bash(C, self.line())

    def test_inside_claude_call_the_line_runs_in_bash(self) -> None:
        self.env[VarName("CLAUDE_CALL")] = ShellText("1")
        self.assert_in_bash(C, self.line())

    def test_the_off_file_claude_call_off_names_sends_the_line_to_bash(self) -> None:
        off = self.write(self.root / "off", Source(""), PLAIN)
        self.env[VarName("CLAUDE_CALL_OFF")] = ShellText(str(off))
        self.assert_in_bash(C, self.line())

    def test_the_off_file_in_home_sends_the_line_to_bash(self) -> None:
        _ = self.write(self.home / ".claude" / "claude-call.off", Source(""), PLAIN)
        self.assert_in_bash(C, self.line())

    def test_a_claude_call_not_executable_sends_the_line_to_bash(self) -> None:
        self.call.chmod(PLAIN)
        self.assert_in_bash(C, self.line())

    def test_a_missing_interpreter_sends_the_line_to_bash(self) -> None:
        text = self.wrapper.read_text(encoding="utf-8")
        self.assertEqual(text.count(PYTHON), 1)
        _ = self.wrapper.write_text(text.replace(PYTHON, str(self.root / "python3.14")), encoding="utf-8")
        self.assert_in_bash(C, self.line())

    def test_three_arguments_run_in_bash(self) -> None:
        self.assert_in_bash(C, self.line(), ShellText("extra"))

    def test_two_arguments_not_opening_with_c_run_in_bash(self) -> None:
        got = self.ran(ShellText("-s"), self.line(), stdin=Source("echo from-stdin\n"))
        self.assertEqual((got.status, got.stdout, self.called()), (0, "from-stdin\n", None))

    def test_the_wrapper_is_the_kits_bin_route_bash_named_bash_and_executable(self) -> None:
        self.assertEqual((route.WRAPPER, route.WRAPPER.name, route.WRAPPER.is_file(),
                          os.access(route.WRAPPER, os.X_OK)), (KIT / "bin" / "route" / "bash", "bash", True, True))
