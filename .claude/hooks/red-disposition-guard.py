#!/home/nicolas/.local/bin/python3.14
"""Keep a turn from ending while a check that did not pass is carried without a disposition.

THE RULE (user, 2026-10-10, held by a hook from 2026-10-14): a defect found is fixed now or becomes a task in the
same turn, and a check that did not reach its claim is a defect. Its cause, its age and its "known" status are never
a disposition.

WHY A HOOK AND NOT A NOTE. The rule was a memory carrying a list of excuse words, "known" among them, and it missed
three times: on 2026-10-14 a probe that stopped before its claim was carried through four store records as "the
known one failure", the word on the list and the sentence sent all the same. A list of words is a check on prose,
and it fails exactly when the excuse reads as analysis. This hook reads the results instead.

WHEN IT LOOKS. At the end of every turn (Stop).

ARM ONE, THE RED LEDGER. In a git work tree holding a probe store's record, `tools/ci-output/probes/timings.tsv`
(columns probe, verdict, seconds), every probe whose verdict is not PASS must have a row in the `## Red checks`
section of the task list beside the session's transcript (`<project dir>/memory/TASKS.md`), and every row must
name a probe the record does not pass. A row is a list item whose first code span is the probe's file name, followed
by a task id (`N.43`). A red with no row, a row with no task id, and a row whose probe passes or is gone are each a
finding. A work tree without the record is not read.

ARM TWO, THE HAND-OVER'S COUNTS. Where the session has a task list, so a task id is there to name, in the turn's
last message a count of results that passed below its total (`206
of 207 pass`, `passed 20 of 21`) must share its paragraph or list item with a task id, or with the words pending,
still running, queued or in progress for checks that have not ended. A count inside a code span or a quotation is an
example the message cites, not a result it reports, and is not read.

WHAT IT SAYS. Exit 2 with the findings on stderr, which keeps the turn going until each red is fixed or named with its
task. An event it cannot read, or an error of its own, lets the turn end, reported.

KILL SWITCH: while ~/.claude/red-disposition-guard.off exists the hook refuses nothing.
"""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _shell  # noqa: E402

# A path as the harness's event spells it; prose the hook reads or prints; a probe's file name (`x--y.sh`); a
# verdict as the store's record spells it; the hook's answer: 0 lets the turn end, 1 reports an error of its own,
# 2 keeps the turn going with its findings.
FilePath = NewType("FilePath", str)
Prose = NewType("Prose", str)
ProbeName = NewType("ProbeName", str)
Verdict = NewType("Verdict", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

KILL_SWITCH = Path.home() / ".claude" / "red-disposition-guard.off"
RECORD = Path("tools/ci-output/probes/timings.tsv")
PASS = Verdict("PASS")
LEDGER_HEADING = "## Red checks"
_TASK_ID = re.compile(r"\b[A-Z]\.[0-9]+\b")
_ROW = re.compile(r"^[-*] `([^`]+)`(.*)$")
# A count of results that passed, in either order: "206 of 207 pass", "passed 20 of 21".
_COUNT_THEN_PASS = re.compile(
    r"\b([0-9]+) of (?:the )?([0-9]+)\b(?:\s+[A-Za-z-]+){0,3}?\s+(?:pass|passes|passed|passing|green|succeeded)\b",
    re.IGNORECASE)
_PASS_THEN_COUNT = re.compile(r"\b(?:pass|passes|passed|green|succeeded)\b[:,]?\s+([0-9]+) of (?:the )?([0-9]+)\b",
                              re.IGNORECASE)
_NOT_ENDED = re.compile(r"\b(?:pending|still running|queued|in progress)\b", re.IGNORECASE)
# What a message cites rather than reports: a code span, or a quotation in straight or curly double quotes.
_CITED = re.compile(r"`[^`]*`|\"[^\"]*\"|\u201c[^\u201d]*\u201d")
_ITEM = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s")


class Event(TypedDict, total=False):
    """The Stop event the harness hands the hook."""

    hook_event_name: Literal["Stop"]
    cwd: FilePath
    transcript_path: FilePath
    last_assistant_message: Prose


class Row(NamedTuple):
    """One row of the red ledger: its probe and whether it names a task."""

    probe: ProbeName
    has_task: bool


def work_tree(cwd: Path) -> Path | None:
    """Return the top of the git work tree holding ``cwd``, or None outside one."""
    out = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=False)
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def record_verdicts(record: Path) -> dict[ProbeName, Verdict]:
    """Read the store's record: each probe's file name and its verdict."""
    with record.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    return {ProbeName(Path(row["probe"]).name): Verdict(row["verdict"]) for row in rows}


