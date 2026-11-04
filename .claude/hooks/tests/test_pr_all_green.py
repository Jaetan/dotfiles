#!/home/nicolas/.local/bin/python3.14
"""Regression cases for pr-all-green, the reading every merge and every watch's verdict goes through.

Each case plants a GitHub: the pull request, its files, the workflow files at its head, its test merge commit and
that commit's parents, the runs and their attempts, jobs, logs, check runs, check suites, statuses, ruleset,
artifacts and the base branch's green push run, and checks the exit status, a line of the answer and the merges
asked for. A green baseline answers ALL GREEN; every other case
plants one defect where a red hides behind a green conclusion, or one control that must stay green. The stand-in
gh pages each list as GitHub does and applies --jq to each page as gh does; a case can drop a list's total_count
or change the list between its first page and the next, as an entry created or moved mid-read does. Each planted
run names #7 as the pull request it ran for unless the case names another. Most cases run the script's main in
this process with gh replaced; a case marked `process` runs the script whole with a stand-in `gh` first on PATH,
which is this file run with --as-gh. Each case writes its investigated-run ledger (one naming no failure unless
it gives another) in a temporary directory and passes it with --ledger; a case marked `beside` runs a copy of the
script through a link, its ledger beside the copy under the default name and no --ledger passed.

Usage: test_pr_all_green.py [SCRIPT]
  SCRIPT  the script to test, or a directory holding it (default: ../../../.local/bin beside this kit)
Exit 0 when every case agrees with its expectation.
"""

import contextlib
import copy
import dataclasses
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, NamedTuple, NewType, Protocol, cast

# A case's name; prose a case expects in the answer; a JSON text as gh prints it; gh's exit status; a word of a
# command line; a JSON object's key; a number GitHub answers (an id, a count, a size); a name or state GitHub
# answers; a file's text (a workflow file, a job's log); a REST path and its query; how many cases disagree; the
# exit status.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
JsonText = NewType("JsonText", str)
GhStatus = NewType("GhStatus", int)
Word = NewType("Word", str)
Key = NewType("Key", str)
Count = NewType("Count", int)
Label = NewType("Label", str)
Text = NewType("Text", str)
Endpoint = NewType("Endpoint", str)
DisagreeCount = NewType("DisagreeCount", int)
ExitStatus = NewType("ExitStatus", int)
type Json = Label | Count | bool | None | list[Json] | dict[Key, Json]
type Obj = dict[Key, Json]
type Objs = list[Obj]
type Texts = dict[Key, Text]
type ObjsBy = dict[Key, Objs]


@dataclass
class World:
    """The planted GitHub, and the calls the script made to it."""

    view: Obj
    merged: Obj
    pull: Obj
    files: Objs
    workflows: dict[Key, Texts | None]
    runs: Objs
    jobs: ObjsBy
    logs: Texts
    extra_checks: Objs
    status: Obj
    rules: Objs
    artifacts: ObjsBy
    baseline: ObjsBy
    # By commit, its parents, the first first.
    parents: dict[Key, list[Label]]
    # By run id, its attempts before the latest, as `actions/runs/<id>/attempts/<n>` answers each beyond the fields
    # every attempt of a run shares; the run as listed is its latest attempt.
    attempts: ObjsBy = field(default_factory=dict[Key, Objs])
    # Check suites on the head beyond the runs' own, such as an app's that never runs.
    suites: Objs = field(default_factory=list[Obj])
    fail: Prose | None = None
    merge_fails: bool = False
    # By endpoint, the entries a list's total_count counts beyond those it lists (a runs search lists 1000 at most).
    unlisted: dict[Key, Count] = field(default_factory=dict[Key, Count])
    # By endpoint, the entries that join its list at the front, or move there, once its first page is read, as
    # GitHub answers them then: every later page is cut from the list so changed, and counts it.
    moving: dict[Key, Objs] = field(default_factory=dict[Key, Objs])
    # The endpoints whose pages carry no total_count.
    uncounted: list[Key] = field(default_factory=list[Key])
    calls: list[list[Word]] = field(default_factory=list[list[Word]])


REPO = Label("Jaetan/aletheia")
HEAD = Label("b" * 40)
BASE = Label("a" * 40)
MERGE = Label("e" * 40)
MOVED = Label("f" * 40)  # a base the merge commit merged that is not the pull request's base sha
PR = Word("7")
HOOKS = Path(__file__).resolve().parent.parent
STAMP = "2026-11-04T08:00:00.0000000Z "

FULL = Text("name: Full\non:\n  pull_request:\n  push:\n    branches: [main]\n")
LANES = Text("on:\n  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review]\n"
             + "    paths-ignore: ['**/*.md', 'docs/**']\n")
LICENCE = Text("on:\n  pull_request:\n    paths: ['packaging/licenses/**', 'tools/licence_audit.py']\n")
PRUNE = Text("on:\n  pull_request:\n    types: [closed]\n")
RELEASE = Text("on:\n  push:\n    tags: ['v*']\n")
INFRA = Text("on: [push, pull_request]\n")
WORKFLOWS: Texts = {Key(f".github/workflows/{n}"): t for n, t in (("full.yml", FULL), ("lanes.yml", LANES),
                    ("licence.yml", LICENCE), ("prune.yml", PRUNE), ("release.yml", RELEASE), ("infra.yml", INFRA))}
# A planted job's or run's defaults: one Result line, the first attempt, green, created at this time; a re-run's
# start, and the stamps of runs planted before and after the baseline's.
ONE, SUCCESS, CREATED = Count(1), Label("success"), Label("2026-11-04T07:46:07Z")
RERUN, BEFORE, AFTER = Label("2026-11-04T07:58:00Z"), Label("2026-11-04T07:40:00Z"), Label("2026-11-04T07:50:00Z")
PULL_REQUEST = Label("pull_request")
RENAMED = Label("renamed")  # a changed file's status when the pull request renames it


def sweep_log(steps: list[Label], skips: list[Label], results: Count = ONE) -> Text:
    """A sweep part's log: its skipped lanes, its passed steps, its Result lines."""
    lines = [f"{STAMP}  ⊘ {s} skipped (set X)" for s in skips]
    lines += [f"{STAMP}  ▶ {s} …\n{STAMP}  ✓ {s} (3s)" for s in steps]
    lines += [f"{STAMP}Result:   ALL {len(steps)} STEPS PASSED"] * results
    return Text("\n".join(lines) + "\n")


def job(i: Count, name: Label, conclusion: Label | None = SUCCESS) -> Obj:
    """A job with a green step and a skipped one; no conclusion means it is still running."""
    return {Key("id"): i, Key("name"): name, Key("status"): Label("completed" if conclusion else "in_progress"),
            Key("conclusion"): conclusion, Key("steps"): [
                {Key("name"): Label("build"), Key("status"): Label("completed"), Key("conclusion"): Label("success")},
                {Key("name"): Label("cache miss"), Key("status"): Label("completed"), Key("conclusion"): Label("skipped")}]}


def run(i: Count, path: Label, event: Label, conclusion: Label | None = SUCCESS, attempt: Count = ONE,
        created: Label = CREATED, started: Label | None = None) -> Obj:
    """A workflow run on the head, as its latest attempt answers it: created at its first attempt, started at its
    latest (at its creation unless given), naming #7 as the pull request it ran for."""
    return {Key("id"): i, Key("name"): Label(Path(path).stem), Key("path"): path, Key("event"): event,
            Key("status"): Label("completed" if conclusion else "in_progress"), Key("conclusion"): conclusion,
            Key("run_attempt"): attempt, Key("created_at"): created, Key("run_started_at"): started or created,
            Key("check_suite_id"): Count(i * 10), Key("pull_requests"): [{Key("number"): Count(int(PR))}]}


def attempt(n: Count, conclusion: Label | None, started: Label = CREATED) -> Obj:
    """An attempt before a run's latest, by the fields it does not share with the run."""
    return {Key("run_attempt"): n, Key("status"): Label("completed" if conclusion else "in_progress"),
            Key("conclusion"): conclusion, Key("run_started_at"): started}


def J(text: JsonText) -> Json:
    """A JSON value written as JSON, for the planted answers that need no builder."""
    return cast("Json", json.loads(text))


def baseline() -> World:
    """A pull request whose every check is green."""
    full, lanes, infra = (Label(f".github/workflows/{n}.yml") for n in ("full", "lanes", "infra"))
    pr, push = Label("pull_request"), Label("push")
    jobs: ObjsBy = {Key("1:1"): [job(Count(11), Label("tools/run_ci.py (rest)")),
                                 job(Count(12), Label("tools/run_ci.py (all gates)"))],
                    Key("2:1"): [job(Count(21), Label("stability bench (advisory)")), job(Count(22), Label("coverage floors"))],
                    Key("3:1"): [job(Count(31), Label("lint"))], Key("4:1"): [job(Count(41), Label("lint"))],
                    Key("90:1"): [job(Count(91), Label("tools/run_ci.py (rest)"))]}
    logs: Texts = {Key(str(j[Key("id")])): Text(f"{STAMP}ok\n") for js in jobs.values() for j in js}
    skips = [Label("mutation testing"), Label("coverage floors")]
    logs[Key("11")] = sweep_log([Label("build"), Label("proofs")], skips)
    logs[Key("91")] = sweep_log([Label("build"), Label("build staleness"), Label("proofs")], skips)
    return World(
        view=cast("Obj", J(JsonText(f'{{"state": "OPEN", "mergeStateStatus": "CLEAN", "headRefOid": "{HEAD}"}}'))),
        merged=cast("Obj", J(JsonText(f'{{"state": "MERGED", "mergeCommit": {{"oid": "{"c" * 40}"}}}}'))),
        pull=cast("Obj", J(JsonText(f'{{"state": "open", "head": {{"ref": "topic", "sha": "{HEAD}"}}, '
                                    + f'"base": {{"ref": "main", "sha": "{BASE}"}}, "changed_files": 2, '
                                    + f'"merge_commit_sha": "{MERGE}"}}'))),
        files=cast("Objs", J(JsonText('[{"filename": "tools/x.py", "status": "modified"}, '
                                      + '{"filename": "README.md", "status": "modified"}]'))),
        workflows={Key(HEAD): dict(WORKFLOWS), Key(MERGE): dict(WORKFLOWS), Key(BASE): dict(WORKFLOWS)},
        runs=[run(Count(1), full, pr), run(Count(2), lanes, pr), run(Count(3), infra, pr),
              run(Count(4), infra, push, created=Label("2026-11-04T07:46:03Z"))],
        jobs=jobs, logs=logs, extra_checks=[],
        status=cast("Obj", J(JsonText('{"state": "pending", "statuses": []}'))),
        rules=cast("Objs", J(JsonText('[{"type": "required_status_checks", "parameters": {"required_status_checks": '
                                      + '[{"context": "tools/run_ci.py (all gates)"}, {"context": "lint"}]}}, '
                                      + '{"type": "pull_request", "parameters": {}}]'))),
        artifacts={Key("1"): cast("Objs", J(JsonText('[{"name": "ci-sweep-log-rest", "size_in_bytes": 9}]')))},
        baseline={Key("full.yml"): [run(Count(90), full, push)]},
        parents={Key(MERGE): [BASE, HEAD]},
    )


