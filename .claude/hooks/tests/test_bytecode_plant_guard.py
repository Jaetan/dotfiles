#!/usr/bin/env python3
"""Regression cases for bytecode-plant-guard.py.

Every case pipes one event for a Bash call to the hook, run by its path as the
harness runs it, with the call's cwd a fresh git work tree holding a module
(pkg/m.py), its test, untracked plant scripts the cases run by path, as a red-first
matrix sits in an ignored directory, and a tracked copy of one. A PreToolUse
case compares the answer with the rule: exit 2 refuses a call that plants a
change in a .py and starts a test run no fresh-bytecode guard reaches, naming
what it plants, and a call, planting or not, that gives PYTHONPYCACHEPREFIX a
directory it makes with mktemp or tempfile.mkdtemp and does not remove; exit 0
lets the rest through, the two loop shapes the guard names as known limits
included. A PostToolUse case first leaves
the module's __pycache__ entry as a plant restored within the second leaves it
(the planted code under the source's own mtime and size), or as another case
needs, then expects exit 2 naming the module after a call that plants and
tests, and exit 0 otherwise.

Usage: test_bytecode_plant_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation.
"""

import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType

# A case's name; prose a case names itself by or expects; the text of a call;
# an event as the hook reads it; text a file holds; the hook's exit status.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
CallText = NewType("CallText", str)
EventText = NewType("EventText", str)
FileText = NewType("FileText", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Arm = Literal["PreToolUse", "PostToolUse"]
# How a PostToolUse case leaves the module's bytecode before the call.
type Bytecode = Literal["planted", "fresh", "hash-checked", "foreign-magic", "recompiled"]
PLANTED_CODE: Bytecode = "planted"
RECOMPILED: Bytecode = "recompiled"
FRESH_CODE: Bytecode = "fresh"
HASH_CHECKED: Bytecode = "hash-checked"
FOREIGN_MAGIC: Bytecode = "foreign-magic"

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "bytecode-plant-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="bytecode-plant-guard-test-")).resolve()
ROOT = BASE / "project"
MODULE = ROOT / "pkg" / "m.py"
ORIGINAL = FileText('def f():\n    return "ab"\n')
PLANTED = FileText('def f():\n    return "ba"\n')

REFUSED = Prose("BLOCKED: this call plants a change in a Python file")
LEAKED = Prose("BLOCKED: this call gives PYTHONPYCACHEPREFIX a directory it makes and does not remove")
# The forms every refusal before a call shows: a prefix made, handed to the run and removed in the same call.
REMOVAL_FORMS = (Prose('P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" pytest ...; rm -rf "$P"'),
                 Prose("with tempfile.TemporaryDirectory() as prefix:"),
                 Prose('env=os.environ | {"PYTHONPYCACHEPREFIX": prefix}'))
LEFT = Prose("bytecode Python would run in place of its source")

# A plant script that restores the module and runs pytest per pass with no guard.
UNGUARDED = FileText('''import subprocess
from pathlib import Path
module = Path("pkg/m.py")
original = module.read_text()
for old, new in (("ab", "ba"), ("return", "return  ")):
    module.write_text(original.replace(old, new))
    subprocess.run(["python3", "-m", "pytest", "-q"], check=False)
    module.write_text(original)
''')
# The same, each pass on a new bytecode prefix that its with block removes.
PREFIXED = FileText(UNGUARDED.replace(
    '    subprocess.run(["python3", "-m", "pytest", "-q"], check=False)',
    '    with tempfile.TemporaryDirectory() as prefix:\n'
    '        subprocess.run(["python3", "-m", "pytest", "-q"], check=False,\n'
    '                       env=os.environ | {"PYTHONPYCACHEPREFIX": prefix})',
).replace("import subprocess\n", "import os\nimport subprocess\nimport tempfile\n"))
# Each pass on a new tempfile.mkdtemp prefix that nothing removes.
MKDTEMP_PREFIXED = FileText(UNGUARDED.replace(
    'subprocess.run(["python3", "-m", "pytest", "-q"], check=False)',
    'subprocess.run(["python3", "-m", "pytest", "-q"], check=False,\n'
    '                   env=os.environ | {"PYTHONPYCACHEPREFIX": tempfile.mkdtemp()})',
).replace("import subprocess\n", "import os\nimport subprocess\nimport tempfile\n"))
# The same, each prefix removed by shutil.rmtree after its pass.
RMTREE_PREFIXED = FileText(MKDTEMP_PREFIXED.replace(
    '{"PYTHONPYCACHEPREFIX": tempfile.mkdtemp()})',
    '{"PYTHONPYCACHEPREFIX": (prefix := tempfile.mkdtemp())})\n    shutil.rmtree(prefix)',
).replace("import os\n", "import os\nimport shutil\n"))
# The same, deleting the module's bytecode before each pass.
CLEARED = FileText(UNGUARDED.replace(
    "    module.write_text(original.replace(old, new))\n",
    "    for cached in Path(\"pkg/__pycache__\").glob(\"m.*.pyc\"):\n"
    "        cached.unlink()\n"
    "    module.write_text(original.replace(old, new))\n",
))
# A plant script whose runs are pytest.main in its own process.
IN_PROCESS = FileText('''import pytest
from pathlib import Path
module = Path("pkg/m.py")
original = module.read_text()
module.write_text(original.replace("ab", "ba"))
pytest.main(["-q"])
module.write_text(original)
''')
# A script that runs pytest and writes a log, planting nothing.
LOGGER = FileText('''import subprocess
from pathlib import Path
run = subprocess.run(["python3", "-m", "pytest", "-q"], capture_output=True, text=True, check=False)
Path("run.log").write_text(run.stdout)
''')
# A plant script writing through a loop variable and starting pytest through
# sys.executable, as a red-first matrix does: the reader places neither.
UNPLACED = FileText('''import subprocess
import sys
from pathlib import Path
for module in [Path("pkg/m.py")]:
    original = module.read_text()
    module.write_text(original.replace("ab", "ba"))
    subprocess.run([sys.executable, "-m", "pytest", "-q"], check=False)
    module.write_text(original)
''')
# A script naming a .py test file and writing logs through a loop variable,
# reading nothing back: it plants nothing.
NAMES_A_TEST = FileText('''import subprocess
import sys
from pathlib import Path
for log in [Path("a.log"), Path("b.log")]:
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_m.py"], capture_output=True, text=True)
    log.write_text(run.stdout)
''')
# A plant script whose pytest command comes back from a function: the reader
# cannot read the command, so the word in the code is what says it runs tests.
UNREAD = FileText('''import subprocess
import sys
from pathlib import Path


def argv():
    return [sys.executable, "-m", "pytest", "-q"]


module = Path("pkg/m.py")
original = module.read_text()
module.write_text(original.replace("ab", "ba"))
subprocess.run(argv(), check=False)
module.write_text(original)
''')
# A red-first matrix's shape: originals kept in a dict, each written back by subscript.
BY_DICT = FileText('''import subprocess
from pathlib import Path
M = Path("pkg/m.py")
originals = {M: M.read_text()}
for path in originals:
    path.write_text(originals[path].replace("ab", "ba"))
    subprocess.run(["python3", "-m", "pytest", "-q"], check=False)
    path.write_text(originals[path])
''')
# The same, the original taken out of the dict by name before it is written back.
BY_NAME = FileText('''import subprocess
from pathlib import Path
M = Path("pkg/m.py")
originals = {M: M.read_text()}
for path in originals:
    text = originals[path]
    path.write_text(text.replace("ab", "ba"))
    subprocess.run(["python3", "-m", "pytest", "-q"], check=False)
    path.write_text(text)
''')
# The same, every original written back by a loop over the dict's items.
BY_ITEMS = FileText('''import subprocess
from pathlib import Path
M = Path("pkg/m.py")
originals = {M: M.read_text()}
M.write_text("x = 1")
subprocess.run(["python3", "-m", "pytest", "-q"], check=False)
for path, text in originals.items():
    path.write_text(text)
''')
SCRIPTS = {"by_name.py": BY_NAME, "by_items.py": BY_ITEMS, "by_dict.py": BY_DICT, "unread.py": UNREAD, "unguarded.py": UNGUARDED, "prefixed.py": PREFIXED, "cleared.py": CLEARED,
           "in_process.py": IN_PROCESS, "logger.py": LOGGER, "unplaced.py": UNPLACED,
           "names_a_test.py": NAMES_A_TEST, "mkdtemp_prefixed.py": MKDTEMP_PREFIXED,
           "rmtree_prefixed.py": RMTREE_PREFIXED}

LOOP = "for p in a b; do sed -i 's/ab/ba/' pkg/m.py; {run}; git checkout pkg/m.py; done"
UNGUARDED_LOOP = LOOP.format(run=".venv/bin/pytest -q")
PREFIXED_LOOP = LOOP.format(run='P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" .venv/bin/pytest -q; rm -rf "$P"')
INLINE = ("python3 - <<'EOF'\nimport subprocess\nfrom pathlib import Path\nm = Path('pkg/m.py')\n"
          "o = m.read_text()\nm.write_text(o.replace('ab', 'ba'))\n"
          "subprocess.run(['python3', '-m', 'pytest', '-q'])\nm.write_text(o)\nEOF")

# Before a call: (id, what the case shows, the call, whether the rule refuses it).
BEFORE = [
    ("P01", "a plant script by path, no guard", "python3 unguarded.py", True),
    ("P02", "a plant script giving each pass a new prefix", "python3 prefixed.py", False),
    ("P03", "a plant script deleting the bytecode before each pass", "python3 cleared.py", False),
    ("P04", "a prefix on the command starting a plant script reaches no pass",
     'PYTHONPYCACHEPREFIX="$(mktemp -d)" python3 unguarded.py', True),
    ("P05", "a shell loop: sed -i, pytest, git checkout", UNGUARDED_LOOP, True),
    ("P06", "the loop with a prefix on each run", PREFIXED_LOOP, False),
    ("P07", "the loop through env with a prefix",
     LOOP.format(run='P="$(mktemp -d)"; env PYTHONPYCACHEPREFIX="$P" pytest -q; rm -rf "$P"'), False),
    ("P08", "perl -pi on a .py, python -m pytest, git checkout",
     "perl -pi -e 's/ab/ba/' pkg/m.py && .venv/bin/python -m pytest -q; git checkout pkg/m.py", True),
    ("P09", "an edit then a run, no restore: the .pyc records the old mtime",
     "sed -i 's/ab/ba/' pkg/m.py && pytest -q", False),
    ("P10", "PYTHONDONTWRITEBYTECODE is no guard",
     "sed -i 's/ab/ba/' pkg/m.py && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q; git checkout pkg/m.py", True),
    ("P11", "python -B is no guard",
     "sed -i 's/ab/ba/' pkg/m.py && python3 -B -m pytest -q; git checkout pkg/m.py", True),
    ("P12", "a shell deletion of the bytecode guards the run",
     "find . -name '*.pyc' -delete; sed -i 's/ab/ba/' pkg/m.py; pytest -q; git checkout pkg/m.py", False),
    ("P13", "rm of __pycache__ guards the run",
     "rm -rf pkg/__pycache__; sed -i 's/ab/ba/' pkg/m.py; pytest -q; git checkout pkg/m.py", False),
    ("P14", "inline Python that plants and starts pytest", INLINE, True),
    ("P15", "python -c that edits, a run, git checkout",
     ("python3 -c \"from pathlib import Path; Path('pkg/m.py').write_text('x = 1')\" && pytest -q; "
     "git checkout pkg/m.py"), True),
    ("P16", "a redirection onto a .py, a run, cp back from a copy",
     "cp pkg/m.py /tmp/m.bak; echo 'x = 1' > pkg/m.py && pytest -q; cp /tmp/m.bak pkg/m.py", True),
    ("P17", "a single cp onto a .py, then a run", "cp /dev/null pkg/m.py && python -m unittest", False),
    ("P18", "a single git restore of a .py after a run", "pytest -q; git restore pkg/m.py", False),
    ("P19", "pytest.main in the planting script's own process", "python3 in_process.py", True),
    ("P20", "a run alone", ".venv/bin/python -m pytest -q", False),
    ("P21", "an edit alone", "sed -i 's/ab/ba/' pkg/m.py", False),
    ("P22", "a script that runs pytest and writes a log", "python3 logger.py", False),
    ("P23", "editing a file that is not Python, then a run", "sed -i 's/a/b/' notes.txt && pytest -q", False),
    ("P24", "a plant script writing through a loop variable, pytest through sys.executable", "python3 unplaced.py",
     True),
    ("P25", "a script naming a .py test and writing logs it never reads back", "python3 names_a_test.py", False),
    ("P26", "python -mpytest, the module joined to its flag",
     "sed -i 's/ab/ba/' pkg/m.py && python3 -mpytest -q; git checkout pkg/m.py", True),
    ("P27", "tee onto a .py, a run, git restore", "echo 'x = 1' | tee pkg/m.py; pytest -q; git restore pkg/m.py",
     True),
    ("P28", "a plant script whose pytest command the reader cannot read", "python3 unread.py", True),
    ("P29", "sed plants and a second sed puts it back",
     "sed -i 's/ab/ba/' pkg/m.py; pytest -q; sed -i 's/ba/ab/' pkg/m.py", True),
    ("P30", "git stash puts back what sed planted", "sed -i 's/ab/ba/' pkg/m.py; pytest -q; git stash", True),
    ("P31", "Python editing two files once each, then a run",
     ("python3 - <<'EOF'\nfrom pathlib import Path\nfor name in ['pkg/m.py', 'test_m.py']:\n"
     "    pass\nPath('pkg/m.py').write_text('x = 1')\nPath('test_m.py').write_text('y = 2')\nEOF\npytest -q"),
     False),
    ("P32", "a plant script the repository tracks, by path", "python3 tools/plant_tool.py", False),
    ("P33", "a plant module the repository tracks, by -m", "python3 -m tools.plant_tool", False),
    ("P34", "an edit script rebinding its text before writing, then a run",
     ("python3 - <<'EOF'\nfrom pathlib import Path\np = Path('pkg/m.py')\nt = p.read_text()\n"
     "t = t.replace('ab', 'ba')\np.write_text(t)\nEOF\npytest -q"), False),
    ("P35", "a plant script keeping originals in a dict, as a red-first matrix does", "python3 by_dict.py", True),
    ("P36", "the original taken out of the dict by name, then written back", "python3 by_name.py", True),
    ("P37", "every original written back by a loop over the dict's items", "python3 by_items.py", True),
]
# Before a call, a bytecode prefix the call makes: (id, what the case shows, the call, whether the rule refuses it).
RUN = 'P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" pytest -q; '
TWO = 'A="$(mktemp -d)"; B="$(mktemp -d)"; '
PREFIXES = [
    ("L01", "an inline mktemp prefix on a test run", 'PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest -q', True),
    ("L02", "an inline mktemp prefix through env", 'env PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest -q', True),
    ("L03", "an inline prefix, then another mktemp directory removed",
     'PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest -q; S="$(mktemp -d)"; rm -rf "$S"', True),
    ("L04", "an inline prefix through env beside a mktemp variable it removes",
     'S="$(mktemp -d)"; env PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest -q; rm -rf "$S"', True),
    ("L05", "a mktemp variable as the prefix, never removed", RUN.rstrip("; "), True),
    ("L06", "the variable removed by rm -f, which keeps a directory", RUN + 'rm -f "$P"', True),
    ("L07", "the variable removed before the run",
     'P="$(mktemp -d)"; rm -rf "$P"; PYTHONPYCACHEPREFIX="$P" pytest -q', True),
    ("L08", "a directory inside the prefix removed, not the prefix", RUN + 'rm -rf "$P/pkg"', True),
    ("L09", "the template removed as a literal, not the directory mktemp made",
     'P="$(mktemp -d /tmp/pyc.XXXXXX)"; PYTHONPYCACHEPREFIX="$P" pytest -q; rm -rf /tmp/pyc.XXXXXX', True),
    ("L10", "an exported inline prefix, never removed", 'export PYTHONPYCACHEPREFIX="$(mktemp -d)"; pytest -q', True),
    ("L11", "an inline prefix on a call that plants nothing and runs no test",
     'PYTHONPYCACHEPREFIX="$(mktemp -d)" python3 -c "print(1)"', True),
    ("L12", "the planting loop with an inline prefix on each run",
     LOOP.format(run='PYTHONPYCACHEPREFIX="$(mktemp -d)" .venv/bin/pytest -q'), True),
    ("L13", "the planting loop through env with an inline prefix",
     LOOP.format(run='env PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest -q'), True),
    ("L14", "a plant script giving each pass a tempfile.mkdtemp prefix it never removes",
     "python3 mkdtemp_prefixed.py", True),
    ("L15", "python -c handing PYTHONPYCACHEPREFIX a tempfile.mkdtemp directory",
     ("python3 -c \"import os, subprocess, tempfile; "
     "subprocess.run(['true'], env=os.environ | {'PYTHONPYCACHEPREFIX': tempfile.mkdtemp()})\""), True),
    ("L16", "the variable removed by rm -rf after the run", RUN + 'rm -rf "$P"', False),
    ("L17", "rm -fr", RUN + 'rm -fr "$P"', False),
    ("L18", "rm -R", RUN + 'rm -R "$P"', False),
    ("L19", "rm -r", RUN + 'rm -r "$P"', False),
    ("L20", "rm -f -r, the flags apart", RUN + 'rm -f -r "$P"', False),
    ("L21", "rm --recursive", RUN + 'rm --recursive "$P"', False),
    ("L22", "the variable through env, removed",
     'P="$(mktemp -d)"; env PYTHONPYCACHEPREFIX="$P" pytest -q; rm -rf "$P"', False),
    ("L23", "an exported inline prefix removed through its name",
     'export PYTHONPYCACHEPREFIX="$(mktemp -d)"; pytest -q; rm -rf "$PYTHONPYCACHEPREFIX"', False),
    ("L24", "a new prefix per pass in a loop, each removed",
     'for p in a b; do P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" pytest -q; rm -rf "$P"; done', False),
    ("L25", "the prefix removed by an EXIT trap",
     'P="$(mktemp -d)"; trap \'rm -rf "$P"\' EXIT; PYTHONPYCACHEPREFIX="$P" pytest -q', False),
    ("L26", "a fixed prefix the call does not make", "PYTHONPYCACHEPREFIX=/tmp/pyc pytest -q", False),
    ("L27", "a plant script removing each tempfile.mkdtemp prefix with shutil.rmtree",
     "python3 rmtree_prefixed.py", False),
    ("L28", "inline Python handing the prefix a tempfile.TemporaryDirectory inside its with block",
     ("python3 - <<'EOF'\nimport os, subprocess, tempfile\nwith tempfile.TemporaryDirectory() as prefix:\n"
     "    subprocess.run(['true'], env=os.environ | {'PYTHONPYCACHEPREFIX': prefix})\nEOF"), False),
    ("L29", "python -c making a tempfile.mkdtemp directory that is no prefix",
     "python3 -c \"import tempfile; print(tempfile.mkdtemp())\"", False),
    ("L30", "cp -r of the variable copies the prefix and removes nothing", RUN + 'cp -r "$P" /tmp/copy', True),
    ("L31", "the variable removed with a trailing slash", RUN + 'rm -rf "$P/"', False),
    ("L32", "the prefix from one mktemp variable, another removed",
     TWO + 'PYTHONPYCACHEPREFIX="$A" pytest -q; rm -rf "$B"', True),
    ("L33", "an inline prefix beside a mktemp variable made before it and removed",
     'S="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest -q; rm -rf "$S"', True),
    ("L34", "through env, the prefix from one mktemp variable, another removed",
     TWO + 'env PYTHONPYCACHEPREFIX="$A" pytest -q; rm -rf "$B"', True),
    ("L35", "an exported prefix from one mktemp variable, another removed",
     TWO + 'export PYTHONPYCACHEPREFIX="$A"; pytest -q; rm -rf "$B"', True),
    ("L36", "an exported inline prefix beside a mktemp variable made before it and removed",
     'S="$(mktemp -d)"; export PYTHONPYCACHEPREFIX="$(mktemp -d)"; pytest -q; rm -rf "$S"', True),
    ("L37", "the prefix removed through a copy of its variable, which the reader cannot tell from another",
     RUN + 'D="$P"; rm -rf "$D"', True),
    ("L38", "two mktemp variables, the prefix's removed beside the other",
     TWO + 'PYTHONPYCACHEPREFIX="$A" pytest -q; rm -rf "$B" "$A"', False),
    ("L39", "an exported prefix from a mktemp variable, removed through that variable",
     TWO + 'export PYTHONPYCACHEPREFIX="$A"; pytest -q; rm -rf "$A"', False),
    ("L40", "a new prefix per pass of a loop, only the last removed after the loop",
     'for p in a b; do P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" pytest -q; done; rm -rf "$P"', True),
    ("L41", "the prefix removed after &&, which runs only when the tests pass",
     RUN.removesuffix("; ") + ' && rm -rf "$P"', False),
    ("L42", "one prefix made before a loop, given on each pass, removed after the loop",
     'P="$(mktemp -d)"; for p in a b; do PYTHONPYCACHEPREFIX="$P" pytest -q; done; rm -rf "$P"', False),
    ("L43", "a new prefix per pass of a loop beside a mktemp variable made before it, both removed after",
     'S="$(mktemp -d)"; for p in a b; do P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" pytest -q; done; '
     'rm -rf "$P" "$S"', True),
    ("L44", "the variable removed through ${P:?}, which stops on an empty value", RUN + 'rm -rf "${P:?}"', False),
    ("L45", "a prefix made outside every loop, removed inside a later one",
     RUN + 'for d in once; do rm -rf "$P"; done', False),
    ("L46", "a prefix through env removed only through an exported PYTHONPYCACHEPREFIX holding another",
     TWO + 'export PYTHONPYCACHEPREFIX="$A"; env PYTHONPYCACHEPREFIX="$B" pytest -q; rm -rf "$A" "$PYTHONPYCACHEPREFIX"',
     True),
    ("L47", "an inline prefix assigned alone, with a redirection, then a test run",
     'PYTHONPYCACHEPREFIX="$(mktemp -d)" > /dev/null; pytest -q', True),
    ("L48", "an inline prefix assigned alone, exported bare, never removed",
     'PYTHONPYCACHEPREFIX="$(mktemp -d)"; export PYTHONPYCACHEPREFIX; pytest -q', True),
    ("L49", "an inline prefix assigned alone, exported bare by declare -x, never removed",
     'PYTHONPYCACHEPREFIX="$(mktemp -d)"; declare -x PYTHONPYCACHEPREFIX; pytest -q', True),
    ("L50", "an inline prefix assigned alone, exported bare, removed through its name",
     'PYTHONPYCACHEPREFIX="$(mktemp -d)"; export PYTHONPYCACHEPREFIX; pytest -q; rm -rf "$PYTHONPYCACHEPREFIX"',
     False),
    ("L51", "an exported prefix removed, then unset by name, which gives it nothing",
     'export PYTHONPYCACHEPREFIX="$(mktemp -d)"; pytest -q; rm -rf "$PYTHONPYCACHEPREFIX"; unset PYTHONPYCACHEPREFIX',
     False),
    ("L52", "known limit: a variable given a mktemp directory before a loop and a new one each pass, the last removed",
     'P="$(mktemp -d)"; for p in a b; do P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" pytest -q; done; rm -rf "$P"',
     False),
    ("L53", "known limit: a directory made in an outer loop, given in an inner one, the last removed after both",
     'for o in a b; do P="$(mktemp -d)"; for i in x; do PYTHONPYCACHEPREFIX="$P" pytest -q; done; done; rm -rf "$P"',
     False),
]
# After a call: (id, what the case shows, the call, the module's bytecode before it, whether the rule names it).
AFTER = [
    ("Q01", "a planting call left planted bytecode", UNGUARDED_LOOP, PLANTED_CODE, True),
    ("Q02", "a guarded planting call left planted bytecode: still named", PREFIXED_LOOP, PLANTED_CODE, True),
    ("Q03", "a planting call, the bytecode its source's", UNGUARDED_LOOP, FRESH_CODE, False),
    ("Q04", "a call that plants nothing is not scanned", ".venv/bin/pytest -q", PLANTED_CODE, False),
    ("Q05", "a hash-checked .pyc is validated by content", UNGUARDED_LOOP, HASH_CHECKED, False),
    ("Q06", "another interpreter's .pyc is not this one's to judge", UNGUARDED_LOOP, FOREIGN_MAGIC, False),
    ("Q07", "a call that plants and puts back but runs no test is not scanned",
     "sed -i 's/ab/ba/' pkg/m.py; git checkout pkg/m.py", PLANTED_CODE, False),
    ("Q08", "planted bytecode Python would recompile, its source's mtime moved", UNGUARDED_LOOP, RECOMPILED, False),
]


class Case(NamedTuple):
    """One call, what the rule says of it, and a phrase its answer must hold."""

    id: CaseId
    what: Prose
    call: CallText
    want: ExitStatus
    says: Prose
    arm: Arm = "PreToolUse"
    bytecode: Bytecode = "fresh"


class Outcome(NamedTuple):
    """The hook's exit status and what it printed to stderr."""

    status: ExitStatus
    stderr: Prose


def hook(event: EventText) -> Outcome:
    """Pipe one event to the guard and return its answer."""
    run = subprocess.run([str(GUARD)], input=event, capture_output=True, text=True, timeout=60, check=False)
    return Outcome(ExitStatus(run.returncode), Prose(run.stderr))


def event_of(arm: Arm, call: CallText) -> EventText:
    """Spell the event the harness sends for a call."""
    return EventText(json.dumps({"hook_event_name": arm, "tool_name": "Bash",
                                 "tool_input": {"command": call}, "cwd": str(ROOT)}))


def build() -> None:
    """Make the work tree: the module, its test, the plant scripts, all tracked."""
    MODULE.parent.mkdir(parents=True)
    (MODULE.parent / "__init__.py").write_text("")
    MODULE.write_text(ORIGINAL)
    (ROOT / "test_m.py").write_text("from pkg.m import f\n\n\ndef test_f():\n    assert f() == 'ab'\n")
    (ROOT / "tools").mkdir()
    (ROOT / "tools" / "__init__.py").write_text("")
    (ROOT / "tools" / "plant_tool.py").write_text(UNGUARDED)
    subprocess.run(["git", "init", "-q", str(ROOT)], check=True)
    subprocess.run(["git", "-C", str(ROOT), "add", "pkg", "test_m.py", "tools"], check=True)
    for name, text in SCRIPTS.items():
        (ROOT / name).write_text(text)
    (ROOT / "notes.txt").write_text("a\n")


def leave(bytecode: Bytecode) -> None:
    """Leave the module's __pycache__ entry as a case needs it, the source as it was."""
    cached = Path(importlib.util.cache_from_source(str(MODULE)))
    shutil.rmtree(cached.parent, ignore_errors=True)
    state = MODULE.stat()
    planted_first = bytecode != "fresh"
    if planted_first:
        MODULE.write_text(PLANTED)  # the same size, the same mtime: a plant restored within the second
        os.utime(MODULE, ns=(state.st_atime_ns, state.st_mtime_ns))
    mode = (py_compile.PycInvalidationMode.CHECKED_HASH if bytecode == "hash-checked"
            else py_compile.PycInvalidationMode.TIMESTAMP)
    py_compile.compile(str(MODULE), cfile=str(cached), invalidation_mode=mode)
    MODULE.write_text(ORIGINAL)
    os.utime(MODULE, ns=(state.st_atime_ns, state.st_mtime_ns))
    if bytecode == "foreign-magic":
        data = bytearray(cached.read_bytes())
        data[0] ^= 0xFF
        cached.write_bytes(bytes(data))
    if bytecode == "recompiled":
        os.utime(MODULE, ns=(state.st_atime_ns, state.st_mtime_ns + 2_000_000_000))


def cases() -> list[Case]:
    """Every case, before and after a call."""
    built = [
        Case(CaseId(case_id), Prose(what), CallText(call), REFUSE if refuses else ALLOW,
             REFUSED if refuses else Prose(""))
        for case_id, what, call, refuses in BEFORE
    ]
    built += [
        Case(CaseId(case_id), Prose(what), CallText(call), REFUSE if refuses else ALLOW,
             LEAKED if refuses else Prose(""))
        for case_id, what, call, refuses in PREFIXES
    ]
    built += [
        Case(CaseId(case_id), Prose(what), CallText(call), REFUSE if names else ALLOW,
             LEFT if names else Prose(""), "PostToolUse", bytecode)
        for case_id, what, call, bytecode, names in AFTER
    ]
    return built


def special_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Events no call spells: unreadable, another tool, an empty command, a call the reader cannot follow."""
    unreadable = hook(EventText("{not json"))
    other_tool = hook(EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read",
                                            "tool_input": {"file_path": str(MODULE)}, "cwd": str(ROOT)})))
    empty = hook(event_of("PreToolUse", CallText("  ")))
    unfollowed = hook(event_of("PreToolUse", CallText("echo 'unclosed")))
    return [
        (CaseId("S01"), Prose("an event that is no JSON is reported, the call goes ahead"),
         unreadable.status == REPORTED),
        (CaseId("S02"), Prose("another tool's event is let through"), other_tool.status == ALLOW),
        (CaseId("S03"), Prose("an empty command is let through"), empty.status == ALLOW),
        (CaseId("S04"), Prose("a call the reader cannot follow goes ahead unchecked"),
         unfollowed.status == ALLOW),
    ]


def main() -> ExitStatus:
    """Run every case; print one line each, MISS for a disagreement, then the count."""
    bad = 0
    total = 0
    build()
    try:
        for case in cases():
            if case.arm == "PostToolUse":
                leave(case.bytecode)
            outcome = hook(event_of(case.arm, case.call))
            total += 1
            ok = outcome.status == case.want and case.says in outcome.stderr
            if ok and case.arm == "PostToolUse" and case.want == REFUSE:
                ok = str(MODULE) in outcome.stderr
            if ok and case.arm == "PreToolUse" and case.want == REFUSE:
                ok = all(form in outcome.stderr for form in REMOVAL_FORMS)
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case.id:<4} want={case.want} got={outcome.status} {case.what}")
            if not ok and outcome.stderr.strip():
                print("        " + " | ".join(outcome.stderr.strip().splitlines()[-4:])[:300])
        for case_id, what, ok in special_cases():
            total += 1
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case_id:<4} {what}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"{total} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