def ledger_rows(tasks: Prose) -> list[Row] | None:
    """Read the rows of the task list's red ledger, or None when the list has no such section."""
    lines = tasks.splitlines()
    if LEDGER_HEADING not in lines:
        return None
    rows: list[Row] = []
    for line in lines[lines.index(LEDGER_HEADING) + 1:]:
        if line.startswith(("# ", "## ")):
            break
        if match := _ROW.match(line):
            rows.append(Row(ProbeName(match[1]), bool(_TASK_ID.search(match[2]))))
    return rows


def ledger_findings(verdicts: dict[ProbeName, Verdict], rows: list[Row] | None) -> list[Prose]:
    """Hold the ledger to the record both ways."""
    red = sorted(probe for probe, verdict in verdicts.items() if verdict != PASS)
    if rows is None:
        return [Prose(f"{probe} is {verdicts[probe]} in the store's record and the task list has no "
                      f"'{LEDGER_HEADING}' section") for probe in red]
    named = {row.probe for row in rows}
    found = [Prose(f"{probe} is {verdicts[probe]} in the store's record and no '{LEDGER_HEADING}' row names it")
             for probe in red if probe not in named]
    for row in rows:
        if row.probe not in verdicts:
            found.append(Prose(f"the '{LEDGER_HEADING}' row for {row.probe} names a probe the record does not hold"))
        elif verdicts[row.probe] == PASS:
            found.append(Prose(f"the '{LEDGER_HEADING}' row for {row.probe} is stale: the record passes it"))
        elif not row.has_task:
            found.append(Prose(f"the '{LEDGER_HEADING}' row for {row.probe} names no task"))
    return found


def units(message: Prose) -> list[Prose]:
    """Split a message into paragraphs, each list item a unit of its own."""
    found: list[list[Prose]] = []
    for line in map(Prose, message.splitlines()):
        if not line.strip():
            found.append([])
        elif _ITEM.match(line) or not found:
            found.append([line])
        else:
            found[-1].append(line)
    return [Prose(" ".join(unit)) for unit in found if unit]


def count_findings(message: Prose) -> list[Prose]:
    """Name each count of passes below its total that stands without a task id or a not-ended word."""
    found: list[Prose] = []
    for unit in units(message):
        if _TASK_ID.search(unit) or _NOT_ENDED.search(unit):
            continue
        reported = _CITED.sub(" ", unit)
        for pattern in (_COUNT_THEN_PASS, _PASS_THEN_COUNT):
            for match in pattern.finditer(reported):
                if int(match[1]) < int(match[2]):
                    found.append(Prose(f"'{match[0]}' leaves {int(match[2]) - int(match[1])} without a disposition"))
    return found


def handle(event: Event) -> ExitStatus:
    """Answer one Stop event."""
    findings: list[Prose] = []
    cwd, transcript = event.get("cwd"), event.get("transcript_path")
    tasks = Path(transcript).parent / "memory" / "TASKS.md" if transcript else None
    listed = tasks is not None and tasks.is_file()
    top = work_tree(Path(cwd)) if cwd else None
    if top is not None and (top / RECORD).is_file():
        text = Prose(tasks.read_text(encoding="utf-8")) if tasks is not None and listed else Prose("")
        findings += ledger_findings(record_verdicts(top / RECORD), ledger_rows(text))
    if listed:
        findings += count_findings(event.get("last_assistant_message") or Prose(""))
    if not findings:
        return ALLOW
    print("red-disposition-guard: a check that did not pass is carried without a disposition. Fix it now, or name "
          "its task: a red probe gets a row under '## Red checks' in the task list (`<probe file>` and the task id), "
          "and a count below its total in the hand-over names the task in the same paragraph.\n  "
          + "\n  ".join(findings), file=sys.stderr)
    return REFUSE


def main() -> ExitStatus:
    """Read the event and hold the turn's end to the rule; an error of the hook's own lets the turn end, reported."""
    _shell.bound_memory()
    if KILL_SWITCH.exists():
        return ALLOW
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return ALLOW  # a hook that cannot read its event must not block the session
    if not isinstance(event, dict) or event.get("hook_event_name") != "Stop":
        return ALLOW
    try:
        return handle(cast("Event", event))
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(f"red-disposition-guard: the turn's end went unchecked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return REPORTED


if __name__ == "__main__":
    sys.exit(main())
