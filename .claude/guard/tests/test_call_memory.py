"""claude-call's memory cap, pinned to the kit: each call's scope gets MemoryMax with MemorySwapMax=0, call_max for a
call the memory-tier rule wrote light and heavy_max for every other, read from the machine file in the sizes
declarations/schema.py reads, an empty size refused, the tier line found where and as state/tiers.py writes it. Each
case loads call/claude-call as tests/test_call_ledger.py does, its SESSIONS, PROC, MACHINE and RUNTIME replaced by a
sessions folder, a proc tree, a machine file and a runtime folder of the case's own, and its event and warn by mocks,
so no case reads the real /proc or machine file or writes the real state, the real events log or stderr."""

import os
import shlex
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest import mock

from declarations import schema
from declarations.schema import ByteCount, Reading, TomlKey, TomlText
from dispatcher.tree import ShellText
from dispatcher.verdict import KitFault
from dispatcher.words import VarName
from state import tiers
from state.session import Places, process_key
from tests.test_call_ledger import BOOT, PID, STAT, loaded, tool_line

type Why = Literal["call_max: a light line", "heavy_max: a heavy line", "heavy_max: no tier line"]
type Cap = tuple[ByteCount, Why]

CALL = ShellText("cabal run shake -- build")
OTHER = ShellText("true")
CALL_MAX, HEAVY_MAX = ByteCount(3 << 30), ByteCount(19 << 30)
MACHINE = 'schema = 1\n[memory]\ncall_max = "3G"\nheavy_max = "19G"\n'
SIZES = ("7", "7K", "7M", "7G", "7T", "512M", "24G", "0012K")
BAD_SIZES = ("G", "0", "0G", "4X", "4g", "4.5G", "-4G", " 4G", "4GG", "G4")
LIGHT: Why = "call_max: a light line"
HEAVY: Why = "heavy_max: a heavy line"
NO_LINE: Why = "heavy_max: no tier line"


