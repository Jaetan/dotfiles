"""Read Python code for what it writes and what commands it starts.

Used by the guard hooks for code a call hands to Python (`python3 -c`, a
heredoc or here-string, code piped in, a script by path or `-m` module).  The
code is parsed with `ast`; nothing is run.  The reader is bounded by the user's
ruling (2026-09-27): it does not model scopes, argv values or control flow, and
what it cannot place is charged where the code runs, so it errs toward refusing.

  * Imports are followed: `import subprocess as sp`, `from os import system`
    and `from sys import argv` name what they import, and so does `run =
    subprocess.run`.
  * A name's values are every value it is bound to (assignments, annotated
    and augmented ones); a name with any binding the reader cannot evaluate
    is unknown, and so is a loop or `with` target.  Values are built from
    string constants, f-strings, `+`, the `/` of paths, Path(...),
    os.path.join, Path.home(), Path.cwd(), .parent, .resolve(), tempfile's
    directories and the like.  A value known up to an unknown part keeps
    what is known, the rest spelled `${UNKNOWN}`, so a write under a known
    directory is placed there.
  * A write site is a path method that writes (write_text, write_bytes,
    unlink, rmdir, touch, mkdir, rename, symlink_to, hardlink_to, chmod;
    replace on a known path), open() or a module's open() (keywords too) with
    a mode that writes or one it cannot see, os.open with a flag that writes,
    the writing os and shutil calls (os.utime, shutil.unpack_archive
    included), tarfile and zipfile opened to write, extract and extractall,
    and fileinput with inplace.  Each site yields the paths it can be
    evaluated to, or None when it cannot.  After os.chdir, a relative path
    is placed under every directory the code may be in.
  * A command started through subprocess (a list, a name bound to one or to
    several, each a command it may start, a list with starred or runtime
    parts, or a string with shell=True),
    os.system, os.popen or subprocess.getoutput is returned as shell text
    with the directory it runs in, for the caller to read like any other
    call; one the reader cannot read at all is returned as None.
"""

from __future__ import annotations

import ast
import functools
import re
import warnings
import os
import shlex
import tempfile
from dataclasses import dataclass, field

UNKNOWN = "${UNKNOWN}"
_WRITE_METHODS = {"write_text", "write_bytes", "unlink", "rmdir", "touch", "mkdir", "symlink_to", "hardlink_to",
                  "chmod", "rename"}
_OS_FIRST = {"remove", "unlink", "rmdir", "removedirs", "makedirs", "mkdir", "truncate", "chmod", "chown",
             "lchown", "mkfifo", "utime"}
_OS_BOTH = {"rename", "replace", "renames"}
_OS_SECOND = {"symlink", "link"}
_OS_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_EXCL", "O_TMPFILE"}
_SUBPROCESS = {"run", "call", "check_call", "check_output", "Popen"}
_OPEN_MODULES = {"gzip", "bz2", "lzma", "io", "codecs"}
_PATH_CTORS = {"Path", "pathlib.Path", "PurePath", "pathlib.PurePath", "PosixPath", "pathlib.PosixPath",
               "os.path.join", "str", "os.fspath", "os.path.abspath", "os.path.realpath", "os.path.normpath"}
_TEMP_DIRS = {"tempfile.mkdtemp", "tempfile.gettempdir", "tempfile.mktemp", "tempfile.TemporaryDirectory",
              "tempfile.NamedTemporaryFile", "tempfile.mkstemp"}


