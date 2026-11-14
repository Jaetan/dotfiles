"""Every Bash and Monitor call runs under a claude process held to the machine's slice and its agents' CPUs.

THE RULE (user, 2026-11-08). A fail-closed session check holds the user's CPUs whether or not a call's own scope
starts: ~/.local/bin/session-limits, which the shell function `claude` runs, starts claude in the machine's slice under
`taskset -c` of the agents' CPUs, and every process claude starts inherits both. A Bash or Monitor call is refused
while the claude process CLAUDE_PID names, read as the session state keys it, runs in a cgroup whose path, the unified
`0::` line of /proc/PID/cgroup, has no segment equal to the machine's slice (Machine.slice; a slice beneath it keeps
that segment), or while its Cpus_allowed_list in /proc/PID/status names a CPU outside the agents' (Machine.agent_cpus);
and refused where CLAUDE_PID names no process the kit reads or a file its facts need cannot be read. A machine table
declaring `absent` bounds nothing for its fact, whose file is then not read.
"""

from itertools import groupby
from pathlib import Path
from typing import Literal, NewType

from declarations.schema import CpuNumber, Reading, SliceName, TomlKey, TomlText, cpu_list
from dispatcher.core import Event, HookEvent, ToolName, state_places
from dispatcher.verdict import KitFault, Prose, Verdict, refuse
from state.session import Pid, StateFault, process_key

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
ProcFile = Literal["cgroup", "status"]
ProcText = NewType("ProcText", str)
Prefix = NewType("Prefix", str)
CpuText = NewType("CpuText", str)
UNIFIED = Prefix("0::")
CPU_LIST = Prefix("Cpus_allowed_list:")
ADVICE = """this call runs under a claude process outside what the machine file {file} declares.

{problems}

Start claude through ~/.local/bin/session-limits, which the shell function `claude` runs: it starts claude in the
machine's slice on the agents' CPUs, and every call it runs inherits both, whether or not the call's own scope starts."""


def spelled(cpus: frozenset[CpuNumber]) -> CpuText:
    """``cpus`` as taskset -c spells a list, each run of consecutive CPUs a range."""
    runs = [[cpu for _, cpu in run] for _, run in groupby(enumerate(sorted(cpus)), lambda pair: pair[1] - pair[0])]
    return CpuText(",".join(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs))


def slice_held(declared: SliceName | None) -> Prose | None:
    """The machine's slice as a refusal names it, None where its table declares `absent`."""
    return None if declared is None else Prose(f"{declared}, the machine's slice ([slice] name)")


def cpus_held(agents: frozenset[CpuNumber] | None) -> Prose | None:
    """The agents' CPUs as a refusal names them, None where their table declares `absent`."""
    return None if agents is None else Prose(f"{spelled(agents)}, the agents' CPUs ([cpus] agents)")


def read(proc: Path, pid: Pid, name: ProcFile) -> ProcText:
    """The file ``name`` of process ``pid`` under the proc tree ``proc``; OSError where it cannot be read."""
    return ProcText((proc / str(pid) / name).read_text(encoding="utf-8", errors="replace"))


def after(text: ProcText, prefix: Prefix) -> ProcText | None:
    """What follows ``prefix`` on the first line of ``text`` it starts, stripped; None where no line starts with it."""
    return next((ProcText(line.removeprefix(prefix).strip()) for line in text.splitlines() if line.startswith(prefix)),
                None)


def in_slice(proc: Path, pid: Pid, declared: SliceName | None) -> Prose | None:
    """What shows process ``pid`` outside the slice ``declared``, None where it runs in it or none is declared."""
    if declared is None:
        return None
    held = slice_held(declared)
    try:
        path = after(read(proc, pid, "cgroup"), UNIFIED)
    except OSError as fault:
        return Prose(f"  * the cgroup of claude, process {pid}, cannot be read ({fault}), so nothing holds it to {held}")
    if path is None:
        return Prose(f"  * the cgroup file of claude, process {pid}, has no unified `0::` line, so nothing holds it to "
                     f"{held}")
    if declared in path.split("/"):
        return None
    return Prose(f"  * claude, process {pid}, runs in the cgroup {path}, outside {held}")


def on_cpus(proc: Path, pid: Pid, agents: frozenset[CpuNumber] | None) -> Prose | None:
    """What shows process ``pid`` allowed a CPU outside ``agents``, None where it is not or none are declared."""
    if agents is None:
        return None
    held = cpus_held(agents)
    try:
        listed = after(read(proc, pid, "status"), CPU_LIST)
    except OSError as fault:
        return Prose(f"  * the status of claude, process {pid}, cannot be read ({fault}), so nothing holds it to {held}")
    if listed is None:
        return Prose(f"  * the status of claude, process {pid}, has no Cpus_allowed_list, so nothing holds it to {held}")
    try:  # the list taskset -c and the machine file spell, read by the machine file's own parser
        allowed = cpu_list(Reading(proc / str(pid) / "status"), TomlText(listed), TomlKey("Cpus_allowed_list"))
    except KitFault:
        return Prose(f"  * the status of claude, process {pid}, lists {listed!r} as its CPUs, no CPU list, so nothing "
                     f"holds it to {held}")
    outside = (allowed or frozenset()) - agents
    if not outside:
        return None
    return Prose(f"  * claude, process {pid}, may run on CPUs {listed}, {spelled(outside)} of them off {held}")


def judge(event: Event) -> Verdict | None:
    """Refuse a call whose claude process runs outside the machine's slice or its agents' CPUs, or cannot be read."""
    machine = event.machine()
    held = [fact for fact in (slice_held(machine.slice), cpus_held(machine.agent_cpus)) if fact is not None]
    if not held:
        return None
    proc = state_places(event.places).proc
    try:
        pid = process_key(event.reading.env, proc).pid
    except StateFault as fault:
        problems = [Prose(f"  * {fault.reason}; nothing holds the claude process to {' and '.join(held)}")]
    else:
        found = (in_slice(proc, pid, machine.slice), on_cpus(proc, pid, machine.agent_cpus))
        problems = [problem for problem in found if problem is not None]
    if not problems:
        return None
    return refuse(Prose(ADVICE.format(file=event.declarations_of().machine_file, problems="\n".join(problems))))
