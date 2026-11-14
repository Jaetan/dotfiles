"""Python code a call runs, read for the processes it starts, and the words that tell what a Python interpreter runs.

starts parses a Python text with the standard library's ast, never running it, and finds every call of a function
that starts a process: subprocess's run, call, check_call, check_output, Popen, getoutput and getstatusoutput,
os.system, os.popen, the os.exec and os.spawn families, os.posix_spawn and posix_spawnp, asyncio's
create_subprocess_exec and create_subprocess_shell (asyncio.subprocess's too) and pty.spawn, each named through its
module, an `import X as Y` or `import X.Y` binding, or a `from X import Y [as Z]` or `from X import *` one, wherever
in the code it stands, no scope or flow followed. A start is spelled where its program and arguments are literal
strings (string constants and f-strings with no placeholder, in a list or tuple where the function takes one): its
words are what the program receives, the program first, as the process is exec'd: the text a shell runs
(shell=True, os.system, os.popen, getoutput, getstatusoutput, create_subprocess_shell) as `/bin/sh -c TEXT` and the
words after the text, executable= replacing the program (and /bin/sh), an exec, spawn or posix_spawn path replacing
argv[0]. A start's cwd= and env= are read where literal: a cwd= string, a cwd= the code does not make literal
unknown; an env= dict display of string keys (a value not literal unknown), os.environ, os.environ.copy() or
dict(os.environ, K=V), a `**os.environ` entry inheriting with the pairs after it, a pair before it unknown, any
other env= an unknown environment. A start's stdin=, stdout=, stderr=, capture_output=, input= and mode are not
read: where its program's streams end is no part of the reading. After any os.chdir or os.fchdir the code holds, a start without a literal cwd=
runs in an unknown directory, and after any write to os.environ, os.putenv or os.unsetenv a start that inherits the
environment has it unknown. Any other start (a name, a starred argument, a ** keyword, a non-literal shell= or
executable=) is counted unspelled. A text ast cannot parse is a Reading with its fault and no start; the warnings
parsing raises (an invalid escape sequence) are silenced, since the hook's stderr is its refusal.

The same reading gives each while or for loop (async for included) whose body or else clause calls time.sleep or
asyncio.sleep, named as a start function is, wherever in the code it stands, no flow followed: the line it starts on,
and whether it spins, its body only sleeping and checking: pass, continue and break, ifs whose tests and branches
only sleep and check, and expressions and assignments calling nothing but sleeps and time and os.path functions. It
tells too whether the code calls either sleep outside the body and else clause of every loop (in a loop's test, a
comprehension or a function defined outside the loop among them), which makes the interpreter running it a sleep.

planted reads the same way for the files the code writes whose path ends `.py` or is not literal (a literal
string, or a call of one literal string and no keyword as Path("x.py") is): the receiver of any write_text or
write_bytes method, the destination of shutil's copy, copy2, copyfile and move and of os.replace and os.rename, and
the file of the builtin open (io.open and builtins.open too) whose mode holds w, a, x or + or is not literal.

invoked reads a Python interpreter's words as CPython's getopt does: the code -c runs, the module -m runs, the
script operand, or its input; -c and -m end the options and take their value attached or from the next word, and
-I or -P before them keeps the directory off the path a module is found on.
"""

import ast
import warnings
from collections.abc import Iterator, Sequence
from typing import Literal, NamedTuple, NewType

from dispatcher.tree import Count, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName

PythonText = NewType("PythonText", str)
QualName = NewType("QualName", str)
LocalName = NewType("LocalName", str)
KeywordName = NewType("KeywordName", str)
Family = Literal["popen", "shell", "exec_l", "exec_v", "posix_spawn", "async_exec", "pty"]
RunKind = Literal["code", "module", "script", "input"]
LineNumber = NewType("LineNumber", int)

SHELL = ShellText("/bin/sh")
INHERITED = frozenset({QualName("os.environ")})
CHDIRS = frozenset({QualName("os.chdir"), QualName("os.fchdir")})
ENV_WRITES = frozenset({QualName("os.putenv"), QualName("os.unsetenv")})
SLEEPS = frozenset({QualName("time.sleep"), QualName("asyncio.sleep")})
LOOPS = (ast.While, ast.For, ast.AsyncFor)
READS = ("time.", "os.path.")
QUIET = (ast.Pass, ast.Continue, ast.Break, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign)
ENVIRON_METHODS = frozenset({"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"})
ARGS, EXECUTABLE, SHELL_FLAG = KeywordName("args"), KeywordName("executable"), KeywordName("shell")
CWD, ENV = KeywordName("cwd"), KeywordName("env")
POPEN = {ARGS: Count(0), EXECUTABLE: Count(2), SHELL_FLAG: Count(8), CWD: Count(9), ENV: Count(10)}
SHORT_VALUED = frozenset("WX")
STOPS = frozenset("hV?")
SAFE_PATH = frozenset("IP")