@dataclass
class Facts:
    sites: list[list[str] | None] = field(default_factory=list)  # each write site's paths, or None: unknown
    # Each command the code starts: its shell text (None when it cannot be read) and the directories it may run
    # in (None: where the code runs; a None among them: somewhere unknown).
    commands: list[tuple[str | None, list[str | None] | None]] = field(default_factory=list)
    uses_argv: bool = False
    parsed: bool = True
    polls: bool = False  # a while or for loop that sleeps: a watcher, as the shell loop is
    sleeps: bool = False  # it sleeps at all: in a shell loop, the loop's delay


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _write_mode(node: ast.AST | None) -> bool:
    """A mode argument that opens for writing: a constant mode with w, a, x or +, or one the reader cannot see."""
    if node is None:
        return False
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return True  # a mode computed at run time may write
    m = re.split(r"[:|]", node.value, maxsplit=1)[0]  # tarfile's 'w:gz' and 'w|xz': the mode before the method
    return bool(m) and set(m) <= set("rwxabtU+") and bool(set(m) & set("wax+"))


def _partial(vals: list[str] | None) -> list[str]:
    return vals if vals is not None else [UNKNOWN]


class _Reader:
    def __init__(self, cwd: str | None) -> None:
        self.cwd = cwd
        self.home = os.environ.get("HOME")
        self.names: dict[str, list[str] | None] = {}
        self.prev: dict[str, list[str] | None] = {}  # the previous pass's bindings, for names bound later
        self.lists: dict[str, list[ast.AST]] = {}
        self.grown: set[str] = set()  # lists extended at run time (append, extend, +=)
        self.alternatives: dict[str, list[list[ast.AST]]] = {}  # every list a name is bound to
        self.alias: dict[str, str] = {}  # a local name for what an import brought in
        self.path_names: set[str] = set()  # names bound to a Path
        self.temp_files: set[str] = set()  # names bound to a tempfile object
        self.chdirs: list[str | None] = []  # directories os.chdir moves to; None: one the reader cannot place
        self.facts = Facts()

    def canon(self, node: ast.AST) -> str:
        """A callee or attribute's dotted name, with its first part resolved through the imports."""
        name = _dotted(node) or ""
        head, dot, rest = name.partition(".")
        if head in self.alias:
            return self.alias[head] + dot + rest
        return name

    # ── values ────────────────────────────────────────────────────────────────

    def value(self, node: ast.AST | None, depth: int = 0) -> list[str] | None:
        if node is None or depth > 20:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            acc = [""]
            for part in node.values:
                if isinstance(part, ast.Constant):
                    vals = [str(part.value)]
                elif isinstance(part, ast.FormattedValue):
                    vals = _partial(self.value(part.value, depth + 1))
                else:
                    return None
                acc = [a + v for a in acc for v in vals][:16]
            return None if all(a.startswith(UNKNOWN) for a in acc) else acc
        if isinstance(node, ast.Name):
            if self.canon(node) != node.id:
                return None
            return self.names[node.id] if node.id in self.names else self.prev.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            left = self.value(node.left, depth + 1)
            if left is None:
                return None  # nothing known from the start
            right = _partial(self.value(node.right, depth + 1))
            join = (lambda a, b: a + b) if isinstance(node.op, ast.Add) else os.path.join
            return [join(a, b) for a in left for b in right][:16]
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            inner = self.value(node.value, depth + 1)
            return None if inner is None else [os.path.dirname(v.rstrip("/")) or "." for v in inner]
        if isinstance(node, ast.Attribute) and node.attr == "name" and isinstance(node.value, ast.Name) \
                and node.value.id in self.temp_files:
            return [os.path.join(tempfile.gettempdir(), "tmp.file")]
        if isinstance(node, ast.Call):
            name = self.canon(node.func)
            if name in ("pathlib.Path.home", "Path.home"):
                return [self.home] if self.home else None
            if name in ("pathlib.Path.cwd", "Path.cwd", "os.getcwd"):
                return [self.cwd] if self.cwd else None
            if name in _TEMP_DIRS:
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                base = self.value(kw.get("dir")) if "dir" in kw else [tempfile.gettempdir()]
                if base is None:
                    return None
                return base if name == "tempfile.gettempdir" else [os.path.join(b, "tmp.x") for b in base]
            if name == "os.path.expanduser" and node.args:
                inner = self.value(node.args[0], depth + 1)
                return None if inner is None else [os.path.expanduser(v) for v in inner]
            if name == "os.path.dirname" and node.args:
                inner = self.value(node.args[0], depth + 1)
                return None if inner is None else [os.path.dirname(v) for v in inner]
            if name in _PATH_CTORS or name.split(".")[-1] in ("Path", "PurePath", "PosixPath") and \
                    name.startswith("pathlib."):
                if not node.args:
                    return ["."]
                acc = self.value(node.args[0], depth + 1)
                if acc is None:
                    return None
                for arg in node.args[1:]:
                    acc = [os.path.join(a, v) for a in acc for v in _partial(self.value(arg, depth + 1))][:16]
                return acc
            if isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in ("resolve", "absolute", "expanduser"):
                    inner = self.value(node.func.value, depth + 1)
                    return None if inner is None else [os.path.expanduser(v) for v in inner]
                if attr in ("with_suffix", "with_name", "with_stem"):
                    inner = self.value(node.func.value, depth + 1)
                    return None if inner is None else [os.path.dirname(v) or "." for v in inner]
                if attr == "joinpath":
                    acc = self.value(node.func.value, depth + 1)
                    if acc is None:
                        return None
                    for arg in node.args:
                        acc = [os.path.join(a, v) for a in acc for v in _partial(self.value(arg, depth + 1))][:16]
                    return acc
        return None

    def pathish(self, node: ast.AST | None) -> bool:
        """Whether an expression is a Path (so `.replace` on it moves a file, not a substring)."""
        if isinstance(node, ast.Name):
            return node.id in self.path_names
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return True
        if isinstance(node, ast.Call):
            name = self.canon(node.func)
            if name in ("Path", "pathlib.Path", "PurePath", "pathlib.PurePath", "PosixPath", "pathlib.PosixPath",
                        "Path.home", "pathlib.Path.home", "Path.cwd", "pathlib.Path.cwd"):
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "resolve", "absolute", "joinpath", "with_suffix", "with_name", "with_stem", "expanduser"):
                return self.pathish(node.func.value)
        return False

    def word_list(self, node: ast.AST | None) -> list[ast.AST] | None:
        """The words of an argv expression as nodes, an unknown word standing for what runs out of sight."""
        if isinstance(node, (ast.List, ast.Tuple)):
            out: list[ast.AST] = []
            for e in node.elts:
                if isinstance(e, ast.Starred):
                    inner = self.word_list(e.value)
                    out += inner if inner is not None else [ast.Constant(UNKNOWN)]
                else:
                    out.append(e)
            return out
        if isinstance(node, ast.Name) and node.id in self.lists and self.canon(node) == node.id:
            return self.lists[node.id] + ([ast.Constant(UNKNOWN)] if node.id in self.grown else [])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.word_list(node.left)
            if left is None:
                return None
            right = self.word_list(node.right)
            return left + (right if right is not None else [ast.Constant(UNKNOWN)])
        if isinstance(node, ast.Call):
            split = self.canon(node.func) == "shlex.split" and node.args
            text = self.value(node.args[0]) if split else (
                self.value(node.func.value) if isinstance(node.func, ast.Attribute) and node.func.attr == "split"
                and not node.args else None)
            if text:
                try:
                    return [ast.Constant(w) for w in (shlex.split(text[0]) if split else text[0].split())]
                except ValueError:
                    return None
        return None

    def bind(self, target: ast.AST, vals: list[str] | None, value_node: ast.AST | None = None) -> None:
        if isinstance(target, ast.Name):
            name = target.id
            if value_node is not None and self.pathish(value_node):
                self.path_names.add(name)
            words = self.word_list(value_node) if value_node is not None else None
            if words is not None:
                self.lists[name] = words
                self.alternatives.setdefault(name, [])
                if words not in self.alternatives[name]:
                    self.alternatives[name].append(words)  # `if flag: cmd = [...] else: cmd = [...]`: both
            if name in self.names:
                old = self.names[name]
                self.names[name] = None if old is None or vals is None else (old + vals)[:32]
            else:
                self.names[name] = vals
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self.bind(elt, None)

    def collect_names(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.alias[a.asname] = a.name
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for a in node.names:
                    if a.name != "*":
                        self.alias[a.asname or a.name] = f"{node.module}.{a.name}"
        for _ in range(2):  # ast.walk is breadth-first: a later binding can feed an earlier-listed one
            self.prev, self.names = self.names, {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    vals = self.value(node.value)
                    callee = self.canon(node.value) if isinstance(node.value, (ast.Name, ast.Attribute)) else ""
                    for t in node.targets:
                        if isinstance(t, ast.Name) and callee.startswith(("subprocess.", "os.", "shutil.")):
                            self.alias[t.id] = callee  # `run = subprocess.run`: a call of run is that call
                        self.bind(t, vals, node.value)
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    self.bind(node.target, self.value(node.value), node.value)
                elif isinstance(node, ast.AugAssign):
                    if isinstance(node.target, ast.Name) and node.target.id in self.lists:
                        self.grown.add(node.target.id)
                    self.bind(node.target, None)
                elif isinstance(node, ast.NamedExpr):
                    self.bind(node.target, self.value(node.value), node.value)
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    self.bind(node.target, None)
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        if item.optional_vars is None:
                            continue
                        ctx = self.canon(item.context_expr.func) if isinstance(item.context_expr, ast.Call) else ""
                        if ctx == "tempfile.TemporaryDirectory":
                            self.bind(item.optional_vars, self.value(item.context_expr))
                        else:
                            if ctx == "tempfile.NamedTemporaryFile" and isinstance(item.optional_vars, ast.Name):
                                self.temp_files.add(item.optional_vars.id)
                            self.bind(item.optional_vars, None)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for a in node.args.args + node.args.kwonlyargs:
                        self.names[a.arg] = None
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                        node.func.attr in ("append", "extend", "insert") and isinstance(node.func.value, ast.Name):
                    self.grown.add(node.func.value.id)

    # ── sites and commands ────────────────────────────────────────────────────

    def places(self, vals: list[str] | None) -> list[str] | None:
        """Where the paths may land once os.chdir has moved the code: under each directory it may be in."""
        if vals is None or not self.chdirs:
            return vals
        out: list[str] = []
        for v in vals:
            if os.path.isabs(v) or v.startswith(UNKNOWN):
                out.append(v)
                continue
            for d in [self.cwd, *self.chdirs]:
                if d is None:
                    return None
                out.append(os.path.join(d, v))
        return out

    def site(self, node: ast.AST | None) -> None:
        self.facts.sites.append(self.places(self.value(node)))

    def shell_text(self, node: ast.AST) -> str:
        """A string node as shell text, each unknown part kept as ${UNKNOWN}."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                vals = self.value(part.value) if isinstance(part, ast.FormattedValue) else (
                    [str(part.value)] if isinstance(part, ast.Constant) else None)
                parts.append(vals[0] if vals else UNKNOWN)
            return "".join(parts)
        vals = self.value(node)
        return vals[0] if vals else UNKNOWN

    def argv_text(self, node: ast.AST) -> str | None:
        """An argv as shell text; None when its program is out of sight."""
        elts = self.word_list(node)
        if not elts:
            return None
        words = []
        for e in elts:
            if self.canon(e) == "sys.executable":
                words.append("python3")
                continue
            vals = self.value(e)
            words.append(vals[0] if vals else UNKNOWN)
        if words[0].startswith(UNKNOWN):
            return None
        return " ".join(shlex.quote(w) if UNKNOWN not in w else w for w in words)

    def run_dirs(self, kw: dict[str, ast.AST]) -> list[str | None] | None:
        """Where a started command runs: its cwd= keyword, else wherever the code may be."""
        if "cwd" in kw:
            vals = self.value(kw["cwd"])
            if vals is None or any(v.startswith(UNKNOWN) for v in vals):
                return [None]
            return [v if os.path.isabs(v) or self.cwd is None else os.path.join(self.cwd, v) for v in vals]
        return [self.cwd, *self.chdirs] if self.chdirs else None

    def calls(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):  # `while ...: time.sleep(...)`, or a for loop: polling written in Python
            if isinstance(node, ast.Call) and self.canon(node.func) in ("time.sleep", "asyncio.sleep"):
                self.facts.sleeps = True
            if isinstance(node, (ast.While, ast.For, ast.AsyncFor)) and any(
                    isinstance(sub, ast.Call) and self.canon(sub.func) in ("time.sleep", "asyncio.sleep")
                    for sub in ast.walk(node)):
                self.facts.polls = True
        for node in ast.walk(tree):  # the directories the code moves to, before the sites they place
            if isinstance(node, ast.Call) and self.canon(node.func) == "os.chdir" and node.args:
                vals = self.value(node.args[0])
                if vals is None or any(v.startswith(UNKNOWN) for v in vals):
                    self.chdirs.append(None)
                else:
                    self.chdirs += [v if os.path.isabs(v) or self.cwd is None else os.path.join(self.cwd, v)
                                    for v in vals]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Subscript, ast.Attribute, ast.Name)) and "sys.argv" in (
                    self.canon(node.value if isinstance(node, ast.Subscript) else node),):
                self.facts.uses_argv = True
            if not isinstance(node, ast.Call):
                continue
            name = self.canon(node.func)
            args = node.args
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            if name in ("argparse.ArgumentParser",) or name.endswith(".parse_args"):
                self.facts.uses_argv = True
            mod, _, fn = name.rpartition(".")
            if name in ("tarfile.open", "zipfile.ZipFile", "tarfile.TarFile"):
                target = args[0] if args else kw.get("name") or kw.get("file")
                if isinstance(target, ast.Call) and self.canon(target.func) in ("io.BytesIO", "io.StringIO"):
                    continue  # an archive in memory writes no file
                mode = args[1] if len(args) > 1 else kw.get("mode")
                if target is not None and (_write_mode(mode) if name != "zipfile.ZipFile" or mode is not None
                                           else False):
                    self.site(target)
                continue
            if isinstance(node.func, ast.Attribute) and mod not in ("os", "shutil", "sys", "subprocess", "os.path"):
                attr = node.func.attr
                recv = node.func.value
                if attr in _WRITE_METHODS:
                    self.site(recv)
                    if attr == "rename" and args:
                        self.site(args[0])
                    continue
                if attr == "replace" and self.pathish(recv):
                    self.site(recv)
                    if args:
                        self.site(args[0])
                    continue
                if attr == "open":
                    if mod in _OPEN_MODULES:
                        target = args[0] if args else kw.get("filename") or kw.get("file")  # gzip's keyword too
                        if target is not None and _write_mode(args[1] if len(args) > 1 else kw.get("mode")):
                            self.site(target)
                    elif _write_mode(args[0] if args else kw.get("mode")):
                        self.site(recv)
                    continue
            if name in ("open", "builtins.open", "io.open", "codecs.open"):
                target = args[0] if args else kw.get("file")
                if target is not None and _write_mode(args[1] if len(args) > 1 else kw.get("mode")):
                    self.site(target)
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("extractall", "extract"):
                place = args[0] if node.func.attr == "extractall" and args else (
                    args[1] if node.func.attr == "extract" and len(args) > 1 else None)
                self.site(place or kw.get("path") or ast.Constant("."))
                continue
            if mod == "os":
                if fn in _OS_FIRST and args:
                    self.site(args[0])
                elif fn in _OS_BOTH and len(args) > 1:
                    self.site(args[0])
                    self.site(args[1])
                elif fn in _OS_SECOND and len(args) > 1:
                    self.site(args[1])
                elif fn == "open" and args:
                    flags = args[1] if len(args) > 1 else kw.get("flags")
                    named = {n.attr for n in ast.walk(flags) if isinstance(n, ast.Attribute)} if flags else set()
                    opaque = flags is not None and any(isinstance(n, (ast.Name, ast.Constant, ast.Call))
                                                       for n in ast.walk(flags))
                    if named & _OS_WRITE_FLAGS or opaque:
                        self.site(args[0])
                elif fn in ("system", "popen") and args:
                    text = self.shell_text(args[0])
                    self.facts.commands.append((None if text.startswith(UNKNOWN) else text, self.run_dirs({})))
                elif re.fullmatch(r"(exec|spawn)[lv]p?e?", fn) and args:
                    # os.execvp(file, argv), os.spawnlp(mode, file, arg0, ...): the argv runs, in its list or words
                    rest = args[1:] if fn.startswith("spawn") else args
                    listed = rest[1] if fn.endswith(("v", "vp", "ve", "vpe")) and len(rest) > 1 else None
                    words = self.word_list(listed) if listed is not None else [*rest[1:]] if len(rest) > 1 else None
                    text = self.argv_text(ast.List(words)) if words else None
                    self.facts.commands.append((text, self.run_dirs({})))
                continue
            if mod == "shutil":
                if fn.startswith("copy") and len(args) > 1:
                    self.site(args[1])
                elif fn == "unpack_archive":
                    self.site(args[1] if len(args) > 1 else kw.get("extract_dir") or ast.Constant("."))
                elif fn == "make_archive" and args:
                    self.site(args[0])  # base_name: the archive is written beside it
                elif fn == "move" and len(args) > 1:
                    self.site(args[0])
                    self.site(args[1])
                elif fn == "rmtree" and args:
                    self.site(args[0])
                continue
            if name == "fileinput.input":
                inplace = kw.get("inplace")
                if isinstance(inplace, ast.Constant) and inplace.value:
                    files = args[0] if args else kw.get("files")
                    if files is None:
                        self.facts.uses_argv = True  # no files named: the ones on its command line
                    self.site(files)
                continue
            if mod == "subprocess" and fn in _SUBPROCESS | {"getoutput", "getstatusoutput"}:
                first = args[0] if args else kw.get("args")
                if first is None:
                    continue
                dirs = self.run_dirs(kw)
                shell = kw.get("shell")
                if fn in ("getoutput", "getstatusoutput") or (isinstance(shell, ast.Constant) and shell.value):
                    text = self.shell_text(first)
                    self.facts.commands.append((None if text.startswith(UNKNOWN) else text, dirs))
                    continue
                alternatives = self.alternatives.get(first.id, []) if isinstance(first, ast.Name) else []
                if len(alternatives) > 1:  # a list chosen at run time: every one it may be
                    for words in alternatives:
                        self.facts.commands.append((self.argv_text(ast.List(words)), dirs))
                    continue
                text = self.argv_text(first)
                if text is None and isinstance(first, (ast.Constant, ast.JoinedStr)):
                    text = shlex.quote(self.shell_text(first))
                if text is not None and "executable" in kw:  # the program run is executable=, argv[0] its name
                    exe = self.value(kw["executable"])
                    try:
                        rest = shlex.split(text)[1:]
                    except ValueError:  # an argv whose known part holds a lone quote: not a command to read
                        rest = None
                    text = shlex.join([exe[0]] + rest) if exe and UNKNOWN not in exe[0] and rest is not None \
                        else None
                self.facts.commands.append((text, dirs))


@functools.lru_cache(maxsize=64)
def read_python(code: str, cwd: str | None) -> Facts:
    """What Python code writes and starts; Facts.parsed is False when it does not parse.  Read once per text."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # `\\d` in a plain string: Python's warning, not the call's business
            tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return Facts(parsed=False)
    reader = _Reader(cwd)
    try:
        reader.collect_names(tree)
        reader.calls(tree)
    except RecursionError:  # code Python runs that is too deep for this reader: a write and a command unseen
        return Facts(sites=[None], commands=[(None, None)])
    return reader.facts
