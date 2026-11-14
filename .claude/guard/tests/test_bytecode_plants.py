"""The bytecode-plants rule, through the dispatcher with every other rule switched off: a Bash or Monitor call that
writes a `.py` file, in its shell or in the Python code it carries, and starts a Python test run is refused naming
each write and each run, unless it names PYTHONPYCACHEPREFIX; a write of no Python file, a Python write with no test
run and a test run with no write pass."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher.core import Answer, EventBytes, FolderPath, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_declarations import NONE_DECLARED

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
CaseId = NewType("CaseId", str)
HEADLINE = Prose("this call writes a Python file and starts a Python test run")
ADVICE = Prose('P="$(mktemp -d)"; PYTHONPYCACHEPREFIX="$P" <test run>; rm -rf "$P"')
FRESH = ShellText('P="$(mktemp -d)"; ')
REMOVED = ShellText('; rm -rf "$P"')
INLINE = Prose("a folder made inline, which no later command can name to remove it")
UNMADE = Prose("the call gives PYTHONPYCACHEPREFIX a folder it does not make with mktemp")
UNREMOVED = Prose("the call gives PYTHONPYCACHEPREFIX a folder it does not remove with rm -r")
SED = ShellText("sed -i s/a/b/ pkg/mod.py")
SED_WRITES = Prose("`sed -i s/a/b/ pkg/mod.py` writes the Python file `pkg/mod.py`")
OPEN = ShellText('open("pkg/mod.py", "w")')
OPEN_WRITES = Prose('`open("pkg/mod.py", "w")` writes a Python file')
PYTEST_RUNS = Prose("`pytest tests/` runs Python tests")
HEREDOC = ShellText("""python3 <<'EOF'
import subprocess
from pathlib import Path
Path("pkg/mod.py").write_text("x = 1")
subprocess.run(["pytest", "tests/"])
EOF""")
UNSPELLED = ShellText(f"""python3 - <<'EOF'
import subprocess, sys
{OPEN}
subprocess.run([sys.executable, "-m", "pytest"])
EOF""")
SCRIPT = ShellText(f"cat > plant <<'EOF'\n{OPEN}\nEOF\n")

class Refusal(NamedTuple):
    """A call refused and the lines its refusal names besides the headline and the advice."""

    call: ShellText
    lines: tuple[Prose, ...]


type Refusals = dict[CaseId, Refusal]


def judged(base: Path, call: ShellText, tool: ShellText = ShellText("Bash")) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call`` run from the project under ``base``, every rule but
    bytecode-plants switched off."""
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(base / "off")),
                        ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(base / "home"))}),
                        home=base / "home")
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(base / "project"),
              "tool_input": {"command": call}}
    return answer(EventBytes(json.dumps(fields).encode()), settings)


