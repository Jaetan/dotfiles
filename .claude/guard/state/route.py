"""claude-call's route, stated once for the route rule and for claude-call: the wrapper and the ledger of its calls.

THE ROUTE. Once the Bash tool's shell is bash, CLAUDE_CODE_SHELL names WRAPPER, the kit's bin/route/bash, which hands
every Bash and Monitor call to claude-call. This module names the wrapper and never reads it.

THE LEDGER. At the start of its main, before the call's scope starts, claude-call appends line(digest(call_text(LINE)))
to ledger(LIVE, the key of its CLAUDE_PID): `{"sha256": "<hex>"}` and a newline, the sha256 of the call's text, written
by one os.write on an O_APPEND descriptor so the line lands whole. The ledger is ledger.jsonl in the claude process's
state folder, so the sweep at SessionStart removes it with the folder, and Session.records(), which reads the folder's
.json files alone, never reads it.

THE READER. A reader takes the ledger's end() before a call and asks awaited() after it, with the digest of the
PostToolUse command, the text claude-call ran: the call is recorded when a whole line after that offset is its line,
every other line skipped. claude-call can start after PostToolUse does, so seen() is asked again every POLL seconds
until BOUND seconds have passed. A missing ledger holds no line; one that cannot be read raises KitFault naming it.

Exceptions, each with the failure it holds:
- a ledger shorter than the offset is read from its start, so a ledger cut or remade after end() still shows its line;
- a last line without its newline is not read, so a line cut short is never taken for a whole one.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import NewType

from dispatcher.tree import Seconds, ShellText
from dispatcher.verdict import KitFault, Prose
from state.session import Places, ProcessKey

Digest = NewType("Digest", str)
"""The sha256 of a call's text as 64 lowercase hex digits."""
LedgerLine = NewType("LedgerLine", bytes)
Offset = NewType("Offset", int)
"""A byte offset into the ledger."""

KIT = Path(__file__).resolve().parents[1]
WRAPPER = KIT / "bin" / "route" / "bash"
LEDGER = "ledger.jsonl"
POLL = Seconds(0.025)
BOUND = Seconds(3.0)
pause = time.sleep  # the wait between two asks and the clock awaited() reads, which a test replaces so none sleeps
clock = time.monotonic


def ledger(places: Places, key: ProcessKey) -> Path:
    """The ledger of the claude process ``key``, in that process's state folder."""
    return places.sessions / key.name / LEDGER


def digest(text: ShellText) -> Digest:
    """The sha256 of ``text``'s UTF-8 bytes."""
    return Digest(hashlib.sha256(text.encode("utf-8")).hexdigest())


def line(digest: Digest) -> LedgerLine:
    """The bytes claude-call appends for the call whose text has ``digest``."""
    return LedgerLine((json.dumps({"sha256": digest}) + "\n").encode("ascii"))


def end(path: Path) -> Offset:
    """The size in bytes of the ledger ``path``, 0 where it is missing."""
    try:
        return Offset(path.stat().st_size)
    except FileNotFoundError:
        return Offset(0)


def seen(path: Path, offset: Offset, digest: Digest) -> bool:
    """Whether a whole line after byte ``offset`` of the ledger ``path`` is line(digest), the whole ledger read where it
    is shorter than ``offset``; KitFault naming the ledger where it cannot be read."""
    try:
        with path.open("rb") as ledger_file:
            size = ledger_file.seek(0, os.SEEK_END)
            ledger_file.seek(offset if offset <= size else 0)
            tail = ledger_file.read()
    except FileNotFoundError:
        return False
    except OSError as fault:
        raise KitFault(Prose(f"the ledger {path} cannot be read: {fault}")) from None
    return line(digest) in [whole + b"\n" for whole in tail.split(b"\n")[:-1]]  # the piece after the last newline is cut


def awaited(path: Path, offset: Offset, digest: Digest) -> bool:
    """seen(), asked again every POLL seconds until it holds or BOUND seconds have passed."""
    deadline = clock() + BOUND
    while not seen(path, offset, digest):
        if clock() >= deadline:
            return False
        pause(POLL)
    return True
