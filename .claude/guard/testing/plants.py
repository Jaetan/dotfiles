#!/home/nicolas/.local/bin/python3.14
"""The guard kit's plant harness: a test is shown to hold its code by planting a defect there and watching it die.

A plant is a file of the kit, an exact old text found there once, the new text that replaces it, and the tests that
hold that code. Run: `plants.py PLANTS.toml --out DIR [--kit DIR] [--timeout SECONDS]`, PLANTS.toml holding

    [[plant]]
    name = "what the plant breaks"
    file = "testing/runner.py"         # relative to the kit
    old = '''the exact text'''
    new = '''its replacement'''
    tests = ["tests.test_runner.Refusals.test_in_tree"]   # runner selection names

Every pass runs in a copy of the kit (DIR, default the kit holding this file) made for it in a folder of its own
under the temporary folder, without bytecode, ruff's cache, git's folder or the Go build cache under bin/go; the
real kit is only read, so nothing is planted in it and nothing is restored. A pass runs this folder's own runner.py
over the copy's tests, so a plant in the copy's runner cannot change how its own tests are judged; each pass gets a
fresh PYTHONPYCACHEPREFIX, made for the pass and removed after it, runs under an address-space cap and a time
limit, and leaves its output in DIR/baseline.log or DIR/plant-NN.log. Before any plant, every plant's old text is
found once in its file, and a baseline pass runs every plant's tests unplanted, which must pass.

A plant is killed when the runner exits 1 naming the tests that died, or when its tests outrun the time limit, since
a test that never ends never passes; it survives when its tests pass; any other exit gives no verdict. Stdout gives
a line per plant and a closing line of counts; DIR/summary.json holds each plant's verdict and the tests that died.

Exit 0: every plant killed. 1: a plant survived. 2: the plant file cannot be read, holds no plant or a plant missing
a field or naming no test, a plant's old text is not in its file exactly once, the baseline does not pass, or a pass
gave no verdict.
"""

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

from runner import Prose, Report, Selector, TestId

PlantName = NewType("PlantName", str)
KitPath = NewType("KitPath", str)
SourceText = NewType("SourceText", str)
RunnerStatus = NewType("RunnerStatus", int)
Seconds = NewType("Seconds", float)
Arg = NewType("Arg", str)
ExitStatus = Literal[0, 1, 2]
Verdict = Literal["killed", "survived", "no verdict"]

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "runner.py"
PRLIMIT = "/usr/bin/prlimit"
CAP_BYTES = 8 << 30
SKIPPED_NAMES = frozenset({"__pycache__", ".ruff_cache", ".git"})
SKIPPED_PATHS = (Path("bin") / "go",)


class Plant(NamedTuple):
    """One planted defect: the kit file, the exact text it replaces and its replacement, and the tests holding it."""

    name: PlantName
    file: KitPath
    old: SourceText
    new: SourceText
    tests: tuple[Selector, ...]


class Pass(NamedTuple):
    """A pass of the runner: its exit status (None when it outran the limit) and the tests that did not pass."""

    status: RunnerStatus | None
    died: tuple[TestId, ...]


class Judgement(NamedTuple):
    """A plant's verdict, the tests that died, why there is no verdict where there is none, and the pass's log."""

    plant: Plant
    verdict: Verdict
    died: tuple[TestId, ...]
    note: Prose
    log: Path


class SummaryRow(TypedDict):
    """A plant's row in summary.json."""

    name: PlantName
    file: KitPath
    verdict: Verdict
    died: list[TestId]
    note: Prose
    log: Prose


class PlantFault(Exception):
    """A plant file or a plant that cannot be run, with the line that says why."""

    def __init__(self, reason: Prose) -> None:
        super().__init__(reason)
        self.reason = reason


