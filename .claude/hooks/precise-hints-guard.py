#!/home/nicolas/.local/bin/python3.14
"""Hold a Python type hint an edit writes to the repository's own gate, before it lands or at once after.

THE RULE (user, 2026-09-26, widened 2026-09-29): a hint is universally
quantified. `str` claims the code handles any string, `list[float]` any list of
floats, `dict[str, X]` any key; the code handles none of those. So no `Any` or
`object`, no `str`, `int`, `float` or `bytes` standing for a value with a
meaning, no hint nested three subscripts deep, and no alias that renames such
a shape. Prose is a `NewType` of its own.

WHY A HOOK AND NOT A NOTE. The rule was a memory, and new code still carried
`type _Segment = tuple[int, int, int, int]`, `list[str]` library names and
`str` path constants, while ruff, pylint and basedpyright passed every one.
The repository now holds the gate, tools/check_precise_hints.py, with its
record; this hook runs that same gate, so a lapse is refused while it is
written rather than found in review. One checker, two callers: the hook
carries no rule of its own.

BEFORE AN EDIT. For an Edit, a Write or a MultiEdit of a file in a git
repository that holds tools/check_precise_hints.py and python/.venv/bin/python,
the file's text after the edit is computed, written to a temporary file, and
handed to `python -m tools.check_precise_hints --file TMP --as REL` run in that
repository. The gate's exit 1 (a hint its record does not allow) or 2 (the
file or the record cannot be read) refuses the edit with the gate's own words;
exit 0 lets it through. An edit whose old text the file does not hold exactly
as the tool needs it passes, the tool refusing it itself.

AFTER A BASH CALL. A write through the shell cannot be read before it runs, so
each file the harness reports the call changed (the tool response's
bashEditDiff.changedFiles) is handed to the same gate where it stands. Where
the call changed more files than it lists (moreFiles), every file `git status`
shows modified or untracked in the call's repository is checked instead. The
write stands; a finding is sent back with exit 2, which shows it to the model
at once, so the hint is typed before anything is built on it.

A DIRECTORY WITH A RECORD OF ITS OWN. A repository without the gate, this kit's
own, is held to it wherever a directory above the file holds a
PYTHON_IMPRECISE_HINTS.yaml: the gate of the aletheia checkout ($ALETHEIA_REPO,
or ~/dev/agda/aletheia, as tests/check_hints.sh finds it) runs with --root that
directory and --record that file. A checkout whose gate refuses those arguments
judges nothing, and the hook says so rather than refusing.

Either way the gate decides what it reads; the hook only skips what cannot be
in its scope, a suffix other than .py and .sh, and a file no gate covers. An error of the hook's own is reported and the call goes ahead, as the
other guards here do: a hook that fails must never block the session silently.
"""

import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from enum import StrEnum
from typing import Literal, NamedTuple, NewType, TypedDict, cast

import _shell

# The tools whose calls the hook reads; a path as an event spells it, and one
# relative to its repository as the gate takes it; the text of a file; a count
# of files; prose the gate or the hook prints; and the hook's own answer: 0 lets
# the call through, 2 refuses it or reports a finding after it, 1 reports an
# error and lets it through.
type Editor = Literal["Edit", "Write", "MultiEdit"]
FilePath = NewType("FilePath", str)
RepoPath = NewType("RepoPath", str)
FileText = NewType("FileText", str)
FileCount = NewType("FileCount", int)
Prose = NewType("Prose", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)


class Replacement(TypedDict, total=False):
    """One replacement an Edit asks for, or one of a MultiEdit's."""

    old_string: FileText
    new_string: FileText
    replace_all: bool


class Request(Replacement, total=False):
    """What an Edit, a Write or a MultiEdit asks for."""

    file_path: FilePath
    content: FileText
    edits: list[Replacement]


class EditDiff(TypedDict, total=False):
    """The files a Bash call changed, as the harness reports them."""

    changedFiles: list[FilePath]
    moreFiles: FileCount


class BashResponse(TypedDict, total=False):
    """What the harness reports after a Bash call, as far as the hook reads it."""

    bashEditDiff: EditDiff


class Event(TypedDict, total=False):
    """The event the harness hands the hook."""

    hook_event_name: Literal["PreToolUse", "PostToolUse"]
    tool_name: Editor | Literal["Bash"]
    tool_input: Request
    tool_response: BashResponse
    cwd: FilePath


