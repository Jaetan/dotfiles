#!/home/nicolas/.local/bin/python3.14
"""Regression cases for pr-checks-watch, the script every pull request watch runs through.

Each case copies the script into a temporary directory of its own and plants beside the copy a stand-in
pr-all-green, which records its command line and what the script had printed when it started, prints planted
lines and ends with a planted status (a negative one by that signal). A stand-in `gh`, alone on PATH, answers
`pr view` and `pr checks` from the case's plan, recording each call and, at the watch, what the script had
printed; a case may plant it without its execute bit, leave it out, or have it remove itself once it has
answered the view. The case holds the exit status, every line of stdout in order, gh's calls, pr-all-green's
command line and the record, over an earlier record where the case plants one.

Usage: test_pr_checks_watch.py [SCRIPT]
  SCRIPT  the script to test, or a directory holding it (default: ../../../.local/bin beside this kit)
Exit 0 when every case agrees with its expectation.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

# A case's name; prose a case names itself by or expects; a commit's hash; a word of a command line, where
# {dir} stands for the case's own directory; text a process prints or a file holds, {dir} the same; an exit
# status a stand-in or the script ends with; how many cases disagree; this suite's exit status.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
Sha = NewType("Sha", str)
Word = NewType("Word", str)
Text = NewType("Text", str)
Status = NewType("Status", int)
DisagreeCount = NewType("DisagreeCount", int)
ExitStatus = NewType("ExitStatus", int)


class PullView(TypedDict, total=False):
    """What the stand-in gh answers `pr view --json headRefOid`."""

    headRefOid: Sha


class GhPlan(TypedDict):
    """What the stand-in gh answers: the view (an object, a failure, or text printed as it is) and the watch."""

    view: PullView | Literal["fail"] | Text
    vanish: bool  # the stand-in removes itself once it has answered the view
    watch_out: Text
    watch_err: Text
    watch_status: Status


class VerdictPlan(TypedDict):
    """What the stand-in pr-all-green prints on stdout and stderr and ends with."""

    lines: list[Text]
    err: Text
    status: Status


class VerdictFile(VerdictPlan):
    """The plan as the stand-in reads it, with the file the script's stdout goes to."""

    stdout: Text


class Case(NamedTuple):
    """One run of a copy of the script beside its stand-ins, and what the run must show."""

    id: CaseId
    what: Prose
    argv: list[Word]
    gh: GhPlan
    verdict: VerdictPlan | None  # None: no pr-all-green beside the copy
    want: ExitStatus
    out: list[Text] | None  # every stdout line, in order; None: only `has` is read
    calls: list[Text]  # gh's calls, in order
    given: list[Word] | None  # pr-all-green's command line; None: it never runs
    record: Text | None = None  # what {dir}/watch.log holds; None: no such file
    has: Prose = Prose("")  # words stdout or stderr carries
    link: bool = False  # run through a symlink in another directory, a decoy pr-all-green beside the link
    runnable: bool = True  # False: pr-all-green planted without its execute bit
    gh_file: Literal["runnable", "absent", "unrunnable"] = "runnable"  # the stand-in gh, as planted
    stale: Text | None = None  # what {dir}/watch.log holds before the run; None: no such file


HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HOOKS.parent.parent / ".local/bin"
SCRIPT = GIVEN / "pr-checks-watch" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="pr-checks-watch-test-")).resolve()
GH_STUB = """#!@PYTHON@
import json, sys
from pathlib import Path
case = Path(__file__).resolve().parent.parent
plan = json.loads((case / "gh.json").read_text())
args = sys.argv[1:]
with open(case / "gh.calls", "a") as log:
    log.write(" ".join(args) + "\\n")
if args[:2] == ["pr", "view"]:
    if plan["vanish"]:
        Path(__file__).unlink()
    if plan["view"] == "fail":
        sys.stderr.write("gh: could not resolve the pull request\\n"); sys.exit(1)
    print(plan["view"] if isinstance(plan["view"], str) else json.dumps(plan["view"])); sys.exit(0)
if args[:2] == ["pr", "checks"]:
    (case / "gh.seen").write_text((case / "stdout.txt").read_text())
    sys.stdout.write(plan["watch_out"]); sys.stderr.write(plan["watch_err"]); sys.exit(plan["watch_status"])
sys.stderr.write("stub gh: unexpected call\\n"); sys.exit(9)
""".replace("@PYTHON@", sys.executable)
VERDICT_STUB = """#!@PYTHON@
import json, os, sys
from pathlib import Path
here = Path(__file__).parent
plan = json.loads((here / "verdict.json").read_text())
(here / "verdict.calls").write_text(json.dumps(sys.argv[1:]))
(here / "verdict.seen").write_text(Path(plan["stdout"]).read_text())
for line in plan["lines"]:
    print(line)
sys.stdout.flush()
sys.stderr.write(plan["err"])
if plan["status"] < 0:
    os.kill(os.getpid(), -plan["status"])
sys.exit(plan["status"])
""".replace("@PYTHON@", sys.executable)

