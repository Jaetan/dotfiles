"""The declarations module: which declaration holds for a path, what it resolves to, that each file is read once per
Declarations, and that every file it cannot read or that breaks the schema, and a machine without its file, is a
KitFault the dispatcher refuses on. The repositories and worktrees are built in the runner's temporary folder, outside
every git tree."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NewType
from unittest import mock

from declarations.lookup import GIT_TIMEOUT, NO_PROJECT, Declarations, Facts, Gate, Project
from declarations.schema import (
    ByteCount,
    CpuNumber,
    Heavy,
    Machine,
    ModuleName,
    PathText,
    ProgramName,
    SliceName,
    Suffix,
    TomlKey,
    Word,
)
from dispatcher.core import FAULT_TAIL, WHOLE_KIT, EventBytes, FolderPath, Settings, answer
from dispatcher.gate_run import rel
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds
from dispatcher.verdict import KitFault, Prose

KIT = Path(__file__).resolve().parents[1]
EXAMPLES = KIT / "declarations" / "examples"
DOTFILES_DRAFT = Path("/home/nicolas/dev/agda/aletheia-ci-output/n270/build/declarations-draft/dotfiles/guard.toml")
ENTRY = KIT / "dispatcher" / "dispatch.py"
TomlSource = NewType("TomlSource", str)
TomlBytes = NewType("TomlBytes", bytes)
GitOutput = NewType("GitOutput", bytes)
Status = NewType("Status", int)
Fragment = NewType("Fragment", str)
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

PROJECT = TomlSource('''schema = 1
[records]
paths = ["../records", "~/kept", "/abs/records"]
[bulk]
paths = ["target"]
[unread_scripts]
paths = ["tools/run.sh"]
[[heavy]]
script = "tools/run.sh"
writes = true
[[heavy]]
module = "tools.ci"
[gates.precise_hints]
run = ["venv/bin/python", "-m", "tools.hints"]
protocol = "hints-1"
suffixes = [".py"]
rule_source = "the rules"
[gates.test_determinism]
run = ["checker"]
cwd = "sub"
as_from = "from"
protocol = "determinism-1"
[probes]
record = "probes.tsv"
''')
TRACKED = TomlSource('schema = 1\n[records]\npaths = ["kept"]\n[bulk]\npaths = ["out"]\n')
TRACKED_OWN = TomlSource('schema = 1\n[records]\npaths = ["own-kept"]\n')
NESTED = TomlSource('schema = 1\n[records]\npaths = ["n-kept"]\n')
TABLES: dict[TomlKey, TomlSource] = {
    TomlKey("cpus"): TomlSource('[cpus]\nagents = "0-3,8"\n'),
    TomlKey("slice"): TomlSource('[slice]\nname = "claude.slice"\n'),
    TomlKey("memory"): TomlSource('[memory]\ncall_max = "512M"\nheavy_max = "2G"\n'),
    TomlKey("records"): TomlSource('[records]\npaths = ["~/logs", "/var/kept"]\n'),
}


def machine_text(top: TomlSource = TomlSource(""), **swapped: TomlSource) -> TomlSource:
    """The layout's machine file, ``top`` among its top-level keys and each table named in ``swapped`` spelled as
    given there, an empty text leaving it out."""
    return TomlSource(f"schema = 1\n{top}" + "".join(swapped.get(name, text) for name, text in TABLES.items()))


def absent(table: TomlKey) -> TomlSource:
    """The table ``table`` declaring this machine has none of its facts."""
    return TomlSource(f'[{table}]\nabsent = "this machine has none"\n')


def declaring(**tables: TomlSource) -> TomlSource:
    """A machine file holding ``tables`` as given and every other table declaring its facts absent."""
    return machine_text(**({name: absent(name) for name in TABLES} | tables))


MACHINE = machine_text()
NONE_DECLARED = declaring()
NO_FACTS = Machine(None, None, None, None, ())


def declared(home: Path) -> Machine:
    """The facts MACHINE declares, under the home folder ``home``."""
    return Machine(frozenset(CpuNumber(cpu) for cpu in (0, 1, 2, 3, 8)), SliceName("claude.slice"),
                   ByteCount(512 << 20), ByteCount(2 << 30), (home / "logs", Path("/var/kept")))


def git(cwd: Path, *words: Word) -> None:
    """Run git in ``cwd``, with no user or system configuration but an identity."""
    _ = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *words],
                       cwd=cwd, env=GIT_ENV, check=True, capture_output=True)


def write(path: Path, text: TomlSource) -> Path:
    """Write ``text`` at ``path``, its folders made."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")
    return path


def repository(path: Path, declaration: TomlSource | None = None, committed: bool = False) -> Path:
    """A repository at ``path`` with one commit, holding ``declaration`` as its `.claude/guard.toml`, committed or not."""
    path.mkdir(parents=True)
    git(path, Word("init"), Word("-q"))
    _ = write(path / "sub" / "deeper" / "x.txt", TomlSource("x\n"))
    if declaration is not None:
        _ = write(path / ".claude" / "guard.toml", declaration)
    if declaration is not None and not committed:
        _ = write(path / ".gitignore", TomlSource(".claude/\n"))
    git(path, Word("add"), Word("-A"))
    git(path, Word("commit"), Word("-qm"), Word("init"))
    return path


def scratch(case: unittest.TestCase) -> Path:
    """A folder of the case's own in the temporary folder, removed after it."""
    folder = Path(tempfile.mkdtemp()).resolve()
    case.addCleanup(shutil.rmtree, folder)
    return folder


