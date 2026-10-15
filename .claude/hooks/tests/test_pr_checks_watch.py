#!/usr/bin/env python3
"""Regression cases for pr-checks-watch, the script every pull request watch runs through.

The verdict is a pure function over a head before and after the watch, the rollup's checks and the ruleset's
required contexts: it is held over planted rollups, loaded from the script itself. The script whole is held
by running it with a stand-in `gh` first on PATH, which answers `pr view`, `api` and `pr checks` from a table
the case plants and logs each call: the verdict lines land on stdout, the watch's tables in --record, and the
exit status is the verdict's.

Usage: test_pr_checks_watch.py [SCRIPT]
  SCRIPT  the script to test, or a directory holding it (default: ../../../.local/bin beside this kit)
Exit 0 when every case agrees with its expectation.
"""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

# A case's name; prose a case names itself by or expects; a commit's hash; a check's name; a check run's
# status and conclusion and a status context's state as GitHub spells them; the text of the watch's table;
# gh's exit status; a word of the script's command line; how many cases disagree; the exit status.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
Sha = NewType("Sha", str)
CheckName = NewType("CheckName", str)
RunStatus = NewType("RunStatus", str)
Conclusion = NewType("Conclusion", str)
ContextState = NewType("ContextState", str)
TableText = NewType("TableText", str)
GhStatus = NewType("GhStatus", int)
CommandWord = NewType("CommandWord", str)
EntryKind = NewType("EntryKind", str)
RuleType = NewType("RuleType", str)
DisagreeCount = NewType("DisagreeCount", int)
ExitStatus = NewType("ExitStatus", int)


class RollupEntry(TypedDict, total=False):
    """One entry of a planted rollup: a check run or a status context, as gh prints them."""

    __typename: EntryKind
    name: CheckName
    status: RunStatus
    conclusion: Conclusion | None
    context: CheckName
    state: ContextState


class PullView(TypedDict):
    """What the stand-in answers `gh pr view --json`."""

    headRefOid: Sha
    baseRefName: CheckName
    statusCheckRollup: list[RollupEntry]


class RequiredCheck(TypedDict):
    """One required check of a planted rule."""

    context: CheckName


class RuleParams(TypedDict, total=False):
    """A planted rule's parameters."""

    required_status_checks: list[RequiredCheck]


class RuleJson(TypedDict):
    """One planted rule of the base branch's ruleset."""

    type: RuleType
    parameters: RuleParams


class StubTable(TypedDict):
    """What the stand-in gh answers: the views in order (the last repeats), the rules, and the watch."""

    views: list[PullView | Literal["fail"]]
    rules: list[RuleJson] | Literal["fail"]
    watch_table: TableText
    watch_status: GhStatus

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HOOKS.parent.parent / ".local/bin"
SCRIPT = GIVEN / "pr-checks-watch" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="pr-checks-watch-test-")).resolve()
STUB = """#!/usr/bin/env python3
import json, os, sys
table = json.load(open(os.environ["STUB_GH_TABLE"]))
with open(os.environ["STUB_GH_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    views = table["views"]
    answer = views.pop(0) if len(views) > 1 else views[0]
    json.dump(table, open(os.environ["STUB_GH_TABLE"], "w"))  # the next call, a new process, reads the rest
    if answer == "fail":
        sys.stderr.write("gh: could not resolve the pull request\\n"); sys.exit(1)
    print(json.dumps(answer)); sys.exit(0)
if args[:1] == ["api"]:
    if table["rules"] == "fail":
        sys.stderr.write("gh: HTTP 404\\n"); sys.exit(1)
    print(json.dumps(table["rules"])); sys.exit(0)
if args[:2] == ["pr", "checks"]:
    print(table["watch_table"]); sys.exit(table["watch_status"])
sys.stderr.write("stub gh: unexpected call\\n"); sys.exit(9)
"""

spec = importlib.util.spec_from_loader("pr_checks_watch", importlib.machinery.SourceFileLoader(
    "pr_checks_watch", str(SCRIPT)))
