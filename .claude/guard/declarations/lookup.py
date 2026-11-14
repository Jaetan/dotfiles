"""The declaration that holds for a path: its repository's `.claude/guard.toml` and the machine's file, read once.

Declarations(...).for_path(PATH) gives the Facts for an absolute PATH: those of the repository PATH lies in, and the
machine's, read when first asked, so a path's project facts never meet a fault of the machine file. The repository is
found from PATH's nearest folder that exists, symbolic links resolved, or from the folder holding a `.git` that PATH
lies inside: where no `.git` stands in that folder or above it, PATH is outside every repository and gets the machine's
facts alone; otherwise git names the work tree and the common git dir. The declaration is the work tree's own
`.claude/guard.toml`, else, in a linked worktree, the main work tree's, the parent of a common dir named `.git`; a
nested repository is a work tree of its own; a work tree with neither file gets the machine's facts alone. Records and
the probe record resolve against the main work tree's root, a gate's cwd and as_from, bulk folders, heavy scripts and
unread scripts against the work tree PATH is in, so a file of a linked worktree is held to that tree's own gate, and
`~/` against the home folder.

A hook event holds one Declarations: it reads the machine file once and each declaration once, on first ask, and asks
git once per folder. A file that cannot be read, is not UTF-8 TOML or breaks the schema (schema.py) is a KitFault,
which the dispatcher turns into a refusal, and so is a machine without its file, the fault naming the file, the
tables it must declare and the switch of the whole kit; a repository without its file declares nothing of its own.
"""

import os
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from dispatcher.verdict import KitFault, Prose

from declarations.schema import (
    MACHINE_TABLES,
    GateEntry,
    Heavy,
    Machine,
    PathText,
    ProjectEntries,
    Protocol,
    Suffix,
    TomlTable,
    Word,
    anchor_of,
    at_home,
    machine_facts,
    project_entries,
)

DECLARATION = Path(".claude") / "guard.toml"
MACHINE_FILE = Path(".config") / "claude-guard" / "machine.toml"
KIT_SWITCH = Path(".config") / "claude-guard" / "off" / "all"
EXAMPLE = Path(__file__).resolve().parent / "examples" / "machine.toml"
GIT_TIMEOUT = 10.0
CLEARED = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES",
                     "GIT_DISCOVERY_ACROSS_FILESYSTEM"})


class Tree(NamedTuple):
    """A work tree and the main work tree of its repository, the same folder outside a linked worktree."""

    work: Path
    main: Path


class Gate(NamedTuple):
    """A declared gate, its folders absolute and a first word holding a slash made absolute against its cwd."""

    run: tuple[Word, ...]
    cwd: Path
    as_from: Path
    protocol: Protocol
    rule_source: Prose
    suffixes: tuple[Suffix, ...]


class Project(NamedTuple):
    """A repository's facts, every path absolute, each heavy and unread script among them, the probe record None
    where it declares none."""

    records: tuple[Path, ...]
    bulk: tuple[Path, ...]
    heavy: tuple[Heavy, ...]
    test_determinism: Gate | None
    precise_hints: Gate | None
    unread_scripts: tuple[Path, ...]
    probe_record: Path | None = None


@dataclass(frozen=True, slots=True)
class Facts:
    """What holds for a path: its repository's facts, the file they came from and its work tree, and the machine's
    facts, read through ``machine_of`` when first asked and no part of the facts' equality."""

    machine_of: Callable[[], Machine] = field(compare=False)
    project: Project
    declaration: Path | None
    work_root: Path | None

    @property
    def machine(self) -> Machine:
        """The machine's facts; a KitFault where its file is missing or breaks the schema."""
        return self.machine_of()

    @property
    def records(self) -> tuple[Path, ...]:
        """Every run-record folder that holds for the path, the machine's first."""
        return (*self.machine.records, *self.project.records)


NO_PROJECT = Project((), (), (), None, None, ())


def read_toml(file: Path) -> TomlTable | None:
    """The table ``file`` holds, None where there is no such file."""
    try:
        data = file.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as fault:
        raise KitFault(Prose(f"the declaration {file} cannot be read: {fault.strerror}")) from None
    try:
        return tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        raise KitFault(Prose(f"the declaration {file} is not UTF-8")) from None
    except tomllib.TOMLDecodeError as fault:
        raise KitFault(Prose(f"the declaration {file} is not TOML: {fault}")) from None


def missing_machine(file: Path, home: Path) -> Prose:
    """The fault of a machine without its file: the file, the tables it must declare, and the whole kit's switch."""
    tables = ", ".join(f"[{key}] ({facts.named})" for key, facts in MACHINE_TABLES.items())
    return Prose(f"the machine file {file} is missing: it declares schema = 1 and the tables {tables}, each holding "
                 f'its facts or `absent = "<why>"` alone naming why this machine has none, as {EXAMPLE} does; the '
                 f"file {home / KIT_SWITCH} switches the whole kit off")


