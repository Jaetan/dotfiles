#!/usr/bin/env python3
"""Regression cases for merge-dribble-guard.py.

Every case builds a git work tree of its own under a fresh temporary directory, writes the dribble the case
gives (and the script it runs, when the case gives one), and puts a stand-in `gh` first on PATH that answers
`gh pr view N --json state,mergeStateStatus` from the case's table (or fails, or prints junk) and logs each call.
It then pipes a PostToolUse event to the hook and compares the answer with the rule: exit 2 reports a merge other
than through pr-all-green after errexit, or one GitHub would refuse now, exit 1 a merge state that could not be
read, exit 0 lets the call through. A case names a line the hook must print and may name one it must not.

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


# A dribble's text; the words after a pr-all-green's name; a case's name; prose a case names itself by or
# expects; an event as the hook reads it; the stand-in gh's table, as JSON; a pull request's number (from 1); how
# many times the stand-in was called; the hook's exit status.
FileText = NewType("FileText", str)
Args = NewType("Args", str)
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
# The head every pr-all-green merge of a case names, the line a dribble turns errexit on with, the script a
# dispatching dribble execs, and the file a `gh api graphql` reads its query from.
SHA = "0123456789abcdef0123456789abcdef01234567"
ERREXIT = FileText("set -euo pipefail\n")
SCRIPT = ".commands-to-run.d/x-dribble.sh"
ASIDE = "query.graphql"
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
    """A dribble, the stand-in's table, and the answer the rule wants: its exit status, a text the hook prints, a
    text it must not print (none when empty), the script the dribble execs and the query file it names (each none
    when None)."""

    id: CaseId
    what: Prose
    dribble: FileText | None
    answers: Answers
    want: ExitStatus
    says: Prose
    lacks: Prose = Prose("")
    script: FileText | None = None
    aside: FileText | None = None


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
    if case.script is not None:
        (repo / SCRIPT).parent.mkdir()
        _ = (repo / SCRIPT).write_text(case.script, encoding="utf-8")
        (repo / SCRIPT).chmod(0o755)
    if case.aside is not None:
        _ = (repo / ASIDE).write_text(case.aside, encoding="utf-8")
    return hook(repo, case.answers)


def merge(number: PullNumber, *flags: MergeFlag) -> FileText:
    """A dribble whose one merge is `gh pr merge NUMBER FLAGS...`."""
    return FileText(f"#!/usr/bin/env bash\nset -euo pipefail\ngh pr merge {number} {' '.join(flags)}\n")


def after(before: FileText, args: Args) -> FileText:
    """A dribble running BEFORE, then `pr-all-green ARGS`."""
    return FileText(f"#!/usr/bin/env bash\n{before}pr-all-green {args}\n")


def verified(number: PullNumber, before: FileText = ERREXIT) -> FileText:
    """A dribble whose one merge is `pr-all-green NUMBER --head SHA --squash-with-body body.txt`, after BEFORE."""
    return after(before, Args(f"{number} --head {SHA} --squash-with-body body.txt"))


def cases() -> list[Case]:
    """Every case of the rule."""
    behind = table({5: ("OPEN", "BEHIND")})
    clean = table({5: ("OPEN", "CLEAN")})
    through = Prose("merge through `pr-all-green 5 --head SHA --squash-with-body FILE` after `set -e`")
    unnumbered = Prose("merge through `pr-all-green N --head SHA --squash-with-body FILE`")
    errexit_off = Prose("runs with errexit off")
    no_head = Prose("names no --head")
    set_e = FileText("set -e\n")
    merging = 'mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'
    arming = FileText('{"query": "mutation { enablePullRequestAutoMerge(input: {}) { clientMutationId } }"}\n')
    run = f"/opt/kit/pr-all-green 5 --head {SHA} --squash-with-body body.txt"

    return [
        Case(CaseId("M01"), Prose("a gh merge of a CLEAN pull request is refused, naming pr-all-green"),
             merge(5, "--squash"), clean, REFUSE, through),
        Case(CaseId("M02"), Prose("a gh merge of a BEHIND pull request gets update-branch, a watch, a merge dribble"),
             merge(5, "--squash"), behind, REFUSE,
             Prose("watch it to GREEN (`pr-checks-watch 5`), then hand over a merge dribble running "
                   "`pr-all-green 5 --head SHA")),
        Case(CaseId("M03"), Prose("a gh merge armed with --auto on a continuation line is refused"),
             FileText("gh pr merge 5 --squash \\\n  --auto\n"), clean, REFUSE, through),
        Case(CaseId("M04"), Prose("a gh merge after the dribble's own update-branch is refused"),
             FileText("gh api -X PUT repos/o/r/pulls/5/update-branch\ngh pr merge 5 --squash\n"), clean,
             REFUSE, through),
        Case(CaseId("M05"), Prose("a MERGED pull request is refused"), verified(5),
             table({5: ("MERGED", "UNKNOWN")}), REFUSE, Prose("MERGED")),
        Case(CaseId("M06"), Prose("HAS_HOOKS is mergeable"), verified(5), table({5: ("OPEN", "HAS_HOOKS")}), ALLOW,
             Prose("")),
        Case(CaseId("M07"), Prose("BLOCKED is refused"), verified(5), table({5: ("OPEN", "BLOCKED")}), REFUSE,
             Prose("BLOCKED")),
        Case(CaseId("M08"), Prose("DIRTY is refused"), verified(5), table({5: ("OPEN", "DIRTY")}), REFUSE,
             Prose("DIRTY")),
        Case(CaseId("M09"), Prose("UNSTABLE is refused"), verified(5), table({5: ("OPEN", "UNSTABLE")}), REFUSE,
             Prose("UNSTABLE")),
        Case(CaseId("M10"), Prose("UNKNOWN is refused"), verified(5), table({5: ("OPEN", "UNKNOWN")}), REFUSE,
             Prose("UNKNOWN")),
        Case(CaseId("M11"), Prose("of two merges, the BEHIND one is named"),
             after(FileText(f"set -e\npr-all-green 5 --head {SHA} --squash-with-body body.txt\n"),
                   Args(f"6 --head {SHA} --squash-with-body body.txt")),
             table({5: ("OPEN", "CLEAN"), 6: ("OPEN", "BEHIND")}), REFUSE, Prose("#6 is BEHIND")),
        Case(CaseId("M12"), Prose("a gh merge spelling its number through a variable is refused"),
             FileText('gh pr merge "$m" --squash\n'), behind, REFUSE, unnumbered),
        Case(CaseId("M13"), Prose("a gh that fails is reported and goes through"), verified(5), table({5: "fail"}),
             REPORTED, Prose("gh pr view 5 failed: could not resolve")),
        Case(CaseId("M14"), Prose("a gh that prints junk is reported"), verified(5), table({5: "junk"}), REPORTED,
             Prose("printed no JSON")),
        Case(CaseId("M15"), Prose("a merge named inside a heredoc body is text"),
             FileText("cat <<'EOF'\ngh pr merge 5 --squash\nEOF\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M16"), Prose("no dribble, nothing to read"), None, behind, ALLOW, Prose("")),
        Case(CaseId("M17"), Prose("a dribble that merges nothing"), FileText("git status\n"), behind, ALLOW,
             Prose("")),
        Case(CaseId("M18"), Prose("an option's value is not read as the pull request"),
             FileText('gh pr merge --subject "fix: x" -R o/r 5 --squash\n'), behind, REFUSE, Prose("#5 is BEHIND")),
        Case(CaseId("M19"), Prose("a refusal beside an unreadable one still refuses"),
             after(FileText(f"set -e\npr-all-green 5 --head {SHA} --squash-with-body body.txt\n"),
                   Args(f"6 --head {SHA} --squash-with-body body.txt")),
             table({5: ("OPEN", "BEHIND"), 6: "fail"}), REFUSE, Prose("could not be read")),
        Case(CaseId("M20"), Prose("a state outside GitHub's set is reported"), verified(5),
             table({5: ("OPEN", "SOMETHING")}), REPORTED, Prose("answered state")),
        Case(CaseId("M21"), Prose("a gh merge with no number is refused"),
             FileText("gh pr merge --squash\n"), behind, REFUSE, unnumbered),
        Case(CaseId("M22"), Prose("another program's `pr merge` is not gh's"), FileText("hub pr merge 5\n"),
             behind, ALLOW, Prose("")),
        Case(CaseId("M23"), Prose("a `gh pr view` merges nothing"), FileText("gh pr view 5\n"), behind, ALLOW,
             Prose("")),
        Case(CaseId("M24"), Prose("a flag before the number is not the pull request"),
             FileText("gh pr merge --squash 5\n"), behind, REFUSE, Prose("#5 is BEHIND")),
        Case(CaseId("M25"), Prose("a word that only begins with digits is no number"),
             FileText("gh pr merge 5x --squash\n"), behind, REFUSE, unnumbered),
        Case(CaseId("M27"), Prose("a pull request state outside GitHub's set is reported"), verified(5),
             table({5: ("ELSEWHERE", "CLEAN")}), REPORTED, Prose("answered state")),
        Case(CaseId("M28"), Prose("a pr-all-green merge is asked its merge state"), verified(5), behind, REFUSE,
             Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M29"), Prose("a pr-all-green that only reads merges nothing"),
             FileText(f"pr-all-green 5 --head {SHA}\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M30"), Prose("options read with `=`"),
             after(set_e, Args(f"5 --head={SHA} --squash-with-body=body.txt")), behind, REFUSE,
             Prose("#5 is BEHIND"), no_head),
        Case(CaseId("M31"), Prose("a prefix naming one option alone is read as that option"),
             after(set_e, Args(f"5 --hea {SHA} --squash body.txt")), behind, REFUSE, Prose("#5 is BEHIND"), no_head),
        Case(CaseId("M32"), Prose("a prefix naming two options names none"),
             after(set_e, Args(f"5 --h {SHA} --squash-with-body body.txt")), clean, REFUSE, no_head),
        Case(CaseId("M33"), Prose("a pull request spelled through a variable is a finding"),
             after(set_e, Args(f'"$n" --head {SHA} --squash-with-body body.txt')), clean, REFUSE,
             Prose("spell the number")),
        Case(CaseId("M34"), Prose("a head spelled through a variable is a finding"),
             after(set_e, Args('5 --head "$h" --squash-with-body body.txt')), clean, REFUSE, no_head),
        Case(CaseId("M35"), Prose("a head of 41 characters is no hash"),
             after(set_e, Args(f"5 --head {SHA}0 --squash-with-body body.txt")), clean, REFUSE, no_head),
        Case(CaseId("M36"), Prose("an uppercase head is no hash"),
             after(set_e, Args(f"5 --head {SHA.upper()} --squash-with-body body.txt")), clean, REFUSE, no_head),
        Case(CaseId("M37"), Prose("the word after `--` is the pull request"),
             after(set_e, Args(f"--head {SHA} --squash-with-body body.txt -- 5")), behind, REFUSE,
             Prose("#5 is BEHIND"), Prose("spell the number")),
        Case(CaseId("M38"), Prose("the values of --repo and --logs are not the pull request"),
             after(set_e, Args(f"--repo o/r --logs logs 5 --head {SHA} --squash-with-body body.txt")), behind,
             REFUSE, Prose("#5 is BEHIND"), Prose("spell the number")),
        Case(CaseId("M79"), Prose("the values of --ledger and --stops are not the pull request"),
             after(set_e, Args(f"--ledger l.yaml --stops .github/workflows/x.yml 5 --head {SHA} --squash-with-body body.txt")),
             behind, REFUSE, Prose("#5 is BEHIND"), Prose("spell the number")),
        Case(CaseId("M39"), Prose("a pr-all-green merge with no `set` before it is a finding"),
             verified(5, FileText("")), clean, REFUSE, errexit_off),
        Case(CaseId("M40"), Prose("a `set -e` after the merge does not count"),
             FileText(f"pr-all-green 5 --head {SHA} --squash-with-body body.txt\nset -e\n"), clean, REFUSE,
             errexit_off),
        Case(CaseId("M41"), Prose("`set -o errexit` turns errexit on"), verified(5, FileText("set -o errexit\n")),
             behind, REFUSE, Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M42"), Prose("`set +e` turns errexit off, not on"), verified(5, FileText("set +e\n")), clean,
             REFUSE, errexit_off),
        Case(CaseId("M43"), Prose("an option after `-o pipefail` is read"),
             verified(5, FileText("set -o pipefail -e\n")), behind, REFUSE, Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M44"), Prose("`set -- -e` sets a positional parameter, not errexit"),
             verified(5, FileText("set -- -e\n")), clean, REFUSE, errexit_off),
        Case(CaseId("M45"), Prose("an `echo -e` is no `set`"), verified(5, FileText("echo -e x\n")), clean, REFUSE,
             errexit_off),
        Case(CaseId("M46"), Prose("an `exec` that only redirects, before the `set -e` and the merge, is read past"),
             verified(5, FileText("exec > >(tee log) 2>&1\nset -e\n")), behind, REFUSE, Prose("#5 is BEHIND"),
             errexit_off),
        Case(CaseId("M47"), Prose("the merge in the script the dribble execs is read, after that script's `set -e`"),
             FileText(f"#!/usr/bin/env bash\nexec {SCRIPT}\n"), behind, REFUSE, Prose("#5 is BEHIND"), errexit_off,
             verified(5)),
        Case(CaseId("M48"), Prose("the merge in the script the dribble execs, with no `set -e`, is a finding"),
             FileText(f"#!/usr/bin/env bash\nexec {SCRIPT}\n"), clean, REFUSE, errexit_off, Prose(""),
             verified(5, FileText(""))),
        Case(CaseId("M49"), Prose("a `set +e` after the `set -e` turns errexit off"),
             verified(5, FileText("set -e\nset +e\n")), clean, REFUSE, errexit_off),
        Case(CaseId("M50"), Prose("a `set +o errexit` after the `set -e` turns errexit off"),
             verified(5, FileText("set -e\nset +o errexit\n")), clean, REFUSE, errexit_off),
        Case(CaseId("M51"), Prose("of two options of one `set`, the last decides"),
             verified(5, FileText("set -e +e\n")), clean, REFUSE, errexit_off),
        Case(CaseId("M52"), Prose("a `set -e` after a `set +e` turns errexit on"),
             verified(5, FileText("set +e\nset -e\n")), behind, REFUSE, Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M53"), Prose("a `set +u` leaves errexit as it was"),
             verified(5, FileText("set -eu\nset +u\n")), behind, REFUSE, Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M54"), Prose("a `set +o pipefail` leaves errexit as it was"),
             verified(5, FileText("set -e\nset +o pipefail\n")), behind, REFUSE, Prose("#5 is BEHIND"),
             errexit_off),
        Case(CaseId("M55"), Prose("a dispatcher's `set -e` before it execs the script does not reach the script"),
             FileText(f"#!/usr/bin/env bash\nset -euo pipefail\nexec {SCRIPT}\n"), clean, REFUSE, errexit_off,
             Prose(""), verified(5, FileText(""))),
        Case(CaseId("M56"), Prose("a `set -e` in a subshell does not reach the merge"),
             verified(5, FileText("( set -e )\n")), clean, REFUSE, errexit_off),
        Case(CaseId("M57"), Prose("a `set +e` in a subshell leaves the merge's shell as it was"),
             verified(5, FileText("set -e\n( set +e )\n")), behind, REFUSE, Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M58"), Prose("a `set -e` in a group runs in the merge's shell"),
             verified(5, FileText("{ set -e; }\n")), behind, REFUSE, Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M59"), Prose("a `bash -c` text's own `set -e` comes before its merge"),
             FileText(f"bash -c 'set -e; pr-all-green 5 --head {SHA} --squash-with-body body.txt'\n"), behind,
             REFUSE, Prose("#5 is BEHIND"), errexit_off),
        Case(CaseId("M60"), Prose("a `gh api` PUT on the merge path is refused, naming pr-all-green"),
             FileText("gh api -X PUT repos/o/r/pulls/5/merge -f merge_method=squash\n"), behind, REFUSE, through),
        Case(CaseId("M61"), Prose("the merge path with the repository spelled in gh's placeholders is refused"),
             FileText("gh api --method=PUT 'repos/{owner}/{repo}/pulls/5/merge'\n"), clean, REFUSE, through),
        Case(CaseId("M62"), Prose("the merge path through variables is refused"),
             FileText('gh api -X PUT "repos/$repo/pulls/$n/merge"\n'), clean, REFUSE, unnumbered),
        Case(CaseId("M63"), Prose("a graphql query naming mergePullRequest is refused"),
             FileText(f"gh api graphql -f query='{merging}'\n"), clean, REFUSE, unnumbered),
        Case(CaseId("M64"), Prose("a graphql query read from a `@` file naming enablePullRequestAutoMerge is refused"),
             FileText(f"gh api graphql -F query=@{ASIDE}\n"), clean, REFUSE, unnumbered, aside=arming),
        Case(CaseId("M65"), Prose("a graphql body read from the file `--input` names is refused"),
             FileText(f"gh api graphql --input {ASIDE}\n"), clean, REFUSE, unnumbered, aside=arming),
        Case(CaseId("M66"), Prose("a graphql body read from the file `--input=` names is refused"),
             FileText(f"gh api graphql --input={ASIDE}\n"), clean, REFUSE, unnumbered, aside=arming),
        Case(CaseId("M67"), Prose("a graphql body read from a heredoc is refused"),
             FileText(f"gh api graphql --input - <<'EOF'\n{arming}EOF\n"), clean, REFUSE, unnumbered),
        Case(CaseId("M68"), Prose("a graphql body piped in from an echo is refused"),
             FileText(f"echo '{arming.strip()}' | gh api graphql --input -\n"), clean, REFUSE, unnumbered),
        Case(CaseId("M69"), Prose("a `gh api` reading a pull request merges nothing"),
             FileText("gh api repos/o/r/pulls/5\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M70"), Prose("a graphql query reading a merge state merges nothing"),
             FileText("gh api graphql -f query='query { repository(owner: \"o\", name: \"r\") "
                      "{ pullRequest(number: 5) { mergeStateStatus } } }'\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M71"), Prose("a REST call whose body names the mutation merges nothing"),
             FileText("gh api repos/o/r/issues/5/comments -f body='mergePullRequest is refused'\n"), behind, ALLOW,
             Prose("")),
        Case(CaseId("M72"), Prose("a gh command other than api naming the merge path merges nothing"),
             FileText("gh pr comment 5 --body 'see repos/o/r/pulls/5/merge'\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M73"), Prose("pr-all-green run by a Python interpreter is the same merge"),
             FileText(f"set -euo pipefail\npython3.14 {run}\n"), behind, REFUSE, Prose("#5 is BEHIND"),
             errexit_off),
        Case(CaseId("M74"), Prose("through env, past the interpreter's `-X` and its value, the head is read"),
             FileText('set -e\nenv python3.14 -X dev /opt/kit/pr-all-green 5 --head "$h" --squash-with-body b\n'),
             clean, REFUSE, no_head),
        Case(CaseId("M75"), Prose("through exec, past the interpreter's `-u`, the number is read"),
             FileText(f'set -e\nexec python3.14 -u pr-all-green "$n" --head {SHA} --squash-with-body b\n'), clean,
             REFUSE, Prose("spell the number")),
        Case(CaseId("M76"), Prose("pr-all-green run by an interpreter with no `set` before it is a finding"),
             FileText(f"python3.14 {run}\n"), clean, REFUSE, errexit_off),
        Case(CaseId("M77"), Prose("an interpreter run with options alone merges nothing"),
             FileText("python3.14 -V\n"), behind, ALLOW, Prose("")),
        Case(CaseId("M78"), Prose("a `set -e` before a merge put in a subshell counts for nothing"),
             verified(5, FileText("set -e\n( ")).replace("body.txt\n", "body.txt )\n"), clean, REFUSE,
             errexit_off),
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
            ok = outcome.status == case.want and case.says in outcome.stderr and not (
                case.lacks and case.lacks in outcome.stderr)
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
