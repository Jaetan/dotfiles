#!/home/nicolas/.local/bin/python3.14
"""Hold a merge dribble to merges through pr-all-green, of pull requests GitHub would merge now.

THE RULE (user, 2026-10-10 and 2026-11-04): a dribble merges a pull request only through
`pr-all-green N --head SHA --squash-with-body FILE`, which reads every check of SHA past its conclusion and merges
only when every one is green, run in a shell whose errexit is on, so that its refusal stops what follows. Every
other merge is refused whatever its flags: a `gh pr merge` (`--auto` squashes once the required contexts pass, the
coverage floors and the other lanes unread), a `gh api` on a pull request's `pulls/N/merge` path, and a
`gh api graphql` whose query names `mergePullRequest` or `enablePullRequestAutoMerge`. The pull request must also be
one GitHub would merge now: `mergeable: MERGEABLE` says only that the branch has no conflict, while a branch behind
its base is MERGEABLE and BEHIND, and a ruleset that wants branches current refuses to merge it.

WHY A HOOK AND NOT A NOTE. The rule was a memory, reached through an index that was not opened; the readiness gate
that was applied listed CI and review threads only, and the merge dribble for #370 went out BEHIND and failed in
the user's hands.

WHEN IT LOOKS. After every Bash, Edit, Write or MultiEdit call whose working directory is in a git work tree
holding a .commands-to-run.sh at its root that changed since the hook last read it (its mtime and size, kept in
the work tree's git directory). An unchanged dribble costs no network call.

WHAT IT READS. The dribble as the shell reads it (`_commands.expand`): a script it runs through `exec` or `bash`
is read in place, heredoc bodies are text, continuation lines join, and `if`, `&&`, `||` and `!` read as one
sequence. A `gh pr merge` is a finding, and so is a `gh api` with a word holding `pulls/N/merge`, whatever its
method and however it spells the repository, or a `gh api graphql` whose query text names either mutation: its
words, what it reads on stdin (a heredoc, a `<` file, an echo, printf or cat piped in) and each file a
`KEY=@PATH` field or `--input` names. Every `pr-all-green` carrying `--squash-with-body` is a merge, run by its
name past the wrappers `_commands.unwrap` peels (env, exec, taskset, timeout and the rest) or as the script of a
Python interpreter past its options (`-W` and `-X` taking a value). Its options are read as its argparse reads them
(`--name VALUE`, `--name=VALUE`, a prefix naming one option alone); its pull request number and its `--head` must
be literals (a number, a 40-character lowercase hex hash), and errexit must be on where it runs: on as the last
`set` touching it in the merge's own shell leaves it, off before any. `-e` within a cluster of short options
(`-eu`, `-euo pipefail`) or `-o errexit` turns it on, `+e` or `+o errexit` off, each in its order up to the first
word that is no option. The merge's own shell is the `_shell` scope of its command: a group, an `if` body, a
function called and a sourced file share it; a subshell, a substitution, a pipeline element, a `bash -c` text and
each script run by its path or through `exec` are each a shell of their own, so a dispatcher's `set -e` before it
execs the script counts for nothing in the script, and neither does a `set -e` before a merge put in a subshell
or a pipeline (refused as errexit off, though the subshell inherits it). For every merge whose number reads,
`gh pr view N --json state,mergeStateStatus`.

WHAT IT DOES NOT HOLD. Control flow: since `if`, `&&`, `||` and `!` read as one sequence, a `|| true` after the
merge, a merge in a branch, and a `set` in a branch not taken are read as if each ran in turn. A query file the
dribble writes before the `gh api` reads it is not on disk when the hook reads the dribble, so it goes unread.

WHAT IT SAYS. Exit 2 naming each finding: a merge gh runs gets the pr-all-green form, a pull request that is not
open with a merge state of CLEAN or HAS_HOOKS its state, and BEHIND the route of GitHub's update-branch, a watch
to GREEN and a merge dribble. The dribble stands and the message reaches the model at once, and each version of
the dribble is read once. A gh that cannot answer (no network, no login) is reported (exit 1) and the call goes
ahead, as an error of the hook's own is.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, NewType, TypedDict, cast, get_args

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _commands as C  # noqa: E402
import _shell  # noqa: E402


@dataclass(frozen=True)
class Gt[T]:
    """``Annotated`` metadata: the annotated value is greater than ``bound``."""

    bound: T


# A pull request's number: GitHub numbers from 1. A path as the harness's event spells it, turned into a Path at
# once: any path the harness sends, so the name is all a type can add. The dribble's text, shell source the shell
# reader parses at once. A word of a command as the shell reader hands it. A commit hash. Prose is what the hook
# prints. The text a `gh api` sends: a word, its stdin or a file it names. The dribble's version (mtime and size)
# the hook last read. The shell a command runs in, as the shell reader numbers it. The hook's answer: 0 lets the
# call through, 1 reports an error of its own or gh's, 2 reports a finding.
PullNumber = Annotated[int, Gt(0)]
FilePath = NewType("FilePath", str)
CommandLine = NewType("CommandLine", str)
Word = NewType("Word", str)
Sha = NewType("Sha", str)
Prose = NewType("Prose", str)
QueryText = NewType("QueryText", str)
Version = NewType("Version", str)
Scope = NewType("Scope", int)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)
# GitHub's states, as `gh pr view --json` spells them (the GraphQL PullRequestState and MergeStateStatus enums).
type PullState = Literal["OPEN", "CLOSED", "MERGED"]
type MergeState = Literal["BEHIND", "BLOCKED", "CLEAN", "DIRTY", "DRAFT", "HAS_HOOKS", "UNKNOWN", "UNSTABLE"]
type Tool = Literal["Read", "Edit", "Write", "MultiEdit", "Bash"]
# The program a merge runs, and pr-all-green's long options as its argparse defines them.
type Route = Literal["gh", "pr-all-green"]
type VerifierOption = Literal["--head", "--logs", "--squash-with-body", "--repo", "--ledger", "--stops", "--help"]
READY: frozenset[MergeState] = frozenset({"CLEAN", "HAS_HOOKS"})
DRIBBLE = ".commands-to-run.sh"
VERIFIER: Route = "pr-all-green"
_NUMBER = re.compile(r"[1-9][0-9]*")
_HEAD = re.compile(r"[0-9a-f]{40}")
# The options of `gh pr merge` that take a value, so the value is not read as the pull request.
_WITH_VALUE = frozenset({"--subject", "-t", "--body", "-b", "--body-file", "-F", "--match-head-commit",
                         "--author-email", "-A", "--repo", "-R"})
# Held to pr-all-green's own parser by tests/check_verifier_options.py.
TAKES_VALUE: frozenset[VerifierOption] = frozenset({"--head", "--logs", "--squash-with-body", "--repo", "--ledger",
                                                    "--stops"})
# A cluster of short options to `set`, turning them on (`-`) or off (`+`).
_CLUSTER = re.compile(r"[-+][A-Za-z]+")
# The REST path that merges a pull request, and the GraphQL mutations that merge one or arm its auto-merge.
_API_MERGE = re.compile(r"pulls/([^/]+)/merge")
_MUTATIONS = ("mergePullRequest", "enablePullRequestAutoMerge")


class Event(TypedDict, total=False):
    """The event the harness hands the hook."""

    hook_event_name: Literal["PostToolUse"]
    tool_name: Tool
    cwd: FilePath


class State(TypedDict):
    """What the hook keeps between calls, in the work tree's git directory."""

    version: Version