SHA = Sha("a" * 40)
HEAD_LINE = Text(f"head {SHA} watched")
VIEW: PullView = {"headRefOid": SHA}
CALLS = [Text("pr view 409 --json headRefOid"), Text("pr checks 409 --watch --fail-fast --interval 20")]
GIVEN_PLAIN = [Word("409"), Word("--head"), Word(SHA)]
RECORDED = [Word("409"), Word("--record"), Word("{dir}/watch.log")]
GIVEN_LOGS = [*GIVEN_PLAIN, Word("--logs"), Word("{dir}/watch.log.logs")]


def plan(watch_status: Status = Status(0), view: PullView | Literal["fail"] | Text = VIEW,
         watch_out: Text = Text("lanes\tpass\n"), watch_err: Text = Text(""), *, vanish: bool = False) -> GhPlan:
    """The stand-in gh's answers."""
    return {"view": view, "vanish": vanish, "watch_out": watch_out, "watch_err": watch_err,
            "watch_status": watch_status}


def answer(status: Status, *lines: Text, err: Text = Text("")) -> VerdictPlan:
    """The stand-in pr-all-green's answer."""
    return {"lines": list(lines), "err": err, "status": status}


def watched(status: Status, *lines: Text) -> list[Text]:
    """Every stdout line of a run that reached pr-all-green."""
    return [HEAD_LINE, Text(f"watch ended with status {status}"), *lines]


def no_verdict(status: Status) -> Text:
    """The line naming a status of pr-all-green that is no verdict."""
    return Text(f"UNREAD: pr-all-green ended with status {status}, which is no verdict")


def no_head(printed: Text) -> Text:
    """The line naming a view that holds no head, with what gh printed."""
    return Text(f"UNREAD: gh pr view 409 answered no string headRefOid: {printed}")


def unkept(record: Text, why: Text) -> Text:
    """The line naming a record that cannot be written, and why."""
    return Text(f"UNREAD: the record {record} cannot be written: {why}")


def unstarted(why: Text) -> Text:
    """The line naming a gh that cannot be started, and why."""
    return Text(f"UNREAD: gh could not be started: {why}")


ALL_GREEN = (Text(f"pull request #409: OPEN CLEAN {SHA}"), Text(f"ALL GREEN at {SHA}: every point holds"))
RED = (Text(f"pull request #409: OPEN CLEAN {SHA}"), Text("  - [red] job lint concluded skipped"),
       Text(f"RED at {SHA}: 1 problem(s)"))
UNREAD = (Text("gh could not answer: HTTP 502"),)
INCOMPLETE = (Text(f"pull request #409: OPEN BLOCKED {SHA}"), Text(f"INCOMPLETE at {SHA}: 1 problem(s)"))
NOT_MERGED = Text("NOT MERGED: #409 reads OPEN after the merge")
S0, S1, S2, S3, S4, S5, S8 = (Status(n) for n in (0, 1, 2, 3, 4, 5, 8))
MISSING = Text("{dir}/missing/watch.log")
# The stand-ins' directory: open(2) refuses to write a directory to every account, root included.
DIRECTORY = Text("{dir}/bin")
FULL = Text("/dev/full")  # opens, but no write reaches it