class Spec(NamedTuple):
    """How a start function takes its program: its family, the positional arguments before the program, whether its
    last positional argument is the environment, and the keyword naming its shell text where it takes one."""

    family: Family
    skip: Count = Count(0)
    env_last: bool = False
    text_keyword: KeywordName = KeywordName("cmd")


def table() -> dict[QualName, Spec]:
    """Every start function by its qualified name."""
    found: dict[QualName, Spec] = {}
    for name in ("run", "call", "check_call", "check_output", "Popen"):
        found[QualName(f"subprocess.{name}")] = Spec("popen")
    for name in ("subprocess.getoutput", "subprocess.getstatusoutput", "os.popen"):
        found[QualName(name)] = Spec("shell")
    found[QualName("os.system")] = Spec("shell", text_keyword=KeywordName("command"))
    for prefix in ("asyncio", "asyncio.subprocess"):
        found[QualName(f"{prefix}.create_subprocess_shell")] = Spec("shell")
        found[QualName(f"{prefix}.create_subprocess_exec")] = Spec("async_exec")
    for stem, skip in (("exec", 0), ("spawn", 1)):
        for suffix in ("l", "lp", "le", "lpe", "v", "vp", "ve", "vpe"):
            family: Family = "exec_l" if suffix.startswith("l") else "exec_v"
            found[QualName(f"os.{stem}{suffix}")] = Spec(family, Count(skip), suffix.endswith("e"))
    for name in ("os.posix_spawn", "os.posix_spawnp"):
        found[QualName(name)] = Spec("posix_spawn")
    found[QualName("pty.spawn")] = Spec("pty")
    return found


STARTS = table()


class Env(NamedTuple):
    """What a start does to the environment it inherits: ``pairs`` replacing it where ``replaced``, else added on
    top of it, a value None where unknown; ``known`` False where the code does not make it literal."""

    pairs: tuple[tuple[VarName, ShellText | None], ...] = ()
    replaced: bool = False
    known: bool = True


class Start(NamedTuple):
    """A start the code spells: the words its program receives, the directory cwd= names (None where not given),
    whether that directory is known, its environment, and the code of the call as written."""

    words: tuple[ShellText, ...]
    cwd: ShellText | None
    cwd_known: bool
    env: Env
    text: ShellText


class SleepLoop(NamedTuple):
    """A loop of the code that sleeps, as the module docstring describes: the line it starts on, and whether it
    spins."""

    line: LineNumber
    spins: bool


class Reading(NamedTuple):
    """A Python text's starts: those it spells, the count of those it does not, why ast cannot parse it, its loops
    that sleep, in source order, and whether it sleeps outside them."""

    starts: tuple[Start, ...]
    unspelled: Count
    fault: Prose | None = None
    sleeps: tuple[SleepLoop, ...] = ()
    pauses: bool = False


class Run(NamedTuple):
    """What a Python interpreter's words run: the kind, the index of the word holding it, where in that word its
    value starts, and whether -I or -P keeps the directory off the module path."""

    kind: RunKind
    index: Count
    offset: Count = Count(0)
    safe_path: bool = False


def literal(node: ast.expr | None) -> ShellText | None:
    """A string constant or an f-string with no placeholder, None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ShellText(node.value)
    if isinstance(node, ast.JoinedStr):
        parts = [part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        if len(parts) == len(node.values):
            return ShellText("".join(parts))
    return None


def literals(node: ast.expr | None) -> tuple[ShellText, ...] | None:
    """The strings of a list or tuple of literal strings, None where it is not one."""
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    found = [literal(item) for item in node.elts]
    return None if any(item is None for item in found) else tuple(item for item in found if item is not None)


def absent(node: ast.expr | None) -> bool:
    """Whether an argument is not given, or given as None."""
    return node is None or (isinstance(node, ast.Constant) and node.value is None)


def flag(node: ast.expr | None) -> bool | None:
    """A constant's truth, False where absent, None where the code does not make it a constant."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and not isinstance(node.value, str | bytes):
        return bool(node.value)
    return None


