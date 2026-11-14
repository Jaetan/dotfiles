#!/home/nicolas/.local/bin/python3.14
"""Rebuild the corpus of every distinct Bash command in every project's transcripts.

A Bash command is the input.command of a tool_use block named Bash in a transcript line's message.content. The
transcripts are every main transcript, <project>/<session>.jsonl, and every agent transcript, beneath
<project>/<session>/subagents/, under ~/.claude/projects. Each is read streaming, one line at a time, in up to 16
worker processes under a 4 GiB address-space cap each, and only when its size or modification time moved since
the last build: what each transcript held is kept in transcripts.jsonl, so a rebuild reads only what changed. A
command's count is the number of distinct tool_use ids carrying it, since a resumed session repeats earlier blocks
under their ids; one id can carry two texts, the call as written and as a hook rewrote it, and each counts it.

`corpus_build.py [--projects DIR] [--out DIR]` reads the transcripts beneath --projects (default ~/.claude/projects)
and writes beneath --out (default ~/.local/state/claude-guard/corpus/): corpus.jsonl, one {i, n, cmd} per distinct
command in the order first seen over the transcripts sorted by path; transcripts.jsonl; stats.json, with the
corpus's size and the build's run time. Exit 0 built; 2 a transcript could not be read or a worker hit its cap.
"""

import argparse
import collections
import json
import resource
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Literal, NewType, TypedDict

from corpus_common import CORPUS, CORPUS_DIR, PROJECTS, CorpusRow, Kib, RowId, Seconds, ShellText, UseCount, encoded

PathText = NewType("PathText", str)
ToolUseId = NewType("ToolUseId", str)
ByteCount = NewType("ByteCount", int)
Nanoseconds = NewType("Nanoseconds", int)
LineCount = NewType("LineCount", int)
TranscriptKind = Literal["main", "agent"]
ExitStatus = Literal[0, 2]

CAP = 4 << 30
WORKERS = 16
INDEX = "transcripts.jsonl"
STATS = "stats.json"
SEPARATORS = "\x00\x1d\x1e\x1f"


class Extract(TypedDict):
    """What one transcript held: its identity at reading and every Bash call in it, in order."""

    path: PathText
    kind: TranscriptKind
    size: ByteCount
    mtime_ns: Nanoseconds
    lines: LineCount
    bad_lines: LineCount
    calls: list[tuple[ToolUseId, ShellText]]


def transcripts(projects: Path) -> list[tuple[Path, TranscriptKind]]:
    """Every main and agent transcript beneath the projects folder, sorted by path."""
    main = [(p, "main") for p in projects.glob("*/*.jsonl")]
    agent = [(p, "agent") for p in projects.glob("*/*/subagents/**/*.jsonl")]
    found: list[tuple[Path, TranscriptKind]] = [*main, *agent]
    return sorted(found)


