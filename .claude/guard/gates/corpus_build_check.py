#!/home/nicolas/.local/bin/python3.14
"""Hold the corpus builder to a fixture set: what each build reads, and the corpus and index it writes.

`corpus_build_check.py [--builder PATH] [--fixtures DIR] [--work DIR] [--report PATH]` takes the stages of the
fixture set (default corpus_build_fixtures/ beside it), one folder each in name order: the transcripts the stage
writes under projects/, and expect.json naming each one's kind, bad lines and calls and the whole corpus after the
build. Stage by stage it copies the transcripts into a projects folder of its own, each stage's with a modification
time of its own, and runs the builder (default corpus_build.py beside it) with that folder as --projects and a
folder of its own as --out, so neither ~/.claude/projects nor the real corpus is read or written. After each build:

  read     stats.json's read_this_build against the transcripts the stage wrote, their number, bytes and lines:
           a transcript left alone since the last build is not read again.
  index    every row of transcripts.jsonl against its transcript's latest version: kind, bad lines and calls as
           the fixture states them, size, mtime_ns and line count as the file stands; a row missing, extra or
           twice differs.
  corpus   corpus.jsonl against the stage's expected commands, in order, each with its count.

Exit 0 every build agrees; 1 a difference, each printed as `<stage> <field>: got ..., want ...` and written to
--report as {stage, field, got, want}; 2 the fixture set is empty (no stage, no transcript or no expected command)
or cannot be read, --work is not empty, or the builder exits non-zero. --work defaults to a temporary folder,
removed at the end.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

from corpus_build import ByteCount, Extract, LineCount, Nanoseconds, PathText, ToolUseId, TranscriptKind
from corpus_common import PYTHON, CorpusRow, RowId, ShellText, UseCount, corpus_rows

RelPath = NewType("RelPath", str)
StageName = NewType("StageName", str)
Field = NewType("Field", str)
JsonText = NewType("JsonText", str)
Prose = NewType("Prose", str)
TranscriptBytes = NewType("TranscriptBytes", bytes)
TranscriptCount = NewType("TranscriptCount", int)
ExitStatus = Literal[0, 1, 2]

HERE = Path(__file__).resolve().parent
BUILDER = HERE / "corpus_build.py"
FIXTURES = HERE / "corpus_build_fixtures"
BASE_NS = 1_700_000_000 * 10**9
STAGE_NS = 1000 * 10**9
BUILD_SECONDS = 600


class TranscriptWant(TypedDict):
    """What the fixture states of one transcript it writes."""

    kind: TranscriptKind
    bad_lines: LineCount
    calls: list[tuple[ToolUseId, ShellText]]


class CommandWant(TypedDict):
    """One expected corpus row, its index being its place in the list."""

    n: UseCount
    cmd: ShellText


class StageWant(TypedDict):
    """A stage's expect.json."""

    index: dict[RelPath, TranscriptWant]
    corpus: list[CommandWant]


class ReadCounts(TypedDict):
    """What one build read."""

    transcripts: TranscriptCount
    bytes: ByteCount
    lines: LineCount


class BuildStats(TypedDict):
    """The part of stats.json the check reads."""

    read_this_build: ReadCounts


class Difference(TypedDict):
    """One field of one build that differs from the fixture."""

    stage: StageName
    field: Field
    got: JsonText
    want: JsonText


class Stage(NamedTuple):
    """One fixture stage: its name, its transcripts' folder, the transcripts it writes and what it expects."""

    name: StageName
    projects: Path
    files: list[RelPath]
    want: StageWant


class Unusable(Exception):
    """The fixture set or the build cannot be judged."""


