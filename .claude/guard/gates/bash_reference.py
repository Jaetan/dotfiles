#!/home/nicolas/.local/bin/python3.14
"""bash's own reading of shell text: the reference the corpus gate judges a reader against.

Four answers per text, each from bash under the tool line's options (--norc --noprofile +O extglob, no alias),
one bash per text or per batch, every bash inside the sandbox corpus_common.sandboxed builds. Each runs
`bash --pretty-print` with a text on stdin: bash parses each command and prints make_command_string's reprint of it,
executing nothing.

  accept   the text alone: accepted when bash exits 0.
  reprint  the text as the body of `__bref () { :; TEXT }`: inside a function body bash prints a `;`, a newline
           and a blank line between commands alike, so the reprint does not depend on the text's layout, and the
           leading `:` keeps a text of comments alone a body. The wrapper is only reprinted: nothing defines or
           calls __bref.
  word     whether the text is exactly one bash word: `case TEXT in esac`, which bash accepts with one word and
           refuses with none or several. Words go a batch at a time, each line followed by a sentinel command
           written with two spaces, whose reprint (one space) stands alone on a line only when bash read it as a
           command: a batch passes when bash exits 0 and prints one sentinel per word, a failed batch is halved.
  list     whether the text is a list of whole bash words, none or several, with no operator between them:
           `for _ in TEXT; do :; done`, batched as words are.

Before any text reaches it, the bash named must reprint a canary alone and wrapped, whose run would print
something else or nothing, or the reference cannot be built. Each answer is cached by the text's key beneath
~/.local/state/claude-guard/corpus/bash-reference/<tag>/, the tag naming bash's version, its binary's digest and
this file's digest, so a bash upgrade or an edit here starts a fresh cache. Only texts the cache lacks reach bash,
split over up to 16 sandboxes at once.

As a program, `bash_reference.py [--corpus PATH] [--bash PATH]` builds the accept answer of every corpus command
and the reprint of every accepted one, and prints what it asked, what it ran and the run time. Exit 0 built; 2 the reference could not be built.
`bash_reference.py --serve accept|reprint|word|list --bash PATH` is the sandbox's side: NUL-terminated texts on stdin, one JSON
row out per text, in order.
"""

import argparse
import fcntl
import hashlib
import json
import math
import re
import resource
import subprocess
import sys
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

from corpus_common import (CORPUS, CORPUS_DIR, PYTHON, Arg, Kib, Seconds, ShellText, TextBytes, TextKey, corpus_rows,
                           encoded, key_of, sandboxed)

BashMessage = NewType("BashMessage", str)
Tag = NewType("Tag", str)
Count = NewType("Count", int)
Answer = Literal["accept", "reprint", "word", "list"]
ExitStatus = Literal[0, 2]

BASH = Path("/usr/bin/bash")
OPTIONS = [Arg("--norc"), Arg("--noprofile"), Arg("--pretty-print"), Arg("+O"), Arg("extglob")]
REFERENCE_DIR = CORPUS_DIR / "bash-reference"
SHARDS = 16
SHARD_FLOOR = 256
BATCH = 512
CAP = Kib(2 << 20)
WALL = Seconds(900)
TEXT_WALL = Seconds(120)
SENTINEL_SOURCE = TextBytes(b":  bash-reference-sentinel\n")
SENTINEL_LINE = ShellText(": bash-reference-sentinel")
CANARY = ShellText("echo  ran-$((6*7))")
CANARY_ALONE = ShellText("echo ran-$((6*7))\n\n")
CANARY_WRAPPED = ShellText("__bref () \n{ \n    :;\n    echo ran-$((6*7))\n}\n\n")


class ReferenceFault(Exception):
    """bash's reference could not be built: bash absent or failing, a canary run, a sandbox fault, a count off."""


class Accept(NamedTuple):
    """Whether bash accepts a text alone, and the first line it wrote to stderr."""

    ok: bool
    err: BashMessage


class Reprint(NamedTuple):
    """Whether bash accepts a text wrapped as a function body, its reprint, and the first line of its stderr."""

    ok: bool
    text: ShellText
    err: BashMessage


class Word(NamedTuple):
    """Whether bash reads a text as exactly one word, and the first line it wrote to stderr when not."""

    one: bool
    err: BashMessage


class Served(TypedDict):
    """One row the sandbox's side writes: `ok` and `text` for the text alone or wrapped, `one` for a word, `err` for
    all three."""

    ok: bool
    text: ShellText
    one: bool
    err: BashMessage


class AcceptRow(TypedDict):
    k: TextKey
    ok: bool
    err: BashMessage


class ReprintRow(TypedDict):
    k: TextKey
    ok: bool
    text: ShellText
    err: BashMessage


class WordRow(TypedDict):
    k: TextKey
    one: bool
    err: BashMessage


def digest(paths: Iterable[Path]) -> Tag:
    """The first 12 hex digits of the sha256 over the files' bytes."""
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes())
    return Tag(h.hexdigest()[:12])