assert spec is not None and spec.loader is not None
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def run(name: CheckName, status: RunStatus = RunStatus("COMPLETED"),
        conclusion: Conclusion | None = Conclusion("SUCCESS")) -> RollupEntry:
    """A check run entry of the rollup."""
    return {"__typename": EntryKind("CheckRun"), "name": name, "status": status, "conclusion": conclusion}


def context(name: CheckName, state: ContextState) -> RollupEntry:
    """A status context entry of the rollup."""
    return {"__typename": EntryKind("StatusContext"), "context": name, "state": state}


REQUIRED = [watch.CheckName("tools/run_ci.py (all gates)"), watch.CheckName("mutation testing")]
GREEN_ROLLUP = [run(CheckName("tools/run_ci.py (all gates)")), run(CheckName("mutation testing")), run(CheckName("PR title")),
                run(CheckName("lint"), conclusion=Conclusion("SKIPPED")), run(CheckName("note"), conclusion=Conclusion("NEUTRAL")), context(CheckName("ci/external"), ContextState("SUCCESS"))]
SHA, SHA2 = watch.Sha("a" * 40), watch.Sha("b" * 40)


class Unit(NamedTuple):
    """One planted judgement: what verdict() gets, the status it must answer, and words its lines must carry."""

    id: CaseId
    before: Sha
    after: Sha
    rollup: list[RollupEntry]
    required: list[CheckName]
    want: ExitStatus
    what: Prose
    names: tuple[Prose, ...] = ()


UNITS = [
    Unit(CaseId("u1"), SHA, SHA, GREEN_ROLLUP, REQUIRED, watch.GREEN, Prose("every check green, required present"),
         (Prose("GREEN at " + SHA),)),
    Unit(CaseId("u2"), SHA, SHA, [*GREEN_ROLLUP, run(CheckName("coverage floors"), conclusion=Conclusion("FAILURE"))], REQUIRED,
         watch.RED, Prose("one failed check is red"), (Prose("FAILURE coverage floors"), Prose("RED: coverage floors"))),
    Unit(CaseId("u3"), SHA, SHA, [*GREEN_ROLLUP, run(CheckName("bench"), RunStatus("IN_PROGRESS"), None)], REQUIRED, watch.INCOMPLETE,
         Prose("a running check is incomplete"), (Prose("IN_PROGRESS bench"), Prose("INCOMPLETE: bench"))),
    Unit(CaseId("u4"), SHA, SHA, [run(CheckName("tools/run_ci.py (all gates)"))], REQUIRED, watch.INCOMPLETE,
         Prose("a required context with no check yet is incomplete"),
         (Prose("ABSENT mutation testing (the ruleset requires it)"), Prose("mutation testing (absent)"))),
    Unit(CaseId("u5"), SHA, SHA, [], REQUIRED, watch.INCOMPLETE,
         Prose("no check at all, the registration race, is incomplete, never green"),
         (Prose("ABSENT tools/run_ci.py (all gates)"),)),
    Unit(CaseId("u6"), SHA, SHA2, GREEN_ROLLUP, REQUIRED, watch.INCOMPLETE,
         Prose("a head that moved is incomplete even when green"),
         (Prose("head moved from " + SHA), Prose("the head moved"))),
    Unit(CaseId("u7"), SHA, SHA, [*GREEN_ROLLUP, context(CheckName("ci/external"), ContextState("FAILURE"))], REQUIRED, watch.RED,
         Prose("a failed status context is red"), (Prose("FAILURE ci/external"),)),
    Unit(CaseId("u8"), SHA, SHA, [*GREEN_ROLLUP, run(CheckName("legs"), conclusion=Conclusion("CANCELLED"))], REQUIRED, watch.RED,
         Prose("a cancelled check has ended and is not green"), (Prose("CANCELLED legs"),)),
    Unit(CaseId("u9"), SHA, SHA, [*GREEN_ROLLUP, run(CheckName("pending"), RunStatus("IN_PROGRESS"), None),
                                  run(CheckName("coverage floors"), conclusion=Conclusion("FAILURE"))], REQUIRED, watch.RED,
         Prose("red outranks pending"), (Prose("RED: coverage floors"),)),
    Unit(CaseId("u10"), SHA, SHA, [*GREEN_ROLLUP, {"__typename": EntryKind("Other"), "name": CheckName("odd")}], REQUIRED,
         watch.INCOMPLETE, Prose("an entry of a kind gh does not spell is named, not ignored"),
         (Prose("UNKNOWN KIND Other odd"),)),
    Unit(CaseId("u11"), SHA, SHA, GREEN_ROLLUP, [], watch.GREEN, Prose("no ruleset requirement, every check green")),
    Unit(CaseId("u12"), SHA, SHA, [*GREEN_ROLLUP, context(CheckName("ci/external"), ContextState("PENDING"))], REQUIRED, watch.INCOMPLETE,
         Prose("a pending status context is incomplete"), (Prose("PENDING ci/external"),)),
]