class Verdict(StrEnum):
    """What the gate's answer means: nothing to say, a hint its record refuses, or no answer."""

    CLEAN = "clean"
    FINDING = "finding"
    BROKEN = "broken"


class Gate(NamedTuple):
    """Where a file's hints are judged: the checkout that holds the gate, the tree the
    file's rows are relative to, and that tree's own record where it is not the checkout's."""

    checker: Path
    root: Path
    record: Path | None


class GateRun(NamedTuple):
    """What the gate answered, and what it printed."""

    verdict: Verdict
    report: Prose


GATE = Path("tools") / "check_precise_hints.py"
INTERPRETER = Path("python") / ".venv" / "bin" / "python"
# A directory holding this file is held to the gate under its own record.
RECORD = "PYTHON_IMPRECISE_HINTS.yaml"
# The checkout whose gate judges such a directory, as tests/check_hints.sh finds it.
CHECKER = Path(os.environ.get("ALETHEIA_REPO") or Path.home() / "dev" / "agda" / "aletheia")
EDITORS: frozenset[Editor] = frozenset({"Edit", "Write", "MultiEdit"})
SUFFIXES = frozenset({".py", ".sh"})
GATE_MEMORY = 2 << 30
GATE_SECONDS = 60
NAME_THE_TYPE = (
    "Name the type instead: a NewType, an enum or a Literal, a TypedDict, a NamedTuple\n"
    "or a dataclass; prose is Prose. A row in the gate's record needs the user's\n"
    "approval. The gate says:\n"
)
BEFORE = "precise-hints-guard: this edit leaves a type hint the repository's gate refuses\n" \
         "(tools/check_precise_hints.py, AGENTS/python.md cat 8). " + NAME_THE_TYPE
AFTER = "precise-hints-guard: this Bash call wrote a type hint the repository's gate refuses\n" \
        "(tools/check_precise_hints.py, AGENTS/python.md cat 8). The write stands, so type it\n" \
        "now. " + NAME_THE_TYPE


def repository_of(path: Path) -> Path | None:
    """Name the git work tree holding ``path``, or None outside any."""
    for parent in path.parents:
        if (parent / ".git").exists():
            return parent
    return None


def holds_gate(checkout: Path) -> bool:
    """Say whether a checkout holds the gate and the interpreter it runs under."""
    return (checkout / GATE).is_file() and os.access(checkout / INTERPRETER, os.X_OK)


def gate_for(path: Path) -> Gate | None:
    """Name where ``path`` is judged: its repository's own gate, or a directory's record above it."""
    repo = repository_of(path)
    if repo is None:
        return None
    if holds_gate(repo):
        return Gate(repo, repo, None)
    for parent in path.parents:
        if (parent / RECORD).is_file():
            return Gate(CHECKER, parent, parent / RECORD) if holds_gate(CHECKER) else None
        if parent == repo:
            return None
    return None


def edited_text(tool: Editor, request: Request, current: FileText | None) -> FileText | None:
    """Compute the file's text after the edit; None where the tool itself would refuse it."""
    if tool == "Write":
        content = request.get("content")
        return FileText(content) if isinstance(content, str) else None
    if current is None:
        return None
    edits = request.get("edits") if tool == "MultiEdit" else [request]
    if not isinstance(edits, list):
        return None
    text = current
    for edit in edits:
        if not isinstance(edit, dict):
            return None
        old, new, every = edit.get("old_string"), edit.get("new_string"), edit.get("replace_all")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return None
        found = text.count(old)
        if found == 0 or (found > 1 and not every):
            return None
        text = FileText(text.replace(old, new) if every else text.replace(old, new, 1))
    return text


def cap_gate_memory() -> None:
    """Bound the gate's address space: it reads whatever text the call brings."""
    resource.setrlimit(resource.RLIMIT_AS, (GATE_MEMORY, GATE_MEMORY))