def tag_of(bash: Path) -> Tag:
    """The cache folder's name: bash's version, its binary's digest, this file's digest."""
    try:
        run = subprocess.run([bash, "--version"], capture_output=True, timeout=30, check=False)
    except OSError as error:
        raise ReferenceFault(f"{bash} --version: {error}") from error
    line = run.stdout.decode("utf-8", "replace").split("\n", 1)[0]
    m = re.match(r"GNU bash, version (\S+)", line)
    if run.returncode != 0 or not m:
        raise ReferenceFault(f"{bash} --version gave status {run.returncode} and {line[:80]!r}, no bash version")
    return Tag(f"bash-{m.group(1)}-{digest([bash.resolve()])}-{digest([Path(__file__).resolve()])}")


# ---- the sandbox's side ----

def reprint_of(bash: Path, text: TextBytes) -> tuple[bool, ShellText, BashMessage]:
    """bash's verdict on a text read as a script, its reprint, and the first line of its stderr."""
    run = subprocess.run([bash, *OPTIONS], input=text, capture_output=True, timeout=TEXT_WALL, check=False)
    err = BashMessage(run.stderr.decode("utf-8", "surrogateescape").split("\n", 1)[0])
    return run.returncode == 0, ShellText(run.stdout.decode("utf-8", "surrogateescape")), err


CONTEXT: dict[Answer, tuple[TextBytes, TextBytes]] = {"word": (TextBytes(b"case "), TextBytes(b" in esac\n")),
                                                     "list": (TextBytes(b"for _ in "), TextBytes(b"; do :; done\n"))}


def words_of(bash: Path, answer: Answer, words: list[TextBytes]) -> list[Served]:
    """Each text's verdict in the answer's context: a batch that passes vouches for all its texts, a failed one is
    halved down to one."""
    before, after = CONTEXT[answer]
    ok, out, err = reprint_of(bash, TextBytes(b"".join(before + w + after + SENTINEL_SOURCE for w in words)))
    if ok and out.split("\n").count(SENTINEL_LINE) == len(words):
        return [Served(ok=True, text=ShellText(""), one=True, err=BashMessage("")) for _ in words]
    if len(words) == 1:
        return [Served(ok=True, text=ShellText(""), one=False, err=err)]
    half = len(words) // 2
    return words_of(bash, answer, words[:half]) + words_of(bash, answer, words[half:])


def wrapped(text: TextBytes) -> TextBytes:
    return TextBytes(b"__bref () \n{ \n:\n" + text + b"\n}\n")


def serve(answer: Answer, bash: Path) -> ExitStatus:
    texts = [TextBytes(t) for t in sys.stdin.buffer.read().split(b"\0")]
    if texts[-1] != b"":
        print("bash_reference --serve: the last text is not NUL-terminated", file=sys.stderr)
        return 2
    texts = texts[:-1]
    rows: list[Served] = []
    if answer in ("accept", "reprint"):
        for t in texts:
            ok, out, err = reprint_of(bash, t if answer == "accept" else wrapped(t))
            rows.append(Served(ok=ok, text=out, one=False, err=err))
    else:
        for a in range(0, len(texts), BATCH):
            rows += words_of(bash, answer, texts[a:a + BATCH])
    sys.stdout.buffer.write(b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8", "surrogateescape")
                                     for r in rows))
    return 0


# ---- the gate's side ----

