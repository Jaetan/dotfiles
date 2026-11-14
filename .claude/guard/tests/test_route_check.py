"""The route-check rule, through the dispatcher with every other rule switched off: a Bash or Monitor call of any agent
is refused while CLAUDE_CODE_SHELL in the hook's environment is not an absolute path named `bash` resolving to
claude-call's wrapper, an executable file, or while CLAUDE_CODE_SHELL_PREFIX or BASH_ENV is set, empty included; each
line names the variable, its value and what it breaks, and the refusal names the wrapper and the ways out. A call it
passes keeps the ledger's end, and a call whose line is not in the ledger past that end at PostToolUse or
PostToolUseFailure sets the process's flag and is refused, as is every Bash and Monitor call after it, /clear or not.

Every case replaces route.WRAPPER by an executable fixture file named bash in a folder of its own, keeps the state in
its own folder under a proc tree of its own naming one claude process, and replaces route's pause and clock, so no case
sleeps: the clock passes route.BOUND at its second reading."""

import itertools
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import NewType
from unittest import mock

from dispatcher.core import (AgentId, Answer, Event, EventBytes, FolderPath, HookEvent, Payload, SessionId, Settings,
                             ToolName, answer)
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import Environment, VarName
from rules.route_check import ToolUseId, judge
from state import route
from state.session import Pid, RecordId, Session, StartTicks, process_key
from state.session import Places as StatePlaces
from tests.test_declarations import declaring
from tests.test_session_state import BOOT, claude_pid, proc_tree, stat_line

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = "route_check"
MARK = Prose("guard route-check: this call's shell has no claude-call route, and a call that never reaches the wrapper ")
PASSED = Answer(0, Prose(""), Prose(""))
SHELL = VarName("CLAUDE_CODE_SHELL")
PREFIX = VarName("CLAUDE_CODE_SHELL_PREFIX")
BASH_ENV = VarName("BASH_ENV")
NEVER = Prose("and the wrapper is never reached")
LinkName = NewType("LinkName", str)
"""The name of a symlink to the wrapper."""
PID = Pid(4242)
FIRST = ToolUseId("toolu_1")
LATER = ToolUseId("toolu_2")
TRUE = ShellText("true")
FLAGGED = Prose("guard route-check: a Bash or Monitor call of this claude process left no claude-call record, so it ran "
                "outside\nclaude-call's scope, memory cap, view and log, and every later Bash and Monitor call of the "
                "process is refused.\n\n  * the call ")