class Names:
    """The qualified names the code's imports bind, each local name to every one bound to it."""

    def __init__(self, tree: ast.Module) -> None:
        self.bound: dict[LocalName, set[QualName]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname is None:
                        top = alias.name.partition(".")[0]
                        self.bind(LocalName(top), QualName(top))
                    else:
                        self.bind(LocalName(alias.asname), QualName(alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module is not None and not node.level:
                for alias in node.names:
                    if alias.name == "*":
                        for name in (*STARTS, *SLEEPS):
                            module, _, function = name.rpartition(".")
                            if module == node.module:
                                self.bind(LocalName(function), name)
                    else:
                        self.bind(LocalName(alias.asname or alias.name), QualName(f"{node.module}.{alias.name}"))

    def bind(self, local: LocalName, name: QualName) -> None:
        """Bind ``local`` to ``name`` too."""
        self.bound.setdefault(local, set()).add(name)

    def of(self, node: ast.expr) -> frozenset[QualName]:
        """Every qualified name an expression of names and attributes may stand for."""
        if isinstance(node, ast.Name):
            return frozenset(self.bound.get(LocalName(node.id), ()))
        if isinstance(node, ast.Attribute):
            return frozenset(QualName(f"{base}.{node.attr}") for base in self.of(node.value))
        return frozenset()

    def inherited(self, node: ast.expr) -> bool:
        """Whether an expression is os.environ or os.environ.copy()."""
        if isinstance(node, ast.Call) and not node.args and not node.keywords and isinstance(node.func, ast.Attribute):
            return node.func.attr == "copy" and bool(self.of(node.func.value) & INHERITED)
        return bool(self.of(node) & INHERITED)

    def environment(self, node: ast.expr | None) -> Env:
        """The environment an env= argument gives, as the module docstring describes."""
        if node is None or absent(node) or self.inherited(node):
            return Env()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict" \
                and len(node.args) == 1 and self.inherited(node.args[0]):
            if any(keyword.arg is None for keyword in node.keywords):
                return Env(known=False)
            return Env(tuple((VarName(keyword.arg or ""), literal(keyword.value)) for keyword in node.keywords))
        if not isinstance(node, ast.Dict):
            return Env(known=False)
        pairs: list[tuple[VarName, ShellText | None]] = []
        replaced = True
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                if not self.inherited(value):
                    return Env(known=False)
                replaced = False
                pairs = [(name, None) for name, _ in pairs]
                continue
            name = literal(key)
            if name is None:
                return Env(known=False)
            pairs.append((VarName(name), literal(value)))
        return Env(tuple(pairs), replaced)


def argument(call: ast.Call, keyword: KeywordName, position: Count | None = None) -> ast.expr | None:
    """The argument of ``call`` named ``keyword``, or at ``position`` among its positional ones."""
    if position is not None and position < len(call.args):
        return call.args[position]
    return next((given.value for given in call.keywords if given.arg == keyword), None)


def positional(call: ast.Call, position: Count) -> ast.expr | None:
    """The positional argument of ``call`` at ``position``, None where it has none there."""
    return call.args[position] if 0 <= position < len(call.args) else None


def program_words(spec: Spec, call: ast.Call) -> tuple[ShellText, ...] | None:
    """The words a start's program receives, None where the code does not spell them."""
    args = call.args
    if any(isinstance(arg, ast.Starred) for arg in args) or any(given.arg is None for given in call.keywords):
        return None
    match spec.family:
        case "popen":
            return popen_words(call)
        case "shell":
            text = literal(argument(call, spec.text_keyword, Count(0)))
            return None if text is None else (SHELL, ShellText("-c"), text)
        case "async_exec":
            found = [literal(arg) for arg in args]
            known = tuple(word for word in found if word is not None)
            return known if found and len(known) == len(found) else None
        case "pty":
            given = argument(call, KeywordName("argv"), Count(0))
            text = literal(given)
            return (text,) if text is not None else literals(given) or None
        case "posix_spawn":
            return exec_words(literal(positional(call, Count(0))), literals(positional(call, Count(1))))
        case "exec_l":
            end = len(args) - (1 if spec.env_last else 0)
            found = [literal(arg) for arg in args[spec.skip + 1:end]]
            words = None if any(word is None for word in found) else tuple(w for w in found if w is not None)
            return exec_words(literal(positional(call, spec.skip)), words)
        case "exec_v":
            return exec_words(literal(positional(call, spec.skip)), literals(positional(call, Count(spec.skip + 1))))


def exec_words(path: ShellText | None, argv: tuple[ShellText, ...] | None) -> tuple[ShellText, ...] | None:
    """The words a program run from ``path`` with ``argv`` receives, ``path`` in place of argv[0]."""
    return None if path is None or argv is None else (path, *argv[1:])


def popen_words(call: ast.Call) -> tuple[ShellText, ...] | None:
    """The words a subprocess start's program receives: its args, under its shell= and executable=."""
    shell = flag(argument(call, SHELL_FLAG, POPEN[SHELL_FLAG]))
    given = argument(call, EXECUTABLE, POPEN[EXECUTABLE])
    executable = None if absent(given) else literal(given)
    if shell is None or (not absent(given) and executable is None):
        return None
    args = argument(call, ARGS, POPEN[ARGS])
    text = literal(args)
    words = (text,) if text is not None else literals(args)
    if not words:
        return None
    if shell:
        return (executable or SHELL, ShellText("-c"), *words)
    return words if executable is None else (executable, *words[1:])


def environment_of(spec: Spec, call: ast.Call) -> ast.expr | None:
    """The argument giving a start's environment, None where it has none."""
    if spec.env_last:
        return call.args[-1] if call.args else None
    if spec.family == "posix_spawn":
        return positional(call, Count(2))
    return argument(call, ENV, POPEN[ENV] if spec.family == "popen" else None)


def walked(tree: ast.Module, names: Names) -> tuple[bool, bool]:
    """Whether the code changes its directory, and whether it writes its environment."""
    moves, writes = False, False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = names.of(node.func)
            moves = moves or bool(called & CHDIRS)
            writes = writes or bool(called & ENV_WRITES) or (
                isinstance(node.func, ast.Attribute) and node.func.attr in ENVIRON_METHODS
                and names.inherited(node.func.value))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
            writes = writes or names.inherited(node.value)
    return moves, writes


def idle(names: Names, node: ast.AST) -> bool:
    """Whether ``node`` yields nothing and calls nothing but sleeps and time and os.path functions."""
    return not any(isinstance(sub, ast.Yield | ast.YieldFrom) or (isinstance(sub, ast.Call) and not any(
        name in SLEEPS or name.startswith(READS) for name in names.of(sub.func))) for sub in ast.walk(node))


def spins(names: Names, body: Sequence[ast.stmt]) -> bool:
    """Whether the statements ``body`` only sleep and check, as the module docstring describes."""
    return all(idle(names, statement.test) and spins(names, [*statement.body, *statement.orelse])
               if isinstance(statement, ast.If) else isinstance(statement, QUIET) and idle(names, statement)
               for statement in body)


def naps(names: Names, node: ast.AST) -> bool:
    """Whether ``node`` is a call of time.sleep or asyncio.sleep."""
    return isinstance(node, ast.Call) and bool(names.of(node.func) & SLEEPS)


def looped(loop: ast.While | ast.For | ast.AsyncFor) -> Iterator[ast.AST]:
    """Every node of the body and the else clause of ``loop``."""
    return (sub for statement in (*loop.body, *loop.orelse) for sub in ast.walk(statement))


def sleeping(tree: ast.Module, names: Names) -> tuple[SleepLoop, ...]:
    """Each loop of ``tree`` whose body or else clause calls a sleep, in source order."""
    return tuple(sorted(SleepLoop(LineNumber(node.lineno), spins(names, node.body)) for node in ast.walk(tree)
                        if isinstance(node, LOOPS) and any(naps(names, sub) for sub in looped(node))))


def pausing(tree: ast.Module, names: Names) -> bool:
    """Whether ``tree`` calls a sleep outside the body and else clause of every loop, so outside each loop sleeping()
    gives."""
    inside = {sub for node in ast.walk(tree) if isinstance(node, LOOPS) for sub in looped(node)}
    return any(naps(names, node) and node not in inside for node in ast.walk(tree))


def calls(tree: ast.Module) -> Iterator[ast.Call]:
    """Every call in ``tree``, in source order."""
    found = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    yield from sorted(found, key=lambda node: (node.lineno, node.col_offset))


def parsed(code: PythonText) -> ast.Module | Prose:
    """The Python text ``code`` parsed, its warnings silenced; why ast cannot parse it where it cannot."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(code)
    except (SyntaxError, ValueError, RecursionError) as fault:
        where = f" (line {fault.lineno})" if isinstance(fault, SyntaxError) and fault.lineno else ""
        reason = fault.msg if isinstance(fault, SyntaxError) else str(fault) or type(fault).__name__
        return Prose(f"{reason}{where}")


def starts(code: PythonText) -> Reading:
    """The process starts and the loops that sleep of the Python text ``code``, as the module docstring
    describes."""
    tree = parsed(code)
    if not isinstance(tree, ast.Module):
        return Reading((), Count(0), tree)
    names = Names(tree)
    moves, writes = walked(tree, names)
    spelled: list[Start] = []
    unspelled = 0
    for node in calls(tree):
        spec = next((STARTS[name] for name in sorted(names.of(node.func)) if name in STARTS), None)
        if spec is None:
            continue
        words = program_words(spec, node)
        if words is None:
            unspelled += 1
            continue
        env = names.environment(environment_of(spec, node))
        if writes and not env.replaced:
            env = env._replace(known=False)
        given = argument(node, CWD, POPEN[CWD] if spec.family == "popen" else None)
        cwd = literal(given)
        known = (absent(given) or cwd is not None) and not (moves and cwd is None)
        text = ast.get_source_segment(code, node) or ast.unparse(node)
        spelled.append(Start(words, cwd, known, env, ShellText(text)))
    return Reading(tuple(spelled), Count(unspelled), sleeps=sleeping(tree, names), pauses=pausing(tree, names))


def invoked(values: Sequence[ShellText | None]) -> Run | None:
    """What a Python interpreter whose words have ``values`` runs, None where it runs nothing: -h, -V, --help,
    --version, or a -c or -m with no value. An unknown word ends the options and is the script."""
    index = 1
    safe = False
    while index < len(values):
        value = values[index]
        if value is None:
            return Run("script", Count(index))
        if value == "-":
            return Run("input", Count(index))
        if value == "--":
            return Run("script", Count(index + 1)) if index + 1 < len(values) else Run("input", Count(index))
        if value.startswith("--"):
            if value in ("--help", "--version") or value.startswith("--help-"):
                return None
            index += 2 if value == "--check-hash-based-pycs" else 1
            continue
        if value.startswith("-") and len(value) > 1:
            index += 1
            for position, letter in enumerate(value[1:], 1):
                if letter in STOPS:
                    return None
                safe = safe or letter in SAFE_PATH
                if letter in "cm":
                    kind: RunKind = "code" if letter == "c" else "module"
                    if position + 1 < len(value):
                        return Run(kind, Count(index - 1), Count(position + 1), safe)
                    return Run(kind, Count(index), Count(0), safe) if index < len(values) else None
                if letter in SHORT_VALUED:
                    index += 0 if position + 1 < len(value) else 1
                    break
            continue
        return Run("script", Count(index))
    return Run("input", Count(index))


COPIES = frozenset(QualName(name) for name in ("shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.move",
                                                 "os.replace", "os.rename"))
OPENS = frozenset({QualName("io.open"), QualName("builtins.open")})
WRITE_METHODS = frozenset({"write_text", "write_bytes"})
WRITE_MODES = frozenset("wax+")
DST, FILE, MODE = KeywordName("dst"), KeywordName("file"), KeywordName("mode")


class Written(NamedTuple):
    """A file a call of the code writes: the expression naming it, None where the call does not give it."""

    path: ast.expr | None


def written(names: Names, call: ast.Call) -> Written | None:
    """The file ``call`` writes, as the module docstring says; None where it writes none."""
    func, called = call.func, names.of(call.func)
    if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
        return Written(func.value)
    if called & COPIES:
        return Written(argument(call, DST, Count(1)))
    if (isinstance(func, ast.Name) and func.id == "open") or called & OPENS:
        mode = argument(call, MODE, Count(1))
        text = literal(mode)
        if not absent(mode) and (text is None or not WRITE_MODES.isdisjoint(text)):
            return Written(argument(call, FILE, Count(0)))
    return None


def target(node: ast.expr | None) -> ShellText | None:
    """The path ``node`` names where the code makes it literal, None where it does not."""
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        return literal(node.args[0])
    return literal(node)


def planted(code: PythonText) -> tuple[ShellText, ...]:
    """Each call of the Python text ``code`` that writes a file whose path ends `.py` or is not literal, as written;
    none where ast cannot parse it."""
    module = parsed(code)
    if not isinstance(module, ast.Module):
        return ()
    names = Names(module)
    found: list[ShellText] = []
    for call in calls(module):
        write = written(names, call)
        path = None if write is None else target(write.path)
        if write is not None and (path is None or path.endswith(".py")):
            found.append(ShellText(ast.get_source_segment(code, call) or ast.unparse(call)))
    return tuple(found)
