#!/home/nicolas/.local/bin/python3.14
"""Hold every check that plants a change in a Python file and restores it to fresh bytecode.

THE RULE (user, 2026-10-24, "let's make 'clear the saved copies' a rule"): a
check that edits a Python file to plant a defect, runs tests, and puts the file
back runs every pass on bytecode no earlier pass saved: a new empty
PYTHONPYCACHEPREFIX where each pass's test run starts, or the planted modules'
.pyc deleted before each pass and after the last restore. CPython reuses a .pyc
whose recorded source mtime, in whole seconds, and size match the source, so a
plant that keeps the file's size, restored within the second, keeps running: a
pass then tests the previous plant, and the tree keeps the planted bytecode
after the run. PYTHONDONTWRITEBYTECODE and -B stop writing, not reading, so
neither is a guard. A single edit is no plant: its .pyc records the old
source's mtime, and the next import recompiles.

WHY A HOOK AND NOT A NOTE. The rule was a memory from 2026-10-02, and it was
missed on 2026-10-09, 2026-10-16 and 2026-10-24: each time the script that
planted the change was written without it ("then let's have a hook for this").

BEFORE A CALL (PreToolUse, Bash). The call is read with _commands.expand, the
scripts it runs and the Python it hands an interpreter included, except Python
the repository tracks (a script git tracks, a module run with -m), which is
reviewed code. It plants when it puts a .py back after changing it:
  * a .py the shell edits (sed or perl in place, a redirection or tee onto it)
    or Python writes, which git checkout or git restore then names, or git
    stash puts back, or cp or mv copies back from a copy the call made of it;
  * a .py sed or perl edits in place twice, a plant and its reverse;
  * Python code writing back a file's original text: a name bound only to a
    read of a file (read_text, read_bytes, read), or taken from a dict of such
    reads, handed to write_text, write_bytes or write; the file is the .py the
    write names, or, where the reader cannot place it, any the code names.
It tests when it starts pytest, py.test, or python -m pytest or -m unittest,
from the shell or from Python code (a command the reader reads; where it cannot
read one, or the code calls pytest.main or unittest.main, the word in the code).
A call that plants and tests is refused (exit 2) unless every test run has a
guard where it starts: a shell run its own PYTHONPYCACHEPREFIX assignment; a
run Python code starts, that code naming PYTHONPYCACHEPREFIX or deleting
__pycache__ or .pyc files, read in its text, since the reader does not follow
the environment a subprocess is given. A shell deletion of __pycache__ or .pyc
(rm, find -delete or -exec rm) anywhere in the call guards every run. A prefix
set on the command that starts a plant script does not guard the runs inside
it, which share it pass after pass.

AFTER A CALL (PostToolUse, Bash). After a call that plants and tests, guarded
or not, the git work tree it ran in, and each one holding a .py it planted, is
scanned: a tracked .py whose __pycache__ entry this interpreter would accept
(its magic, a timestamp check, and the source's mtime second and size) while
its code differs from a fresh compile of the source is planted bytecode left
behind. Each is named with exit 2.

An error of the hook's own is reported (exit 1) and the call goes ahead, as the
other guards here do; a call the reader cannot follow goes ahead unchecked.
"""

import ast
import collections
import importlib.util
import json
import marshal
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType
from typing import Literal, NewType

import _commands
import _python
import _shell

