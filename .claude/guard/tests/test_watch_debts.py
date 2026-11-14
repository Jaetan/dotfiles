"""The watch-debts rule, through the dispatcher with every other rule of the kit switched off: a heavy background Bash
run owes a stall watch on its log and a workflow a token watch on its transcript folder, every call of the main thread
but Monitor, TaskStop and ToolSearch refused until that Monitor runs; an expiry or the watch's end reopens the debt
while the run lives, the run's end, TaskStop or a Stop not listing it closes it, and an OVER LIMIT owes a TaskStop. A
Monitor that is not the watcher alone on the run's target is no watch, a light or foreground run owes nothing, and an
agent's event is judged as nothing. A debt keeps the turn going once, three times over its ceiling; a write of the
switch file passes it, and a watcher that is not executable is demanded of no call.

Each case has a work folder, a home holding the machine file, a sessions folder, a proc tree holding one claude process
and a transcript of its own in the temporary folder; the task notices are written into that transcript as the harness
queues them."""

import json
import os
import shutil
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import NewType

from dispatcher.core import (AgentId, Answer, BackgroundTask, Event, EventBytes, FolderPath, HookEvent, Payload,
                             SessionId, Settings, TaskId, ToolInput, ToolName, ToolResponse, answer)
from dispatcher.model import ReadSettings
from dispatcher.notices import TaskStatus
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import Directory, Environment, VarName
from dispatcher.writes import FilePath
from rules.watch_debts import judge
from state.session import FieldName, Pid, Places, RecordId, Session, StartTicks, process_key
from tests.test_declarations import NONE_DECLARED
from tests.test_session_state import proc_tree, stat_line

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = "watch_debts.py"
NoticeStatus = NewType("NoticeStatus", str)
EventText = NewType("EventText", str)
Program = NewType("Program", str)

PID = Pid(10)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")
BASH, MONITOR, WORKFLOW, TASK_STOP, TOOL_SEARCH, READ, WRITE, EDIT, MULTI_EDIT = (ToolName(name) for name in (
    "Bash", "Monitor", "Workflow", "TaskStop", "ToolSearch", "Read", "Write", "Edit", "MultiEdit"))
B1, B2, M1, M2, W1, W2 = (TaskId(name) for name in ("b1", "b2", "m1", "m2", "w1", "w2"))
EXPIRED = EventText("[Monitor expired after 30 minutes]")
OVER = EventText("OVER LIMIT: 2,000,001 new tokens, ceiling 2,000,000; stop the workflow")
STALL = "~/.local/bin/log-stall-watch"
TOKEN = "~/.local/bin/workflow-token-watch"
STAND_IN = "#!/usr/bin/python3\nimport sys\n"  # a watcher on disk, which the call model reads as the script it runs
RUNNING = TaskStatus("running")
TURN = "the turn does not end yet while a live run owes its watch"


