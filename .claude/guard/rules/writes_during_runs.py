"""A write into a repository waits for the heavy run live there, so a long run never reads a tree that changes under it.

THE RULE (user, 2026-09-25, kept blunt 2026-11-09). A Bash or Monitor call is refused where a path it writes lies in
the git work tree of a live heavy run. The paths a call writes are those dispatcher/writes.py's writes() names for the
commands of its call model; a path the call does not make known, and an output or errors end it does not make known,
count as a write into the work tree of the call's own directory. A live heavy run is a process the proc tree of the
session state's places shows whose command line, read by the call model, runs a command heavy() (dispatcher/heavy.py)
calls heavy under the declaration of the process's directory; its work tree is the one that declaration's lookup
(declarations/lookup.py) finds. The refusal names each run and each write; what passes is waiting for the run's
completion notification, the edit drafted in the scratchpad meanwhile.

EXCEPTIONS, each with the failure it holds:
  * a git command writes the repository of its folder, its directory with each -C value placed in turn, unless its
    subcommand, the first word past git's global options, is status, diff, show, log, ls-files, ls-tree, grep,
    rev-parse, cat-file, blame or describe, or is branch with no operand (a word not led by -) past it (user,
    2026-11-09): `git add` writes the index a run reads, no writer row names git, and a subcommand the call does not
    make known may write;
  * a process whose command line the call model cannot read, a heavy row's name in its words, is a live heavy run: a
    run is never missed for words the reader refuses.
"""

from collections.abc import Iterator, Sequence
from pathlib import Path
from shlex import join
from typing import TYPE_CHECKING, NamedTuple

from declarations.lookup import start_of
from dispatcher.core import Event, HookEvent, ToolName, state_places
from dispatcher.heavy import KIT_ROWS, base, heavy
from dispatcher.model import Command, Word, build
from dispatcher.tree import ShellText
from dispatcher.verdict import Prose, Unreadable, Verdict, refuse
from dispatcher.words import Directory
from dispatcher.writes import FilePath, write_of, writes
from state.session import Pid

if TYPE_CHECKING:
    from declarations.lookup import Facts

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
GIT = ShellText("git")
READS = frozenset(ShellText(name) for name in ("status", "diff", "show", "log", "ls-files", "ls-tree", "grep",
                                                "rev-parse", "cat-file", "blame", "describe"))
BRANCH = ShellText("branch")
CUT = 120
ADVICE = """this call writes into the git work tree of a live heavy run, which must never read a tree that changes
under it.

{problems}

Wait for the run's completion notification, then make the edit; draft it in the scratchpad meanwhile."""


class Target(NamedTuple):
    """A place a command writes: how a refusal names it, and its absolute path, None where the call does not make it
    known."""

    named: Prose
    path: FilePath | None


class Run(NamedTuple):
    """A live heavy run: its pid, its command line cut short, and its work tree."""

    pid: Pid
    line: Prose
    tree: Path | None


class Git(NamedTuple):
    """A git command: the folder it works in, None where the call does not make it known, and its words from its
    subcommand on."""

    folder: FilePath | None
    words: tuple[Word, ...]

    @property
    def reads(self) -> bool:
        """Whether the command only reads its repository: its subcommand is one READS names, or branch with no
        operand, a word not led by -, past it."""
        if not self.words:
            return False
        subcommand, *rest = self.words
        return subcommand.value in READS or (
            subcommand.value == BRANCH and all((word.value or "").startswith("-") for word in rest))


def git_of(command: Command) -> Git:
    """The git ``command``: its folder, its directory with each -C value before its subcommand placed in turn, and its
    words from its subcommand on, the first word past the global options, -C and -c taking their values."""
    folder = None if command.cwd is None else FilePath(command.cwd)
    rest = iter(command.words[1:])
    for word in rest:
        if word.value in ("-C", "-c"):
            given = next(rest, None)
            if word.value == "-C":
                place = None if folder is None else Directory(folder)
                folder = None if given is None else write_of(given, "edited", place).path
        elif not (word.value or "").startswith("-"):
            return Git(folder, (word, *rest))
    return Git(folder, ())


def targets(command: Command) -> Iterator[Target]:
    """Each place ``command`` writes: the paths writes() names, an output or errors end the call does not make known,
    and the repository of a git command that does not only read."""
    for write in writes(command):
        unknown = "" if write.path is not None else ", a path the call does not make known"
        yield Target(Prose(f"`{write.word.spelled}` ({write.kind}{unknown})"), write.path)
    if "unknown" in (command.streams.output.reach, command.streams.errors.reach):
        yield Target(Prose(f"an output end of `{command.text}` the call does not make known"), None)
    if command.name is not None and base(command.name) == GIT:
        git = git_of(command)
        if not git.reads:
            yield Target(Prose(f"the repository `{command.text}` writes"), git.folder)


def tree(event: Event, path: FilePath | None) -> Path | None:
    """The work tree holding ``path``, the call's own directory where the call does not make it known; None outside
    every tree."""
    found = event.declarations_of().tree_of(start_of(Path(path or event.cwd or "/")))
    return None if found is None else found.work


def names(event: Event, trees: Sequence[Path]) -> frozenset[ShellText]:
    """The names a heavy row of the kit or of the declarations of ``trees`` runs by: a program, an argv's first word,
    a module's last part, a script's base name."""
    rows = (*KIT_ROWS, *(row for folder in trees for row in event.declaration(folder).project.heavy))
    return frozenset(ShellText(name) for row in rows for name in (
        row.program, row.argv[0] if row.argv else None, row.module and row.module.rsplit(".", 1)[-1],
        row.script and base(row.script)) if name)


def heavy_line(event: Event, words: Sequence[ShellText], cwd: Path, facts: Facts) -> bool:
    """Whether the command line ``words``, run in ``cwd``, runs a command heavy() calls heavy under ``facts``, or is
    one the call model cannot read."""
    try:
        call = build(ShellText(join(words)), Directory(str(cwd)), event.reading)
    except Unreadable:
        return True
    return any(heavy(command, facts) is not None for command in call.commands)


def runs(event: Event, trees: Sequence[Path]) -> Iterator[Run]:
    """Each live heavy run the proc tree shows whose directory lies in one of ``trees`` and whose words hold a heavy
    row's name, both checked before the call model reads its command line."""
    wanted = names(event, trees)
    for entry in state_places(event.places).proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw, cwd = (entry / "cmdline").read_bytes(), (entry / "cwd").readlink()
        except OSError:  # ended, or not the hook's user's to read
            continue
        words = [ShellText(text) for text in raw.decode(errors="replace").split("\0") if text]
        if not (any(cwd.is_relative_to(folder) for folder in trees)
                and any(name in base(word) for word in words for name in wanted)):
            continue
        facts = event.declaration(cwd)
        if heavy_line(event, words, cwd, facts):
            line = " ".join(words)
            yield Run(Pid(int(entry.name)), Prose(line if len(line) <= CUT else f"{line[:CUT]}..."), facts.work_root)


def judge(event: Event) -> Verdict | None:
    """Refuse a Bash or Monitor call that writes into the git work tree of a live heavy run, naming each run and each
    write."""
    placed = [(target, at) for command in event.call().commands for target in dict.fromkeys(targets(command))
              if (at := tree(event, target.path)) is not None]
    trees = list(dict.fromkeys(at for _, at in placed))
    live = list(runs(event, trees)) if trees else []
    found = [Prose(f"  * {target.named} lies in {at}, where pid {run.pid} runs `{run.line}`")
             for target, at in placed for run in live if run.tree == at]
    return refuse(Prose(ADVICE.format(problems="\n".join(found)))) if found else None
