#!/usr/bin/env python3
"""Regression cases for workflow-watch-guard.py.

Every case plays a sequence of hook events against the guard, each piped on stdin to a
fresh process that runs the guard by its path, through its own shebang, as Claude Code
does: a Workflow launch's PostToolUse, a Monitor's, a TaskStop's, the PreToolUse of the
calls between, Stop and SessionStart. A case may set up its world first (links, a HOME
spelled through `..` or with a space). Before an event, a step may act on the world
(turn the kill switch on or off, take or give back the watcher's execute bit) and
append lines to a session transcript (a task's notice as Claude Code queues it, or text
that only looks like one). Each step names the exit status the rule wants and text the
answer must hold or lack, and the answer is read where the model reads it: a refusal or
an error on stderr with nothing on stdout, a note as the one JSON shape Claude Code
takes on stdout, and nothing on stderr when the call goes through. Every case gets its
own HOME (its own kill switch and watcher, a stub with its execute bit), its own
temporary directory (its own state) and its own transcript; nothing reads a clock.

The special cases at the end leave that frame where their claim needs it: events that
are not events, state files put out of shape between two events, the state directory's
contents, the Monitor command the guard names (piped through the kit's Monitor guards
beside it under the real HOME, then back through the guard), a session id that tries to
leave the state directory, and the guard loaded in this process to see that every read
of the state comes after the state directory's lock is taken.

Usage: test_workflow_watch_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every step of every case agrees with its expectation.
"""

import contextlib
import fcntl
import importlib.util
import io
import itertools
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Literal, NamedTuple, NewType, TypedDict

# A background task's id; a session's or an agent's id; a path as JSON spells it; a
# path relative to the case's root or HOME; text in which {home}, {root} and {watcher}
# stand for the case's HOME, its root and its watcher path; a transcript line; a
# notice's event line; prose a case names itself by or expects; a case's name;
# milliseconds; a file descriptor and a lock operation as fcntl.flock takes them; a
# state file's JSON text, or a pattern of it; the hook's exit status.
TaskId = NewType("TaskId", str)
SessionId = NewType("SessionId", str)
AgentId = NewType("AgentId", str)
PathText = NewType("PathText", str)
RelDir = NewType("RelDir", str)
Template = NewType("Template", str)
Line = NewType("Line", str)
EventLine = NewType("EventLine", str)
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
Millis = NewType("Millis", int)
FileDescriptor = NewType("FileDescriptor", int)
LockOperation = NewType("LockOperation", int)
StateText = NewType("StateText", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type HookEvent = Literal["PreToolUse", "PostToolUse", "Stop", "SessionStart", "Notification"]
type Tool = Literal["Workflow", "Monitor", "TaskStop", "ToolSearch", "Bash", "Read", "Write", "Edit", "Agent"]
type Source = Literal["startup", "resume", "clear", "compact"]
type Status = Literal["completed", "failed", "killed", "stopped", "running"]
type Operation = Literal["enqueue", "remove"]
type QueueType = Literal["queue-operation", "attachment"]

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "workflow-watch-guard.py" if GIVEN.is_dir() else GIVEN
SIBLINGS = GUARD.parent
BASE = Path(tempfile.mkdtemp(prefix="workflow-watch-guard-test-")).resolve()
SESSION = SessionId("session-under-test")
WATCHER_TAIL = ".local/bin/workflow-token-watch"
KILL_SWITCH_TAIL = ".claude/workflow-watch-guard.off"
TIMEOUT = Millis(1_800_000)
RUN_A, RUN_B, RUN_A2 = TaskId("wrunaaaaa"), TaskId("wrunbbbbb"), TaskId("wrunagain")
WATCH_1, WATCH_2 = TaskId("bwatch111"), TaskId("bwatch222")
DIR_A = RelDir("projects/s/subagents/workflows/wf_aaaa")
DIR_B = RelDir("projects/s/subagents/workflows/wf_bbbb")
DIR_C = RelDir("projects/s/subagents/workflows/wf_cccc")


class ToolInput(TypedDict, total=False):
    command: Template
    description: Prose
    timeout_ms: Millis
    file_path: Template
    content: Prose
    prompt: Prose
    script: Prose
    query: Prose
    task_id: TaskId
    shell_id: TaskId


class Launched(TypedDict, total=False):
    status: Literal["async_launched", "failed"]
    taskId: TaskId | Millis
    transcriptDir: Template


class MonitorStarted(TypedDict, total=False):
    taskId: TaskId
    timeoutMs: Millis
    persistent: bool


class Stopped(TypedDict, total=False):
    message: Prose
    task_id: TaskId


class BackgroundTask(TypedDict, total=False):
    id: TaskId
    type: Literal["workflow", "shell"]
    status: Status
    command: Template


class Event(TypedDict, total=False):
    hook_event_name: HookEvent
    session_id: SessionId
    transcript_path: PathText
    tool_name: Tool
    tool_input: ToolInput | Template
    tool_response: Launched | MonitorStarted | Stopped | Template
    agent_id: AgentId
    agent_type: Literal["workflow-subagent"]
    source: Source
    background_tasks: list[BackgroundTask | Template]
    stop_hook_active: bool


class World(NamedTuple):
    """One case's root, HOME (as spelled), temporary directory and transcript."""

    root: Path
    home: Path
    tmp: Path
    transcript: Path


type Act = Callable[[World], None]


class Step(NamedTuple):
    """One event; what is done and appended before it; the answer wanted; text it holds and lacks."""

    event: Event
    want: ExitStatus
    says: Template = Template("")
    lacks: Template = Template("")
    lines: tuple[Line, ...] = ()
    transcript: Template = Template("")  # another transcript the lines go to and the event names
    act: Act | None = None


class Case(NamedTuple):
    id: CaseId
    what: Prose
    steps: list[Step]
    setup: Act | None = None
    home: RelDir = RelDir("home")  # HOME relative to the case's root, as spelled


class Answer(NamedTuple):
    status: ExitStatus
    stdout: Prose
    stderr: Prose


class Verdict(NamedTuple):
    """A case, what it checks, and a line per step that disagrees."""

    id: CaseId
    what: Prose
    wrong: list[Prose]


class Lock(NamedTuple):
    """A flock the guard took (the path its descriptor names, and the operation), or a read of the state."""

    path: Path
    operation: LockOperation | Literal["read"]


class Bend(NamedTuple):
    """A state file put out of shape: a pattern of its JSON text and what replaces it."""

    pattern: StateText
    replacement: StateText


# Acts on the world -------------------------------------------------------------

def kill_switch_on(world: World) -> None:
    (world.home / KILL_SWITCH_TAIL).parent.mkdir(parents=True, exist_ok=True)
    (world.home / KILL_SWITCH_TAIL).write_text("")


def kill_switch_off(world: World) -> None:
    (world.home / KILL_SWITCH_TAIL).unlink()


def watcher_not_executable(world: World) -> None:
    (world.home / WATCHER_TAIL).chmod(0o644)


def watcher_executable(world: World) -> None:
    (world.home / WATCHER_TAIL).chmod(0o755)


def linked_paths(world: World) -> None:
    """The watcher reached through a symbolic link, and the runs' directories through a linked parent."""
    real = world.home / "real-bin" / "workflow-token-watch"
    real.parent.mkdir()
    (world.home / WATCHER_TAIL).replace(real)
    (world.home / WATCHER_TAIL).symlink_to(real)
    (world.home / "projects").mkdir(exist_ok=True)
    (world.home / "alias").symlink_to(world.home / "projects")


def projects_through_a_space(world: World) -> None:
    """The runs' directories, plainly spelled, reached through a link to a path with a space."""
    target = world.root / "with space" / "projects"
    target.mkdir(parents=True)
    (world.home / "projects").symlink_to(target)


# Events ------------------------------------------------------------------------

def under_home(directory: RelDir) -> Template:
    return Template("{home}/" + directory)


def launch(task: TaskId, directory: RelDir) -> Event:
    """A Workflow launch's PostToolUse."""
    return Event(hook_event_name="PostToolUse", tool_name="Workflow", tool_input=ToolInput(script=Prose("x")),
                 tool_response=Launched(status="async_launched", taskId=task, transcriptDir=under_home(directory)))


def launch_reply(reply: Launched | Template) -> Event:
    return Event(hook_event_name="PostToolUse", tool_name="Workflow", tool_input=ToolInput(script=Prose("x")),
                 tool_response=reply)


def monitor(task: TaskId | None, command: Template) -> Event:
    """A Monitor's PostToolUse; a task of None leaves the id out of the result."""
    started = MonitorStarted(timeoutMs=TIMEOUT, persistent=False)
    if task is not None:
        started["taskId"] = task
    return Event(hook_event_name="PostToolUse", tool_name="Monitor",
                 tool_input=ToolInput(command=command, description=Prose("watch"), timeout_ms=TIMEOUT),
                 tool_response=started)


def watch_command(directory: RelDir, *rest: Template) -> Template:
    return Template(" ".join([Template("{watcher}"), under_home(directory), *rest]))


def watch(task: TaskId, directory: RelDir, *rest: Template) -> Event:
    return monitor(task, watch_command(directory, *rest))


def stop(task: TaskId) -> Event:
    return Event(hook_event_name="PostToolUse", tool_name="TaskStop", tool_input=ToolInput(task_id=task),
                 tool_response=Stopped(message=Prose(f"Successfully stopped task: {task}"), task_id=task))


def stop_by(given: ToolInput, response: Stopped) -> Event:
    return Event(hook_event_name="PostToolUse", tool_name="TaskStop", tool_input=given, tool_response=response)


def call(tool: Tool, tool_input: ToolInput | None = None, agent: AgentId | None = None) -> Event:
    event = Event(hook_event_name="PreToolUse", tool_name=tool,
                  tool_input=tool_input or ToolInput(command=Template("ls")))
    if agent:
        event["agent_id"], event["agent_type"] = agent, "workflow-subagent"
    return event


def listed(task: TaskId, status: Status = "running") -> BackgroundTask:
    return BackgroundTask(id=task, type="workflow", status=status)


def turn_end(tasks: list[BackgroundTask | Template] | None, active: bool = False) -> Event:
    event = Event(hook_event_name="Stop", stop_hook_active=active)
    if tasks is not None:
        event["background_tasks"] = tasks
    return event


def session_start(source: Source) -> Event:
    return Event(hook_event_name="SessionStart", source=source)


# Transcript lines --------------------------------------------------------------

def notice_text(task: TaskId | None, status: Status | None = None, event: EventLine | None = None,
                result: Template = Template("")) -> Template:
    parts = [f"<task-id>{task}</task-id>"] if task else []
    parts.append("<tool-use-id>toolu_x</tool-use-id>")
    if status:
        parts.append(f"<status>{status}</status>")
    parts.append("<summary>a task</summary>")
    if result:
        parts.append(f"<result>{result}</result>")
    if event:
        parts.append(f"<event>{event}</event>")
    return Template("<task-notification>\n" + "\n".join(parts) + "\n</task-notification>")


def queued(text: Template, operation: Operation = "enqueue", kind: QueueType = "queue-operation") -> Line:
    return Line(json.dumps({"type": kind, "operation": operation, "timestamp": "2026-10-03T00:00:00Z",
                            "sessionId": SESSION, "content": text}))


def notice(task: TaskId, status: Status | None = None, event: EventLine | None = None,
           result: Template = Template("")) -> Line:
    return queued(notice_text(task, status, event, result))


def user_says(text: Template) -> Line:
    return Line(json.dumps({"type": "user", "message": {"role": "user", "content": text}}))


def tool_result(text: Template) -> Line:
    return Line(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_y", "content": text}]}}))


PARTIAL = Line("\x00PARTIAL")  # what precedes it is written without its newline
COMPLETE = Line("\x00COMPLETE")  # the newline that ends the line written last
REPLACE = Line("\x00REPLACE")  # the transcript is emptied, as a new file in its place would be
FILLER = Line(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 400}]}}))


