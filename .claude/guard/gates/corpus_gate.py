#!/home/nicolas/.local/bin/python3.14
"""The shell reader's gate: every corpus command bash accepts is read by the reader as bash reads it.

Run whenever the reader's mvdan.cc/sh pin changes: `corpus_gate.py --reader PATH`, after corpus_build.py has built
the corpus. Every call runs in bash, so bash is the reference (bash_reference.py): a command is judged when bash's
--pretty-print accepts it. The reader runs inside the sandbox over the whole corpus, and each judged command is cut
into units, the call's top level and each command or process substitution's interior. Per unit, bash reprints the
text it reads for the unit and the reader's print of the unit with its marks removed, each as a function body so
the layout of either does not count, and the two reprints must be equal; and each word the reader marks must be one
bash word.

THE READER'S PROTOCOL, the research prototype's: run with no arguments, it reads JSONL {i, cmd} on stdin and writes
one JSONL row per input, in order, of which the gate reads {i, ok, err, print, subs}. `print` is the reader's print
of its tree as text bash reads as the source means, every word one marked literal, \\x01 its verbatim source \\x02,
and every heredoc body marked whole, \\x03 body \\x04; each sub {kind, inner, print} is a substitution's source
interior and its statements printed alike. A row with ok false is a refusal, err the reader's message.

WORDS. Each marked word must be what bash reads as exactly one word (bash_reference.py's word answer), so a merged or
split word is red even where its reprint reads the same. A whole ((...)) arithmetic command is checked as the one
word $((...)); an array assignment name=(...), whose elements the reader rebuilds one space apart as bash's reprint
does, must end at its own closing parenthesis with the elements a list of whole words (the list answer). Left to the
reprints: the clauses of a C-style for header and the regex after =~.

VERDICTS. A command agrees when every unit's reprints are equal and every word is one word. Where the reprints
differ, the reader's print with a function definition's outer brace pair folded (mvdan reads `f() { a; } && b` as a
function whose body runs on to b) is reprinted by bash too; equal to the command's reprint, the difference changes
no command, word or redirection target and is reported as function body extent. Any other difference is a
misreading: a word bash reads as none or several, unequal reprints, a substitution interior bash will not read alone
or as a function body, a reader's print bash refuses. A refusal is honest only when corpus_refusals.toml records the
command's key with the reader's same message and a reason.

Writes beneath --work (default ~/.local/state/claude-guard/corpus/gate-run/): reader.jsonl and report.json.
Exit 0: no misreading and no unrecorded refusal. 1: a misreading, or a refusal the list does not record. 2: the
corpus is empty or unreadable, the reference cannot be built, the reader fails, or the list is malformed.
"""

import argparse
import collections
import json
import re
import resource
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, NamedTuple, NewType, NotRequired, TypedDict, cast

from bash_reference import BASH, Reference, ReferenceFault
from corpus_common import (CORPUS, CORPUS_DIR, Arg, Kib, RowId, Seconds, ShellText, TextKey, corpus_rows, key_of,
                           sandboxed)

MarkedText = NewType("MarkedText", str)
ReaderMessage = NewType("ReaderMessage", str)
UnitId = NewType("UnitId", str)
Offset = NewType("Offset", int)
Prose = NewType("Prose", str)
ExitStatus = Literal[0, 1, 2]
Verdict = Literal["agree", "function body extent", "misreading"]
Tally = Literal["agree", "function body extent", "misreading", "refused, recorded", "refused, not recorded"]

HERE = Path(__file__).resolve().parent
REFUSALS = HERE / "corpus_refusals.toml"
WORK = CORPUS_DIR / "gate-run"
READER_CAP = Kib(8 << 20)
READER_WALL = Seconds(1800)
SHOWN = 10


class SubRow(TypedDict):
    kind: Literal["cmdsubst", "backquote", "procsubst"]
    inner: ShellText
    print: MarkedText


class ReaderRow(TypedDict):
    i: RowId
    ok: bool
    err: NotRequired[ReaderMessage]
    print: MarkedText
    subs: list[SubRow]


class RefusalEntry(TypedDict):
    sha256: TextKey
    reader_error: ReaderMessage
    reason: Prose


class RefusalFile(TypedDict):
    refusal: list[RefusalEntry]


class GateFault(Exception):
    """The gate cannot judge: the corpus, the reader or the refusal list is unusable."""


class Fault(NamedTuple):
    """Why one unit is a misreading, with bash's reprint and the reader's around the first line that differs."""

    unit: UnitId
    why: Prose
    bash: list[ShellText]
    reader: list[ShellText]


