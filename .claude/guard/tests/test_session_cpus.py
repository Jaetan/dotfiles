"""The session-cpus rule, through the dispatcher with every other rule switched off: a Bash or Monitor call is refused
while the claude process CLAUDE_PID names runs outside the machine's slice or may run on a CPU outside its agents',
or while that process or a file its facts need cannot be read, each line naming what it found and the fact it breaks,
the refusal naming the machine file and session-limits; a machine table declaring `absent` bounds nothing for its fact.

Every case reads a proc tree of its own, whose boot_id and PID/stat, PID/cgroup and PID/status files it writes; the
measured shapes of 2026-11-08 are its defaults."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher.core import Answer, EventBytes, FolderPath, HookEvent, Settings, ToolName, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from state.session import Pid
from state.session import Places as StatePlaces
from tests.test_declarations import TomlSource, declaring

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = "session_cpus"
MARK = Prose("guard session-cpus: this call runs under a claude process outside what the machine file ")
ProcText = NewType("ProcText", str)
PID = Pid(4242)
PASSED = Answer(0, Prose(""), Prose(""))
SCOPE = "/user.slice/user-1000.slice/user@1000.service/claude.slice/run-p1610831-i7045040.scope"
APP = "/user.slice/user-1000.slice/user@1000.service/app.slice/run-p7-i8.scope"
CGROUP = ProcText(f"0::{SCOPE}\n")
STATUS = ProcText("Name:\tclaude\nCpus_allowed:\tfffff\nCpus_allowed_list:\t0-19\nMems_allowed_list:\t0\n")
CPUS = TomlSource('[cpus]\nagents = "0-19"\n')
COMMAS = TomlSource('[cpus]\nagents = "0-3,8-9,12"\n')
SLICE = TomlSource('[slice]\nname = "claude.slice"\n')
BOTH = declaring(cpus=CPUS, slice=SLICE)
SLICE_HELD = Prose("claude.slice, the machine's slice ([slice] name)")
CPUS_HELD = Prose("0-19, the agents' CPUs ([cpus] agents)")


def status(listed: ProcText) -> ProcText:
    """A status file whose Cpus_allowed_list is ``listed``."""
    return ProcText(STATUS.replace("\t0-19\n", f"\t{listed}\n"))


class Places(NamedTuple):
    """A case's folders: the home holding the machine file, the proc tree, and the switch folder."""

    base: Path
    home: Path
    proc: Path
    off: Path


class Case(unittest.TestCase):
    """A fresh home, proc tree and switch folder per test, every rule but session-cpus switched off; the machine file
    declares both facts and process PID runs in the measured scope on the measured CPUs until a test says otherwise."""

    at: Places

    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp()).resolve()
        self.at = Places(base, base / "home", base / "proc", base / "off")
        for folder in (self.at.home / ".config" / "claude-guard", self.at.proc / "sys" / "kernel" / "random",
                       self.at.off):
            folder.mkdir(parents=True)
        _ = (self.at.proc / "sys" / "kernel" / "random" / "boot_id").write_text("bbbb-0001\n", encoding="ascii")
        for module in (KIT / "rules").glob("*.py"):
            if not module.stem.startswith("_") and module.stem != RULE:
                (self.at.off / module.stem.replace("_", "-")).touch()
        self.machine(BOTH)
        self.process()

    def tearDown(self) -> None:
        shutil.rmtree(self.at.base)

    def machine(self, text: TomlSource) -> None:
        """Write ``text`` as the machine file."""
        _ = (self.at.home / ".config" / "claude-guard" / "machine.toml").write_text(text, encoding="utf-8")

    def process(self, cgroup: ProcText | None = CGROUP, status_text: ProcText | None = STATUS) -> None:
        """Process PID, running, with the cgroup and status files given, a file None missing."""
        folder = self.at.proc / str(PID)
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        _ = (folder / "stat").write_text(f"{PID} (claude) S {' '.join(['0'] * 18)} 100 0 0\n", encoding="utf-8")
        for name, text in (("cgroup", cgroup), ("status", status_text)):
            if text is not None:
                _ = (folder / name).write_text(text, encoding="utf-8")

    def judged(self, tool: ToolName = ToolName("Bash"), event: HookEvent = "PreToolUse",
               pid: ShellText | None = ShellText(str(PID))) -> Answer:
        """The dispatcher's answer to ``event`` for a ``tool`` call, CLAUDE_PID ``pid``, unset where None."""
        env = {VarName("HOME"): ShellText(str(self.at.home))}
        if pid is not None:
            env[VarName("CLAUDE_PID")] = pid
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.at.off)),
                            ReadSettings(READER, Seconds(5.0), env), home=self.at.home,
                            places=StatePlaces(self.at.base / "sessions", self.at.proc))
        fields = {"hook_event_name": event, "tool_name": tool, "cwd": str(self.at.base),
                  "tool_input": {"command": "true"}}
        return answer(EventBytes(json.dumps(fields).encode()), settings)

    def refused(self, *lines: Prose, tool: ToolName = ToolName("Bash"), pid: ShellText | None = ShellText(str(PID)),
                ) -> Prose:
        """The refusal of the call, holding each of ``lines`` as a line of its own; its stderr."""
        got = self.judged(tool=tool, pid=pid)
        self.assertEqual(got.status, 2, got.stderr)
        for line in lines:
            self.assertIn(f"\n  * {line}\n", got.stderr)
        self.assertEqual(got.stderr.count("\n  * "), len(lines), got.stderr)
        return got.stderr