def heavy(module: ModuleName | None = None, script: PathText | None = None, writes: bool = False) -> Heavy:
    """A heavy command with no modifier but ``writes``."""
    return Heavy(module, script, (), None, writes, (), (), (), None, ())


class Layout(unittest.TestCase):
    """One layout every lookup reads: a main tree with an untracked declaration, its subfolders, two linked worktrees,
    two nested repositories, a repository with a committed declaration, its linked worktree, a bare clone's worktree,
    two fresh clones, a folder outside every repository, a symbolic link and the machine file."""

    root: Path
    home: Path
    main: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp()).resolve()
        cls.home = cls.root / "home"
        cls.main = repository(cls.root / "main", PROJECT)
        git(cls.main, Word("worktree"), Word("add"), Word("-q"), Word(".claude/worktrees/inner"), Word("-b"),
            Word("inner"))
        git(cls.main, Word("worktree"), Word("add"), Word("-q"), Word("../outer"), Word("-b"), Word("outer"))
        _ = repository(cls.main / "nested", NESTED)
        plain = repository(cls.main / "plain-nested")
        _ = write(plain / ".claude", TomlSource("a file, not a folder\n"))
        tracked = repository(cls.root / "tracked", TRACKED, committed=True)
        git(tracked, Word("worktree"), Word("add"), Word("-q"), Word("../tracked-wt"), Word("-b"), Word("own"))
        _ = write(cls.root / "tracked-wt" / ".claude" / "guard.toml", TRACKED_OWN)
        git(cls.root, Word("clone"), Word("-q"), Word("tracked"), Word("fresh"))
        git(cls.root, Word("clone"), Word("-q"), Word("main"), Word("fresh-untracked"))
        git(cls.root, Word("clone"), Word("-q"), Word("--bare"), Word("tracked"), Word("bare.git"))
        git(cls.root / "bare.git", Word("worktree"), Word("add"), Word("-q"), Word("../bare-wt"), Word("main"))
        (cls.root / "elsewhere" / "deep").mkdir(parents=True)
        (cls.root / "link").symlink_to(cls.main / "sub")
        _ = write(cls.root / "machine.toml", MACHINE)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root)

    def facts(self, path: Path) -> Facts:
        """What holds for ``path`` under the layout's machine file."""
        return Declarations(self.root / "machine.toml", self.home).for_path(path)

    def machine(self) -> Machine:
        """The layout's machine facts."""
        return declared(self.home)

    def main_facts(self, work: Path) -> Facts:
        """The main tree's declaration as it holds in its work tree ``work``: records and the probe record at the main
    tree's root, everything else, the gates' folders included, in ``work``."""
        project = Project(
            (self.root / "records", self.home / "kept", Path("/abs/records")), (work / "target",),
            (heavy(script=PathText(str(work / "tools" / "run.sh")), writes=True), heavy(module=ModuleName("tools.ci"))),
            Gate((Word("checker"),), work / "sub", work / "from", "determinism-1", Prose(""), ()),
            Gate((Word(str(work / "venv" / "bin" / "python")), Word("-m"), Word("tools.hints")), work,
                 work, "hints-1", Prose("the rules"), (Suffix(".py"),)), (work / "tools" / "run.sh",),
            self.main / "probes.tsv")
        return Facts(self.machine, project, self.main / ".claude" / "guard.toml", work)

    def bare(self, work: Path | None) -> Facts:
        """No project declaration, in the work tree ``work`` (None outside every repository)."""
        return Facts(self.machine, NO_PROJECT, None, work)


class Lookups(Layout):
    """Each place a call can name gets the declaration of the repository it lies in, resolved for its work tree."""

    def test_the_calls_folder(self) -> None:
        self.assertEqual(self.facts(self.main), self.main_facts(self.main))

    def test_a_subfolder_a_file_and_a_path_not_yet_made(self) -> None:
        for path in (self.main / "sub", self.main / "sub" / "deeper" / "x.txt", self.main / "sub" / "new" / "y.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.facts(path), self.main_facts(self.main))

    def test_a_path_inside_the_git_folder(self) -> None:
        self.assertEqual(self.facts(self.main / ".git" / "config"), self.main_facts(self.main))

    def test_a_symbolic_link_is_followed(self) -> None:
        self.assertEqual(self.facts(self.root / "link" / "deeper"), self.main_facts(self.main))

    def test_a_linked_worktree_inside_its_tree(self) -> None:
        inner = self.main / ".claude" / "worktrees" / "inner"
        self.assertEqual(self.facts(inner / "sub"), self.main_facts(inner))

    def test_a_linked_worktree_outside_its_tree(self) -> None:
        outer = self.root / "outer"
        self.assertEqual(self.facts(outer), self.main_facts(outer))

    def test_a_file_of_a_linked_worktree_is_handed_to_its_own_gate(self) -> None:
        """The old hooks ran the gate of the file's own work tree: a file there has its REL against that tree."""
        outer = self.root / "outer"
        gate = self.facts(outer / "pkg" / "m.py").project.precise_hints
        assert gate is not None
        self.assertEqual((gate.cwd, rel(gate, outer / "pkg" / "m.py")), (outer, "pkg/m.py"))

    def test_a_linked_worktree_with_a_declaration_of_its_own(self) -> None:
        own = self.root / "tracked-wt"
        project = NO_PROJECT._replace(records=(self.root / "tracked" / "own-kept",))
        self.assertEqual(self.facts(own), Facts(self.machine, project, own / ".claude" / "guard.toml", own))

    def test_a_worktree_of_a_bare_repository_is_its_own_main_tree(self) -> None:
        work = self.root / "bare-wt"
        project = NO_PROJECT._replace(records=(work / "kept",), bulk=(work / "out",))
        self.assertEqual(self.facts(work), Facts(self.machine, project, work / ".claude" / "guard.toml", work))

    def test_a_nested_repository_with_its_own(self) -> None:
        nested = self.main / "nested"
        project = NO_PROJECT._replace(records=(nested / "n-kept",))
        self.assertEqual(self.facts(nested / "sub"),
                         Facts(self.machine, project, nested / ".claude" / "guard.toml", nested))

    def test_a_nested_repository_without_one(self) -> None:
        self.assertEqual(self.facts(self.main / "plain-nested" / "sub"), self.bare(self.main / "plain-nested"))

    def test_a_path_outside_every_repository(self) -> None:
        for path in (self.root / "elsewhere" / "deep", self.root / "elsewhere" / "none" / "z.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.facts(path), self.bare(None))

    def test_a_fresh_clone_carries_a_committed_declaration(self) -> None:
        fresh = self.root / "fresh"
        project = NO_PROJECT._replace(records=(fresh / "kept",), bulk=(fresh / "out",))
        self.assertEqual(self.facts(fresh / "sub"),
                         Facts(self.machine, project, fresh / ".claude" / "guard.toml", fresh))

    def test_a_fresh_clone_of_an_untracked_declaration_has_none(self) -> None:
        self.assertEqual(self.facts(self.root / "fresh-untracked"), self.bare(self.root / "fresh-untracked"))

    def test_the_hooks_git_environment_is_ignored(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "tracked" / ".git")}):
            self.assertEqual(self.facts(self.main / "sub"), self.main_facts(self.main))

    def test_a_relative_path_is_a_fault(self) -> None:
        with self.assertRaises(KitFault) as caught:
            _ = self.facts(Path("sub"))
        self.assertIn("relative path sub", caught.exception.reason)

    def test_records_are_the_machines_then_the_projects(self) -> None:
        self.assertEqual(self.facts(self.main).records, (self.home / "logs", Path("/var/kept"), self.root / "records",
                                                         self.home / "kept", Path("/abs/records")))