class Answer(NamedTuple):
    """What the stand-in gh answers: its exit status and its stdout and stderr."""

    status: GhStatus
    out: JsonText
    err: Prose


def ok(text: JsonText | Text) -> Answer:
    """A successful answer."""
    return Answer(GhStatus(0), JsonText(text), Prose(""))


NOT_FOUND = Answer(GhStatus(1), JsonText(""), Prose("gh: Not Found (HTTP 404)"))
# GitHub's page when a call names no per_page, and the most per_page asks for.
PAGE, MOST_PER_PAGE = Count(30), Count(100)
PER_PAGE = re.compile(r"[?&]per_page=(\d+)")
# The --jq forms this stand-in applies: every entry of the page or of one key of it, and one key's entries beside
# the page's total_count.
EACH = re.compile(r"\.(\w*)\[\]")
COUNTED = re.compile(r"\{total_count, items: \.(\w+)\}")
# One attempt of a run, and a commit, as their REST paths name them.
ATTEMPT = re.compile(r"actions/runs/(\d+)/attempts/(\d+)")
COMMIT = re.compile(r"commits/(\w+)")


def printed(value: Json) -> JsonText:
    """One JSON value as gh prints a --jq result: compact, its keys sorted, a line of its own."""
    return JsonText(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n")


def dumps(value: Json | dict[Key, Objs]) -> JsonText:
    """One JSON value."""
    return JsonText(json.dumps(value))


class Listing(NamedTuple):
    """A list endpoint: its entries, the key each page holds them under (none when a page is the list itself), the
    page's other fields, whether each page carries total_count, and whether GitHub pages it at all."""

    entries: Objs
    key: Key | None = None
    fields: Obj | None = None
    counted: bool = False
    paged: bool = True


def pages_of(listing: Listing, size: Count, unlisted: Count) -> list[Json]:
    """The pages GitHub answers for ``listing``, ``size`` entries each, an empty list one empty page; total_count
    counts ``unlisted`` entries beyond those listed."""
    step = size if listing.paged else max(len(listing.entries), 1)
    chunks: list[list[Json]] = [[*listing.entries[i:i + step]] for i in range(0, len(listing.entries), step)] or [[]]
    if listing.key is None:
        return [*chunks]
    total: Obj = {Key("total_count"): Count(len(listing.entries) + unlisted)} if listing.counted else {}
    return [{**(listing.fields or {}), **total, listing.key: chunk} for chunk in chunks]


def jq(expr: Word | None, page: Json) -> JsonText:
    """``expr`` applied to one page as gh applies it, printed as gh prints it; a form it does not know fails loudly."""
    if expr is None:
        return dumps(page)
    if (each_of := EACH.fullmatch(expr)) is not None:
        items = page if not each_of[1] else page.get(Key(each_of[1])) if isinstance(page, dict) else None
        if not isinstance(items, list):
            raise AssertionError(f"--jq {expr} on {page!r}")
        return JsonText("".join(printed(x) for x in items))
    if (counted := COUNTED.fullmatch(expr)) is not None and isinstance(page, dict):
        return printed({Key("items"): page.get(Key(counted[1])), Key("total_count"): page.get(Key("total_count"))})
    raise AssertionError(f"--jq {expr}")


def number(value: Json) -> Count:
    """A planted number."""
    assert isinstance(value, int) and not isinstance(value, bool)
    return Count(value)


def attempt_of(w: World, run_id: Count, n: Count) -> Obj | None:
    """Attempt ``n`` of run ``run_id`` as `actions/runs/<id>/attempts/<n>` answers it, None when it has none."""
    found = [x for x in w.runs if x[Key("id")] == run_id]
    if not found or n == found[0][Key("run_attempt")]:
        return found[0] if found else None
    earlier = [x for x in w.attempts.get(Key(str(run_id)), []) if x[Key("run_attempt")] == n]
    return {**found[0], **earlier[0]} if earlier else None


def listing_of(w: World, head: Endpoint, path: Endpoint) -> Listing | None:
    """The list endpoint ``head`` (``path`` with its query), None for a ref with no workflow directory."""
    if head == f"pulls/{PR}/files":
        return Listing(w.files)
    if head == "contents/.github/workflows":
        tree = w.workflows.get(Key(path.split("=")[1]))
        if tree is None:
            return None
        entries: Objs = [{Key("type"): Label("file"), Key("path"): Label(p)} for p in tree]
        return Listing([*entries, {Key("type"): Label("dir"), Key("path"): Label(".github/workflows/old.yml")}],
                       paged=False)
    if head == "actions/runs":
        return Listing(w.runs, Key("workflow_runs"), counted=True)
    if head.startswith("actions/runs/") and "/attempts/" in head:
        rid, rest = head.removeprefix("actions/runs/").split("/attempts/")
        return Listing(w.jobs.get(Key(f"{rid}:{rest.split('/')[0]}"), []), Key("jobs"), counted=True)
    if head == f"commits/{HEAD}/check-runs":  # filter=all: every attempt's, each in its run's suite
        mine: Objs = [{Key("id"): j[Key("id")], Key("name"): j[Key("name")], Key("status"): j[Key("status")],
                       Key("conclusion"): j[Key("conclusion")], Key("app"): {Key("slug"): Label("github-actions")},
                       Key("check_suite"): {Key("id"): r[Key("check_suite_id")]}}
                      for r in w.runs for n in range(1, number(r[Key("run_attempt")]) + 1)
                      for j in w.jobs.get(Key(f"{r[Key('id')]}:{n}"), [])]
        return Listing([*mine, *w.extra_checks], Key("check_runs"), counted=True)
    if head == f"commits/{HEAD}/check-suites":
        theirs: Objs = [{Key("id"): r[Key("check_suite_id")], Key("app"): {Key("slug"): Label("github-actions")},
                         Key("status"): r[Key("status")], Key("conclusion"): r[Key("conclusion")]} for r in w.runs]
        return Listing([*theirs, *w.suites], Key("check_suites"), counted=True)
    if head == f"commits/{HEAD}/status":
        statuses = w.status[Key("statuses")]
        assert isinstance(statuses, list)
        return Listing(cast("Objs", statuses), Key("statuses"), {Key("state"): w.status[Key("state")]}, counted=True)
    if head == "rules/branches/main":
        return Listing(w.rules)
    if head.startswith("actions/workflows/"):
        return Listing(w.baseline.get(Key(head.split("/")[2]), []), Key("workflow_runs"), counted=True)
    if head.startswith("actions/runs/") and head.endswith("/artifacts"):
        return Listing(w.artifacts.get(Key(head.split("/")[2]), []), Key("artifacts"), counted=True)
    raise AssertionError(path)


def answer(w: World, a: list[Word]) -> Answer:
    """The planted GitHub's answer to one gh call, paged as GitHub pages it: the first page alone, of per_page
    entries or 30, without --paginate, every page with it, --jq applied to each page; a call it does not know fails
    the case loudly."""
    w.calls.append(a)
    if w.fail is not None and any(w.fail in x for x in a):
        return Answer(GhStatus(1), JsonText(""), Prose("gh: HTTP 502"))
    if a[:2] == ["pr", "view"]:
        return ok(dumps(w.merged if a[-1] == "state,mergeCommit" else w.view))
    if a[:2] == ["pr", "merge"]:
        return Answer(GhStatus(1), JsonText(""), Prose("merge refused")) if w.merge_fails else ok(JsonText(""))
    if a[0] != "api":
        raise AssertionError(a)
    if a[1] == "-H":
        path, ref = a[3].removeprefix(f"repos/{REPO}/contents/").split("?ref=")
        tree = w.workflows[Key(ref)]
        if tree is None or Key(path) not in tree:
            return NOT_FOUND
        return ok(tree[Key(path)])
    paginate = a[1] == "--paginate"
    rest = a[2:] if paginate else a[1:]
    if len(rest) not in (1, 3) or rest[1:2] not in ([], ["--jq"]) or (paginate and len(rest) == 1):
        raise AssertionError(a)
    expr = rest[2] if len(rest) == 3 else None
    path = Endpoint(rest[0].removeprefix(f"repos/{REPO}/"))
    head = Endpoint(path.split("?")[0])
    if head == f"pulls/{PR}":
        return ok(dumps(w.pull))
    if (at := ATTEMPT.fullmatch(head)) is not None:
        found = attempt_of(w, Count(int(at[1])), Count(int(at[2])))
        return NOT_FOUND if found is None else ok(dumps(found))
    if (commit := COMMIT.fullmatch(head)) is not None:
        parents = w.parents.get(Key(commit[1]))
        found_commit: Obj = {Key("sha"): Label(commit[1]), Key("parents"): [{Key("sha"): sha} for sha in parents or []]}
        return NOT_FOUND if parents is None else ok(dumps(found_commit))
    if head.startswith("actions/jobs/") and head.endswith("/logs"):
        job_id = Count(int(head.split("/")[2]))
        # GitHub keeps no log of a job still running or of one that ran no step, and answers 404 for it.
        done = [j for js in w.jobs.values() for j in js
                if j[Key("id")] == job_id and j[Key("status")] == "completed" and j[Key("steps")]]
        return ok(w.logs[Key(str(job_id))]) if done else NOT_FOUND
    listing = listing_of(w, head, path)
    if listing is None:
        return NOT_FOUND
    if Key(head) in w.uncounted:
        listing = listing._replace(counted=False)
    per_page = PER_PAGE.search(path)
    size = Count(min(int(per_page[1]), MOST_PER_PAGE)) if per_page else PAGE
    unlisted = w.unlisted.get(Key(head), Count(0))
    pages = pages_of(listing, size, unlisted)
    if moved := w.moving.get(Key(head), []):
        ids = {number(e[Key("id")]) for e in moved}
        later = listing._replace(entries=[*moved, *(e for e in listing.entries if number(e[Key("id")]) not in ids)])
        pages = [*pages[:1], *pages_of(later, size, unlisted)[1:]]
    return ok(JsonText("".join(jq(expr, page) for page in (pages if paginate else pages[:1]))))


def world_of(text: JsonText) -> World:
    """The world `dataclasses.asdict` wrote as JSON ``text``: each field it holds over the baseline's."""
    return dataclasses.replace(baseline(), **cast("dict[Key, Json]", json.loads(text)))


if len(sys.argv) > 1 and sys.argv[1] == "--as-gh":
    WORLD_FILE = Path(os.environ["PR_ALL_GREEN_WORLD"])
    planted = world_of(JsonText(WORLD_FILE.read_text(encoding="utf-8")))
    got = answer(planted, [Word(x) for x in sys.argv[2:]])
    _ = WORLD_FILE.write_text(json.dumps(dataclasses.asdict(planted)), encoding="utf-8")
    _ = sys.stdout.write(got.out)
    _ = sys.stderr.write(got.err)
    sys.exit(got.status)

GIVEN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HOOKS.parent.parent / ".local/bin"
SCRIPT = GIVEN / "pr-all-green" if GIVEN.is_dir() else GIVEN
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_loader("pr_all_green", importlib.machinery.SourceFileLoader("pr_all_green", str(SCRIPT)))
assert spec is not None and spec.loader is not None
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ScriptReader(Protocol):
    """The script's Reader class, whose gh a case replaces with the planted GitHub."""

    gh: ClassVar[Callable[["ScriptReader", *tuple[Word, ...]], JsonText]]


SCRIPT_MAIN = cast("Callable[[list[Word]], ExitStatus]", m.main)
SCRIPT_READER = cast("type[ScriptReader]", m.Reader)
SCRIPT_GH_ERROR = cast("type[Exception]", m.GhError)


def in_process(w: World, argv: list[Word]) -> tuple[ExitStatus, Prose]:
    """The script's main run here, its gh the planted GitHub."""

    def gh(_reader: ScriptReader, *args: Word) -> JsonText:
        got = answer(w, list(args))
        if got.status:
            raise SCRIPT_GH_ERROR(f"gh {' '.join(args)}: exit {got.status}: {got.err}")
        return got.out

    SCRIPT_READER.gh = gh
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            status = SCRIPT_MAIN(argv)
        except Exception as exc:  # noqa: BLE001  the script raised: the case disagrees, and the suite goes on
            print(f"RAISED {type(exc).__name__}: {exc}")
            status = RAISED
    return status, Prose(out.getvalue())


def as_process(w: World, argv: list[Word], script: Path = SCRIPT) -> tuple[ExitStatus, Prose]:
    """``script`` run whole, the stand-in gh first on PATH; the world comes back with the calls it saw."""
    with tempfile.TemporaryDirectory(prefix="pr-all-green-test-") as tmp:
        stand_in = Path(tmp) / "gh"
        _ = stand_in.write_text(f'#!/bin/sh\nexec {sys.executable} -I {Path(__file__).resolve()} --as-gh "$@"\n',
                                encoding="utf-8")
        stand_in.chmod(0o755)
        world_file = Path(tmp) / "world.json"
        _ = world_file.write_text(json.dumps(dataclasses.asdict(w)), encoding="utf-8")
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}", "PR_ALL_GREEN_WORLD": str(world_file)}
        done = subprocess.run([str(script), *argv], capture_output=True, text=True, check=False, env=env)
        back = world_of(JsonText(world_file.read_text(encoding="utf-8")))
        w.calls[:] = back.calls
    return ExitStatus(done.returncode), Prose(done.stdout + done.stderr)