class Passes(Case):
    """A claude process inside the slice, a slice beneath it included, on the agents' CPUs or fewer, passes."""

    def test_the_measured_shape(self) -> None:
        self.assertEqual(self.judged(), PASSED)

    def test_a_slice_beneath_the_declared_one(self) -> None:
        self.process(cgroup=ProcText(f"0::{SCOPE.replace('claude.slice/', 'claude.slice/claude-work.slice/')}\n"))
        self.assertEqual(self.judged(), PASSED)

    def test_cgroup_v1_lines_beside_the_unified_one(self) -> None:
        self.process(cgroup=ProcText(f"12:cpuset:/\n4:memory:/user.slice\n0::{SCOPE}\n"))
        self.assertEqual(self.judged(), PASSED)

    def test_a_scope_whose_name_is_not_utf8(self) -> None:
        _ = (self.at.proc / str(PID) / "cgroup").write_bytes(f"0::{SCOPE}".encode() + b"\xff\n")
        self.assertEqual(self.judged(), PASSED)

    def test_a_range_list_with_commas(self) -> None:
        self.machine(declaring(cpus=COMMAS, slice=SLICE))
        self.process(status_text=status(ProcText("0-3,8,12")))
        self.assertEqual(self.judged(), PASSED)

    def test_fewer_cpus_than_the_agents(self) -> None:
        self.process(status_text=status(ProcText("2-5")))
        self.assertEqual(self.judged(), PASSED)

    def test_monitor_alike(self) -> None:
        self.assertEqual(self.judged(tool=ToolName("Monitor")), PASSED)


