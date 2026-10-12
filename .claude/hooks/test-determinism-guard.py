#!/home/nicolas/.local/bin/python3.14
"""Hold every test the session reads or writes to the rule that it is deterministic.

THE RULE (user, 2026-10-02): the tests run on machines of very different power,
so every one of them is fully deterministic, in every language. A test reads no
clock and waits on no duration: no sleep, no timer, no deadline or timeout that
decides an outcome. It starts no thread, goroutine or concurrent task. Where the
code needs time or concurrency, the clock or the scheduler is injected and the
test drives it step by step. A larger timeout, a retry, a ceiling or a tolerance
argued from how loaded a machine was is never a fix; statistical framing is out
everywhere, mutation lanes included.

WHY A HOOK AND NOT A NOTE. The rule was a memory, and tests still gained
two-second deadlines and goroutine races, and a mutation lane a tuned timeout,
while every lint passed. This hook makes the rule arrive whenever a test is
read or written, and runs the repository's own gate,
tools/check_test_determinism.py, on every test written, so a new time or thread
site is reported at once. One checker, two callers: the gate says which files
are tests and what a site is; the hook carries no rule of its own.

READING A TEST. After a Read of a file, or a Bash call whose command names an
existing file or directory by a path spelled as one (with a separator or a
suffix), the gate is asked which of those paths are tests (`--is-test`); for
any, the rule is added to the model's context.

WRITING A TEST. After an Edit, a Write or a MultiEdit, or a Bash call that
changed files (the tool response's bashEditDiff, and every file `git status`
shows where the call changed more than it lists), each test file among them is
handed to the gate where it stands (`--file PATH --as REL`). A site the record
does not allow is sent back with exit 2, which shows it to the model at once;
otherwise the rule is added to the model's context.

Only a repository holding the gate and python/.venv/bin/python is in scope. An
error of the hook's own is reported and the call goes ahead, as the other
guards here do: a hook that fails must never block the session silently.
"""

import json
import os
import re
import resource
import subprocess
import sys
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, cast

import _shell

# A path as an event spells it; one relative to its repository as the gate
# takes it; an argument handed to the gate; a shell command line; a count of
# files; prose the gate or the hook prints; the hook's answer: 0 lets the call
# through (with context, where printed), 2 reports a finding, 1 reports an
# error of the hook's own.
FilePath = NewType("FilePath", str)
RepoPath = NewType("RepoPath", str)
GateArgument = NewType("GateArgument", str)
CommandLine = NewType("CommandLine", str)
FileCount = NewType("FileCount", int)
Prose = NewType("Prose", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)
# The gate's own answers: clean or a path classified a test, a site or a path
# out of step, a file or the record it cannot read.
GATE_CLEAN, GATE_OUT_OF_STEP = ExitStatus(0), ExitStatus(1)

type Tool = Literal["Read", "Edit", "Write", "MultiEdit", "Bash"]


class Request(TypedDict, total=False):
    """The part of a tool's input the hook reads."""

    file_path: FilePath
    command: CommandLine


class EditDiff(TypedDict, total=False):
    """The files a Bash call changed, as the harness reports them."""

    changedFiles: list[FilePath]
    moreFiles: FileCount


class BashResponse(TypedDict, total=False):
    """What the harness reports after a Bash call, as far as the hook reads it."""

    bashEditDiff: EditDiff


class Event(TypedDict, total=False):
    """The event the harness hands the hook."""

    hook_event_name: Literal["PostToolUse"]
    tool_name: Tool
    tool_input: Request
    tool_response: BashResponse
    cwd: FilePath


class Named(NamedTuple):
    """A path the call touched, in the repository whose gate judges it."""

    repo: Path
    rel: RepoPath
    path: Path


class GateRun(NamedTuple):
    """What the gate answered, and what it printed to each stream."""

    status: ExitStatus
    out: Prose
    err: Prose


