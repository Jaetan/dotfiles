"""The tier line, stated once for the memory-tier rule and for claude-call: the memory tier of each Bash or Monitor call.

THE LINE. At PreToolUse the memory-tier rule appends line(digest(text), tier) to tiers(LIVE, the key of its CLAUDE_PID):
`{"sha256": "<hex>", "tier": "heavy"}` or `"light"` and a newline, the sha256 of the call's command text as
state/route.py's digest() gives it, written by one write on an O_APPEND descriptor so the line lands whole.

THE FILE. It is tiers.jsonl in the claude process's state folder, beside the ledger, so the sweep at SessionStart
removes it with the folder, and Session.records(), which reads the folder's .json files alone, never reads it.

THE READER. claude-call, which imports nothing of the kit, is to read the line of its call's digest before the call's
scope starts: heavy_max for a heavy line, call_max for a light one, heavy_max where it finds none.
"""

import json
from pathlib import Path
from typing import Literal, NewType

from state.route import Digest
from state.route import digest as digest
from state.session import Places, ProcessKey

Tier = Literal["heavy", "light"]
TierLine = NewType("TierLine", bytes)

TIERS = "tiers.jsonl"


def tiers(places: Places, key: ProcessKey) -> Path:
    """The tier file of the claude process ``key``, in that process's state folder."""
    return places.sessions / key.name / TIERS


def line(sha256: Digest, tier: Tier) -> TierLine:
    """The bytes appended for the call whose command text has the digest ``sha256`` and whose memory tier is
    ``tier``."""
    return TierLine((json.dumps({"sha256": sha256, "tier": tier}) + "\n").encode("ascii"))


def append(path: Path, line: TierLine) -> None:
    """Append ``line`` to the tier file ``path`` in one write on an O_APPEND descriptor, its folder made where
    missing; OSError where it cannot be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab", buffering=0) as tier_file:  # O_APPEND, and one write(2) per write
        _ = tier_file.write(line)
