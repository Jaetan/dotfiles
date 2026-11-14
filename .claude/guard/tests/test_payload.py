"""What an event gives a rule from its payload, each typed: the tool's response, the transcript's path, the tasks in
flight at a Stop, whether the event is the main thread's, and the switch folder the dispatcher names. Each is read on
a payload holding its key, lacking it, and holding it with a value of another type; the switch folder is read through
core.answer, the dispatcher's own build of the event."""

import unittest
from typing import NewType

from dispatcher.core import Event, EventBytes, FolderPath, TaskId, payload_of
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.writes import FilePath
from tests.test_dispatcher import KitCase, RuleFile, event, rule

EventText = NewType("EventText", str)
"""The fields of a Stop event's JSON object after its hook event's name, as the harness writes them."""

READING = ReadSettings(ReaderPath(""), Seconds(1.0), {})
READS_OFF = rule(ShellText('{"PreToolUse"}'), ShellText("None"), ShellText('return refuse(Prose(f"off={event.off}"))'))


def read(fields: EventText = EventText("")) -> Event:
    """The event a Stop holding ``fields`` gives a rule, its bytes read as the dispatcher reads them."""
    raw = EventBytes(f'{{"hook_event_name": "Stop"{", " if fields else ""}{fields}}}'.encode())
    return Event(payload_of(raw), READING)


class Response(unittest.TestCase):
    def test_the_task_ids_and_the_transcript_folder_are_read(self) -> None:
        said = read(EventText('"tool_response": {"taskId": "t1", "transcriptDir": "/d", "backgroundTaskId": "b2", '
                              '"task_id": "s3"}')).response
        self.assertEqual(said, {"taskId": TaskId("t1"), "transcriptDir": FolderPath("/d"),
                                "backgroundTaskId": TaskId("b2"), "task_id": TaskId("s3")})

    def test_an_event_without_a_response_gives_an_empty_one(self) -> None:
        self.assertEqual(read().response, {})

    def test_a_response_that_is_text_gives_an_empty_one(self) -> None:
        self.assertEqual(read(EventText('"tool_response": "done"')).response, {})

    def test_a_value_that_is_not_text_is_left_out(self) -> None:
        said = read(EventText('"tool_response": {"taskId": 5, "transcriptDir": ["/d"], "task_id": "s3", '
                              '"stdout": "x"}')).response
        self.assertEqual(said, {"task_id": TaskId("s3")})

    def test_an_empty_value_is_left_out(self) -> None:
        said = read(EventText('"tool_response": {"backgroundTaskId": "", "transcriptDir": "", "taskId": "t1"}'))
        self.assertEqual(said.response, {"taskId": TaskId("t1")})


class Transcript(unittest.TestCase):
    def test_the_path_is_read(self) -> None:
        self.assertEqual(read(EventText('"transcript_path": "/t/s.jsonl"')).transcript, FilePath("/t/s.jsonl"))

    def test_an_event_without_a_path_gives_none(self) -> None:
        self.assertIsNone(read().transcript)

    def test_a_path_that_is_not_text_gives_none(self) -> None:
        self.assertIsNone(read(EventText('"transcript_path": 7')).transcript)

    def test_an_empty_path_gives_none(self) -> None:
        self.assertIsNone(read(EventText('"transcript_path": ""')).transcript)


class InFlight(unittest.TestCase):
    def test_a_task_not_ended_is_in_flight(self) -> None:
        tasks = read(EventText('"background_tasks": [{"id": "a", "status": "running"}, '
                               '{"id": "b", "status": "completed"}, {"id": "c"}]')).in_flight
        self.assertEqual(tasks, {TaskId("a"), TaskId("c")})

    def test_a_task_of_each_terminal_status_has_ended(self) -> None:
        tasks = read(EventText('"background_tasks": [{"id": "c", "status": "completed"}, '
                               '{"id": "f", "status": "failed"}, {"id": "k", "status": "killed"}, '
                               '{"id": "s", "status": "stopped"}, {"id": "r", "status": "running"}]')).in_flight
        self.assertEqual(tasks, {TaskId("r")})

    def test_an_event_without_the_list_says_nothing_of_its_tasks(self) -> None:
        self.assertIsNone(read().in_flight)

    def test_a_list_of_another_type_says_nothing_of_its_tasks(self) -> None:
        self.assertIsNone(read(EventText('"background_tasks": 5')).in_flight)

    def test_an_empty_list_has_none_in_flight(self) -> None:
        self.assertEqual(read(EventText('"background_tasks": []')).in_flight, frozenset())

    def test_an_entry_of_another_shape_is_left_out_and_a_status_of_another_type_is_in_flight(self) -> None:
        tasks = read(EventText('"background_tasks": ["a", {"id": 5, "status": "running"}, '
                               '{"id": "", "status": "running"}, {"id": "d", "status": ["completed"]}]')).in_flight
        self.assertEqual(tasks, {TaskId("d")})


class MainThread(unittest.TestCase):
    def test_an_event_naming_no_agent_is_the_main_threads(self) -> None:
        self.assertTrue(read().main_thread)

    def test_an_event_naming_an_agent_is_not_the_main_threads(self) -> None:
        self.assertFalse(read(EventText('"agent_id": "a1"')).main_thread)

    def test_an_agent_id_that_is_not_text_is_the_main_threads(self) -> None:
        self.assertTrue(read(EventText('"agent_id": 5')).main_thread)

    def test_an_empty_agent_id_is_the_main_threads(self) -> None:
        self.assertTrue(read(EventText('"agent_id": ""')).main_thread)


class SwitchFolder(KitCase):
    def test_the_dispatcher_names_its_switch_folder(self) -> None:
        kit = self.make({RuleFile("reads_off.py"): READS_OFF})
        self.assertIn(f"guard reads-off: off={kit.off}", kit.judge(event(tool=ShellText("Read"))).stderr)

    def test_an_event_built_outside_the_dispatcher_names_none(self) -> None:
        self.assertIsNone(read().off)