type Plant = Callable[[World], None]


def find_job(w: World, i: Count) -> Obj:
    """The planted job with id ``i``."""
    return next(j for js in w.jobs.values() for j in js if j[Key("id")] == i)


def find_run(w: World, i: Count) -> Obj:
    """The planted run with id ``i``."""
    return next(r for r in w.runs if r[Key("id")] == i)


def set_job(i: Count, **fields: Json) -> Plant:
    """Change fields of job ``i``."""
    return lambda w: find_job(w, i).update({Key(k): v for k, v in fields.items()})


def set_run(i: Count, **fields: Json) -> Plant:
    """Change fields of run ``i``."""
    return lambda w: find_run(w, i).update({Key(k): v for k, v in fields.items()})


def head_file(name: Label, text: Text | None, ends: tuple[Label, ...] = (HEAD, MERGE)) -> Plant:
    """Plant (or, with None, remove) a workflow file at the head and its test merge commit, or at each of ``ends``."""
    def plant(w: World) -> None:
        for end in ends:
            tree = w.workflows[Key(end)]
            assert tree is not None
            if text is None:
                del tree[Key(f".github/workflows/{name}")]
            else:
                tree[Key(f".github/workflows/{name}")] = text
    return plant


def log_line(i: Count, line: Text) -> Plant:
    """Append a line to job ``i``'s log."""
    def plant(w: World) -> None:
        w.logs[Key(str(i))] = Text(w.logs[Key(str(i))] + STAMP + line + "\n")
    return plant


def part_log(text: Text) -> Plant:
    """Replace the sweep part's log."""
    def plant(w: World) -> None:
        w.logs[Key("11")] = text
    return plant


def drop_run(i: Count) -> Plant:
    """Remove run ``i``."""
    def plant(w: World) -> None:
        w.runs = [r for r in w.runs if r[Key("id")] != i]
    return plant


def changed(*names: Label, previous: Label | None = None, how: Label = RENAMED) -> Plant:
    """The pull request changes exactly ``names``, each modified, the first ``how`` (renamed unless given) from
    ``previous`` when given."""
    def plant(w: World) -> None:
        w.files = [{Key("filename"): n, Key("status"): Label("modified")} for n in names]
        if previous is not None:
            w.files[0].update({Key("previous_filename"): previous, Key("status"): how})
        w.pull[Key("changed_files")] = Count(len(names))
    return plant


def assign(**fields: Json | Prose | bool) -> Plant:
    """Set top-level fields of the world."""
    def plant(w: World) -> None:
        for k, v in fields.items():
            setattr(w, k, v)
    return plant


def view(**fields: Json) -> Plant:
    """Change fields of the pull request's view."""
    return lambda w: w.view.update({Key(k): v for k, v in fields.items()})


def each(*plants: Plant) -> Plant:
    """Several plants in turn."""
    def plant(w: World) -> None:
        for p in plants:
            p(w)
    return plant


def other_check(check_id: Count, name: Label, status: Label, conclusion: Label | None, app: Label, suite: Count) -> Plant:
    """A check run on the head that is no job of the latest runs."""
    def plant(w: World) -> None:
        w.extra_checks.append({Key("id"): check_id, Key("name"): name, Key("status"): status, Key("conclusion"): conclusion,
                               Key("app"): {Key("slug"): app}, Key("check_suite"): {Key("id"): suite}})
    return plant


def label(value: Json) -> Label:
    """A planted name, state or stamp."""
    assert isinstance(value, str)
    return Label(value)


def logged(w: World, jobs: Objs) -> None:
    """A clean log for each finished job of ``jobs``."""
    for j in jobs:
        if j[Key("status")] == "completed":
            w.logs[Key(str(j[Key("id")]))] = Text(f"{STAMP}ok\n")


def add_run(i: Count, name: Label, conclusion: Label | None, created: Label, jobs: Objs,
            event: Label = PULL_REQUEST) -> Plant:
    """Run ``i`` of the workflow file ``name``, created at ``created``, its one attempt ending ``conclusion`` (None
    while it runs) with ``jobs``."""
    def plant(w: World) -> None:
        w.runs.append(run(i, Label(f".github/workflows/{name}"), event, conclusion, created=created))
        w.jobs[Key(f"{i}:1")] = jobs
        logged(w, jobs)
    return plant


def rerun(i: Count, conclusion: Label | None, jobs: Objs) -> Plant:
    """Run ``i`` re-run once: what it was becomes its attempt 1, and attempt 2, started at RERUN, ends ``conclusion``
    (None while it runs) with ``jobs``."""
    def plant(w: World) -> None:
        r = find_run(w, i)
        first = r[Key("conclusion")]
        w.attempts[Key(str(i))] = [attempt(Count(1), None if first is None else label(first), label(r[Key("run_started_at")]))]
        r.update({Key("status"): Label("completed" if conclusion else "in_progress"), Key("conclusion"): conclusion,
                  Key("run_attempt"): Count(2), Key("run_started_at"): RERUN})
        w.jobs[Key(f"{i}:2")] = jobs
        logged(w, jobs)
    return plant


def first_attempt(i: Count, conclusion: Label, jobs: Objs) -> Plant:
    """Run ``i``'s attempt 1 ended ``conclusion`` with ``jobs``, and its re-run passed with the jobs it had."""
    def plant(w: World) -> None:
        green = w.jobs[Key(f"{i}:1")]
        find_run(w, i)[Key("conclusion")] = conclusion
        w.jobs[Key(f"{i}:1")] = jobs
        logged(w, jobs)
        rerun(i, SUCCESS, green)(w)
    return plant


def earlier_run(w: World) -> None:
    """A cancelled run of full.yml, created before the green one."""
    add_run(Count(8), Label("full.yml"), Label("cancelled"), BEFORE, [])(w)


def rerun_red(w: World) -> None:
    """The lanes run re-run: attempt 1 green, attempt 2 red."""
    rerun(Count(2), Label("failure"), [job(Count(23), Label("stability bench (advisory)"), Label("failure"))])(w)


def two_pulls(w: World) -> None:
    """The head is also the head of #8 (another base): #7's lanes run is red, #8's later one green."""
    of_other_pull(Count(2))(w)
    add_run(Count(5), Label("lanes.yml"), Label("failure"), BEFORE,
            [job(Count(51), Label("stability bench (advisory)"), Label("failure"))])(w)


def of_other_pull(i: Count) -> Plant:
    """Run ``i`` ran for #8, another pull request at the same head."""
    return set_run(i, pull_requests=[{Key("number"): Count(8)}])


