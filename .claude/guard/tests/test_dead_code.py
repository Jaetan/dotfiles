"""The kit holds no dead code: vulture finds no definition in it that nothing reads.

The rule: every function, class, variable, attribute and import of the kit is read somewhere, so a definition nothing
reads cannot land. The case runs /usr/bin/vulture over the kit, bin/ and __pycache__ left out, at vulture's default
confidence, so every finding it can make counts, and fails naming each one; a file vulture cannot parse fails it too.
A second case shows the check can fail, on a fixture holding an unused function and a file that does not parse.
Vulture is required: where /usr/bin/vulture is missing, the test fails with the line that installs it.

Exceptions, the names alive though vulture sees nothing read them:
- the test classes unittest discovers under tests/, run by discovery and named by no code: the case hands vulture
  their names, without which it reports every test class of the kit;
- the names in tests/dead_code_exemptions.py, read by vulture with the kit, each under the reason it is alive,
  without which it reports each of them.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NewType

from testing.runner import cases

Finding = NewType("Finding", str)
"""A line vulture prints: a definition nothing reads, or a file it cannot parse."""
ClassName = NewType("ClassName", str)

KIT = Path(__file__).resolve().parents[1]
VULTURE = Path("/usr/bin/vulture")
INSTALL = "sudo apt install vulture (version 2.16, from Debian unstable)"
DEAD = """def used() -> None:
    pass


def unused() -> None:
    pass


used()
"""


def discovered_classes() -> list[ClassName]:
    """The names of the test classes unittest discovers under tests/."""
    suite = unittest.TestLoader().discover(str(KIT / "tests"), pattern="test*.py", top_level_dir=str(KIT))
    return sorted({ClassName(type(case).__name__) for case in cases(suite)})


def findings(vulture: Path, folder: Path) -> list[Finding]:
    """What vulture prints for the Python files under ``folder``, the discovered test classes aside: none when clean."""
    if not vulture.is_file():
        raise AssertionError(f"{vulture} is missing, so nothing checks the kit for dead code: {INSTALL}")
    command = [str(vulture), str(folder), "--exclude", f"{KIT / 'bin'}/*,*/__pycache__/*",
               "--ignore-names", ",".join(discovered_classes())]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    return [Finding(line) for line in (run.stdout + run.stderr).splitlines()]


class DeadCode(unittest.TestCase):
    def test_the_kit_holds_no_dead_code(self) -> None:
        if found := findings(VULTURE, KIT):
            self.fail("vulture finds what nothing reads: remove each definition, or name it in "
                      "tests/dead_code_exemptions.py under the reason it is alive\n" + "\n".join(found))

    def test_an_unused_function_and_an_unparsable_file_are_found(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / "dead.py").write_text(DEAD)
            (Path(scratch) / "broken.py").write_text("def broken(:\n")
            found = "\n".join(findings(VULTURE, Path(scratch)))
        self.assertIn("dead.py:5: unused function 'unused'", found)
        self.assertIn("broken.py:1: invalid syntax", found)

    def test_a_missing_vulture_fails_with_its_install_line(self) -> None:
        with self.assertRaises(AssertionError) as raised:
            findings(KIT / "no-vulture", KIT)
        self.assertIn(INSTALL, str(raised.exception))