class Case(unittest.TestCase):
    """A case with a work folder, a switch folder turning off every rule but this one, a proc tree holding PID, a home
    holding the machine file and the two watchers, a log path in the work folder, a workflow folder and a transcript."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.off = self.root / "off"
        self.off.mkdir()
        for rule in (KIT / "rules").glob("[!_]*.py"):
            if rule.name != OWN:
                (self.off / rule.stem.replace("_", "-")).touch()
        self.places = Places(self.root / "sessions", proc_tree(self.root, BOOT, {PID: stat_line(PID, START)}))
        self.home = self.root / "home"
        (self.home / ".config" / "claude-guard").mkdir(parents=True)
        _ = (self.home / ".config" / "claude-guard" / "machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
        for script in (self.home / ".local" / "bin" / "log-stall-watch", self.home / ".local" / "bin" /
                       "workflow-token-watch", self.root / "bin" / "log-stall-watch"):
            script.parent.mkdir(parents=True, exist_ok=True)
            _ = script.write_text(STAND_IN, encoding="utf-8")
            script.chmod(0o755)
        self.repo = self.root / "repo"
        (self.repo / "logs").mkdir(parents=True)
        self.log = self.repo / "logs" / "run.log"
        self.folder = self.root / "workflows" / "wf_1"
        self.folder.mkdir(parents=True)
        self.transcript: Path | None = self.root / "transcript.jsonl"
        _ = self.transcript.write_text('{"type": "user"}\n', encoding="utf-8")
        self.env: Environment = {VarName("HOME"): ShellText(str(self.home)),
                                 VarName("CLAUDE_PID"): ShellText(str(PID))}

    def payload(self, name: HookEvent, tool: ToolName | None, command: ShellText | None = None,
                task: TaskId | None = None, shell: TaskId | None = None, path: Path | None = None) -> Payload:
        """The payload of an event ``name`` of a ``tool`` call run from the work folder, of no call where None."""
        given: ToolInput = {}
        if command is not None:
            given["command"] = command
        if task is not None:
            given["task_id"] = task
        if shell is not None:
            given["shell_id"] = shell
        if path is not None:
            given["file_path"] = FilePath(str(path))
        payload: Payload = {"hook_event_name": name, "session_id": SessionId("s"), "cwd": Directory(str(self.repo))}
        if tool is not None:
            payload["tool_name"], payload["tool_input"] = tool, given
        if self.transcript is not None:
            payload["transcript_path"] = FilePath(str(self.transcript))
        return payload

    def answered(self, name: HookEvent, tool: ToolName | None, command: ShellText | None = None,
                 response: ToolResponse | None = None, task: TaskId | None = None,
                 agent: AgentId | None = None, shell: TaskId | None = None, path: Path | None = None,
                 tasks: Sequence[BackgroundTask] | None = None) -> Answer:
        """The dispatcher's answer to an event ``name`` of a ``tool`` call run from the work folder, of a Stop listing
        ``tasks`` in flight where given."""
        payload = self.payload(name, tool, command, task, shell, path)
        if tasks is not None:
            payload["background_tasks"] = list(tasks)
        if response is not None:
            payload["tool_response"] = response
        if agent is not None:
            payload["agent_id"] = agent
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), self.env), self.home, self.places)
        return answer(EventBytes(json.dumps(payload).encode()), settings)

    def post(self, tool: ToolName, command: ShellText | None = None, response: ToolResponse | None = None,
             task: TaskId | None = None, agent: AgentId | None = None, shell: TaskId | None = None) -> None:
        """Send the PostToolUse of a call, which records and refuses nothing."""
        got = self.answered("PostToolUse", tool, command, response, task, agent, shell)
        self.assertEqual((got.status, got.stderr), (0, ""))

    def refusal(self, tool: ToolName = READ, command: ShellText | None = None,
                agent: AgentId | None = None, path: Path | None = None) -> Prose | None:
        """The refusal of a PreToolUse of a ``tool`` call, None where it passes with nothing."""
        return self.refused(self.answered("PreToolUse", tool, command, agent=agent, path=path))

    def stop(self, tasks: Sequence[BackgroundTask] | None = None) -> Prose | None:
        """The refusal of a Stop listing ``tasks`` in flight, of a Stop carrying no list where None; None where the turn
        ends."""
        return self.refused(self.answered("Stop", None, tasks=tasks))

    def noted(self, tool: ToolName = READ) -> Prose:
        """The note beside a PreToolUse of a ``tool`` call that passes with one."""
        got = self.answered("PreToolUse", tool)
        self.assertEqual((got.status, got.stderr), (0, ""))
        said = json.loads(got.stdout)["hookSpecificOutput"]
        self.assertEqual(said["hookEventName"], "PreToolUse")
        return said["additionalContext"]

    def refused(self, got: Answer) -> Prose | None:
        """The refusal ``got`` holds, None where it passes with nothing."""
        if got.status == 0:
            self.assertEqual(got.stderr, "")
            return None
        self.assertEqual(got.status, 2)
        self.assertIn("guard watch-debts: ", got.stderr)
        return got.stderr

    def launch(self, task: TaskId = B1, command: ShellText | None = None, agent: AgentId | None = None) -> None:
        """Record a background Bash run of ``command``, pytest writing the case's log where None."""
        self.post(BASH, command or ShellText(f"pytest tests/ > {self.log} 2>&1"),
                  {"backgroundTaskId": task}, agent=agent)

    def workflow(self, task: TaskId = W1, folder: Path | None = None) -> None:
        """Record a workflow launch on ``folder``, the case's workflow folder where None."""
        self.post(WORKFLOW, response={"taskId": task, "transcriptDir": FolderPath(str(folder or self.folder))})

    def watch(self, task: TaskId, command: ShellText, agent: AgentId | None = None) -> None:
        """Record a Monitor call of ``command`` under ``task``."""
        self.post(MONITOR, command, {"taskId": task}, agent=agent)

    def notice(self, task: TaskId, status: NoticeStatus | None = None, event: EventText | None = None) -> None:
        """Queue a task notice in the transcript as the harness writes one."""
        head = f"<task-notification>\n<task-id>{task}</task-id>\n" + (f"<status>{status}</status>\n" if status else "")
        body = "<summary>s</summary>\n" + (f"<event>{event}</event>\n" if event else "") + "</task-notification>"
        line = json.dumps({"type": "queue-operation", "operation": "enqueue", "content": head + body})
        with (self.transcript or self.root / "none").open("a", encoding="utf-8") as out:
            _ = out.write(line + "\n")

    def owes_stall(self, task: TaskId, log: Path | None = None) -> Prose:
        """The line naming a heavy run that owes a stall watch on ``log``, the case's log where None."""
        target = log or self.log
        return Prose(f"the heavy background run {task} on {target} has no live stall watch: run the Monitor "
                     f"`{STALL} {target} 900`")

    def owes_token(self, task: TaskId, folder: Path | None = None) -> Prose:
        """The line naming a workflow that owes a token watch on ``folder``, the case's folder where None."""
        target = folder or self.folder
        return Prose(f"the workflow {task} on {target} has no live token watch: run the Monitor `{TOKEN} {target}`")

    def stalled(self) -> ShellText:
        """The stall watch of the case's log."""
        return ShellText(f"{STALL} {self.log} 900")

    def tokened(self, folder: Path | None = None) -> ShellText:
        """The token watch of ``folder``, the case's folder where None."""
        return ShellText(f"{TOKEN} {folder or self.folder}")

    def unexecutable(self, program: Program) -> None:
        """Make the watcher ``program`` under the case's home one that cannot run."""
        (self.home / ".local" / "bin" / program).chmod(0o644)