CASES = [
    Case(CaseId("c1"), Prose("ALL GREEN passes through as 0, the watch's tables in the record, --logs beside it"),
         RECORDED, plan(watch_err=Text("gh: a note on stderr\n")), answer(S0, *ALL_GREEN), ExitStatus(0),
         watched(S0, *ALL_GREEN), CALLS, GIVEN_LOGS, Text("lanes\tpass\ngh: a note on stderr\n")),
    Case(CaseId("c2"), Prose("RED passes through as 1, even where the watch ended 0"),
         [Word("409")], plan(), answer(S1, *RED), ExitStatus(1), watched(S0, *RED), CALLS, GIVEN_PLAIN),
    Case(CaseId("c3"), Prose("UNREAD passes through as 2"),
         [Word("409")], plan(), answer(S2, *UNREAD), ExitStatus(2), watched(S0, *UNREAD), CALLS, GIVEN_PLAIN),
    Case(CaseId("c4"), Prose("INCOMPLETE passes through as 3, the watch's pending status 8 no verdict"),
         RECORDED, plan(S8), answer(S3, *INCOMPLETE), ExitStatus(3), watched(S8, *INCOMPLETE), CALLS, GIVEN_LOGS,
         Text("lanes\tpass\n")),
    Case(CaseId("c5"), Prose("a watch that failed fast is no verdict: ALL GREEN reads 0"),
         [Word("409")], plan(S1), answer(S0, *ALL_GREEN), ExitStatus(0), watched(S1, *ALL_GREEN), CALLS, GIVEN_PLAIN),
    Case(CaseId("c6"), Prose("pr-all-green's usage status 4 is no verdict: UNREAD, the status named"),
         [Word("409")], plan(), answer(S4, err=Text("pr-all-green: usage\n")), ExitStatus(2),
         watched(S0, no_verdict(S4)), CALLS, GIVEN_PLAIN, has=Prose("pr-all-green: usage")),
    Case(CaseId("c7"), Prose("pr-all-green's NOT MERGED status 5 is no verdict: UNREAD, the status named"),
         [Word("409")], plan(), answer(S5, NOT_MERGED), ExitStatus(2), watched(S0, NOT_MERGED, no_verdict(S5)), CALLS,
         GIVEN_PLAIN),
    Case(CaseId("c8"), Prose("a pr-all-green ended by a signal is no verdict: UNREAD, the status named"),
         [Word("409")], plan(), answer(Status(-15), Text("reading")), ExitStatus(2),
         watched(S0, Text("reading"), no_verdict(Status(-15))), CALLS, GIVEN_PLAIN),
    Case(CaseId("c9"), Prose("no pr-all-green beside the script: UNREAD, naming the path, never a traceback's RED"),
         [Word("409")], plan(), None, ExitStatus(2),
         watched(S0, Text("UNREAD: {dir}/pr-all-green could not run: No such file or directory")), CALLS, None),
    Case(CaseId("c10"), Prose("a pr-all-green that cannot be run: UNREAD, naming the path and why"),
         [Word("409")], plan(), answer(S0, *ALL_GREEN), ExitStatus(2),
         watched(S0, Text("UNREAD: {dir}/pr-all-green could not run: Permission denied")), CALLS, None, runnable=False),
    Case(CaseId("c11"), Prose("gh failing before the watch: UNREAD with its message, no watch, no pr-all-green"),
         RECORDED, plan(view="fail"), answer(S0, *ALL_GREEN), ExitStatus(2),
         [Text("UNREAD: gh pr view 409 ended with status 1: gh: could not resolve the pull request")], CALLS[:1], None),
    Case(CaseId("c12"), Prose("gh printing no JSON before the watch: UNREAD, no watch, no pr-all-green"),
         [Word("409")], plan(view=Text("no json")), answer(S0, *ALL_GREEN), ExitStatus(2),
         [Text("UNREAD: gh pr view 409 printed no JSON: Expecting value: line 1 column 1 (char 0)")], CALLS[:1], None),
    Case(CaseId("c13"), Prose("a view naming no head: UNREAD with what gh printed, no watch, no pr-all-green"),
         [Word("409")], plan(view={}), answer(S0, *ALL_GREEN), ExitStatus(2), [no_head(Text("{}"))], CALLS[:1], None),
    Case(CaseId("c14"), Prose("an empty --record keeps no record and passes no --logs"),
         [Word("409"), Word("--record"), Word("")], plan(), answer(S0, *ALL_GREEN), ExitStatus(0),
         watched(S0, *ALL_GREEN), CALLS, GIVEN_PLAIN),
    Case(CaseId("c15"), Prose("run through a symlink, pr-all-green is the one beside the resolved script"),
         [Word("409")], plan(), answer(S0, *ALL_GREEN), ExitStatus(0), watched(S0, *ALL_GREEN), CALLS, GIVEN_PLAIN,
         link=True),
    Case(CaseId("c16"), Prose("an argument the script does not take is a usage error, nothing called"),
         [Word("409"), Word("--unknown")], plan(), answer(S0, *ALL_GREEN), ExitStatus(4), [], [], None,
         has=Prose("unrecognized arguments: --unknown")),
    Case(CaseId("c17"), Prose("no pull request is a usage error, nothing called"),
         [], plan(), answer(S0, *ALL_GREEN), ExitStatus(4), [], [], None,
         has=Prose("the following arguments are required: pull")),
    Case(CaseId("c18"), Prose("a pull request that is no number is a usage error, nothing called"),
         [Word("abc")], plan(), answer(S0, *ALL_GREEN), ExitStatus(4), [], [], None,
         has=Prose("invalid int value: 'abc'")),
    Case(CaseId("c19"), Prose("--help prints the usage and exits 0, nothing called"),
         [Word("--help")], plan(), answer(S0, *ALL_GREEN), ExitStatus(0), None, [], None,
         has=Prose("usage: pr-checks-watch")),
    Case(CaseId("c20"), Prose("a view that is a JSON array: UNREAD, never a traceback's RED"),
         [Word("409")], plan(view=Text('["a"]')), answer(S0, *ALL_GREEN), ExitStatus(2), [no_head(Text('["a"]'))],
         CALLS[:1], None),
    Case(CaseId("c21"), Prose("a view that is a JSON string spelling a head: UNREAD, never taken for the head"),
         [Word("409")], plan(view=Text(f'"{SHA}"')), answer(S0, *ALL_GREEN), ExitStatus(2),
         [no_head(Text(f'"{SHA}"'))], CALLS[:1], None),
    Case(CaseId("c22"), Prose("a view whose headRefOid is no string: UNREAD, never a traceback's RED"),
         [Word("409")], plan(view=Text('{"headRefOid": 5}')), answer(S0, *ALL_GREEN), ExitStatus(2),
         [no_head(Text('{"headRefOid": 5}'))], CALLS[:1], None),
    Case(CaseId("c23"), Prose("a record in a missing directory: UNREAD naming it and why, no watch, no pr-all-green"),
         [Word("409"), Word("--record"), Word(MISSING)], plan(), answer(S0, *ALL_GREEN), ExitStatus(2),
         [HEAD_LINE, unkept(MISSING, Text("No such file or directory"))], CALLS[:1], None),
    Case(CaseId("c24"), Prose("a record that is a directory: UNREAD naming it and why, no watch, no pr-all-green"),
         [Word("409"), Word("--record"), Word(DIRECTORY)], plan(), answer(S0, *ALL_GREEN), ExitStatus(2),
         [HEAD_LINE, unkept(DIRECTORY, Text("Is a directory"))], CALLS[:1], None),
    Case(CaseId("c25"), Prose("a record whose write fails after the watch: UNREAD naming it and why, no pr-all-green"),
         [Word("409"), Word("--record"), Word(FULL)], plan(), answer(S0, *ALL_GREEN), ExitStatus(2),
         [HEAD_LINE, unkept(FULL, Text("No space left on device"))], CALLS, None),
    Case(CaseId("c26"), Prose("no gh on PATH: UNREAD naming gh and why, never a traceback's RED, nothing run"),
         [Word("409")], plan(), answer(S0, *ALL_GREEN), ExitStatus(2), [unstarted(Text("No such file or directory"))],
         [], None, gh_file="absent"),
    Case(CaseId("c27"), Prose("a gh without its execute bit: UNREAD naming gh and why, never a traceback's RED"),
         [Word("409")], plan(), answer(S0, *ALL_GREEN), ExitStatus(2), [unstarted(Text("Permission denied"))],
         [], None, gh_file="unrunnable"),
    Case(CaseId("c28"), Prose("a gh gone by the watch: UNREAD naming gh and why, never a traceback's RED"),
         [Word("409")], plan(vanish=True), answer(S0, *ALL_GREEN), ExitStatus(2),
         [HEAD_LINE, unstarted(Text("No such file or directory"))], CALLS[:1], None),
    Case(CaseId("c29"), Prose("a view whose headRefOid is empty: UNREAD, no watch, no pr-all-green"),
         [Word("409")], plan(view={"headRefOid": Sha("")}), answer(S0, *ALL_GREEN), ExitStatus(2),
         [Text("UNREAD: gh pr view 409 answered an empty headRefOid")], CALLS[:1], None),
    Case(CaseId("c30"), Prose("an earlier record is replaced by the watch's tables, never appended to"),
         RECORDED, plan(), answer(S0, *ALL_GREEN), ExitStatus(0), watched(S0, *ALL_GREEN), CALLS, GIVEN_LOGS,
         Text("lanes\tpass\n"), stale=Text("lanes\tfail\n")),
]