class Merge(NamedTuple):
    """A merge the dribble runs: the program it goes through, the pull request's number (None when the word naming
    it is no number), the command as the dribble spells it, and for pr-all-green its `--head` (None when it is no
    literal hash) and whether a `set` before it turned errexit on."""

    route: Route
    number: PullNumber | None
    spelled: Prose
    head: Sha | None
    errexit: bool


class Status(NamedTuple):
    """GitHub's answer for one pull request."""

    state: PullState
    merge_state: MergeState


def repository_of(path: Path) -> Path | None:
    """The root of the git work tree holding ``path``, or None outside one."""
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         check=False)
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def git_dir(repo: Path) -> Path:
    """The work tree's git directory, where the hook keeps its state."""
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"], capture_output=True,
                         text=True, check=True)
    return Path(out.stdout.strip())


def pull_number(word: Word) -> PullNumber | None:
    """The pull request a word names when it is a number from 1 spelled plainly, else None."""
    return int(word) if _NUMBER.fullmatch(word) else None


def spelled(argv: list[Word]) -> Prose:
    """The command as a message names it: its program's name and its words."""
    return Prose(" ".join([C.prog(argv[0]), *argv[1:]]))


def errexit_after(argv: list[Word], before: bool) -> bool:
    """errexit as the command leaves it, ``before`` being its state before: a `set` turns it on with `-e` within a
    cluster of short options (`-e`, `-eu`, `-euo pipefail`) or `-o errexit` and off with `+e` or `+o errexit`, the
    last of them deciding; any other command leaves it. Its options end at the first word that is no cluster
    (`--`, `-`, a positional parameter), and an `o` in a cluster takes the next word as the name of its option."""
    if C.prog(argv[0]) != "set":
        return before
    state, args, at = before, argv[1:], 0
    while at < len(args) and _CLUSTER.fullmatch(args[at]):
        cluster, name = args[at], args[at + 1] if at + 1 < len(args) else ""
        if "e" in cluster or ("o" in cluster and name == "errexit"):
            state = cluster.startswith("-")
        at += 2 if "o" in cluster else 1
    return state


