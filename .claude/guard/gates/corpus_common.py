"""What the corpus builder, bash's reference and the corpus gate share: where the corpus lives and how a text is keyed.

The corpus and every cache built from it live beneath ~/.local/state/claude-guard/corpus/, outside any git
repository: nothing of the corpus is committed. A text is keyed by the sha256 of its UTF-8 bytes, a lone surrogate
written back as the byte it stood for, so a key names one text whatever folder or run it came from.
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import NewType, TypedDict

ShellText = NewType("ShellText", str)
TextBytes = NewType("TextBytes", bytes)
TextKey = NewType("TextKey", str)
RowId = NewType("RowId", int)
UseCount = NewType("UseCount", int)

STATE = Path.home() / ".local" / "state" / "claude-guard"
CORPUS_DIR = STATE / "corpus"
CORPUS = CORPUS_DIR / "corpus.jsonl"
PROJECTS = Path.home() / ".claude" / "projects"
PYTHON = Path("/home/nicolas/.local/bin/python3.14")


class CorpusRow(TypedDict):
    """One distinct command: its index in the corpus, the tool_use ids carrying it, its text."""

    i: RowId
    n: UseCount
    cmd: ShellText


def encoded(text: ShellText) -> TextBytes:
    """The bytes a text stands for, a lone surrogate written back as its byte."""
    return TextBytes(text.encode("utf-8", "surrogateescape"))


def key_of(text: ShellText) -> TextKey:
    """The text's key: the sha256 of its bytes, in hex."""
    return TextKey(hashlib.sha256(encoded(text)).hexdigest())


def corpus_rows(path: Path) -> Iterator[CorpusRow]:
    """Every row of a corpus file, in order."""
    with path.open(encoding="utf-8", errors="surrogateescape") as lines:
        for line in lines:
            row: CorpusRow = json.loads(line)
            yield row


Arg = NewType("Arg", str)
Kib = NewType("Kib", int)
Seconds = NewType("Seconds", float)


def sandboxed(argv: list[Arg], cap: Kib, wall: Seconds) -> list[Arg]:
    """The argv that runs `argv` inside bubblewrap: the whole filesystem read-only, no network and no shared
    namespace, a fresh session, a cleared environment (PATH=/usr/bin:/bin, LANG=C.UTF-8, HOME a missing folder),
    an address-space cap of `cap` KiB and a wall limit of `wall` seconds. stdin and stdout pass through."""
    head = ["timeout", "-k", "5", f"{wall:g}", "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--unshare-all", "--die-with-parent", "--new-session", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "LANG", "C.UTF-8", "--setenv", "HOME", "/nonexistent", "--", "/usr/bin/prlimit",
            f"--as={cap * 1024}", "--"]
    return [Arg(a) for a in head] + argv
