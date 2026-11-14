"""The task notices dispatcher/notices.py reads from a session transcript. Each case writes a fixture transcript of its
own in the temporary folder, every queued line built by `queued` from a notice `notification` spells as the harness
does, and reads it with notices()."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NewType
from unittest import mock

from dispatcher.notices import EventLine, Notice, Offset, TaskId, TaskStatus, notices
from dispatcher.verdict import Prose

Line = NewType("Line", str)
EntryType = Literal["queue-operation", "user"]
Operation = Literal["enqueue", "remove"]

EXPIRY = EventLine("[Monitor expired after 30m with 1 event delivered. Re-arm it if you still need the watch.]")
OVER = EventLine("OVER LIMIT 2013442 new tokens past 2000000")


def notification(task: TaskId | None, status: TaskStatus | None = None, summary: Prose = Prose("Monitor event"),
                 events: tuple[Prose, ...] = ()) -> Prose:
    """A notice as the harness queues it: its head, its summary, then each event."""
    head = "".join([f"<task-id>{task}</task-id>\n" if task else "", f"<status>{status}</status>\n" if status else ""])
    body = "".join(f"<event>{event}</event>\n" for event in events)
    return Prose(f"<task-notification>\n{head}<summary>{summary}</summary>\n{body}</task-notification>")


def queued(content: Prose | list[Prose], kind: EntryType = "queue-operation", operation: Operation = "enqueue") -> Line:
    """The transcript line that queues ``content``."""
    return Line(json.dumps({"type": kind, "operation": operation, "content": content}) + "\n")


class Transcript(unittest.TestCase):
    """A case with a transcript file of its own, written from lines."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "session.jsonl"

    def write(self, *lines: Line) -> Offset:
        """Write the transcript as ``lines``; give its length in bytes."""
        self.path.write_text("".join(lines))
        return Offset(self.path.stat().st_size)

    def read(self, *lines: Line) -> list[Notice]:
        """The notices of a transcript of ``lines``, read from 0."""
        self.write(*lines)
        return notices(self.path, Offset(0))[0]


class Notices(Transcript):
    def test_each_ending_status_ends_its_task(self) -> None:
        for status in ("completed", "failed", "killed", "stopped"):
            with self.subTest(status=status):
                [notice] = self.read(queued(notification(TaskId("b1"), TaskStatus(status))))
                self.assertEqual(notice, Notice(TaskId("b1"), TaskStatus(status), ()))
                self.assertTrue(notice.ends_task)

    def test_a_running_status_or_none_ends_no_task(self) -> None:
        running, silent = self.read(queued(notification(TaskId("b1"), TaskStatus("running"))),
                                    queued(notification(TaskId("b2"))))
        self.assertEqual((running.status, silent.status), (TaskStatus("running"), None))
        self.assertFalse(running.ends_task or silent.ends_task)

    def test_an_expiry_ends_a_watch(self) -> None:
        [notice] = self.read(queued(notification(TaskId("b1"), events=(Prose(f"1 event delivered\n{EXPIRY}"),))))
        self.assertEqual(notice.events, (EventLine("1 event delivered"), EXPIRY))
        self.assertEqual((notice.ends_watch, notice.over_limit, notice.ends_task), (True, False, False))

    def test_an_over_limit_line_reports_over_limit(self) -> None:
        [notice] = self.read(queued(notification(TaskId("b1"), events=(Prose(f"1900000 new tokens\n{OVER}"),))))
        self.assertEqual((notice.over_limit, notice.ends_watch), (True, False))

    def test_a_notice_quoted_in_a_summary_is_not_read(self) -> None:
        quoted = notification(TaskId("b2"), TaskStatus("killed"), Prose("quoted"))
        self.assertEqual(self.read(queued(notification(TaskId("b1"), summary=Prose(f"result: {quoted}"))),
                                   queued(notification(None, summary=quoted))),
                         [Notice(TaskId("b1"), None, ())])

    def test_only_the_lines_of_a_closed_event_are_read(self) -> None:
        content = Prose(f"{notification(TaskId('b1'))}\n{OVER} outside</event>\n<event>{EXPIRY} unclosed\n"
                        f"<event>{OVER}</event>\n<event>{EXPIRY} at the end")
        [notice] = self.read(queued(content))
        self.assertEqual(notice.events, (OVER,))


class Lines(Transcript):
    def test_a_line_of_another_type_or_operation_or_content_is_skipped(self) -> None:
        notice = notification(TaskId("b1"), TaskStatus("completed"))
        self.assertEqual(self.read(queued(notice, kind="user"), queued(notice, operation="remove"),
                                   queued([notice]), queued(Prose(f"user text {notice}"))), [])

    def test_a_line_that_is_not_a_json_object_is_skipped(self) -> None:
        deep = 100_000
        kept = queued(notification(TaskId("b1"), TaskStatus("failed")))
        self.assertEqual(self.read(Line("<task-notification> torn\n"), Line(json.dumps(["<task-notification>"]) + "\n"),
                                   Line("[" * deep + '"<task-notification>"' + "]" * deep + "\n"), kept),
                         [Notice(TaskId("b1"), TaskStatus("failed"), ())])

    def test_a_line_without_the_notice_opening_is_never_parsed(self) -> None:
        line = Line(json.dumps({"type": "user", "content": "text"}) + "\n")
        with mock.patch("json.loads", wraps=json.loads) as parse:
            self.read(line, line, queued(notification(TaskId("b1"))))
        self.assertEqual(parse.call_count, 1)


class Offsets(Transcript):
    def test_a_partial_last_line_is_kept_for_the_next_read(self) -> None:
        first, second = queued(notification(TaskId("b1"))), queued(notification(TaskId("b2"), TaskStatus("stopped")))
        stopped = self.write(first, Line(second[:-1]))
        found, offset = notices(self.path, Offset(0))
        self.assertEqual((found, offset), ([Notice(TaskId("b1"), None, ())], Offset(len(first.encode()))))
        self.assertEqual(stopped, offset + len(second.encode()) - 1)
        end = self.write(first, second)
        self.assertEqual(notices(self.path, offset), ([Notice(TaskId("b2"), TaskStatus("stopped"), ())], end))

    def test_a_transcript_shorter_than_the_offset_is_read_from_0(self) -> None:
        lines = queued(notification(TaskId("b1"))), queued(notification(TaskId("b2")))
        end = self.write(*lines)
        self.assertEqual(notices(self.path, Offset(end + 10)),
                         ([Notice(TaskId("b1"), None, ()), Notice(TaskId("b2"), None, ())], end))
        self.assertEqual(notices(self.path, end), ([], end))