def verifier_words(argv: list[Word]) -> list[Word] | None:
    """The words of a pr-all-green run from its name on: the command itself when it names pr-all-green, or the
    script a Python interpreter runs and the words after it when that script is pr-all-green, past the
    interpreter's options as `_commands` reads them (a value after `-W` or `-X`); None for any other command."""
    at = 0
    if C.is_python(argv[0]):
        at = 1
        while at < len(argv) and argv[at].startswith("-"):
            at += 2 if argv[at] in C._INTERP_VALUES["python"] else 1
    return argv[at:] if at < len(argv) and C.prog(argv[at]) == VERIFIER else None


def verifier_option(word: Word) -> VerifierOption | None:
    """The pr-all-green option a word names as its argparse reads a long option: the option or a prefix naming it
    alone, a value after `=` set apart. None for any other word, an ambiguous prefix (`--h`) included."""
    name = word.partition("=")[0]
    named = [option for option in get_args(VerifierOption.__value__) if option.startswith(name)]
    return cast("VerifierOption", named[0]) if len(named) == 1 else None


def verifier_merge(argv: list[Word], errexit: bool) -> Merge | None:
    """A pr-all-green that merges, one carrying `--squash-with-body`, read as its argparse reads it; None for one
    that only reads. Its pull request is its first positional word, and a repeated option keeps its last value."""
    given: dict[VerifierOption, Word] = {}
    positional: list[Word] = []
    waiting: VerifierOption | None = None
    for word in argv[1:]:
        option = verifier_option(word)
        if waiting is not None:
            given[waiting], waiting = word, None
        elif option in TAKES_VALUE and "=" in word:
            given[option] = Word(word.partition("=")[2])
        elif option in TAKES_VALUE:
            waiting = option
        elif not word.startswith("-"):
            positional.append(word)
    if "--squash-with-body" not in given:
        return None
    head = given.get("--head", Word(""))
    return Merge(VERIFIER, pull_number(positional[0]) if positional else None, spelled(argv),
                 Sha(head) if _HEAD.fullmatch(head) else None, errexit)


def query_texts(cmd: _shell.Command, words: list[Word], prev: _shell.Command | None) -> list[QueryText]:
    """What a `gh api` sends: its words, what it reads on stdin as `_commands` reads it (a heredoc, a `<` file, an
    echo, printf or cat piped in from ``prev``), and each file a `KEY=@PATH` field or `--input` names, where it can
    be read."""
    paths = [word.partition("=@")[2] for word in words if "=@" in word]
    paths += [after for word, after in zip(words, words[1:], strict=False) if word == "--input"]
    paths += [word.removeprefix("--input=") for word in words if word.startswith("--input=")]
    sent = [C._stdin_text(cmd, prev, {}), *(C.read_regular(C.resolve(path, cmd.cwd)) for path in paths)]
    return [*cast("list[QueryText]", words), *(QueryText(text) for text in sent if text is not None)]