def read_plants(path: Path) -> list[Plant]:
    """The plants a TOML file holds, each with a name, a file, an old and a new text, and at least one test."""
    try:
        rows = tomllib.loads(path.read_text(encoding="utf-8")).get("plant", [])
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as fault:
        raise PlantFault(Prose(f"the plant file {path} cannot be read: {fault}")) from None
    plants: list[Plant] = []
    for index, row in enumerate(rows, 1):
        fields = (row.get("name"), row.get("file"), row.get("old"), row.get("new"))
        tests = row.get("tests")
        if (not all(isinstance(field, str) and field for field in fields) or not isinstance(tests, list) or not tests
                or not all(isinstance(test, str) and test for test in tests)):
            raise PlantFault(Prose(f"plant {index} of {path} needs a name, a file, an old and a new text, and tests"))
        name, file, old, new = fields
        plants.append(Plant(PlantName(name), KitPath(file), SourceText(old), SourceText(new),
                            tuple(Selector(test) for test in tests)))
    if not plants:
        raise PlantFault(Prose(f"the plant file {path} holds no [[plant]]"))
    return plants


def placed(plant: Plant, kit: Path) -> None:
    """Refuse a plant whose old text is not in its file exactly once."""
    try:
        found = (kit / plant.file).read_text(encoding="utf-8").count(plant.old)
    except OSError as fault:
        raise PlantFault(Prose(f"plant {plant.name!r}: {fault}")) from None
    if found != 1:
        raise PlantFault(Prose(f"plant {plant.name!r}: its old text is in {plant.file} {found} times, not once"))


def copy_kit(kit: Path, copy: Path) -> None:
    """Copy the kit's files and symbolic links into ``copy``, but its caches."""
    for folder, subfolders, files in os.walk(kit):
        here = Path(folder)
        subfolders[:] = [name for name in subfolders if name not in SKIPPED_NAMES
                         and (here / name).relative_to(kit) not in SKIPPED_PATHS]
        target = copy / here.relative_to(kit)
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            _ = shutil.copy2(here / name, target / name, follow_symlinks=False)


def apply(plant: Plant, root: Path) -> None:
    """Replace the plant's old text with its new one in the file under ``root``."""
    target = root / plant.file
    planted = target.read_text(encoding="utf-8").replace(plant.old, plant.new, 1)
    _ = target.write_text(planted, encoding="utf-8")


def run_pass(copy: Path, names: tuple[Selector, ...], log: Path, work: Path, timeout: Seconds) -> Pass:
    """Run the selection over the copy's tests on a fresh bytecode prefix, removed after the pass."""
    prefix = Path(tempfile.mkdtemp(prefix="pyc-", dir=work))
    report = work / f"{log.stem}.json"
    argv = [Arg(a) for a in (PRLIMIT, f"--as={CAP_BYTES}", "--", sys.executable, str(RUNNER), "--tests",
                             str(copy / "tests"), "--report", str(report), *names)]
    environ = {**os.environ, "PYTHONPYCACHEPREFIX": str(prefix), "TMPDIR": str(work)}
    status: RunnerStatus | None = None
    try:
        with log.open("w", encoding="utf-8") as out:
            child = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=environ, cwd=copy, start_new_session=True)
            try:
                status = RunnerStatus(child.wait(timeout=timeout))
            except subprocess.TimeoutExpired:
                pass
            finally:  # nothing the pass started outlives it, whether it ended, outran the limit or was interrupted
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                _ = child.wait()
    finally:
        shutil.rmtree(prefix, ignore_errors=True)
    if status is None:
        return Pass(None, ())
    try:
        got: Report = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Pass(status, ())
    return Pass(status, tuple(row["id"] for row in got["tests"] if row["outcome"] != "passed"))


def judged(plant: Plant, ran: Pass, log: Path, timeout: Seconds) -> Judgement:
    """A plant's verdict from its pass."""
    if ran.status is None:
        return Judgement(plant, "killed", (), Prose(f"its tests outran {timeout:g} s"), log)
    if ran.status == 1 and ran.died:
        return Judgement(plant, "killed", ran.died, Prose(""), log)
    if ran.status == 3:
        return Judgement(plant, "survived", (), Prose(""), log)
    return Judgement(plant, "no verdict", ran.died, Prose(f"the runner exited {ran.status}"), log)