GATE = Path("tools") / "check_test_determinism.py"
INTERPRETER = Path("python") / ".venv" / "bin" / "python"
EDITORS = frozenset({"Edit", "Write", "MultiEdit"})
GATE_MEMORY = 2 << 30
GATE_SECONDS = 60
# What a command line may name as a path: a run of path characters. The gate,
# not this pattern, decides which of them are tests.
PATH_TOKEN = re.compile(r"[\w./@+-]+")
# A token counts as a path only when it is spelled as one, with a separator or
# a suffix: a bare word such as `python` or `go` names a command or a module as
# often as the directory of the same name.
PATH_LIKE = re.compile(r"\w/\w|\w\.\w+$")
RULE = (
    "test-determinism-guard: {paths} {verb} a test. Every test is fully deterministic, "
    "because the tests run on machines of very different power (AGENTS.md, Universal "
    "Rules): it reads no clock and waits on no duration (no sleep, timer, deadline or "
    "timeout deciding an outcome) and starts no thread, goroutine or concurrent task. "
    "Inject the clock or the scheduler and drive it step by step. A larger timeout, a "
    "retry, a ceiling or a tolerance argued from machine load is never a fix, in any "
    "language or mutation lane."
)
FINDING = (
    "test-determinism-guard: this call left a test using physical time or a thread the\n"
    "repository's record does not allow (tools/check_test_determinism.py, AGENTS.md,\n"
    "Universal Rules). The write stands, so rewrite it now: inject the clock or the\n"
    "scheduler and drive it. A row in the record needs the user's approval. The gate says:\n"
)


def repository_of(path: Path) -> Path | None:
    """Name the git work tree holding ``path``, or None outside any."""
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def holds_gate(repo: Path) -> bool:
    """Say whether a repository holds the gate and the interpreter it runs under."""
    return (repo / GATE).is_file() and os.access(repo / INTERPRETER, os.X_OK)


def named(path: Path) -> Named | None:
    """Place a path in the repository whose gate judges it, or None out of scope."""
    repo = repository_of(path)
    if repo is None or not holds_gate(repo) or path == repo:
        return None
    return Named(repo, RepoPath(path.relative_to(repo).as_posix()), path)


def cap_gate_memory() -> None:
    """Bound the gate's address space: it reads whatever text the call brings."""
    resource.setrlimit(resource.RLIMIT_AS, (GATE_MEMORY, GATE_MEMORY))


def gate(repo: Path, *arguments: GateArgument) -> GateRun:
    """Run the gate in its repository, memory-bound and timed out."""
    run = subprocess.run(
        [str(repo / INTERPRETER), "-m", "tools.check_test_determinism", *arguments],
        cwd=repo, capture_output=True, text=True, timeout=GATE_SECONDS, check=False,
        preexec_fn=cap_gate_memory,
    )
    return GateRun(ExitStatus(run.returncode), Prose(run.stdout), Prose(run.stderr))


def broken(run: GateRun) -> RuntimeError:
    """Describe an answer the gate should never give."""
    return RuntimeError(f"the gate exited {run.status}: {(run.err or run.out)[-800:]}")


def tests_of(paths: list[Named]) -> list[Named]:
    """Ask each repository's gate which of its paths are tests."""
    by_repo: dict[Path, dict[RepoPath, Named]] = {}
    for each in paths:
        by_repo.setdefault(each.repo, {})[each.rel] = each
    found: list[Named] = []
    for repo, rels in by_repo.items():
        run = gate(repo, GateArgument("--is-test"), *map(GateArgument, rels))
        if run.status not in (GATE_CLEAN, GATE_OUT_OF_STEP):
            raise broken(run)
        found += [rels[RepoPath(line)] for line in run.out.splitlines() if line in rels]
    return found


def absolute(cwd: FilePath, raw: FilePath) -> Path:
    """Resolve a path an event spells against the call's working directory."""
    return Path(os.path.abspath(os.path.join(cwd, raw)))


