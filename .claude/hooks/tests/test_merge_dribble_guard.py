#!/usr/bin/env python3
"""Regression cases for merge-dribble-guard.py.

Every case builds a git work tree of its own under a fresh temporary directory, writes the dribble the case
gives, and puts a stand-in `gh` first on PATH that answers `gh pr view N --json state,mergeStateStatus` from the
case's table (or fails, or prints junk) and logs each call. It then pipes a PostToolUse event to the hook and
compares the answer with the rule: exit 2 reports a merge GitHub would refuse now, exit 1 a merge state that could
not be read, exit 0 lets the call through.

Usage: test_merge_dribble_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, NewType


@dataclass(frozen=True)
class Gt[T]:
    """``Annotated`` metadata: the annotated value is greater than ``bound``."""

    bound: T


# A dribble's text; a case's name; prose a case names itself by or expects; an event as the hook reads it; the
# stand-in gh's table, as JSON; a pull request's number (from 1); how many times the stand-in was called; the
# hook's exit status.
FileText = NewType("FileText", str)
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
EventText = NewType("EventText", str)
Answers = NewType("Answers", str)
PullNumber = Annotated[int, Gt(0)]
CallCount = Annotated[int, Gt(-1)]
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)
type Tool = Literal["Bash", "Write", "Read"]
type EventName = Literal["PostToolUse", "PreToolUse"]
# What the stand-in answers for one pull request: GitHub's state and merge state (with one state and one merge
# state outside GitHub's sets, for the cases that read them), a failure, or output that is not JSON.
type PullState = Literal["OPEN", "CLOSED", "MERGED", "ELSEWHERE"]
type MergeState = Literal["BEHIND", "BLOCKED", "CLEAN", "DIRTY", "HAS_HOOKS", "UNKNOWN", "UNSTABLE", "SOMETHING"]
type Answer = tuple[PullState, MergeState] | Literal["fail", "junk"]
type MergeFlag = Literal["--squash", "--auto", "--delete-branch"]

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "merge-dribble-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="merge-dribble-guard-test-")).resolve()
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_GH_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
table = json.loads(os.environ["STUB_GH_ANSWERS"])
if sys.argv[1:3] != ["pr", "view"]:
    sys.exit(3)
answer = table.get(sys.argv[3], "fail")
if answer == "fail":
    print("could not resolve to a PullRequest", file=sys.stderr)
    sys.exit(1)
if answer == "junk":
    print("not json")
    sys.exit(0)
state, merge_state = answer
print(json.dumps({"state": state, "mergeStateStatus": merge_state}))
"""


class Case(NamedTuple):
    """A dribble, the stand-in's table, and the answer the rule wants."""

    id: CaseId
    what: Prose
    dribble: FileText | None
    answers: Answers
    want: ExitStatus
    says: Prose


class Outcome(NamedTuple):
    """The hook's exit status, what it printed, and the gh calls the stand-in logged."""

    status: ExitStatus
    stderr: Prose
    gh_calls: CallCount


def table(answers: dict[PullNumber, Answer]) -> Answers:
    """The stand-in's table, by pull request number."""
    return Answers(json.dumps({str(number): answer for number, answer in answers.items()}))


def repository(name: CaseId) -> Path:
    """A fresh git work tree for one case."""
    repo = BASE / name
    repo.mkdir()
    _ = subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=GIT_ENV)
    return repo


def stub_dir() -> Path:
    """A directory holding the stand-in gh, made once."""
    where = BASE / "bin"
    if not (where / "gh").exists():
        where.mkdir(exist_ok=True)
        _ = (where / "gh").write_text(STUB, encoding="utf-8")
        (where / "gh").chmod(0o755)
    return where


def hook(repo: Path, answers: Answers, tool: Tool = "Bash", event_name: EventName = "PostToolUse") -> Outcome:
    """Pipe one event for ``repo`` to the hook with the stand-in gh first on PATH."""
    log = repo.parent / f"{repo.name}.gh.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
        "PATH": f"{stub_dir()}{os.pathsep}{os.environ.get('PATH', '')}",
        "STUB_GH_ANSWERS": answers, "STUB_GH_LOG": str(log)}
    event = EventText(json.dumps({"hook_event_name": event_name, "tool_name": tool, "cwd": str(repo)}))
    done = subprocess.run([str(GUARD)], input=event, capture_output=True, text=True, check=False, env=env)
    calls = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
    return Outcome(ExitStatus(done.returncode), Prose(done.stderr), calls)


