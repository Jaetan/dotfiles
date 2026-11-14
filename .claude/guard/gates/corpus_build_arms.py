#!/home/nicolas/.local/bin/python3.14
"""The corpus builder check's arms: each builder defect it holds turns it red, and an empty fixture set exits 2.

`corpus_build_arms.py [--work DIR]` runs corpus_build_check.py over the fixture set beside it with:

  baseline       the real builder: exit 0.
  plants         a copy of corpus_build.py with one defect written in, each exit 1 naming exactly its differences:
                 an unchanged transcript read again, a new one missed, a changed one missed, a command counted
                 once per occurrence rather than once per tool_use id, a non-Bash tool_use read as a command.
  unusable       a fixture set with no stage, one writing no transcript, one expecting no command, one whose
                 transcript its expect.json does not name, and a work folder not empty: exit 2 with that reason.
  restored       the real builder again: exit 0, its source byte-identical to the start.
  real corpus    corpus.jsonl, transcripts.jsonl and stats.json under ~/.local/state/claude-guard/corpus/ with the
                 same size and modification time before and after.

Writes beneath --work (default a new temporary folder, kept and printed) one folder and one log per arm, and
arms.json. Exit 0 every arm as expected; 1 an arm not; 2 a plant's text is not in the builder's source once.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType

from corpus_build import INDEX, STATS, ByteCount, Nanoseconds
from corpus_build_check import Difference, Field, Prose, StageName
from corpus_common import CORPUS, CORPUS_DIR, PYTHON

ArmName = NewType("ArmName", str)
SourceText = NewType("SourceText", str)
Digest = NewType("Digest", str)
Status = NewType("Status", int)
ExitStatus = Literal[0, 1, 2]

HERE = Path(__file__).resolve().parent
CHECK = HERE / "corpus_build_check.py"
BUILDER = HERE / "corpus_build.py"
COMMON = HERE / "corpus_common.py"
FIXTURES = HERE / "corpus_build_fixtures"
REAL = [CORPUS, CORPUS_DIR / INDEX, CORPUS_DIR / STATS]

FIRST = StageName("1-first-build")
SAME = StageName("2-unchanged")
MOVED = StageName("3-one-new-one-changed")
READ = Field("read")
CORPUS_FIELD = Field("corpus")
UNCHANGED_CONDITION = SourceText('if before and before["size"] == st.st_size and before["mtime_ns"] == st.st_mtime_ns:')


class Spot(NamedTuple):
    """Where the check finds a difference: a stage and a field of its build."""

    stage: StageName
    field: Field


class Plant(NamedTuple):
    """A builder defect: the source replacements writing it, and the differences the check must name for it."""

    edits: list[tuple[SourceText, SourceText]]
    spots: set[Spot]


class CheckRun(NamedTuple):
    """One run of the check: its exit status, the differences its report names and its output."""

    status: Status
    spots: set[Spot]
    output: Prose


class FileState(NamedTuple):
    """A real builder output's size and modification time."""

    size: ByteCount
    mtime_ns: Nanoseconds


class Arm(NamedTuple):
    """One arm's outcome."""

    name: ArmName
    want: ExitStatus
    got: Status
    ok: bool
    note: Prose


PLANTS: dict[ArmName, Plant] = {
    ArmName("an unchanged transcript read again"): Plant(
        [(UNCHANGED_CONDITION, SourceText("if False:"))],
        {Spot(SAME, READ), Spot(MOVED, READ)}),
    ArmName("a new transcript missed"): Plant(
        [(SourceText("        else:\n            todo.append((path, kind))"),
          SourceText("        elif before or not old:\n            todo.append((path, kind))")),
         (SourceText("ordered = [current[PathText(str(p))] for p, _ in found]"),
          SourceText("ordered = [current[PathText(str(p))] for p, _ in found if PathText(str(p)) in current]"))],
        {Spot(MOVED, READ), Spot(MOVED, CORPUS_FIELD), Spot(MOVED, Field("index proj/s3.jsonl"))}),
    ArmName("a changed transcript missed"): Plant(
        [(UNCHANGED_CONDITION, SourceText("if before:"))],
        {Spot(MOVED, READ), Spot(MOVED, CORPUS_FIELD), Spot(MOVED, Field("index proj/s1/subagents/agent-a1.jsonl"))}),
    ArmName("a command counted twice"): Plant(
        [(SourceText("counts = collections.Counter(command for _, command in carried)"),
          SourceText('counts = collections.Counter(command for e in ordered for _, command in e["calls"])'))],
        {Spot(s, CORPUS_FIELD) for s in (FIRST, SAME, MOVED)}),
    ArmName("a non-Bash block read as a command"): Plant(
        [(SourceText(' and block.get("name") == "Bash"'), SourceText(""))],
        {Spot(s, f) for s in (FIRST, SAME, MOVED) for f in (CORPUS_FIELD, Field("index proj/s1.jsonl"))}),
}


def digest(path: Path) -> Digest:
    """The sha256 of a file's bytes."""
    return Digest(hashlib.sha256(path.read_bytes()).hexdigest())


def real_state() -> list[FileState | None]:
    """Each real builder output's size and modification time, None where it is missing."""
    states: list[FileState | None] = []
    for path in REAL:
        st = path.stat() if path.exists() else None
        states.append(FileState(ByteCount(st.st_size), Nanoseconds(st.st_mtime_ns)) if st else None)
    return states