def start_of(path: Path) -> Path:
    """The folder a lookup of ``path`` starts from: resolved, out of any `.git`, the nearest that exists."""
    real = Path(os.path.realpath(path))
    if ".git" in real.parts:
        real = Path(*real.parts[:real.parts.index(".git")])
    while not real.is_dir():
        real = real.parent
    return real


def git_tree(folder: Path) -> Tree:
    """The work tree holding ``folder`` and its main work tree, as git names them."""
    environ = {name: value for name, value in os.environ.items() if name not in CLEARED}
    try:
        done = subprocess.run(["git", "-C", str(folder), "rev-parse", "--path-format=absolute", "--show-toplevel",
                               "--git-common-dir"], capture_output=True, env=environ, stdin=subprocess.DEVNULL,
                              timeout=GIT_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise KitFault(Prose(f"git outran its {GIT_TIMEOUT:g} s limit naming the repository of {folder}")) from None
    except OSError as fault:
        raise KitFault(Prose(f"git cannot run: {fault.strerror}")) from None
    said = done.stdout.decode("utf-8", "surrogateescape").splitlines()
    if done.returncode != 0 or len(said) != 2:
        why = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise KitFault(Prose(f"git cannot name the repository of {folder}: {why[0] if why else 'no answer'}"))
    work, common = Path(said[0]), Path(said[1])
    return Tree(work, common.parent if common.name == ".git" else work)


def placed(text: PathText, root: Path, home: Path) -> Path:
    """A declared path text as an absolute path: `~/` and absolute ones as they stand, others under ``root``."""
    return Path(os.path.normpath(at_home(text, home) if anchor_of(text) == "home" else root / text))


def gate(entry: GateEntry | None, tree: Tree, home: Path) -> Gate | None:
    """A declared gate with its folder and its command's first word made absolute."""
    if entry is None:
        return None
    cwd = tree.work if entry.cwd is None else placed(entry.cwd, tree.work, home)
    as_from = tree.work if entry.as_from is None else placed(entry.as_from, tree.work, home)
    first, *rest = entry.run
    run = (Word(str(placed(PathText(first), cwd, home))) if "/" in first else first, *rest)
    return Gate(run, cwd, as_from, entry.protocol, entry.rule_source, entry.suffixes)


def resolved(entries: ProjectEntries, tree: Tree, home: Path) -> Project:
    """A repository's declaration with every path made absolute for a path in ``tree``."""
    heavy = tuple(command if command.script is None
                  else command._replace(script=PathText(str(placed(command.script, tree.work, home))))
                  for command in entries.heavy)
    return Project(tuple(placed(path, tree.main, home) for path in entries.records),
                   tuple(placed(path, tree.work, home) for path in entries.bulk), heavy,
                   gate(entries.test_determinism, tree, home), gate(entries.precise_hints, tree, home),
                   tuple(placed(path, tree.work, home) for path in entries.unread_scripts),
                   None if entries.probe_record is None else placed(entries.probe_record, tree.main, home))


class Declarations:
    """The declarations a hook event reads, each file read once and each folder's repository asked of git once."""

    __slots__ = ("entries", "home", "machine_file", "read_machine", "trees")

    def __init__(self, machine_file: Path | None = None, home: Path | None = None) -> None:
        self.home = home or Path.home()
        self.machine_file = machine_file or self.home / MACHINE_FILE
        self.read_machine: Machine | None = None
        self.trees: dict[Path, Tree | None] = {}
        self.entries: dict[Path, ProjectEntries | None] = {}

    def machine(self) -> Machine:
        """The machine's facts; a KitFault where it has no file."""
        if self.read_machine is None:
            raw = read_toml(self.machine_file)
            if raw is None:
                raise KitFault(missing_machine(self.machine_file, self.home))
            self.read_machine = machine_facts(raw, self.machine_file, self.home)
        return self.read_machine

    def tree_of(self, folder: Path) -> Tree | None:
        """The work trees holding ``folder``, None where it lies outside every repository."""
        if folder not in self.trees:
            inside = any((above / ".git").exists() for above in (folder, *folder.parents))
            self.trees[folder] = git_tree(folder) if inside else None
        return self.trees[folder]

    def entries_at(self, file: Path) -> ProjectEntries | None:
        """The declaration ``file`` holds, None where there is no such file."""
        if file not in self.entries:
            raw = read_toml(file)
            self.entries[file] = None if raw is None else project_entries(raw, file)
        return self.entries[file]

    def for_path(self, path: Path) -> Facts:
        """What holds for the absolute ``path``, the machine file not read until its facts are asked."""
        if not path.is_absolute():
            raise KitFault(Prose(f"a declaration was asked for the relative path {path}"))
        tree = self.tree_of(start_of(path))
        if tree is None:
            return Facts(self.machine, NO_PROJECT, None, None)
        for file in dict.fromkeys((tree.work / DECLARATION, tree.main / DECLARATION)):
            entries = self.entries_at(file)
            if entries is not None:
                return Facts(self.machine, resolved(entries, tree, self.home), file, tree.work)
        return Facts(self.machine, NO_PROJECT, None, tree.work)
