#!/usr/bin/env python3
"""Regression cases for background-run-watch-guard.py.

Every case plays a sequence of hook events against the guard, each piped on stdin to a fresh process
that runs the guard by its path, through its own shebang, as Claude Code does: a Bash call's
PostToolUse (started in the background or not), a Monitor's, a TaskStop's, the PreToolUse of the calls
between, Stop and SessionStart. Before an event, a step may act on the world (turn the kill switch on or
off, take the watcher's execute bit, put the state out of shape) and append lines to the session
transcript (a task's notice as Claude Code queues it). Each step names the exit status the rule wants
and text the answer must hold or lack, read where the model reads it: a refusal or a stand-down on
stderr with nothing on stdout, a note as the one JSON shape Claude Code takes on stdout, and nothing on
stderr when the call goes through. Every case gets its own HOME (its own kill switch and watcher, a stub
with its execute bit), its own temporary directory (its own state) and its own transcript; nothing
reads a clock.

Usage: test_background_run_watch_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every step of every case agrees with its expectation.
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

# A background task's id; a session's or an agent's id; text in which {home} and {root} stand for the
# case's HOME and root; a transcript line; a notice's event line; prose a case names itself by or
# expects; a case's name; milliseconds; the hook's exit status.
TaskId = NewType("TaskId", str)
SessionId = NewType("SessionId", str)
AgentId = NewType("AgentId", str)
Template = NewType("Template", str)
Line = NewType("Line", str)
EventLine = NewType("EventLine", str)
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
Millis = NewType("Millis", int)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type HookEvent = Literal["PreToolUse", "PostToolUse", "Stop", "SessionStart"]
type Tool = Literal["Bash", "Monitor", "TaskStop", "ToolSearch", "Read", "Write", "Edit"]
type Source = Literal["startup", "resume", "clear", "compact"]
type Status = Literal["completed", "failed", "killed", "stopped", "running"]

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "background-run-watch-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="background-run-watch-guard-test-")).resolve()
SESSION = SessionId("session-under-test")
WATCHER_TAIL = ".local/bin/log-stall-watch"
KILL_SWITCH_TAIL = ".claude/background-run-watch-guard.off"
TIMEOUT = Millis(1_800_000)
RUN, RUN_2 = TaskId("brunaaaaa"), TaskId("brunbbbbb")
WATCH, WATCH_2 = TaskId("bwatch111"), TaskId("bwatch222")
LOG = Template("{root}/repo/logs/run.log")
LOG_2 = Template("{root}/repo/logs/other.log")
HEAVY = Template("cd {root}/repo && taskset -c 0-19 cargo build > logs/run.log 2>&1")
HEAVY_2 = Template("cd {root}/repo && taskset -c 0-19 cargo test > logs/other.log 2>&1")
WATCHER = Template("{home}/" + WATCHER_TAIL)


class ToolInput(TypedDict, total=False):
    command: Template
    run_in_background: bool
    file_path: Template
    task_id: TaskId


class BashResult(TypedDict, total=False):
    stdout: Prose
    stderr: Prose
    backgroundTaskId: TaskId


class MonitorStarted(TypedDict, total=False):
    taskId: TaskId
    timeoutMs: Millis


class Stopped(TypedDict, total=False):
    message: Prose
    task_id: TaskId


class BackgroundTask(TypedDict, total=False):
    id: TaskId
    status: Status


class Event(TypedDict, total=False):
    hook_event_name: HookEvent
    session_id: SessionId
    transcript_path: Template
    cwd: Template
    tool_name: Tool
    tool_input: ToolInput
    tool_response: BashResult | MonitorStarted | Stopped | Template
    agent_id: AgentId
    source: Source
    background_tasks: list[BackgroundTask]


class World(NamedTuple):
    """One case's root, HOME, temporary directory and transcript."""

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
    act: Act | None = None


class Case(NamedTuple):
    id: CaseId
    what: Prose
    steps: list[Step]


# Acts on the world ----------------------------------------------------------------

def kill_switch_on(world: World) -> None:
    (world.home / KILL_SWITCH_TAIL).parent.mkdir(parents=True, exist_ok=True)
    (world.home / KILL_SWITCH_TAIL).write_text("")


def kill_switch_off(world: World) -> None:
    (world.home / KILL_SWITCH_TAIL).unlink()


def watcher_not_executable(world: World) -> None:
    (world.home / WATCHER_TAIL).chmod(0o644)


def corrupt_state(world: World) -> None:
    for state in (world.tmp / "claude-background-run-watch-guard").glob("session-*.json"):
        state.write_text('{"runs": "not a map"}')


