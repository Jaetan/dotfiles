"""The runner's exit statuses and refusals, each shown on a fixture suite run by testing/runner.py in a child process.

A fixture suite is a tests package written in a temporary folder; its tests touch a mark named after themselves in
the folder MARKS names, so a case can say which tests ran, and that none did before a refusal.
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
RUNNER = KIT / "testing" / "runner.py"

ModuleName = NewType("ModuleName", str)
SourceText = NewType("SourceText", str)
Selector = NewType("Selector", str)
Prose = NewType("Prose", str)
Mark = NewType("Mark", str)
ExitCode = NewType("ExitCode", int)

HEAD = SourceText("import os\nimport tempfile\nimport unittest\nfrom pathlib import Path\n\n\n"
                  "def mark(name):\n    Path(os.environ['MARKS'], name).touch()\n\n\n")
PASSING = SourceText(HEAD + "class A(unittest.TestCase):\n"
                     "    def test_one(self):\n        mark('one')\n\n"
                     "    def test_two(self):\n        mark('two')\n")
NEVER_STARTS = SourceText(HEAD + "class B(unittest.TestCase):\n"
                          "    def run(self, result=None):\n        return result\n\n"
                          "    def test_never(self):\n        mark('never')\n")


def with_test(body: SourceText) -> SourceText:
    """A module whose class A holds the test ``body`` writes beside test_one and test_two, which pass."""
    return SourceText(PASSING.replace("class A(unittest.TestCase):\n", "class A(unittest.TestCase):\n" + body))


class Ran(NamedTuple):
    """A child run of the runner: its exit status, stdout, stderr, the marks its tests left and its report."""

    status: ExitCode
    out: Prose
    err: Prose
    marks: frozenset[Mark]
    report: Prose

    def closing(self) -> Prose:
        """The last line of stdout."""
        return Prose((self.out.strip().splitlines() or [""])[-1])


def run_suite(modules: dict[ModuleName, SourceText], *names: Selector, tmpdir: Path | None = None) -> Ran:
    """Write a fixture suite of ``modules`` and run the runner over it with ``names``, TMPDIR ``tmpdir`` if given."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        tests = root / "fixture" / "tests"
        tests.mkdir(parents=True)
        (tests / "__init__.py").write_text("")
        for name, text in modules.items():
            (tests / f"{name}.py").write_text(text)
        marks = root / "marks"
        marks.mkdir()
        report = root / "report.json"
        environ = {**os.environ, "MARKS": str(marks)}
        if tmpdir is not None:
            environ["TMPDIR"] = str(tmpdir)
        child = subprocess.run([sys.executable, str(RUNNER), "--tests", str(tests), "--report", str(report), *names],
                               capture_output=True, text=True, check=False, timeout=60, env=environ)
        return Ran(ExitCode(child.returncode), Prose(child.stdout), Prose(child.stderr),
                   frozenset(Mark(p.name) for p in marks.iterdir()),
                   Prose(report.read_text() if report.exists() else ""))