# Cases -------------------------------------------------------------------------

BASH = call("Bash")
OWES = Template("is refused while a workflow run owes")
SILENT = Template("workflow-watch-guard")
NOT_PLAIN = Template("path is not plain and absolute")
HOLDS = Template("does not hold a run")


def refused_watch(command: Template, reason: Template) -> list[Step]:
    """A Monitor whose command is not a watch, and the call after it still refused."""
    return [Step(monitor(WATCH_1, command), ALLOW, reason), Step(BASH, REFUSE, Template("did not count"))]


def cases() -> list[Case]:
    c, p, t, e = CaseId, Prose, Template, EventLine
    kill_switch = t("{home}/" + KILL_SWITCH_TAIL)
    over_line = e("OVER LIMIT: 2,000,001 new tokens, ceiling 2,000,000; stop the workflow")
    return [
        Case(c("L01"), p("a launch, then any other call: refused, naming the Monitor command, timeout and advice"), [
            Step(launch(RUN_A, DIR_A), ALLOW, t("before any other call, a workflow run owes this")),
            Step(BASH, REFUSE, t(f"command: {{watcher}} {{home}}/{DIR_A}\n    timeout_ms: 1800000, re-armed on "
                                 "each expiry while the run lasts\nOn the watcher's OVER LIMIT line, stop the run "
                                 "at once with TaskStop. Monitor, TaskStop and ToolSearch go through meanwhile.")),
            Step(call("Read", ToolInput(file_path=t("/x"))), REFUSE, OWES),
            Step(call("Agent", ToolInput(prompt=p("x"))), REFUSE, OWES),
            Step(call("Workflow", ToolInput(script=p("x"))), REFUSE, OWES)]),
        Case(c("L02"), p("the watch, then the call: it goes through"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(watch(WATCH_1, DIR_A), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW)]),
        Case(c("L03"), p("a watch on another directory does not hold the run, and is told why"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_B), ALLOW, t("is no live run's transcript dir")),
            Step(BASH, REFUSE, t("The last Monitor that named the watcher did not count")),
            Step(watch(WATCH_2, DIR_A), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW),
            Step(BASH, REFUSE, lacks=t("did not count"), lines=(notice(WATCH_2, "completed"),))]),
        Case(c("L04"), p("two runs owe two watches"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, REFUSE, t(f"run {RUN_B}: it has no watch"), t(f"run {RUN_A}")),
            Step(watch(WATCH_2, DIR_B), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("L05"), p("a relaunch on the same directory is held by the live watch on it, and is armed by it"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(stop(RUN_A), ALLOW),
            Step(launch(RUN_A2, DIR_A), ALLOW, lacks=t("owes")),
            Step(BASH, ALLOW),
            Step(BASH, REFUSE, t(f"run {RUN_A2}: its watch ended"), lines=(notice(WATCH_1, "completed"),))]),
        Case(c("L06"), p("before any launch, and once every run ended, calls go through"), [
            Step(BASH, ALLOW),
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(stop(RUN_A), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("L07"), p("a launch's note lists the run that owes, even when the new run is covered"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW, t(f"run {RUN_B}: it has no watch")),
            Step(launch(RUN_A2, DIR_A), ALLOW, t(f"run {RUN_B}: it has no watch"), t(f"run {RUN_A2}"))]),
        Case(c("L08"), p("a watch started before its run's launch holds the run from its launch"), [
            Step(watch(WATCH_1, DIR_A), ALLOW, lacks=SILENT),
            Step(launch(RUN_A, DIR_A), ALLOW, lacks=t("owes")),
            Step(BASH, ALLOW),
            Step(BASH, REFUSE, t(f"run {RUN_A}: its watch ended"), t("did not count"),
                 lines=(notice(WATCH_1, "completed"),))]),
        Case(c("L11"), p("a launch on the directory of a watch noted as holding no run clears that note"), [
            Step(launch(RUN_A, DIR_B), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW, t("is no live run's transcript dir")),
            Step(launch(RUN_A2, DIR_A), ALLOW, lacks=t("did not count")),
            Step(BASH, REFUSE, t(f"run {RUN_A}: it has no watch"), t("did not count"))]),
        Case(c("L09"), p("a run launched on a directory a watch reported over is over from its launch"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(watch(WATCH_2, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("reported OVER LIMIT"), lines=(notice(WATCH_1, "completed", over_line),)),
            Step(stop(RUN_A), ALLOW),
            Step(launch(RUN_A2, RelDir(DIR_A + "/")), ALLOW, t(f"run {RUN_A2} ({{home}}/{DIR_A}/): its watch reported "
                                                              "OVER LIMIT")),
            Step(BASH, REFUSE, t(f"TaskStop, task_id {RUN_A2}"))]),
        Case(c("L10"), p("the over directory outlives its run and its watch: a resume after them is over at once"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("reported OVER LIMIT"), lines=(notice(WATCH_1, "completed", over_line),)),
            Step(stop(RUN_A), ALLOW),
            Step(BASH, ALLOW),
            Step(launch(RUN_A2, DIR_A), ALLOW, t(f"run {RUN_A2} ({{home}}/{DIR_A}): its watch reported OVER LIMIT")),
            Step(session_start("startup"), ALLOW),
            Step(launch(RUN_B, DIR_A), ALLOW, t(f"run {RUN_B}: it has no watch"))]),
        Case(c("A01"), p("Monitor, TaskStop and ToolSearch go through while a watch is owed"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(call("ToolSearch", ToolInput(query=p("select:Monitor,TaskStop"))), ALLOW),
            Step(call("Monitor", ToolInput(command=t("x"))), ALLOW),
            Step(call("TaskStop", ToolInput(task_id=RUN_A)), ALLOW),
            Step(BASH, REFUSE)]),
        Case(c("A02"), p("a Write or Edit of the kill switch, by any spelling, goes through; nothing else does"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(call("Write", ToolInput(file_path=kill_switch, content=p(""))), ALLOW),
            Step(call("Edit", ToolInput(file_path=kill_switch)), ALLOW),
            Step(call("Write", ToolInput(file_path=t("{home}/.claude/../" + KILL_SWITCH_TAIL), content=p(""))),
                 ALLOW),
            Step(call("Write", ToolInput(file_path=t("{home}/.claude/other.off"), content=p(""))), REFUSE, OWES),
            Step(call("Read", ToolInput(file_path=kill_switch)), REFUSE, OWES)]),
        Case(c("A03"), p("while the kill switch exists nothing is refused, and launches, watches, stops, notices "
                         "and turn ends are still kept"), [
            Step(launch(RUN_A, DIR_A), ALLOW, lacks=SILENT, act=kill_switch_on),
            Step(BASH, ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(launch(RUN_A2, DIR_C), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(stop(RUN_B), ALLOW),
            Step(turn_end([listed(RUN_A), listed(WATCH_1), listed(RUN_A2)]), ALLOW),
            Step(launch(TaskId("wrunzzzzz"), RelDir("projects/s/subagents/workflows/wf_zzzz")), ALLOW),
            Step(turn_end([listed(RUN_A), listed(WATCH_1), listed(TaskId("wrunzzzzz"))]), ALLOW,
                 lines=(notice(RUN_A2, "completed"),)),
            Step(BASH, REFUSE, t("run wrunzzzzz"), t(f"run {RUN_A}"), act=kill_switch_off),
            Step(BASH, REFUSE, lacks=t(f"run {RUN_A2}")),
            Step(BASH, REFUSE, lacks=t(f"run {RUN_B}"))]),
        Case(c("A04"), p("a subagent's calls go through; its launch is not recorded"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(call("Bash", agent=AgentId("a1234")), ALLOW),
            Step(Event(**launch(RUN_B, DIR_B), agent_id=AgentId("a1234")), ALLOW),
            Step(BASH, REFUSE, t(f"run {RUN_A}"), t(f"run {RUN_B}")),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("A05"), p("while the watcher is not executable the guard stands down and says so, at a launch too, "
                         "and only while something is owed"), [
            Step(launch(RUN_A, DIR_A), REPORTED, t("is not executable"), act=watcher_not_executable),
            Step(BASH, REPORTED, t("is not executable")),
            Step(turn_end([listed(RUN_A)]), REPORTED, t("is not executable")),
            Step(call("ToolSearch", ToolInput(query=p("select:Monitor"))), ALLOW),
            Step(call("Monitor", ToolInput(command=t("x"))), ALLOW),
            Step(call("Write", ToolInput(file_path=kill_switch, content=p(""))), ALLOW),
            Step(BASH, REFUSE, OWES, act=watcher_executable),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW, act=watcher_not_executable),
            Step(launch(RUN_A2, DIR_A), ALLOW, lacks=SILENT),
            Step(turn_end([listed(RUN_A), listed(WATCH_1)]), ALLOW),
            Step(launch(RUN_B, DIR_B), REPORTED, t("is not executable")),
            Step(turn_end([listed(RUN_A), listed(WATCH_1)]), ALLOW),
            Step(BASH, ALLOW, act=watcher_executable)]),
        Case(c("A08"), p("while no watch can be demanded, a run over its ceiling is still refused, the stand-down "
                         "said beside it"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("is not executable"), lines=(notice(WATCH_1, "completed", over_line),),
                 act=watcher_not_executable),
            Step(BASH, REFUSE, t(f"run {RUN_A} ({{home}}/{DIR_A}): its watch reported OVER LIMIT"), t(f"run {RUN_B}:")),
            Step(turn_end([listed(RUN_A), listed(RUN_B)]), REFUSE, t("reported OVER LIMIT")),
            Step(stop(RUN_A), ALLOW),
            Step(BASH, REPORTED, t("is not executable"))]),
        Case(c("A06"), p("a HOME spelled through `..` gets the watcher named by the path it resolves to, and that "
                         "watch counts"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, t(f"command: {{root}}/home/{WATCHER_TAIL} {{root}}/home/{DIR_A}")),
            Step(monitor(WATCH_1, t(f"{{root}}/home/{WATCHER_TAIL} {{root}}/home/{DIR_A}")), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW)], home=RelDir("x/../home")),
        Case(c("A07"), p("a HOME with a space gives the watcher no plain path: the guard stands down and says so"), [
            Step(launch_reply(Launched(status="async_launched", taskId=RUN_A, transcriptDir=t("{root}/runs/wf_a"))),
                 REPORTED, t("has no plain path")),
            Step(BASH, REPORTED, t("has no plain path"))], home=RelDir("my home")),
        Case(c("E01"), p("the run's completion notice ends its debt"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(BASH, ALLOW, lines=(notice(RUN_A, "completed"),))]),
        Case(c("E02"), p("a failed, killed or stopped run ends too"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(launch(RUN_A2, DIR_C), ALLOW),
            Step(BASH, REFUSE),
            Step(BASH, ALLOW, lines=(notice(RUN_A, "failed"), notice(RUN_B, "killed"), notice(RUN_A2, "stopped")))]),
        Case(c("E03"), p("a TaskStop of the run ends its debt, whichever field names the task"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(launch(RUN_A2, DIR_C), ALLOW),
            Step(BASH, REFUSE),
            Step(stop(RUN_A), ALLOW),
            Step(stop_by(ToolInput(task_id=RUN_B), Stopped(message=p("stopped"))), ALLOW),
            Step(stop_by(ToolInput(shell_id=RUN_A2), Stopped(message=p("stopped"))), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("E04"), p("a notice with no status, or a running one, does not end the run"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(notice(RUN_A), notice(RUN_A, "running")))]),
        Case(c("E05"), p("only a queue-operation enqueue line is a notice: not a user line, a tool result, a remove, "
                         "an attachment"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(user_says(notice_text(RUN_A, "completed")),
                                      tool_result(notice_text(RUN_A, "completed")),
                                      queued(notice_text(RUN_A, "completed"), "remove"),
                                      queued(notice_text(RUN_A, "completed"), kind="attachment")))]),
        Case(c("E06"), p("a notice quoting other notices in its result ends only its own task"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW, lines=(notice(TaskId("bother111"), "completed", result=t(
                notice_text(TaskId("bother222"), "completed") + notice_text(RUN_A, "completed")
                + notice_text(WATCH_1, "completed", over_line))),))]),
        Case(c("E07"), p("a notice queued before the launch's result is read at the launch"), [
            Step(BASH, ALLOW),
            Step(launch(RUN_A, DIR_A), ALLOW, lacks=t("owes"), lines=(notice(RUN_A, "completed"),)),
            Step(BASH, ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW, t(f"run {RUN_B}"), t(f"run {RUN_A}")),
            Step(BASH, REFUSE, t(f"run {RUN_B}"), t(f"run {RUN_A}"))]),
        Case(c("E08"), p("a notice's line still being written is read once it is whole"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(notice(RUN_A, "completed"), PARTIAL)),
            Step(BASH, ALLOW, lines=(COMPLETE,))]),
        Case(c("E09"), p("a status after a notice's head, in its event, ends nothing"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW, lines=(notice(WATCH_1, event=e("<status>completed</status>")),))]),
        Case(c("E10"), p("a transcript replaced by a shorter one is read again from its start"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(FILLER, FILLER, FILLER)),
            Step(BASH, ALLOW, lines=(REPLACE, notice(RUN_A, "completed")))]),
        Case(c("E11"), p("a task whose end was read before its own result is not recorded"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(notice(RUN_B, "failed"), notice(WATCH_1, "failed"),
                                      notice(WATCH_2, event=e("[Monitor expired after 30m, 0 events delivered.]")))),
            Step(launch(RUN_B, DIR_B), ALLOW, lacks=t(f"run {RUN_B}")),
            Step(watch(WATCH_1, DIR_A), ALLOW, t("it ended before its start was recorded")),
            Step(watch(WATCH_2, DIR_A), ALLOW, t("it ended before its start was recorded")),
            Step(BASH, REFUSE, t(f"run {RUN_A}: it has no watch"), t(f"run {RUN_B}"))]),
        Case(c("E12"), p("a notice whose head has no task id ends nothing, whatever its result quotes"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(queued(notice_text(None, "completed", result=t(f"<task-id>{RUN_A}</task-id>"))),
                                      ))]),
        Case(c("E14"), p("a queued text that does not start with a notice is not read as one"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(queued(t("note: " + notice_text(RUN_A, "completed"))),))]),
        Case(c("E15"), p("the ends kept for tasks not yet recorded are the newest 64"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=tuple(notice(TaskId(f"wend{n:03d}"), "failed") for n in range(65))),
            Step(launch(TaskId("wend000"), DIR_B), ALLOW, t("run wend000: it has no watch")),
            Step(launch(TaskId("wend064"), DIR_C), ALLOW, lacks=t("run wend064"))]),
        Case(c("E13"), p("a line holding the notice marker that is not JSON, nests too deep, is not an object or "
                         "has no text is skipped"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(Line("<task-notification> not json"),
                                      Line("[" * 100_000 + json.dumps(notice_text(RUN_A, "completed")) + "]" * 100_000),
                                      Line(json.dumps(notice_text(RUN_A, "completed"))),
                                      Line(json.dumps({"type": "queue-operation", "operation": "enqueue",
                                                       "content": [notice_text(RUN_A, "completed")]}))))]),
        Case(c("W01"), p("the watch's expiry reopens the debt, and says the watch ended"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW, lines=(notice(WATCH_1, event=e("500,000 new tokens of 2,000,000")),)),
            Step(BASH, REFUSE, t("its watch ended"),
                 lines=(notice(WATCH_1, event=e("[Monitor expired after 30m with 2 events delivered.]")),)),
            Step(watch(WATCH_2, DIR_A), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("W02"), p("a TaskStop of the watch reopens the debt"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(stop(WATCH_1), ALLOW),
            Step(BASH, REFUSE, t("its watch ended"))]),
        Case(c("W03"), p("the watcher's exit (stream ended, or failed) reopens the debt"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(watch(WATCH_2, DIR_B), ALLOW),
            Step(BASH, REFUSE, t(f"run {RUN_A}: its watch ended"),
                 lines=(notice(WATCH_1, "completed"), notice(WATCH_2, "failed"))),
            Step(BASH, REFUSE, t(f"run {RUN_B}: its watch ended"))]),
        Case(c("W04"), p("OVER LIMIT: only the run's stop clears it, not a new watch"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, REFUSE, t(f"Stop the run now with TaskStop, task_id {RUN_A}"),
                 lines=(notice(WATCH_1, "completed", over_line),)),
            Step(watch(WATCH_2, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("reported OVER LIMIT")),
            Step(stop(RUN_A), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("W05"), p("OVER LIMIT as a live event, before the stream ends, holds as well"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("reported OVER LIMIT"),
                 lines=(notice(WATCH_1, event=e("OVER LIMIT: 3,000,000 new tokens")),))]),
        Case(c("W06"), p("an OVER LIMIT from a watch on another run's directory leaves this run alone"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(watch(WATCH_2, DIR_B), ALLOW),
            Step(BASH, REFUSE, t(f"run {RUN_B} ("), t(f"run {RUN_A} ("),
                 lines=(notice(WATCH_2, event=e("OVER LIMIT: 2,500,000 new tokens")),))]),
        Case(c("W07"), p("OVER LIMIT batched after another line in one event still marks the run"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("reported OVER LIMIT"),
                 lines=(notice(WATCH_1, "completed", e("1,500,000 new tokens of 2,000,000\n" + over_line)),)),
            Step(watch(WATCH_2, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("reported OVER LIMIT"))]),
        Case(c("W08"), p("an OVER LIMIT queued before the watch's TaskStop is read before the watch goes"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(stop(WATCH_1), ALLOW, lines=(notice(WATCH_1, event=over_line),)),
            Step(BASH, REFUSE, t("reported OVER LIMIT"))]),
        Case(c("W10"), p("an event is the text after the last <event> before its close, unclosed tags or not"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, REFUSE, t("reported OVER LIMIT"),
                 lines=(notice(WATCH_1, event=e("x<event>" * 3 + over_line)),))]),
        Case(c("W11"), p("an <event> never closed is not an event"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW, lines=(queued(t(notice_text(WATCH_1, event=e("500,000 new tokens"))
                                               .replace("</task-notification>", "<event>" + over_line
                                                        + "\n</task-notification>"))),))]),
        Case(c("W09"), p("only a line that starts with OVER LIMIT or [Monitor expired counts as one"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW, lines=(notice(WATCH_1, event=e("ahead: OVER LIMIT when past the ceiling")),
                                     notice(WATCH_1, event=e("so: [Monitor expired is a notice")))),
            Step(BASH, ALLOW)]),
        Case(c("S01"), p("a run with no watch keeps the turn going once over the debt's life; the count starts "
                         "again once nothing is owed"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(turn_end([listed(RUN_A)]), REFUSE, t("the turn does not end yet")),
            Step(turn_end([listed(RUN_A)], active=True), ALLOW),
            Step(BASH, REFUSE),
            Step(turn_end([listed(RUN_A)]), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(BASH, ALLOW),
            Step(turn_end([listed(RUN_A)]), REFUSE, t("its watch ended"), lines=(notice(WATCH_1, "completed"),))]),
        Case(c("S02"), p("a run the Stop event no longer lists has ended, and its debt with it"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(turn_end([listed(RUN_B)]), REFUSE, t(f"run {RUN_B}"), t(f"run {RUN_A}")),
            Step(turn_end([]), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("S03"), p("a watch the Stop event no longer lists has ended"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(turn_end([listed(RUN_A), listed(WATCH_1)]), ALLOW),
            Step(turn_end([listed(RUN_A)]), REFUSE, t("its watch ended"))]),
        Case(c("S04"), p("a Stop event without the list keeps what was recorded"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(turn_end(None), REFUSE)]),
        Case(c("S05"), p("a run over its ceiling keeps the turn going three times over the debt's life, calls "
                         "between or not, and not once it is stopped"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(turn_end([listed(RUN_A)]), REFUSE, t("reported OVER LIMIT"),
                 lines=(notice(WATCH_1, "completed", over_line),)),
            Step(turn_end([listed(RUN_A)], active=True), REFUSE, t("reported OVER LIMIT")),
            Step(BASH, REFUSE, t("reported OVER LIMIT")),
            Step(turn_end([listed(RUN_A)]), REFUSE, t("reported OVER LIMIT")),
            Step(BASH, REFUSE, t("reported OVER LIMIT")),
            Step(turn_end([listed(RUN_A)]), ALLOW),
            Step(stop(RUN_A), ALLOW),
            Step(turn_end([]), ALLOW)]),
        Case(c("S06"), p("a task the Stop event lists as ended is not in flight"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(turn_end([listed(RUN_A), listed(RUN_B, "completed"), listed(WATCH_1, "killed")]), REFUSE,
                 t(f"run {RUN_A}: its watch ended"), t(f"run {RUN_B}"))]),
        Case(c("S07"), p("a Stop listing an entry that is not an object reads the rest"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(turn_end([listed(RUN_A), t("not an object")]), REFUSE)]),
        Case(c("S08"), p("a run's count starts again when a watch pays its debt, even with no call before the next"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(turn_end([listed(RUN_A)]), REFUSE, t("its watch ended"), lines=(notice(WATCH_1, "completed"),)),
            Step(watch(WATCH_2, DIR_A), ALLOW),
            Step(turn_end([listed(RUN_A)]), REFUSE, t("reported OVER LIMIT"),
                 lines=(notice(WATCH_2, "completed", over_line),)),
            Step(turn_end([listed(RUN_A)]), REFUSE),
            Step(turn_end([listed(RUN_A)]), REFUSE),
            Step(turn_end([listed(RUN_A)]), ALLOW)]),
        Case(c("S09"), p("each run keeps its own count: one run's spent turn ends do not spend another's"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(watch(WATCH_2, DIR_B), ALLOW),
            Step(turn_end([listed(RUN_A), listed(RUN_B), listed(WATCH_2)]), REFUSE,
                 lines=(notice(WATCH_1, "completed", over_line),)),
            Step(turn_end([listed(RUN_A), listed(RUN_B), listed(WATCH_2)]), REFUSE),
            Step(turn_end([listed(RUN_A), listed(RUN_B), listed(WATCH_2)]), REFUSE),
            Step(turn_end([listed(RUN_A), listed(RUN_B), listed(WATCH_2)]), ALLOW),
            Step(stop(RUN_A), ALLOW, lines=(notice(WATCH_2, "completed"),)),
            Step(turn_end([listed(RUN_B)]), REFUSE, t(f"run {RUN_B}: its watch ended"))]),
        Case(c("N01"), p("a new process (startup, resume) drops the session's debts; a compaction or a clear does "
                         "not"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(session_start("compact"), ALLOW),
            Step(session_start("clear"), ALLOW),
            Step(BASH, REFUSE),
            Step(session_start("resume"), ALLOW),
            Step(BASH, ALLOW),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(session_start("startup"), ALLOW),
            Step(BASH, ALLOW)]),
        Case(c("N02"), p("another session's debt is not this session's"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(Event(**BASH, session_id=SessionId("another-session")), ALLOW),
            Step(BASH, REFUSE)]),
        Case(c("N03"), p("the session's transcript at a new path is read from its start"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, lines=(FILLER, FILLER)),
            Step(BASH, ALLOW, lines=(notice(RUN_A, "completed"),), transcript=t("{root}/second.jsonl"))]),
        Case(c("N04"), p("a launch whose transcript does not exist yet is recorded"), [
            Step(launch(RUN_A, DIR_A), ALLOW, transcript=t("{root}/not-yet.jsonl")),
            Step(BASH, REFUSE, t(f"run {RUN_A}"), transcript=t("{root}/not-yet.jsonl"))]),
        Case(c("C01"), p("the watcher by ~/, $HOME/ or ${HOME}/, a ceiling up to 2,000,000, an interval up to 60, "
                         "blanks around, count"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(monitor(WATCH_1, t(f"~/{WATCHER_TAIL} {{home}}/{DIR_A} 2000000 1")), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW),
            Step(stop(WATCH_1), ALLOW),
            Step(monitor(WATCH_2, t(f"$HOME/{WATCHER_TAIL} {{home}}/{DIR_A}/ 1500000 60")), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW),
            Step(stop(WATCH_2), ALLOW),
            Step(monitor(WATCH_1, t(f" ${{HOME}}/{WATCHER_TAIL}\t{{home}}/{DIR_A}  1_000 ")), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW)]),
        Case(c("C02"), p("a ceiling above 2,000,000, below 1, or not a whole number does not count"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            *refused_watch(watch_command(DIR_A, t("2000001")), t("is not between 1 and 2,000,000")),
            *refused_watch(watch_command(DIR_A, t("2e6")), t("is not a whole number")),
            *refused_watch(watch_command(DIR_A, t("0")), t("is not between")),
            *refused_watch(watch_command(DIR_A, t("-5")), t("is not between"))]),
        Case(c("C03"), p("an interval of 0 or less, past 60 seconds, or not a number does not count"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            *refused_watch(watch_command(DIR_A, t("2000000"), t("61")), t("is not above 0 and at most 60 seconds")),
            *refused_watch(watch_command(DIR_A, t("2000000"), t("0")), t("is not above 0")),
            *refused_watch(watch_command(DIR_A, t("2000000"), t("-5")), t("is not above 0")),
            *refused_watch(watch_command(DIR_A, t("2000000"), t("nan")), t("is not above 0")),
            *refused_watch(watch_command(DIR_A, t("2000000"), t("soon")), t("is not a number"))]),
        Case(c("C04"), p("the watcher not alone (a pipe, a list, a redirection, a subshell, a line) does not count"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            *[step for tail in (" | grep -v tokens", "; true", " && true", " > /dev/null", " < /dev/null",
                                "\nsleep 1", " &")
              for step in refused_watch(t(watch_command(DIR_A) + tail), t("not alone"))],
            *refused_watch(t(f"({watch_command(DIR_A)})"), t("not alone"))]),
        Case(c("C05"), p("the watcher by its bare name, through an interpreter, or at another path does not count"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            *refused_watch(t(f"workflow-token-watch {{home}}/{DIR_A}"), NOT_PLAIN),
            *refused_watch(t(f"python3 {{watcher}} {{home}}/{DIR_A}"), NOT_PLAIN),
            *refused_watch(t(f"/tmp/elsewhere/workflow-token-watch {{home}}/{DIR_A}"), t("by its path"))]),
        Case(c("C06"), p("a variable, a bare ~ or $HOME, a substitution, quoting, an extra word or no directory does "
                         "not count"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            *refused_watch(t("{watcher} $DIR"), t("expands a variable")),
            *refused_watch(t("{watcher} $HOME"), t("expands a variable")),
            *refused_watch(t("{watcher} ~"), NOT_PLAIN),
            *refused_watch(t(f"{{watcher}} {{home}}/{DIR_A} `echo 5`"), t("expands")),
            *refused_watch(t(f"{{watcher}} {{home}}/{DIR_A} 2000000 1 extra"), t("is not `")),
            *refused_watch(t("{watcher}"), t("is not `")),
            *refused_watch(t(f"{{watcher}} '{{home}}/{DIR_A}"), t("quotes or escapes"))]),
        Case(c("C07"), p("a Monitor that does not name the watcher is not a watch and says nothing"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(monitor(WATCH_1, t("tail -f /var/log/x | grep --line-buffered ERROR")), ALLOW, lacks=SILENT),
            Step(BASH, REFUSE, lacks=t("did not count"))]),
        Case(c("C08"), p("a Monitor naming the watcher says nothing while no run is recorded, state or none"), [
            Step(monitor(WATCH_2, watch_command(DIR_A, t("9000000"))), ALLOW, lacks=SILENT),
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(watch(WATCH_1, DIR_A), ALLOW),
            Step(stop(RUN_A), ALLOW),
            Step(monitor(WATCH_2, watch_command(DIR_A, t("9000000"))), ALLOW, lacks=SILENT),
            Step(launch(RUN_B, DIR_B), ALLOW),
            Step(BASH, REFUSE)]),
        Case(c("C09"), p("a run's directory spelled with ./ and a trailing slash is the same directory"), [
            Step(launch(RUN_A, RelDir("projects/s/./subagents/workflows/wf_aaaa/")), ALLOW),
            Step(BASH, REFUSE, t(f"command: {{watcher}} {{home}}/{DIR_A}\n")),
            Step(watch(WATCH_1, DIR_A), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW)]),
        Case(c("C10"), p("a word the shell reads otherwise does not count: quoted ~ or $HOME, #, \\r, a glob, a "
                         "brace, ~ inside, .., a relative path, $HOME_X"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(launch(RUN_B, RelDir("../home_X/wf")), ALLOW),
            *refused_watch(t(f"{{watcher}} \"~/{DIR_A}\""), t("quotes or escapes")),
            *refused_watch(t(f"{{watcher}} '$HOME/{DIR_A}'"), t("quotes or escapes")),
            *refused_watch(t(f"{{watcher}} {{home}}/{DIR_A}#x"), NOT_PLAIN),
            *refused_watch(t(f"{{watcher}} {{home}}/{DIR_A}\r2000000"), NOT_PLAIN),
            *refused_watch(t("{watcher} {home}/projects/s/subagents/workflows/wf_aa*"), NOT_PLAIN),
            *refused_watch(t("{watcher} {home}/projects/s/subagents/workflows/{wf_aaaa,x}"), NOT_PLAIN),
            *refused_watch(t("{watcher} {home}/projects/s~/subagents/workflows/wf_aaaa"), NOT_PLAIN),
            *refused_watch(t(f"{{watcher}} {{home}}/{DIR_A}/missing/.."), NOT_PLAIN),
            *refused_watch(t(f"{{watcher}} {DIR_A}"), NOT_PLAIN),
            *refused_watch(t("{watcher} $HOME_X/wf"), t("expands a variable"))]),
        Case(c("C11"), p("the watcher by the path a link points to, and a directory through a linked parent, count"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE),
            Step(monitor(WATCH_1, t("{home}/real-bin/workflow-token-watch {home}/alias/s/subagents/workflows/wf_aaaa")),
                 ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW),
            Step(launch(RUN_B, RelDir("alias/s/subagents/workflows/wf_bbbb")), ALLOW,
                 t(f"command: {{watcher}} {{home}}/alias/s/subagents/workflows/wf_bbbb\n"))], setup=linked_paths),
        Case(c("C12"), p("a run's directory plain as given but resolving through a space is named as given, and that "
                         "watch counts"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(BASH, REFUSE, t(f"command: {{watcher}} {{home}}/{DIR_A}\n")),
            Step(watch(WATCH_1, DIR_A), ALLOW, lacks=HOLDS),
            Step(BASH, ALLOW)], setup=projects_through_a_space),
        Case(c("R01"), p("a launch result in prose is not read: an error, reported"), [
            Step(launch_reply(t(f"Workflow launched in background. Task ID: {RUN_A}\nSummary: x\n"
                                f"Transcript dir: {{home}}/{DIR_A}\nScript file: /x.js")), REPORTED, t("not recorded")),
            Step(BASH, ALLOW)]),
        Case(c("R02"), p("a launch result missing the task id, the directory or both is an error, reported"), [
            Step(launch_reply(Launched(status="failed")), REPORTED, t("not recorded")),
            Step(launch_reply(Launched(status="async_launched", taskId=RUN_A)), REPORTED, t("not recorded")),
            Step(launch_reply(Launched(status="async_launched", transcriptDir=under_home(DIR_A))), REPORTED,
                 t("not recorded")),
            Step(launch_reply(Launched(status="async_launched", taskId=Millis(7), transcriptDir=under_home(DIR_A))),
                 REPORTED, t("not recorded")),
            Step(launch_reply(Launched(status="async_launched", taskId=TaskId(""), transcriptDir=under_home(DIR_A))),
                 REPORTED, t("not recorded")),
            Step(launch_reply(Launched(status="async_launched", taskId=RUN_A, transcriptDir=t(""))), REPORTED,
                 t("not recorded")),
            Step(BASH, ALLOW)]),
        Case(c("R03"), p("an event of another kind is left alone"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(Event(hook_event_name="Notification"), ALLOW),
            Step(BASH, REFUSE)]),
        Case(c("R04"), p("a Monitor result with no task id does not count, and is told why"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(monitor(None, watch_command(DIR_A)), ALLOW, t("names no task id")),
            Step(BASH, REFUSE)]),
        Case(c("R05"), p("a run's directory with no plain spelling is an error, reported"), [
            Step(launch(RUN_A, RelDir("projects/s/sub agents/wf_x")), REPORTED, t("has no plain spelling")),
            Step(BASH, ALLOW)]),
        Case(c("R06"), p("an event of an odd shape is an error of the hook's own, reported in one line"), [
            Step(launch(RUN_A, DIR_A), ALLOW),
            Step(Event(hook_event_name="PostToolUse", tool_name="Monitor", tool_input=t("not an object"),
                       tool_response=MonitorStarted(taskId=WATCH_1)), REPORTED, t("went unchecked"))]),
    ]


