"""A call that writes a Python file and runs Python tests leaves no planted bytecode in a work tree it reaches.

THE RULE (user, 2026-10-24, ported blunt 2026-11-09). CPython runs a module's __pycache__ entry while its source's
mtime in whole seconds and its size are the ones the entry records, so a plant restored within the second it was
written, to its old size, keeps running in every later test run. So after a Bash call that writes a `.py` file and
starts a Python test run, as rules/bytecode_plants.py reads both, each git work tree holding the directory of a
command of its call model, or a `.py` file a shell write of the call names, is scanned. The call is refused naming
each tracked `.py` (git ls-files) whose entry CPython would run, its magic number this interpreter's, its flags 0 and
its recorded mtime second and size the source's, while its code is not a fresh compile of the source, or cannot be
shown to be one; the refusal names the entry to delete. A tree git cannot list is refused as the kit's fault.

EXCEPTIONS: none.
"""

import marshal
import struct
import subprocess
from collections.abc import Iterator
from importlib.util import MAGIC_NUMBER, cache_from_source
from pathlib import Path
from shlex import join

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import Call
from dispatcher.verdict import Prose, Verdict, refuse
from dispatcher.writes import writes
from rules.bytecode_plants import PY, python_writes, tested
from rules.pr_watch import tree

EVENTS: frozenset[HookEvent] = frozenset({"PostToolUse"})
TOOLS = frozenset({ToolName("Bash")})
HEADER = struct.Struct("<4sIII")
MASK = 0xFFFFFFFF
CACHE = "__pycache__"
GIT_SECONDS = 30.0
ADVICE = """this call wrote a Python file and ran Python tests, and left a __pycache__ entry CPython runs in place of
its source: the entry records the source's mtime second and size, so every later test run imports it, but its code
is not the source's.

{stale}

Delete each entry named, so the next import compiles the source:
  rm {entries}"""


def entry(source: Path) -> Path:
    """The __pycache__ entry beside ``source`` this interpreter imports it from."""
    return source.parent / CACHE / Path(cache_from_source(str(source))).name


def stale(source: Path) -> bool:
    """Whether CPython would run ``source``'s entry while its code is not, or cannot be shown to be, the source's."""
    try:
        data = entry(source).read_bytes()
        state = source.stat()
    except OSError:
        return False
    if data[:HEADER.size] != HEADER.pack(MAGIC_NUMBER, 0, int(state.st_mtime) & MASK, state.st_size & MASK):
        return False
    try:
        return marshal.loads(data[HEADER.size:]) != compile(source.read_bytes(), source, "exec", dont_inherit=True)
    except (ValueError, EOFError, SyntaxError, OSError):
        return True


def listed(root: Path) -> list[Path]:
    """The tracked `.py` files of the work tree ``root``."""
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", f"*{PY}"], capture_output=True,
                          timeout=GIT_SECONDS, check=True)
    return [root / name for name in done.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def reached(call: Call) -> Iterator[Path]:
    """The folders ``call`` reaches: each command's directory, and the folder of each `.py` file a shell write names."""
    for command in call.commands:
        if command.cwd is not None:
            yield Path(command.cwd)
        yield from (Path(write.path).parent for write in writes(command)
                    if write.path is not None and write.path.endswith(PY))


def judge(event: Event) -> Verdict | None:
    """Refuse a Bash call that writes a `.py` file and starts a Python test run where a work tree it reaches holds a
    tracked source whose entry CPython runs in place of it, naming each source and its entry."""
    call = event.call()
    if not (call.unspelled or any(map(tested, call.commands))) or next(python_writes(call), None) is None:
        return None
    roots = sorted({root for root in map(tree, reached(call)) if (root / ".git").exists()})
    found = [source for root in roots for source in listed(root) if stale(source)]
    lines = "\n".join(f"  * {source} runs {entry(source)}" for source in found)
    return refuse(Prose(ADVICE.format(stale=lines, entries=join(map(str, map(entry, found)))))) if found else None
