#!/home/nicolas/.local/bin/python3.14
"""The corpus gate's arms: each misreading kind, a refusal, each refusal list row, a reference that runs its text
and each unusable input turn it red.

`corpus_gate_arms.py --reader PATH [--from DIR] [--work DIR]` reads a finished whole-corpus run of corpus_gate.py
with that reader (its report.json and reader.jsonl in --from, by default the gate's own work folder) and runs the
gate again over a sub-corpus: the rows each plant targets and every command the refusal list records.

  baseline   the real reader: exit 0, every target agreeing.
  probes     the parser research's hand-written calls, each with its verdict as bash reads it: a function
             definition followed by &&, || or | function body extent, the rest agreeing (zsh's `repeat 3 echo hi`
             is a plain command in bash). Exit 1 where a probe is a misreading, else 0.
  plants     a wrapper reader per kind runs the real one and rewrites the marked print of its targets, every 37th
             agreeing row where the plant applies, up to 25, as the parser research planted: two words merged, a
             word split, a redirection target changed, two commands joined, an operator swapped, a heredoc body
             line dropped, a word inside a substitution changed. Exit 1 naming exactly the targets as misreadings.
  hand       plants no corpus row takes, each on a probe call it targets alone: an array assignment merged with
             the next word, two arithmetic commands merged into one. Exit 1 naming exactly that call.
  refusal    a wrapper reader refuses its targets, every 37th agreeing row up to 25: exit 1 naming exactly the
             targets as refusals not recorded.
  rows       the list with one row removed, for every row, and with one row's message changed: exit 1 naming
             exactly that command as a refusal not recorded.
  reference  a bash that runs the text it is given instead of reprinting it: exit 2, the canary named.
  unusable   an empty corpus, a missing corpus, a bash that gives no version, a reader that exits 1: exit 2.
  restored   the baseline again: exit 0.

Writes beneath --work (default ~/.local/state/claude-guard/corpus/gate-arms/) the sub-corpus, the wrapper readers,
the bash that runs its text, one folder and one log per arm, and arms.json. Exit 0 every arm as expected; 1 an arm not; 2 the run it reads is
missing or holds no target for a plant.
"""

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NamedTuple, NewType, NotRequired, TypedDict

from corpus_common import CORPUS_DIR, PYTHON, CorpusRow, RowId, ShellText, TextKey, UseCount, corpus_rows, key_of

MarkedText = NewType("MarkedText", str)
ArmName = NewType("ArmName", str)
Prose = NewType("Prose", str)
Status = NewType("Status", int)
ExitStatus = Literal[0, 1, 2]
Plant = Callable[[MarkedText], MarkedText | None]
Where = Literal["top", "sub"]
Verdict = Literal["agree", "function body extent", "misreading"]

HERE = Path(__file__).resolve().parent
GATE = HERE / "corpus_gate.py"
REFUSALS = HERE / "corpus_refusals.toml"
WORK = CORPUS_DIR / "gate-arms"
STRIDE = 37
PER_PLANT = 25
REFUSAL_PLANT = ArmName("a refusal planted")
RUNNING_BASH = """#!/usr/bin/bash
# The arm's stand-in for bash: --version answers as bash does, anything else runs the text on stdin.
if [ "$1" = --version ]; then exec /usr/bin/bash --version; fi
exec /usr/bin/bash --norc --noprofile -s
"""
SHA = re.compile(r'sha256 = "([0-9a-f]{64})"')


def once(pattern: Prose, repl: Prose) -> Plant:
    def plant(text: MarkedText) -> MarkedText | None:
        new, n = re.subn(pattern, repl, text, count=1)
        return MarkedText(new) if n else None
    return plant


