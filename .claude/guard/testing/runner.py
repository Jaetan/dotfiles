#!/home/nicolas/.local/bin/python3.14
"""The guard kit's test runner: it runs the kit's own suites, and no run that tested nothing reads as a pass.

Run: `runner.py [--tests DIR] [--report FILE] [NAME ...]`. It discovers every `test*.py` module under DIR (default:
the kit's tests/ folder, a package whose parent, the kit root, is the folder its modules import from) with Python's
own unittest, and runs them in this process. A NAME selects the tests whose id it is, or whose id it begins at a dot
(a module, a class or a method: `tests.test_runner.Refusals`); a test module that cannot be imported is run, as an
error, whatever the selection, since what it holds is unknown. The runner imports nothing from the kit, so it can
run a planted copy's suite without the real kit's modules standing in for the copy's.

Before anything runs, the temporary folder tempfile names must lie outside every git work tree: inside one, the
kit's rules under test read the tests' scratch files as that repository's. The tests get a folder of their own made
in it, as TMPDIR, removed when the run ends.

Exit 0: no selection, and every test of the suite ran and passed, at least one. 1: a test failed or errored, was
skipped, failed as expected, succeeded unexpectedly or never started, or a module or class fixture errored. 2,
refused, one line on stderr and nothing on stdout: before any test runs, the temporary folder lies inside a git
work tree, DIR is not a package, or a NAME selects no test; after the run, no test ran. 3: a selection ran and
every test it selected passed, which is never a pass of the suite. Stdout lists every test that did not pass, then
a closing line of counts, naming the selection for a subset. --report FILE writes the exit status, the selection
and each selected test's outcome as JSON, a refusal included.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

TestId = NewType("TestId", str)
Selector = NewType("Selector", str)
Prose = NewType("Prose", str)
Count = NewType("Count", int)
ExitStatus = Literal[0, 1, 2, 3]
Outcome = Literal["passed", "failed", "error", "skipped", "expected failure", "unexpected success", "not run"]

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
FAILED_IMPORT = "unittest.loader._FailedTest."


class TestRow(TypedDict):
    """One selected test and its outcome."""

    id: TestId
    outcome: Outcome


class Report(TypedDict):
    """What --report writes: the exit status, the selection (empty for the whole suite) and every selected test."""

    status: ExitStatus
    selection: list[Selector]
    tests: list[TestRow]


class Tally(NamedTuple):
    """The counts the closing line gives."""

    tests: Count
    passed: Count
    failed: Count
    errors: Count
    skipped: Count
    unrun: Count


class Refusal(Exception):
    """A run refused, with the line that says why."""

    def __init__(self, reason: Prose) -> None:
        super().__init__(reason)
        self.reason = reason


class Outcomes(unittest.TestResult):
    """A result that also keeps the tests that passed, and holds each test's output until it is known to fail."""

    def __init__(self) -> None:
        super().__init__()
        self.buffer = True
        self.passed: list[unittest.TestCase] = []

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.passed.append(test)


def tree_holding(path: Path) -> Path | None:
    """The git work tree holding ``path``, or None outside every one."""
    return next((d for d in (path, *path.parents) if (d / ".git").exists()), None)


def cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    """Every test of a suite, in discovery order."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def selects(name: Selector, test: unittest.TestCase) -> bool:
    """Whether a selection name selects a test: its whole id, or a prefix of it ending at a dot."""
    return test.id() == name or test.id().startswith(name + ".")


def chosen(found: list[unittest.TestCase], names: list[Selector]) -> list[unittest.TestCase]:
    """The tests a selection runs: all for none, else those a name selects, and every failed import alike."""
    if not names:
        return found
    idle = [name for name in names if not any(selects(name, test) for test in found)]
    if idle:
        raise Refusal(Prose(f"REFUSED: the selection names no test: {', '.join(idle)}"))
    return [test for test in found
            if test.id().startswith(FAILED_IMPORT) or any(selects(name, test) for name in names)]


def owner(entry: unittest.TestCase) -> unittest.TestCase:
    """The test an entry of a result's lists belongs to: a subtest's own test, else the entry itself."""
    found: unittest.TestCase = getattr(entry, "test_case", entry)
    return found


def outcome(test: unittest.TestCase, result: Outcomes) -> Outcome:
    """A test's outcome, the worst it reported: one whose subtest failed has failed, one never reported has not
    run."""
    def holds(entries: Iterable[unittest.TestCase]) -> bool:
        return any(owner(entry) is test for entry in entries)
    if holds(entry for entry, _ in result.errors):
        return "error"
    if holds(entry for entry, _ in result.failures):
        return "failed"
    if holds(result.unexpectedSuccesses):
        return "unexpected success"
    if holds(entry for entry, _ in result.expectedFailures):
        return "expected failure"
    if holds(entry for entry, _ in result.skipped):
        return "skipped"
    if holds(result.passed):
        return "passed"
    return "not run"


def fixture_errors(result: Outcomes) -> list[unittest.TestCase]:
    """The errors raised outside every test, by a module's or a class's fixture."""
    return [entry for entry, _ in result.errors if not isinstance(owner(entry), unittest.TestCase)]


def tally(got: list[Outcome], result: Outcomes) -> Tally:
    """The counts of a finished run."""
    return Tally(Count(len(got)), Count(got.count("passed")),
                 Count(got.count("failed") + got.count("expected failure") + got.count("unexpected success")),
                 Count(got.count("error") + len(fixture_errors(result))), Count(got.count("skipped")),
                 Count(got.count("not run")))


def verdict(counts: Tally, names: list[Selector]) -> ExitStatus:
    """The exit status of a finished run."""
    if counts.unrun == counts.tests and not counts.errors:
        raise Refusal(Prose("REFUSED: no test ran"))
    if counts.failed + counts.errors + counts.skipped + counts.unrun:
        return 1
    return 3 if names else 0


def listing(selected: list[unittest.TestCase], got: list[Outcome], result: Outcomes) -> Iterator[Prose]:
    """A line per test that did not pass, with the tracebacks and skip reasons it reported under it."""
    reported = [*result.errors, *result.failures, *result.expectedFailures, *result.skipped]
    for entry in fixture_errors(result):
        yield Prose(f"ERROR {entry.id()}\n" + "".join(text for e, text in reported if e is entry).rstrip("\n"))
    for test, one in zip(selected, got, strict=True):
        if one != "passed":
            texts = "".join(text for entry, text in reported if owner(entry) is test)
            yield Prose(f"{one.upper()} {test.id()}\n{texts}".rstrip("\n"))


def closing(counts: Tally, names: list[Selector], suite: Count) -> Prose:
    """The closing line: the counts, and for a subset the selection it ran out of how many tests."""
    line = (f"{counts.passed} passed, {counts.failed} failed, {counts.errors} errors, {counts.skipped} skipped, "
            f"{counts.unrun} not run")
    if names:
        return Prose(f"selection {', '.join(names)} ran {counts.tests} of {suite} tests: {line}; a selection is "
                     "never a pass of the suite")
    return Prose(f"{counts.tests} tests: {line}")


def discovered(tests: Path) -> list[unittest.TestCase]:
    """Every test under the suite's folder, its parent first on sys.path in place of this file's folder."""
    root = tests.resolve().parent
    if sys.path and Path(sys.path[0] or ".").resolve() == HERE:
        sys.path[0] = str(root)
    else:
        sys.path.insert(0, str(root))
    try:
        suite = unittest.TestLoader().discover(str(tests.resolve()), pattern="test*.py", top_level_dir=str(root))
    except ImportError as fault:
        raise Refusal(Prose(f"REFUSED: the suite's folder {tests} is not a package: {fault}")) from None
    return list(cases(suite))


def run(tests: Path, names: list[Selector]) -> tuple[ExitStatus, list[TestRow]]:
    """Run the selection of the suite under ``tests`` in a temporary folder of its own, print what did not pass and
    the closing line; return the exit status and each selected test's outcome."""
    base = Path(tempfile.gettempdir()).resolve()
    if (tree := tree_holding(base)) is not None:
        raise Refusal(Prose(f"REFUSED: the temporary folder {base} lies inside the git work tree {tree}, where the "
                            "kit's rules read the tests' scratch files as that repository's; set TMPDIR outside every "
                            "git tree"))
    found = discovered(tests)
    selected = chosen(found, names)
    scratch = Path(tempfile.mkdtemp(prefix="guard-run-", dir=base))
    before = os.environ.get("TMPDIR")
    result = Outcomes()
    try:
        os.environ["TMPDIR"] = str(scratch)
        tempfile.tempdir = str(scratch)
        unittest.TestSuite(selected).run(result)
    finally:
        tempfile.tempdir = None
        if before is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = before
        shutil.rmtree(scratch, ignore_errors=True)
    got = [outcome(test, result) for test in selected]
    counts = tally(got, result)
    status = verdict(counts, names)
    for line in listing(selected, got, result):
        print(line)
    print(closing(counts, names, Count(len(found))))
    return status, [TestRow(id=TestId(test.id()), outcome=one) for test, one in zip(selected, got, strict=True)]


def main() -> ExitStatus:
    """Parse the arguments, run, write the report; name the exit status."""
    parser = argparse.ArgumentParser(description="Run the guard kit's own tests; see the module docstring.")
    _ = parser.add_argument("--tests", type=Path, default=KIT / "tests", help="the suite's folder, a package")
    _ = parser.add_argument("--report", type=Path, help="write the exit status and each test's outcome as JSON")
    _ = parser.add_argument("names", nargs="*", type=Selector, help="a test id, or a prefix of one ending at a dot")
    arguments = parser.parse_args()
    names: list[Selector] = arguments.names
    report = Report(status=2, selection=names, tests=[])
    try:
        report["status"], report["tests"] = run(arguments.tests, names)
    except Refusal as refusal:
        print(refusal.reason, file=sys.stderr)
    if arguments.report is not None:
        Path(arguments.report).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return report["status"]


if __name__ == "__main__":
    sys.exit(main())