def gh_merge(cmd: _shell.Command, argv: list[Word], prev: _shell.Command | None) -> Merge | None:
    """A merge gh runs: a `gh pr merge`, a `gh api` with a word holding a `pulls/N/merge` path, or a
    `gh api graphql` whose query text names a mutation that merges; None for any other gh command."""
    rest = argv[1:]
    if rest[:2] == ["pr", "merge"]:
        target, skip = Word(""), False  # the first word neither an option nor an option's value
        for word in rest[2:]:
            if skip:
                skip = False
            elif word in _WITH_VALUE:
                skip = True
            elif not word.startswith("-"):
                target = word
                break
        return Merge("gh", pull_number(target), spelled(argv), None, False)
    if rest[:1] != ["api"]:
        return None
    path = next((found for found in map(_API_MERGE.search, rest) if found), None)
    if path:
        return Merge("gh", pull_number(Word(path.group(1))), spelled(argv), None, False)
    if "graphql" in rest and any(name in text for text in query_texts(cmd, rest, prev) for name in _MUTATIONS):
        return Merge("gh", None, spelled(argv), None, False)
    return None


def merges_of(text: CommandLine, cwd: Path) -> list[Merge]:
    """Every merge the dribble runs, in the shell reader's order: each one gh runs, and each pr-all-green carrying
    `--squash-with-body` with whether errexit is on in its shell, as the last `set` touching it there left it."""
    merges: list[Merge] = []
    errexit: dict[Scope, bool] = {}
    prev: _shell.Command | None = None
    for inv in C.expand(text, str(cwd)):
        argv = cast("list[Word]", inv.core.argv)
        cmd, piped_from, prev = inv.cmd, prev, inv.cmd
        if not argv:
            continue
        shell = Scope(cmd.scope)
        errexit[shell] = errexit_after(argv, errexit.get(shell, False))
        run = verifier_words(argv)
        if run is not None:
            verified = verifier_merge(run, errexit[shell])
            if verified:
                merges.append(verified)
        elif C.prog(argv[0]) == "gh":
            found = gh_merge(cmd, argv, piped_from)
            if found:
                merges.append(found)
    return merges


def through(number: PullNumber | None) -> Prose:
    """The one way a dribble merges the pull request."""
    return Prose(f"`pr-all-green {number or 'N'} --head SHA --squash-with-body FILE` after `set -e`")


def spelling_findings(merge: Merge) -> list[Prose]:
    """What is wrong with the merge as the dribble spells it, before GitHub is asked."""
    if merge.route == "gh":
        return [Prose(f"`{merge.spelled}` merges without reading every check, `--auto` included: merge through "
                      f"{through(merge.number)}, which merges only when every check of SHA is green")]
    problems: list[Prose] = []
    if merge.number is None:
        problems.append(Prose(f"`{merge.spelled}` names no pull request number this hook can read; spell the number"))
    if merge.head is None:
        problems.append(Prose(f"`{merge.spelled}` names no --head this hook can read; spell the 40-character "
                              "lowercase hash"))
    if not merge.errexit:
        problems.append(Prose(f"`{merge.spelled}` runs with errexit off: put `set -euo pipefail` before it in the "
                              "shell that runs it, after any `set +e`, so that its refusal stops what follows"))
    return problems


