"""A commit dribble waits until every entry point it commits has run since its last edit.

THE RULE (user, 2026-10-02, made blunt 2026-11-09). What an entry point prints, its summary, its refusals and its help,
is a claim about what the code does, and an edit can make it false without touching it: an installer once shipped
printing the behaviour its change had just replaced, past tests, probes and a review, since nobody had run it. So
before each Bash call, each entry point a command of it runs is kept in the session state (state/session.py) with its
sha256, the last run of each path alone: a file the shell runs by a path, which it runs only where it is executable;
the script of a Python interpreter, or its -m module, the folder's NAME/PARTS.py or NAME/PARTS/__main__.py; the script
operand of bash, sh or dash; each where its folder is known. After each Bash, Edit, Write or
MultiEdit call whose work tree holds a pending commit dribble, a `.commands-to-run.sh` at its root newer than HEAD's
commit that runs `git commit` or `git commit-tree` when read as `bash DRIBBLE` runs it, the call is refused naming
each changed entry point whose sha256 no kept run has, with the form that runs it. A changed file is one git status
shows modified, added or untracked, a deletion none; an entry point is a `.py` file holding an
`if __name__ == "__main__":` line, or an executable file whose first line is a shebang.

EXCEPTIONS: none.
"""

import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Final, NewType

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import CARRIERS, Command
from dispatcher.scripts import operand
from dispatcher.tree import ShellText
from dispatcher.verdict import KitFault, Prose, Verdict, refuse
from dispatcher.words import Directory
from rules._dribble import model
from rules.bytecode_plants import run_of
from rules.job_lifetime import base
from rules.pr_watch import tree

GitArgument = NewType("GitArgument", str)
GitOutput = NewType("GitOutput", str)
FileBytes = NewType("FileBytes", bytes)
Digest = NewType("Digest", str)

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse", "PostToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Edit"), ToolName("Write"), ToolName("MultiEdit")})
BASH = ToolName("Bash")
KIND: Final = "notice"
IDENT = "entry-point:"
DRIBBLE = ".commands-to-run.sh"
GIT = ShellText("git")
COMMITS = frozenset({ShellText("commit"), ShellText("commit-tree")})
PY = ".py"
SHEBANG = b"#!"
MAIN_GUARD = re.compile(rb"""^if __name__ == ['"]__main__['"]\s*:""", re.MULTILINE)
HEAD_TIME = tuple(map(GitArgument, ("log", "-1", "--format=%ct")))
STATUS = tuple(map(GitArgument, ("status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")))
GIT_SECONDS = 30.0
ADVICE = """the dribble {dribble} commits entry points no run has read since their last edit:

{entries}

Run each after its last edit, in the form named, by its path, through python or through bash, sh or dash, and read
its whole output: what an entry point prints is a claim about what the code does, and an edit can make it false."""


def at(cwd: Directory | None, value: ShellText | None) -> Path | None:
    """The file ``value`` names from ``cwd``, resolved; None where either is not known."""
    return None if value is None or cwd is None else Path(os.path.realpath(os.path.join(cwd, value)))


def runs(command: Command) -> list[Path]:
    """The files ``command`` runs as the module docstring names them, each resolved from its folder."""
    values = [word.value for word in command.words]
    first = at(command.cwd, command.name) if command.name is not None and "/" in command.name else None
    found = [first] if first is not None and os.access(first, os.X_OK) else []
    run = run_of(command)
    if run is not None and run.kind == "script":
        found.append(at(command.cwd, run.value))
    if run is not None and run.kind == "module" and run.value is not None:
        stem = run.value.replace(".", "/")
        found += [at(command.cwd, ShellText(stem + PY)), at(command.cwd, ShellText(stem + "/__main__" + PY))]
    index = operand(values) if base(command.words) in CARRIERS else None
    if index is not None:
        found.append(at(command.cwd, values[index]))
    return [path for path in found if path is not None and path.is_file()]


def entry(path: Path) -> FileBytes | None:
    """The bytes of the file ``path`` where it is an entry point, as the module docstring says; None where not."""
    with path.open("rb") as handle:
        data = handle.read(len(SHEBANG))
        shebang = data == SHEBANG and os.access(path, os.X_OK)
        if path.suffix != PY and not shebang:
            return None
        data += handle.read()
    return FileBytes(data) if shebang or MAIN_GUARD.search(data) else None


def git(root: Path, arguments: tuple[GitArgument, ...]) -> GitOutput | None:
    """What git prints for ``arguments`` in the work tree ``root``, None where it fails."""
    done = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, timeout=GIT_SECONDS, check=False)
    return GitOutput(done.stdout.decode("utf-8", "surrogateescape")) if done.returncode == 0 else None


def pending(root: Path, event: Event) -> Path | None:
    """The dribble of the work tree ``root`` where it is a pending commit dribble, as the module docstring says."""
    dribble = root / DRIBBLE
    if not (root / ".git").exists() or not dribble.is_file():
        return None
    head = git(root, HEAD_TIME)
    if head is not None and dribble.stat().st_mtime <= int(head):
        return None
    call = model(dribble, event)
    commands = call.commands if call is not None else ()
    commits = any(base(command.words) == GIT and COMMITS & {word.value for word in command.words[1:]}
                  for command in commands)
    return dribble if commits else None


def changed(root: Path) -> list[Path]:
    """Each file of the work tree ``root`` git status shows modified, added or untracked and on disk, resolved."""
    listed = git(root, STATUS)
    if listed is None:
        raise KitFault(Prose(f"git status cannot read the work tree {root}"))
    return [Path(os.path.realpath(root / item[3:])) for item in listed.split("\0") if (root / item[3:]).is_file()]


def digest(data: FileBytes) -> Digest:
    """The sha256 of ``data``, in hex."""
    return Digest(hashlib.sha256(data).hexdigest())


def form(path: Path) -> ShellText:
    """The command that runs the entry point ``path``: its path where it is executable, python3 given it where not."""
    quoted = shlex.quote(str(path))
    return ShellText(quoted if os.access(path, os.X_OK) else f"python3 {quoted}")


def judge(event: Event) -> Verdict | None:
    """Keep each entry point a Bash call runs before it runs; after a call, refuse each changed entry point of the
    work tree's pending commit dribble that no kept run has at its sha256."""
    if event.name == "PreToolUse":
        runs_kept = [path for command in event.call().commands for path in runs(command)] if event.tool == BASH else []
        kept = [(path, data) for path in runs_kept if (data := entry(path)) is not None]
        if kept:
            from state.session import FieldName, FieldValue, RecordId  # imported for a run this rule keeps

            session = event.state()
            for path, data in kept:
                session.record(KIND, RecordId(IDENT + str(path)), {FieldName("sha256"): FieldValue(digest(data))})
        return None
    if event.cwd is None:
        return None
    root = tree(Path(event.cwd))
    dribble = pending(root, event)
    if dribble is None:
        return None
    from state.session import FieldName  # imported for an event this rule judges

    seen = {(record.ident, record.fields.get(FieldName("sha256"))) for record in event.state().records(KIND)}
    unrun = [path for path in changed(root)
             if (data := entry(path)) is not None and (IDENT + str(path), digest(data)) not in seen]
    entries = "\n".join(f"  * {path}, run as `{form(path)}`" for path in unrun)
    return refuse(Prose(ADVICE.format(dribble=dribble, entries=entries))) if unrun else None