def read_transcript(path: Path, kind: TranscriptKind) -> Extract:
    """Every Bash tool_use block of one transcript, read a line at a time."""
    st = path.stat()
    calls: list[tuple[ToolUseId, ShellText]] = []
    lines = bad = 0
    with path.open("rb") as fh:
        for line in fh:
            lines += 1
            if b'"Bash"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                bad += 1
                continue
            message = record.get("message") if isinstance(record, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for k, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                    command = (block.get("input") or {}).get("command")
                    if isinstance(command, str):
                        ident = block.get("id") or f"noid-{path}-{lines}-{k}"
                        calls.append((ToolUseId(ident), ShellText(command)))
    return Extract(path=PathText(str(path)), kind=kind, size=ByteCount(st.st_size),
                   mtime_ns=Nanoseconds(st.st_mtime_ns), lines=LineCount(lines), bad_lines=LineCount(bad),
                   calls=calls)


def load_index(index: Path) -> dict[PathText, Extract]:
    """What the last build read, by transcript path; empty on a first build."""
    if not index.exists():
        return {}
    with index.open(encoding="utf-8", errors="surrogateescape") as rows:
        extracts: list[Extract] = [json.loads(row) for row in rows]
    return {e["path"]: e for e in extracts}


def write_atomically(path: Path, rows: list[ShellText]) -> None:
    """Write the lines to a sibling file, then rename it over the target."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", errors="surrogateescape") as w:
        for row in rows:
            w.write(row + "\n")
    tmp.replace(path)


def dumped(value: Extract | CorpusRow) -> ShellText:
    """One JSON line."""
    return ShellText(json.dumps(value, ensure_ascii=False))


def build(projects: Path, out: Path) -> ExitStatus:
    """Read what changed beneath the projects folder, write the corpus, the index and the stats beneath out."""
    start = time.perf_counter()
    resource.setrlimit(resource.RLIMIT_AS, (CAP, CAP))
    out.mkdir(parents=True, exist_ok=True)
    corpus, index, stats_file = out / CORPUS.name, out / INDEX, out / STATS
    old = load_index(index)
    found = transcripts(projects)
    current: dict[PathText, Extract] = {}
    todo: list[tuple[Path, TranscriptKind]] = []
    for path, kind in found:
        st = path.stat()
        before = old.get(PathText(str(path)))
        if before and before["size"] == st.st_size and before["mtime_ns"] == st.st_mtime_ns:
            current[before["path"]] = before
        else:
            todo.append((path, kind))
    try:
        with ProcessPoolExecutor(WORKERS) as pool:
            for extract in pool.map(read_transcript, [p for p, _ in todo], [k for _, k in todo], chunksize=2):
                current[extract["path"]] = extract
    except (OSError, MemoryError) as error:
        print(f"corpus_build: a transcript could not be read: {error!r}", file=sys.stderr)
        return 2
    carried: set[tuple[ToolUseId, ShellText]] = set()
    order: dict[ShellText, RowId] = {}
    ordered = [current[PathText(str(p))] for p, _ in found]
    for extract in ordered:
        for ident, command in extract["calls"]:
            carried.add((ToolUseId(ident), command))
            order.setdefault(command, RowId(len(order)))
    counts = collections.Counter(command for _, command in carried)
    write_atomically(corpus, [dumped(CorpusRow(i=i, n=UseCount(counts[c]), cmd=c)) for c, i in order.items()])
    write_atomically(index, [dumped(e) for e in ordered])
    lengths = sorted(len(encoded(c)) for c in order)
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    stats = {
        "transcripts": {kind: sum(1 for e in ordered if e["kind"] == kind) for kind in ("main", "agent")},
        "transcript_bytes": {kind: sum(e["size"] for e in ordered if e["kind"] == kind) for kind in ("main", "agent")},
        "read_this_build": {"transcripts": len(todo), "bytes": sum(current[PathText(str(p))]["size"] for p, _ in todo),
                            "lines": sum(current[PathText(str(p))]["lines"] for p, _ in todo)},
        "bad_lines": sum(e["bad_lines"] for e in ordered),
        "tool_use_blocks": sum(len(e["calls"]) for e in ordered),
        "distinct_ids": len({ident for ident, _ in carried}),
        "distinct_commands": len(order),
        "corpus_bytes": corpus.stat().st_size,
        "command_bytes": {"median": statistics.median(lengths) if lengths else 0,
                          "p99": lengths[int(len(lengths) * 0.99)] if lengths else 0,
                          "max": lengths[-1] if lengths else 0},
        "multiline": sum(1 for c in order if "\n" in c),
        "with_separator_chars": sum(1 for c in order if any(ch in c for ch in SEPARATORS)),
        "seconds": Seconds(round(time.perf_counter() - start, 2)),
        "builder_maxrss_kib": Kib(usage),
    }
    stats_file.write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(stats))
    return 0


def main() -> ExitStatus:
    """Parse the folders and build."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", type=Path, default=PROJECTS, help="the folder of project transcript folders")
    parser.add_argument("--out", type=Path, default=CORPUS_DIR, help="the folder the corpus, index and stats go to")
    args = parser.parse_args()
    return build(args.projects, args.out)


if __name__ == "__main__":
    sys.exit(main())