def base_moved(w: World) -> None:
    """The merge commit merged a base newer than the pull request's base sha: lanes.yml runs on this pull request
    there and does not exist at the base sha; the head turns it into a release workflow."""
    w.workflows[Key(MOVED)] = dict(WORKFLOWS)
    tree = w.workflows[Key(BASE)]
    assert tree is not None
    del tree[Key(".github/workflows/lanes.yml")]
    head_file(Label("lanes.yml"), RELEASE)(w)
    w.parents[Key(MERGE)] = [MOVED, HEAD]


def app_suite(w: World) -> None:
    """An app that never runs on the head left its check suite queued with no check run, as on #465's head."""
    w.suites.append({Key("id"): Count(113251404920), Key("app"): {Key("slug"): Label("claude")},
                     Key("status"): Label("queued"), Key("conclusion"): None})


def set_pull(**fields: Json) -> Plant:
    """Change fields of the pull request."""
    return lambda w: w.pull.update({Key(k): v for k, v in fields.items()})


def lost_runner(again: Label | None) -> Plant:
    """Run 2's attempt 1 concluded failure in its bench job with no step concluded failure, as a runner GitHub lost
    leaves it, and its re-run, attempt 2, ends ``again`` (None while it runs) in its bench and coverage jobs."""
    def plant(w: World) -> None:
        find_run(w, Count(2))[Key("conclusion")] = Label("failure")
        w.jobs[Key("2:1")] = [job(Count(23), BENCH, Label("failure"))]
        logged(w, w.jobs[Key("2:1")])
        rerun(Count(2), again, [job(Count(21), BENCH, again), job(Count(22), Label("coverage floors"), again)])(w)
    return plant


def orphan(conclusion: Label) -> Plant:
    """A check run of run 2's check suite, concluded ``conclusion``, that is no job of its attempts."""
    return other_check(Count(5), Label("orphan"), Label("completed"), conclusion, Label("github-actions"), Count(20))


def startup_failure(w: World) -> None:
    """The lanes run failed to start: no job."""
    find_run(w, Count(2))[Key("conclusion")] = Label("startup_failure")
    w.jobs[Key("2:1")] = []


def bench_step(i: Count, conclusion: Label | None) -> Plant:
    """A step `bench` inside job ``i``, concluded ``conclusion`` (queued, not concluded, when None)."""
    def plant(w: World) -> None:
        steps = find_job(w, i)[Key("steps")]
        assert isinstance(steps, list)
        steps.append({Key("name"): Label("bench"), Key("status"): Label("completed" if conclusion else "queued"),
                      Key("conclusion"): conclusion})
    return plant


def failed_step(i: Count) -> Plant:
    """A failed step inside job ``i``."""
    return bench_step(i, Label("failure"))


def lost_runner_beside(w: World) -> None:
    """lost_runner's world, run 2's attempt 1 also holding a green job, a skipped one and a cancelled one."""
    lost_runner(SUCCESS)(w)
    beside = [job(Count(24), Label("coverage floors")), job(Count(25), Label("docs"), Label("skipped")),
              job(Count(26), Label("lint"), Label("cancelled"))]
    w.jobs[Key("2:1")] = [*w.jobs[Key("2:1")], *beside]
    logged(w, beside)


def moved_lanes(text: Text, how: Label = RENAMED) -> Plant:
    """The pull request's files report lanes.yml ``how`` (renamed unless given) to lanes2.yml, which holds ``text``
    at the head and the test merge commit, and tools/x.py modified; a rename leaves no lanes.yml there, a copy
    keeps it."""
    old, new = Label(".github/workflows/lanes.yml"), Label(".github/workflows/lanes2.yml")
    def plant(w: World) -> None:
        for end in (HEAD, MERGE):
            tree = w.workflows[Key(end)]
            assert tree is not None
            tree[Key(new)] = text
            if how == RENAMED:
                del tree[Key(old)]
        changed(new, Label("tools/x.py"), previous=old, how=how)(w)
    return plant


def runs_insert(w: World) -> None:
    """100 green workflow_dispatch runs of full.yml with no job, listed before the baseline's (newest first), and
    run 77 of lanes.yml, queued with no job yet, created at the front between the first page of 100 and the
    second."""
    fillers = [run(Count(1000 + i), Label(".github/workflows/full.yml"), Label("workflow_dispatch"),
                   created=Label("2026-11-04T07:30:00Z")) for i in range(100)]
    w.runs = [*fillers, *w.runs]
    late = run(Count(77), Label(".github/workflows/lanes.yml"), PULL_REQUEST, None, created=Label("2026-11-04T07:59:00Z"))
    late[Key("status")] = Label("queued")
    w.moving[Key("actions/runs")] = [late]


def checks_reorder(w: World) -> None:
    """100 green check runs of another app and its check `external`, listed last, still running when the first
    page of 100 is read; it then completes failure and moves to the front, total_count unchanged."""
    for i in range(100):
        other_check(Count(5000 + i), Label(f"other-{i:03d}"), Label("completed"), SUCCESS, Label("other"), Count(1))(w)
    other_check(Count(6000), Label("external"), Label("in_progress"), None, Label("other"), Count(1))(w)
    done = {**w.extra_checks[-1], Key("status"): Label("completed"), Key("conclusion"): Label("failure")}
    w.moving[Key(f"commits/{HEAD}/check-runs")] = [done]


def jobs_insert(w: World) -> None:
    """Run 3's attempt lists 100 green jobs after its lint job, and job 39, still running, joins the front between
    the first page of 100 and the second."""
    fillers = [job(Count(3000 + i), Label(f"lint {i:03d}")) for i in range(100)]
    w.jobs[Key("3:1")] = [*w.jobs[Key("3:1")], *fillers]
    logged(w, fillers)
    w.moving[Key("actions/runs/3/attempts/1/jobs")] = [job(Count(39), Label("late lint"), None)]


def uncounted(endpoint: Label) -> Plant:
    """The pages of the list at ``endpoint`` carry no total_count."""
    def plant(w: World) -> None:
        w.uncounted.append(Key(endpoint))
    return plant


def required(context: Label) -> Plant:
    """The ruleset also requires ``context``."""
    def plant(w: World) -> None:
        params = w.rules[0][Key("parameters")]
        assert isinstance(params, dict)
        contexts = params[Key("required_status_checks")]
        assert isinstance(contexts, list)
        contexts.append({Key("context"): context})
    return plant


def status_of(state: Label) -> Plant:
    """One commit status in ``state``."""
    return assign(status={Key("state"): state, Key("statuses"): [{Key("context"): Label("ci/x"), Key("state"): state}]})


def no_base_workflows(w: World) -> None:
    """The base has no workflow directory."""
    w.workflows[Key(BASE)] = None


def many_files(w: World) -> None:
    """GitHub says the pull request changes more files than gh listed."""
    w.pull[Key("changed_files")] = Count(3001)


def empty_artifact(i: Count) -> Plant:
    """Run ``i`` uploaded its sweep log empty."""
    def plant(w: World) -> None:
        w.artifacts[Key(str(i))] = [{Key("name"): Label("ci-sweep-log-rest"), Key("size_in_bytes"): Count(0)}]
    return plant


def first_attempt_artifact(i: Count) -> Plant:
    """Run ``i`` uploaded the bench's first attempt, which it does only when it retried."""
    def plant(w: World) -> None:
        w.artifacts[Key(str(i))] = [{Key("name"): Label("stability-variance-attempt1"), Key("size_in_bytes"): Count(842)}]
    return plant


def cancelled_bench(w: World) -> None:
    """A cancelled run of lanes.yml, created before the green one, its bench job cancelled while it ran."""
    add_run(Count(8), Label("lanes.yml"), Label("cancelled"), BEFORE, [job(Count(81), BENCH, Label("cancelled"))])(w)


def statuses(count: Count, red: Count) -> Plant:
    """``count`` commit statuses, the one at index ``red`` failed."""
    return assign(status={Key("state"): Label("failure"), Key("statuses"): [
        {Key("context"): Label(f"ci/{i:02d}"), Key("state"): Label("failure" if i == red else "success")}
        for i in range(count)]})


def second_page_rule(w: World) -> None:
    """A second required_status_checks rule, the 32nd rule and so on the second page of 30, requires a context no
    job carries."""
    filler: Objs = [{Key("type"): Label("pull_request"), Key("parameters"): {}} for _ in range(29)]
    w.rules = [*w.rules, *filler, {Key("type"): Label("required_status_checks"), Key("parameters"): {
        Key("required_status_checks"): [{Key("context"): Label("mutation testing")}]}}]


def counts_more(endpoint: Label) -> Plant:
    """The total_count of the list at ``endpoint`` counts one entry more than the list holds."""
    def plant(w: World) -> None:
        w.unlisted[Key(endpoint)] = Count(1)
    return plant


L, T, N = Label, Text, Count
SKIPS = [L("mutation testing"), L("coverage floors")]
BENCH = L("stability bench (advisory)")
# The bench's retry warning as the runner logs it, the annotation's title dropped, and the step's script as the
# runner echoes it into the same log.
RETRY_WARNING = T("##[warning]stability bench failed on attempt 1 \u2014 retrying once (runner jitter suspected; "
                  + "see the jitter watch)")
RETRY_SCRIPT = (T('\x1b[36;1mecho "::warning title=FLAKE-RETRY::stability bench failed on attempt 1 \u2014 retrying '
                  + 'once (runner jitter suspected; see the jitter watch)"\x1b[0m'),
                T('\x1b[36;1m  || echo "::warning title=FLAKE-RETRY::could not preserve attempt-1 stability JSONs '
                  + '(none written before the failure?)"\x1b[0m'))
RETRIED = Prose("lanes / stability bench (advisory) passed on a retry after a failed first attempt: ##[warning]")
COVERED_RETRY = Prose("cancelled run 8 lanes attempt 1 / stability bench (advisory): a bench failed its first attempt "
                      + "and retried: ##[warning]stability bench failed on attempt 1")
# The ledger's name beside the script and a ledger naming no failure; the finding an entry carries; the file a
# case's ledger is written to and passed as --ledger.
LEDGER_NAME = "pr-all-green-github-side.yaml"
NO_FAILURES = T("failures: []\n")
FINDING = T("the runner went offline in the incident GitHub's status page records for that hour")
INVESTIGATED = "investigated.yaml"
# Where a case's expected line names its ledger's path, which main writes in; how the answer starts when that
# ledger cannot be read; the remedy a red line names for an attempt the ledger names, less the run id.
AT_LEDGER = "<ledger>"
NOT_READ = f"the ledger cannot be read: {AT_LEDGER}"
RE_RUN = "GitHub-side per the ledger: re-run run"