def flying(*tasks: TaskId) -> list[BackgroundTask]:
    """A Stop's list of ``tasks``, each running."""
    return [{"id": task, "status": RUNNING} for task in tasks]


class Stall(Case):
    def test_a_heavy_background_run_owes_a_stall_watch_on_its_log(self) -> None:
        self.launch()
        refusal = self.refusal()
        assert refusal is not None
        self.assertIn(self.owes_stall(B1), refusal)
        self.assertIn("Every call but Monitor, TaskStop and ToolSearch is refused", refusal)

    def test_the_watch_pays_it_and_an_expiry_reopens_it_while_the_run_lives(self) -> None:
        self.launch()
        self.watch(M1, self.stalled())
        self.assertIsNone(self.refusal())
        self.notice(M1, event=EXPIRED)
        self.assertIn(self.owes_stall(B1), self.refusal() or "")

    def test_the_end_of_the_watch_reopens_it(self) -> None:
        self.launch()
        self.watch(M1, self.stalled())
        self.notice(M1, NoticeStatus("completed"))
        self.assertIn(self.owes_stall(B1), self.refusal() or "")

    def test_the_end_of_the_run_closes_it(self) -> None:
        self.launch()
        self.notice(B1, NoticeStatus("completed"))
        self.assertIsNone(self.refusal())

    def test_taskstop_closes_it_by_its_task_or_shell_id(self) -> None:
        self.launch()
        self.launch(B2)
        self.post(TASK_STOP, task=B1)
        self.post(TASK_STOP, shell=B2)
        self.assertIsNone(self.refusal())

    def test_the_first_heavy_command_names_the_log(self) -> None:
        other = self.repo / "logs" / "other.log"
        self.launch(command=ShellText(f"pytest a > {self.log} 2>&1; pytest b > {other} 2>&1"))
        self.assertIn(self.owes_stall(B1), self.refusal() or "")

    def test_a_target_is_quoted_in_the_monitor_named(self) -> None:
        log = self.repo / "logs" / "run log.log"
        self.launch(command=ShellText(f"pytest tests/ > '{log}' 2>&1"))
        self.assertIn(f"`{STALL} '{log}' 900`", self.refusal() or "")
        self.watch(M1, ShellText(f"{STALL} '{log}' 900"))
        self.assertIsNone(self.refusal())

    def test_a_run_whose_output_ends_in_no_file_owes_until_it_ends(self) -> None:
        self.launch(command=ShellText("pytest tests/"))
        self.watch(M1, self.stalled())
        self.assertIn("the heavy background run b1 has no target to watch: it owes until it ends, or stop it with "
                      "TaskStop, task_id b1", self.refusal() or "")
        self.notice(B1, NoticeStatus("failed"))
        self.assertIsNone(self.refusal())

    def test_a_light_or_foreground_run_owes_nothing(self) -> None:
        self.post(BASH, ShellText(f"ls > {self.log}"), {"backgroundTaskId": B1})
        self.post(BASH, ShellText(f"pytest tests/ > {self.log} 2>&1"), {})
        self.assertIsNone(self.refusal())

    def test_the_log_is_placed_and_resolved_where_the_command_runs(self) -> None:
        (self.root / "link").symlink_to(self.repo / "logs")
        self.launch(command=ShellText("pytest tests/ > ../link/run.log 2>&1"))
        self.assertIn(self.owes_stall(B1), self.refusal() or "")
        self.watch(M1, ShellText(f"{STALL} {self.root / 'link' / 'run.log'} 900"))
        self.assertIsNone(self.refusal())

    def test_a_watch_started_before_its_run_pays_it(self) -> None:
        self.watch(M1, self.stalled())
        self.launch()
        self.assertIsNone(self.refusal())

    def test_an_end_read_at_the_launch_is_never_recorded(self) -> None:
        self.notice(B1, NoticeStatus("failed"))
        self.launch()
        self.assertIsNone(self.refusal())