class Case(unittest.TestCase):
    """A fresh home, switch folder and wrapper per test, every rule but route-check switched off; CLAUDE_CODE_SHELL
    names the wrapper and neither other variable is set until a test says otherwise."""

    base: Path
    home: Path
    off: Path
    wrapper: Path
    places: StatePlaces
    ledger: Path
    pause: mock.MagicMock

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.home, self.off = self.base / "home", self.base / "off"
        for folder in (self.home / ".config" / "claude-guard", self.off, self.base / "route"):
            folder.mkdir(parents=True)
        _ = (self.home / ".config" / "claude-guard" / "machine.toml").write_text(declaring(), encoding="utf-8")
        for module in (KIT / "rules").glob("*.py"):
            if not module.stem.startswith("_") and module.stem != RULE:
                (self.off / module.stem.replace("_", "-")).touch()
        self.wrapper = self.base / "route" / "bash"
        _ = self.wrapper.write_text("#!/bin/sh\n", encoding="ascii")
        self.wrapper.chmod(0o755)
        _ = self.enterContext(mock.patch.object(route, "WRAPPER", self.wrapper))
        proc = proc_tree(self.base, BOOT, {PID: stat_line(PID, StartTicks(777))})
        self.places = StatePlaces(self.base / "sessions", proc)
        self.ledger = route.ledger(self.places, process_key(claude_pid(PID), proc))
        _ = self.enterContext(mock.patch.object(route, "clock", side_effect=itertools.count(0.0, route.BOUND)))
        self.pause = self.enterContext(mock.patch.object(route, "pause"))

    def tearDown(self) -> None:
        shutil.rmtree(self.base)

    def linked(self, name: LinkName) -> ShellText:
        """A symlink named ``name`` to the wrapper, in a folder of its own."""
        link = self.base / "links" / name
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(self.wrapper)
        return ShellText(str(link))

    def judged(self, variables: Environment | None = None, tool: ToolName = ToolName("Bash"),
               event: HookEvent = "PreToolUse", agent: AgentId | None = None, call: ToolUseId | None = FIRST,
               command: ShellText = TRUE, session: SessionId = SessionId("s1")) -> Answer:
        """The dispatcher's answer to ``event`` for a ``tool`` call ``call`` (none where None) of ``agent`` (the main
        thread's where None) running ``command`` in the session ``session``, the hook's environment naming the case's
        claude process and holding ``variables``, CLAUDE_CODE_SHELL naming the wrapper where None."""
        env = {VarName("HOME"): ShellText(str(self.home))} | claude_pid(PID)
        env |= {SHELL: ShellText(str(self.wrapper))} if variables is None else variables
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), home=self.home, places=self.places)
        fields = {"hook_event_name": event, "tool_name": tool, "cwd": str(self.base), "tool_input": {"command": command},
                  "session_id": session}
        if agent is not None:
            fields["agent_id"] = agent
        if call is not None:
            fields["tool_use_id"] = call
        return answer(EventBytes(json.dumps(fields).encode()), settings)

    def appended(self, text: ShellText) -> None:
        """claude-call's ledger line for the call ``text``, appended to the case's ledger."""
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("ab") as ledger:
            _ = ledger.write(route.line(route.digest(text)))

    def notices(self) -> list[RecordId]:
        """The id of every notice the case's claude process keeps, in every file."""
        state = Session(self.places, process_key(claude_pid(PID), self.places.proc), None)
        return [record.ident for record in state.records("notice")]

    def flagged(self, got: Answer, text: ShellText = TRUE, call: ToolUseId | Prose = FIRST) -> None:
        """``got`` is the flag's refusal, naming the unrecorded call ``text`` of tool_use_id ``call``."""
        self.assertEqual(got.status, 2, got.stderr)
        self.assertTrue(got.stderr.startswith(f"{FLAGGED}{text!r}, tool_use_id {call!r}, has no line in the ledger "
                                              f"{self.ledger}\n"), got.stderr)

    def refused(self, *lines: Prose, variables: Environment | None = None, tool: ToolName = ToolName("Bash"),
                agent: AgentId | None = None) -> Prose:
        """The refusal of the call, holding each of ``lines`` as a line of its own and no other; its stderr."""
        got = self.judged(variables, tool=tool, agent=agent)
        self.assertEqual(got.status, 2, got.stderr)
        self.assertTrue(got.stderr.startswith(f"{MARK}{route.WRAPPER} runs outside\nclaude-call's scope, memory cap, "
                                              "view and log.\n\n  * "), got.stderr)
        for line in lines:
            self.assertIn(f"\n  * {line}\n", got.stderr)
        self.assertEqual(got.stderr.count("\n  * "), len(lines), got.stderr)
        return got.stderr

    def shell_refused(self, shell: ShellText | None, broken: Prose) -> None:
        """A call whose CLAUDE_CODE_SHELL is ``shell`` (unset where None) refused with the one line ``broken``."""
        variables = {} if shell is None else {SHELL: shell}
        _ = self.refused(Prose(f"CLAUDE_CODE_SHELL is {shell or ''!r}, {broken}"), variables=variables)


class Passes(Case):
    """CLAUDE_CODE_SHELL naming the wrapper, or a symlink named bash to it, passes, for every tool and agent."""

    def test_the_wrapper(self) -> None:
        self.assertEqual(self.judged(), PASSED)

    def test_a_symlink_named_bash_to_it(self) -> None:
        self.assertEqual(self.judged({SHELL: self.linked(LinkName("bash"))}), PASSED)

    def test_a_wrapper_named_through_a_symlinked_folder(self) -> None:
        (self.base / "via").symlink_to(self.base / "route")
        _ = self.enterContext(mock.patch.object(route, "WRAPPER", self.base / "via" / "bash"))
        self.assertEqual(self.judged(), PASSED)

    def test_monitor_alike(self) -> None:
        self.assertEqual(self.judged(tool=ToolName("Monitor")), PASSED)

    def test_an_agents_call_alike(self) -> None:
        self.assertEqual(self.judged(agent=AgentId("a1b2c3")), PASSED)


class Shell(Case):
    """CLAUDE_CODE_SHELL gives one line, naming the first check it fails."""

    def test_unset(self) -> None:
        self.shell_refused(None, Prose(f"no path, so Claude Code picks a shell of its own, {NEVER}"))

    def test_empty(self) -> None:
        self.shell_refused(ShellText(""), Prose(f"no path, so Claude Code picks a shell of its own, {NEVER}"))

    def test_relative(self) -> None:
        relative = ShellText(os.path.relpath(self.wrapper))
        self.shell_refused(relative, Prose(f"not an absolute path, {NEVER}"))

    def test_named_bash2(self) -> None:
        self.shell_refused(self.linked(LinkName("bash2")), Prose(f"named 'bash2', not `bash`, {NEVER}"))

    def test_named_zsh(self) -> None:
        self.shell_refused(self.linked(LinkName("zsh")), Prose(f"named 'zsh', not `bash`, {NEVER}"))

    def test_the_systems_bash(self) -> None:
        system = ShellText("/usr/bin/bash")
        self.shell_refused(system, Prose(f"resolving to {os.path.realpath(system)}, not the wrapper's "
                                         f"{self.wrapper}, {NEVER}"))

    def test_a_wrapper_not_executable(self) -> None:
        self.wrapper.chmod(0o644)
        self.shell_refused(ShellText(str(self.wrapper)), Prose(f"not an executable file, so Claude Code passes it "
                                                               f"over, {NEVER}"))

    def test_a_wrapper_removed(self) -> None:
        self.wrapper.unlink()
        self.shell_refused(ShellText(str(self.wrapper)), Prose(f"not an executable file, so Claude Code passes it "
                                                               f"over, {NEVER}"))

    def test_a_directory_named_bash(self) -> None:
        self.wrapper.unlink()
        self.wrapper.mkdir()
        self.shell_refused(ShellText(str(self.wrapper)), Prose(f"not an executable file, so Claude Code passes it "
                                                               f"over, {NEVER}"))