class Machines(unittest.TestCase):
    """The machine file's facts, each table declaring its facts absent, a missing file's fault, and the example's."""

    def machine(self, text: TomlSource) -> Machine:
        """The facts of a machine file holding ``text``."""
        folder = scratch(self)
        return Declarations(write(folder / "machine.toml", text), folder / "home").machine()

    def test_a_missing_machine_file_is_a_fault(self) -> None:
        folder = scratch(self)
        declarations = Declarations(folder / "no-such.toml", folder / "home")
        switch = folder / "home" / ".config" / "claude-guard" / "off" / WHOLE_KIT
        for asked, ask in {"machine": declarations.machine,
                           "for_path": lambda: declarations.for_path(folder).machine}.items():
            with self.subTest(asked=asked):
                with self.assertRaises(KitFault) as caught:
                    _ = ask()
                self.assertEqual(caught.exception.reason, (
                    f"the machine file {folder / 'no-such.toml'} is missing: it declares schema = 1 and the tables "
                    "[cpus] (agents), [slice] (name), [memory] (call_max, optionally heavy_max), "
                    '[records] (paths), each holding its facts or `absent = "<why>"` alone naming why this machine '
                    f"has none, as {EXAMPLES / 'machine.toml'} does; the file {switch} switches the whole kit off"))

    def test_a_paths_project_facts_never_read_the_machine_file(self) -> None:
        folder = scratch(self)
        (folder / "work").mkdir()
        cases = {"missing": None, "incomplete": machine_text(slice=TomlSource(""))}
        for case, text in cases.items():
            with self.subTest(case=case):
                file = folder / f"{case}.toml"
                if text is not None:
                    _ = write(file, text)
                facts = Declarations(file, folder / "home").for_path(folder / "work")
                self.assertEqual(facts, Facts(lambda: NO_FACTS, NO_PROJECT, None, None))
                for asked in ("machine", "records"):
                    with self.assertRaises(KitFault) as caught:
                        _ = getattr(facts, asked)
                    self.assertIn(str(file), caught.exception.reason)

    def test_each_table_declares_its_facts_absent(self) -> None:
        full = declared(Path("/h"))
        cases = {TomlKey("cpus"): full._replace(agent_cpus=None),
                 TomlKey("slice"): full._replace(slice=None),
                 TomlKey("memory"): full._replace(call_max=None, heavy_max=None),
                 TomlKey("records"): full._replace(records=())}
        for table, want in cases.items():
            with self.subTest(table=table):
                folder = scratch(self)
                file = write(folder / "machine.toml", machine_text(**{table: absent(table)}))
                self.assertEqual(Declarations(file, Path("/h")).machine(), want)
        self.assertEqual(self.machine(NONE_DECLARED), NO_FACTS)
        self.assertIsNone(NO_FACTS.job_bound)

    def test_the_machine_file_sits_under_the_home_folder(self) -> None:
        home = scratch(self)
        _ = write(home / ".config" / "claude-guard" / "machine.toml",
                  machine_text(slice=TomlSource('[slice]\nname = "a.slice"\n')))
        self.assertEqual(Declarations(home=home).machine().slice, "a.slice")

    def test_the_example(self) -> None:
        home = scratch(self)
        got = Declarations(EXAMPLES / "machine.toml", home).machine()
        self.assertEqual(got, Machine(frozenset(CpuNumber(cpu) for cpu in range(20)), SliceName("claude.slice"),
                                      None, None, (home / "dev" / "logs",)))
        self.assertEqual(got.job_bound, 20)

    def test_sizes_and_tiers(self) -> None:
        for call, high, want in (('"100"', None, (100, 100)), ('"2K"', '"3M"', (2 << 10, 3 << 20)),
                                 ('"512M"', '"512M"', (512 << 20, 512 << 20)), ('"1G"', '"2T"', (1 << 30, 2 << 40))):
            text = f"[memory]\ncall_max = {call}\n" + (f"heavy_max = {high}\n" if high else "")
            with self.subTest(call=call, heavy=high):
                got = self.machine(machine_text(memory=TomlSource(text)))
                self.assertEqual((got.call_max, got.heavy_max), want)

    def test_cpu_lists(self) -> None:
        got = self.machine(machine_text(cpus=TomlSource('[cpus]\nagents = "0-2,5,7-8"\n')))
        self.assertEqual((got.agent_cpus, got.job_bound), ({0, 1, 2, 5, 7, 8}, 6))

    def test_a_home_record(self) -> None:
        folder = scratch(self)
        file = write(folder / "machine.toml", machine_text(records=TomlSource('[records]\npaths = ["~"]\n')))
        self.assertEqual(Declarations(file, folder / "h").machine().records, (folder / "h",))