# Running -----------------------------------------------------------------------

def filled(text: Template, world: World) -> Template:
    return Template(text.replace("{watcher}", str(world.home / WATCHER_TAIL)).replace("{home}", str(world.home))
                    .replace("{root}", str(world.root)))


def fill(event: Event, world: World, transcript: Path | None = None) -> Event:
    """The event with its placeholders filled, its session and its transcript set."""
    text = filled(Template(json.dumps(event)), world)
    out: Event = json.loads(text)
    out.setdefault("session_id", SESSION)
    out["transcript_path"] = PathText(str(transcript or world.transcript))
    return out


def hook(world: World, event: Event, program: Path = GUARD, real_home: bool = False) -> Answer:
    """Pipe an event to a hook run by its path, as Claude Code runs it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")} | {"TMPDIR": str(world.tmp)}
    if not real_home:
        env["HOME"] = str(world.home)
    try:
        done = subprocess.run([str(program)], input=json.dumps(event), capture_output=True, text=True, check=False,
                              env=env)
    except OSError as exc:  # the hook did not start: its interpreter is missing, or it is not executable
        return Answer(ExitStatus(127), Prose(""), Prose(f"the hook did not start: {exc}"))
    return Answer(ExitStatus(done.returncode), Prose(done.stdout), Prose(done.stderr))


def note_of(answer: Answer, event: Event) -> tuple[Prose, Prose | None]:
    """The note on stdout, and what is wrong with stdout's shape, if anything."""
    if not answer.stdout.strip():
        return Prose(""), None
    try:
        data = json.loads(answer.stdout)
    except ValueError:
        return Prose(""), Prose(f"stdout is not JSON: {answer.stdout[:120]!r}")
    output = data.get("hookSpecificOutput") if isinstance(data, dict) else None
    if not (isinstance(data, dict) and set(data) == {"hookSpecificOutput"} and isinstance(output, dict)
            and set(output) == {"hookEventName", "additionalContext"}
            and output.get("hookEventName") == event.get("hook_event_name")
            and isinstance(output.get("additionalContext"), str)):
        return Prose(""), Prose(f"stdout is not the context shape: {answer.stdout[:160]!r}")
    return Prose(output["additionalContext"]), None