def fresh_copy(kit: Path, work: Path, label: Prose) -> Path:
    """A new copy of the kit in a folder of its own under ``work``."""
    copy = work / label
    copy_kit(kit, copy)
    return copy


def judge(plants: list[Plant], kit: Path, out: Path, timeout: Seconds) -> list[Judgement]:
    """Check every plant is placed, run the baseline, then each plant in a copy of its own; return the verdicts."""
    for plant in plants:
        placed(plant, kit)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="guard-plants-"))
    try:
        every = tuple(dict.fromkeys(test for plant in plants for test in plant.tests))
        baseline = run_pass(fresh_copy(kit, work, Prose("baseline")), every, out / "baseline.log", work, timeout)
        if baseline.status != 3:
            raise PlantFault(Prose(f"the baseline, every plant's tests unplanted, did not pass (runner exit "
                                   f"{baseline.status}; died: {', '.join(baseline.died) or 'none'}); see "
                                   f"{out / 'baseline.log'}"))
        verdicts: list[Judgement] = []
        for index, plant in enumerate(plants, 1):
            copy = fresh_copy(kit, work, Prose(f"plant-{index:02d}"))
            apply(plant, copy)
            log = out / f"plant-{index:02d}.log"
            verdicts.append(judged(plant, run_pass(copy, plant.tests, log, work, timeout), log, timeout))
            shutil.rmtree(copy, ignore_errors=True)
        return verdicts
    finally:
        shutil.rmtree(work, ignore_errors=True)


def summary_row(judgement: Judgement) -> SummaryRow:
    """A verdict as summary.json writes it."""
    return SummaryRow(name=judgement.plant.name, file=judgement.plant.file, verdict=judgement.verdict,
                      died=list(judgement.died), note=judgement.note, log=Prose(str(judgement.log)))


def line(judgement: Judgement) -> Prose:
    """A verdict as stdout gives it."""
    said = {"killed": ", ".join(judgement.died) or judgement.note, "survived": ", ".join(judgement.plant.tests),
            "no verdict": f"{judgement.note}, see {judgement.log}"}[judgement.verdict]
    return Prose(f"{judgement.verdict:<10} {judgement.plant.name}: {said}")


def main() -> ExitStatus:
    """Parse the arguments, judge every plant, print and write the verdicts; name the exit status."""
    parser = argparse.ArgumentParser(description="Show the kit's tests red by planting defects; see the docstring.")
    _ = parser.add_argument("plants", type=Path, help="the TOML file of [[plant]] rows")
    _ = parser.add_argument("--out", type=Path, required=True, help="the folder for each pass's log and the summary")
    _ = parser.add_argument("--kit", type=Path, default=HERE.parent, help="the kit to copy (default: this one)")
    _ = parser.add_argument("--timeout", type=float, default=600.0, help="seconds a pass may take")
    arguments = parser.parse_args()
    out: Path = arguments.out
    try:
        verdicts = judge(read_plants(arguments.plants), Path(arguments.kit).resolve(), out,
                         Seconds(arguments.timeout))
    except PlantFault as fault:
        print(f"REFUSED: {fault.reason}", file=sys.stderr)
        return 2
    for judgement in verdicts:
        print(line(judgement))
    _ = (out / "summary.json").write_text(json.dumps([summary_row(j) for j in verdicts], indent=1) + "\n",
                                          encoding="utf-8")
    tally = {verdict: sum(j.verdict == verdict for j in verdicts) for verdict in ("killed", "survived", "no verdict")}
    print(f"{len(verdicts)} plants: {tally['killed']} killed, {tally['survived']} survived, "
          f"{tally['no verdict']} no verdict")
    if tally["no verdict"]:
        return 2
    return 1 if tally["survived"] else 0


if __name__ == "__main__":
    sys.exit(main())