class WholeSuite(unittest.TestCase):
    """A run of the whole suite exits 0 only when every test ran and passed; anything else that is not a refusal
    exits 1."""

    def test_every_test_passing_exits_zero(self) -> None:
        ran = run_suite({ModuleName("test_a"): PASSING})
        self.assertEqual((ran.status, ran.marks), (0, {"one", "two"}), ran.out + ran.err)
        self.assertEqual(ran.closing(), "2 tests: 2 passed, 0 failed, 0 errors, 0 skipped, 0 not run")

    def test_a_failure_exits_one(self) -> None:
        ran = run_suite({ModuleName("test_a"): with_test(SourceText("    def test_bad(self):\n"
                                                                    "        self.assertEqual(1, 2)\n\n"))})
        self.assertEqual(ran.status, 1, ran.out + ran.err)
        self.assertIn("FAILED tests.test_a.A.test_bad\nTraceback", ran.out)
        self.assertEqual(ran.closing(), "3 tests: 2 passed, 1 failed, 0 errors, 0 skipped, 0 not run")

    def test_a_failed_subtest_fails_its_test(self) -> None:
        ran = run_suite({ModuleName("test_a"): with_test(SourceText("    def test_sub(self):\n"
                                                                    "        for i in range(2):\n"
                                                                    "            with self.subTest(i=i):\n"
                                                                    "                self.assertEqual(i, 0)\n\n"))})
        self.assertEqual(ran.status, 1, ran.out + ran.err)
        self.assertIn("FAILED tests.test_a.A.test_sub\nTraceback", ran.out)
        self.assertEqual(ran.closing(), "3 tests: 2 passed, 1 failed, 0 errors, 0 skipped, 0 not run")

    def test_a_skip_is_a_failure(self) -> None:
        ran = run_suite({ModuleName("test_a"): with_test(SourceText("    def test_skip(self):\n"
                                                                    "        self.skipTest('not today')\n\n"))})
        self.assertEqual(ran.status, 1, ran.out + ran.err)
        self.assertIn("SKIPPED tests.test_a.A.test_skip\nnot today", ran.out)
        self.assertEqual(ran.closing(), "3 tests: 2 passed, 0 failed, 0 errors, 1 skipped, 0 not run")

    def test_an_expected_failure_is_a_failure(self) -> None:
        ran = run_suite({ModuleName("test_a"): with_test(SourceText("    @unittest.expectedFailure\n"
                                                                    "    def test_xfail(self):\n"
                                                                    "        self.assertEqual(1, 2)\n\n"))})
        self.assertEqual(ran.status, 1, ran.out + ran.err)
        self.assertIn("EXPECTED FAILURE tests.test_a.A.test_xfail", ran.out)

    def test_an_unexpected_success_is_a_failure(self) -> None:
        ran = run_suite({ModuleName("test_a"): with_test(SourceText("    @unittest.expectedFailure\n"
                                                                    "    def test_xpass(self):\n"
                                                                    "        pass\n\n"))})
        self.assertEqual(ran.status, 1, ran.out + ran.err)
        self.assertIn("UNEXPECTED SUCCESS tests.test_a.A.test_xpass", ran.out)

    def test_a_test_that_never_starts_is_a_failure(self) -> None:
        ran = run_suite({ModuleName("test_a"): PASSING, ModuleName("test_b"): NEVER_STARTS})
        self.assertEqual((ran.status, ran.marks), (1, {"one", "two"}), ran.out + ran.err)
        self.assertIn("NOT RUN tests.test_b.B.test_never", ran.out)
        self.assertEqual(ran.closing(), "3 tests: 2 passed, 0 failed, 0 errors, 0 skipped, 1 not run")

    def test_a_fixture_error_exits_one_though_no_test_ran(self) -> None:
        ran = run_suite({ModuleName("test_a"): SourceText(
            HEAD + "class C(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n"
            "        raise RuntimeError('no fixture')\n\n    def test_c(self):\n        mark('c')\n")})
        self.assertEqual((ran.status, ran.marks), (1, frozenset()), ran.out + ran.err)
        self.assertIn("ERROR setUpClass (tests.test_a.C)", ran.out)

    def test_an_unimportable_module_errors_any_selection(self) -> None:
        ran = run_suite({ModuleName("test_a"): PASSING, ModuleName("test_b"): SourceText("import no_such_module\n")},
                        Selector("tests.test_a.A.test_one"))
        self.assertEqual(ran.status, 1, ran.out + ran.err)
        self.assertIn("ERROR unittest.loader._FailedTest.tests.test_b", ran.out)

    def test_the_tests_get_a_temporary_folder_of_their_own(self) -> None:
        ran = run_suite({ModuleName("test_a"): SourceText(
            HEAD + "class T(unittest.TestCase):\n    def test_tmp(self):\n"
            "        mark(os.environ['TMPDIR'].replace('/', '|') + '=' + tempfile.gettempdir().replace('/', '|'))\n")})
        self.assertEqual(ran.status, 0, ran.out + ran.err)
        (seen,) = ran.marks
        environ, gettempdir = (Path(side.replace("|", "/")) for side in seen.split("="))
        self.assertEqual(environ, gettempdir)
        self.assertEqual(environ.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(environ.name.startswith("guard-run-"), environ)
        self.assertFalse(environ.exists(), "the tests' folder outlived the run")

    def test_the_report_holds_each_outcome(self) -> None:
        ran = run_suite({ModuleName("test_a"): with_test(SourceText("    def test_skip(self):\n"
                                                                    "        self.skipTest('x')\n\n"))})
        self.assertEqual(json.loads(ran.report), {
            "status": 1, "selection": [],
            "tests": [{"id": "tests.test_a.A.test_one", "outcome": "passed"},
                      {"id": "tests.test_a.A.test_skip", "outcome": "skipped"},
                      {"id": "tests.test_a.A.test_two", "outcome": "passed"}]})


class Selection(unittest.TestCase):
    """A selection runs the tests its names select, and exits 3, never 0, when they all pass."""

    def test_a_passing_subset_exits_three(self) -> None:
        ran = run_suite({ModuleName("test_a"): PASSING}, Selector("tests.test_a.A.test_one"))
        self.assertEqual((ran.status, ran.marks), (3, {"one"}), ran.out + ran.err)
        self.assertEqual(ran.closing(), "selection tests.test_a.A.test_one ran 1 of 2 tests: 1 passed, 0 failed, "
                                        "0 errors, 0 skipped, 0 not run; a selection is never a pass of the suite")

    def test_a_class_name_selects_its_tests(self) -> None:
        ran = run_suite({ModuleName("test_a"): PASSING}, Selector("tests.test_a.A"))
        self.assertEqual((ran.status, ran.marks), (3, {"one", "two"}), ran.out + ran.err)


class Refusals(unittest.TestCase):
    """A run that cannot test what it claims to is refused with exit 2, one line on stderr and nothing on stdout."""

    def assertRefused(self, ran: Ran, reason: Prose) -> None:
        """The run was refused for ``reason``, and no test of the fixture ran."""
        self.assertEqual((ran.status, ran.out, ran.marks), (2, "", frozenset()), ran.out + ran.err)
        self.assertIn(reason, ran.err)
        self.assertEqual(len(ran.err.strip().splitlines()), 1, ran.err)
        self.assertEqual(json.loads(ran.report)["status"], 2)

    def test_temporary_folder_in_a_git_tree(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch).resolve() / "repo"
            (repo / "tmp").mkdir(parents=True)
            _ = subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True,
                               env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
            ran = run_suite({ModuleName("test_a"): PASSING}, tmpdir=repo / "tmp")
        self.assertRefused(ran, Prose(f"lies inside the git work tree {repo}"))

    def test_no_test_found(self) -> None:
        self.assertRefused(run_suite({}), Prose("REFUSED: no test ran"))

    def test_no_test_started(self) -> None:
        self.assertRefused(run_suite({ModuleName("test_b"): NEVER_STARTS}), Prose("REFUSED: no test ran"))

    def test_a_selection_naming_no_test(self) -> None:
        ran = run_suite({ModuleName("test_a"): PASSING}, Selector("tests.test_a.A.test_one"),
                        Selector("tests.test_a.Nope"))
        self.assertRefused(ran, Prose("REFUSED: the selection names no test: tests.test_a.Nope"))

    def test_a_name_selects_at_a_dot_only(self) -> None:
        ran = run_suite({ModuleName("test_a"): PASSING}, Selector("tests.test_a.A.test_on"))
        self.assertRefused(ran, Prose("REFUSED: the selection names no test: tests.test_a.A.test_on"))

    def test_a_suite_folder_that_is_no_package(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch) / "report.json"
            child = subprocess.run([sys.executable, str(RUNNER), "--tests", scratch, "--report", str(report)],
                                   capture_output=True, text=True, check=False, timeout=60,
                                   env={**os.environ, "MARKS": scratch})
            ran = Ran(ExitCode(child.returncode), Prose(child.stdout), Prose(child.stderr), frozenset(),
                      Prose(report.read_text()))
        self.assertRefused(ran, Prose("is not a package"))
