#!/home/nicolas/.local/bin/python3.15
"""Hold a merge dribble to pull requests GitHub would merge now.

THE RULE (user, 2026-10-10): a dribble that merges a pull request goes to the user only when the pull request is
merge-ready, and merge-ready includes GitHub's merge state. `mergeable: MERGEABLE` says only that the branch has
no conflict: a branch behind its base is MERGEABLE and BEHIND, and a ruleset that wants branches current refuses
to merge it.

WHY A HOOK AND NOT A NOTE. The rule was a memory, reached through an index that was not opened; the readiness gate
that was applied listed CI and review threads only, and the merge dribble for #370 went out BEHIND and failed in
the user's hands.

WHEN IT LOOKS. After every Bash, Edit, Write or MultiEdit call whose working directory is in a git work tree
holding a .commands-to-run.sh at its root that changed since the hook last read it (its mtime and size, kept in
the work tree's git directory). An unchanged dribble costs no network call.

WHAT IT READS. Every `gh pr merge N` the dribble runs, read as the shell reads it (`_commands.expand`): heredoc
bodies are text, continuation lines join. A merge armed with `--auto` waits for the checks by design and is left
alone, and so is one whose number the dribble hands to GitHub's update-branch first
(`gh api ... pulls/N/update-branch`). A number the dribble spells through a variable cannot be checked and is a
finding. For each other pull request, `gh pr view N --json state,mergeStateStatus`.

WHAT IT SAYS. Exit 2 naming each pull request that is not open with a merge state of CLEAN or HAS_HOOKS: BEHIND
gets the update-branch route, anything else its state. The dribble stands and the message reaches the model at
once, and each version of the dribble is read once. A gh that cannot answer (no network, no login) is reported
(exit 1) and the call goes ahead, as an error of the hook's own is.
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
# reader parses at once. Prose is what the hook prints. The dribble's version (mtime and size) the hook last
# read. The hook's answer: 0 lets the call through, 1 reports an error of its own or gh's, 2 reports a finding.
PullNumber = Annotated[int, Gt(0)]
FilePath = NewType("FilePath", str)
CommandLine = NewType("CommandLine", str)
Prose = NewType("Prose", str)
Version = NewType("Version", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)
# GitHub's states, as `gh pr view --json` spells them (the GraphQL PullRequestState and MergeStateStatus enums).
type PullState = Literal["OPEN", "CLOSED", "MERGED"]
type MergeState = Literal["BEHIND", "BLOCKED", "CLEAN", "DIRTY", "DRAFT", "HAS_HOOKS", "UNKNOWN", "UNSTABLE"]
type Tool = Literal["Read", "Edit", "Write", "MultiEdit", "Bash"]
READY: frozenset[MergeState] = frozenset({"CLEAN", "HAS_HOOKS"})
DRIBBLE = ".commands-to-run.sh"
_NUMBER = re.compile(r"[1-9][0-9]*")
# The options of `gh pr merge` that take a value, so the value is not read as the pull request.
_WITH_VALUE = frozenset({"--subject", "-t", "--body", "-b", "--body-file", "-F", "--match-head-commit",
                         "--author-email", "-A", "--repo", "-R"})
_UPDATE_BRANCH = re.compile(r"(?:^|/)pulls/([1-9][0-9]*)/update-branch$")


class Event(TypedDict, total=False):
    """The event the harness hands the hook."""

    hook_event_name: Literal["PostToolUse"]
    tool_name: Tool
    cwd: FilePath


class State(TypedDict):
    """What the hook keeps between calls, in the work tree's git directory."""

    version: Version


class Merge(NamedTuple):
    """A merge the dribble runs: the pull request's number, or the word that names it when it is not a number."""

    number: PullNumber | None
    spelled: Prose
    auto: bool


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


def merges_and_updates(text: CommandLine, cwd: Path) -> tuple[list[Merge], frozenset[PullNumber]]:
    """Every `gh pr merge` the dribble runs, and the numbers it hands to GitHub's update-branch."""
    merges: list[Merge] = []
    updated: set[PullNumber] = set()
    for inv in C.expand(text, str(cwd)):
        argv = inv.core.argv
        if not argv or C.prog(argv[0]) != "gh":
            continue
        rest = argv[1:]
        if rest[:2] == ["pr", "merge"]:
            target, skip = "", False  # the first word neither an option nor an option's value
            for word in rest[2:]:
                if skip:
                    skip = False
                elif word in _WITH_VALUE:
                    skip = True
                elif not word.startswith("-"):
                    target = word
                    break
            number = int(target) if _NUMBER.fullmatch(target) else None
            merges.append(Merge(number, Prose(target), "--auto" in rest))
        elif rest[:1] == ["api"]:
            for word in rest[1:]:
                found = _UPDATE_BRANCH.search(word)
                if found:
                    updated.add(int(found[1]))
    return merges, frozenset(updated)


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
                     f"and arm `gh pr merge {n} --squash --auto`")
    return Prose(f"#{n} has merge state {answer.merge_state}, not CLEAN: GitHub would refuse the merge")


def load_state(path: Path) -> State:
    """The state kept from the last call, or a fresh one."""
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    return State(version=Version(str(kept.get("version", ""))))


def after_tool(event: Event) -> ExitStatus:
    """Read a changed dribble's merges and ask GitHub whether each would land now."""
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
    merges, updated = merges_and_updates(text, repo)
    problems: list[Prose] = []
    unanswered: list[Prose] = []
    for merge in merges:
        if merge.auto or merge.number in updated:
            continue
        if merge.number is None:
            problems.append(Prose(f"`gh pr merge {merge.spelled}` names no pull request number this hook can read; "
                                  "spell the number"))
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
        print(f"merge-dribble-guard: {DRIBBLE} merges a pull request GitHub would refuse now:\n  "
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