def judged(answer: Answer, step: Step, event: Event, world: World) -> Prose | None:
    """What is wrong with an answer, read where the model reads it, or None."""
    note, shape = note_of(answer, event)
    says, lacks = filled(step.says, world), filled(step.lacks, world)
    if answer.status != step.want:
        return Prose(f"want={step.want} got={answer.status}")
    if shape:
        return shape
    if step.want == ALLOW and answer.stderr:
        return Prose(f"stderr at exit 0: {answer.stderr[:160]!r}")
    if step.want != ALLOW and answer.stdout:
        return Prose(f"stdout at exit {answer.status}: {answer.stdout[:160]!r}")
    if step.want == REPORTED and (len(answer.stderr.strip().splitlines()) != 1
                                  or not answer.stderr.startswith("workflow-watch-guard:")):
        return Prose("an error not reported as the hook's own one line")
    read = note if step.want == ALLOW else answer.stderr
    if says not in read:
        return Prose(f"lacks {says[:80]!r}")
    if lacks and (lacks in note or lacks in answer.stderr):
        return Prose(f"holds {lacks[:80]!r}")
    return None


def append(transcript: Path, lines: tuple[Line, ...], world: World) -> None:
    """Append lines to a transcript; PARTIAL takes back the newline after the line before it."""
    if not lines:
        return
    transcript.touch()
    with transcript.open("rb+") as handle:
        handle.seek(0, os.SEEK_END)
        for line in lines:
            if line == REPLACE:
                handle.truncate(0)
                handle.seek(0)
            elif line == PARTIAL:
                handle.truncate(handle.tell() - 1)
                handle.seek(0, os.SEEK_END)
            elif line == COMPLETE:
                handle.write(b"\n")
            else:
                handle.write(filled(Template(line), world).encode() + b"\n")