def status(number: PullNumber) -> Status | Prose:
    """GitHub's state and merge state for the pull request, or why gh could not say."""
    out = subprocess.run(["gh", "pr", "view", str(number), "--json", "state,mergeStateStatus"], capture_output=True,
                         text=True, check=False, timeout=60)
    if out.returncode != 0:
        return Prose(f"gh pr view {number} failed: {(out.stderr.strip().splitlines() or ['no output'])[-1]}")
    try:
        answer = json.loads(out.stdout)
    except ValueError:
        return Prose(f"gh pr view {number} printed no JSON")
    state, merge_state = answer.get("state"), answer.get("mergeStateStatus")
    if state not in get_args(PullState.__value__) or merge_state not in get_args(MergeState.__value__):
        return Prose(f"gh pr view {number} answered state {state!r}, mergeStateStatus {merge_state!r}")
    return Status(cast("PullState", state), cast("MergeState", merge_state))


def finding(merge: Merge, answer: Status) -> Prose | None:
    """What is wrong with merging now, or None when GitHub would merge it."""
    n = merge.number
    if answer.state != "OPEN":
        return Prose(f"#{n} is {answer.state}: there is nothing to merge")
    if answer.merge_state in READY:
        return None
    if answer.merge_state == "BEHIND":
        return Prose(f"#{n} is BEHIND its base: hand it to GitHub's update-branch "
                     f"(gh api -X PUT repos/OWNER/REPO/pulls/{n}/update-branch), check the new head is signed, "
                     f"watch it to GREEN (`pr-checks-watch {n}`), then hand over a merge dribble running "
                     f"{through(n)}")
    return Prose(f"#{n} has merge state {answer.merge_state}, not CLEAN: GitHub would refuse the merge")


def load_state(path: Path) -> State:
    """The state kept from the last call, or a fresh one."""
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    return State(version=Version(str(kept.get("version", ""))))


def after_tool(event: Event) -> ExitStatus:
    """Read a changed dribble's merges, hold each to pr-all-green, and ask GitHub whether each would land now."""
    raw_cwd = event.get("cwd")
    cwd = Path(raw_cwd) if isinstance(raw_cwd, str) else Path.cwd()
    repo = repository_of(cwd)
    if repo is None or not (repo / DRIBBLE).is_file():
        return ALLOW
    dribble = repo / DRIBBLE
    stat = dribble.stat()
    version = Version(f"{stat.st_mtime_ns}:{stat.st_size}")
    where = git_dir(repo) / "merge-dribble-guard.json"
    state = load_state(where)
    if state["version"] == version:
        return ALLOW
    state["version"] = version
    text = CommandLine(dribble.read_text(encoding="utf-8", errors="replace"))
    problems: list[Prose] = []
    unanswered: list[Prose] = []
    for merge in merges_of(text, repo):
        problems += spelling_findings(merge)
        if merge.number is None:
            continue
        answer = status(merge.number)
        if isinstance(answer, Status):
            problem = finding(merge, answer)
            if problem:
                problems.append(problem)
        else:
            unanswered.append(answer)
    _ = where.write_text(json.dumps(state), encoding="utf-8")
    if unanswered:
        print("merge-dribble-guard: the merge state could not be read, so it went unchecked:\n  "
              + "\n  ".join(unanswered), file=sys.stderr)
    if problems:
        print(f"merge-dribble-guard: {DRIBBLE} merges a pull request other than through pr-all-green, or one "
              "GitHub would refuse now:\n  "
              + "\n  ".join(problems)
              + "\nThe dribble stands; rewrite it before handing it over. mergeable: MERGEABLE says only that "
              "there is no conflict; the merge state must read CLEAN.", file=sys.stderr)
        return REFUSE
    return REPORTED if unanswered else ALLOW


def main() -> ExitStatus:
    """Read the event and check the dribble; an error of the hook's own lets the call go ahead, reported."""
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return ALLOW  # a hook that cannot read its event must not block the session
    if not isinstance(event, dict) or event.get("hook_event_name") not in ("PostToolUse", None):
        return ALLOW
    if event.get("tool_name") not in ("Bash", "Edit", "Write", "MultiEdit"):
        return ALLOW
    try:
        return after_tool(cast("Event", event))
    except (OSError, RuntimeError, subprocess.SubprocessError, _shell.ParseError, _shell.BudgetExceeded) as exc:
        print(f"merge-dribble-guard: the call went unchecked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return REPORTED


if __name__ == "__main__":
    sys.exit(main())
