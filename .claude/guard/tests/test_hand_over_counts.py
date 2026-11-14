"""The hand-over-counts rule, through the dispatcher with every other rule of the kit switched off: at a Stop, a count
of passes below its total is refused at every attempt unless its paragraph or list item says the checks have not ended
or names a task id the task list gained since the last Stop passed, or the session's start; an id the list held then,
one it does not hold, and any id while nothing is kept dispose of nothing; a count in cited text is not read and a full
count passes; the ids are kept at SessionStart and at each Stop passed, never at one refused; no task list and a
SubagentStop are judged as nothing.

Each case has a transcript folder holding the task list, a sessions folder and a proc tree of its own in the temporary
folder, one claude process standing in it."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import NewType

from dispatcher.core import EventBytes, ExitStatus, FolderPath, HookEvent, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import Environment, VarName
from state.session import FieldName, FieldValue, Pid, Places, RecordId, Session, StartTicks, process_key
from tests.test_dispatcher import machine_home
from tests.test_session_state import proc_tree, stat_line

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = "hand_over_counts.py"
TaskList = NewType("TaskList", str)
type Kept = dict[RecordId, dict[FieldName, FieldValue]]

PID = Pid(10)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")
LISTED = TaskList("# Tasks\n\n- N.21 the first\n- N.3 the second\n")
GAINED = TaskList(LISTED + "- N.40 a new one\n")
RED = Prose("206 of 207 pass.")
RED_FINDING = Prose("  * '206 of 207 pass' leaves 1 without a disposition")
IDENT, FIELD = RecordId("task-ids"), FieldName("ids")
AT_START: Kept = {IDENT: {FIELD: FieldValue("N.21 N.3")}}
AFTER_GAIN: Kept = {IDENT: {FIELD: FieldValue("N.21 N.3 N.40")}}


class Case(unittest.TestCase):
    """A case with a transcript folder whose task list holds LISTED, a switch folder turning off every rule but this
    one, and a proc tree holding PID."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.off = self.root / "off"
        self.off.mkdir()
        for rule in (KIT / "rules").glob("[!_]*.py"):
            if rule.name != OWN:
                (self.off / rule.stem.replace("_", "-")).touch()
        self.places = Places(self.root / "sessions", proc_tree(self.root, BOOT, {PID: stat_line(PID, START)}))
        self.home = machine_home(self)
        self.env: Environment = {VarName("HOME"): ShellText(str(self.home)),
                                 VarName("CLAUDE_PID"): ShellText(str(PID))}
        self.project = self.root / "project"
        (self.project / "memory").mkdir(parents=True)
        self.listing(LISTED)

    def listing(self, text: TaskList) -> None:
        """Write ``text`` as the session's task list."""
        _ = (self.project / "memory" / "TASKS.md").write_text(text, encoding="utf-8")

    def answered(self, name: HookEvent, message: Prose | None = None, *, active: bool = False,
                 transcript: bool = True) -> tuple[ExitStatus, Prose]:
        """The dispatcher's exit status and stderr for an event ``name`` whose last message is ``message`` (none where
        None) and whose stop_hook_active is ``active``, naming the case's transcript where ``transcript``."""
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), self.env), self.home, self.places)
        fields = {"hook_event_name": name, "session_id": "s", "cwd": str(self.root), "stop_hook_active": active}
        if transcript:
            fields["transcript_path"] = str(self.project / "session.jsonl")
        if message is not None:
            fields["last_assistant_message"] = message
        got = answer(EventBytes(json.dumps(fields).encode()), settings)
        return got.status, got.stderr

    def started(self) -> None:
        """Start the session, and see it pass."""
        self.assertEqual(self.answered("SessionStart"), (0, ""))

    def passes(self, message: Prose) -> None:
        """See a Stop whose last message is ``message`` pass."""
        self.assertEqual(self.answered("Stop", message), (0, ""))

    def refused(self, message: Prose, *, active: bool = False) -> list[Prose]:
        """The findings the refusal of a Stop whose last message is ``message`` names."""
        status, stderr = self.answered("Stop", message, active=active)
        self.assertEqual(status, 2, stderr)
        return [line for line in stderr.splitlines() if line.startswith("  * ")]

    def kept(self) -> Kept:
        """The notices the process keeps, by id."""
        session = Session(self.places, process_key(self.env, self.places.proc), None)
        return {record.ident: dict(record.fields) for record in session.records("notice")}