class Watches(Case):
    def test_what_is_no_stall_watch(self) -> None:
        self.launch()
        log = self.log
        for at, command in enumerate((
                f"{STALL} {self.root / 'other.log'} 900", f"{STALL} {log} 900 5 6", f"{STALL} {log}",
                f"{STALL} {log} 0", f"{STALL} {log} 1801", f"{STALL} {log} 9x", f"{STALL} {log} 900 0",
                f"{STALL} {log} 900 61", f"{STALL} {log} 900 1e1", f"{STALL} {os.path.relpath(log)} 900",
                f"{STALL} {log} 900 > /dev/null", f"{STALL} {log} 900; true", f"{self.root}/bin/log-stall-watch {log} 900",
                f"{TOKEN} {log}", f"{STALL} {log} $SILENCE", f"nice {STALL} {log} 900", f"{STALL} {log} 900 ''",
                f"{STALL}", f"{STALL} {log} 900 $INTERVAL")):
            with self.subTest(command=command):
                self.watch(TaskId(f"m{at}"), ShellText(command))
                self.assertIn(self.owes_stall(B1), self.refusal() or "")
        self.watch(M1, self.stalled())
        self.assertIsNone(self.refusal())

    def test_what_is_a_stall_watch(self) -> None:
        self.launch()
        log = self.log
        for at, command in enumerate((
                f"$HOME/.local/bin/log-stall-watch {log} 1", f"{STALL} {log} 1800", f"{STALL} {log} 900 60",
                f"{STALL} {log} 900 0.5", f"{self.home}/.local/bin/log-stall-watch {log} 900")):
            with self.subTest(command=command):
                self.watch(TaskId(f"m{at}"), ShellText(command))
                self.assertIsNone(self.refusal())
                self.post(TASK_STOP, task=TaskId(f"m{at}"))
                self.assertIn(self.owes_stall(B1), self.refusal() or "")

    def test_what_is_a_token_watch(self) -> None:
        self.workflow()
        folder = self.folder
        for at, command in enumerate((f"{TOKEN} {folder}", f"{TOKEN} {folder} 1", f"{TOKEN} {folder} 2000000",
                                      f"{TOKEN} {folder} 2000000 60")):
            with self.subTest(command=command):
                self.watch(TaskId(f"m{at}"), ShellText(command))
                self.assertIsNone(self.refusal())
                self.post(TASK_STOP, task=TaskId(f"m{at}"))
                self.assertIn(self.owes_token(W1), self.refusal() or "")

    def test_what_is_no_token_watch(self) -> None:
        self.workflow()
        folder = self.folder
        for at, command in enumerate((f"{TOKEN} {folder} 0", f"{TOKEN} {folder} 2000001", f"{TOKEN} {folder} 5 0",
                                      f"{TOKEN} {folder} 5 61", f"{TOKEN} {folder} 5 6 7", f"{STALL} {folder} 900",
                                      f"{TOKEN} {self.root}")):
            with self.subTest(command=command):
                self.watch(TaskId(f"m{at}"), ShellText(command))
                self.assertIn(self.owes_token(W1), self.refusal() or "")