class SecondRoutes(Case):
    """CLAUDE_CODE_SHELL_PREFIX and BASH_ENV each give a line while set at all, empty included."""

    def route_refused(self, name: VarName, value: ShellText, broken: Prose) -> None:
        """A call beside the wrapper whose ``name`` is ``value`` refused with the one line ``broken``."""
        _ = self.refused(Prose(f"{name} is {value!r}, {broken}"),
                         variables={SHELL: ShellText(str(self.wrapper)), name: value})

    def test_a_prefix(self) -> None:
        self.route_refused(PREFIX, ShellText("/usr/local/bin/wrap"), Prose(
            "so every call reaches the wrapper as the prefix's line, which it never hands to claude-call"))

    def test_an_empty_prefix(self) -> None:
        self.route_refused(PREFIX, ShellText(""), Prose(
            "so every call reaches the wrapper as the prefix's line, which it never hands to claude-call"))

    def test_bash_env(self) -> None:
        self.route_refused(BASH_ENV, ShellText("/home/u/.bashenv"), Prose(
            "so every bash the session starts runs that file first, a second route beside the wrapper"))

    def test_an_empty_bash_env(self) -> None:
        self.route_refused(BASH_ENV, ShellText(""), Prose(
            "so every bash the session starts runs that file first, a second route beside the wrapper"))

    def test_all_three_named(self) -> None:
        _ = self.refused(Prose(f"CLAUDE_CODE_SHELL is '', no path, so Claude Code picks a shell of its own, {NEVER}"),
                         Prose(f"{PREFIX} is 'p', so every call reaches the wrapper as the prefix's line, which it "
                               "never hands to claude-call"),
                         Prose(f"{BASH_ENV} is 'b', so every bash the session starts runs that file first, a second "
                               "route beside the wrapper"),
                         variables={PREFIX: ShellText("p"), BASH_ENV: ShellText("b")})


class Everyone(Case):
    """A Monitor call and an agent's call are refused alike, and the refusal names the wrapper and the ways out."""

    LINE = Prose(f"CLAUDE_CODE_SHELL is '', no path, so Claude Code picks a shell of its own, {NEVER}")

    def test_monitor(self) -> None:
        _ = self.refused(self.LINE, variables={}, tool=ToolName("Monitor"))

    def test_an_agents_call(self) -> None:
        _ = self.refused(self.LINE, variables={}, agent=AgentId("a1b2c3"))

    def test_the_refusal_names_the_ways_out(self) -> None:
        said = self.refused(self.LINE, variables={})
        self.assertTrue(said.endswith(
            f"\n\nSet CLAUDE_CODE_SHELL to {self.wrapper}, unset CLAUDE_CODE_SHELL_PREFIX and BASH_ENV, and start a "
            "new claude process,\nsince Claude Code picks its shell once per process.\nOr switch this rule off with "
            f"the file {self.off}/route-check, or the whole kit with {self.off}/all.\n"), said)

    def test_an_event_built_without_the_dispatcher_names_the_live_switches(self) -> None:
        payload = Payload(hook_event_name="PreToolUse", tool_name=ToolName("Bash"))
        verdict = judge(Event(payload, ReadSettings(READER, Seconds(5.0), claude_pid(PID)), self.home, self.places))
        self.assertIsNotNone(verdict)
        assert verdict is not None
        self.assertIn("with the file ~/.config/claude-guard/off/route-check, or the whole kit with "
                      "~/.config/claude-guard/off/all.", verdict.text)


class Reach(Case):
    """Only a Bash or Monitor call at PreToolUse, PostToolUse or PostToolUseFailure is judged, and the rule's switch
    turns it off."""

    def test_another_tool(self) -> None:
        self.assertEqual(self.judged({}, tool=ToolName("Read")), PASSED)

    def test_another_event(self) -> None:
        self.assertEqual(self.judged({}, event="PostToolBatch"), PASSED)

    def test_switched_off(self) -> None:
        (self.off / "route-check").touch()
        self.assertEqual(self.judged({}), PASSED)
        self.assertEqual(self.judged(event="PostToolUse"), PASSED)


