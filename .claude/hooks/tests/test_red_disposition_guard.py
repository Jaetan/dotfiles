#!/usr/bin/env python3
"""Regression cases for red-disposition-guard.py.

Every case builds, under a fresh temporary directory, a git work tree (with the probe store's record when the case
gives one) and a project directory holding the session's transcript path and, when the case gives one, a task list.
It then pipes a Stop event to the hook, with HOME pointed at the case's own directory so the kill switch is the
case's, and compares the exit status with the rule: 2 keeps the turn going with a finding, 1 reports an error of the
hook's own, 0 lets the turn end. The hand-over sentences are the ones a session wrote, blocked or let through.

Usage: test_red_disposition_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType

# A file's text; a case's name; prose a case says or expects on stderr; a probe's file name; a verdict as the
# store's record spells it; the hook's exit status.
FileText = NewType("FileText", str)
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
ProbeName = NewType("ProbeName", str)
Verdict = NewType("Verdict", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)
PASS, FAIL, UNRUN = Verdict("PASS"), Verdict("FAIL"), Verdict("UNRUN")
type EventName = Literal["Stop", "PostToolUse"]

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "red-disposition-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="red-disposition-guard-test-")).resolve()
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

SLICES = ProbeName("tools_mutation_cpp_slices.py--the-sliced-union-is-the-unsliced-census.sh")
OTHER = ProbeName("README.md--an-unresolved-check-exits-zero.sh")
ROW = FileText(f"- `{SLICES}` — N.43: legs older than cpp/CMakeLists.txt; a pin-change gate")
NO_TASK_ROW = FileText(f"- `{SLICES}` — legs older than cpp/CMakeLists.txt")


def record(*rows: tuple[ProbeName, Verdict]) -> FileText:
    """The store's record with one row per (probe file, verdict)."""
    return FileText("probe\tverdict\tseconds\n" + "".join(f"probes/{p}\t{v}\t0.1\n" for p, v in rows))


def tasks(*rows: FileText, after: FileText = FileText("")) -> FileText:
    """A task list whose red ledger holds ``rows``, with ``after`` following the next section's heading."""
    body = "".join(f"{row}\n" for row in rows)
    return FileText(f"# Task list\n\n## Red checks\n\nWhat this section holds.\n\n{body}\n## A.1 — a task\n\n{after}")


class Case(NamedTuple):
    """One case: what the trees hold, what the turn's last message says, and the answer the rule wants."""

    name: CaseId
    want: ExitStatus
    message: Prose = Prose("Done.")
    record: FileText | None = None
    task_list: FileText | None = None
    in_git: bool = True
    subdir: bool = False
    kill_switch: bool = False
    stdin: FileText | None = None
    event_name: EventName = "Stop"
    says: Prose | None = None
    no_list: bool = False