class Token(Case):
    def test_a_workflow_owes_a_token_watch_on_its_folder(self) -> None:
        self.workflow()
        self.assertIn(self.owes_token(W1), self.refusal() or "")
        self.watch(M1, self.tokened())
        self.assertIsNone(self.refusal())
        self.notice(M1, event=EXPIRED)
        self.assertIn(self.owes_token(W1), self.refusal() or "")
        self.notice(W1, NoticeStatus("completed"))
        self.assertIsNone(self.refusal())

    def test_its_folder_is_resolved(self) -> None:
        (self.root / "link").symlink_to(self.folder)
        self.workflow(folder=self.root / "link")
        self.assertIn(self.owes_token(W1), self.refusal() or "")
        self.watch(M1, self.tokened(self.root / "link"))
        self.assertIsNone(self.refusal())

    def test_an_over_limit_owes_a_taskstop(self) -> None:
        self.workflow()
        self.watch(M1, self.tokened())
        self.notice(M1, NoticeStatus("completed"), OVER)
        over = f"the workflow w1 on {self.folder} is over its ceiling: stop it with TaskStop, task_id w1"
        self.assertIn(over, self.refusal() or "")
        self.watch(M2, self.tokened())
        self.assertIn(over, self.refusal() or "")
        self.post(TASK_STOP, task=W1)
        self.assertIsNone(self.refusal())

    def test_an_over_limit_marks_only_the_runs_on_its_target(self) -> None:
        other = self.root / "workflows" / "wf_2"
        other.mkdir()
        self.workflow()
        self.workflow(W2, other)
        self.watch(M1, self.tokened())
        self.watch(M2, self.tokened(other))
        self.notice(M1, event=OVER)
        refusal = self.refusal() or ""
        self.assertIn("the workflow w1 on", refusal)
        self.assertNotIn("the workflow w2", refusal)

    def test_a_workflow_with_no_folder_owes_until_it_ends(self) -> None:
        self.post(WORKFLOW, response={"taskId": W1})
        self.assertIn("the workflow w1 has no target to watch", self.refusal() or "")
        self.notice(W1, NoticeStatus("killed"))
        self.assertIsNone(self.refusal())