def world_for(name: CaseId, home: RelDir = RelDir("home")) -> World:
    root = BASE / name
    world = World(root, root / home, root / "tmp", root / "transcript.jsonl")
    world.home.mkdir(parents=True)
    world.tmp.mkdir()
    world.transcript.write_text("")
    stub = world.home / WATCHER_TAIL
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return world


def run_case(case: Case) -> Verdict:
    world = world_for(case.id, case.home)
    if case.setup:
        case.setup(world)
    wrong: list[Prose] = []
    for number, step in enumerate(case.steps, 1):
        if step.act:
            step.act(world)
        transcript = Path(filled(step.transcript, world)) if step.transcript else world.transcript
        append(transcript, step.lines, world)
        event = fill(step.event, world, transcript)
        answer = hook(world, event)
        problem = judged(answer, step, event, world)
        if problem:
            wrong.append(Prose(f"step {number}: {problem}: "
                               + " | ".join((answer.stderr or answer.stdout).strip().splitlines()[-3:])[:240]))
    return Verdict(case.id, case.what, wrong)


def load_guard() -> ModuleType:
    sys.path.insert(0, str(GUARD.parent))
    spec = importlib.util.spec_from_file_location("workflow_watch_guard", GUARD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locks_before_reading() -> list[Prose]:
    """In this process: every event takes the state directory's lock, and reads the state only after it."""
    world = world_for(CaseId("X07"))
    try:
        guard = load_guard()
    except BaseException as exc:  # noqa: BLE001 - a guard that exits or raises on import disagrees
        return [Prose(f"the guard does not load: {type(exc).__name__}: {exc}")]
    guard.STATE_DIR = world.tmp / "claude-workflow-watch-guard"
    guard.KILL_SWITCH = world.home / KILL_SWITCH_TAIL
    guard.WATCHER = world.home / WATCHER_TAIL
    taken: list[Lock] = []
    real_flock, real_state_path = fcntl.flock, guard.state_path

    def recorded(fd: FileDescriptor, operation: LockOperation) -> None:
        taken.append(Lock(Path(os.readlink(f"/proc/self/fd/{fd}")), operation))
        real_flock(fd, operation)

    class Watched(type(Path())):
        def read_text(self, encoding: Prose | None = None, errors: Prose | None = None,
                      newline: Prose | None = None) -> Prose:
            taken.append(Lock(Path(self), "read"))
            return Prose(super().read_text(encoding=encoding, errors=errors, newline=newline))

    guard.fcntl.flock = recorded
    guard.state_path = lambda session: Watched(real_state_path(session))
    wrong: list[Prose] = []
    try:
        for event in (launch(RUN_A, DIR_A), BASH, turn_end([listed(RUN_A)]), watch(WATCH_1, DIR_A),
                      stop(WATCH_1), session_start("startup")):
            before = len(taken)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    guard.handle(fill(event, world))
            except Exception as exc:  # noqa: BLE001 - a case that raises disagrees; the others still run
                wrong.append(Prose(f"{event.get('hook_event_name')}: {type(exc).__name__}: {exc}"))
            seen = taken[before:]
            lock = Lock(guard.STATE_DIR, LockOperation(fcntl.LOCK_EX))
            reads = [index for index, each in enumerate(seen) if each.operation == "read"]
            if lock not in seen or any(index < seen.index(lock) for index in reads):
                wrong.append(Prose(f"a {event.get('hook_event_name')} event read the state before its lock, or took "
                                   f"none: {seen}"))
    finally:
        guard.fcntl.flock, guard.state_path = real_flock, real_state_path
    return wrong


BENT: dict[Prose, Bend] = {
    Prose("a run that is a string"): Bend(StateText(r'"wrunaaaaa": \{[^}]*\}'), StateText('"wrunaaaaa": "x"')),
    Prose("a run missing a key"): Bend(StateText(r', "over": false'), StateText("")),
    Prose("runs a list"): Bend(StateText(r'"runs": \{[^}]*\}\}'), StateText('"runs": []')),
    Prose("watches a list"): Bend(StateText(r'"watches": \{\}'), StateText('"watches": []')),
    Prose("a watch's directory a number"): Bend(StateText(r'"watches": \{\}'),
                                                StateText('"watches": {"bwatch111": 7}')),
    Prose("over_dirs not a list"): Bend(StateText(r'"over_dirs": \[\]'), StateText('"over_dirs": {}')),
    Prose("an over directory a number"): Bend(StateText(r'"over_dirs": \[\]'), StateText('"over_dirs": [7]')),
    Prose("ended not a list"): Bend(StateText(r'"ended": \[\]'), StateText('"ended": "x"')),
    Prose("an ended task a number"): Bend(StateText(r'"ended": \[\]'), StateText('"ended": [7]')),
    Prose("the transcript a number"): Bend(StateText(r'"transcript": "[^"]*"'), StateText('"transcript": 7')),
    Prose("the offset a string"): Bend(StateText(r'"offset": \d+'), StateText('"offset": "7"')),
    Prose("the offset below 0"): Bend(StateText(r'"offset": \d+'), StateText('"offset": -1')),
    Prose("the note a number"): Bend(StateText(r'"note": ""'), StateText('"note": 7')),
    Prose("a run's count not a number"): Bend(StateText(r'"refused": 0'), StateText('"refused": false')),
    Prose("a run's count below 0"): Bend(StateText(r'"refused": 0'), StateText('"refused": -4')),
    Prose("a run's directory a number"): Bend(StateText(r'"directory": "[^"]*"'), StateText('"directory": 7')),
    Prose("a run's spelling a number"): Bend(StateText(r'"named": "[^"]*"'), StateText('"named": 7')),
    Prose("a run's armed a number"): Bend(StateText(r'"armed": false'), StateText('"armed": 0')),
    Prose("a run's over a number"): Bend(StateText(r'"over": false'), StateText('"over": 0')),
}


def special_cases() -> Iterator[Verdict]:
    world = world_for(CaseId("X01"))
    wrong: list[Prose] = []
    for raw in ("{not json", "[]", json.dumps({"hook_event_name": "PreToolUse"})):
        try:
            done = subprocess.run([str(GUARD)], input=raw, capture_output=True, text=True, check=False,
                                  env={**os.environ, "HOME": str(world.home), "TMPDIR": str(world.tmp)})
        except OSError as exc:
            wrong.append(Prose(f"{raw!r}: the guard did not start: {exc}"))
            continue
        if (done.returncode, done.stdout, done.stderr) != (ALLOW, "", ""):
            wrong.append(Prose(f"{raw!r}: exit {done.returncode} {done.stderr[-120:]!r}"))
    yield Verdict(CaseId("X01"), Prose("an event that cannot be read, or names nothing, goes through"), wrong)

    wrong = []
    shapes = {Prose("not json"): Bend(StateText(r"(?s).*"), StateText("{not json")),
              Prose("a list"): Bend(StateText(r"(?s).*"), StateText("[]")),
              Prose("keys missing"): Bend(StateText(r"(?s).*"), StateText('{"runs": {}}')), **BENT}
    for name, bent in shapes.items():
        world = world_for(CaseId("X02"))
        hook(world, fill(launch(RUN_A, DIR_A), world))
        states = list(world.tmp.rglob("session-*.json"))
        for state in states:
            text, count = re.subn(bent.pattern, bent.replacement.replace("\\", "\\\\"), state.read_text(), count=1)
            state.write_text(text)
            if count != 1:
                wrong.append(Prose(f"{name}: the pattern {bent.pattern!r} is not in the state file"))
        answer = hook(world, fill(BASH, world))
        if not states or answer != Answer(ALLOW, Prose(""), Prose("")):
            wrong.append(Prose(f"{name}: {answer}"))
        shutil.rmtree(BASE / "X02")
    yield Verdict(CaseId("X02"), Prose("a state file of another shape is read as empty, not as an error"), wrong)

    world = world_for(CaseId("X03"))
    wrong = []
    for event in (watch(WATCH_1, DIR_A, Template("9000000")), stop(WATCH_1), BASH, turn_end([]),
                  monitor(WATCH_1, Template("tail -f /x"))):
        hook(world, fill(event, world))
        if any(world.tmp.iterdir()):
            wrong.append(Prose(f"before any run or watch, a {event.get('hook_event_name')} event left "
                               f"{[q.name for q in world.tmp.iterdir()]}"))
    hook(world, fill(launch(RUN_A, DIR_A), world))
    held = list(world.tmp.rglob("session-*.json"))
    hook(world, fill(stop(RUN_A), world))
    left = list(world.tmp.rglob("session-*"))
    hook(world, fill(watch(WATCH_1, DIR_A), world))
    watched = list(world.tmp.rglob("session-*.json"))
    hook(world, fill(stop(WATCH_1), world))
    gone = list(world.tmp.rglob("session-*"))
    if not held or left or not watched or gone:
        wrong.append(Prose(f"run recorded {[q.name for q in held]}, left {[q.name for q in left]}; watch recorded "
                           f"{[q.name for q in watched]}, left {[q.name for q in gone]}"))
    hook(world, fill(launch(RUN_A, DIR_A), world))
    hook(world, fill(watch(WATCH_1, DIR_A), world))
    append(world.transcript, (notice(WATCH_1, "completed", EventLine("OVER LIMIT: 2,000,001 new tokens")),), world)
    hook(world, fill(stop(RUN_A), world))
    kept = list(world.tmp.rglob("session-*.json"))
    hook(world, fill(session_start("startup"), world))
    dropped = list(world.tmp.rglob("session-*"))
    if not kept or dropped:
        wrong.append(Prose(f"over directory kept {[q.name for q in kept]}, after a new process {dropped}"))
    yield Verdict(CaseId("X03"), Prose("state is written for a run, a watch or an over directory only, and none "
                                       "stays once none is recorded or a new process starts"), wrong)

    world = world_for(CaseId("X04"))
    hook(world, fill(launch(RUN_A, DIR_A), world))
    answer = hook(world, fill(BASH, world))
    lines = answer.stderr.splitlines()
    command = next((line.split("command: ", 1)[1] for line in lines if "command: " in line), "")
    timeout = next((line.split("timeout_ms: ", 1)[1].split(",")[0] for line in lines if "timeout_ms: " in line), "0")
    demanded = fill(call("Monitor", ToolInput(command=Template(command), description=Prose("watch"),
                                              timeout_ms=Millis(int(timeout)))), world)
    wrong = [] if answer.status == REFUSE and command else [Prose(f"no command named: {answer.stderr[-200:]}")]
    for sibling in ("no-polling-loops.py", "heavy-run-guard.py"):
        if (SIBLINGS / sibling).is_file():
            said = hook(world, demanded, SIBLINGS / sibling, real_home=True)
            if said.status != ALLOW:
                wrong.append(Prose(f"{sibling} refuses the demanded command (exit {said.status}): "
                                   f"{said.stderr[-200:]}"))
        else:
            wrong.append(Prose(f"{sibling} is not beside the guard"))
    counted = hook(world, fill(monitor(WATCH_1, Template(command)), world))
    after = hook(world, fill(BASH, world))
    if counted != Answer(ALLOW, Prose(""), Prose("")) or after.status != ALLOW:
        wrong.append(Prose(f"the guard does not count its own command {command!r}: {counted}, then {after.status}"))
    yield Verdict(CaseId("X04"), Prose("the Monitor command the guard names passes the kit's Monitor guards, and the "
                                       "guard counts it"), wrong)

    world = world_for(CaseId("X05"))
    escape = SessionId("../../../escape")
    hook(world, Event(**{**fill(launch(RUN_A, DIR_A), world), "session_id": escape}))
    inside = [path for path in world.tmp.rglob("*") if path.is_file()]
    refused = hook(world, Event(**{**fill(BASH, world), "session_id": escape}))
    yield Verdict(CaseId("X05"), Prose("a session id that names a path outside the state directory stays inside it"),
                  [] if inside and all(path.parent == world.tmp / "claude-workflow-watch-guard" for path in inside)
                  and not any(world.root.glob("*escape*")) and refused.status == REFUSE
                  else [Prose(f"files {inside}, refusal {refused.status}")])

    yield Verdict(CaseId("X07"), Prose("every event takes the state directory's lock, and reads the state only after "
                                       "it"), locks_before_reading())


def main() -> ExitStatus:
    bad = 0
    total = 0
    try:
        for verdict in itertools.chain((run_case(case) for case in cases()), special_cases()):
            total += 1
            bad += bool(verdict.wrong)
            print(f"{'MISS' if verdict.wrong else 'ok  '} {verdict.id:<4} {verdict.what}")
            for line in verdict.wrong:
                print(f"        {line}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"{total} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