# The text of a Bash call or of a command Python code starts; a directory or a
# path as the call or the event spells it; one word of a command line; Python
# source a call hands an interpreter; a name Python code binds; how many test
# runs a call starts; a line the hook prints; the hook's answer: 0 lets the call
# through, 1 reports an error of its own, 2 reports a finding.
CallText = NewType("CallText", str)
Directory = NewType("Directory", str)
FilePath = NewType("FilePath", str)
CommandWord = NewType("CommandWord", str)
Source = NewType("Source", str)
Identifier = NewType("Identifier", str)
RunCount = NewType("RunCount", int)
Prose = NewType("Prose", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Arm = Literal["PreToolUse", "PostToolUse"]

PREFIX = "PYTHONPYCACHEPREFIX"
TEST_PROGRAMS = frozenset({"pytest", "py.test"})
TEST_MODULES = frozenset({"pytest", "unittest"})
IN_PLACE_EDITORS = frozenset({"sed", "perl"})
COPIERS = frozenset({"cp", "mv", "install"})
RESTORERS = frozenset({"checkout", "restore"})
STASHERS = frozenset({"push", "pop", "apply"})
WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>"})
READ_METHODS = frozenset({"read_text", "read_bytes", "read"})
WRITE_METHODS = frozenset({"write_text", "write_bytes", "write"})
# The interpreter options that take a value, so the value is not read as the script.
VALUED_OPTIONS = frozenset({"-W", "-X", "-Q"})
# A short-option cluster holding -i: sed -i, sed -i.bak, perl -pi, perl -pie.
IN_PLACE_FLAG = re.compile(r"-[A-Za-z]*i[A-Za-z.]*|--in-place(=.*)?")
PY_LITERAL = re.compile(r"""\.py["']""")
TEST_WORD = re.compile(r"\b(pytest|unittest)\b")
IN_PROCESS_RUN = re.compile(r"\b(pytest|unittest)\.main\(")
BYTECODE = re.compile(r"__pycache__|\.pyc\b")
DELETES = re.compile(r"\bunlink\(|\brmtree\(|\bremove\(|\brm\b|-delete\b")
UNPLACED = FilePath("a .py the code names, through a path the reader cannot place")
# A work tree past this many tracked .py files is not scanned whole: the scan
# would cost seconds per call.
MAX_SCANNED = 20000
GIT_SECONDS = 20

REFUSAL = """BLOCKED: this call plants a change in a Python file and runs tests without fresh bytecode for every pass.
{plants}
{runs}
Python reuses a .pyc whose source keeps its size and its mtime second, so a plant
restored within the second keeps running: a pass then tests the previous plant, and
the tree keeps the planted bytecode (USER RULE 2026-10-24). Give every pass fresh
bytecode where its test run starts:
  * a shell run: PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest ...
  * a run Python starts: env=os.environ | {{"PYTHONPYCACHEPREFIX": tempfile.mkdtemp()}}, new per pass
  * or delete the planted modules' __pycache__ entries before each pass and after the last restore.
PYTHONDONTWRITEBYTECODE and -B stop writing, not reading: neither is a guard. A prefix
set on the command that starts a plant script does not reach the passes inside it."""

LEFT_BEHIND = """bytecode-plant-guard: this call planted a change in a Python file, and the tree now holds
bytecode Python would run in place of its source:
{stale}
Each .pyc keeps the source's size and mtime second but not its code: a plant restored
within the second. Delete each (rm PATH) and run again every pass that ran on it
(USER RULE 2026-10-24)."""


@dataclass
class Reading:
    """What a call edits and puts back, the test runs it starts and whether each has a guard."""

    edits: set[FilePath] = field(default_factory=set[FilePath])
    in_place: collections.Counter[FilePath] = field(default_factory=collections.Counter[FilePath])
    restores: set[FilePath] = field(default_factory=set[FilePath])
    copies: set[tuple[FilePath, FilePath]] = field(default_factory=set[tuple[FilePath, FilePath]])
    written_back: list[FilePath] = field(default_factory=list[FilePath])
    stashes: bool = False
    unguarded: list[Prose] = field(default_factory=list[Prose])
    runs: RunCount = RunCount(0)
    deletes_bytecode: bool = False

    @property
    def planted_paths(self) -> list[FilePath]:
        """Return each .py the call changes and puts back."""
        restored = self.restores | ({*self.edits} if self.stashes else set[FilePath]())
        found = {path for path in self.edits if path in restored}
        found |= {path for path, count in self.in_place.items() if count > 1}
        found |= set(self.written_back)
        return sorted(found)

    @property
    def plants(self) -> list[Prose]:
        """Return one line per .py the call plants and puts back."""
        return [Prose(f"  * plants and puts back {path}") for path in self.planted_paths]

    @property
    def shaped(self) -> bool:
        """Say whether the call plants and tests, guarded or not."""
        return bool(self.planted_paths) and self.runs > 0

    @property
    def refused(self) -> bool:
        """Say whether the call plants and tests with a run no guard reaches."""
        return self.shaped and bool(self.unguarded) and not self.deletes_bytecode


def _spelled(word: _shell.Word) -> Prose:
    """Return the word as the command receives it where known, as written where not."""
    return Prose(word.value if word.value is not None else (word.partial or word.text))


def _argv(inv: _commands.Inv) -> list[CommandWord]:
    """Return an invocation's command line with its wrappers peeled off."""
    return [CommandWord(word) for word in inv.core.argv]


def _cwd(inv: _commands.Inv) -> Directory | None:
    """Return the directory an invocation runs in, where the reader knows it."""
    return Directory(inv.cmd.cwd) if inv.cmd.cwd is not None else None


def _placed(path: FilePath, cwd: Directory | None) -> FilePath:
    """Return a path made absolute against the directory the command ran in, where that is known."""
    if os.path.isabs(path) or cwd is None:
        return path
    return FilePath(os.path.normpath(os.path.join(cwd, path)))


def starts_tests(argv: Sequence[CommandWord]) -> bool:
    """Say whether a command line runs Python tests: pytest, py.test, python -m pytest or -m unittest."""
    if not argv:
        return False
    if _commands.prog(argv[0]) in TEST_PROGRAMS:
        return True
    if not _commands.is_python(argv[0]):
        return False
    for index, word in enumerate(argv[1:], 1):
        if word == "-m":
            return index + 1 < len(argv) and argv[index + 1] in TEST_MODULES
        if word.startswith("-m"):
            return word[2:] in TEST_MODULES
        if not word.startswith("-"):
            return False
    return False


def _split(text: CallText) -> list[CommandWord]:
    """Return a command's words as the shell splits them, none where it cannot."""
    try:
        return [CommandWord(word) for word in shlex.split(text)]
    except ValueError:
        return []


def _read_shell(inv: _commands.Inv, reading: Reading) -> None:
    """Record what a shell command edits, puts back, copies and stashes."""
    argv = _argv(inv)
    if not argv:
        return
    name = _commands.prog(argv[0])
    cwd = _cwd(inv)

    def place(word: CommandWord | Prose) -> FilePath:
        return _placed(FilePath(word), cwd)

    if name in IN_PLACE_EDITORS and any(IN_PLACE_FLAG.fullmatch(word) for word in argv[1:]):
        for word in argv[1:]:
            if word.endswith(".py"):
                reading.edits.add(place(word))
                reading.in_place[place(word)] += 1
    if name == "tee":
        reading.edits.update(place(word) for word in argv[1:] if word.endswith(".py"))
    for redirect in inv.cmd.redirects:
        if redirect.op in WRITE_REDIRECTS and redirect.target is not None:
            target = _spelled(redirect.target)
            if target.endswith(".py"):
                reading.edits.add(place(target))
    if name in COPIERS:
        operands = [word for word in argv[1:] if not word.startswith("-")]
        if len(operands) == 2:
            source, target = place(operands[0]), place(operands[1])
            if target.endswith(".py") and (target, source) in reading.copies:
                reading.restores.add(target)
            reading.copies.add((source, target))
    if name == "git" and RESTORERS & set(argv[1:]):
        reading.restores.update(place(word) for word in argv[1:] if word.endswith(".py"))
    if name == "git" and "stash" in argv[1:]:
        rest = argv[argv.index(CommandWord("stash")) + 1:]
        reading.stashes |= not rest or rest[0] in STASHERS or rest[0].startswith("-")


def _tracked_code(inv: _commands.Inv) -> bool:
    """Say whether the Python an invocation runs is the repository's: a -m module, or a script git tracks."""
    if inv.code_origin != "repo-script":
        return False
    words = iter(_argv(inv)[1:])
    for word in words:
        if word == "-m" or word.startswith("-m"):
            return True
        if word in VALUED_OPTIONS:
            next(words, None)
            continue
        if word.startswith("-"):
            continue
        script = Path(_placed(FilePath(word), _cwd(inv)))
        listed = subprocess.run(
            ["git", "-C", str(script.parent), "ls-files", "--error-unmatch", "--", script.name],
            capture_output=True, timeout=GIT_SECONDS, check=False,
        )
        return listed.returncode == 0
    return False


def _shell_deletes_bytecode(inv: _commands.Inv) -> bool:
    """Say whether a shell command deletes bytecode: rm of a __pycache__ or .pyc, find with -delete or -exec rm."""
    argv = _argv(inv)
    name = _commands.prog(argv[0]) if argv else ""
    names_bytecode = any(BYTECODE.search(word) for word in argv[1:])
    if name == "rm":
        return names_bytecode
    if name == "find":
        removes = "-delete" in argv or any(_commands.prog(word) == "rm" for word in argv[1:])
        return names_bytecode and removes
    return False


def _guarded_shell_run(inv: _commands.Inv) -> bool:
    """Say whether a shell test run sets its own bytecode prefix: an assignment on it, or through env."""
    if PREFIX in inv.cmd.assigns:
        return True
    return any(_spelled(word).startswith(PREFIX + "=") for word in inv.cmd.words)


def _python_guard(source: Source) -> bool:
    """Say whether Python code names a fresh bytecode prefix or deletes bytecode, read in its text."""
    return PREFIX in source or (BYTECODE.search(source) is not None and DELETES.search(source) is not None)


def _python_writes(facts: _python.Facts) -> list[FilePath]:
    """Return the .py paths of Python code's write sites the reader places."""
    return [
        FilePath(path) for site in facts.sites if site is not None for path in site
        if path.endswith(".py") and _python.UNKNOWN not in path
    ]


def _is_read(node: ast.expr) -> bool:
    """Say whether an expression reads a file's text: a call of read_text, read_bytes or read."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in READ_METHODS


def _reads_dict(node: ast.expr) -> bool:
    """Say whether an expression is a dict of file reads: a literal or a comprehension of them."""
    if isinstance(node, ast.Dict):
        return bool(node.values) and all(_is_read(value) for value in node.values)
    return isinstance(node, ast.DictComp) and _is_read(node.value)


def _bindings(tree: ast.Module) -> list[tuple[Identifier, ast.expr | None]]:
    """Return every name a module binds with the value bound, None where a loop or another form binds it."""
    found: list[tuple[Identifier, ast.expr | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.append((Identifier(target.id), node.value))
                else:
                    found += [(Identifier(name.id), None) for name in ast.walk(target) if isinstance(name, ast.Name)]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name):
            value = node.value if isinstance(node, (ast.AnnAssign, ast.NamedExpr)) else None
            found.append((Identifier(node.target.id), value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension, ast.With, ast.withitem)):
            targets = [node.target] if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) else (
                [node.optional_vars] if isinstance(node, ast.withitem) and node.optional_vars else [])
            found += [(Identifier(name.id), None) for target in targets for name in ast.walk(target)
                      if isinstance(name, ast.Name)]
    return found


def _originals(tree: ast.Module) -> tuple[set[Identifier], set[Identifier]]:
    """Return the names bound only to a file's text, and the names bound only to a dict of such reads.

    A name taken from such a dict (by subscript or get, or as the value a loop
    over its items or values binds) holds an original too.
    """
    bound = _bindings(tree)
    dicts = {name for name, _ in bound} - {name for name, value in bound if value is None or not _reads_dict(value)}

    def from_dicts(value: ast.expr) -> bool:
        if isinstance(value, ast.Subscript):
            return isinstance(value.value, ast.Name) and value.value.id in dicts
        return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "get"
                and isinstance(value.func.value, ast.Name) and value.func.value.id in dicts)

    loop_values: set[Identifier] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            continue
        iterated = node.iter
        if not (isinstance(iterated, ast.Call) and isinstance(iterated.func, ast.Attribute)
                and isinstance(iterated.func.value, ast.Name) and iterated.func.value.id in dicts):
            continue
        if iterated.func.attr == "values" and isinstance(node.target, ast.Name):
            loop_values.add(Identifier(node.target.id))
        if iterated.func.attr == "items" and isinstance(node.target, ast.Tuple) and len(node.target.elts) == 2:
            value = node.target.elts[1]
            loop_values |= {Identifier(name.id) for name in ast.walk(value) if isinstance(name, ast.Name)}
    texts = {name for name, _ in bound} - {
        name for name, value in bound
        if not (value is not None and (_is_read(value) or from_dicts(value))) and name not in loop_values
    }
    return texts, dicts


def _literal_path(node: ast.expr, bound: dict[Identifier, list[ast.expr | None]]) -> FilePath | None:
    """Return the path a write names when it is a string or a Path() of one, through one name at most."""
    if isinstance(node, ast.Name):
        values = bound.get(Identifier(node.id), [])
        node = values[0] if len(values) == 1 and values[0] is not None else node
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path"
            and len(node.args) == 1):
        node = node.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return FilePath(node.value)
    return None


def _written_back(source: Source) -> list[FilePath]:
    """Return the files Python code writes its original text back to: the .py it names, or UNPLACED."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    texts, dicts = _originals(tree)
    bound: dict[Identifier, list[ast.expr | None]] = collections.defaultdict(list[ast.expr | None])
    for name, value in _bindings(tree):
        bound[name].append(value)
    found: list[FilePath] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in WRITE_METHODS and node.args):
            continue
        data = node.args[0]
        original = (isinstance(data, ast.Name) and data.id in texts) or (
            isinstance(data, ast.Subscript) and isinstance(data.value, ast.Name) and data.value.id in dicts)
        if not original:
            continue
        path = _literal_path(node.func.value, bound)
        if path is not None and path.endswith(".py"):
            found.append(path)
        elif path is None and PY_LITERAL.search(source):
            found.append(UNPLACED)
    return found