# Events --------------------------------------------------------------------------

def launch(task: TaskId = RUN, command: Template = HEAVY, background: bool = True) -> Event:
    """A Bash call's PostToolUse; background names its task as the harness does."""
    result = BashResult(stdout=Prose(""), stderr=Prose(""))
    if background:
        result["backgroundTaskId"] = task
    return Event(hook_event_name="PostToolUse", tool_name="Bash",
                 tool_input=ToolInput(command=command, run_in_background=background), tool_response=result)


def monitor(task: TaskId, command: Template) -> Event:
    return Event(hook_event_name="PostToolUse", tool_name="Monitor", tool_input=ToolInput(command=command),
                 tool_response=MonitorStarted(taskId=task, timeoutMs=TIMEOUT))


def watch_of(log: Template = LOG, silence: Template = Template("900")) -> Template:
    return Template(f"{WATCHER} {log} {silence}")


def pre(tool: Tool = "Read", file_path: Template = Template("/etc/hostname")) -> Event:
    return Event(hook_event_name="PreToolUse", tool_name=tool, tool_input=ToolInput(file_path=file_path))


def task_stop(task: TaskId) -> Event:
    return Event(hook_event_name="PostToolUse", tool_name="TaskStop", tool_input=ToolInput(task_id=task),
                 tool_response=Stopped(message=Prose("stopped"), task_id=task))


def stop(tasks: list[BackgroundTask] | None = None) -> Event:
    event = Event(hook_event_name="Stop")
    if tasks is not None:
        event["background_tasks"] = tasks
    return event


def session_start(source: Source) -> Event:
    return Event(hook_event_name="SessionStart", source=source)


def notice(task: TaskId, status: Status | None = "completed", event: EventLine | None = None) -> Line:
    parts = [f"<task-id>{task}</task-id>", "<tool-use-id>toolu_x</tool-use-id>"]
    if status:
        parts.append(f"<status>{status}</status>")
    parts.append("<summary>a task</summary>")
    if event:
        parts.append(f"<event>{event}</event>")
    text = "<task-notification>\n" + "\n".join(parts) + "\n</task-notification>"
    return Line(json.dumps({"type": "queue-operation", "operation": "enqueue", "sessionId": SESSION, "content": text}))


READ_REFUSED = Step(pre(), REFUSE, Template("Read is refused while a background run owes this"))
READ_ALLOWED = Step(pre(), ALLOW)
EXPIRED = EventLine("[Monitor expired after 1800000ms]")


# Cases ---------------------------------------------------------------------------