def entry(run_id: Count | Label, n: Count | Label, repository: Label = REPO, finding: Text = FINDING) -> Text:
    """A ledger entry naming attempt ``n`` of run ``run_id``, each a number or the text an entry writes for it."""
    return T(f"  - repository: {repository}\n    run: {run_id}\n    attempt: {n}\n    finding: {finding}\n")


def ledger(*entries: Text) -> Text:
    """A ledger holding ``entries``."""
    return T("failures:\n" + "".join(entries))


NAMED = ledger(entry(N(2), N(1)))
UNNAMED = Prose("[red] run 2 lanes [pull_request] is completed/failure at attempt 1; a failure purely on GitHub's "
                + f"side is excused by an entry of the ledger {AT_LEDGER} naming run 2 attempt 1 with its finding\n")
# The red line of run 2's attempt 1 the ledger names when the excuse refuses a step or job of ours, and of run 8's
# cancelled attempt 1 holding a failed step; a failed check run that is no job.
LEDGER_REFUSES = Prose("[red] run 2 lanes [pull_request] is completed/failure at attempt 1: the ledger names it, and ")
CANCEL_REFUSES = Prose("[red] run 8 lanes [pull_request] is completed/cancelled at attempt 1: it was cancelled, and step "
                       + "stability bench (advisory) / bench of ours concluded failure\n")
ORPHAN_RED = Prose("[red] check orphan (github-actions) is completed/failure\n")
NO_LANES_RUN = Prose(f"lanes.yml runs on this pull request and has no pull_request run on {HEAD[:8]}")
ARGV = [PR, Word("--head"), Word(HEAD), Word("--repo"), Word(REPO)]
LANES_PATH = ".github/workflows/lanes.yml"
STOPS_LANES = [*ARGV, Word("--stops"), Word(LANES_PATH)]
BODY = Word(str(HOOKS / "tests" / "test_pr_all_green.py"))  # any readable file serves as the squash body
MERGE_ARGV = [*ARGV, Word("--squash-with-body"), BODY]
S = ExitStatus
GREEN, RED, UNREAD, INCOMPLETE, USAGE, NOT_MERGED, RAISED = S(0), S(1), S(2), S(3), S(4), S(5), S(-1)
MERGE_CALL = [Word(x) for x in ("pr", "merge", PR, "--repo", REPO, "--squash", "--match-head-commit", HEAD,
                                "--body-file", BODY)]


class Case(NamedTuple):
    """A planted GitHub, the command line, the exit status, a line the answer holds, whether it merges, whether it
    runs as a process, its ledger's text (None: no ledger written), whether it runs a copy with the ledger beside."""

    name: CaseId
    plant: Plant
    status: ExitStatus
    says: Prose
    argv: list[Word] = ARGV
    merges: bool = False
    process: bool = False
    ledger: Text | None = NO_FAILURES
    beside: bool = False


def nothing(_w: World) -> None:
    """Plant nothing."""