class Reference:
    """bash's answers, cached on disk and in memory, computed for the texts the cache lacks."""

    def __init__(self, bash: Path) -> None:
        self.bash = bash
        self.tag = tag_of(bash)
        self.folder = REFERENCE_DIR / self.tag
        self.folder.mkdir(parents=True, exist_ok=True)
        self.ran: dict[Answer, Count] = {"accept": Count(0), "reprint": Count(0), "word": Count(0), "list": Count(0)}
        for answer, want in (("accept", CANARY_ALONE), ("reprint", CANARY_WRAPPED)):
            canary = self._records(answer, [CANARY])[0]
            if (canary["ok"], canary["text"], canary["err"]) != (True, want, ""):
                raise ReferenceFault(f"{bash} does not reprint without running: the canary {CANARY!r} ({answer}) "
                                     f"gave ok {canary['ok']}, {canary['text'][:80]!r}, {canary['err'][:80]!r}")
        self.accepted: dict[TextKey, Accept] = {
            r["k"]: Accept(r["ok"], r["err"]) for r in self._rows_of("accept", AcceptRow)}
        self.reprinted: dict[TextKey, Reprint] = {
            r["k"]: Reprint(r["ok"], r["text"], r["err"]) for r in self._rows_of("reprint", ReprintRow)}
        self.worded: dict[TextKey, Word] = {r["k"]: Word(r["one"], r["err"]) for r in self._rows_of("word", WordRow)}
        self.listed: dict[TextKey, Word] = {r["k"]: Word(r["one"], r["err"]) for r in self._rows_of("list", WordRow)}

    def _file(self, answer: Answer) -> Path:
        return self.folder / f"{answer}.jsonl"

    def _rows_of[R: (AcceptRow, ReprintRow, WordRow)](self, answer: Answer, _: type[R]) -> list[R]:
        path = self._file(answer)
        if not path.exists():
            return []
        with path.open(encoding="utf-8", errors="surrogateescape") as rows:
            return [json.loads(row) for row in rows]

    def _append(self, answer: Answer, rows: list[AcceptRow] | list[ReprintRow] | list[WordRow]) -> None:
        with self._file(answer).open("a", encoding="utf-8", errors="surrogateescape") as w:
            fcntl.flock(w, fcntl.LOCK_EX)
            w.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)

    def _missing(self, answer: Answer, texts: Iterable[ShellText], have: Iterable[TextKey]) -> list[ShellText]:
        known = set(have)
        todo: dict[TextKey, ShellText] = {}
        for t in texts:
            k = key_of(t)
            if k not in known and k not in todo:
                todo[k] = t
        bad = [k for k, t in todo.items() if "\0" in t]
        if bad:
            raise ReferenceFault(f"{len(bad)} texts hold a NUL, which no bash text carries, first {bad[0]}")
        self.ran[answer] = Count(self.ran[answer] + len(todo))
        return list(todo.values())

    def _records(self, answer: Answer, texts: list[ShellText]) -> list[Served]:
        """Serve the answer over the texts in up to SHARDS sandboxes; one row per text, in order."""
        if not texts:
            return []
        size = max(SHARD_FLOOR, math.ceil(len(texts) / SHARDS))
        shards = [texts[a:a + size] for a in range(0, len(texts), size)]
        argv = sandboxed([Arg(str(PYTHON)), Arg(str(Path(__file__).resolve())), Arg("--serve"), Arg(answer),
                          Arg("--bash"), Arg(str(self.bash))], CAP, WALL)

        def one(shard: list[ShellText]) -> list[Served]:
            feed = b"".join(encoded(t) + b"\0" for t in shard)
            run = subprocess.run(argv, input=feed, capture_output=True, check=False)
            if run.returncode != 0:
                tail = run.stderr.decode("utf-8", "replace")[-400:]
                raise ReferenceFault(f"bash's {answer} sandbox exited {run.returncode}: {tail}")
            lines = run.stdout.decode("utf-8", "surrogateescape").split("\n")
            if lines[-1] != "" or len(lines) - 1 != len(shard):
                raise ReferenceFault(f"bash's {answer} gave {len(lines) - 1} rows for {len(shard)} texts")
            return [json.loads(line) for line in lines[:-1]]

        with ThreadPoolExecutor(SHARDS) as pool:
            return [rec for part in pool.map(one, shards) for rec in part]

    def ensure_accept(self, texts: Iterable[ShellText]) -> None:
        todo = self._missing("accept", texts, self.accepted)
        rows = [AcceptRow(k=key_of(t), ok=r["ok"], err=r["err"])
                for t, r in zip(todo, self._records("accept", todo), strict=True)]
        self._append("accept", rows)
        self.accepted.update((r["k"], Accept(r["ok"], r["err"])) for r in rows)

    def ensure_reprint(self, texts: Iterable[ShellText]) -> None:
        todo = self._missing("reprint", texts, self.reprinted)
        rows = [ReprintRow(k=key_of(t), ok=r["ok"], text=r["text"], err=r["err"])
                for t, r in zip(todo, self._records("reprint", todo), strict=True)]
        self._append("reprint", rows)
        self.reprinted.update((r["k"], Reprint(r["ok"], r["text"], r["err"])) for r in rows)

    def ensure_word(self, answer: Literal["word", "list"], texts: Iterable[ShellText]) -> None:
        have = self.worded if answer == "word" else self.listed
        todo = self._missing(answer, texts, have)
        rows = [WordRow(k=key_of(t), one=r["one"], err=r["err"])
                for t, r in zip(todo, self._records(answer, todo), strict=True)]
        self._append(answer, rows)
        have.update((r["k"], Word(r["one"], r["err"])) for r in rows)


def main() -> ExitStatus:
    """Build the accept answer of every corpus command and the reprint of every accepted one, or serve one answer
    inside the sandbox."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--bash", type=Path, default=BASH)
    parser.add_argument("--serve", choices=["accept", "reprint", "word", "list"])
    args = parser.parse_args()
    if args.serve:
        return serve(args.serve, args.bash)
    resource.setrlimit(resource.RLIMIT_AS, (8 << 30, 8 << 30))
    start = time.perf_counter()
    try:
        ref = Reference(args.bash)
        commands = [r["cmd"] for r in corpus_rows(args.corpus)]
        ref.ensure_accept(commands)
        accepted = [c for c in commands if ref.accepted[key_of(c)].ok]
        ref.ensure_reprint(accepted)
    except (ReferenceFault, OSError) as error:
        print(f"bash_reference: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"tag": ref.tag, "commands": len(commands), "accepted": len(accepted), "ran": ref.ran,
                      "seconds": round(time.perf_counter() - start, 2)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