def run_case(case: Case) -> Outcome:
    """Build the case's tree and dribble, then ask the hook."""
    repo = repository(case.id)
    if case.dribble is not None:
        _ = (repo / ".commands-to-run.sh").write_text(case.dribble, encoding="utf-8")
    return hook(repo, case.answers)


def merge(number: PullNumber, *flags: MergeFlag) -> FileText:
    """A dribble whose one merge is `gh pr merge NUMBER FLAGS...`."""
    return FileText(f"#!/usr/bin/env bash\nset -euo pipefail\ngh pr merge {number} {' '.join(flags)}\n")


def cases() -> list[Case]:
    """Every case of the rule."""
    behind = table({5: ("OPEN", "BEHIND")})
    return [
        Case(CaseId("M01"), Prose("a CLEAN pull request is merged"), merge(5, "--squash"),
             table({5: ("OPEN", "CLEAN")}), ALLOW, Prose("")),
        Case(CaseId("M02"), Prose("a BEHIND pull request is refused, with the update-branch route"),
             merge(5, "--squash"), behind, REFUSE, Prose("update-branch")),
        Case(CaseId("M03"), Prose("a merge armed with --auto on a continuation line waits by design"),
             FileText("gh pr merge 5 --squash \\\n  --auto\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M04"), Prose("a merge after the dribble's own update-branch is left alone"),
             FileText("gh api -X PUT repos/o/r/pulls/5/update-branch\ngh pr merge 5 --squash\n"), behind,
             ALLOW, Prose("")),
        Case(CaseId("M05"), Prose("a MERGED pull request is refused"), merge(5, "--squash"),
             table({5: ("MERGED", "UNKNOWN")}), REFUSE, Prose("MERGED")),
        Case(CaseId("M06"), Prose("HAS_HOOKS is mergeable"), merge(5), table({5: ("OPEN", "HAS_HOOKS")}), ALLOW,
             Prose("")),
        Case(CaseId("M07"), Prose("BLOCKED is refused"), merge(5), table({5: ("OPEN", "BLOCKED")}), REFUSE,
             Prose("BLOCKED")),
        Case(CaseId("M08"), Prose("DIRTY is refused"), merge(5), table({5: ("OPEN", "DIRTY")}), REFUSE,
             Prose("DIRTY")),
        Case(CaseId("M09"), Prose("UNSTABLE is refused"), merge(5), table({5: ("OPEN", "UNSTABLE")}), REFUSE,
             Prose("UNSTABLE")),
        Case(CaseId("M10"), Prose("UNKNOWN is refused"), merge(5), table({5: ("OPEN", "UNKNOWN")}), REFUSE,
             Prose("UNKNOWN")),
        Case(CaseId("M11"), Prose("of two merges, the BEHIND one is named"),
             FileText("gh pr merge 5 --squash\ngh pr merge 6 --squash\n"),
             table({5: ("OPEN", "CLEAN"), 6: ("OPEN", "BEHIND")}), REFUSE, Prose("#6 is BEHIND")),
        Case(CaseId("M12"), Prose("a number spelled through a variable cannot be checked"),
             FileText('gh pr merge "$m" --squash\n'), behind, REFUSE, Prose("spell the number")),
        Case(CaseId("M13"), Prose("a gh that fails is reported and goes through"), merge(5), table({5: "fail"}),
             REPORTED, Prose("gh pr view 5 failed: could not resolve")),
        Case(CaseId("M14"), Prose("a gh that prints junk is reported"), merge(5), table({5: "junk"}), REPORTED,
             Prose("printed no JSON")),
        Case(CaseId("M15"), Prose("a merge named inside a heredoc body is text"),
             FileText("cat <<'EOF'\ngh pr merge 5 --squash\nEOF\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M16"), Prose("no dribble, nothing to read"), None, behind, ALLOW, Prose("")),
        Case(CaseId("M17"), Prose("a dribble that merges nothing"), FileText("git status\n"), behind, ALLOW,
             Prose("")),
        Case(CaseId("M18"), Prose("an option's value is not read as the pull request"),
             FileText('gh pr merge --subject "fix: x" -R o/r 5 --squash\n'), behind, REFUSE, Prose("#5 is BEHIND")),
        Case(CaseId("M19"), Prose("a refusal beside an unreadable one still refuses"),
             FileText("gh pr merge 5\ngh pr merge 6\n"), table({5: ("OPEN", "BEHIND"), 6: "fail"}), REFUSE,
             Prose("could not be read")),
        Case(CaseId("M20"), Prose("a state outside GitHub's set is reported"), merge(5),
             table({5: ("OPEN", "SOMETHING")}), REPORTED, Prose("answered state")),
        Case(CaseId("M21"), Prose("a merge with no number names none the hook can read"),
             FileText("gh pr merge --squash\n"), behind, REFUSE, Prose("spell the number")),
        Case(CaseId("M22"), Prose("another program's `pr merge` is not gh's"), FileText("hub pr merge 5\n"),
             behind, ALLOW, Prose("")),
        Case(CaseId("M23"), Prose("a `gh pr view` merges nothing"), FileText("gh pr view 5\n"), behind, ALLOW,
             Prose("")),
        Case(CaseId("M24"), Prose("a flag before the number is not the pull request"),
             FileText("gh pr merge --squash 5\n"), behind, REFUSE, Prose("#5 is BEHIND")),
        Case(CaseId("M25"), Prose("a word that only begins with digits is no number"),
             FileText("gh pr merge 5x --squash\n"), behind, REFUSE, Prose("spell the number")),
        Case(CaseId("M26"), Prose("an api call to another endpoint updates no branch"),
             FileText("gh api repos/o/r/pulls/5/updated\ngh pr merge 5 --squash\n"), behind, REFUSE,
             Prose("#5 is BEHIND")),
        Case(CaseId("M27"), Prose("a pull request state outside GitHub's set is reported"), merge(5),
             table({5: ("ELSEWHERE", "CLEAN")}), REPORTED, Prose("answered state")),
    ]