def _python_tests(source: Source, facts: _python.Facts) -> bool:
    """Say whether Python code runs tests: a command it starts that does, or the word where it cannot be read."""
    if any(text is not None and starts_tests(_split(CallText(text))) for text, _ in facts.commands):
        return True
    if IN_PROCESS_RUN.search(source):
        return True
    unread = any(text is None for text, _ in facts.commands)
    return unread and TEST_WORD.search(source) is not None


def read_call(text: CallText, cwd: Directory) -> Reading:
    """Read what a call plants and puts back, and the test runs it starts, each with its guard."""
    reading = Reading()
    for inv in _commands.expand(text, cwd):
        argv = _argv(inv)
        where = Prose(" ".join(argv[:4]))
        _read_shell(inv, reading)
        reading.deletes_bytecode |= _shell_deletes_bytecode(inv)
        if starts_tests(argv):
            reading.runs = RunCount(reading.runs + 1)
            if not _guarded_shell_run(inv):
                reading.unguarded.append(Prose(f"  * runs tests with no {PREFIX} of its own: {where}"))
        if inv.code is None or inv.code[0] != "python" or _tracked_code(inv):
            continue
        source = Source(inv.code[1])
        facts = _commands.python_facts(source, inv.cmd.cwd)
        reading.edits.update(_placed(path, _cwd(inv)) for path in _python_writes(facts))
        reading.written_back += [
            path if path == UNPLACED else _placed(path, _cwd(inv)) for path in _written_back(source)
        ]
        if _python_tests(source, facts):
            reading.runs = RunCount(reading.runs + 1)
            if not _python_guard(source):
                reading.unguarded.append(
                    Prose(f"  * Python code run by {where} starts tests and neither names {PREFIX} "
                          "nor deletes bytecode")
                )
    return reading