def split_word(text: MarkedText) -> MarkedText | None:
    m = re.search(r"\x01([^\x01\x02\s]{2,})\x02", text)
    if not m:
        return None
    w = m.group(1)
    return MarkedText(text[:m.start()] + "\x01" + w[:len(w) // 2] + "\x02 \x01" + w[len(w) // 2:] + "\x02"
                      + text[m.end():])


def drop_body_line(text: MarkedText) -> MarkedText | None:
    m = re.search(r"\x03([^\x04]*\n[^\x04]*\n)\x04", text)
    if not m:
        return None
    lines = m.group(1).split("\n")[:-1]
    return MarkedText(text[:m.start(1)] + "\n".join(lines[:-1]) + "\n" + text[m.end(1):])


PLANTS: dict[ArmName, tuple[Where, Plant]] = {
    ArmName("two words merged"): ("top", once(Prose("\x02 \x01"), Prose(" "))),
    ArmName("a word split"): ("top", split_word),
    ArmName("a redirection target changed"): ("top", once(Prose(r"(>\x01)([^\x02]*)\x02"), Prose("\\1\\2.planted\x02"))),
    ArmName("two commands joined"): ("top", once(Prose("\x02\n\x01"), Prose("\x02 \x01"))),
    ArmName("an operator swapped"): ("top", once(Prose(" && "), Prose(" || "))),
    ArmName("a heredoc body line dropped"): ("top", drop_body_line),
    ArmName("a word inside a substitution changed"): ("sub", once(Prose(r"\x01([^\x02]*)\x02"),
                                                                  Prose("\x01\\1planted\x02"))),
}


HAND_PLANTS: dict[ArmName, tuple[ShellText, Plant]] = {
    ArmName("an array assignment merged with the next word"): (
        ShellText("declare -a q=(1 2) r"), once(Prose(r"(=\([^\x02]*\))\x02 \x01"), Prose("\\1 "))),
    ArmName("two arithmetic commands merged"): (
        ShellText("((a)) && ((b))"), once(Prose(r"\)\)\x02 && \x01\(\("), Prose(")) && (("))),
}


PROBES: list[tuple[ShellText, Verdict]] = [(ShellText(c), v) for c, v in (
    ("repeat 3 echo hi", "agree"),
    ("v(){ a; } && b", "function body extent"),
    ("f() { a; } || b", "function body extent"),
    ("f() { a; } | b", "function body extent"),
    ("f() { a; }; b", "agree"),
    ("f() ( echo x )", "agree"),
    ("for f in a b; do echo $f; done", "agree"),
    ("if a; then b; elif c; then d; else e; fi", "agree"),
    ("case $x in a|b) echo 1;; *) echo 2;; esac", "agree"),
    ("case x in (a) ;; b) ;& esac", "agree"),
    ("python3 - <<'PY'\nimport sys\nprint(sys.argv)\nPY\necho after", "agree"),
    ("cat <<A <<B\na\nA\nb\nB", "agree"),
    ("cat <<'EOF'\nEOF\ntrue", "agree"),
    ("x=$(a|b; c) y=`d e` z=<(f)", "agree"),
    ('echo "$(cd /x && ls | wc -l)"', "agree"),
    ("(time ls) && (for i in 1; do :; done)", "agree"),
    ("arr=(1 2 3) b=(x); local -a c=(4 5)", "agree"),
    ("for ((i=0;i<3;i++)); do echo $i; done", "agree"),
    ("a 2>&1 >/dev/null |& b", "agree"),
    ("until a; b; do c; done", "agree"),
    ("( time ( a; echo x ) ) > f", "agree"),
    *((c, "agree") for c, _ in HAND_PLANTS.values()),
)]


class SubRow(TypedDict):
    print: MarkedText


class ReaderRow(TypedDict):
    i: RowId
    ok: bool
    err: NotRequired[Prose]
    print: MarkedText
    subs: list[SubRow]


class Keyed(TypedDict):
    key: TextKey


class GateReport(TypedDict):
    agree: list[TextKey]
    misreadings: list[Keyed]
    function_body_extent: list[Keyed]
    unrecorded_refusals: list[Keyed]


class Arm(NamedTuple):
    name: ArmName
    want: ExitStatus
    got: Status
    ok: bool
    note: Prose


def planted(row: ReaderRow, where: Where, plant: Plant) -> ReaderRow | None:
    """The row with the plant applied, or None where it does not apply."""
    if where == "top":
        new = plant(row["print"])
        return None if new is None or new == row["print"] else row | {"print": new}
    if not row["subs"]:
        return None
    new = plant(row["subs"][0]["print"])
    return None if new is None else row | {"subs": [row["subs"][0] | {"print": new}, *row["subs"][1:]]}


def planted_reader(kind: ArmName, real: Path, targets: Path) -> Status:
    """Run as the gate's reader: the real reader's rows, the target rows planted or refused."""
    chosen = set(json.loads(targets.read_text(encoding="utf-8"))[kind])
    with subprocess.Popen([real], stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            row: ReaderRow = json.loads(line)
            if row["i"] in chosen and kind == REFUSAL_PLANT:
                row = row | {"ok": False, "err": Prose("planted refusal"), "print": MarkedText(""), "subs": []}
            elif row["i"] in chosen:
                where, plant = PLANTS[kind] if kind in PLANTS else ("top", HAND_PLANTS[kind][1])
                row = planted(row, where, plant) or row
            sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
    return Status(proc.returncode)


def run_gate(name: ArmName, work: Path, reader: Path, corpus: Path, refusals: Path,
             bash: Path | None = None) -> tuple[Status, GateReport | None]:
    folder = work / "arms" / re.sub(r"\W+", "-", name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").unlink(missing_ok=True)
    argv = [str(PYTHON), str(GATE), "--reader", str(reader), "--corpus", str(corpus), "--refusals", str(refusals),
            "--work", str(folder)] + (["--bash", str(bash)] if bash else [])
    with (folder / "gate.log").open("w") as log:
        status = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
    report = folder / "report.json"
    found: GateReport | None = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return Status(status), found


def keys(rows: list[Keyed]) -> set[TextKey]:
    return {r["key"] for r in rows}


def arms(args: argparse.Namespace) -> ExitStatus:
    source: Path = args.source
    work: Path = args.work
    reader: Path = args.reader
    try:
        full = json.loads((source / "report.json").read_text(encoding="utf-8"))
        agree = set(full["agree"])
        rows = {r["i"]: r for r in corpus_rows(Path(full["corpus"]))}
        with (source / "reader.jsonl").open(encoding="utf-8", errors="surrogateescape") as lines:
            agreeing = [row for row in map(json.loads, lines) if row["ok"] and key_of(rows[row["i"]]["cmd"]) in agree]
    except (OSError, KeyError, ValueError) as error:
        print(f"corpus_gate_arms: the whole-corpus run in {source} cannot be read: {error!r}", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    targets: dict[ArmName, list[RowId]] = {}
    for name, (where, plant) in PLANTS.items():
        targets[name] = [r["i"] for r in agreeing[::STRIDE] if planted(r, where, plant) is not None][:PER_PLANT]
        if not targets[name]:
            print(f"corpus_gate_arms: no agreeing row takes the plant {name!r}", file=sys.stderr)
            return 2
    targets[REFUSAL_PLANT] = [r["i"] for r in agreeing[::STRIDE]][:PER_PLANT]
    text = REFUSALS.read_text(encoding="utf-8")
    listed = set(SHA.findall(text))
    keep = {i for ids in targets.values() for i in ids} | {i for i, r in rows.items() if key_of(r["cmd"]) in listed}
    sub = work / "sub-corpus.jsonl"
    with sub.open("w", encoding="utf-8", errors="surrogateescape") as w:
        for i in sorted(keep):
            w.write(json.dumps(CorpusRow(i=rows[i]["i"], n=rows[i]["n"], cmd=rows[i]["cmd"]), ensure_ascii=False)
                    + "\n")
    tfile = work / "targets.json"
    tfile.write_text(json.dumps(targets), encoding="utf-8")
    key = {i: key_of(rows[i]["cmd"]) for i in keep}
    every = {key[i] for ids in targets.values() for i in ids}
    results: list[Arm] = []

    def record(name: ArmName, want: ExitStatus, got: Status, ok: bool, note: Prose) -> None:
        results.append(Arm(name, want, got, ok and got == want, note))

    status, rep = run_gate(ArmName("baseline"), work, reader, sub, REFUSALS)
    record(ArmName("baseline"), 0, status, rep is not None and every <= set(rep["agree"]),
           Prose(f"{len(keep)} commands, {len(every)} targets agree" if rep else "no report"))
    probes = work / "probes.jsonl"
    with probes.open("w", encoding="utf-8") as w:
        for i, (c, _) in enumerate(PROBES):
            w.write(json.dumps(CorpusRow(i=RowId(i), n=UseCount(1), cmd=c)) + "\n")
    status, rep = run_gate(ArmName("probes"), work, reader, probes, REFUSALS)
    verdicts: dict[TextKey, Verdict] = {}
    if rep:
        verdicts = ({k: "agree" for k in rep["agree"]} | {k: "misreading" for k in keys(rep["misreadings"])}
                    | {k: "function body extent" for k in keys(rep["function_body_extent"])})
    wrong = [c for c, v in PROBES if verdicts.get(key_of(c)) != v]
    record(ArmName("probes"), 1 if any(v == "misreading" for _, v in PROBES) else 0, status,
           rep is not None and not wrong,
           Prose(f"{len(PROBES) - len(wrong)} of {len(PROBES)} as expected" + (f", wrong {wrong}" if wrong else "")))
    hand = work / "hand-targets.json"
    hand.write_text(json.dumps({k: [[c for c, _ in PROBES].index(call)] for k, (call, _) in HAND_PLANTS.items()}),
                    encoding="utf-8")
    for name, (call, _) in HAND_PLANTS.items():
        wrapper = work / f"reader-{re.sub(r'\W+', '-', name)}"
        wrapper.write_text(f"#!/bin/bash\nexec {PYTHON} {HERE / 'corpus_gate_arms.py'} planted-reader "
                           f"--kind '{name}' --real {reader} --targets {hand}\n", encoding="utf-8")
        wrapper.chmod(0o755)
        status, rep = run_gate(name, work, wrapper, probes, REFUSALS)
        got = keys(rep["misreadings"]) if rep else set()
        record(name, 1, status, got == {key_of(call)}, Prose(f"{call!r} named" if got == {key_of(call)} else
                                                             f"named {len(got)} calls"))
    for name in [*PLANTS, REFUSAL_PLANT]:
        wrapper = work / f"reader-{re.sub(r'\W+', '-', name)}"
        wrapper.write_text(f"#!/bin/bash\nexec {PYTHON} {HERE / 'corpus_gate_arms.py'} planted-reader "
                           f"--kind '{name}' --real {reader} --targets {tfile}\n", encoding="utf-8")
        wrapper.chmod(0o755)
        status, rep = run_gate(name, work, wrapper, sub, REFUSALS)
        want = {key[i] for i in targets[name]}
        named, other = ("unrecorded_refusals", "misreadings") if name == REFUSAL_PLANT else (
            "misreadings", "unrecorded_refusals")
        got = keys(rep[named]) if rep else set()
        record(name, 1, status, rep is not None and got == want and not rep[other],
               Prose(f"{len(want)} planted, {len(got & want)} named, {len(got - want)} others"))
    blocks = text.split("\n[[refusal]]\n")
    without = work / "refusals-without.toml"
    for k in range(1, len(blocks)):
        sha = SHA.findall(blocks[k])[0]
        without.write_text("\n[[refusal]]\n".join(blocks[:k] + blocks[k + 1:]), encoding="utf-8")
        name = ArmName(f"row {k} removed")
        status, rep = run_gate(name, work, reader, sub, without)
        got = keys(rep["unrecorded_refusals"]) if rep else set()
        record(name, 1, status, rep is not None and got == {sha} and not rep["misreadings"], Prose(sha[:12]))
    changed = work / "refusals-changed.toml"
    changed.write_text(text.replace('reader_error = "', 'reader_error = "changed ', 1), encoding="utf-8")
    first = SHA.findall(text)[0]
    status, rep = run_gate(ArmName("row 1 message changed"), work, reader, sub, changed)
    got = keys(rep["unrecorded_refusals"]) if rep else set()
    record(ArmName("row 1 message changed"), 1, status, got == {first}, Prose(first[:12]))
    empty = work / "empty-corpus.jsonl"
    empty.write_text("", encoding="utf-8")
    running = work / "bash-that-runs-its-text"
    running.write_text(RUNNING_BASH, encoding="utf-8")
    running.chmod(0o755)
    for name, corpus, bash, rdr, said in (
            (ArmName("empty corpus"), empty, None, reader, "is empty"),
            (ArmName("missing corpus"), work / "no-such-corpus.jsonl", None, reader, "No such file"),
            (ArmName("bash gives no version"), sub, Path("/bin/false"), reader, "no bash version"),
            (ArmName("a reference that runs its text"), sub, running, reader, "does not reprint without running"),
            (ArmName("reader exits 1"), sub, None, Path("/bin/false"), "exited 1")):
        status, rep = run_gate(name, work, rdr, corpus, REFUSALS, bash)
        log = (work / "arms" / re.sub(r"\W+", "-", name) / "gate.log").read_text(encoding="utf-8")
        record(name, 2, status, rep is None and said in log,
               Prose(("no report written" if rep is None else "a report was written")
                     + (f", the log names {said!r}" if said in log else f", the log lacks {said!r}")))
    status, rep = run_gate(ArmName("restored"), work, reader, sub, REFUSALS)
    record(ArmName("restored"), 0, status, rep is not None and every <= set(rep["agree"]), Prose(""))
    (work / "arms.json").write_text(json.dumps([a._asdict() for a in results], indent=1) + "\n", encoding="utf-8")
    for a in results:
        print(f"{'ok ' if a.ok else 'BAD'} {a.name}: want {a.want}, got {a.got}; {a.note}")
    bad = [a for a in results if not a.ok]
    print(f"{len(results) - len(bad)} of {len(results)} arms as expected; {work / 'arms.json'}")
    return 1 if bad else 0


def main() -> Status:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    modes = parser.add_subparsers(dest="mode")
    pr = modes.add_parser("planted-reader")
    pr.add_argument("--kind", required=True)
    pr.add_argument("--real", type=Path, required=True)
    pr.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--reader", type=Path)
    parser.add_argument("--from", dest="source", type=Path, default=CORPUS_DIR / "gate-run")
    parser.add_argument("--work", type=Path, default=WORK)
    args = parser.parse_args()
    if args.mode == "planted-reader":
        return planted_reader(ArmName(args.kind), args.real, args.targets)
    if args.reader is None:
        parser.error("--reader is required")
    return Status(arms(args))


if __name__ == "__main__":
    sys.exit(main())
