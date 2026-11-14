"""A call that writes a Python file and runs Python tests gives them a fresh bytecode cache, and removes it.

THE RULE (user, 2026-10-24 and 2026-11-03, ported blunt 2026-11-09). CPython reuses a module's .pyc while its source's
mtime in whole seconds and its size match, so a plant restored within the second it was written keeps running; and a
cache folder a call leaves behind stays on the tmpfs charged to the session's slice (556 of them held 8.4 GB and
systemd-oomd killed a call). So a Bash or Monitor call is refused where:
  * it writes a `.py` file and starts a Python test run, and gives PYTHONPYCACHEPREFIX no value;
  * it gives PYTHONPYCACHEPREFIX a value and does not both make a folder and remove one: run mktemp and an rm with
    -r, -R or --recursive, or spell tempfile's TemporaryDirectory, or mkdtemp and rmtree.
The call gives the prefix a value where its text, or the text of a command of its call model, scripts and Python
starts included, spells PYTHONPYCACHEPREFIX= or the name quoted, as Python's env= does; the model keeps no assignment,
so the text is read, and TemporaryDirectory, mkdtemp and rmtree are read there and in every Python text the model
read. Which folder is made and which removed is not read.

A `.py` write is a word ending `.py` that dispatcher/writes.py's writes() names for a command of the call model, or a
write dispatcher/python.py's planted() finds in the Python code the call carries (Call.python's carried texts: a -c
text, an interpreter's input, a script or module file the call writes).
A Python test run is a command of the call model, Python starts included, named pytest or py.test, a Python
interpreter running `-m pytest` or `-m unittest`, or a script or module named runner.py or test_*.py. A planting tool
that sets the cache itself (the kit's testing/plants.py, mutmut) is neither, and its own writes are not the call's.

EXCEPTIONS, each with the failure it holds:
  * a process start the call's Python code does not spell counts as a test run: `subprocess.run([sys.executable,
    "-m", "pytest"])`, the usual way a plant script runs its tests, names no program the call model can read;
  * a value given inline, PYTHONPYCACHEPREFIX=$( or ="$(, is refused whatever the call removes: no later command can
    name the folder it makes, which is how the 556 folders were left;
  * a Python file found on disk is not read for writes: a project's own tools write files and start processes the
    rule cannot tell from a plant's, and reading them refused 931 past runs of the kit's runner, the old hooks'
    suite, the mutation runner and pr-checks-watch.
"""

import re
from collections.abc import Iterator, Sequence
from fnmatch import fnmatchcase
from typing import NamedTuple

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.heavy import base
from dispatcher.model import PYTHON_NAME, Call, Command, Word
from dispatcher.python import RunKind, invoked, planted
from dispatcher.tree import ShellText
from dispatcher.verdict import Prose, Verdict, refuse
from dispatcher.writes import writes

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
GIVEN = re.compile(r"PYTHONPYCACHEPREFIX=|[\"']PYTHONPYCACHEPREFIX[\"']")
INLINE = re.compile(r'PYTHONPYCACHEPREFIX="?\$\(')
MAKES = ShellText("mktemp")
REMOVES = ShellText("rm")
RECURSIVE = ShellText("--recursive")
TEMPORARY = ShellText("TemporaryDirectory")
PYTHON_MADE = (ShellText("mkdtemp"), ShellText("rmtree"))
PY = ".py"
RUNNERS = frozenset({ShellText("pytest"), ShellText("py.test")})
MODULES = frozenset({ShellText("pytest"), ShellText("unittest")})
TESTS = ("runner.py", "test_*.py")
UNSPELLED = Prose("a process the call's Python code starts without spelling it counts as a test run")
UNMADE = Prose("the call gives PYTHONPYCACHEPREFIX a folder it does not make with mktemp")
UNREMOVED = Prose("the call gives PYTHONPYCACHEPREFIX a folder it does not remove with rm -r")
ADVICE = """this call writes a Python file and starts a Python test run without a fresh bytecode cache, or gives
the cache a folder it does not make and remove. A test run may import a planted module through the .pyc CPython
wrote for it before, its source's mtime second and size unchanged, and a folder left behind stays on the tmpfs.

{problems}

Make the cache in the call and remove it:
  P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" <test run>; rm -rf "$P"
or, in Python, with tempfile.TemporaryDirectory() as prefix: and env=os.environ | {{"PYTHONPYCACHEPREFIX": prefix}}."""