class Examples(unittest.TestCase):
    """The aletheia example reads as the repository would declare it."""

    def test_the_aletheia_example(self) -> None:
        folder = scratch(self)
        work = repository(folder / "aletheia", TomlSource(""))
        _ = shutil.copyfile(EXAMPLES / "aletheia.toml", work / ".claude" / "guard.toml")
        got = Declarations(write(folder / "m.toml", MACHINE), folder).for_path(work).project
        self.assertEqual(got.records, (folder / "aletheia-ci-output",))
        self.assertEqual(got.bulk[0], work / "rust" / "target")
        self.assertEqual(len(got.heavy), 21)
        self.assertEqual(got.heavy[0], heavy(module=ModuleName("tools.run_ci"), writes=True))
        self.assertEqual(got.heavy[3].light_with, ("--scope",))
        self.assertEqual(got.heavy[11].script, str(work / "probes" / "run_all.sh"))
        self.assertEqual(got.unread_scripts, (work / "probes" / "run_all.sh",))
        self.assertEqual(got.heavy[14]._replace(light_targets=(), verbose_flags=()),
                         Heavy(None, None, (Word("cabal"), Word("run"), Word("shake")), None, True, (), (), (), None, ()))
        self.assertEqual((got.heavy[14].light_targets, got.heavy[14].verbose_flags), (("count-modules", "help"), ("-V",)))
        self.assertEqual((got.heavy[15].program, got.heavy[15].targeted_by, got.heavy[15].writes),
                         (ProgramName("unit_tests"), "catch2-operands", False))
        self.assertEqual(got.heavy[19].valued_options, ("--frames", "--runs", "--warmup", "--bench", "--json"))
        self.assertEqual(got.test_determinism, Gate(
            (Word(str(work / "python" / ".venv" / "bin" / "python")), Word("-m"), Word("tools.check_test_determinism")),
            work, work, "determinism-1", Prose("AGENTS.md, Universal Rules"), ()))
        assert got.precise_hints is not None
        self.assertEqual((got.precise_hints.suffixes, got.precise_hints.as_from), ((".py", ".sh"), work))
        self.assertEqual(got.probe_record, folder / "dev" / "agda" / "aletheia-ci-output" / "probes" / "aletheia"
                         / "timings.tsv")

    def test_the_dotfiles_draft(self) -> None:
        folder = scratch(self)
        work = repository(folder / "dotfiles", TomlSource(""))
        _ = shutil.copyfile(DOTFILES_DRAFT, work / ".claude" / "guard.toml")
        got = Declarations(write(folder / "m.toml", MACHINE), folder).for_path(work).project
        kit = "/home/nicolas/dotfiles/.claude/guard"
        self.assertEqual(got.precise_hints, Gate(
            (Word("/home/nicolas/dev/agda/aletheia/python/.venv/bin/python"), Word("-m"),
             Word("tools.check_precise_hints"), Word("--root"), Word(kit), Word("--record"),
             Word(f"{kit}/PYTHON_IMPRECISE_HINTS.yaml")), folder / "dev" / "agda" / "aletheia",
            work / ".claude" / "guard", "hints-1", Prose("AGENTS/python.md cat 8"), (Suffix(".py"),)))


    def test_the_row_scope(self) -> None:
        folder = scratch(self)
        work = repository(folder / "r", TomlSource('schema = 1\n[[heavy]]\nprogram = "agda"\n'
                                                   '[[heavy]]\nmodule = "pytest"\ntargeted_by = "pytest-operands"\n'
                                                   '[[heavy]]\nprogram = "pylint"\ntargeted_by = "python-files"\n'))
        got = Declarations(write(folder / "m.toml", MACHINE), folder).for_path(work).project.heavy
        self.assertEqual((got[0].targeted_by, got[1].targeted_by, got[2].targeted_by),
                         (None, "pytest-operands", "python-files"))