class Recorded(Case):
    """A call claude-call recorded passes after it ran: its line in the ledger past the end kept at its PreToolUse, or
    anywhere where none was kept, and its kept end is forgotten."""

    def test_a_line_before_its_post_tool_use(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        self.assertEqual(self.notices(), [RecordId("route-offset:toolu_1")])
        self.appended(TRUE)
        self.assertEqual(self.judged(event="PostToolUse"), PASSED)
        self.assertEqual(self.notices(), [])

    def test_a_line_appended_during_the_wait(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        pause = mock.Mock(side_effect=lambda _: self.appended(TRUE))
        with mock.patch.object(route, "clock", side_effect=iter([0.0, 0.0, route.BOUND])), \
                mock.patch.object(route, "pause", pause):
            self.assertEqual(self.judged(event="PostToolUse"), PASSED)
        pause.assert_called_once_with(route.POLL)

    def test_a_post_tool_use_failure_alike(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        self.appended(TRUE)
        self.assertEqual(self.judged(event="PostToolUseFailure"), PASSED)
        self.assertEqual(self.notices(), [])

    def test_no_end_kept_reads_the_ledger_from_its_start(self) -> None:
        self.appended(TRUE)
        self.assertEqual(self.judged(event="PostToolUse"), PASSED)

    def test_a_call_with_no_tool_use_id_keeps_no_end(self) -> None:
        self.assertEqual(self.judged(call=None), PASSED)
        self.assertEqual(self.notices(), [])


class Unrecorded(Case):
    """A call with no line in the ledger after it ran sets the flag and is refused, and so is every later Bash and
    Monitor call of the process, any agent's, until a new process or the switch."""

    def test_no_line_sets_the_flag_and_refuses_every_later_call(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        self.flagged(self.judged(event="PostToolUse"))
        self.assertEqual(self.notices(), [RecordId("route-unrecorded")])
        self.flagged(self.judged(call=LATER))
        self.flagged(self.judged(call=LATER, tool=ToolName("Monitor")))
        self.flagged(self.judged(call=LATER, agent=AgentId("a1b2c3")))

    def test_a_post_tool_use_failure_alike(self) -> None:
        self.assertEqual(self.judged(), PASSED)
        self.flagged(self.judged(event="PostToolUseFailure"))
        self.flagged(self.judged(call=LATER))

    def test_an_identical_earlier_line_before_the_end_kept(self) -> None:
        self.appended(TRUE)
        self.assertEqual(self.judged(), PASSED)
        self.flagged(self.judged(event="PostToolUse"))

    def test_the_flag_outlives_a_clear(self) -> None:
        self.flagged(self.judged(event="PostToolUse"))
        self.flagged(self.judged(call=LATER, session=SessionId("s2")))

    def test_the_flag_outlives_the_agent_that_set_it(self) -> None:
        self.flagged(self.judged(event="PostToolUse", agent=AgentId("a1b2c3")))
        for agents in self.ledger.parent.glob("agent-*.json"):
            agents.unlink()
        self.flagged(self.judged(call=LATER))

    def test_the_flag_refuses_before_the_route(self) -> None:
        self.flagged(self.judged(event="PostToolUse"))
        self.flagged(self.judged({}, call=LATER))

    def test_the_flag_keeps_the_first_300_characters(self) -> None:
        self.flagged(self.judged(event="PostToolUse", command=ShellText("x" * 301)), text=ShellText("x" * 300))

    def test_a_call_with_no_tool_use_id(self) -> None:
        self.flagged(self.judged(event="PostToolUse", call=None), call=Prose(""))

    def test_the_refusal_names_the_ways_out(self) -> None:
        got = self.judged(event="PostToolUse")
        self.assertEqual(got.stderr, f"{FLAGGED}'true', tool_use_id 'toolu_1', has no line in the ledger {self.ledger}"
                         "\n\nFix the route, then start a new claude process, since this flag lives in the process's "
                         "state, which /clear does not\nclear. Or switch this rule off with the file "
                         f"{self.off}/route-check.\n")


class NoEnd(Case):
    """A call the route or the flag refuses keeps no ledger end."""

    def test_the_route_refusing(self) -> None:
        self.assertEqual(self.judged({}).status, 2)
        self.assertEqual(self.notices(), [])

    def test_the_flag_refusing(self) -> None:
        self.flagged(self.judged(event="PostToolUse"))
        self.flagged(self.judged(call=LATER))
        self.assertEqual(self.notices(), [RecordId("route-unrecorded")])


if __name__ == "__main__":
    unittest.main()