def run_gate(gate: Gate, file: Path, path: Path) -> GateRun:
    """Hand one file's text to the gate, as the path it stands for under the gate's tree."""
    rel = RepoPath(path.relative_to(gate.root).as_posix())
    held = ["--root", str(gate.root), "--record", str(gate.record)] if gate.record is not None else []
    run = subprocess.run(
        [str(gate.checker / INTERPRETER), "-m", "tools.check_precise_hints", *held,
         "--file", str(file), "--as", rel],
        cwd=gate.checker, capture_output=True, text=True, timeout=GATE_SECONDS, check=False,
        preexec_fn=cap_gate_memory,
    )
    if "usage:" in run.stderr:  # a checkout whose gate does not take these arguments judges nothing
        return GateRun(Verdict.BROKEN, Prose(f"the gate in {gate.checker} refused its arguments:\n{run.stderr[-800:]}"))
    if run.returncode == 0:
        return GateRun(Verdict.CLEAN, Prose(run.stdout.rstrip()))
    if run.returncode in (1, 2):  # a hint out of step with the record, or a file it cannot read
        return GateRun(Verdict.FINDING, Prose(run.stdout.rstrip()))
    return GateRun(Verdict.BROKEN, Prose(f"the gate exited {run.returncode}:\n{(run.stderr or run.stdout)[-1500:]}"))


def before_edit(event: Event) -> ExitStatus:
    """Run the gate on the text an edit would leave; refuse the edit on a finding."""
    tool = event.get("tool_name")
    request = event.get("tool_input")
    if tool not in EDITORS or not isinstance(request, dict):
        return ALLOW
    raw = request.get("file_path")
    if not isinstance(raw, str) or not raw:
        return ALLOW
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()
    path = Path(os.path.abspath(os.path.join(cwd, raw)))
    gate = gate_for(path) if path.suffix in SUFFIXES else None
    if gate is None:
        return ALLOW
    current = FileText(path.read_text(encoding="utf-8")) if path.is_file() else None
    text = edited_text(cast("Editor", tool), request, current)
    if text is None:
        return ALLOW
    with tempfile.NamedTemporaryFile("w", suffix=path.suffix, encoding="utf-8") as staged:
        staged.write(text)
        staged.flush()
        run = run_gate(gate, Path(staged.name), path)
    if run.verdict is Verdict.BROKEN:
        print(f"precise-hints-guard: the edit goes ahead unchecked; {run.report}", file=sys.stderr)
        return REPORTED
    if run.verdict is Verdict.FINDING:
        print(BEFORE + run.report, file=sys.stderr)
        return REFUSE
    return ALLOW


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


def after_bash(event: Event) -> ExitStatus:
    """Run the gate on every file a Bash call changed; report a finding back at once."""
    response = event.get("tool_response")
    diff = response.get("bashEditDiff") if isinstance(response, dict) else None
    if not isinstance(diff, dict):
        return ALLOW
    listed = diff.get("changedFiles")
    changed = [Path(path) for path in listed if isinstance(path, str)] if isinstance(listed, list) else []
    more = diff.get("moreFiles")
    if isinstance(more, int) and more > 0:
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()
        here = repository_of(Path(cwd) / "call")
        changed += dirty_files(here) if here is not None else []
    reports: list[Prose] = []
    errors: list[Prose] = []
    for path in dict.fromkeys(changed):
        gate = gate_for(path) if path.suffix in SUFFIXES and path.is_file() else None
        if gate is None:
            continue
        run = run_gate(gate, path, path)
        if run.verdict is Verdict.BROKEN:
            errors.append(run.report)
        elif run.verdict is Verdict.FINDING:
            reports.append(run.report)
    if reports:
        print(AFTER + "\n".join(reports), file=sys.stderr)
        return REFUSE
    if errors:
        print("precise-hints-guard: the call's files went unchecked; " + "\n".join(errors), file=sys.stderr)
        return REPORTED
    return ALLOW


def main() -> ExitStatus:
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return ALLOW  # a hook that cannot read the event must not block the session
    if not isinstance(event, dict):
        return ALLOW
    try:
        name, tool = event.get("hook_event_name"), event.get("tool_name")
        if name in ("PreToolUse", None) and tool in EDITORS:
            return before_edit(cast("Event", event))
        if name == "PostToolUse" and tool == "Bash":
            return after_bash(cast("Event", event))
        return ALLOW
    except Exception as exc:  # noqa: BLE001 - an unguarded call is reported, never silent
        print(f"precise-hints-guard: internal error, the call goes ahead unchecked: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return REPORTED


if __name__ == "__main__":
    sys.exit(main())