class FolderKeys(unittest.TestCase):
    """A gate's as_from and the project's probe record are read in each form a gate's cwd is."""

    def test_as_from_and_the_probe_record(self) -> None:
        folder = scratch(self)
        home = folder / "home"
        machine = write(folder / "m.toml", MACHINE)
        work = repository(folder / "r", TomlSource(""))
        gates = TomlSource('[gates.test_determinism]\nrun = ["x"]\ncwd = "elsewhere"\nprotocol = "determinism-1"\n{}'
                           '[gates.precise_hints]\nrun = ["x"]\ncwd = "elsewhere"\nprotocol = "hints-1"\n{}')
        for text, want in ((PathText("from/here"), work / "from" / "here"), (PathText("~/from"), home / "from"),
                           (PathText("/abs/from"), Path("/abs/from")), (None, None)):
            with self.subTest(text=text):
                line = "" if text is None else f'as_from = "{text}"\n'
                probes = "" if text is None else f'[probes]\nrecord = "{text}"\n'
                _ = write(work / ".claude" / "guard.toml", TomlSource(f"schema = 1\n{probes}" + gates.format(line, line)))
                got = Declarations(machine, home).for_path(work).project
                assert got.test_determinism is not None and got.precise_hints is not None
                self.assertEqual((got.test_determinism.as_from, got.precise_hints.as_from, got.probe_record),
                                 (want or work, want or work, want))


class ReadOnce(unittest.TestCase):
    """A Declarations reads each declaration and the machine file once, and asks git once per folder."""

    def test_a_declaration_is_read_once(self) -> None:
        folder = scratch(self)
        work = repository(folder / "r", TRACKED)
        declarations = Declarations(write(folder / "m.toml", MACHINE), folder)
        first = declarations.for_path(work)
        _ = write(work / ".claude" / "guard.toml", NESTED)
        self.assertEqual(declarations.for_path(work / "sub").project, first.project)
        self.assertNotEqual(Declarations(folder / "m.toml", folder).for_path(work).project, first.project)

    def test_the_machine_file_is_read_once(self) -> None:
        folder = scratch(self)
        file = write(folder / "m.toml", MACHINE)
        declarations = Declarations(file, folder)
        first = declarations.machine()
        _ = write(file, NONE_DECLARED)
        self.assertEqual(declarations.for_path(folder).machine, first)
        self.assertEqual(Declarations(file, folder).machine(), NO_FACTS)

    def test_git_is_asked_once_per_folder(self) -> None:
        folder = scratch(self)
        work = repository(folder / "r")
        declarations = Declarations(write(folder / "m.toml", MACHINE), folder)
        first = declarations.for_path(work)
        (work / ".git").rename(folder / "moved-git")
        self.assertEqual(declarations.for_path(work), first)
        self.assertIsNone(Declarations(folder / "m.toml", folder).for_path(work).work_root)


class GitFaults(unittest.TestCase):
    """git that cannot run, outruns its limit, fails or answers wrongly is a KitFault."""

    def fault(self, effect: subprocess.CompletedProcess[GitOutput] | OSError | subprocess.TimeoutExpired) -> KitFault:
        """The fault a lookup in a repository raises where git gives ``effect``, its time limit checked."""
        folder = scratch(self)
        (folder / ".git").mkdir()
        _ = write(folder / "m.toml", MACHINE)
        given = {"side_effect": effect} if isinstance(effect, Exception) else {"return_value": effect}
        with mock.patch("declarations.lookup.subprocess.run", **given) as run, self.assertRaises(KitFault) as caught:
            _ = Declarations(folder / "m.toml", folder).for_path(folder)
        self.assertEqual(run.call_args.kwargs["timeout"], GIT_TIMEOUT)
        return caught.exception

    def test_git_outruns_its_limit(self) -> None:
        self.assertIn("git outran", self.fault(subprocess.TimeoutExpired("git", GIT_TIMEOUT)).reason)

    def test_git_cannot_run(self) -> None:
        self.assertIn("git cannot run", self.fault(FileNotFoundError(2, "No such file")).reason)

    def test_git_fails(self) -> None:
        said = subprocess.CompletedProcess(["git"], 128, GitOutput(b"/a\n/a/.git\n"), GitOutput(b"fatal: not ours\n"))
        self.assertIn("fatal: not ours", self.fault(said).reason)

    def test_git_answers_one_line(self) -> None:
        said = subprocess.CompletedProcess(["git"], 0, GitOutput(b"/a\n"), GitOutput(b""))
        self.assertIn("no answer", self.fault(said).reason)