RULES: list[RuleJson] = [{"type": RuleType("pull_request"), "parameters": {}},
         {"type": RuleType("required_status_checks"), "parameters": {"required_status_checks": [
             {"context": CheckName("tools/run_ci.py (all gates)")}, {"context": CheckName("mutation testing")}]}},
         {"type": RuleType("required_status_checks"), "parameters": {"required_status_checks": [
             {"context": CheckName("mutation testing")}, {"context": CheckName("PR title")}]}}]


def units() -> DisagreeCount:
    """Run the planted judgements; return how many disagree."""
    bad = 0
    for unit in UNITS:
        checks = [watch.check_of(entry) for entry in unit.rollup]
        judged = watch.verdict(watch.PullNumber(409), watch.Sha(unit.before), watch.Sha(unit.after), checks,
                               [watch.CheckName(name) for name in unit.required])
        text = "\n".join(judged.lines)
        missing = [name for name in unit.names if name not in text]
        ok = judged.status == unit.want and not missing and judged.lines[-1].startswith(
            {watch.GREEN: "GREEN", watch.RED: "RED", watch.INCOMPLETE: "INCOMPLETE"}[judged.status])
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {unit.id:<4} want={unit.want} got={judged.status} {unit.what}")
        if not ok:
            print("        " + " | ".join(judged.lines)[:300])
            for name in missing:
                print(f"        not named: {name}")
    got = watch.required_contexts(RULES)
    ok = got == ["tools/run_ci.py (all gates)", "mutation testing", "PR title"]
    bad += not ok
    print(f"{'ok  ' if ok else 'MISS'} u13  the required contexts are read from every rule, in order, once each")
    return DisagreeCount(bad)


class EndToEnd(NamedTuple):
    """One run of the script against a planted gh: the table, the status, and words stdout must carry."""

    id: CaseId
    table: StubTable
    argv: list[CommandWord]
    want: ExitStatus
    what: Prose
    names: tuple[Prose, ...] = ()


def view(sha: Sha, rollup: list[RollupEntry]) -> PullView:
    """What the stand-in answers `gh pr view --json`."""
    return {"headRefOid": sha, "baseRefName": CheckName("main"), "statusCheckRollup": rollup}