class Rule(unittest.TestCase):
    """Each case refused names its lines; each case passed is let through."""

    base: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(tempfile.mkdtemp()).resolve()
        for folder in ("project/tests", "project/testing", "project/tools", "home/.config/claude-guard", "off"):
            (cls.base / folder).mkdir(parents=True)
        machine = cls.base / "home" / ".config" / "claude-guard" / "machine.toml"
        _ = machine.write_text(NONE_DECLARED, encoding="utf-8")
        for script in ("tests/test_x.py", "testing/runner.py", "tools/check.py"):
            _ = (cls.base / "project" / script).write_text("#!/usr/bin/env python3\nprint(1)\n", encoding="utf-8")
            (cls.base / "project" / script).chmod(0o755)
        for script, run in (("fresh.sh", 'P="$(mktemp -d)"\nPYTHONPYCACHEPREFIX="$P" pytest tests/\nrm -rf "$P"'),
                            ("exported.sh", 'P="$(mktemp -d)"\nexport PYTHONPYCACHEPREFIX="$P"\npytest tests/\nrm -rf "$P"'),
                            ("stale.sh", "pytest tests/")):
            _ = (cls.base / "project" / "testing" / script).write_text(f"#!/bin/bash\n{run}\n", encoding="utf-8")
        for script, body in (("plant_disk.py", f'import subprocess\n{OPEN}\nsubprocess.run(["pytest", "tests/"])\n'),
                             ("fresh_disk.py", f'import os, subprocess, tempfile\n{OPEN}\n'
                                               "with tempfile.TemporaryDirectory() as prefix:\n"
                                               '    subprocess.run(["pytest"], env=os.environ | {"PYTHONPYCACHEPREFIX": prefix})\n'),
                             ("environ_disk.py", f'import os, subprocess, tempfile\n{OPEN}\n'
                                                 "with tempfile.TemporaryDirectory() as prefix:\n"
                                                 '    os.environ["PYTHONPYCACHEPREFIX"] = prefix\n    subprocess.run(["pytest"])\n')):
            _ = (cls.base / "project" / "testing" / script).write_text(body, encoding="utf-8")
        for module in (KIT / "rules").glob("*.py"):
            if not module.stem.startswith("_") and module.stem != "bytecode_plants":
                (cls.base / "off" / module.stem.replace("_", "-")).touch()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base)

    def refused(self, cases: Refusals, tool: ShellText = ShellText("Bash")) -> None:
        """Every case refused, its stderr holding the headline, the advice and each of its lines."""
        for case, (call, lines) in cases.items():
            with self.subTest(case=case, call=call):
                got = judged(self.base, call, tool)
                self.assertEqual(got.status, 2, got.stderr)
                for line in (HEADLINE, ADVICE, *lines):
                    self.assertIn(line, got.stderr)

    def passed(self, cases: dict[CaseId, ShellText]) -> None:
        """Every case let through."""
        for case, call in cases.items():
            with self.subTest(case=case, call=call):
                got = judged(self.base, call)
                self.assertEqual((got.status, got.stderr), (0, ""))

    def test_a_shell_write_and_a_test_run(self) -> None:
        self.refused({
            CaseId("sed then pytest"): Refusal(ShellText(f"{SED} && pytest tests/"), (SED_WRITES, PYTEST_RUNS)),
            CaseId("-m pytest"): Refusal(ShellText(f"{SED} && python3 -m pytest"), (Prose("`python3 -m pytest` runs"),)),
            CaseId("-m unittest"): Refusal(ShellText(f"{SED} && python3 -m unittest tests.test_x"), (SED_WRITES,)),
            CaseId("py.test"): Refusal(ShellText(f"{SED} && py.test"), (Prose("`py.test` runs Python tests"),)),
            CaseId("runner by path"): Refusal(ShellText(f"{SED} && testing/runner.py tests.test_x"), (SED_WRITES,)),
            CaseId("test script"): Refusal(ShellText(f"{SED} && python3 tests/test_x.py"), (SED_WRITES,)),
            CaseId("test module"): Refusal(ShellText(f"{SED} && python3 -m tests.test_x"), (SED_WRITES,)),
            CaseId("runner module"): Refusal(ShellText(f"{SED} && python3 -m testing.runner"), (SED_WRITES,)),
            CaseId("unknown directory"): Refusal(ShellText('cd "$BYTECODE_UNSET" && sed -i s/a/b/ mod.py && pytest'),
                                          (Prose("writes the Python file `mod.py`"),)),
        })

    def test_a_monitor_call(self) -> None:
        self.refused({CaseId("monitor"): Refusal(ShellText(f"{SED} && pytest tests/"), (SED_WRITES,))}, ShellText("Monitor"))

    def test_the_python_code_the_call_carries(self) -> None:
        self.refused({
            CaseId("heredoc"): Refusal(HEREDOC, (Prose('`Path("pkg/mod.py").write_text("x = 1")` writes a Python file'),
                                          Prose("runs Python tests"))),
            CaseId("indented heredoc"): Refusal(ShellText(f"python3 <<-'EOF'\n{OPEN}\nEOF\npytest tests/"), (OPEN_WRITES,)),
            CaseId("-c"): Refusal(ShellText(f"python3 -c '{OPEN}' && pytest tests/"), (OPEN_WRITES,)),
            CaseId("-c attached"): Refusal(ShellText(f"python3 -c'{OPEN}' && pytest tests/"), (OPEN_WRITES,)),
            CaseId("here-string"): Refusal(ShellText(f"python3 <<< '{OPEN}' && pytest tests/"), (OPEN_WRITES,)),
            CaseId("written script"): Refusal(ShellText(f"{SCRIPT}python3 plant && pytest tests/"), (OPEN_WRITES,)),
            CaseId("written input"): Refusal(ShellText(f"{SCRIPT}python3 < plant && pytest tests/"), (OPEN_WRITES,)),
            CaseId("unspelled start"): Refusal(UNSPELLED, (OPEN_WRITES, Prose("without spelling it counts as a test run"))),
            CaseId("a script's run"): Refusal(ShellText(f"{SED} && bash testing/stale.sh"), (SED_WRITES, PYTEST_RUNS)),
            CaseId("a mention alone"): Refusal(ShellText(f"rg -n PYTHONPYCACHEPREFIX notes.txt; {SED} && pytest tests/"),
                                        (SED_WRITES, PYTEST_RUNS)),
        })

    def test_a_fresh_cache_named(self) -> None:
        self.passed({
            CaseId("sed"): ShellText(f'{FRESH}{SED} && PYTHONPYCACHEPREFIX="$P" pytest tests/{REMOVED}'),
            CaseId("heredoc"): ShellText(f'{FRESH}PYTHONPYCACHEPREFIX="$P" {HEREDOC}\nrm -rf "$P"'),
            CaseId("exported"): ShellText(f'{FRESH}export PYTHONPYCACHEPREFIX="$P"; {SED} && pytest tests/{REMOVED}'),
            CaseId("a script's run"): ShellText(f"{SED} && bash testing/fresh.sh"),
            CaseId("a script's export"): ShellText(f"{SED} && bash testing/exported.sh"),
            CaseId("a script on disk"): ShellText("python3 testing/fresh_disk.py"),
            CaseId("a script on disk setting its environment"): ShellText("python3 testing/environ_disk.py"),
            CaseId("a plant script on disk"): ShellText("python3 testing/plant_disk.py"),
            CaseId("temporary directory"): ShellText(
                "python3 <<'EOF'\nimport os, subprocess, tempfile\nfrom pathlib import Path\n"
                'Path("pkg/mod.py").write_text("x = 1")\nwith tempfile.TemporaryDirectory() as prefix:\n'
                '    subprocess.run(["pytest"], env=os.environ | {"PYTHONPYCACHEPREFIX": prefix})\nEOF'),
            CaseId("mkdtemp and rmtree"): ShellText(
                "python3 <<'EOF'\nimport os, shutil, subprocess, tempfile\nprefix = tempfile.mkdtemp()\n"
                'subprocess.run(["pytest"], env=os.environ | {"PYTHONPYCACHEPREFIX": prefix})\nshutil.rmtree(prefix)\nEOF'),
        })

    def test_a_cache_the_call_keeps(self) -> None:
        self.refused({
            CaseId("inline"): Refusal(ShellText(f'{SED} && PYTHONPYCACHEPREFIX="$(mktemp -d)" pytest tests/{REMOVED}'),
                                      (INLINE,)),
            CaseId("inline, no write"): Refusal(ShellText("PYTHONPYCACHEPREFIX=$(mktemp -d) pytest tests/"), (INLINE,)),
            CaseId("inline export"): Refusal(ShellText("export PYTHONPYCACHEPREFIX=$(mktemp -d); pytest tests/"),
                                             (INLINE,)),
            CaseId("not removed"): Refusal(ShellText(f'{FRESH}PYTHONPYCACHEPREFIX="$P" pytest tests/'), (UNREMOVED,)),
            CaseId("not made"): Refusal(ShellText('PYTHONPYCACHEPREFIX=/tmp/pyc pytest tests/; rm -rf /tmp/pyc'),
                                        (UNMADE,)),
            CaseId("removed, not recursively"): Refusal(
                ShellText(f'{FRESH}PYTHONPYCACHEPREFIX="$P" pytest; rm -f "$P" report.txt'), (UNREMOVED,)),
            CaseId("a long option holding r"): Refusal(ShellText(f'{FRESH}PYTHONPYCACHEPREFIX="$P" pytest; rm --force "$P"'),
                                                       (UNREMOVED,)),
            CaseId("mkdtemp alone"): Refusal(ShellText(
                "python3 <<'EOF'\nimport os, subprocess, tempfile\n"
                'subprocess.run(["pytest"], env=os.environ | {"PYTHONPYCACHEPREFIX": tempfile.mkdtemp()})\nEOF'),
                (UNREMOVED,)),
        })

    def test_what_passes(self) -> None:
        self.passed({
            CaseId("no python file"): ShellText("sed -i s/a/b/ notes.txt && pytest tests/"),
            CaseId("no test run"): ShellText(f"A=1; {SED}"),
            CaseId("no write"): ShellText("pytest tests/"),
            CaseId("a mention alone, no write"): ShellText("rg -n PYTHONPYCACHEPREFIX notes.txt"),
            CaseId("recursive spelled long"): ShellText(f'{FRESH}PYTHONPYCACHEPREFIX="$P" pytest; rm --recursive "$P"'),
            CaseId("recursive in a bundle"): ShellText(f'{FRESH}PYTHONPYCACHEPREFIX="$P" pytest; rm -fR -- "$P"'),
            CaseId("a script written, not run"): ShellText(
                "cat > drive.py <<'EOF'\nimport os, shutil, subprocess, tempfile\np = tempfile.mkdtemp()\n"
                'subprocess.run(["pytest"], env=dict(os.environ, PYTHONPYCACHEPREFIX=p))\nshutil.rmtree(p)\nEOF'),
            CaseId("another module"): ShellText(f"{SED} && python3 -m json.tool notes.txt"),
            CaseId("a module's input"): ShellText(f"python3 -m json.tool <<'EOF'\n{OPEN}\nEOF\npytest tests/"),
            CaseId("another script"): ShellText(f"{SED} && python3 tools/check.py"),
            CaseId("code writing no python file"): ShellText(
                "python3 <<'EOF'\nimport subprocess\nfrom pathlib import Path\nPath(\"out.json\").write_text(\"\")\n"
                "subprocess.run([\"pytest\"])\nEOF"),
            CaseId("script writing no python file"): ShellText(
                "cat > plant <<'EOF'\nprint(1)\nEOF\npython3 plant && pytest tests/"),
        })


if __name__ == "__main__":
    unittest.main()