def _work_tree(start: Path) -> Path | None:
    """Name the git work tree holding ``start``, or None outside any."""
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _tracked_sources(tree: Path) -> list[Path]:
    """Return the tracked .py files of a work tree, none past MAX_SCANNED."""
    listed = subprocess.run(
        ["git", "-C", str(tree), "ls-files", "-z", "--", "*.py"],
        capture_output=True, timeout=GIT_SECONDS, check=False,
    )
    if listed.returncode != 0:
        return []
    names = [name for name in listed.stdout.decode("utf-8", "surrogateescape").split("\0") if name]
    return [] if len(names) > MAX_SCANNED else [tree / name for name in names]


def stale_bytecode(source: Path) -> bool:
    """Say whether ``source``'s __pycache__ entry is one Python would run that is not the source's code."""
    cached = source.parent / "__pycache__" / Path(importlib.util.cache_from_source(str(source))).name
    try:
        data = cached.read_bytes()
        state = source.stat()
    except OSError:
        return False
    if len(data) < 16 or data[:4] != importlib.util.MAGIC_NUMBER:
        return False
    if int.from_bytes(data[4:8], "little") != 0:  # a hash-checked .pyc is validated by content
        return False
    recorded = (int.from_bytes(data[8:12], "little"), int.from_bytes(data[12:16], "little"))
    if (int(state.st_mtime) & 0xFFFFFFFF, state.st_size & 0xFFFFFFFF) != recorded:
        return False  # Python would recompile it
    try:
        held = marshal.loads(data[16:])
        fresh = compile(source.read_bytes(), str(source), "exec", dont_inherit=True)
    except (ValueError, EOFError, TypeError, SyntaxError, OSError):
        return False
    return not isinstance(held, CodeType) or held != fresh


