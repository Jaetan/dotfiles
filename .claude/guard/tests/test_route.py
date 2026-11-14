"""claude-call's route as state/route.py states it: the wrapper's path, the ledger's place, the line claude-call writes
and the reader that waits for it. Each case writes the ledger of a process key of its own in the temporary folder, and
the waiting cases replace the module's pause and clock, so no case sleeps or reads the real state."""

import tempfile
import time
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from dispatcher.tree import Seconds, ShellText
from dispatcher.verdict import KitFault
from state import route
from state.route import Digest, LedgerLine, Offset, awaited, digest, end, ledger, line, seen
from state.session import BootId, Pid, Places, ProcessKey, RecordId, Session, StartTicks

KIT = Path(__file__).resolve().parents[1]
KEY = ProcessKey(BootId("b0"), Pid(7), StartTicks(9))
CAFE = ShellText("echo 'café' $HOME")
CAFE_SHA256 = Digest("beb109244f5ee4d8d0de1b8786b6bc051e9a3e7d650e051314d043b26f861d93")  # by sha256sum
OTHER = Digest("0" * 64)


class Ledger(unittest.TestCase):
    """A case with a sessions folder of its own and the ledger of KEY in it, not yet written."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.places = Places(sessions=Path(scratch.name) / "sessions", proc=Path(scratch.name) / "proc")
        self.path = ledger(self.places, KEY)

    def write(self, *lines: LedgerLine) -> Offset:
        """Append ``lines`` to the ledger; give its end."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as out:
            out.write(b"".join(lines))
        return end(self.path)


class Route(Ledger):
    def test_the_wrapper_is_the_kits_bin_route_bash(self) -> None:
        self.assertEqual(route.WRAPPER, KIT / "bin" / "route" / "bash")

    def test_the_ledger_is_ledger_jsonl_in_the_process_folder(self) -> None:
        self.assertEqual(self.path, self.places.sessions / "7-9-b0" / "ledger.jsonl")

    def test_session_records_never_read_the_ledger(self) -> None:
        session = Session(self.places, KEY, None)
        session.record("run", RecordId("r1"), {})
        self.write(line(OTHER))
        self.assertEqual([record.ident for record in session.records("run")], ["r1"])

    def test_the_digest_is_the_sha256_of_the_utf8_text(self) -> None:
        self.assertEqual(digest(CAFE), CAFE_SHA256)

    def test_a_line_is_the_digest_in_json_and_a_newline_read_back_by_seen(self) -> None:
        self.assertEqual(line(CAFE_SHA256), f'{{"sha256": "{CAFE_SHA256}"}}\n'.encode())
        self.write(line(CAFE_SHA256))
        self.assertTrue(seen(self.path, Offset(0), CAFE_SHA256))

    def test_the_end_of_a_missing_ledger_is_0_and_of_a_written_one_its_size(self) -> None:
        self.assertEqual(end(self.path), 0)
        self.assertEqual(self.write(line(OTHER), line(CAFE_SHA256)), 2 * len(line(OTHER)))

    def test_a_missing_ledger_holds_no_line(self) -> None:
        self.assertFalse(seen(self.path, Offset(0), CAFE_SHA256))

    def test_a_line_past_the_offset_is_seen_and_one_before_it_is_not(self) -> None:
        offset = self.write(line(OTHER))
        self.write(line(CAFE_SHA256))
        self.assertTrue(seen(self.path, offset, CAFE_SHA256))
        self.assertFalse(seen(self.path, self.write(line(OTHER)), CAFE_SHA256))

    def test_a_ledger_shorter_than_the_offset_is_read_from_its_start(self) -> None:
        offset = self.write(line(OTHER), line(OTHER), line(OTHER))
        self.path.write_bytes(line(CAFE_SHA256))
        self.assertTrue(seen(self.path, offset, CAFE_SHA256))

    def test_a_last_line_without_its_newline_is_not_read(self) -> None:
        self.write(line(OTHER), LedgerLine(line(CAFE_SHA256).removesuffix(b"\n")))
        self.assertFalse(seen(self.path, Offset(0), CAFE_SHA256))

    def test_a_line_of_another_shape_or_digest_is_not_matched(self) -> None:
        whole = line(CAFE_SHA256).removesuffix(b"\n")
        for shape in (b"x" + whole, b" " + whole, whole + b" ", whole.replace(b": ", b":"),
                      whole.replace(b"}", b', "x": 1}'), line(OTHER).removesuffix(b"\n")):
            with self.subTest(shape=shape):
                self.path.unlink(missing_ok=True)
                self.write(LedgerLine(shape + b"\n"))
                self.assertFalse(seen(self.path, Offset(0), CAFE_SHA256))

    def test_an_unreadable_ledger_is_a_kit_fault_naming_it(self) -> None:
        self.path.mkdir(parents=True)
        with self.assertRaises(KitFault) as raised:
            seen(self.path, Offset(0), CAFE_SHA256)
        self.assertTrue(raised.exception.reason.startswith(f"the ledger {self.path} cannot be read: "))

    def test_the_pause_and_the_clock_are_time_sleep_and_time_monotonic(self) -> None:
        self.assertIs(route.pause, time.sleep)
        self.assertIs(route.clock, time.monotonic)


class Awaited(Ledger):
    """A case whose real time.sleep fails the case if called, the module's pause and clock replaced by each case."""

    def setUp(self) -> None:
        super().setUp()
        sleep = mock.patch("time.sleep", side_effect=AssertionError("the real time.sleep was called"))
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.offset = self.write(line(CAFE_SHA256))

    def waited(self, readings: Iterator[Seconds], pause: mock.Mock) -> bool:
        """awaited() for CAFE past the offset, the clock giving ``readings`` and the pause ``pause``."""
        with mock.patch.object(route, "clock", side_effect=readings), mock.patch.object(route, "pause", pause):
            return awaited(self.path, self.offset, CAFE_SHA256)

    def test_a_line_landing_during_the_wait_is_seen(self) -> None:
        pause = mock.Mock(side_effect=lambda _: self.write(line(CAFE_SHA256)))
        self.assertTrue(self.waited(iter([Seconds(0.0), Seconds(0.0)]), pause))
        pause.assert_called_once_with(0.025)
        self.sleep.assert_not_called()

    def test_no_line_once_the_clock_passes_the_bound(self) -> None:
        pause = mock.Mock()
        self.assertFalse(self.waited(iter([Seconds(0.0), Seconds(2.99), Seconds(3.0)]), pause))
        pause.assert_called_once_with(0.025)
        self.sleep.assert_not_called()