def dirty_files(repo: Path) -> list[Path]:
    """List every file ``git status`` shows modified or untracked in a repository."""
    listed = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True, text=True, check=False, timeout=GATE_SECONDS,
    ).stdout.split("\0")
    files: list[Path] = []
    entries = iter(listed)
    for entry in entries:
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            next(entries, None)  # a rename or a copy lists its old path next
        files.append(repo / entry[3:])
    return files


def written_by(event: Event, cwd: FilePath) -> list[Path]:
    """Name the files the call wrote: an editor's one, or the files a Bash call changed."""
    tool, request = event.get("tool_name"), event.get("tool_input")
    if tool in EDITORS and isinstance(request, dict):
        raw = request.get("file_path")
        return [absolute(cwd, raw)] if isinstance(raw, str) and raw else []
    response = event.get("tool_response")
    diff = response.get("bashEditDiff") if isinstance(response, dict) else None
    if not isinstance(diff, dict):
        return []
    listed = diff.get("changedFiles")
    changed = [Path(p) for p in listed if isinstance(p, str)] if isinstance(listed, list) else []
    more = diff.get("moreFiles")
    if isinstance(more, int) and more > 0:
        here = repository_of(Path(cwd))
        changed += dirty_files(here) if here is not None else []
    return list(dict.fromkeys(changed))


def read_by(event: Event, cwd: FilePath) -> list[Path]:
    """Name the paths the call read: a Read's file, or every existing path a command names."""
    tool, request = event.get("tool_name"), event.get("tool_input")
    if not isinstance(request, dict):
        return []
    if tool == "Read":
        raw = request.get("file_path")
        return [absolute(cwd, raw)] if isinstance(raw, str) and raw else []
    command = request.get("command")
    if tool != "Bash" or not isinstance(command, str):
        return []
    tokens = [token for token in PATH_TOKEN.findall(command) if PATH_LIKE.search(token)]
    paths = [absolute(cwd, FilePath(token)) for token in tokens]
    return [path for path in dict.fromkeys(paths) if path.exists()]


def judge_writes(tests: list[Named]) -> list[Prose]:
    """Hand each written test to the gate where it stands; return what it refused."""
    findings: list[Prose] = []
    for each in tests:
        if not each.path.is_file():
            continue
        run = gate(each.repo, GateArgument("--file"), GateArgument(str(each.path)),
                   GateArgument("--as"), GateArgument(each.rel))
        if run.status == GATE_OUT_OF_STEP:
            findings.append(Prose(run.out.rstrip()))
        elif run.status != GATE_CLEAN:
            raise broken(run)
    return findings


def context(text: Prose) -> None:
    """Add text to the model's context after the tool result."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": text}}))


def after_tool(event: Event) -> ExitStatus:
    """Report a written test's new sites, or remind the rule for a test read or written."""
    raw_cwd = event.get("cwd")
    cwd = FilePath(raw_cwd) if isinstance(raw_cwd, str) else FilePath(os.getcwd())
    writes = [n for n in map(named, written_by(event, cwd)) if n is not None]
    reads = [n for n in map(named, read_by(event, cwd)) if n is not None]
    written = tests_of(writes) if writes else []
    findings = judge_writes(written)
    if findings:
        print(FINDING + "\n".join(findings), file=sys.stderr)
        return REFUSE
    touched = list(dict.fromkeys([*written, *(tests_of(reads) if reads else [])]))
    if touched:
        names = ", ".join(each.rel for each in touched)
        context(Prose(RULE.format(paths=names, verb="is" if len(touched) == 1 else "are each")))
    return ALLOW


def main() -> ExitStatus:
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return ALLOW  # a hook that cannot read the event must not block the session
    if not isinstance(event, dict) or event.get("hook_event_name") not in ("PostToolUse", None):
        return ALLOW
    try:
        return after_tool(cast("Event", event))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"test-determinism-guard: the call went unchecked: {exc}", file=sys.stderr)
        return REPORTED


if __name__ == "__main__":
    sys.exit(main())