def stages(fixtures: Path) -> list[Stage]:
    """Every stage of the fixture set, in name order, each one's transcripts matching its expected index."""
    found: list[Stage] = []
    for folder in sorted(p for p in fixtures.iterdir() if p.is_dir()):
        try:
            want: StageWant = json.loads((folder / "expect.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise Unusable(Prose(f"stage {folder.name}: expect.json cannot be read: {error!r}")) from error
        projects = folder / "projects"
        files = sorted(RelPath(str(p.relative_to(projects))) for p in projects.rglob("*") if p.is_file())
        if set(files) != set(want["index"]):
            raise Unusable(Prose(f"stage {folder.name}: its transcripts {files} are not the ones expect.json names "
                                 f"{sorted(want['index'])}"))
        found.append(Stage(StageName(folder.name), projects, files, want))
    if not found:
        raise Unusable(Prose(f"the fixture set {fixtures} holds no stage"))
    if not any(s.files for s in found):
        raise Unusable(Prose(f"the fixture set {fixtures} writes no transcript"))
    if not any(s.want["corpus"] for s in found):
        raise Unusable(Prose(f"the fixture set {fixtures} expects no command"))
    return found


def line_count(data: TranscriptBytes) -> LineCount:
    """The lines a reader iterating the file sees, the last one counted without its newline."""
    return LineCount(data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0))


def shown(value: ReadCounts | list[CorpusRow] | Extract | None) -> JsonText:
    """A value as JSON text, keys sorted."""
    return JsonText(json.dumps(value, ensure_ascii=False, sort_keys=True))


def run_build(builder: Path, projects: Path, out: Path, log: Path) -> None:
    """Run the builder over the projects folder into out, its output logged; Unusable when it does not exit 0."""
    argv = [str(PYTHON), str(builder), "--projects", str(projects), "--out", str(out)]
    with log.open("w", encoding="utf-8") as sink:
        try:
            status = subprocess.run(argv, stdout=sink, stderr=subprocess.STDOUT, check=False,
                                    timeout=BUILD_SECONDS).returncode
        except subprocess.TimeoutExpired as error:
            raise Unusable(Prose(f"the builder ran past {BUILD_SECONDS} s; {log}")) from error
    if status != 0:
        raise Unusable(Prose(f"the builder exited {status}; {log}"))


def compare(stage: Stage, projects: Path, out: Path, wants: dict[RelPath, TranscriptWant]) -> list[Difference]:
    """The differences between one build's outputs and the fixture."""
    diffs: list[Difference] = []

    def differ(field: Field, got: JsonText, want: JsonText) -> None:
        if got != want:
            diffs.append(Difference(stage=stage.name, field=field, got=got, want=want))

    written = [TranscriptBytes((projects / f).read_bytes()) for f in stage.files]
    stats: BuildStats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    differ(Field("read"), shown(stats["read_this_build"]),
           shown(ReadCounts(transcripts=TranscriptCount(len(written)), bytes=ByteCount(sum(len(d) for d in written)),
                            lines=LineCount(sum(line_count(d) for d in written)))))

    with (out / "transcripts.jsonl").open(encoding="utf-8", errors="surrogateescape") as rows:
        index: list[Extract] = [json.loads(row) for row in rows]
    by_path: dict[PathText, list[Extract]] = {}
    for row in index:
        by_path.setdefault(row["path"], []).append(row)
    expected: dict[PathText, RelPath] = {PathText(str(projects / rel)): rel for rel in wants}
    for path, rel in expected.items():
        st = Path(path).stat()
        t = wants[rel]
        want = Extract(path=path, kind=t["kind"], size=ByteCount(st.st_size), mtime_ns=Nanoseconds(st.st_mtime_ns),
                       lines=line_count(TranscriptBytes(Path(path).read_bytes())), bad_lines=t["bad_lines"],
                       calls=t["calls"])
        got = by_path.get(path, [])
        differ(Field(f"index {rel}"), shown(got[0]) if len(got) == 1 else JsonText(f"{len(got)} rows"), shown(want))
    for path in sorted(set(by_path) - set(expected)):
        differ(Field(f"index {path}"), shown(by_path[path][0]), shown(None))

    differ(Field("corpus"), shown(list(corpus_rows(out / "corpus.jsonl"))),
           shown([CorpusRow(i=RowId(k), n=c["n"], cmd=c["cmd"]) for k, c in enumerate(stage.want["corpus"])]))
    return diffs


def check(builder: Path, fixtures: Path, work: Path) -> list[Difference]:
    """Run every stage's build and gather the differences."""
    if work.exists() and any(work.iterdir()):
        raise Unusable(Prose(f"the work folder {work} is not empty"))
    found = stages(fixtures)
    projects, out, logs = work / "projects", work / "out", work / "logs"
    for folder in (projects, out, logs):
        folder.mkdir(parents=True, exist_ok=True)
    wants: dict[RelPath, TranscriptWant] = {}
    diffs: list[Difference] = []
    for k, stage in enumerate(found):
        mtime = BASE_NS + (k + 1) * STAGE_NS
        for rel in stage.files:
            target = projects / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage.projects / rel, target)
            os.utime(target, ns=(mtime, mtime))
            wants[rel] = stage.want["index"][rel]
        run_build(builder, projects, out, logs / f"{stage.name}.log")
        diffs.extend(compare(stage, projects, out, wants))
    return diffs


def main() -> ExitStatus:
    """Parse the arguments, check, report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--builder", type=Path, default=BUILDER, help="the builder script to hold")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES, help="the fixture set's folder")
    parser.add_argument("--work", type=Path, help="an empty or missing folder to build in, kept")
    parser.add_argument("--report", type=Path, help="where to write the differences as JSON")
    args = parser.parse_args()
    temporary = args.work is None
    work: Path = Path(tempfile.mkdtemp(prefix="corpus-build-check-")) if temporary else args.work
    try:
        diffs = check(args.builder, args.fixtures, work)
    except (Unusable, OSError, ValueError, KeyError) as error:
        print(f"corpus_build_check: {error}", file=sys.stderr)
        return 2
    finally:
        if temporary:
            shutil.rmtree(work, ignore_errors=True)
    if args.report is not None:
        args.report.write_text(json.dumps(diffs, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for d in diffs:
        print(f"{d['stage']} {d['field']}: got {d['got']}, want {d['want']}")
    print(f"corpus_build_check: {len(diffs)} differences")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
