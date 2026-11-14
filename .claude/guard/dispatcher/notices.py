"""The task notices a session transcript queues, read from the byte where the last read stopped.

THE RULE: the harness writes each background task's notice into the session transcript as one JSON line, an object of
type `queue-operation` and operation `enqueue` whose content starts `<task-notification>`. notices(path, offset) reads
the whole lines after byte ``offset`` and gives a Notice for each such line whose head names a task, with the offset
where it stopped; it skips every other line, one without `<task-notification>` before parsing it. A notice ends its
task when its status is completed, failed, killed or stopped. Its event lines are the lines of each closed `<event>`:
it ends a watch when one starts `[Monitor expired`, and reports OVER LIMIT when one starts `OVER LIMIT`. The module
only reads the file: it keeps no state and judges no event.

EXCEPTIONS, each with the failure it holds:
- The task and status come from the head, before `<summary>`: else a result quoting other notices ends their tasks.
- A last line without its newline is left for the next read: else a line still being written is lost.
- A transcript shorter than ``offset`` is read again from 0: else a replaced transcript hides its notices.
"""

import json
import re
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, cast

from dispatcher.verdict import Prose

Offset = NewType("Offset", int)
TaskId = NewType("TaskId", str)
TaskStatus = NewType("TaskStatus", str)
EventLine = NewType("EventLine", str)

NOTICE_START = "<task-notification>"
TASK = re.compile(r"<task-id>([^<]*)</task-id>")
STATUS = re.compile(r"<status>([^<]*)</status>")
TERMINAL = frozenset({TaskStatus("completed"), TaskStatus("failed"), TaskStatus("killed"), TaskStatus("stopped")})
EXPIRED = "[Monitor expired"
OVER_LIMIT = "OVER LIMIT"


class QueueEntry(TypedDict, total=False):
    """A transcript line that queues a notice."""

    type: Literal["queue-operation"]
    operation: Literal["enqueue", "remove"]
    content: Prose


class Notice(NamedTuple):
    """One task notice: its task, its status where it has one, and the lines of its closed events."""

    task: TaskId
    status: TaskStatus | None
    events: tuple[EventLine, ...]

    @property
    def ends_task(self) -> bool:
        """Its status is completed, failed, killed or stopped."""
        return self.status in TERMINAL

    @property
    def ends_watch(self) -> bool:
        """An event line starts `[Monitor expired`."""
        return any(line.startswith(EXPIRED) for line in self.events)

    @property
    def over_limit(self) -> bool:
        """An event line starts `OVER LIMIT`."""
        return any(line.startswith(OVER_LIMIT) for line in self.events)


def event_lines(text: Prose) -> tuple[EventLine, ...]:
    """The lines of every closed `<event>` in ``text``."""
    return tuple(EventLine(line) for part in text.split("</event>")[:-1] if "<event>" in part
                 for line in part.rpartition("<event>")[2].splitlines())


def notice_in(content: Prose) -> Notice | None:
    """The notice a queued text holds, or None: its task and status from its head, its event lines from the rest."""
    if not content.startswith(NOTICE_START):
        return None
    head, _, rest = content.partition("<summary>")
    task, status = TASK.search(head), STATUS.search(head)
    if task is None:
        return None
    return Notice(TaskId(task.group(1)), TaskStatus(status.group(1)) if status else None, event_lines(Prose(rest)))


def notices(path: Path, offset: Offset) -> tuple[list[Notice], Offset]:
    """The notices queued in the transcript at ``path`` after byte ``offset``, and the offset where reading stopped."""
    if path.stat().st_size < offset:
        offset = Offset(0)
    found: list[Notice] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            offset = Offset(offset + len(raw))
            if NOTICE_START.encode() not in raw:
                continue
            try:
                entry = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            queued = cast("QueueEntry", entry) if isinstance(entry, dict) else QueueEntry()
            content = queued.get("content")
            if queued.get("type") == "queue-operation" and queued.get("operation") == "enqueue" \
                    and isinstance(content, str) and (notice := notice_in(content)) is not None:
                found.append(notice)
    return found, offset