CASES = [
    Case(CaseId("B01"), Prose("a heavy background run's launch notes the watch, and every other call is refused"), [
        Step(launch(), ALLOW, Template(f"command: {WATCHER} {LOG} 900")),
        Step(pre(), REFUSE, Template(f"run {RUN} (cargo build): it has no watch. Start the watch with the Monitor "
                                     f"tool:\n    command: {WATCHER} {LOG} 900")),
        Step(pre("Bash"), REFUSE, Template("Bash is refused")),
        Step(pre("Edit"), REFUSE, Template(f"The watch ends on the log's `EXIT=` line, which the launch appends with "
                                           f"`; echo \"EXIT=$?\" >> {LOG}`")),
    ]),
    Case(CaseId("B02"), Prose("Monitor, TaskStop and ToolSearch go through while a run owes"), [
        Step(launch(), ALLOW),
        Step(pre("Monitor"), ALLOW), Step(pre("TaskStop"), ALLOW), Step(pre("ToolSearch"), ALLOW),
    ]),
    Case(CaseId("B03"), Prose("the watcher on the run's log holds it"), [
        Step(launch(), ALLOW), Step(monitor(WATCH, watch_of()), ALLOW), READ_ALLOWED,
        Step(stop(), ALLOW),
    ]),
    Case(CaseId("B04"), Prose("a Monitor that is not the watcher holds nothing, and says nothing"), [
        Step(launch(), ALLOW),
        Step(monitor(WATCH, Template(f"tail -f {LOG} | grep --line-buffered EXIT")), ALLOW, lacks=Template("holds")),
        READ_REFUSED,
    ]),
    Case(CaseId("B05"), Prose("the watcher on another file holds nothing, and says why"), [
        Step(launch(), ALLOW),
        Step(monitor(WATCH, watch_of(LOG_2)), ALLOW, Template(f"this Monitor holds no run: {LOG_2} is no live run's "
                                                              "log")),
        READ_REFUSED,
    ]),
    Case(CaseId("B06"), Prose("a watcher not alone in its command, quoted, or built from a variable holds nothing"), [
        Step(launch(), ALLOW),
        Step(monitor(WATCH, Template(f"{WATCHER} {LOG} 900 | grep STALL")), ALLOW, Template("is not alone in it")),
        Step(monitor(WATCH, Template(f"{WATCHER} '{LOG}' 900")), ALLOW, Template("quotes or escapes a word")),
        Step(monitor(WATCH, Template(f"L={LOG}; {WATCHER} $L 900")), ALLOW, Template("is not alone in it")),
        Step(monitor(WATCH, Template(f"{WATCHER} $LOG 900")), ALLOW, Template("expands a variable")),
        READ_REFUSED,
    ]),
    Case(CaseId("B07"), Prose("a watch's silence and interval are held to their bounds"), [
        Step(launch(), ALLOW),
        Step(monitor(WATCH, watch_of(silence=Template("0"))), ALLOW, Template("its silence 0 is not a whole number")),
        Step(monitor(WATCH, watch_of(silence=Template("1801"))), ALLOW, Template("its silence 1801")),
        Step(monitor(WATCH, watch_of(silence=Template("90.5"))), ALLOW, Template("its silence 90.5")),
        Step(monitor(WATCH, watch_of(silence=Template("900 61"))), ALLOW, Template("its interval 61")),
        Step(monitor(WATCH, watch_of(silence=Template("900 0"))), ALLOW, Template("its interval 0")),
        Step(monitor(WATCH, watch_of(silence=Template("900 x"))), ALLOW, Template("its interval x is not a number")),
        Step(monitor(WATCH, Template(f"{WATCHER} {LOG}")), ALLOW, Template("is not `log-stall-watch LOG SILENCE")),
        READ_REFUSED,
        Step(monitor(WATCH, watch_of(silence=Template("1800 60"))), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B08"), Prose("a relative or dotted log, or another program, holds nothing"), [
        Step(launch(), ALLOW),
        Step(monitor(WATCH, Template(f"{WATCHER} logs/run.log 900")), ALLOW, Template("is not plain and absolute")),
        Step(monitor(WATCH, Template(f"{WATCHER} {{root}}/repo/logs/../logs/run.log 900")), ALLOW,
             Template("is not plain and absolute")),
        Step(monitor(WATCH, Template(f"/usr/bin/log-stall-watch {LOG} 900")), ALLOW,
             Template("its program is not the watcher by its path")),
        READ_REFUSED,
    ]),
    Case(CaseId("B09"), Prose("the watcher named through ~/ holds the run"), [
        Step(launch(), ALLOW), Step(monitor(WATCH, Template(f"~/{WATCHER_TAIL} {LOG} 900")), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B10"), Prose("an expired watch leaves the run owing again, and a new watch pays it"), [
        Step(launch(), ALLOW), Step(monitor(WATCH, watch_of()), ALLOW), READ_ALLOWED,
        Step(pre(), REFUSE, Template("its watch ended"), lines=(notice(WATCH, None, EXPIRED),)),
        Step(monitor(WATCH_2, watch_of()), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B11"), Prose("a watch that ended leaves the run owing again"), [
        Step(launch(), ALLOW), Step(monitor(WATCH, watch_of()), ALLOW),
        Step(pre(), REFUSE, Template("its watch ended"), lines=(notice(WATCH),)),
    ]),
    Case(CaseId("B12"), Prose("the run's end frees every call"), [
        Step(launch(), ALLOW), READ_REFUSED,
        Step(pre(), ALLOW, lines=(notice(RUN, "failed"),)),
    ]),
    Case(CaseId("B13"), Prose("a TaskStop of the run frees every call; one of its watch leaves it owing"), [
        Step(launch(), ALLOW), Step(monitor(WATCH, watch_of()), ALLOW),
        Step(task_stop(WATCH), ALLOW), READ_REFUSED,
        Step(task_stop(RUN), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B14"), Prose("the turn's end is kept going once while a run owes, again once its watch ends"), [
        Step(launch(), ALLOW),
        Step(stop(), REFUSE, Template("the turn does not end yet: a background run owes this")),
        Step(stop(), ALLOW),
        Step(monitor(WATCH, watch_of()), ALLOW), Step(stop(), ALLOW),
        Step(stop(), REFUSE, Template("its watch ended"), lines=(notice(WATCH, None, EXPIRED),)),
        Step(stop(), ALLOW),
    ]),
    Case(CaseId("B15"), Prose("a run the Stop event does not list as in flight has ended"), [
        Step(launch(), ALLOW),
        Step(stop([BackgroundTask(id=RUN, status="completed")]), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B16"), Prose("a run the Stop event lists as running still owes"), [
        Step(launch(), ALLOW),
        Step(stop([BackgroundTask(id=RUN, status="running")]), REFUSE, Template("a background run owes")),
    ]),
    Case(CaseId("B17"), Prose("a background call running nothing heavy owes nothing"), [
        Step(launch(command=Template("pr-checks-watch 450 --record {root}/pr450.log")), ALLOW, lacks=Template("owes")),
        READ_ALLOWED, Step(stop(), ALLOW),
    ]),
    Case(CaseId("B18"), Prose("a heavy call in the foreground owes nothing"), [
        Step(launch(background=False), ALLOW, lacks=Template("owes")), READ_ALLOWED,
    ]),
    Case(CaseId("B19"), Prose("a subagent's launch is not recorded"), [
        Step(Event(**launch(), agent_id=AgentId("agent-1")), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B20"), Prose("a new process drops the state; a clear or a compaction keeps it"), [
        Step(launch(), ALLOW),
        Step(session_start("clear"), ALLOW), READ_REFUSED,
        Step(session_start("compact"), ALLOW), READ_REFUSED,
        Step(session_start("startup"), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B21"), Prose("the kill switch lets every call through and records still; writing it goes through"), [
        Step(launch(), ALLOW),
        Step(pre("Write", Template("{home}/" + KILL_SWITCH_TAIL)), ALLOW),
        Step(pre(), ALLOW, act=kill_switch_on), Step(stop(), ALLOW),
        Step(pre(), REFUSE, Template("Read is refused"), act=kill_switch_off),
    ]),
    Case(CaseId("B22"), Prose("a watcher that cannot run stands the guard down, saying why"), [
        Step(launch(), REPORTED, Template("no watch can be demanded"), act=watcher_not_executable),
        Step(pre(), REPORTED, Template(f"the watcher {{home}}/{WATCHER_TAIL} is not executable")),
    ]),
    Case(CaseId("B23"), Prose("the log is resolved where the heavy command runs, the event's directory aside"), [
        Step(Event(**launch(), cwd=Template("{root}")), ALLOW, Template(f"command: {WATCHER} {LOG} 900")),
        Step(monitor(WATCH, watch_of()), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B24"), Prose("a run whose output ends in no file is held by any watch, and says no log was read"), [
        Step(launch(command=Template("cd {root}/repo && taskset -c 0-19 cargo build")), ALLOW,
             Template("no log of it was read")),
        READ_REFUSED,
        Step(monitor(WATCH, watch_of(LOG_2)), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B25"), Prose("a run whose end was read before its launch is not recorded"), [
        Step(pre("Bash"), ALLOW, lines=(notice(RUN),)),
        Step(launch(), ALLOW), Step(stop(), ALLOW),
    ]),
    Case(CaseId("B26"), Prose("two runs: a watch on one leaves the other owing, named alone"), [
        Step(launch(), ALLOW), Step(launch(RUN_2, HEAVY_2), ALLOW),
        Step(monitor(WATCH, watch_of()), ALLOW),
        Step(pre(), REFUSE, Template(f"run {RUN_2} (cargo test): it has no watch"),
             lacks=Template(f"run {RUN} ")),
        Step(monitor(WATCH_2, watch_of(LOG_2)), ALLOW), READ_ALLOWED,
    ]),
    Case(CaseId("B27"), Prose("a watch started before its run's launch is recorded does not hold the run"), [
        Step(launch(), ALLOW), Step(monitor(WATCH, watch_of()), ALLOW),
        Step(launch(RUN_2, HEAVY), ALLOW, Template(f"run {RUN_2}"), lines=(notice(RUN),)),
        Step(pre(), REFUSE, Template(f"run {RUN_2} (cargo build): it has no watch")),
    ]),
    Case(CaseId("B28"), Prose("a state file of another shape is read as empty"), [
        Step(launch(), ALLOW),
        Step(pre(), ALLOW, act=corrupt_state),
    ]),
    Case(CaseId("B29"), Prose("an event the guard does not read goes through"), [
        Step(Event(hook_event_name="PreToolUse"), ALLOW),
    ]),
    Case(CaseId("B31"), Prose("a watch whose end was read before its start holds nothing, and says why"), [
        Step(launch(), ALLOW),
        Step(monitor(WATCH, watch_of()), ALLOW, Template("it ended before its start was recorded"),
             lines=(notice(WATCH),)),
        READ_REFUSED,
    ]),
    Case(CaseId("B32"), Prose("a run whose log has no plain spelling is told to start again"), [
        Step(launch(command=Template('cd {root}/repo && taskset -c 0-19 cargo build > "logs/run log.log" 2>&1')),
             ALLOW, Template("has no plain spelling a watch can name. Stop it with TaskStop, task_id brunaaaaa")),
        READ_REFUSED,
    ]),
]


# The runner ----------------------------------------------------------------------

def world_for(case: Case) -> World:
    root = BASE / case.id
    home, tmp = root / "home", root / "tmp"
    for directory in (home / ".local" / "bin", tmp, root / "repo" / "logs"):
        directory.mkdir(parents=True)
    watcher = home / WATCHER_TAIL
    watcher.write_text("#!/bin/sh\n")
    watcher.chmod(0o755)
    transcript = root / "transcript.jsonl"
    transcript.write_text("")
    return World(root, home, tmp, transcript)


def filled(text: Template, world: World) -> Template:
    return Template(text.replace("{home}", str(world.home)).replace("{root}", str(world.root)))


def answer(event: Event, world: World) -> tuple[ExitStatus, Prose, Prose]:
    payload = {"session_id": SESSION, "transcript_path": str(world.transcript), "cwd": str(world.root / "repo"),
               **json.loads(filled(Template(json.dumps(event)), world))}
    env = {**os.environ, "HOME": str(world.home), "TMPDIR": str(world.tmp)}
    out = subprocess.run([str(GUARD)], input=json.dumps(payload), capture_output=True, text=True, env=env,
                         check=False)
    return ExitStatus(out.returncode), Prose(out.stdout), Prose(out.stderr)


def wrong_in(step: Step, world: World, status: ExitStatus, stdout: Prose, stderr: Prose) -> list[Prose]:
    wrong: list[Prose] = []
    if status != step.want:
        wrong.append(Prose(f"exit {status}, wanted {step.want}"))
    said = stderr if status != ALLOW else Prose("")
    if status == ALLOW and stderr:
        wrong.append(Prose(f"stderr while the call goes through: {stderr[:200]!r}"))
    if status != ALLOW and stdout:
        wrong.append(Prose(f"stdout beside a refusal: {stdout[:200]!r}"))
    if status == ALLOW and stdout:
        try:
            said = Prose(json.loads(stdout)["hookSpecificOutput"]["additionalContext"])
        except (ValueError, KeyError, TypeError):
            wrong.append(Prose(f"stdout is not the one note shape: {stdout[:200]!r}"))
    says, lacks = filled(step.says, world), filled(step.lacks, world)
    if says and says not in said:
        wrong.append(Prose(f"the answer lacks {says!r}: {said[:600]!r}"))
    if lacks and lacks in said:
        wrong.append(Prose(f"the answer holds {lacks!r}: {said[:600]!r}"))
    return wrong


def run_case(case: Case) -> list[Prose]:
    world = world_for(case)
    wrong: list[Prose] = []
    for number, step in enumerate(case.steps, 1):
        if step.lines:
            with world.transcript.open("a", encoding="utf-8") as handle:
                handle.writelines(line + "\n" for line in step.lines)
        if step.act is not None:
            step.act(world)
        status, stdout, stderr = answer(step.event, world)
        wrong += [Prose(f"step {number}: {each}") for each in wrong_in(step, world, status, stdout, stderr)]
    return wrong


def not_json() -> list[Prose]:
    out = subprocess.run([str(GUARD)], input="not json", capture_output=True, text=True, check=False)
    return [] if out.returncode == 0 and not out.stdout and not out.stderr else [
        Prose(f"text that is not JSON gave {out.returncode}, {out.stderr!r}")]


def main() -> ExitStatus:
    checks = [(case.id, case.what, lambda case=case: run_case(case)) for case in CASES]
    checks.append((CaseId("B30"), Prose("text that is not JSON goes through, silently"), not_json))
    bad = 0
    for identity, what, check in checks:
        try:
            wrong = check()
        except Exception as exc:  # noqa: BLE001 - a case that raises disagrees; the others still run
            wrong = [Prose(f"raised {type(exc).__name__}: {exc}")]
        print(f"{'MISS' if wrong else 'ok  '} {identity:<4} {what}")
        for line in wrong:
            print(f"     {line}")
        bad += bool(wrong)
    print(f"{len(checks)} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