class Thread(Case):
    def test_monitor_taskstop_and_toolsearch_pass_while_a_debt_stands(self) -> None:
        self.launch()
        self.assertIsNone(self.refusal(MONITOR, self.stalled()))
        self.assertIsNone(self.refusal(TASK_STOP))
        self.assertIsNone(self.refusal(TOOL_SEARCH))
        self.assertIn(self.owes_stall(B1), self.refusal(BASH, ShellText("true")) or "")

    def test_an_agents_event_is_judged_as_nothing(self) -> None:
        agent = AgentId("a1")
        self.launch(B2, agent=agent)
        self.assertIsNone(self.refusal())
        self.launch()
        self.assertIsNone(self.refusal(agent=agent))
        self.watch(M1, self.stalled(), agent=agent)
        self.post(TASK_STOP, task=B1, agent=agent)
        self.assertIn(self.owes_stall(B1), self.refusal() or "")

    def test_the_offset_read_is_kept_beside_the_transcript(self) -> None:
        self.notice(B2, NoticeStatus("completed"))
        self.assertIsNone(self.refusal())
        session = Session(self.places, process_key(self.env, self.places.proc), None)
        assert self.transcript is not None
        kept = [record.fields for record in session.records("notice")]
        self.assertEqual(kept, [{FieldName("transcript"): str(self.transcript),
                                 FieldName("offset"): str(self.transcript.stat().st_size)}])

    def test_a_new_transcript_is_read_from_its_start(self) -> None:
        assert self.transcript is not None
        _ = self.transcript.write_text("{}\n" * 2048, encoding="utf-8")
        self.launch()
        self.assertIsNotNone(self.refusal())
        self.transcript = self.root / "cleared.jsonl"
        self.notice(B1, NoticeStatus("completed"))
        with self.transcript.open("a", encoding="utf-8") as out:
            _ = out.write("{}\n" * 4096)
        self.assertIsNone(self.refusal())

    def test_a_notice_is_read_once(self) -> None:
        self.notice(M1, event=EXPIRED)
        self.assertIsNone(self.refusal())
        self.launch()
        self.watch(M1, self.stalled())
        self.assertIsNone(self.refusal())

    def test_records_of_another_rule_are_left_alone(self) -> None:
        session = Session(self.places, process_key(self.env, self.places.proc), None)
        session.record("run", RecordId("x1"), {})
        session.record("watch", RecordId("x2"), {FieldName("target"): str(self.log)})
        self.assertIsNone(self.refusal())
        self.launch()
        self.assertIn(self.owes_stall(B1), self.refusal() or "")

    def test_a_missing_transcript_gives_no_notice(self) -> None:
        self.transcript = self.root / "missing.jsonl"
        self.assertIsNone(self.refusal())
        self.launch()
        self.assertIn(self.owes_stall(B1), self.refusal() or "")

    def test_an_event_naming_no_transcript_is_judged_as_nothing(self) -> None:
        self.launch()
        named, self.transcript = self.transcript, None
        self.assertIsNone(self.refusal())
        self.launch(B2)
        self.transcript = named
        refusal = self.refusal() or ""
        self.assertIn(self.owes_stall(B1), refusal)
        self.assertNotIn("run b2", refusal)