END_TO_END = [
    EndToEnd(CaseId("e1"), {"views": [view(SHA, GREEN_ROLLUP)], "rules": RULES, "watch_table": TableText("all pass table"),
                            "watch_status": GhStatus(0)}, [CommandWord("409"), CommandWord("--record"), CommandWord(str(BASE / "e1.log"))], watch.GREEN,
             Prose("a green run: the verdict on stdout, the tables in the record"),
             (Prose("head " + SHA + " watched; base main"), Prose("watch ended with status 0"),
              Prose("GREEN at " + SHA))),
    EndToEnd(CaseId("e2"), {"views": [view(SHA, [*GREEN_ROLLUP, run(CheckName("coverage floors"), conclusion=Conclusion("FAILURE"))])],
                            "rules": RULES, "watch_table": TableText("fail table"), "watch_status": GhStatus(1)}, [CommandWord("409")], watch.RED,
             Prose("a red run"), (Prose("RED: coverage floors; fix, push and re-arm pr-checks-watch 409"),)),
    EndToEnd(CaseId("e3"), {"views": [view(SHA, GREEN_ROLLUP), view(SHA2, GREEN_ROLLUP)], "rules": RULES,
                            "watch_table": TableText(""), "watch_status": GhStatus(0)}, [CommandWord("409")], watch.INCOMPLETE,
             Prose("the head moved during the watch"), (Prose("head moved from " + SHA),)),
    EndToEnd(CaseId("e4"), {"views": ["fail"], "rules": RULES, "watch_table": TableText(""), "watch_status": GhStatus(0)}, [CommandWord("409")],
             watch.UNREAD, Prose("gh that cannot answer is unread, its message printed"),
             (Prose("UNREAD: gh pr view 409 ended with status 1: gh: could not resolve the pull request"),)),
    EndToEnd(CaseId("e5"), {"views": [view(SHA, GREEN_ROLLUP)], "rules": "fail", "watch_table": TableText(""),
                            "watch_status": GhStatus(0)}, [CommandWord("409")], watch.UNREAD,
             Prose("a ruleset gh cannot read is unread"), (Prose("UNREAD: the ruleset of main"),)),
    EndToEnd(CaseId("e6"), {"views": [view(SHA, GREEN_ROLLUP)], "rules": RULES, "watch_table": TableText(""),
                            "watch_status": GhStatus(0)}, [CommandWord("409"), CommandWord("--unknown")], watch.USAGE,
             Prose("an argument the script does not take is a usage error")),
    EndToEnd(CaseId("e7"), {"views": [view(SHA, GREEN_ROLLUP)], "rules": RULES, "watch_table": TableText(""),
                            "watch_status": GhStatus(0)}, [], watch.USAGE, Prose("no pull request is a usage error")),
]


def end_to_end() -> DisagreeCount:
    """Run the script against the stand-in gh; return how many cases disagree."""
    bad = 0
    bin_dir = BASE / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    for case in END_TO_END:
        table = BASE / f"{case.id}.json"
        table.write_text(json.dumps(case.table), encoding="utf-8")
        log = BASE / f"{case.id}.calls"
        env = os.environ | {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                            "STUB_GH_TABLE": str(table), "STUB_GH_LOG": str(log)}
        done = subprocess.run([str(SCRIPT), *case.argv], capture_output=True, text=True, check=False, env=env)
        missing = [name for name in case.names if name not in done.stdout]
        ok = done.returncode == case.want and not missing
        if case.id == "e1":
            record = (BASE / "e1.log").read_text(encoding="utf-8")
            calls = log.read_text(encoding="utf-8").splitlines()
            watched = "pr checks 409 --watch --fail-fast --interval 20" in calls
            rules_read = "api repos/{owner}/{repo}/rules/branches/main" in calls
            ok = ok and record.startswith("all pass table") and watched and rules_read
            if not (record.startswith("all pass table") and watched and rules_read):
                print(f"        record={record[:40]!r} calls={calls}")
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {case.id:<4} want={case.want} got={done.returncode} {case.what}")
        if not ok:
            print("        stdout: " + " | ".join(done.stdout.strip().splitlines())[:300])
            print("        stderr: " + " | ".join(done.stderr.strip().splitlines())[:300])
            for name in missing:
                print(f"        not named: {name}")
    return DisagreeCount(bad)


def main() -> ExitStatus:
    """Run every case; exit 0 when all agree."""
    bad = units() + end_to_end()
    total = len(UNITS) + 1 + len(END_TO_END)
    print(f"{total} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
