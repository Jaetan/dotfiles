"""The red-probes rule, through the dispatcher with every other rule of the kit switched off: at a Stop, each probe of
the declared record whose verdict is not exactly PASS and whose file name the last message does not hold is refused,
together, at every attempt; a red named anywhere, a code span included, by its file name alone, passes; a record that is
missing, opens with another line, is not UTF-8 or holds a row that is not three fields naming a file is a fault refused
once and noted while stop_hook_active is true; no declaration, one naming no record, an event naming no folder and a
SubagentStop are judged as nothing.

Each case has a git work tree of its own in the temporary folder, its `.claude/guard.toml` declaring the record
`probes.tsv` at its root, a sessions folder and a proc tree, one claude process standing in it."""

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
from state.session import Pid, Places, StartTicks
from tests.test_declarations import TomlSource, repository
from tests.test_dispatcher import machine_home
from tests.test_session_state import proc_tree, stat_line

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = "red_probes.py"
RecordText = NewType("RecordText", str)
type Heard = tuple[ExitStatus, Prose, Prose]

PID = Pid(10)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")
DECLARED = TomlSource('schema = 1\n[probes]\nrecord = "probes.tsv"\n')
HEADER = RecordText("probe\tverdict\tseconds\n")
GREEN = RecordText(HEADER + "probes/a.sh\tPASS\t0.5\nprobes/b.sh\tPASS\t1.0\n")
ONE_RED = RecordText(HEADER + "probes/a.sh\tPASS\t0.5\nprobes/x--y.sh\tFAIL\t2.0\n")
TWO_RED = RecordText(HEADER + "probes/x--y.sh\tFAIL\t2.0\nprobes/z.sh\tERROR\t3.0\n")
RED_FINDING = Prose("  * x--y.sh, verdict 'FAIL'")
SAID = Prose("The work is done.")


class Case(unittest.TestCase):
    """A case with a work tree declaring its probe record, a switch folder turning off every rule but this one, and a
    proc tree holding PID."""

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
        self.repo = repository(self.root / "repo", DECLARED)
        self.record = self.repo / "probes.tsv"

    def recorded(self, text: RecordText) -> None:
        """Write ``text`` as the probe record."""
        _ = self.record.write_text(text, encoding="utf-8")

    def answered(self, message: Prose | None = SAID, *, name: HookEvent = "Stop", active: bool = False,
                 folder: bool = True) -> Heard:
        """The dispatcher's exit status, stdout and stderr for an event ``name`` whose last message is ``message``
        (none where None) and whose stop_hook_active is ``active``, naming the work tree as its cwd where ``folder``."""
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), self.env), self.home, self.places)
        fields = {"hook_event_name": name, "session_id": "s", "transcript_path": str(self.root / "session.jsonl"),
                  "stop_hook_active": active}
        if folder:
            fields["cwd"] = str(self.repo)
        if message is not None:
            fields["last_assistant_message"] = message
        got = answer(EventBytes(json.dumps(fields).encode()), settings)
        return got.status, got.stdout, got.stderr

    def passes(self, message: Prose | None = SAID, *, name: HookEvent = "Stop", active: bool = False,
               folder: bool = True) -> None:
        """See the event pass with nothing said."""
        self.assertEqual(self.answered(message, name=name, active=active, folder=folder), (0, "", ""))

    def refused(self, message: Prose | None = SAID, *, active: bool = False) -> list[Prose]:
        """The findings the refusal of a Stop whose last message is ``message`` names."""
        status, stdout, stderr = self.answered(message, active=active)
        self.assertEqual((status, stdout), (2, ""), stderr)
        self.assertIn(f"The record is {self.record}.", stderr)
        return [line for line in stderr.splitlines() if line.startswith("  * ")]

    def faulted(self, fault: Prose) -> None:
        """See a Stop refused for the fault ``fault`` while stop_hook_active is false, and noted once it is true."""
        status, stdout, stderr = self.answered()
        self.assertEqual((status, stdout), (2, ""), stderr)
        self.assertIn(f"guard kit: {fault}", stderr)
        status, stdout, stderr = self.answered(active=True)
        self.assertEqual((status, stderr), (0, ""), stdout)
        self.assertIn(f"guard kit: {fault}", json.loads(stdout)["systemMessage"])