class Memory(unittest.TestCase):
    """A case with a sessions folder, a proc tree, a machine file and a runtime folder of its own, CLAUDE_PID naming
    the tree's process."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.places = Places(sessions=self.scratch / "sessions", proc=self.scratch / "proc")
        (self.places.proc / PID).mkdir(parents=True)
        _ = (self.places.proc / PID / "stat").write_text(STAT, encoding="utf-8")
        (self.places.proc / "sys" / "kernel" / "random").mkdir(parents=True)
        _ = (self.places.proc / "sys" / "kernel" / "random" / "boot_id").write_text(BOOT, encoding="ascii")
        environ = mock.patch.dict(os.environ, {"CLAUDE_PID": PID})
        environ.start()
        self.addCleanup(environ.stop)
        self.call = loaded(None)
        self.call.SESSIONS, self.call.PROC = self.places.sessions, self.places.proc
        self.call.MACHINE = self.scratch / "machine.toml"
        _ = self.call.MACHINE.write_text(MACHINE, encoding="utf-8")
        self.call.RUNTIME = self.scratch / "runtime"
        (self.call.RUNTIME / "systemd" / "private").mkdir(parents=True)
        self.event, self.warn = mock.Mock(), mock.Mock()
        self.call.event, self.call.warn = self.event, self.warn
        self.tiers = tiers.tiers(self.places, process_key({VarName("CLAUDE_PID"): PID}, self.places.proc))

    def cap(self, call: ShellText = CALL) -> Cap | None:
        """claude-call's cap for the tool's line running ``call``."""
        return self.call.memory_max(tool_line(ShellText(f"eval {shlex.quote(call)}")))

    def written(self, call: ShellText, tier: tiers.Tier) -> None:
        """The memory-tier rule's line for ``call``, appended as state/tiers.py appends it."""
        tiers.append(self.tiers, tiers.line(tiers.digest(call), tier))

    def test_a_size_reads_as_the_schema_reads_it_on_each_unit_and_a_bad_size_is_refused_by_both(self) -> None:
        reading, key = Reading(self.call.MACHINE), TomlKey("memory.call_max")
        for text in SIZES:
            with self.subTest(size=text):
                self.assertEqual(self.call.size(text), schema.size(reading, TomlText(text), key))
        for text in BAD_SIZES:
            with self.subTest(size=text):
                with self.assertRaises(KitFault):
                    _ = schema.size(reading, TomlText(text), key)
                with self.assertRaises(ValueError):
                    _ = self.call.size(text)
        with self.assertRaises(ValueError):
            _ = self.call.size("")

    def test_a_light_line_gives_call_max(self) -> None:
        self.written(CALL, "light")
        self.assertEqual(self.cap(), (CALL_MAX, LIGHT))
        self.event.assert_not_called()

    def test_a_heavy_line_and_no_line_give_heavy_max(self) -> None:
        self.assertEqual(self.cap(), (HEAVY_MAX, NO_LINE))
        self.written(OTHER, "light")
        self.assertEqual(self.cap(), (HEAVY_MAX, NO_LINE))
        self.written(CALL, "heavy")
        self.assertEqual(self.cap(), (HEAVY_MAX, HEAVY))
        self.assertEqual(self.cap(OTHER), (CALL_MAX, LIGHT))
        self.event.assert_not_called()

    def test_the_last_line_of_the_calls_digest_gives_its_tier(self) -> None:
        self.written(CALL, "light")
        self.written(CALL, "heavy")
        self.assertEqual(self.cap(), (HEAVY_MAX, HEAVY))
        self.written(CALL, "light")
        self.assertEqual(self.cap(), (CALL_MAX, LIGHT))

    def test_a_line_cut_short_no_process_or_a_tier_file_that_cannot_be_read_gives_heavy_max(self) -> None:
        self.tiers.parent.mkdir(parents=True)
        _ = self.tiers.write_bytes(tiers.line(tiers.digest(CALL), "light").rstrip(b"\n"))
        self.assertEqual(self.cap(), (HEAVY_MAX, NO_LINE))
        self.written(CALL, "light")
        with mock.patch.dict(os.environ):
            del os.environ["CLAUDE_PID"]
            self.assertEqual(self.cap(), (HEAVY_MAX, NO_LINE))
            os.environ["CLAUDE_PID"] = "4194303"  # a pid the proc tree does not hold
            self.assertEqual(self.cap(), (HEAVY_MAX, NO_LINE))
        self.tiers.unlink()
        self.tiers.mkdir()
        self.assertEqual(self.cap(), (HEAVY_MAX, NO_LINE))
        self.event.assert_not_called()

    def test_heavy_max_defaults_to_call_max(self) -> None:
        _ = self.call.MACHINE.write_text('[memory]\ncall_max = "3G"\n', encoding="utf-8")
        self.assertEqual(self.cap(), (CALL_MAX, NO_LINE))

    def test_no_machine_file_memory_absent_or_a_file_that_gives_no_cap_sets_none_recorded_once(self) -> None:
        for shape, text in (("no machine file", None), ("[memory] absent", '[memory]\nabsent = "no cgroup"\n'),
                            ("no [memory]", "schema = 1\n"), ("no call_max", '[memory]\nheavy_max = "19G"\n'),
                            ("a bad size", '[memory]\ncall_max = "4X"\n'), ("a number", "[memory]\ncall_max = 4\n"),
                            ("not TOML", "[memory\n")):
            with self.subTest(shape=shape):
                self.event.reset_mock()
                self.call.MACHINE.unlink(missing_ok=True)
                if text is not None:
                    _ = self.call.MACHINE.write_text(text, encoding="utf-8")
                self.written(CALL, "light")
                self.assertIsNone(self.cap())
                self.assertEqual([called.args[0] for called in self.event.call_args_list], ["no-cap"])
                self.assertIn(str(self.call.MACHINE), self.event.call_args.args[1])

    def test_a_scope_with_a_cap_sets_its_memory_max_and_no_swap_and_one_without_sets_neither(self) -> None:
        capped = self.call.scope_argv("u.scope", (CALL_MAX, LIGHT))
        self.assertEqual(capped[capped.index("--") - 4:capped.index("--")],
                         ["-p", f"MemoryMax={CALL_MAX}", "-p", "MemorySwapMax=0"])
        uncapped = self.call.scope_argv("u.scope", None)
        self.assertEqual(uncapped, capped[:-5] + capped[-1:])

    def test_the_machine_file_is_the_users_or_the_one_claude_call_machine_names(self) -> None:
        with mock.patch.dict(os.environ):
            _ = os.environ.pop("CLAUDE_CALL_MACHINE", None)
            self.assertEqual(loaded(None).MACHINE, Path.home() / ".config" / "claude-guard" / "machine.toml")
            os.environ["CLAUDE_CALL_MACHINE"] = str(self.call.MACHINE)
            self.assertEqual(loaded(None).MACHINE, self.call.MACHINE)