class Refused(Case):
    """A count below its total with no not-ended word and no id the list gained is refused, at every attempt."""

    def test_a_count_alone_is_refused_at_every_attempt(self) -> None:
        self.started()
        for active in (False, True):
            with self.subTest(stop_hook_active=active):
                self.assertEqual(self.refused(RED, active=active), [RED_FINDING])

    def test_a_count_after_its_pass_is_refused(self) -> None:
        self.started()
        self.assertEqual(self.refused(Prose("The suite passed 20 of 21.")),
                         ["  * 'passed 20 of 21' leaves 1 without a disposition"])

    def test_a_count_is_read_whatever_its_case(self) -> None:
        self.started()
        for message, finding in (("Passed 20 of 21.", "Passed 20 of 21"), ("20 of 21 PASS.", "20 of 21 PASS")):
            with self.subTest(message=message):
                self.assertEqual(self.refused(Prose(message)), [f"  * '{finding}' leaves 1 without a disposition"])

    def test_the_refusal_names_the_two_ways_out(self) -> None:
        self.started()
        _, stderr = self.answered("Stop", Prose("12 of 15 tests pass."))
        self.assertIn("  * '12 of 15 tests pass' leaves 3 without a disposition", stderr)
        self.assertIn("fix it, or open a task for it and name its new id in the\nsame paragraph", stderr)

    def test_an_id_the_list_held_at_the_previous_end_is_refused(self) -> None:
        self.started()
        self.assertEqual(self.refused(Prose("206 of 207 pass; N.3 holds the red one.")),
                         ["  * '206 of 207 pass' leaves 1 without a disposition"])

    def test_an_id_the_list_does_not_hold_is_refused(self) -> None:
        self.started()
        self.listing(GAINED)
        self.assertEqual(self.refused(Prose("206 of 207 pass; N.99 holds the red one.")), [RED_FINDING])

    def test_with_nothing_kept_a_new_id_disposes_of_nothing(self) -> None:
        self.listing(GAINED)
        self.assertEqual(self.refused(Prose("206 of 207 pass; N.40 holds the red one.")), [RED_FINDING])

    def test_a_list_item_is_judged_apart_from_its_neighbours(self) -> None:
        self.started()
        self.listing(GAINED)
        for first, second in (("- ", "- "), ("1. ", "2) ")):
            with self.subTest(item=first):
                message = Prose(f"Results:\n{first}206 of 207 pass\n{second}N.40 is opened")
                self.assertEqual(self.refused(message), [RED_FINDING])

    def test_a_refused_stop_keeps_nothing(self) -> None:
        self.started()
        self.listing(GAINED)
        self.assertEqual(self.refused(Prose("206 of 207 pass, N.40.\n\n5 of 6 pass.")),
                         ["  * '5 of 6 pass' leaves 1 without a disposition"])
        self.assertEqual(self.kept(), AT_START)
        self.passes(Prose("206 of 207 pass, N.40."))


class Passed(Case):
    """A not-ended word, an id the list gained, a full count, a cited count, no list and an agent's end pass."""

    def test_a_not_ended_word_disposes(self) -> None:
        self.started()
        for word in ("pending", "Still running", "QUEUED", "in progress"):
            with self.subTest(word=word):
                self.passes(Prose(f"206 of 207 pass, the last one {word}."))

    def test_an_id_the_list_gained_since_the_last_stop_passed_disposes(self) -> None:
        self.started()
        self.passes(Prose("Done."))
        self.listing(GAINED)
        self.passes(Prose("206 of 207 pass;\nthe red one is N.40."))
        self.assertEqual(self.kept(), AFTER_GAIN)

    def test_a_full_count_passes(self) -> None:
        self.started()
        self.passes(Prose("7 of 7 pass, and the suite passed 9 of 9."))

    def test_a_cited_count_is_not_read(self) -> None:
        self.started()
        for cited in ("`206 of 207 pass`", '"206 of 207 pass"', "“206 of 207 pass”"):
            with self.subTest(cited=cited):
                self.passes(Prose(f"The rule refuses {cited} alone."))

    def test_no_task_list_passes_and_keeps_nothing(self) -> None:
        (self.project / "memory" / "TASKS.md").unlink()
        self.started()
        self.passes(RED)
        self.assertEqual(self.kept(), {})

    def test_a_stop_naming_no_transcript_passes(self) -> None:
        self.assertEqual(self.answered("Stop", RED, transcript=False), (0, ""))
        self.assertEqual(self.kept(), {})

    def test_a_stop_with_no_message_passes_and_keeps_the_ids(self) -> None:
        self.assertEqual(self.answered("Stop"), (0, ""))
        self.assertEqual(self.kept(), AT_START)

    def test_a_subagent_stop_is_not_judged(self) -> None:
        self.assertEqual(self.answered("SubagentStop", RED), (0, ""))
        self.assertEqual(self.kept(), {})


class Keeping(Case):
    """The list's ids are kept at SessionStart and at each Stop passed."""

    def test_session_start_keeps_the_ids(self) -> None:
        self.started()
        self.assertEqual(self.kept(), AT_START)

    def test_a_stop_passed_keeps_the_ids(self) -> None:
        self.started()
        self.listing(GAINED)
        self.passes(Prose("Done."))
        self.assertEqual(self.kept(), AFTER_GAIN)


if __name__ == "__main__":
    _ = unittest.main()