class Refused(Case):
    """Each red probe the last message does not name is refused, together, at every attempt."""

    def test_an_unnamed_fail_is_refused_at_every_attempt(self) -> None:
        self.recorded(ONE_RED)
        for active in (False, True):
            with self.subTest(stop_hook_active=active):
                self.assertEqual(self.refused(active=active), [RED_FINDING])

    def test_two_reds_with_one_named_refuse_the_other_alone(self) -> None:
        self.recorded(TWO_RED)
        self.assertEqual(self.answered(Prose("x--y.sh still fails.")), (2, "", f"""\
guard red-probes: this turn's last message leaves red probes of the project's record unnamed.

  * z.sh, verdict 'ERROR'

The record is {self.record}. A red probe is never left unhandled: make it pass and run the store again, or name each red
probe by its file name in this turn's last message, so the user sees it.
"""))

    def test_a_verdict_not_exactly_pass_is_red(self) -> None:
        self.recorded(RecordText(HEADER + "probes/a.sh\tpass\t1\nprobes/b.sh\tTIMEOUT\t2\n"))
        self.assertEqual(self.refused(), [Prose("  * a.sh, verdict 'pass'"), Prose("  * b.sh, verdict 'TIMEOUT'")])

    def test_a_stop_with_no_last_message_is_refused(self) -> None:
        self.recorded(ONE_RED)
        self.assertEqual(self.refused(None), [RED_FINDING])


class Passed(Case):
    """A record every row of which passes, or whose every red the last message names by its file name, passes."""

    def test_every_row_pass_passes(self) -> None:
        self.recorded(GREEN)
        self.passes()

    def test_a_red_named_in_a_code_span_passes_at_every_attempt(self) -> None:
        self.recorded(ONE_RED)
        for active in (False, True):
            with self.subTest(stop_hook_active=active):
                self.passes(Prose("The probe `x--y.sh` is still red."), active=active)

    def test_a_path_folder_part_is_not_needed_to_name_it(self) -> None:
        self.recorded(ONE_RED)
        self.passes(Prose("x--y.sh fails on its second arm."))


class Faulted(Case):
    """A declared record that cannot be read as the rule reads it is a fault of the kit naming the file."""

    def test_a_missing_record(self) -> None:
        self.faulted(Prose(f"the probe record {self.record} cannot be read: "))

    def test_a_wrong_header(self) -> None:
        self.recorded(RecordText("probe,verdict,seconds\nprobes/x--y.sh,FAIL,2.0\n"))
        self.faulted(Prose(f"the probe record {self.record} does not open with the tab-separated header probe, "
                           "verdict, seconds"))

    def test_bytes_not_utf8(self) -> None:
        _ = self.record.write_bytes(HEADER.encode() + b"probes/x\xff.sh\tFAIL\t2.0\n")
        self.faulted(Prose(f"the probe record {self.record} cannot be read: 'utf-8' codec can't decode"))

    def test_a_row_not_of_three_fields_or_naming_no_file(self) -> None:
        for row in (RecordText("probes/x--y.sh\tFAIL"), RecordText("\tFAIL\t2.0")):
            with self.subTest(row=row):
                self.recorded(RecordText(HEADER + row + "\n"))
                self.faulted(Prose(f"the probe record {self.record} holds a row that is not a probe's path, its "
                                   f"verdict and its seconds: {row!r}"))


class NotJudged(Case):
    """No record declared, no folder named and an agent's end are judged as nothing, a red record standing by."""

    def test_no_declaration_passes(self) -> None:
        self.recorded(ONE_RED)
        (self.repo / ".claude" / "guard.toml").unlink()
        self.passes()

    def test_a_declaration_naming_no_record_passes(self) -> None:
        self.recorded(ONE_RED)
        _ = (self.repo / ".claude" / "guard.toml").write_text("schema = 1\n", encoding="utf-8")
        self.passes()

    def test_an_event_naming_no_folder_passes(self) -> None:
        self.recorded(ONE_RED)
        self.passes(folder=False)

    def test_a_subagent_stop_is_not_judged(self) -> None:
        self.recorded(ONE_RED)
        self.passes(None, name="SubagentStop")


if __name__ == "__main__":
    unittest.main()