class Turn(Case):
    def test_a_debt_keeps_the_turn_going_once_then_lets_it_end(self) -> None:
        self.launch()
        refusal = self.stop()
        assert refusal is not None
        self.assertIn(TURN, refusal)
        self.assertIn(self.owes_stall(B1), refusal)
        self.assertIsNone(self.stop())
        self.assertIn(self.owes_stall(B1), self.refusal() or "")
        self.assertIsNone(self.stop())

    def test_a_run_over_its_ceiling_keeps_it_going_three_times(self) -> None:
        self.workflow()
        self.watch(M1, self.tokened())
        self.notice(M1, event=OVER)
        for at in range(3):
            with self.subTest(at=at):
                self.assertIn("the workflow w1 on", self.stop() or "")
        self.assertIsNone(self.stop())

    def test_the_count_starts_again_once_the_run_owes_nothing(self) -> None:
        self.launch()
        self.assertIsNotNone(self.stop())
        self.assertIsNone(self.stop())
        self.watch(M1, self.stalled())
        self.assertIsNone(self.refusal())
        self.notice(M1, event=EXPIRED)
        self.assertIn(self.owes_stall(B1), self.stop() or "")

    def test_a_run_the_stop_does_not_list_has_ended(self) -> None:
        self.launch()
        self.launch(B2)
        refusal = self.stop(flying(B2)) or ""
        self.assertIn(self.owes_stall(B2), refusal)
        self.assertNotIn("run b1", refusal)
        self.assertNotIn("run b1", self.refusal() or "")

    def test_a_watch_the_stop_does_not_list_has_ended(self) -> None:
        self.launch()
        self.watch(M1, self.stalled())
        self.assertIn(self.owes_stall(B1), self.stop(flying(B1)) or "")

    def test_a_stop_carrying_no_list_ends_nothing(self) -> None:
        self.launch()
        self.watch(M1, self.stalled())
        self.assertIsNone(self.stop())
        self.post(TASK_STOP, task=M1)
        self.assertIn(self.owes_stall(B1), self.stop() or "")


class Escapes(Case):
    def test_the_switch_file_is_written_through_an_open_debt(self) -> None:
        self.launch()
        (self.root / "switches").symlink_to(self.off)
        for tool in (WRITE, EDIT, MULTI_EDIT):
            for path in (self.off / "watch-debts", self.off / "all", self.root / "switches" / "watch-debts"):
                with self.subTest(tool=tool, path=path):
                    self.assertIsNone(self.refusal(tool, path=path))
        for tool, other in ((WRITE, self.off / "job-count"), (WRITE, self.root / "watch-debts"),
                            (READ, self.off / "watch-debts"), (EDIT, None)):
            with self.subTest(tool=tool, path=other):
                self.assertIn(self.owes_stall(B1), self.refusal(tool, path=other) or "")

    def test_an_event_built_without_the_dispatcher_names_no_switch_file(self) -> None:
        self.launch()
        payload = self.payload("PreToolUse", WRITE, path=self.off / "watch-debts")
        verdict = judge(Event(payload, ReadSettings(READER, Seconds(5.0), self.env), self.home, self.places))
        self.assertEqual(None if verdict is None else verdict.kind, "refuse")

    def test_a_watcher_that_is_not_executable_is_demanded_of_no_call(self) -> None:
        self.launch()
        self.unexecutable(Program("log-stall-watch"))
        self.assertIn(f"the heavy background run b1 on {self.log} has no live stall watch, and none is demanded while "
                      f"`{STALL}` is not executable", self.noted())
        self.assertIsNone(self.stop())
        self.workflow()
        self.assertIn(self.owes_token(W1), self.refusal() or "")

    def test_a_run_over_its_ceiling_is_refused_whatever_its_watcher(self) -> None:
        self.workflow()
        self.watch(M1, self.tokened())
        self.notice(M1, event=OVER)
        self.unexecutable(Program("workflow-token-watch"))
        over = f"the workflow w1 on {self.folder} is over its ceiling"
        self.assertIn(over, self.refusal() or "")
        self.assertIn(over, self.stop() or "")

    def test_a_run_with_no_target_is_refused_whatever_its_watcher(self) -> None:
        self.launch(command=ShellText("pytest tests/"))
        self.unexecutable(Program("log-stall-watch"))
        self.assertIn("the heavy background run b1 has no target to watch", self.refusal() or "")


if __name__ == "__main__":
    _ = unittest.main()