CASES = [
    # Arm two: the hand-over's counts, as a session wrote them.
    Case(CaseId("store count below total, alone"), REFUSE,
         Prose("Store before: 206 of 207 pass on tree `5ebf3787`."), says=Prose("206 of 207 pass")),
    Case(CaseId("the excuse as written"), REFUSE,
         Prose("Store before: 206 of 207 pass. The one failure is the known stale local C++ mutation tree.")),
    Case(CaseId("every check passed"), ALLOW, Prose("22 of 22 pass, CLEAN, no threads.")),
    Case(CaseId("checks still running"), ALLOW, Prose("#402: 20 of 21 checks pass and one is still running.")),
    Case(CaseId("checks pending"), ALLOW, Prose("20 of 21 checks passed, one pending.")),
    Case(CaseId("a count that is not of passes"), ALLOW, Prose("The old probes catch 10 of the 11.")),
    Case(CaseId("a count with no total"), ALLOW, Prose("6 pass; ruff, basedpyright and pylint are clean.")),
    Case(CaseId("the task named beside the count"), ALLOW,
         Prose("Store before: 206 of 207 pass; the red is N.43's ledger row.")),
    Case(CaseId("pass word first, all passed"), ALLOW, Prose("The fast tier passed 18 of 18 steps.")),
    Case(CaseId("pass word first, one short"), REFUSE, Prose("The fast tier passed 17 of 18 steps.")),
    Case(CaseId("upper case"), REFUSE, Prose("ALL 58 OF 59 STEPS PASSED")),
    Case(CaseId("the task in another list item"), REFUSE,
         Prose("- Store: 206 of 207 pass\n- N.43 holds the next family")),
    Case(CaseId("the task in another paragraph"), REFUSE, Prose("206 of 207 pass.\n\nN.43 holds it.")),
    Case(CaseId("the task on the paragraph's next line"), ALLOW,
         Prose("206 of 207 pass,\nthe red being N.43's ledger row.")),
    Case(CaseId("the task in the list item's next line"), ALLOW,
         Prose("- Store: 206 of 207 pass,\n  the red being N.43's row\n- next item")),
    Case(CaseId("a quoted example"), ALLOW,
         Prose('- **Live check:** it refused a stop on "206 of 207 pass" and made the model continue.')),
    Case(CaseId("a curly-quoted example"), ALLOW, Prose("It refused a stop on \u201c206 of 207 pass\u201d.")),
    Case(CaseId("an example in a code span"), ALLOW, Prose("Arm two refuses `206 of 207 pass` and lets `22 of 22` through.")),
    Case(CaseId("a result beside a quoted example"), REFUSE,
         Prose('It refused "206 of 207 pass"; the store: 205 of 207 pass.'), says=Prose("205 of 207")),
    Case(CaseId("a count where the session has no task list"), ALLOW, Prose("Store before: 206 of 207 pass."),
         no_list=True),
    # Arm one: the red ledger against the store's record.
    Case(CaseId("no record: the ledger is not read"), ALLOW),
    Case(CaseId("all pass and no ledger"), ALLOW, record=record((SLICES, PASS), (OTHER, PASS))),
    Case(CaseId("a red and no task list"), REFUSE, record=record((SLICES, FAIL)), no_list=True, says=Prose(SLICES)),
    Case(CaseId("a red and no ledger section"), REFUSE, record=record((SLICES, FAIL)),
         task_list=FileText("# Task list\n\n## A.1 — a task\n")),
    Case(CaseId("a red with its row"), ALLOW, record=record((SLICES, FAIL), (OTHER, PASS)), task_list=tasks(ROW)),
    Case(CaseId("an unrun probe with its row"), ALLOW, record=record((SLICES, UNRUN)), task_list=tasks(ROW)),
    Case(CaseId("an unrun probe without a row"), REFUSE, record=record((SLICES, UNRUN)), task_list=tasks()),
    Case(CaseId("a row naming no task"), REFUSE, record=record((SLICES, FAIL)), task_list=tasks(NO_TASK_ROW),
         says=Prose("names no task")),
    Case(CaseId("a row whose probe passes"), REFUSE, record=record((SLICES, PASS)), task_list=tasks(ROW),
         says=Prose("stale")),
    Case(CaseId("a row whose probe is gone"), REFUSE, record=record((OTHER, PASS)), task_list=tasks(ROW),
         says=Prose("does not hold")),
    Case(CaseId("the row past the section's end"), REFUSE, record=record((SLICES, FAIL)),
         task_list=tasks(after=ROW)),
    Case(CaseId("the named probe elsewhere in the list"), REFUSE, record=record((SLICES, FAIL)),
         task_list=FileText(f"# Task list\n\n## Red checks\n\n## N.43 — probes\n\n- **Finding** `{SLICES}` refuses\n")),
    Case(CaseId("cwd below the work tree's top"), REFUSE, record=record((SLICES, FAIL)), subdir=True),
    Case(CaseId("outside a git work tree"), ALLOW, record=record((SLICES, FAIL)), in_git=False),
    Case(CaseId("both arms report"), REFUSE, Prose("Store: 206 of 207 pass."), record=record((SLICES, FAIL)),
         says=Prose("206 of 207")),
    # The hook itself.
    Case(CaseId("kill switch"), ALLOW, Prose("206 of 207 pass."), record=record((SLICES, FAIL)), kill_switch=True),
    Case(CaseId("not a Stop event"), ALLOW, Prose("206 of 207 pass."), event_name="PostToolUse"),
    Case(CaseId("unreadable event"), ALLOW, stdin=FileText("not json")),
    Case(CaseId("a record without a verdict column"), REPORTED,
         record=FileText(f"probe\tseconds\nprobes/{SLICES}\t0.1\n"), says=Prose("went unchecked")),
]


def run(case: Case, home: Path) -> tuple[ExitStatus, Prose]:
    """Build the case's trees, pipe its event to the hook, and return its exit status and stderr."""
    tree = home / "tree"
    (tree / "sub").mkdir(parents=True)
    if case.in_git:
        subprocess.run(["git", "init", "-q", str(tree)], check=True, env=GIT_ENV)
    if case.record is not None:
        target = tree / "tools" / "ci-output" / "probes" / "timings.tsv"
        target.parent.mkdir(parents=True)
        target.write_text(case.record, encoding="utf-8")
    project = home / ".claude" / "projects" / "-tree"
    (project / "memory").mkdir(parents=True)
    if not case.no_list:
        listed = case.task_list if case.task_list is not None else FileText("# Task list\n")
        (project / "memory" / "TASKS.md").write_text(listed, encoding="utf-8")
    if case.kill_switch:
        (home / ".claude" / "red-disposition-guard.off").touch()
    event = {"hook_event_name": case.event_name, "cwd": str(tree / "sub" if case.subdir else tree),
             "transcript_path": str(project / "session.jsonl"), "last_assistant_message": case.message,
             "stop_hook_active": False}
    stdin = case.stdin if case.stdin is not None else json.dumps(event)
    out = subprocess.run([str(GUARD)], input=stdin, capture_output=True, text=True, check=False,
                         env=GIT_ENV | {"HOME": str(home)})
    return ExitStatus(out.returncode), Prose(out.stderr)


def main() -> ExitStatus:
    """Run every case; report each disagreement."""
    failed = 0
    for number, case in enumerate(CASES):
        status, stderr = run(case, BASE / f"case-{number}")
        lacks = case.says is not None and case.says not in stderr
        if status != case.want or lacks:
            failed += 1
            print(f"FAIL {case.name}: exit {status}, want {case.want}" + (f", stderr lacks {case.says!r}" if lacks else "")
                  + f"\n  {stderr.strip()}")
    shutil.rmtree(BASE)
    print(f"{len(CASES) - failed} of {len(CASES)} cases agree")
    return ExitStatus(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