def special_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases about when the hook looks: an unchanged dribble, a changed one, events it leaves alone."""
    results: list[tuple[CaseId, Prose, bool]] = []
    behind = table({5: ("OPEN", "BEHIND")})
    repo = repository(CaseId("S01"))
    dribble = repo / ".commands-to-run.sh"
    _ = dribble.write_text(merge(5, "--squash"), encoding="utf-8")
    first = hook(repo, behind)
    second = hook(repo, behind)
    results.append((CaseId("S01"), Prose("an unchanged dribble is read once and asks gh once"),
                    first.status == REFUSE and second.status == ALLOW and second.gh_calls == 1))
    _ = dribble.write_text(merge(5, "--squash", "--delete-branch"), encoding="utf-8")
    third = hook(repo, behind)
    results.append((CaseId("S02"), Prose("a changed dribble is read again and refused again"),
                    third.status == REFUSE and third.gh_calls == 2))
    git_dir = subprocess.run(["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"], capture_output=True,
                             text=True, check=True, env=GIT_ENV).stdout.strip()
    results.append((CaseId("S03"), Prose("the state lives in the git directory"),
                    (Path(git_dir) / "merge-dribble-guard.json").is_file()))
    other = repository(CaseId("S04"))
    _ = (other / ".commands-to-run.sh").write_text(merge(5), encoding="utf-8")
    results.append((CaseId("S04"), Prose("a Read call is left alone"),
                    hook(other, behind, tool="Read").status == ALLOW))
    results.append((CaseId("S05"), Prose("an event before the call is left alone"),
                    hook(other, behind, event_name="PreToolUse").status == ALLOW))
    unreadable = subprocess.run([str(GUARD)], input="not json", capture_output=True, text=True, check=False)
    results.append((CaseId("S06"), Prose("an event that cannot be read goes through"), unreadable.returncode == 0))
    outside = BASE / "not-a-repo"
    outside.mkdir()
    results.append((CaseId("S07"), Prose("a call outside a work tree is left alone"),
                    hook(outside, behind).status == ALLOW))
    return results


def main() -> ExitStatus:
    """Run every case; print one line each, MISS for a disagreement, then the count."""
    bad = 0
    total = 0
    try:
        for case in cases():
            outcome = run_case(case)
            total += 1
            ok = outcome.status == case.want and case.says in outcome.stderr
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case.id:<4} want={case.want} got={outcome.status} {case.what}")
            if not ok and outcome.stderr.strip():
                print("        " + " | ".join(outcome.stderr.strip().splitlines()[-4:])[:300])
        for case_id, what, ok in special_cases():
            total += 1
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case_id:<4} {what}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"{total} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
