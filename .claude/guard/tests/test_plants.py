"""The plant harness's verdicts and refusals, each shown by running testing/plants.py over a fixture kit.

The fixture kit is a module m.py, a tests package holding it, and the caches a copy leaves out. Its test_double
appends the bytecode prefix it runs on to the file PREFIXES names, and the prefix of any earlier pass that still
exists to that file's .stale sibling, so a case can say which pass ran on which prefix and whether one outlived its
pass; its test_other passes only in a copy without the caches.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType

KIT = Path(__file__).resolve().parents[1]
PLANTS = KIT / "testing" / "plants.py"

SourceText = NewType("SourceText", str)
Prose = NewType("Prose", str)
ExitCode = NewType("ExitCode", int)
Seconds = NewType("Seconds", float)

MODULE = SourceText("def double(x):\n    return x * 2\n")
TESTS = SourceText("""import os
import sys
import unittest
from pathlib import Path

import m


class M(unittest.TestCase):
    def test_double(self):
        seen = Path(os.environ["PREFIXES"])
        earlier = seen.read_text().split() if seen.exists() else []
        with seen.open("a") as out:
            out.write(sys.pycache_prefix + "\\n")
        with Path(str(seen) + ".stale").open("a") as stale:
            stale.writelines(prefix + "\\n" for prefix in earlier if Path(prefix).exists())
        self.assertEqual(m.double(3), 6)

    def test_other(self):
        copy = Path(m.__file__).parent
        self.assertEqual([(copy / name).exists() for name in ("bin/keep", "bin/go", "__pycache__")],
                         [True, False, False])

    def test_red(self):
        self.fail("red unplanted")