class Refusals(unittest.TestCase):
    """Every declaration a file cannot hold is a KitFault naming the file and the key."""

    def project(self, text: TomlSource | TomlBytes) -> Prose:
        """The fault a repository declaring ``text`` raises."""
        folder = scratch(self)
        work = repository(folder / "r", TomlSource(""))
        _ = write(folder / "m.toml", MACHINE)
        file = work / ".claude" / "guard.toml"
        _ = file.write_bytes(text if isinstance(text, bytes) else text.encode())
        with self.assertRaises(KitFault) as caught:
            _ = Declarations(folder / "m.toml", folder).for_path(work)
        self.assertIn(str(file), caught.exception.reason)
        return caught.exception.reason

    def machine(self, text: TomlSource) -> Prose:
        """The fault a machine file holding ``text`` raises."""
        folder = scratch(self)
        file = write(folder / "m.toml", text)
        with self.assertRaises(KitFault) as caught:
            _ = Declarations(file, folder).machine()
        self.assertIn(str(file), caught.exception.reason)
        return caught.exception.reason

    def refused(self, cases: tuple[tuple[TomlSource, Fragment], ...], machine: bool = False) -> None:
        """Each case's text is refused with its fragment in the reason: a project's after the schema line, a machine's
        whole."""
        for text, fragment in cases:
            with self.subTest(text=text):
                said = self.machine(text) if machine else self.project(TomlSource("schema = 1\n" + text))
                self.assertIn(fragment, said)

    def test_a_file_that_cannot_be_read(self) -> None:
        self.assertIn("is not TOML", self.project(TomlSource("schema = 1\n[records\n")))
        self.assertIn("is not UTF-8", self.project(TomlBytes(b"schema = 1\n# \xff\xfe\n")))
        self.assertIn("is not TOML", self.machine(TomlSource("schema = = 1\n")))
        folder = scratch(self)
        work = repository(folder / "r")
        (work / ".claude" / "guard.toml").mkdir(parents=True)
        with self.assertRaises(KitFault) as caught:
            _ = Declarations(write(folder / "m.toml", MACHINE), folder).for_path(work)
        self.assertIn("cannot be read", caught.exception.reason)

    def test_the_schema_version(self) -> None:
        for text in ("", "schema = 2\n", "schema = true\n", "schema = 1.0\n", 'schema = "1"\n'):
            with self.subTest(text=text):
                self.assertIn("at `schema`: it must be 1", self.project(TomlSource(text)))
        self.assertIn("at `schema`", self.machine(TomlSource("[slice]\n")))

    def test_unknown_keys(self) -> None:
        self.refused(((TomlSource("record = 1\n"), Fragment("at `record`: no such key")),
                      (TomlSource("[records]\npath = []\n"), Fragment("at `records.path`: no such key")),
                      (TomlSource("[bulk]\nfiles = []\n"), Fragment("at `bulk.files`")),
                      (TomlSource('[[heavy]]\nmodule = "m"\nwrite = true\n'), Fragment("at `heavy[1].write`")),
                      (TomlSource('[gates.hints]\nrun = ["x"]\n'), Fragment("at `gates.hints`")),
                      (TomlSource('[gates.test_determinism]\nrun = ["x"]\nprotocol = "determinism-1"\nsuffixes = []\n'),
                       Fragment("at `gates.test_determinism.suffixes`"))))
        self.refused(((machine_text(TomlSource("cpu = 1\n")), Fragment("at `cpu`")),
                      (machine_text(cpus=TomlSource('[cpus]\nagent = "0"\n')), Fragment("at `cpus.agent`")),
                      (machine_text(slice=TomlSource('[slice]\nslice = "a.slice"\n')), Fragment("at `slice.slice`")),
                      (machine_text(memory=TomlSource('[memory]\ncall = "1G"\n')), Fragment("at `memory.call`")),
                      (machine_text(records=TomlSource("[records]\nfolders = []\n")),
                       Fragment("at `records.folders`"))), machine=True)

    def test_types(self) -> None:
        self.refused(((TomlSource("records = 3\n"), Fragment("at `records`: it must be a table")),
                      (TomlSource("gates = 3\n"), Fragment("at `gates`: it must be a table")),
                      (TomlSource('[records]\npaths = "x"\n'), Fragment("at `records.paths`: it must be a list")),
                      (TomlSource('[records]\npaths = ["x", 3]\n'), Fragment("at `records.paths`: it must be a list")),
                      (TomlSource('[bulk]\npaths = [""]\n'), Fragment("at `bulk.paths`: it must be a list")),
                      (TomlSource("heavy = 3\n"), Fragment("at `heavy`: it must be an array")),
                      (TomlSource("heavy = [3]\n"), Fragment("at `heavy[1]`: it must be a table")),
                      (TomlSource('[[heavy]]\nmodule = ""\n'), Fragment("at `heavy[1].module`: it must be a non-empty")),
                      (TomlSource('[[heavy]]\nmodule = "m"\nwrites = "yes"\n'), Fragment("at `heavy[1].writes`")),
                      (TomlSource('[[heavy]]\nmodule = "m"\nlight_with = "--x"\n'),
                       Fragment("at `heavy[1].light_with`")),
                      (TomlSource('[[heavy]]\nmodule = "m"\ntargeted_by = "gtest"\n'),
                       Fragment("at `heavy[1].targeted_by`: it must be one of catch2-operands, pytest-operands, "
                                "python-files"))))
        self.refused(((machine_text(cpus=TomlSource("[cpus]\nagents = 3\n")),
                       Fragment("at `cpus.agents`: it must be a non-empty")),
                      (machine_text(records=TomlSource("[records]\npaths = 3\n")), Fragment("at `records.paths`"))),
                     machine=True)

    def test_a_retired_key_is_no_key(self) -> None:
        self.refused(tuple((TomlSource(f'[[heavy]]\nmodule = "m"\n{line}\n'),
                            Fragment(f"at `heavy[1].{line.partition(' ')[0]}`: no such key"))
                           for line in (TomlSource('option_marks = ["+"]'), TomlSource('needs_one_of = ["--build"]'),
                                        TomlSource('light_values = {"--target" = ["help"]}'),
                                        TomlSource('heavy_operands = ["*.agda"]'), TomlSource('heavy_with = ["--all"]'),
                                        TomlSource('several_valued = ["--target"]'))))

    def test_heavy_matchers(self) -> None:
        self.refused(((TomlSource("[[heavy]]\nwrites = true\n"), Fragment("at `heavy[1]`: it must set exactly one of "
                                                                         "module, script, argv, program, not 0")),
                      (TomlSource('[[heavy]]\nmodule = "m"\nprogram = "p"\n'), Fragment("not 2")),
                      (TomlSource('[[heavy]]\nmodule = "m"\n[[heavy]]\nargv = []\n'),
                       Fragment("at `heavy[2].argv`: it must name at least one word"))))

    def test_path_forms(self) -> None:
        self.refused(((TomlSource('[bulk]\npaths = ["/abs"]\n'),
                       Fragment("at `bulk.paths`: '/abs' is not a relative")),
                      (TomlSource('[bulk]\npaths = ["~/x"]\n'), Fragment("at `bulk.paths`")),
                      (TomlSource('[[heavy]]\nscript = "/x.sh"\n'), Fragment("at `heavy[1].script`: '/x.sh' is not")),
                      (TomlSource('[[heavy]]\nscript = "~/x.sh"\n'), Fragment("at `heavy[1].script`")),
                      (TomlSource('[unread_scripts]\npaths = ["/x.sh"]\n'),
                       Fragment("at `unread_scripts.paths`: '/x.sh' is not a relative")),
                      (TomlSource('[unread_scripts]\npaths = ["~/x.sh"]\n'), Fragment("at `unread_scripts.paths`")),
                      (TomlSource('[unread_scripts]\nscripts = ["x.sh"]\n'),
                       Fragment("at `unread_scripts.scripts`: no such key"))))
        self.refused(((machine_text(records=TomlSource('[records]\npaths = ["logs"]\n')),
                       Fragment("at `records.paths`: 'logs' is not `~/` or absolute")),), machine=True)

    def test_gates(self) -> None:
        self.refused(((TomlSource('[gates.precise_hints]\nprotocol = "hints-1"\n'),
                       Fragment("at `gates.precise_hints.run`: it must name")),
                      (TomlSource('[gates.precise_hints]\nrun = []\nprotocol = "hints-1"\n'),
                       Fragment("at `gates.precise_hints.run`")),
                      (TomlSource('[gates.precise_hints]\nrun = ["x"]\n'),
                       Fragment("at `gates.precise_hints.protocol`: it must be 'hints-1'")),
                      (TomlSource('[gates.test_determinism]\nrun = ["x"]\nprotocol = "hints-1"\n'),
                       Fragment("at `gates.test_determinism.protocol`: it must be 'determinism-1'")),
                      (TomlSource('[gates.precise_hints]\nrun = ["x"]\nprotocol = "hints-1"\ncwd = 3\n'),
                       Fragment("at `gates.precise_hints.cwd`")),
                      (TomlSource('[gates.precise_hints]\nrun = ["x"]\nprotocol = "hints-1"\nrule_source = ""\n'),
                       Fragment("at `gates.precise_hints.rule_source`")),
                      (TomlSource('[gates.precise_hints]\nrun = ["x"]\nprotocol = "hints-1"\nas_from = ["d"]\n'),
                       Fragment("at `gates.precise_hints.as_from`: it must be a non-empty string")),
                      (TomlSource('[gates.test_determinism]\nrun = ["x"]\nprotocol = "determinism-1"\nas_from = 3\n'),
                       Fragment("at `gates.test_determinism.as_from`: it must be a non-empty string"))))

    def test_the_probe_record(self) -> None:
        self.refused(((TomlSource('[probes]\nrecords = "x"\n'),
                       Fragment("at `probes.records`: no such key; the keys here are record")),
                      (TomlSource("[probes]\nrecord = 3\n"), Fragment("at `probes.record`: it must be a non-empty string")),
                      (TomlSource("probes = 3\n"), Fragment("at `probes`: it must be a table"))))

    def test_cpu_lists(self) -> None:
        for text in ("0-", "a", "5-3", "0,,2", " 1", "1-a", "-1"):
            with self.subTest(text=text):
                self.assertIn(f"at `cpus.agents`: {text!r} is not a CPU list",
                              self.machine(machine_text(cpus=TomlSource(f'[cpus]\nagents = "{text}"\n'))))

    def test_slice_and_sizes(self) -> None:
        self.assertIn("at `slice.name`: 'claude' is not",
                      self.machine(machine_text(slice=TomlSource('[slice]\nname = "claude"\n'))))
        for text in ("24g", "0", "G", "1.5G", "-1G", "0K"):
            with self.subTest(text=text):
                self.assertIn(f"at `memory.call_max`: {text!r} is not a size",
                              self.machine(machine_text(memory=TomlSource(f'[memory]\ncall_max = "{text}"\n'))))
        self.assertIn("at `memory.heavy_max`: it must be at least memory.call_max",
                      self.machine(machine_text(memory=TomlSource('[memory]\ncall_max = "8G"\nheavy_max = "4G"\n'))))

    def test_a_missing_table(self) -> None:
        named = {TomlKey("cpus"): "agents", TomlKey("slice"): "name",
                 TomlKey("memory"): "call_max, optionally heavy_max", TomlKey("records"): "paths"}
        for table, facts in named.items():
            with self.subTest(table=table):
                self.assertIn(f'at `{table}`: the table is missing; it holds {facts}, or `absent = "<why>"` alone '
                              "naming why this machine has none",
                              self.machine(machine_text(**{table: TomlSource("")})))
        self.assertIn("at `cpus`: the table is missing", self.machine(TomlSource("schema = 1\n")))

    def test_a_missing_fact(self) -> None:
        said = ', or the table holds `absent = "<why>"` alone'
        self.refused(((machine_text(cpus=TomlSource("[cpus]\n")),
                       Fragment(f"at `cpus.agents`: it must be declared{said}")),
                      (machine_text(slice=TomlSource("[slice]\n")),
                       Fragment(f"at `slice.name`: it must be declared{said}")),
                      (machine_text(memory=TomlSource('[memory]\nheavy_max = "4G"\n')),
                       Fragment(f"at `memory.call_max`: it must be declared{said}")),
                      (machine_text(records=TomlSource("[records]\n")),
                       Fragment(f"at `records.paths`: it must be declared{said}")),
                      (machine_text(records=TomlSource("[records]\npaths = []\n")),
                       Fragment(f"at `records.paths`: it must name a folder{said}"))), machine=True)

    def test_absent_forms(self) -> None:
        beside = {TomlKey("cpus"): 'agents = "0"', TomlKey("slice"): 'name = "a.slice"',
                  TomlKey("memory"): 'call_max = "1G"', TomlKey("records"): 'paths = ["/r"]'}
        self.refused(tuple((machine_text(**{table: TomlSource(f'{absent(table)}{fact}\n')}),
                            Fragment(f"at `{table}.absent`: it stands alone, not beside {fact.split()[0]}"))
                           for table, fact in beside.items()), machine=True)
        self.refused(tuple((machine_text(slice=TomlSource(f"[slice]\nabsent = {reason}\n")),
                            Fragment("at `slice.absent`: it must be a string naming why this machine has none"))
                           for reason in ('""', '"  "', "3", '["why"]', "false")), machine=True)

    def test_misspellings(self) -> None:
        cases = ((TomlKey("cpus"), '[cpu]\nagents = "0-3,8"\n', "cpu"),
                 (TomlKey("slice"), '[slices]\nname = "claude.slice"\n', "slices"),
                 (TomlKey("memory"), '[memry]\ncall_max = "1G"\n', "memry"),
                 (TomlKey("records"), '[record]\npaths = ["/r"]\n', "record"),
                 (TomlKey("cpus"), '[cpus]\nagent = "0-3"\n', "cpus.agent"),
                 (TomlKey("slice"), '[slice]\nnme = "a.slice"\n', "slice.nme"),
                 (TomlKey("memory"), '[memory]\ncall = "1G"\n', "memory.call"),
                 (TomlKey("records"), '[records]\npath = ["/r"]\n', "records.path"),
                 (TomlKey("slice"), '[slice]\nabsnt = "none"\n', "slice.absnt"))
        self.refused(tuple((machine_text(**{table: TomlSource(text)}), Fragment(f"at `{key}`: no such key"))
                           for table, text, key in cases), machine=True)