def left_behind(reading: Reading, cwd: Directory) -> list[Path]:
    """Return the tracked sources whose bytecode a planting call left planted, in each tree it reached."""
    trees: set[Path] = set()
    for start in [Path(cwd), *(Path(path) for path in reading.planted_paths if path != UNPLACED)]:
        tree = _work_tree(start)
        if tree is not None:
            trees.add(tree)
    return [source for tree in sorted(trees) for source in _tracked_sources(tree) if stale_bytecode(source)]


def answer(arm: Arm, text: CallText, cwd: Directory) -> tuple[ExitStatus, Prose]:
    """Read the call and answer it for the arm the event names."""
    reading = read_call(text, cwd)
    if arm == "PreToolUse":
        if reading.refused:
            said = REFUSAL.format(plants="\n".join(reading.plants), runs="\n".join(reading.unguarded))
            return REFUSE, Prose(said)
        return ALLOW, Prose("")
    if not reading.shaped:
        return ALLOW, Prose("")
    stale = left_behind(reading, cwd)
    if stale:
        return REFUSE, Prose(LEFT_BEHIND.format(stale="\n".join(f"  * {path}" for path in stale)))
    return ALLOW, Prose("")


def main() -> ExitStatus:
    """Read the hook event on stdin and answer it."""
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"bytecode-plant-guard: the event could not be read, the call goes ahead: {exc}", file=sys.stderr)
        return REPORTED
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return ALLOW
    arm = event.get("hook_event_name")
    if arm not in ("PreToolUse", "PostToolUse"):
        return ALLOW
    tool_input = event.get("tool_input")
    text = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(text, str) or not text.strip():
        return ALLOW
    raw_cwd = event.get("cwd")
    cwd = Directory(raw_cwd if isinstance(raw_cwd, str) and raw_cwd else os.getcwd())
    try:
        status, said = answer(arm, CallText(text), cwd)
    except (_shell.ParseError, _shell.BudgetExceeded) as exc:
        print(f"bytecode-plant-guard: this call could not be read, it goes ahead unchecked: {exc}", file=sys.stderr)
        return ALLOW
    except Exception as exc:  # noqa: BLE001 - a fault of the reader: reported, never a silent block
        print(f"bytecode-plant-guard: internal error, the call goes ahead: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return REPORTED
    if said:
        print(said, file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