C, P = CaseId, Prose
CASES = [
    Case(C("baseline"), nothing, GREEN, P("ALL GREEN at")),
    Case(C("baseline-process"), nothing, GREEN, P("ALL GREEN at"), process=True),
    # point 1
    Case(C("head-moved"), view(headRefOid=L("d" * 40)), INCOMPLETE, P("the head moved")),
    Case(C("closed"), view(state=L("CLOSED")), RED, P("#7 is CLOSED")),
    Case(C("blocked"), view(mergeStateStatus=L("BLOCKED")), INCOMPLETE, P("merge state is BLOCKED")),
    Case(C("unknown"), view(mergeStateStatus=L("UNKNOWN")), INCOMPLETE, P("merge state is UNKNOWN")),
    Case(C("behind"), view(mergeStateStatus=L("BEHIND")), RED, P("BEHIND, not CLEAN")),
    Case(C("unstable"), view(mergeStateStatus=L("UNSTABLE")), RED, P("UNSTABLE, not CLEAN")),
    Case(C("has-hooks"), view(mergeStateStatus=L("HAS_HOOKS")), GREEN, P("ALL GREEN at")),
    # point 2: which workflows run on this pull request
    Case(C("expected-run-absent"), drop_run(N(2)), INCOMPLETE, P("lanes.yml runs on this pull request and has no pull_request run")),
    Case(C("paths-ignore-skips-docs-only"), each(changed(L("README.md"), L("docs/a/b.md")), drop_run(N(2))), GREEN,
         P("ALL GREEN at")),
    Case(C("paths-selects-licence"), changed(L("packaging/licenses/x.txt")), INCOMPLETE, P("licence.yml runs on this pull request")),
    Case(C("rename-source-counts"), changed(L("tools/y.py"), previous=L("tools/licence_audit.py")), INCOMPLETE,
         P("licence.yml runs on this pull request")),
    Case(C("negation-leaves-other-file"), head_file(L("lanes.yml"), T("on:\n  pull_request:\n    paths: ['**', '!tools/x.py']\n")),
         GREEN, P("ALL GREEN")),
    Case(C("negation-deselects-all"),
         each(head_file(L("lanes.yml"), T("on:\n  pull_request:\n    paths: ['tools/**', '!tools/x.py']\n"),
                        (HEAD, MERGE, BASE)),
              changed(L("tools/x.py")), drop_run(N(2))), GREEN, P("ALL GREEN at")),
    Case(C("negation-leaves-other-file-run-absent"),
         each(head_file(L("lanes.yml"), T("on:\n  pull_request:\n    paths: ['**', '!tools/x.py']\n")), drop_run(N(2))),
         INCOMPLETE, P("lanes.yml runs on this pull request")),
    Case(C("star-stays-in-its-directory"),
         each(head_file(L("licence.yml"), T("on:\n  pull_request:\n    paths: ['tools/*.yml']\n")), changed(L("tools/a/b.yml"))),
         GREEN, P("ALL GREEN")),
    Case(C("trailing-double-star-crosses-directories"), changed(L("packaging/licenses/a/b.txt")), INCOMPLETE,
         P("licence.yml runs on this pull request")),
    Case(C("double-star-crosses-directories"),
         each(head_file(L("licence.yml"), T("on:\n  pull_request:\n    paths: ['tools/**/*.yml']\n")), changed(L("tools/a/b.yml"))),
         INCOMPLETE, P("licence.yml runs on this pull request")),
    Case(C("types-closed-only"), head_file(L("lanes.yml"), PRUNE), RED,
         P("lanes.yml runs on this pull request at the base and not at the test merge commit")),
    Case(C("branches-filter"), head_file(L("lanes.yml"), T("on:\n  pull_request:\n    branches: [release/*]\n")), RED,
         P("at the base and not at the test merge commit")),
    Case(C("branches-ignore-filter"), head_file(L("licence.yml"), T("on:\n  pull_request:\n    branches-ignore: [main]\n")),
         GREEN, P("ALL GREEN")),
    Case(C("trigger-removed"), head_file(L("lanes.yml"), RELEASE), RED, P("stops it running on itself")),
    Case(C("new-workflow-at-head"), head_file(L("new.yml"), T("on: pull_request\n")), INCOMPLETE,
         P("new.yml runs on this pull request and has no pull_request run")),
    Case(C("workflow-deleted-at-head"), each(head_file(L("lanes.yml"), None), drop_run(N(2))), RED,
         P(".github/workflows/lanes.yml runs on this pull request at the base and deleted at the test merge commit: "
           + "the change stops it running on itself; `--stops .github/workflows/lanes.yml` names it")),
    Case(C("stops-deleted-named"), each(head_file(L("lanes.yml"), None), drop_run(N(2))), GREEN,
         P(f"stops running on itself, as --stops names: {LANES_PATH} runs on this pull request at the base and deleted "
           + "at the test merge commit\n"), argv=STOPS_LANES),
    Case(C("stops-trigger-removed-named"), head_file(L("lanes.yml"), RELEASE), GREEN,
         P(f"stops running on itself, as --stops names: {LANES_PATH} runs on this pull request at the base and not at "
           + "the test merge commit\n"), argv=STOPS_LANES),
    Case(C("stops-renamed-named"), each(moved_lanes(RELEASE), drop_run(N(2))), GREEN,
         P(f"stops running on itself, as --stops names: {LANES_PATH} runs on this pull request at the base and not as "
           + ".github/workflows/lanes2.yml at the test merge commit\n"), argv=STOPS_LANES),
    Case(C("stops-names-nothing"), nothing, RED,
         P(f"--stops names {LANES_PATH}, which runs on this pull request at the base and still runs"), argv=STOPS_LANES),
    Case(C("stops-names-another"), head_file(L("lanes.yml"), RELEASE), RED,
         P(f"{LANES_PATH} runs on this pull request at the base and not at the test merge commit: the change stops it"),
         argv=[*ARGV, Word("--stops"), Word(".github/workflows/full.yml")]),
    Case(C("stops-two-names"), head_file(L("lanes.yml"), RELEASE), RED,
         P("--stops names .github/workflows/full.yml, which runs on this pull request at the base and still runs"),
         argv=[*ARGV, Word("--stops"), Word(".github/workflows/full.yml"), Word("--stops"), Word(LANES_PATH)]),
    Case(C("workflow-renamed-trigger-removed"), each(moved_lanes(RELEASE), drop_run(N(2))), RED,
         P(".github/workflows/lanes.yml runs on this pull request at the base and not as .github/workflows/lanes2.yml at "
           + "the test merge commit: the change stops it running on itself")),
    Case(C("workflow-renamed-same-text"),
         each(moved_lanes(LANES), set_run(N(2), path=L(".github/workflows/lanes2.yml"), name=L("lanes2"))), GREEN,
         P("ALL GREEN at")),
    Case(C("workflow-copied"), moved_lanes(RELEASE, L("copied")), GREEN, P("ALL GREEN at")),
    Case(C("expected-run-of-other-pull-only"), of_other_pull(N(2)), INCOMPLETE, NO_LANES_RUN),
    Case(C("expected-run-naming-no-pull"), set_run(N(2), pull_requests=None), INCOMPLETE, NO_LANES_RUN),
    Case(C("no-workflows-at-base"), no_base_workflows, GREEN, P("ALL GREEN")),
    Case(C("workflow-added-at-base"), head_file(L("added.yml"), T("on: pull_request\n"), (BASE, MERGE)), INCOMPLETE,
         P("added.yml runs on this pull request and has no pull_request run")),
    Case(C("base-moved-stops-running"), base_moved, RED,
         P("lanes.yml runs on this pull request at the base and not at the test merge commit")),
    Case(C("no-merge-commit"), set_pull(merge_commit_sha=None), UNREAD,
         P("#7 has no test merge commit (merge_commit_sha is null)")),
    Case(C("merge-commit-of-another-head"), assign(parents={Key(MERGE): [BASE, L("d" * 40)]}), UNREAD,
         P(f"#7's test merge commit {MERGE} merges {BASE}, {'d' * 40}, not the base and {HEAD}")),
    Case(C("unreadable-pattern"), head_file(L("licence.yml"), T("on:\n  pull_request:\n    paths: ['a/[bc]']\n")), UNREAD,
         P("holds a character whose meaning")),
    Case(C("unknown-filter-key"), head_file(L("licence.yml"), T("on:\n  pull_request:\n    labels: [x]\n")), UNREAD,
         P("holds ['labels']")),
    Case(C("pull-request-target"), head_file(L("licence.yml"), T("on:\n  pull_request_target:\n")), UNREAD,
         P("pull_request_target")),
    Case(C("both-paths-filters"),
         head_file(L("licence.yml"), T("on:\n  pull_request:\n    paths: [a]\n    paths-ignore: [b]\n")), UNREAD,
         P("both paths and paths-ignore")),
    Case(C("bad-yaml"), head_file(L("licence.yml"), T("on: [\n")), UNREAD, P("does not parse as YAML")),
    Case(C("files-truncated"), many_files, UNREAD, P("changes 3001 files and gh listed 2")),
    Case(C("gh-fails"), assign(fail=P("contents/.github/workflows")), UNREAD, P("gh could not answer")),
    Case(C("gh-fails-process"), assign(fail=P("actions/runs?head_sha")), UNREAD, P("gh could not answer"), process=True),
    # point 3
    Case(C("twin-push-run-red"), each(set_run(N(4), conclusion=L("failure")), set_job(N(41), conclusion=L("failure"))), RED,
         P("[push] is completed/failure")),
    Case(C("twin-job-red-run-green"), set_job(N(41), conclusion=L("failure")), RED, P("job infra / lint is completed/failure")),
    Case(C("startup-failure"), startup_failure, RED, P("is completed/startup_failure")),
    Case(C("run-in-progress"), set_run(N(3), status=L("in_progress"), conclusion=None), INCOMPLETE, P("is in_progress/None")),
    Case(C("rerun-red"), rerun_red, RED, P("job lanes / stability bench (advisory) is completed/failure")),
    Case(C("runs-total-above"), counts_more(L("actions/runs")), UNREAD, P("actions/runs counts 5 workflow_runs and gh listed 4")),
    Case(C("superseded-red-run"), earlier_run, GREEN, P("superseded run 8")),
    Case(C("cancelled-replaced-same-second"),
         each(add_run(N(8), L("full.yml"), L("cancelled"), CREATED, []),
              add_run(N(9), L("full.yml"), SUCCESS, CREATED, [job(N(95), L("check"))])), GREEN,
         P("superseded run 8 full [pull_request] is completed/cancelled at attempt 1, replaced by run 9")),
    Case(C("cancelled-same-second-lower-id"), add_run(N(8), L("full.yml"), L("cancelled"), CREATED, []), RED,
         P("[red] run 8 full [pull_request] is completed/cancelled at attempt 1, and no later attempt or run replaced it")),
    Case(C("cancelled-attempt-2-same-second"),
         each(add_run(N(8), L("full.yml"), L("cancelled"), BEFORE, []), rerun(N(8), L("cancelled"), []),
              add_run(N(9), L("full.yml"), SUCCESS, RERUN, [job(N(95), L("check"))])), RED,
         P("[red] run 8 full [pull_request] is completed/cancelled at attempt 2, and no later attempt or run replaced it")),
    Case(C("rerun-of-older-run-red"),
         each(add_run(N(8), L("lanes.yml"), SUCCESS, BEFORE, [job(N(81), BENCH)]), rerun(N(8), L("failure"), [job(N(82), BENCH, L("failure"))])),
         RED, P("run 8 lanes [pull_request] is completed/failure at attempt 2")),
    Case(C("rerun-of-older-run-running"),
         each(add_run(N(8), L("lanes.yml"), SUCCESS, BEFORE, [job(N(81), BENCH)]), rerun(N(8), None, [job(N(82), BENCH, None)])),
         INCOMPLETE, P("run 8 lanes [pull_request] is in_progress/None at attempt 2")),
    Case(C("other-pull-same-head"), two_pulls, RED, P("run 5 lanes [pull_request] is completed/failure at attempt 1")),
    Case(C("earlier-run-running"), add_run(N(8), L("lanes.yml"), None, BEFORE, [job(N(81), BENCH, None)]), INCOMPLETE,
         P("run 8 lanes [pull_request] is in_progress/None at attempt 1")),
    Case(C("earlier-run-failed"), add_run(N(8), L("lanes.yml"), L("failure"), BEFORE, [job(N(81), BENCH, L("failure"))]), RED,
         P("run 8 lanes [pull_request] is completed/failure at attempt 1")),
    Case(C("earlier-startup-failure"), add_run(N(8), L("lanes.yml"), L("startup_failure"), BEFORE, []), RED,
         P("[red] run 8 lanes [pull_request] is completed/startup_failure at attempt 1\n")),
    Case(C("attempt-1-red-attempt-2-green"), first_attempt(N(2), L("failure"), [job(N(23), BENCH, L("failure"))]), RED,
         P("run 2 lanes [pull_request] is completed/failure at attempt 1")),
    Case(C("cancelled-attempt-rerun-green"),
         first_attempt(N(2), L("cancelled"), [job(N(23), BENCH), job(N(24), L("coverage floors"), L("skipped")),
                                              job(N(25), L("mutation testing"), L("cancelled"))]),
         GREEN, P("superseded run 2 lanes [pull_request] is completed/cancelled at attempt 1, replaced by run 2")),
    Case(C("cancelled-run-failed-job"),
         add_run(N(8), L("full.yml"), L("cancelled"), BEFORE, [job(N(81), L("lint"), L("failure")), job(N(82), L("build"), L("cancelled"))]),
         RED, P("[red] run 8 full [pull_request] is completed/cancelled at attempt 1\n")),
    Case(C("cancelled-run-replacement-running"),
         each(add_run(N(8), L("full.yml"), L("cancelled"), BEFORE, [job(N(81), L("lint"), L("cancelled"))]),
              set_run(N(1), status=L("in_progress"), conclusion=None)),
         INCOMPLETE, P("run 8 full [pull_request] is completed/cancelled at attempt 1, and run 1 full [pull_request] is "
                       + "in_progress/None at attempt 1 replaces it")),
    Case(C("cancelled-run-not-replaced"),
         each(add_run(N(8), L("infra.yml"), L("cancelled"), L("2026-11-04T07:46:08Z"), []),
              add_run(N(9), L("infra.yml"), SUCCESS, L("2026-11-04T07:46:09Z"), [], L("push")),
              add_run(N(10), L("full.yml"), SUCCESS, L("2026-11-04T07:46:09Z"), [])),
         RED, P("run 8 infra [pull_request] is completed/cancelled at attempt 1, and no later attempt or run replaced it")),
    Case(C("cancelled-run-last-replacement-cancelled"),
         each(add_run(N(8), L("lanes.yml"), L("cancelled"), BEFORE, []), add_run(N(9), L("lanes.yml"), L("cancelled"), AFTER, [])),
         RED, P("run 8 lanes [pull_request] is completed/cancelled at attempt 1, and its last replacement run 9 lanes "
                + "[pull_request] is completed/cancelled")),
    Case(C("rerun-cancelled-after-later-run"),
         each(add_run(N(8), L("lanes.yml"), SUCCESS, BEFORE, [job(N(81), BENCH)]),
              rerun(N(8), L("cancelled"), [job(N(82), BENCH, L("cancelled"))])),
         RED, P("run 8 lanes [pull_request] is completed/cancelled at attempt 2, and no later attempt or run replaced it")),
    Case(C("cancelled-replaced-by-other-pull"), each(cancelled_bench, of_other_pull(N(2))), RED,
         P("[red] run 8 lanes [pull_request] is completed/cancelled at attempt 1, and no later attempt or run replaced it\n")),
    Case(C("cancelled-push-replaced-by-push"),
         each(add_run(N(8), L("infra.yml"), L("cancelled"), BEFORE, [], L("push")), set_run(N(4), pull_requests=[])), GREEN,
         P("superseded run 8 infra [push] is completed/cancelled at attempt 1, replaced by run 4 infra [push]")),
    Case(C("cancelled-job-failed-step"), each(cancelled_bench, failed_step(N(81))), RED, CANCEL_REFUSES),
    Case(C("cancelled-green-job-failed-step"), each(cancelled_bench, set_job(N(81), conclusion=SUCCESS), failed_step(N(81))),
         RED, CANCEL_REFUSES),
    Case(C("cancelled-step-not-concluded"), each(cancelled_bench, bench_step(N(81), None)), GREEN, P("superseded run 8")),
    # point 3: the ledger excuse
    Case(C("github-side-rerun-green"), lost_runner(SUCCESS), GREEN, P(f"github-side run 2 attempt 1: {FINDING}\n"),
         ledger=NAMED),
    Case(C("github-side-unnamed"), lost_runner(SUCCESS), RED, UNNAMED),
    Case(C("github-side-unnamed-stepless"), each(lost_runner(SUCCESS), set_job(N(23), steps=[])), RED, UNNAMED),
    Case(C("github-side-unnamed-failed-step"), each(lost_runner(SUCCESS), failed_step(N(23))), RED,
         P("[red] run 2 lanes [pull_request] is completed/failure at attempt 1\n")),
    Case(C("github-side-other-repository"), lost_runner(SUCCESS), RED, UNNAMED,
         ledger=ledger(entry(N(2), N(1), L("Jaetan/other")))),
    Case(C("github-side-unnamed-rerun-red"), lost_runner(L("failure")), RED,
         P("[red] run 2 lanes [pull_request] is completed/failure at attempt 1\n")),
    Case(C("github-side-no-rerun"),
         each(set_run(N(2), conclusion=L("failure")), set_job(N(21), conclusion=L("failure"))), RED,
         P(f"[red] run 2 lanes [pull_request] is completed/failure at attempt 1: {RE_RUN} 2\n"), ledger=NAMED),
    Case(C("github-side-rerun-red"), lost_runner(L("failure")), RED,
         P(f"[red] run 2 lanes [pull_request] is completed/failure at attempt 1: {RE_RUN} 2\n"), ledger=NAMED),
    Case(C("github-side-rerun-running"), lost_runner(None), INCOMPLETE,
         P("[incomplete] run 2 lanes [pull_request] is completed/failure at attempt 1, GitHub-side per the ledger, and "
           + "run 2 lanes [pull_request] is in_progress/None at attempt 2 replaces it\n"), ledger=NAMED),
    Case(C("github-side-failed-step"), each(lost_runner(SUCCESS), failed_step(N(23))), RED,
         P("[red] run 2 lanes [pull_request] is completed/failure at attempt 1: the ledger names it, and step "
           + "stability bench (advisory) / bench of ours concluded failure"), ledger=NAMED),
    Case(C("github-side-startup-failure"), first_attempt(N(2), L("startup_failure"), []), RED,
         P("[red] run 2 lanes [pull_request] is completed/startup_failure at attempt 1\n"), ledger=NAMED),
    Case(C("github-side-later-run-only"),
         add_run(N(8), L("lanes.yml"), L("failure"), BEFORE, [job(N(81), BENCH, L("failure"))]), RED,
         P(f"[red] run 8 lanes [pull_request] is completed/failure at attempt 1: {RE_RUN} 8\n"),
         ledger=ledger(entry(N(8), N(1)))),
    Case(C("github-side-retry-line"), each(lost_runner(SUCCESS), log_line(N(23), RETRY_WARNING)), RED,
         P("github-side run 2 lanes attempt 1 / stability bench (advisory): a bench failed its first attempt and "
           + "retried: ##[warning]stability bench failed on attempt 1"), ledger=NAMED),
    Case(C("github-side-first-attempt-artifact"), each(lost_runner(SUCCESS), first_attempt_artifact(N(2))), RED,
         P(f"RED at {HEAD}: 1 problem(s)"), ledger=NAMED),
    Case(C("github-side-superseded-check"), each(lost_runner(SUCCESS), orphan(L("cancelled"))), RED,
         P("[red] check orphan (github-actions) is completed/cancelled\n"), ledger=NAMED),
    Case(C("github-side-waiting-check"), each(lost_runner(None), orphan(L("failure"))), RED,
         P("[red] check orphan (github-actions) is completed/failure"), ledger=NAMED),
    Case(C("ledger-superseded-suite-red-check"), each(lost_runner(SUCCESS), orphan(L("failure"))), RED, ORPHAN_RED,
         ledger=NAMED),
    Case(C("ledger-job-timed-out"),
         each(lost_runner(SUCCESS), set_job(N(23), conclusion=L("timed_out")), bench_step(N(23), L("cancelled"))), RED,
         P(f"{LEDGER_REFUSES}job stability bench (advisory) of ours concluded timed_out\n"), ledger=NAMED),
    Case(C("ledger-step-timed-out"), each(lost_runner(SUCCESS), bench_step(N(23), L("timed_out"))), RED,
         P(f"{LEDGER_REFUSES}step stability bench (advisory) / bench of ours concluded timed_out\n"), ledger=NAMED),
    Case(C("ledger-step-cancelled"), each(lost_runner(SUCCESS), bench_step(N(23), L("cancelled"))), GREEN,
         P(f"github-side run 2 attempt 1: {FINDING}\n"), ledger=NAMED),
    Case(C("ledger-jobs-allowed"), lost_runner_beside, GREEN, P(f"github-side run 2 attempt 1: {FINDING}\n"), ledger=NAMED),
    Case(C("github-side-unnamed-job-timed-out"), each(lost_runner(SUCCESS), set_job(N(23), conclusion=L("timed_out"))),
         RED, P("[red] run 2 lanes [pull_request] is completed/failure at attempt 1\n")),
    # the ledger
    Case(C("ledger-beside-script"), lost_runner(SUCCESS), GREEN, P(f"github-side run 2 attempt 1: {FINDING}\n"),
         ledger=NAMED, beside=True),
    Case(C("ledger-beside-script-missing"), nothing, UNREAD, P(f"/kit/{LEDGER_NAME}: No such file or directory\n"),
         ledger=None, beside=True),
    Case(C("ledger-missing"), nothing, UNREAD,
         P(f"the ledger cannot be read: /nonexistent/{INVESTIGATED}: No such file or directory\n"),
         [*ARGV, Word("--ledger"), Word(f"/nonexistent/{INVESTIGATED}")], ledger=None),
    Case(C("ledger-empty-file"), nothing, UNREAD, P(f"{NOT_READ} is not a mapping of the one key `failures`\n"),
         ledger=T("")),
    Case(C("ledger-yaml-error"), nothing, UNREAD, P(f"{NOT_READ} does not parse as YAML"), ledger=T("failures: [\n")),
    Case(C("ledger-other-key"), nothing, UNREAD, P(f"{NOT_READ} is not a mapping of the one key `failures`\n"),
         ledger=T("failures: []\nreviewed: yes\n")),
    Case(C("ledger-failures-not-list"), nothing, UNREAD, P(f"{NOT_READ}: `failures` is not a list\n"),
         ledger=T("failures: none\n")),
    Case(C("ledger-entry-not-mapping"), nothing, UNREAD, P(f"{NOT_READ} entry 2 'run 2' is not a mapping\n"),
         ledger=ledger(entry(N(2), N(1)), T("  - run 2\n"))),
    Case(C("ledger-unknown-key"), nothing, UNREAD,
         P(f"{NOT_READ} entry 1 {{'repository': 'Jaetan/aletheia', 'run': '2', 'attempt': '1', "
           + f"'finding': \"{FINDING}\", 'seen': 'today'}} holds ['attempt', 'finding', 'repository', 'run', 'seen'], "
           + "not exactly repository, run, attempt, finding\n"),
         ledger=ledger(T(entry(N(2), N(1)) + "    seen: today\n"))),
    Case(C("ledger-missing-key"), nothing, UNREAD,
         P("holds ['attempt', 'repository', 'run'], not exactly repository, run, attempt, finding\n"),
         ledger=T(f"failures:\n  - repository: {REPO}\n    run: 2\n    attempt: 1\n")),
    Case(C("ledger-value-not-scalar"), nothing, UNREAD, P("holds a value that is not a scalar\n"),
         ledger=ledger(entry(L("[2]"), N(1)))),
    Case(C("ledger-repository-not-owner-name"), nothing, UNREAD, P(": the repository 'aletheia' is not OWNER/NAME\n"),
         ledger=ledger(entry(N(2), N(1), L("aletheia")))),
    Case(C("ledger-run-not-decimal"), nothing, UNREAD, P(": the run '2a' is not a decimal number\n"),
         ledger=ledger(entry(L("2a"), N(1)))),
    Case(C("ledger-attempt-not-decimal"), nothing, UNREAD, P(": the attempt 'one' is not a decimal number\n"),
         ledger=ledger(entry(N(2), L("one")))),
    Case(C("ledger-empty-finding"), nothing, UNREAD, P(": the finding is empty\n"),
         ledger=ledger(entry(N(2), N(1), finding=T("'   '")))),
    # point 4
    Case(C("skipped-job"), set_job(N(22), conclusion=L("skipped")), RED, P("coverage floors is completed/skipped")),
    Case(C("neutral-job"), set_job(N(22), conclusion=L("neutral")), RED, P("coverage floors is completed/neutral")),
    Case(C("failed-step-in-green-job"), failed_step(N(21)), RED, P("has steps bench=failure")),
    Case(C("stepless-failed-job"), set_job(N(21), conclusion=L("failure"), steps=[]), RED,
         P("job lanes / stability bench (advisory) is completed/failure")),
    Case(C("job-in-progress"), set_job(N(22), status=L("in_progress"), conclusion=None), INCOMPLETE,
         P("coverage floors is in_progress/None")),
    # point 5
    Case(C("other-app-red"), other_check(N(5), L("external"), L("completed"), L("failure"), L("other"), N(1)), RED,
         P("check external (other)")),
    Case(C("other-app-running"), other_check(N(5), L("external"), L("queued"), None, L("other"), N(1)), INCOMPLETE,
         P("is queued/None")),
    Case(C("stray-actions-check"), other_check(N(5), L("orphan"), L("completed"), L("failure"), L("github-actions"), N(1)), RED,
         P("check orphan")),
    Case(C("superseded-check"),
         each(earlier_run, other_check(N(5), L("lint"), L("completed"), L("cancelled"), L("github-actions"), N(80))),
         RED, P("[red] check lint (github-actions) is completed/cancelled\n")),
    Case(C("cancelled-same-run-suite-red-check"),
         each(first_attempt(N(2), L("cancelled"), [job(N(23), BENCH, L("cancelled"))]), orphan(L("failure"))), RED,
         ORPHAN_RED),
    Case(C("cancelled-other-run-suite-red-check"),
         each(cancelled_bench, other_check(N(5), L("orphan"), L("completed"), L("failure"), L("github-actions"), N(80))),
         RED, ORPHAN_RED),
    Case(C("status-red"), status_of(L("failure")), RED, P("status ci/x is failure")),
    Case(C("status-pending"), status_of(L("pending")), INCOMPLETE, P("status ci/x is pending")),
    Case(C("status-red-31st"), statuses(N(31), N(30)), RED, P("status ci/30 is failure")),
    Case(C("statuses-total-above"), counts_more(L(f"commits/{HEAD}/status")), UNREAD,
         P("status counts 1 statuses and gh listed 0")),
    Case(C("check-runs-total-above"), counts_more(L(f"commits/{HEAD}/check-runs")), UNREAD,
         P("check-runs counts 7 check_runs and gh listed 6")),
    Case(C("suite-without-check-run"), app_suite, GREEN, P("suite 113251404920 (claude) holds no check run: queued/None")),
    Case(C("check-suites-total-above"), counts_more(L(f"commits/{HEAD}/check-suites")), UNREAD,
         P("check-suites counts 5 check_suites and gh listed 4")),
    # every list read whole
    Case(C("runs-insert-between-pages"), runs_insert, UNREAD,
         P("actions/runs: the list moved while it was read: id 1099 is listed twice\n")),
    Case(C("check-runs-reorder-between-pages"), checks_reorder, UNREAD,
         P(f"commits/{HEAD}/check-runs: the list moved while it was read: id 5093 is listed twice\n")),
    Case(C("jobs-insert-between-pages"), jobs_insert, UNREAD,
         P("actions/runs/3/attempts/1/jobs: the list moved while it was read: id 3098 is listed twice\n")),
    Case(C("runs-page-without-total-count"), uncounted(L("actions/runs")), UNREAD,
         P("actions/runs answered a page of workflow_runs without total_count\n")),
    # point 6
    Case(C("required-absent"), required(L("mutation testing")), INCOMPLETE, P("required context 'mutation testing' is no job")),
    Case(C("no-required"), assign(rules=[]), UNREAD, P("names no required context")),
    Case(C("required-on-rules-page-2"), second_page_rule, INCOMPLETE, P("required context 'mutation testing' is no job")),
    Case(C("red-outranks-unread"), each(assign(rules=[]), set_job(N(22), conclusion=L("failure"))), RED,
         P("RED at")),
    # point 7
    Case(C("flake-retry"), log_line(N(21), T("##[warning]FLAKE-RETRY stability bench failed on attempt 1")), RED,
         P("passed on a retry")),
    Case(C("echoed-flake-retry"),
         log_line(N(21), T('\x1b[36;1mecho "::warning title=FLAKE-RETRY::stability bench failed on attempt 1"\x1b[0m')),
         GREEN, P("ALL GREEN")),
    Case(C("retry-warning"), log_line(N(21), RETRY_WARNING), RED, P(f"{RETRIED}stability bench failed on attempt 1")),
    Case(C("retry-script-echoed"), each(*(log_line(N(21), line) for line in RETRY_SCRIPT)), GREEN, P("ALL GREEN")),
    Case(C("retry-could-not-preserve"),
         log_line(N(21), T("##[warning]could not preserve attempt-1 stability JSONs (none written before the failure?)")),
         RED, P(f"{RETRIED}could not preserve")),
    Case(C("retry-retrying-once"), log_line(N(21), T("##[warning]stability bench retrying once")), RED,
         P(f"{RETRIED}stability bench retrying once")),
    Case(C("retry-re-measuring-once"), log_line(N(21), T("##[warning]throughput gate re-measuring once")), RED,
         P(f"{RETRIED}throughput gate re-measuring once")),
    Case(C("error-line"), log_line(N(31), T("##[error]Process completed with exit code 1.")), RED, P("infra / lint: ##[error]")),
    Case(C("upload-found-nothing"),
         log_line(N(11), T("##[warning]No files were found with the provided path: x/*.log. No artifacts will be uploaded.")),
         RED, P("uploaded nothing")),
    Case(C("benign-warning"), log_line(N(31), T("##[warning]Node.js 20 actions are deprecated.")), GREEN,
         P("warning in infra / lint: ##[warning]Node.js 20")),
    Case(C("superseded-run-retry-warning"), each(cancelled_bench, log_line(N(81), RETRY_WARNING)), RED, COVERED_RETRY),
    Case(C("waiting-run-retry-warning"),
         each(cancelled_bench, set_run(N(2), status=L("in_progress"), conclusion=None), log_line(N(81), RETRY_WARNING)),
         RED, COVERED_RETRY),
    Case(C("superseded-run-cancel-error-unjudged"),
         each(cancelled_bench, log_line(N(81), T("##[error]The operation was canceled."))), GREEN, P("superseded run 8")),
    Case(C("superseded-run-retry-script-echoed"), each(cancelled_bench, *(log_line(N(81), line) for line in RETRY_SCRIPT)),
         GREEN, P("superseded run 8")),
    Case(C("superseded-job-never-started"), each(cancelled_bench, set_job(N(81), steps=[])), GREEN, P("superseded run 8")),
    # point 8
    Case(C("conditional-step-absent"), nothing, GREEN, P("2 steps (base 3)")),
    Case(C("step-missing"), part_log(sweep_log([L("build")], SKIPS)), RED, P("did not pass ['proofs']")),
    Case(C("lane-skipped"), part_log(sweep_log([L("build"), L("proofs")], [*SKIPS, L("actionlint")])), RED,
         P("skipped ['actionlint']")),
    Case(C("two-results"), part_log(sweep_log([L("build"), L("proofs")], SKIPS, N(2))), RED,
         P("reads 2 `ALL N STEPS PASSED` lines")),
    Case(C("no-result"), part_log(sweep_log([L("build"), L("proofs")], SKIPS, N(0))), RED,
         P("reads 0 `ALL N STEPS PASSED` lines")),
    Case(C("new-step"), part_log(sweep_log([L("build"), L("proofs"), L("fresh")], SKIPS)), GREEN, P("new steps ['fresh']")),
    Case(C("no-baseline-run"), assign(baseline={}), UNREAD, P("no job of that name in a green push run")),
    Case(C("baseline-lacks-part"), set_job(N(91), name=L("tools/run_ci.py (other)")), UNREAD, P("no job of that name")),
    # point 9
    Case(C("empty-artifact"), empty_artifact(N(1)), RED, P("artifact ci-sweep-log-rest of full is empty")),
    Case(C("first-attempt-artifact"), first_attempt_artifact(N(2)), RED,
         P("artifact stability-variance-attempt1 of lanes is a first attempt's upload")),
    Case(C("superseded-run-artifact-unread"), each(earlier_run, empty_artifact(N(8))), GREEN, P("superseded run 8")),
    Case(C("superseded-run-first-attempt-artifact"), each(cancelled_bench, first_attempt_artifact(N(8))), RED,
         P("artifact stability-variance-attempt1 of cancelled run 8 lanes is a first attempt's upload")),
    Case(C("cancelled-attempt-artifact-read-once"),
         each(first_attempt(N(2), L("cancelled"), [job(N(23), BENCH, L("cancelled"))]), first_attempt_artifact(N(2))),
         RED, P(f"RED at {HEAD}: 1 problem(s)")),
    # the merge
    Case(C("merge-on-green"), nothing, GREEN, P(f"MERGED #7 as {'c' * 40}"), MERGE_ARGV, merges=True),
    Case(C("merge-on-green-process"), nothing, GREEN, P(f"MERGED #7 as {'c' * 40}"), MERGE_ARGV, merges=True, process=True),
    Case(C("no-merge-on-red"), set_job(N(22), conclusion=L("failure")), RED, P("coverage floors"), MERGE_ARGV),
    Case(C("no-merge-on-incomplete"), view(mergeStateStatus=L("BLOCKED")), INCOMPLETE, P("BLOCKED"), MERGE_ARGV),
    Case(C("no-merge-on-unread"), assign(rules=[]), UNREAD, P("names no required context"), MERGE_ARGV),
    Case(C("merge-not-landed"), assign(merged={Key("state"): L("OPEN"), Key("mergeCommit"): None}), NOT_MERGED,
         P("NOT MERGED: #7 reads OPEN"), MERGE_ARGV, merges=True),
    Case(C("merged-without-commit"), assign(merged={Key("state"): L("MERGED"), Key("mergeCommit"): None}), NOT_MERGED,
         P("NOT MERGED: #7 reads MERGED"), MERGE_ARGV, merges=True),
    Case(C("open-with-commit"), assign(merged={Key("state"): L("OPEN"), Key("mergeCommit"): {Key("oid"): L("c" * 40)}}),
         NOT_MERGED, P("NOT MERGED: #7 reads OPEN"), MERGE_ARGV, merges=True),
    Case(C("merge-refused"), assign(merge_fails=True), UNREAD, P("merge refused"), MERGE_ARGV, merges=True),
    # the command line
    Case(C("short-head"), nothing, USAGE, P("a full 40-character commit hash"),
         [PR, Word("--head"), Word("abc"), Word("--repo"), Word(REPO)]),
    Case(C("zero-pull"), nothing, USAGE, P("PR is a positive number"), [Word("0"), *ARGV[1:]]),
    Case(C("no-head"), nothing, USAGE, P("--head"), [PR, Word("--repo"), Word(REPO)]),
    Case(C("body-missing"), nothing, USAGE, P("which is no file"), [*ARGV, Word("--squash-with-body"), Word("/nonexistent/b")]),
    Case(C("usage-process"), nothing, USAGE, P("--head"), [PR], process=True),
]