class Dispatcher(unittest.TestCase):
    """A rule whose declaration cannot be read refuses the call, through the dispatcher's own fault path, and a machine
    without its file or one of its tables refuses every Bash and Monitor call through the kit's entry."""

    def dispatched(self, home: Path, tool: Word) -> tuple[Status, Prose, Prose]:
        """What dispatch.py, the kit's entry, answers a ``tool`` call of `true` in ``home``, its home folder, the route
        check switched off there: these cases hold the declarations, and no environment here names claude-call's
        wrapper."""
        off = home / ".config" / "claude-guard" / "off"
        off.mkdir(parents=True, exist_ok=True)
        (off / "route-check").touch()
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(home), "session_id": "s",
                   "tool_input": {"command": "true"}}
        done = subprocess.run([str(ENTRY)], input=json.dumps(payload).encode(), capture_output=True,
                              env={**os.environ, "HOME": str(home)}, check=False, timeout=20)
        return Status(done.returncode), Prose(done.stdout.decode()), Prose(done.stderr.decode())

    def test_a_machine_without_its_file_refuses_every_call_until_the_switch(self) -> None:
        home = scratch(self)
        with self.assertRaises(KitFault) as caught:
            _ = Declarations(home=home).machine()
        self.assertIn(f"the machine file {home / '.config' / 'claude-guard' / 'machine.toml'} is missing",
                      caught.exception.reason)
        for tool in (Word("Bash"), Word("Monitor")):
            with self.subTest(tool=tool):
                self.assertEqual(self.dispatched(home, tool),
                                 (2, "", f"guard kit: {caught.exception.reason}. {FAULT_TAIL}\n"))
        switch = home / ".config" / "claude-guard" / "off" / WHOLE_KIT
        self.assertIn(f"the file {switch} switches the whole kit off", caught.exception.reason)
        switch.touch()
        self.assertEqual(self.dispatched(home, Word("Bash")), (0, "", ""))

    def test_a_machine_file_missing_a_table_refuses_the_call(self) -> None:
        home = scratch(self)
        file = write(home / ".config" / "claude-guard" / "machine.toml", machine_text(slice=TomlSource("")))
        self.assertEqual(self.dispatched(home, Word("Bash")), (2, "", (
            f"guard kit: the declaration {file} is refused at `slice`: the table is missing; it holds name, or "
            f'`absent = "<why>"` alone naming why this machine has none. {FAULT_TAIL}\n')))

    def test_a_broken_declaration_refuses_the_call(self) -> None:
        folder = scratch(self)
        _ = write(folder / "m.toml", MACHINE)
        broken = repository(folder / "broken", TomlSource("schema = 1\n[records\n"))
        sound = repository(folder / "sound", TRACKED)
        (folder / "rules").mkdir()
        (folder / "off").mkdir()
        _ = (folder / "rules" / "declared.py").write_text(
            "from pathlib import Path\nfrom declarations.lookup import Declarations\n"
            'EVENTS = frozenset({"Stop"})\nTOOLS = None\n\n'
            f"def judge(event):\n    _ = Declarations(Path({str(folder / 'm.toml')!r})).for_path(Path(event.cwd))\n",
            encoding="utf-8")
        settings = Settings(FolderPath(str(folder / "rules")), FolderPath(str(folder / "off")),
                            ReadSettings(ReaderPath("/no/reader"), Seconds(1.0), {}))

        def stop(cwd: Path) -> EventBytes:
            return EventBytes(json.dumps({"hook_event_name": "Stop", "cwd": str(cwd), "session_id": "s"}).encode())

        got = answer(stop(broken), settings)
        self.assertEqual(got.status, 2)
        self.assertIn(f"guard kit: the declaration {broken / '.claude' / 'guard.toml'} is not TOML", got.stderr)
        self.assertIn(FAULT_TAIL, got.stderr)
        self.assertEqual(answer(stop(sound), settings).status, 0)


if __name__ == "__main__":
    unittest.main()