def planted(folder: Path, plant: Plant) -> Path:
    """A copy of the builder with the plant's edits, beside a copy of its common module; SystemExit 2 when an edit's
    text is not in the source exactly once."""
    text = SourceText(BUILDER.read_text(encoding="utf-8"))
    for old, new in plant.edits:
        if (n := text.count(old)) != 1:
            print(f"corpus_build_arms: a plant's text is in corpus_build.py {n} times, not once: {old!r}",
                  file=sys.stderr)
            sys.exit(2)
        text = SourceText(text.replace(old, new))
    folder.mkdir(parents=True)
    shutil.copyfile(COMMON, folder / COMMON.name)
    (folder / BUILDER.name).write_text(text, encoding="utf-8")
    return folder / BUILDER.name


def run_check(work: Path, name: ArmName, builder: Path, fixtures: Path) -> CheckRun:
    """Run the check for one arm, its output logged."""
    slug = name.replace(" ", "-")
    report, log = work / f"{slug}.json", work / f"{slug}.log"
    with log.open("w", encoding="utf-8") as sink:
        status = Status(subprocess.run([str(PYTHON), str(CHECK), "--builder", str(builder), "--fixtures",
                                        str(fixtures), "--work", str(work / slug), "--report", str(report)],
                                       stdout=sink, stderr=subprocess.STDOUT, check=False).returncode)
    output = Prose(log.read_text(encoding="utf-8"))
    if not report.exists():
        return CheckRun(status, set(), output)
    rows: list[Difference] = json.loads(report.read_text(encoding="utf-8"))
    return CheckRun(status, {Spot(r["stage"], r["field"]) for r in rows}, output)


def unusable(work: Path) -> list[tuple[ArmName, Path, Prose]]:
    """The arms the check refuses to judge, each with its fixture set and the reason it must print; the work
    folder arm's folder is left holding a file."""
    no_stage = work / "fixtures-no-stage"
    no_stage.mkdir()
    no_transcript = work / "fixtures-no-transcript" / "1-nothing"
    no_command = work / "fixtures-no-command" / "1-no-bash"
    unnamed = work / "fixtures-unnamed-transcript" / "1-unnamed"
    for stage, index, corpus in ((no_transcript, "{}", "[]"),
                                 (no_command, '{"proj/s2.jsonl": {"kind": "main", "bad_lines": 0, "calls": []}}', "[]"),
                                 (unnamed, "{}", '[{"n": 1, "cmd": "ls -la"}]')):
        stage.mkdir(parents=True)
        (stage / "expect.json").write_text(f'{{"index": {index}, "corpus": {corpus}}}\n', encoding="utf-8")
    for stage in (no_command, unnamed):
        (stage / "projects" / "proj").mkdir(parents=True)
        shutil.copyfile(FIXTURES / FIRST / "projects" / "proj" / "s2.jsonl", stage / "projects" / "proj" / "s2.jsonl")
    (work / "work-folder-not-empty").mkdir()
    (work / "work-folder-not-empty" / "left").write_text("left\n", encoding="utf-8")
    return [(ArmName("no stage"), no_stage, Prose("holds no stage")),
            (ArmName("no transcript"), no_transcript.parent, Prose("writes no transcript")),
            (ArmName("no command"), no_command.parent, Prose("expects no command")),
            (ArmName("a transcript expect.json does not name"), unnamed.parent, Prose("are not the ones expect.json")),
            (ArmName("work folder not empty"), FIXTURES, Prose("is not empty"))]


def arms(work: Path) -> ExitStatus:
    """Run every arm, write arms.json, print one line per arm."""
    before_state = real_state()
    before_digest = digest(BUILDER)
    results: list[Arm] = []

    def record(name: ArmName, want: ExitStatus, got: Status, ok: bool, note: Prose) -> None:
        results.append(Arm(name, want, got, ok, note))

    run = run_check(work, ArmName("baseline"), BUILDER, FIXTURES)
    record(ArmName("baseline"), 0, run.status, run.status == 0 and not run.spots, Prose(f"named {sorted(run.spots)}"))
    for name, plant in PLANTS.items():
        run = run_check(work, name, planted(work / "plants" / name.replace(" ", "-"), plant), FIXTURES)
        record(name, 1, run.status, run.status == 1 and run.spots == plant.spots,
               Prose(f"named {sorted(run.spots)}, want {sorted(plant.spots)}"))
    for name, fixtures, reason in unusable(work):
        run = run_check(work, name, BUILDER, fixtures)
        record(name, 2, run.status, run.status == 2 and reason in run.output, Prose(f"output {run.output.strip()!r}"))
    run = run_check(work, ArmName("restored"), BUILDER, FIXTURES)
    same_source = digest(BUILDER) == before_digest
    record(ArmName("restored"), 0, run.status, run.status == 0 and not run.spots and same_source,
           Prose(f"named {sorted(run.spots)}; builder source unchanged {same_source}"))
    untouched = real_state() == before_state
    record(ArmName("real corpus untouched"), 0, Status(0 if untouched else 1), untouched,
           Prose(f"{[str(p) for p in REAL]} size and mtime the same before and after: {untouched}"))
    (work / "arms.json").write_text(json.dumps([a._asdict() for a in results], indent=1) + "\n", encoding="utf-8")
    bad = [a for a in results if not a.ok]
    for a in results:
        print(f"{'ok ' if a.ok else 'BAD'} {a.name}: want {a.want}, got {a.got}; {a.note}")
    print(f"{len(results) - len(bad)} of {len(results)} arms as expected; {work / 'arms.json'}")
    return 1 if bad else 0


def main() -> ExitStatus:
    """Parse the arguments and run the arms."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--work", type=Path, help="an empty or missing folder for the arms' runs, kept")
    args = parser.parse_args()
    work: Path = args.work if args.work is not None else Path(tempfile.mkdtemp(prefix="corpus-build-arms-"))
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        print(f"corpus_build_arms: the work folder {work} is not empty", file=sys.stderr)
        return 2
    return arms(work)


if __name__ == "__main__":
    sys.exit(main())