def run_case(case: Case, w: World, tmp: Path) -> tuple[ExitStatus, Prose]:
    """``case`` run on ``w``, its ledger written in ``tmp`` and passed as --ledger, or, for a case run beside, a copy
    of the script in ``tmp`` run through a link, its ledger beside the copy and none passed."""
    if case.beside:
        kit, links = tmp / "kit", tmp / "bin"
        kit.mkdir()
        links.mkdir()
        _ = shutil.copy2(SCRIPT, kit / "pr-all-green")
        if case.ledger is not None:
            _ = (kit / LEDGER_NAME).write_text(case.ledger, encoding="utf-8")
        (links / "pr-all-green").symlink_to(kit / "pr-all-green")
        return as_process(w, case.argv, links / "pr-all-green")
    argv = case.argv
    if case.ledger is not None:
        _ = (tmp / INVESTIGATED).write_text(case.ledger, encoding="utf-8")
        argv = [*argv, Word("--ledger"), Word(str(tmp / INVESTIGATED))]
    return (as_process if case.process else in_process)(w, argv)


def main() -> ExitStatus:
    """Run every case; print one line per case; exit 0 when all agree."""
    wrong = DisagreeCount(0)
    for case in CASES:
        w = copy.deepcopy(baseline())
        case.plant(w)
        with tempfile.TemporaryDirectory(prefix="pr-all-green-ledger-") as tmp:
            status, out = run_case(case, w, Path(tmp))
            says = Prose(case.says.replace(AT_LEDGER, str(Path(tmp) / INVESTIGATED)))
        merges = [c for c in w.calls if c[:2] == ["pr", "merge"]]
        agrees = status == case.status and says in out and merges == ([MERGE_CALL] if case.merges else [])
        if agrees:
            print(f"ok   {case.name}")
        else:
            wrong = DisagreeCount(wrong + 1)
            print(f"MISS {case.name} want={case.status} got={status} saying {says!r}; merges {merges}\n{out}")
    print(f"{len(CASES)} cases, {wrong} disagree")
    return ExitStatus(1 if wrong else 0)


if __name__ == "__main__":
    sys.exit(main())
