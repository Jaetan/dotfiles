"""A turn ends while a probe of the project's record is red only when its last message names that probe.

THE RULE (user, 2026-11-10). A red probe is never left unhandled, on a first try or a later one: better to talk about
it. So at each Stop, every probe of the project's probe record whose verdict is not exactly PASS is refused unless the
turn's last message names it by its file name, anywhere in it, a code span included. Every red left out is refused
together, at every attempt alike, whatever stop_hook_active says, so each turn ending with a red shows it to the user.

The record is the file the `[probes] record` key names in the declaration holding for the event's cwd: tab-separated,
its first line `probe`, `verdict`, `seconds`, each later row a probe's path, its verdict and its seconds. A probe's file
name is the last part of its path, `probes/x--y.sh` naming `x--y.sh`. A declared record that cannot be read so (missing,
unreadable, not UTF-8, another first line, a row not of those three fields or naming no file) is a fault of the kit
naming the file.

EXCEPTIONS (2), each with the failure it holds:
  * a Stop for which no declaration names a probe record, or which names no folder, is judged as nothing: there is no
    record to read, and taking the absence for a fault would refuse every turn of a project that keeps no probes;
  * an agent's end, a SubagentStop, is not judged: its event carries no last message, so every red would be refused
    with its one way out closed, and its final text goes to the thread that launched it, which names what it reports.
"""

from pathlib import Path
from typing import Final, NewType

from dispatcher.core import Event, HookEvent
from dispatcher.verdict import KitFault, Prose, Verdict, refuse

EVENTS: frozenset[HookEvent] = frozenset({"Stop"})
TOOLS = None
ProbeName = NewType("ProbeName", str)
"""A probe's file name, the last part of its path in the record: `x--y.sh`."""
ProbeVerdict = NewType("ProbeVerdict", str)
"""A probe's verdict as the record spells it, red unless it is exactly PASS."""
HEADER: Final = "probe\tverdict\tseconds"
PASS = ProbeVerdict("PASS")
ADVICE = """this turn's last message leaves red probes of the project's record unnamed.

{findings}

The record is {record}. A red probe is never left unhandled: make it pass and run the store again, or name each red
probe by its file name in this turn's last message, so the user sees it."""


def verdicts(record: Path) -> list[tuple[ProbeName, ProbeVerdict]]:
    """Each probe of ``record`` by its file name, with its verdict; a KitFault naming the file where it cannot be read
    as the module docstring says."""
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as fault:
        raise KitFault(Prose(f"the probe record {record} cannot be read: {fault}")) from None
    if lines[:1] != [HEADER]:
        raise KitFault(Prose(f"the probe record {record} does not open with the tab-separated header probe, verdict, "
                             "seconds"))
    found: list[tuple[ProbeName, ProbeVerdict]] = []
    for line in lines[1:]:
        fields = line.split("\t")
        name = ProbeName(Path(fields[0]).name)
        if len(fields) != 3 or not name:
            raise KitFault(Prose(f"the probe record {record} holds a row that is not a probe's path, its verdict and "
                                 f"its seconds: {line!r}"))
        found.append((name, ProbeVerdict(fields[1])))
    return found


def judge(event: Event) -> Verdict | None:
    """Refuse a Stop whose last message leaves a red probe of the project's record unnamed."""
    if event.cwd is None or (record := event.declaration(Path(event.cwd)).project.probe_record) is None:
        return None
    said = event.payload.get("last_assistant_message")
    message = Prose(said if isinstance(said, str) else "")
    found = [Prose(f"  * {name}, verdict '{verdict}'") for name, verdict in verdicts(record)
             if verdict != PASS and name not in message]
    return refuse(Prose(ADVICE.format(findings="\n".join(found), record=record))) if found else None