""")
TRIPLED = SourceText('name = "doubling triples"\nfile = "m.py"\nold = "x * 2"\nnew = "x * 3"\n')


class Harnessed(NamedTuple):
    """A run of the harness over a fixture kit: its exit status, stdout and stderr, and the folders it used."""

    status: ExitCode
    out: Prose
    err: Prose
    kit: Path
    work: Path
    out_dir: Path
    prefixes: Path


CACHES = ("__pycache__/m.cpython-314.pyc", "bin/go/cache.bin")


def harness(root: Path, plants: SourceText, timeout: Seconds = Seconds(600)) -> Harnessed:
    """Write the fixture kit and ``plants`` under ``root`` and run the harness over them, TMPDIR a folder of its
    own."""
    kit = root / "kit"
    (kit / "tests").mkdir(parents=True)
    for name in (*CACHES, "bin/keep"):
        (kit / name).parent.mkdir(parents=True, exist_ok=True)
        (kit / name).write_text("")
    (kit / "m.py").write_text(MODULE)
    (kit / "tests" / "__init__.py").write_text("")
    (kit / "tests" / "test_m.py").write_text(TESTS)
    (root / "plants.toml").write_text(plants)
    work = root / "tmp"
    work.mkdir()
    prefixes = root / "prefixes"
    child = subprocess.run([sys.executable, str(PLANTS), str(root / "plants.toml"), "--out", str(root / "out"),
                            "--kit", str(kit), "--timeout", f"{timeout:g}"], capture_output=True, text=True, check=False, timeout=120,
                           env={**os.environ, "TMPDIR": str(work), "PREFIXES": str(prefixes)})
    return Harnessed(ExitCode(child.returncode), Prose(child.stdout), Prose(child.stderr), kit, work, root / "out",
                     prefixes)


class Verdicts(unittest.TestCase):
    """One run of two plants, the same defect held by a test that calls the code and by one that does not."""

    harnessed: Harnessed

    @classmethod
    def setUpClass(cls) -> None:
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        cls.harnessed = harness(Path(scratch.name), SourceText(
            f'[[plant]]\n{TRIPLED}tests = ["tests.test_m.M.test_double"]\n\n'
            f'[[plant]]\n{TRIPLED.replace("triples", "triples, held by a test that never calls it")}'
            'tests = ["tests.test_m.M.test_other"]\n'))

    def test_killed_and_survived(self) -> None:
        ran = self.harnessed
        self.assertEqual(ran.status, 1, ran.out + ran.err)
        self.assertEqual(ran.out.splitlines(), [
            "killed     doubling triples: tests.test_m.M.test_double",
            "survived   doubling triples, held by a test that never calls it: tests.test_m.M.test_other",
            "2 plants: 1 killed, 1 survived, 0 no verdict"])
        summary = json.loads((ran.out_dir / "summary.json").read_text())
        self.assertEqual([(row["verdict"], row["died"]) for row in summary],
                         [("killed", ["tests.test_m.M.test_double"]), ("survived", [])])

    def test_each_pass_has_a_fresh_prefix_removed_after_it(self) -> None:
        ran = self.harnessed
        prefixes = ran.prefixes.read_text().split()
        self.assertEqual(len(prefixes), 2, "test_double runs in the baseline and in the first plant's pass")
        self.assertEqual(len(set(prefixes)), 2, prefixes)
        self.assertEqual(Path(str(ran.prefixes) + ".stale").read_text(), "", "a prefix outlived its pass")
        self.assertFalse(any(Path(prefix).exists() for prefix in prefixes))

    def test_the_real_kit_is_only_read(self) -> None:
        ran = self.harnessed
        self.assertEqual((ran.kit / "m.py").read_text(), MODULE)
        self.assertEqual(sorted(p.relative_to(ran.kit).as_posix() for p in ran.kit.rglob("*")),
                         ["__pycache__", CACHES[0], "bin", "bin/go", CACHES[1], "bin/keep", "m.py", "tests",
                          "tests/__init__.py", "tests/test_m.py"])
        self.assertEqual(list(ran.work.iterdir()), [], "the harness left its copies in the temporary folder")


class Outran(unittest.TestCase):
    """A plant whose tests outrun the time limit is killed, since a test that never ends never passes."""

    def test_a_pass_past_its_limit_is_killed(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            ran = harness(Path(scratch), SourceText(
                '[[plant]]\nname = "doubling hangs"\nfile = "m.py"\nold = "return x * 2"\n'
                'new = "while True:\\n        pass"\ntests = ["tests.test_m.M.test_double"]\n'), Seconds(3))
            self.assertEqual(list(ran.work.iterdir()), [], "the harness left its copies in the temporary folder")
        self.assertEqual((ran.status, ran.out.splitlines()),
                         (0, ["killed     doubling hangs: its tests outran 3 s",
                              "1 plants: 1 killed, 0 survived, 0 no verdict"]), ran.out + ran.err)


class Refusals(unittest.TestCase):
    """A plant that cannot be judged is refused with exit 2 before any plant's pass."""

    def test_an_old_text_not_found_once(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            ran = harness(Path(scratch), SourceText(
                f'[[plant]]\n{TRIPLED.replace("x * 2", "x * 4")}tests = ["tests.test_m.M.test_double"]\n'))
            self.assertEqual((ran.status, ran.out, ran.prefixes.exists()), (2, "", False), ran.out + ran.err)
        self.assertIn("REFUSED: plant 'doubling triples': its old text is in m.py 0 times, not once", ran.err)

    def test_a_red_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            ran = harness(Path(scratch), SourceText(f'[[plant]]\n{TRIPLED}tests = ["tests.test_m.M.test_red"]\n'))
        self.assertEqual((ran.status, ran.out), (2, ""), ran.out + ran.err)
        self.assertIn("REFUSED: the baseline, every plant's tests unplanted, did not pass (runner exit 1; died: "
                      "tests.test_m.M.test_red)", ran.err)

    def test_a_plant_file_holding_no_plant(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            ran = harness(Path(scratch), SourceText("# nothing planted\n"))
        self.assertEqual((ran.status, ran.out), (2, ""), ran.out + ran.err)
        self.assertIn("holds no [[plant]]", ran.err)

    def test_a_plant_naming_no_test(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            ran = harness(Path(scratch), SourceText(f"[[plant]]\n{TRIPLED}tests = []\n"))
        self.assertEqual((ran.status, ran.out), (2, ""), ran.out + ran.err)
        self.assertIn("needs a name, a file, an old and a new text, and tests", ran.err)