class Refused(Case):
    """Each line names what was found and the fact it breaks."""

    def test_outside_the_slice(self) -> None:
        self.process(cgroup=ProcText(f"0::{APP}\n"))
        self.refused(Prose(f"claude, process {PID}, runs in the cgroup {APP}, outside {SLICE_HELD}"))

    def test_a_segment_only_holding_the_slices_name(self) -> None:
        path = APP.replace("app.slice", "app.slice/notclaude.slice")
        self.process(cgroup=ProcText(f"0::{path}\n"))
        self.refused(Prose(f"claude, process {PID}, runs in the cgroup {path}, outside {SLICE_HELD}"))

    def test_a_v1_line_in_the_slice_beside_a_unified_line_outside(self) -> None:
        self.process(cgroup=ProcText(f"4:cpu:{SCOPE}\n0::{APP}\n"))
        self.refused(Prose(f"claude, process {PID}, runs in the cgroup {APP}, outside {SLICE_HELD}"))

    def test_no_unified_line(self) -> None:
        self.process(cgroup=ProcText(f"4:cpu:{SCOPE}\n"))
        self.refused(Prose(f"the cgroup file of claude, process {PID}, has no unified `0::` line, so nothing holds it "
                           f"to {SLICE_HELD}"))

    def test_a_cgroup_that_cannot_be_read(self) -> None:
        self.process(cgroup=None)
        missing = self.at.proc / str(PID) / "cgroup"
        self.refused(Prose(f"the cgroup of claude, process {PID}, cannot be read ([Errno 2] No such file or directory: "
                           f"'{missing}'), so nothing holds it to {SLICE_HELD}"))

    def test_a_cpu_outside_the_agents(self) -> None:
        self.process(status_text=status(ProcText("0-23")))
        self.refused(Prose(f"claude, process {PID}, may run on CPUs 0-23, 20-23 of them off {CPUS_HELD}"))

    def test_a_range_list_with_commas_outside(self) -> None:
        self.machine(declaring(cpus=COMMAS, slice=SLICE))
        self.process(status_text=status(ProcText("0-3,8-10,12-14")))
        self.refused(Prose(f"claude, process {PID}, may run on CPUs 0-3,8-10,12-14, 10,13-14 of them off "
                           "0-3,8-9,12, the agents' CPUs ([cpus] agents)"))

    def test_no_cpu_list(self) -> None:
        self.process(status_text=ProcText("Name:\tclaude\nCpus_allowed:\tfffff\n"))
        self.refused(Prose(f"the status of claude, process {PID}, has no Cpus_allowed_list, so nothing holds it to "
                           f"{CPUS_HELD}"))

    def test_a_cpu_list_that_is_no_list(self) -> None:
        self.process(status_text=status(ProcText("0-x")))
        self.refused(Prose(f"the status of claude, process {PID}, lists '0-x' as its CPUs, no CPU list, so nothing "
                           f"holds it to {CPUS_HELD}"))

    def test_a_status_that_cannot_be_read(self) -> None:
        self.process(status_text=None)
        missing = self.at.proc / str(PID) / "status"
        self.refused(Prose(f"the status of claude, process {PID}, cannot be read ([Errno 2] No such file or directory: "
                           f"'{missing}'), so nothing holds it to {CPUS_HELD}"))

    def test_both_named(self) -> None:
        self.process(cgroup=ProcText(f"0::{APP}\n"), status_text=status(ProcText("0-23")))
        self.refused(Prose(f"claude, process {PID}, runs in the cgroup {APP}, outside {SLICE_HELD}"),
                     Prose(f"claude, process {PID}, may run on CPUs 0-23, 20-23 of them off {CPUS_HELD}"))

    def test_a_missing_claude_pid(self) -> None:
        self.refused(Prose("CLAUDE_PID is '', not a process id, so the session state has no key; nothing holds the "
                           f"claude process to {SLICE_HELD} and {CPUS_HELD}"), pid=None)

    def test_a_process_gone(self) -> None:
        shutil.rmtree(self.at.proc / str(PID))
        self.refused(Prose(f"CLAUDE_PID {PID} names no running process, so the session state has no key; nothing "
                           f"holds the claude process to {SLICE_HELD} and {CPUS_HELD}"))

    def test_monitor(self) -> None:
        self.process(cgroup=ProcText(f"0::{APP}\n"))
        self.refused(Prose(f"claude, process {PID}, runs in the cgroup {APP}, outside {SLICE_HELD}"),
                     tool=ToolName("Monitor"))

    def test_the_refusal_names_the_machine_file_and_the_way_out(self) -> None:
        self.process(cgroup=ProcText(f"0::{APP}\n"))
        said = self.refused(Prose(f"claude, process {PID}, runs in the cgroup {APP}, outside {SLICE_HELD}"))
        self.assertTrue(said.startswith(f"{MARK}{self.at.home}/.config/claude-guard/machine.toml declares.\n\n"), said)
        self.assertIn("\n\nStart claude through ~/.local/bin/session-limits, which the shell function `claude` runs:",
                      said)


class Absent(Case):
    """A machine table declaring `absent` bounds nothing for its fact, whose file is not read."""

    def test_no_slice(self) -> None:
        self.machine(declaring(cpus=CPUS))
        self.process(cgroup=None)
        self.assertEqual(self.judged(), PASSED)
        self.process(cgroup=ProcText(f"0::{APP}\n"), status_text=status(ProcText("0-23")))
        self.refused(Prose(f"claude, process {PID}, may run on CPUs 0-23, 20-23 of them off {CPUS_HELD}"))

    def test_no_agents_cpus(self) -> None:
        self.machine(declaring(slice=SLICE))
        self.process(status_text=None)
        self.assertEqual(self.judged(), PASSED)
        self.process(cgroup=ProcText(f"0::{APP}\n"), status_text=status(ProcText("0-23")))
        self.refused(Prose(f"claude, process {PID}, runs in the cgroup {APP}, outside {SLICE_HELD}"))

    def test_one_fact_held_while_claude_pid_is_missing(self) -> None:
        self.machine(declaring(slice=SLICE))
        self.refused(Prose("CLAUDE_PID is '', not a process id, so the session state has no key; nothing holds the "
                           f"claude process to {SLICE_HELD}"), pid=None)

    def test_neither(self) -> None:
        self.machine(declaring())
        self.assertEqual(self.judged(pid=None), PASSED)


class Reach(Case):
    """Only a Bash or Monitor call at PreToolUse is judged, and the rule's switch turns it off."""

    def setUp(self) -> None:
        super().setUp()
        self.process(cgroup=ProcText(f"0::{APP}\n"))

    def test_another_tool(self) -> None:
        self.assertEqual(self.judged(tool=ToolName("Read")), PASSED)

    def test_another_event(self) -> None:
        self.assertEqual(self.judged(event="PostToolUse"), PASSED)

    def test_switched_off(self) -> None:
        (self.at.off / "session-cpus").touch()
        self.assertEqual(self.judged(), PASSED)


if __name__ == "__main__":
    unittest.main()