class Unit(NamedTuple):
    """One unit of a call: the text bash reads for it, the reader's print of it as bash text, the same with a
    function definition's outer braces folded where that applies, and the texts bash must read as one word each and
    as a list of whole words."""

    uid: UnitId
    source: ShellText
    printed: ShellText
    folded: ShellText | None
    words: list[ShellText]
    lists: list[ShellText]


class Misreading(TypedDict):
    key: TextKey
    i: RowId
    head: Prose
    faults: list[Fault]
    nfaults: Offset


class Refused(TypedDict):
    key: TextKey
    i: RowId
    err: ReaderMessage
    recorded_err: ReaderMessage | None
    head: Prose


class Extent(TypedDict):
    key: TextKey
    i: RowId
    head: Prose


# ---- the reader's print as bash text ----

SEG = re.compile(r"(\x01[\s\S]*?\x02|\x03[\s\S]*?\x04)")
ARRAY_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=\(")
ARRAY_WHOLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=\(([\s\S]*)\)")


def unbackquote(text: ShellText) -> ShellText:
    return ShellText(re.sub(r"\\([\\`$])", r"\1", text))


def sub_text(sub: SubRow) -> ShellText:
    """A substitution's interior as bash reads it alone: backquote escapes undone."""
    return unbackquote(sub["inner"]) if sub["kind"] == "backquote" else sub["inner"]


def word_checks(inner: ShellText) -> tuple[list[ShellText], list[ShellText]]:
    """What bash must read a marked word as: one word, or a list of whole words for an array's elements. An
    arithmetic command is the word $((...)); an array assignment that does not end at its closing parenthesis is
    checked as one word, which bash refuses."""
    if inner.startswith("((") and inner.endswith("))"):
        return [ShellText("$" + inner)], []
    if ARRAY_WORD.match(inner):
        whole = ARRAY_WHOLE.fullmatch(inner)
        return ([], [ShellText(whole.group(2))]) if whole else ([inner], [])
    return [inner], []


def bash_text(printed: MarkedText) -> tuple[ShellText, list[ShellText], list[ShellText]]:
    """The reader's print with its marks removed, the texts bash must read as one word each, and the texts it must
    read as a list of whole words."""
    parts = SEG.split(printed)
    words: list[ShellText] = []
    lists: list[ShellText] = []
    cfor = False
    for k, part in enumerate(parts):
        if k % 2 == 0:
            opened, closed = part.rfind("for (("), part.rfind("))")
            if opened != closed:
                cfor = opened > closed
            continue
        if part[0] == "\x01" and not (cfor or parts[k - 1].endswith("=~ ")):
            one, many = word_checks(ShellText(part[1:-1]))
            words += one
            lists += many
    return ShellText(SEG.sub(lambda m: m.group(0)[1:-1], printed)), words, lists


def folded_print(printed: MarkedText) -> MarkedText | None:
    """The reader's print with each function definition whose body opens with a brace group, `() { {`, stripped of
    its outer pair of braces, so `f() { { a; } && b; }` reads as `f() { a; } && b`: the same commands, words and
    redirections. Braces count only outside the marks, where every one is a group's; None where nothing folds."""
    parts = SEG.split(printed)
    folded = False
    for _ in range(printed.count("() { {")):
        at = next(((k, parts[k].find("() { {")) for k in range(0, len(parts), 2) if "() { {" in parts[k]), None)
        if at is None:
            break
        k0, i0 = at[0], at[1] + 3
        depth = 0
        for k in range(k0, len(parts), 2):
            close = next((i for i in range(i0 if k == k0 else 0, len(parts[k]))
                          if (depth := depth + {"{": 1, "}": -1}.get(parts[k][i], 0)) == 0), None)
            if close is not None:
                parts[k] = parts[k][:close] + parts[k][close + 1:]
                parts[k0] = parts[k0][:i0] + parts[k0][i0 + 1:]
                folded = True
                break
        else:
            break
    return MarkedText("".join(parts)) if folded else None


def around(a: ShellText, b: ShellText) -> tuple[list[ShellText], list[ShellText]]:
    """The two reprints' lines around their first differing line."""
    la, lb = a.split("\n"), b.split("\n")
    k = 0
    while k < min(len(la), len(lb)) and la[k] == lb[k]:
        k += 1
    return [ShellText(x) for x in la[max(0, k - 2):k + 3]], [ShellText(x) for x in lb[max(0, k - 2):k + 3]]


def judge(units: list[Unit], ref: Reference) -> tuple[Verdict, list[Fault]]:
    """A call's verdict: every unit's two reprints equal and every marked word one bash word."""
    faults: list[Fault] = []
    folded = False
    for u in units:
        alone = ref.accepted[key_of(u.source)]
        if not alone.ok:
            faults.append(Fault(u.uid, Prose(f"bash refuses the interior alone: {alone.err}"), [], []))
            continue
        bash = ref.reprinted[key_of(u.source)]
        if not bash.ok:
            faults.append(Fault(u.uid, Prose(f"bash refuses the unit as a function body: {bash.err}"), [], []))
            continue
        mine = ref.reprinted[key_of(u.printed)]
        if not mine.ok:
            faults.append(Fault(u.uid, Prose(f"bash refuses the reader's print: {mine.err}"), [], [u.printed]))
            continue
        split = [w for w in u.words if not ref.worded[key_of(w)].one]
        if split:
            faults.append(Fault(u.uid, Prose(f"{len(split)} words bash reads as none or several: "
                                             f"{ref.worded[key_of(split[0])].err}"), [], split[:3]))
        broken = [w for w in u.lists if not ref.listed[key_of(w)].one]
        if broken:
            faults.append(Fault(u.uid, Prose(f"{len(broken)} array element lists bash does not read as whole words: "
                                             f"{ref.listed[key_of(broken[0])].err}"), [], broken[:3]))
        if bash.text == mine.text:
            continue
        fold = ref.reprinted[key_of(u.folded)] if u.folded is not None else None
        if fold is not None and fold.ok and fold.text == bash.text:
            folded = True
            continue
        faults.append(Fault(u.uid, Prose("reprints"), *around(bash.text, mine.text)))
    if faults:
        return "misreading", faults
    return ("function body extent" if folded else "agree"), []


# ---- the run ----

def units_of(row: ReaderRow, command: ShellText) -> list[Unit]:
    """Each unit the reader gives: the top level, then each substitution."""
    sources = [(UnitId("top"), command, row["print"])]
    sources += [(UnitId(f"sub{k}"), sub_text(s), s["print"]) for k, s in enumerate(row["subs"])]
    units: list[Unit] = []
    for uid, source, printed in sources:
        text, words, lists = bash_text(printed)
        fold = folded_print(printed)
        units.append(Unit(uid, source, text, bash_text(fold)[0] if fold is not None else None, words, lists))
    return units


def run_reader(reader: Path, corpus: Path, out: Path) -> None:
    """The reader over the whole corpus, in the sandbox; its rows to `out`."""
    with corpus.open("rb") as fin, out.open("wb") as fout:
        try:
            run = subprocess.run(sandboxed([Arg(str(reader))], READER_CAP, READER_WALL), stdin=fin, stdout=fout,
                                 stderr=subprocess.PIPE, check=False)
        except OSError as error:
            raise GateFault(f"the reader {reader} could not start: {error}") from error
    if run.returncode != 0:
        raise GateFault(f"the reader {reader} exited {run.returncode}: {run.stderr.decode('utf-8', 'replace')[-400:]}")


def reader_rows(path: Path, ids: list[RowId]) -> Iterator[ReaderRow]:
    """The reader's rows, each checked against the corpus row it answers."""
    n = 0
    with path.open(encoding="utf-8", errors="surrogateescape") as lines:
        for n, line in enumerate(lines, 1):
            try:
                row: ReaderRow = json.loads(line)
            except ValueError as error:
                raise GateFault(f"reader row {n} is not JSON: {error}") from error
            if n > len(ids) or row.get("i") != ids[n - 1]:
                raise GateFault(f"reader row {n} answers {row.get('i')!r}, the corpus row is "
                                f"{ids[n - 1] if n <= len(ids) else 'none'}")
            yield row
    if n != len(ids):
        raise GateFault(f"the reader wrote {n} rows for {len(ids)} corpus rows")


def refusal_list(path: Path) -> dict[TextKey, RefusalEntry]:
    try:
        data = cast(RefusalFile, tomllib.loads(path.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise GateFault(f"the refusal list {path}: {error}") from error
    entries: dict[TextKey, RefusalEntry] = {}
    for e in data.get("refusal", []):
        if (set(e) != {"sha256", "reader_error", "reason"} or not re.fullmatch(r"[0-9a-f]{64}", e["sha256"])
                or not e["reason"].strip() or e["sha256"] in entries):
            raise GateFault(f"the refusal list {path} has a malformed or repeated row: {e!r}")
        entries[e["sha256"]] = e
    return entries


def head(text: ShellText) -> Prose:
    return Prose(repr(text[:110]) + ("..." if len(text) > 110 else ""))


def gate(args: argparse.Namespace) -> ExitStatus:
    timings: dict[Prose, Seconds] = {}
    clock = time.perf_counter()

    def lap(name: Prose) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[name] = Seconds(round(now - clock, 2))
        clock = now

    corpus: Path = args.corpus
    work: Path = args.work
    try:
        rows = list(corpus_rows(corpus))
    except (OSError, ValueError) as error:
        raise GateFault(f"the corpus {corpus}: {error}") from error
    if not rows:
        raise GateFault(f"the corpus {corpus} is empty: nothing to judge")
    listed = refusal_list(args.refusals)
    ref = Reference(args.bash)
    commands = {r["i"]: r["cmd"] for r in rows}
    ref.ensure_accept(commands.values())
    accepted = {i for i, c in commands.items() if ref.accepted[key_of(c)].ok}
    lap(Prose("bash accept"))
    work.mkdir(parents=True, exist_ok=True)
    out = work / "reader.jsonl"
    run_reader(args.reader, corpus, out)
    lap(Prose("reader"))
    ids = [r["i"] for r in rows]
    sources: list[ShellText] = []
    texts: list[ShellText] = []
    words: list[ShellText] = []
    lists: list[ShellText] = []
    for row in reader_rows(out, ids):
        if row["i"] in accepted and row["ok"]:
            for u in units_of(row, commands[row["i"]]):
                sources.append(u.source)
                texts += [t for t in (u.source, u.printed, u.folded) if t is not None]
                words += u.words
                lists += u.lists
    ref.ensure_accept(sources)
    ref.ensure_reprint(texts)
    lap(Prose("bash reprint of the units and prints"))
    ref.ensure_word("word", words)
    ref.ensure_word("list", lists)
    lap(Prose("bash words"))
    counts: collections.Counter[Tally] = collections.Counter()
    misread: list[Misreading] = []
    unrecorded: list[Refused] = []
    extent: list[Extent] = []
    agree: list[TextKey] = []
    now_read: list[TextKey] = []
    seen: set[TextKey] = set()
    for row in reader_rows(out, ids):
        if row["i"] not in accepted:
            continue
        command = commands[row["i"]]
        key = key_of(command)
        seen.add(key)
        if not row["ok"]:
            err = row.get("err", ReaderMessage(""))
            entry = listed.get(key)
            if entry is not None and entry["reader_error"] == err:
                counts["refused, recorded"] += 1
                continue
            counts["refused, not recorded"] += 1
            unrecorded.append(Refused(key=key, i=row["i"], err=err, head=head(command),
                                      recorded_err=entry["reader_error"] if entry else None))
            continue
        if key in listed:
            now_read.append(key)
        verdict, faults = judge(units_of(row, command), ref)
        counts[verdict] += 1
        if verdict == "agree":
            agree.append(key)
        elif verdict == "function body extent":
            extent.append(Extent(key=key, i=row["i"], head=head(command)))
        else:
            misread.append(Misreading(key=key, i=row["i"], head=head(command), faults=faults[:3],
                                      nfaults=Offset(len(faults))))
    lap(Prose("judge"))
    absent = sorted(k for k in listed if k not in seen)
    status: ExitStatus = 1 if misread or unrecorded else 0
    report = {
        "status": status, "corpus": str(corpus), "rows": len(rows), "bash_accepted": len(accepted),
        "reader": str(args.reader), "bash": ref.tag, "refusal_list": str(args.refusals), "counts": counts,
        "bash_ran": ref.ran, "seconds": timings,
        "misreadings": [m | {"faults": [f._asdict() for f in m["faults"]]} for m in misread],
        "unrecorded_refusals": unrecorded, "function_body_extent": extent,
        "stale_list_rows": {"now_read": now_read, "absent": absent}, "agree": agree,
    }
    (work / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"corpus {corpus}: {len(rows)} commands, {len(accepted)} bash accepts; reader {args.reader}; {ref.tag}")
    print("verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("seconds: " + ", ".join(f"{k} {v}" for k, v in timings.items()) + f", total {sum(timings.values()):.2f}")
    for title, items in (("misreadings", report["misreadings"]), ("refusals not recorded", unrecorded),
                         ("function body extent (not failing)", extent)):
        if items:
            print(f"{title}: {len(items)}")
            for item in items[:SHOWN]:
                print("  " + json.dumps(item, ensure_ascii=False)[:600])
    if now_read or absent:
        print(f"stale refusal list rows (not failing): {len(now_read)} now read, {len(absent)} not bash-accepted "
              "in this corpus")
    print(f"report {work / 'report.json'}; exit {status}")
    return status


def main() -> ExitStatus:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--reader", type=Path, required=True, help="the reader binary to judge")
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--refusals", type=Path, default=REFUSALS)
    parser.add_argument("--bash", type=Path, default=BASH)
    parser.add_argument("--work", type=Path, default=WORK)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (12 << 30, 12 << 30))
    try:
        return gate(args)
    except (GateFault, ReferenceFault, OSError) as error:
        print(f"corpus_gate: cannot judge: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