class Running(NamedTuple):
    """What a Python interpreter's command runs: the kind, and its value, None where the call does not make it known."""

    kind: RunKind
    value: ShellText | None


def named_test(name: ShellText) -> bool:
    """Whether a script's base name is runner.py or test_*.py."""
    return any(fnmatchcase(name, pattern) for pattern in TESTS)


def run_of(command: Command) -> Running | None:
    """What a Python interpreter's ``command`` runs; None for any other command."""
    values = [word.value for word in command.words]
    run = invoked(values) if command.name is not None and PYTHON_NAME.fullmatch(base(command.name)) else None
    if run is None:
        return None
    value = values[run.index] if run.index < len(values) else None
    return Running(run.kind, None if value is None else ShellText(value[run.offset:]))


def tested(command: Command) -> bool:
    """Whether ``command`` starts a Python test run, as the module docstring says."""
    if command.name is None:
        return False
    name = base(command.name)
    run = run_of(command)
    if name in RUNNERS or named_test(name):
        return True
    if run is None or run.value is None:
        return False
    if run.kind == "module":
        return run.value in MODULES or named_test(ShellText(run.value.rsplit(".", 1)[-1] + PY))
    return run.kind == "script" and named_test(base(run.value))


def python_writes(call: Call) -> Iterator[Prose]:
    """Each `.py` write of ``call``, as the module docstring says, named as the refusal names it."""
    for code in call.python:
        for spelled in planted(code.text) if code.carried else ():
            yield Prose(f"`{spelled}` writes a Python file")
    for command in call.commands:
        for write in writes(command):
            if (write.word.value or "").endswith(PY):
                yield Prose(f"`{command.text}` writes the Python file `{write.word.spelled}`")


def recursive(word: Word) -> bool:
    """Whether ``word`` is an rm option removing a folder: --recursive, or a bundle of short options holding r or R."""
    value = word.value or ""
    return value == RECURSIVE or (value.startswith("-") and not value.startswith("--") and bool({"r", "R"} & set(value)))


def kept(call: Call, texts: Sequence[ShellText]) -> list[Prose]:
    """What keeps the cache folder ``call`` gives PYTHONPYCACHEPREFIX from being one it makes and removes, its text
    and its commands' ``texts`` read as the module docstring says."""
    inline = [text for text in texts if INLINE.search(text)]
    if inline:
        return [Prose(f"`{inline[0]}` gives PYTHONPYCACHEPREFIX a folder made inline, which no later command can name "
                      "to remove it")]
    code = "\n".join([call.text, *(read.text for read in call.python)])
    python = TEMPORARY in code or all(name in code for name in PYTHON_MADE)
    names = [base(command.name) for command in call.commands if command.name is not None]
    removed = any(recursive(word) for command in call.commands
                  if command.name is not None and base(command.name) == REMOVES for word in command.words[1:])
    return [] if python else [line for line, held in ((UNMADE, MAKES in names), (UNREMOVED, removed)) if not held]


def judge(event: Event) -> Verdict | None:
    """Refuse a Bash or Monitor call that writes a `.py` file and starts a Python test run giving PYTHONPYCACHEPREFIX
    no value, or that gives it a folder it does not make and remove, naming each write, run and fault."""
    call = event.call()
    texts = [*(command.text for command in call.commands), call.text]
    if any(GIVEN.search(text) for text in texts):
        lines = kept(call, texts)
    else:
        runs = [Prose(f"`{command.text}` runs Python tests") for command in call.commands if tested(command)]
        runs += [UNSPELLED] if call.unspelled else []
        found = list(dict.fromkeys(python_writes(call))) if runs else []
        lines = [*found, *runs] if found else []
    return refuse(Prose(ADVICE.format(problems="\n".join(f"  * {line}" for line in lines)))) if lines else None
