"""claude-call's ledger, pinned to state/route.py: the line claude-call appends before a call's scope starts lands at
the path and in the bytes the route check reads. Each case loads call/claude-call through a source loader, no bytecode
written, its SESSIONS and PROC replaced by a sessions folder and a proc tree of the case's own, the tree holding one
process with a start time and a boot id, and its warn by a mock, so no case reads the real /proc or writes the real
state, the real events log or stderr."""

import importlib.machinery
import importlib.util
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

import state.session
from dispatcher.tree import ShellText
from dispatcher.words import VarName
from state import route
from state.route import Offset
from state.session import Places, process_key

KIT = Path(__file__).resolve().parents[1]
CLAUDE_CALL = KIT / "call" / "claude-call"
PID = ShellText("4194304")  # PID_MAX_LIMIT, which no live pid reaches: only the case's proc tree names it
STAT = f"{PID} (cl) aude) S {' '.join(['1'] * 18)} 4242 0\n"  # field 22, the start time, 4242
BOOT = "0123abcd-0000-4000-8000-00000000beef\n"
CARRIED = "__CLAUDE_CC_COMMAND_TEXT"
CALL = ShellText("echo 'café' $HOME")
LATER = ShellText("true")
CARRIED_TEXT = ShellText("printf 'carried %s' $HOME")
ELEMENT = ShellText(f"\\builtin 'unset' -v '{CARRIED}'; 'eval' ${CARRIED}")
CHECK = (f'{{ \\builtin [ -n "${{{CARRIED}:+1}}" ] || {{ \\builtin echo \'{CARRIED}?\' >&2; \\builtin exit 126; }}; }}'
         f' && \'eval\' "{ELEMENT}"')  # the carried-text route's call element, as Claude Code 2.1.325 writes it


def tool_line(element: ShellText) -> ShellText:
    """The Bash tool's line around the call element ``element``."""
    return ShellText(f"source /s.sh 2>/dev/null || true && \\builtin shopt -u extglob 2>/dev/null || true && {element}"
                     " < /dev/null && pwd -P >| /c")


def loaded(root: Path | None) -> ModuleType:
    """call/claude-call as a module, loaded with CLAUDE_CALL_ROOT naming ``root``, unset for None."""
    loader = importlib.machinery.SourceFileLoader("claude_call", str(CLAUDE_CALL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ), mock.patch.object(sys, "dont_write_bytecode", True):
        _ = os.environ.pop("CLAUDE_CALL_ROOT", None)
        if root is not None:
            os.environ["CLAUDE_CALL_ROOT"] = str(root)
        loader.exec_module(module)
    return module


class Ledger(unittest.TestCase):
    """A case with a sessions folder and a proc tree of its own, CLAUDE_PID naming the tree's process, CARRIED unset."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.places = Places(sessions=Path(scratch.name) / "sessions", proc=Path(scratch.name) / "proc")
        (self.places.proc / PID).mkdir(parents=True)
        _ = (self.places.proc / PID / "stat").write_text(STAT, encoding="utf-8")
        (self.places.proc / "sys" / "kernel" / "random").mkdir(parents=True)
        _ = (self.places.proc / "sys" / "kernel" / "random" / "boot_id").write_text(BOOT, encoding="ascii")
        environ = mock.patch.dict(os.environ, {"CLAUDE_PID": PID})
        environ.start()
        self.addCleanup(environ.stop)
        _ = os.environ.pop(CARRIED, None)
        self.call = loaded(None)
        self.call.SESSIONS, self.call.PROC = self.places.sessions, self.places.proc
        self.warn = mock.Mock()
        self.call.warn = self.warn
        self.path = route.ledger(self.places, process_key({VarName("CLAUDE_PID"): PID}, self.places.proc))

    def test_a_call_leaves_its_line_where_the_route_check_reads_it_and_a_later_one_after_it(self) -> None:
        self.call.ledger(tool_line(ShellText(f"eval {shlex.quote(CALL)}")))
        self.assertTrue(route.seen(self.path, Offset(0), route.digest(CALL)))
        self.assertEqual(self.path.read_bytes(), route.line(route.digest(CALL)))
        offset = route.end(self.path)
        self.call.ledger(tool_line(ShellText(f"eval {shlex.quote(LATER)}")))
        self.assertTrue(route.seen(self.path, offset, route.digest(LATER)))
        self.assertEqual(self.path.read_bytes(), route.line(route.digest(CALL)) + route.line(route.digest(LATER)))
        self.warn.assert_not_called()

    def test_no_claude_pid_writes_nothing(self) -> None:
        del os.environ["CLAUDE_PID"]
        self.call.ledger(tool_line(ShellText(f"eval {shlex.quote(CALL)}")))
        self.assertFalse(self.places.sessions.exists())
        self.warn.assert_not_called()

    def test_a_carried_text_line_digests_the_variables_text(self) -> None:
        os.environ[CARRIED] = CARRIED_TEXT
        self.call.ledger(tool_line(ShellText(CHECK)))
        self.assertEqual(self.path.read_bytes(), route.line(route.digest(CARRIED_TEXT)))

    def test_a_carried_text_line_without_the_variable_digests_the_element(self) -> None:
        self.call.ledger(tool_line(ShellText(CHECK)))
        self.assertEqual(self.path.read_bytes(), route.line(route.digest(ELEMENT)))

    def test_a_ledger_that_cannot_be_written_is_warned_once_and_the_call_goes_on(self) -> None:
        for shape, pid in (("CLAUDE_PID names no process", "4194303"), ("the sessions folder is a file", PID)):
            with self.subTest(shape=shape):
                self.warn.reset_mock()
                os.environ["CLAUDE_PID"] = pid
                if pid == PID:
                    _ = self.places.sessions.write_text("", encoding="utf-8")
                self.call.ledger(tool_line(ShellText(f"eval {shlex.quote(CALL)}")))
                self.assertFalse(self.path.exists())
                self.assertEqual([warned.args[0] for warned in self.warn.call_args_list], ["no-ledger"])

    def test_the_places_are_the_session_modules_and_a_moved_root_moves_the_sessions(self) -> None:
        self.assertEqual((loaded(None).SESSIONS, loaded(None).PROC), (state.session.SESSIONS, state.session.PROC))
        self.assertEqual(loaded(self.places.sessions).SESSIONS, self.places.sessions / "claude-sessions")