def plant(path: Path, text: Text) -> None:
    """Write an executable stand-in."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def lines_of(path: Path) -> list[Text]:
    """The lines a file holds, none when it is absent."""
    return [Text(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def run(case: Case) -> list[Prose]:
    """Run the case; every way it disagrees with its expectation."""
    home = BASE / case.id
    (home / "bin").mkdir(parents=True)
    copy = home / "pr-checks-watch"
    shutil.copyfile(SCRIPT, copy)
    copy.chmod(0o755)
    if case.gh_file != "absent":
        plant(home / "bin" / "gh", Text(GH_STUB))
    if case.gh_file == "unrunnable":
        (home / "bin" / "gh").chmod(0o644)
    if case.stale is not None:
        (home / "watch.log").write_text(case.stale, encoding="utf-8")
    (home / "gh.json").write_text(json.dumps(case.gh), encoding="utf-8")
    stdout = home / "stdout.txt"
    if case.verdict is not None:
        plant(home / "pr-all-green", Text(VERDICT_STUB))
        if not case.runnable:
            (home / "pr-all-green").chmod(0o644)
        planted: VerdictFile = {**case.verdict, "stdout": Text(str(stdout))}
        (home / "verdict.json").write_text(json.dumps(planted), encoding="utf-8")
    target = copy
    decoy = home / "link"
    if case.link:
        decoy.mkdir()
        target = decoy / "pr-checks-watch"
        target.symlink_to(copy)
        plant(decoy / "pr-all-green", Text(VERDICT_STUB))
        lure: VerdictFile = {**answer(S1, Text("DECOY")), "stdout": Text(str(stdout))}
        (decoy / "verdict.json").write_text(json.dumps(lure), encoding="utf-8")

    env = os.environ | {"PATH": str(home / "bin")}  # no gh but the stand-in is ever found
    argv = [word.replace("{dir}", str(home)) for word in case.argv]
    with stdout.open("w", encoding="utf-8") as out:
        done = subprocess.run([str(target), *argv], stdout=out, stderr=subprocess.PIPE, text=True, check=False, env=env,
                              cwd=home)
    printed = stdout.read_text(encoding="utf-8")
    wrong: list[Prose] = []
    if done.returncode != case.want:
        wrong.append(Prose(f"status {done.returncode}, not {case.want}"))
    want_out = None if case.out is None else [Text(line.replace("{dir}", str(home))) for line in case.out]
    if want_out is not None and printed.splitlines() != want_out:
        wrong.append(Prose(f"stdout {printed.splitlines()}, not {want_out}"))
    if case.has not in printed + done.stderr:
        wrong.append(Prose(f"neither stdout nor stderr carries {case.has!r}"))
    calls = lines_of(home / "gh.calls")
    if calls != case.calls:
        wrong.append(Prose(f"gh calls {calls}, not {case.calls}"))
    given = home / "verdict.calls"
    got = json.loads(given.read_text(encoding="utf-8")) if given.exists() else None
    want_given = None if case.given is None else [Word(word.replace("{dir}", str(home))) for word in case.given]
    if got != want_given:
        wrong.append(Prose(f"pr-all-green given {got}, not {want_given}"))
    if (decoy / "verdict.calls").exists():
        wrong.append(Prose("the decoy pr-all-green beside the symlink ran"))
    record = home / "watch.log"
    kept = record.read_text(encoding="utf-8") if record.exists() else None
    if kept != case.record:
        wrong.append(Prose(f"record {kept!r}, not {case.record!r}"))
    seen = {"watch": home / "gh.seen", "pr-all-green": home / "verdict.seen"}
    for who, lead in (("watch", 1), ("pr-all-green", 2)):
        if want_out is not None and seen[who].exists():
            before = seen[who].read_text(encoding="utf-8")
            if before != "".join(line + "\n" for line in want_out[:lead]):
                wrong.append(Prose(f"stdout held {before!r} when the {who} started"))
    if wrong:
        wrong.append(Prose("stderr: " + " | ".join(done.stderr.strip().splitlines())[:300]))
    return wrong


def main() -> ExitStatus:
    """Run every case; exit 0 when all agree."""
    bad = DisagreeCount(0)
    for case in CASES:
        wrong = run(case)
        bad = DisagreeCount(bad + bool(wrong))
        print(f"{'MISS' if wrong else 'ok  '} {case.id:<4} want={case.want} {case.what}")
        for line in wrong:
            print(f"        {line}"[:400])
    shutil.rmtree(BASE, ignore_errors=True)
    print(f"{len(CASES)} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
