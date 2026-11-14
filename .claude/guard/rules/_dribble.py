"""The call model of a commit dribble, built once for the event that asks it.

A dribble is the `.commands-to-run.sh` at the root of a git work tree, which the user runs. Two rules read it as
`bash DRIBBLE` run from that root reads it: rules/entry_points.py for its commits, rules/merge_dribble.py for its
merges. Building that model reads the dribble and every script it runs, a few processes each time, so `model` builds
it once for an event and gives that event the same model however often it asks, while the dribble keeps its path,
size and modification time; another event, or the dribble rewritten, gets a model of its own.
"""

import shlex
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher.core import Event
from dispatcher.model import Call, build
from dispatcher.tree import ShellText
from dispatcher.words import Directory

Size = NewType("Size", int)
Nanoseconds = NewType("Nanoseconds", int)
type Version = tuple[Path, Size, Nanoseconds]
"""A dribble as its model is kept: its path, its size in bytes and its modification time."""


class Kept(NamedTuple):
    """A model built and the event that asked for it."""

    event: Event
    call: Call


KEPT: dict[Version, Kept] = {}
"""The model of each version of a dribble, kept for the event that built it."""


def model(dribble: Path, event: Event) -> Call | None:
    """The call model of ``dribble``, `bash DRIBBLE` run from its folder, built once for ``event``; None where
    ``dribble`` is no file."""
    if not dribble.is_file():
        return None
    status = dribble.stat()
    version: Version = (dribble, Size(status.st_size), Nanoseconds(status.st_mtime_ns))
    kept = KEPT.get(version)
    if kept is None or kept.event is not event:
        call = build(ShellText(f"bash {shlex.quote(str(dribble))}"), Directory(str(dribble.parent)), event.reading)
        kept = KEPT[version] = Kept(event, call)
    return kept.call
