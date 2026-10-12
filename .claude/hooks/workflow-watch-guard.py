#!/home/nicolas/.local/bin/python3.14
"""Hold every workflow run of the main thread to a live token watch, from launch to end.

THE RULE (user, 2026-10-03, ~/.claude/CLAUDE.md): every workflow is watched from its
launch by the Monitor tool running `~/.local/bin/workflow-token-watch <transcript dir>`,
re-armed each time the Monitor expires (after 30 minutes at most) until the run ends; on
the watcher's OVER LIMIT line, past 2,000,000 new tokens, the run is stopped at once
with TaskStop. A note in the agent's instructions holds only while the agent remembers
it; this hook holds it on every call.

WHAT IT RECORDS, after a tool call of the main thread (a subagent's events carry
agent_id: the workflow's own agents are not held to their run's watch):
  Workflow  the run: its task id and transcript directory, from the tool's response. A
            run launched on a directory a live watch covers is watched at once, and one
            launched on a directory a watch reported over its ceiling earlier in the
            session is over at once: a resumed run shares its directory, and the watcher
            counts the directory's every agent, so a resumed run is held to the tokens of
            all its attempts.
  Monitor   a watch: the Monitor's task id and the directory, when the command is the
            watcher alone, written as the shell will read it: one simple command of
            plain words (no quote, escape, glob, brace or variable other than a leading
            `~/`, `$HOME/` or `${HOME}/`), the watcher by its absolute path, the
            directory by its absolute path with no `.` or `..` part, then at most a
            ceiling from 1 to 2,000,000 and an interval above 0 and at most 60 seconds.
            A watch is kept whether or not a run is recorded on its directory yet. While
            a run is recorded, a Monitor that names the watcher and misses one of these,
            or watches no live run's directory, is told so.
  TaskStop  the end of the run or the watch it stopped.

WHAT ENDS A RUN OR A WATCH. The session transcript records every background task's
notice as a `queue-operation` enqueue line holding one notice, whose task and status
are read from its head, before its summary: a status (completed, failed, killed,
stopped) ends the task; an event line starting "[Monitor expired" ends a watch, and one
starting "OVER LIMIT" marks the watch's directory, and every run on it, over. A task
whose end is read before its own result is recorded is not recorded. The Stop event
lists the tasks still in flight: a run or a watch it does not list as such has ended. A
SessionStart of a new process (startup or resume) drops the session's state, since
nothing the last process launched still runs.

WHAT IT REFUSES. Before every tool call of the main thread, while a run is live with no
live watch on its directory, or over its ceiling: every call but Monitor, TaskStop and
ToolSearch (which loads the two when they are deferred), at exit 2 with the call that
pays each debt. At the end of a turn (Stop), the same debts keep the turn going, each
run at most once while it lacks a watch and three times while it is over its ceiling
(whose cure is one TaskStop); a run's count starts again whenever it owes nothing.
Every call is still refused while a debt stands, but a debt the agent cannot pay never
loops the session.

KILL SWITCH: while ~/.claude/workflow-watch-guard.off exists the hook refuses nothing
(it still records), and a Write or Edit of that file goes through whatever is owed, so a
guard in error can be turned off from inside the session. While the watcher is not
executable, or has no plain path to name it by, no watch can be demanded: the hook then
lets through, saying so, every call a missing watch would have refused; a run over its
ceiling is still refused, since its cure needs no watcher.

GAPS IT LEAVES. A workflow launched by a subagent is not tracked. A call issued in the
same block as the launch is checked before the launch's result is recorded. An OVER
LIMIT read before its watch is recorded is not kept.

STATE lives in the temporary directory, one file per session, read and written under
the directory's lock; a session's file is removed once no run, watch or over directory
is recorded, and a file of another shape is read as empty. FAILURE: an event the hook
cannot read is allowed; an error of the hook's own allows the call and says so on stderr
with exit 1, as the kit's other guards do: a hook that fails must never block the
session silently.
"""

import fcntl
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, cast

import _shell

# A background task's id; a path as an event spells it; a directory; a shell command
# line, or a word of one; a session's id; a notice's status or event line; prose the
# hook prints; a byte offset into the transcript; a watch's token ceiling; its interval
# in seconds; how many turn ends a run's debt has had refused; the hook's answer: 0 lets
# the call through, 2 refuses it (or keeps the turn going), 1 reports an error or a
# stand-down.
TaskId = NewType("TaskId", str)
FilePath = NewType("FilePath", str)
DirPath = NewType("DirPath", str)
CommandLine = NewType("CommandLine", str)
Word = NewType("Word", str)
SessionId = NewType("SessionId", str)
Status = NewType("Status", str)
EventText = NewType("EventText", str)
Prose = NewType("Prose", str)
Offset = NewType("Offset", int)
Ceiling = NewType("Ceiling", int)
Seconds = NewType("Seconds", float)
Count = NewType("Count", int)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type HookEvent = Literal["PreToolUse", "PostToolUse", "Stop", "SessionStart"]

STATE_DIR = Path(tempfile.gettempdir()) / "claude-workflow-watch-guard"
KILL_SWITCH = Path.home() / ".claude" / "workflow-watch-guard.off"
WATCHER = Path.home() / ".local" / "bin" / "workflow-token-watch"
CEILING = Ceiling(2_000_000)
LONGEST_INTERVAL = Seconds(60.0)
MONITOR_TIMEOUT_MS = 1_800_000
STOPS_REFUSED = {False: Count(1), True: Count(3)}  # turn ends a run's debt may keep going: no watch, over
ENDED_KEPT = 64  # ends read before their task was recorded, newest kept
ALWAYS_ALLOWED = frozenset({"Monitor", "TaskStop", "ToolSearch"})
TERMINAL = frozenset({"completed", "failed", "killed", "stopped"})
NEW_PROCESS = frozenset({"startup", "resume"})
OPERATORS = frozenset(";&|()<>\n")
QUOTES = frozenset("'\"\\")
HOME_PREFIX = re.compile(r"^(?:~|\$HOME|\$\{HOME\})(?=/)")
PLAIN = re.compile(r"[A-Za-z0-9_./+,@%:-]+")  # characters both shells pass on as written
NOTICE_START = "<task-notification>"
TASK = re.compile(r"<task-id>([^<]*)</task-id>")
STATUS = re.compile(r"<status>([^<]*)</status>")
EXPIRED = "[Monitor expired"
OVER_LIMIT = "OVER LIMIT"


class Run(TypedDict):
    """A live run: its directory resolved, the plain spelling of it a refusal names,
    whether a watch ever covered it, whether it is over its ceiling, and how many turn
    ends its present debt has kept going."""

    directory: DirPath
    named: DirPath
    armed: bool
    over: bool
    refused: Count


class State(TypedDict):
    """What the hook keeps for one session."""

    transcript: FilePath
    offset: Offset
    runs: dict[TaskId, Run]
    watches: dict[TaskId, DirPath]
    over_dirs: list[DirPath]
    ended: list[TaskId]
    note: Prose


class Notice(NamedTuple):
    """One task notification: the task, its status when it ended, its event lines."""

    task: TaskId
    status: Status | None
    lines: list[EventText]


class Watch(NamedTuple):
    """What a Monitor command watches: a directory, or None with the reason it does not count."""

    directory: DirPath | None
    reason: Prose


class Demand(NamedTuple):
    """The spelling of the watcher a refusal names, or None with the reason no watch can be demanded."""

    watcher: Word | None
    reason: Prose


class Decision(NamedTuple):
    """An answer to a PreToolUse or a Stop: its status, and what it prints on stderr."""

    status: ExitStatus
    message: Prose | None = None


class WorkflowResponse(TypedDict, total=False):
    taskId: TaskId
    transcriptDir: FilePath


class MonitorInput(TypedDict, total=False):
    command: CommandLine


class MonitorResponse(TypedDict, total=False):
    taskId: TaskId


class StopInput(TypedDict, total=False):
    task_id: TaskId
    shell_id: TaskId


class StopResponse(TypedDict, total=False):
    task_id: TaskId


class WriteInput(TypedDict, total=False):
    file_path: FilePath


class BackgroundTask(TypedDict, total=False):
    id: TaskId
    status: Status


class Event(TypedDict, total=False):
    """The event the harness hands the hook, as far as the hook reads it."""

    hook_event_name: HookEvent
    session_id: SessionId
    transcript_path: FilePath
    agent_id: TaskId
    tool_name: Word
    tool_input: MonitorInput | StopInput | WriteInput
    tool_response: WorkflowResponse | MonitorResponse | StopResponse | Prose
    source: Word
    background_tasks: list[BackgroundTask]


class QueueEntry(TypedDict, total=False):
    """A transcript line that queues a notice."""

    type: Literal["queue-operation"]
    operation: Literal["enqueue", "remove"]
    content: Prose


# Paths and the watch command -----------------------------------------------------


def expanded(word: Word) -> Word:
    """The word with a leading `~/`, `$HOME/` or `${HOME}/` taken from the home directory."""
    return Word(HOME_PREFIX.sub(lambda _: str(Path.home()), word))


def plain_path(path: Word) -> bool:
    """Say whether a path is one the shell passes on as written: plain characters,
    absolute, with no `.` or `..` part."""
    return bool(PLAIN.fullmatch(path)) and path.startswith("/") and not {".", ".."} & set(path.split("/"))


def resolved(path: Word) -> DirPath:
    return DirPath(os.path.realpath(path))


def named_watcher() -> Word | None:
    """The plain spelling of the watcher a refusal names: its path, else the path it links to."""
    return next((spelling for spelling in (Word(str(WATCHER)), Word(os.path.realpath(WATCHER)))
                 if plain_path(spelling)), None)


def watch_of(command: CommandLine) -> Watch:
    """Read a Monitor command as a watch: the directory it watches, or why it is not one."""
    text = command.strip(" \t")
    if OPERATORS & set(text):
        return Watch(None, Prose("the watcher is not alone in it: no pipe, list, redirection, subshell or second line"))
    if QUOTES & set(text):
        return Watch(None, Prose("it quotes or escapes a word: give every word bare"))
    words = [Word(word) for word in re.split(r"[ \t]+", text)] if text else []
    if any("$" in HOME_PREFIX.sub("", word) or "`" in word for word in words):
        return Watch(None, Prose("it expands a variable or a command other than a leading $HOME/"))
    if not 2 <= len(words) <= 4:
        return Watch(None, Prose("it is not `workflow-token-watch DIR [CEILING [SECONDS]]`"))
    program, directory = expanded(words[0]), expanded(words[1])
    if not (plain_path(program) and plain_path(directory)):
        return Watch(None, Prose("the watcher's or the directory's path is not plain and absolute: no glob, brace, "
                                 "`#`, `~` inside, control character, relative path, or `.` or `..` part"))
    if resolved(program) != resolved(Word(str(WATCHER))):
        return Watch(None, Prose(f"its program is not the watcher by its path, {WATCHER}"))
    if len(words) > 2:
        try:
            ceiling = Ceiling(int(words[2]))
        except ValueError:
            return Watch(None, Prose(f"its ceiling {words[2]} is not a whole number"))
        if not 0 < ceiling <= CEILING:
            return Watch(None, Prose(f"its ceiling {ceiling:,} is not between 1 and {CEILING:,}"))
    if len(words) > 3:
        try:
            interval = Seconds(float(words[3]))
        except ValueError:
            return Watch(None, Prose(f"its interval {words[3]} is not a number"))
        if not 0 < interval <= LONGEST_INTERVAL:
            return Watch(None, Prose(f"its interval {words[3]} is not above 0 and at most {LONGEST_INTERVAL:g} "
                                     "seconds"))
    return Watch(resolved(directory), Prose(""))


def mentions_watcher(command: CommandLine) -> bool:
    return WATCHER.name in command


def demand() -> Demand:
    """The watcher a refusal can name, or why no watch can be demanded now."""
    if not os.access(os.path.realpath(WATCHER), os.X_OK):
        return Demand(None, Prose(f"the watcher {WATCHER} is not executable"))
    spelling = named_watcher()
    if spelling is None:
        return Demand(None, Prose(f"the watcher {WATCHER} has no plain path to name it by"))
    return Demand(spelling, Prose(""))


# Notices -----------------------------------------------------------------------


def event_lines(text: Prose) -> list[EventText]:
    """The lines of every closed <event> in a notice's text, read in one pass."""
    return [EventText(line) for part in text.split("</event>")[:-1] if "<event>" in part
            for line in part.rpartition("<event>")[2].splitlines()]


def notice_in(content: Prose) -> Notice | None:
    """Read the one notice a queued text holds. Its task and status come from its head,
    before its summary, so a result quoting other notices is not read as one."""
    if not content.startswith(NOTICE_START):
        return None
    head, _, rest = content.partition("<summary>")
    task, status = TASK.search(head), STATUS.search(head)
    if task is None:
        return None
    return Notice(TaskId(task.group(1)), Status(status.group(1)) if status else None, event_lines(Prose(rest)))


def read_notices(path: Path, offset: Offset) -> tuple[list[Notice], Offset]:
    """Read the notices queued after ``offset``; return them and where reading stopped."""
    found: list[Notice] = []
    if path.stat().st_size < offset:
        offset = Offset(0)  # the transcript was replaced: read it again
    with path.open("rb") as handle:
        handle.seek(offset)
        position = offset
        for raw in handle:
            if not raw.endswith(b"\n"):
                break  # a line still being written is read next time
            position = Offset(position + len(raw))
            if NOTICE_START.encode() not in raw:
                continue
            try:
                entry = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            queued = cast("QueueEntry", entry) if isinstance(entry, dict) else QueueEntry()
            content = queued.get("content")
            if queued.get("type") == "queue-operation" and queued.get("operation") == "enqueue" \
                    and isinstance(content, str):
                notice = notice_in(content)
                if notice is not None:
                    found.append(notice)
    return found, position


def apply(state: State, notices: list[Notice]) -> None:
    """End the runs and watches the notices end, mark what a watch reports over, and
    remember the ends of tasks not recorded yet."""
    for notice in notices:
        ends = notice.status in TERMINAL
        if notice.task in state["watches"]:
            directory = state["watches"][notice.task]
            if any(line.startswith(OVER_LIMIT) for line in notice.lines):
                if directory not in state["over_dirs"]:
                    state["over_dirs"].append(directory)
                for run in state["runs"].values():
                    if run["directory"] == directory:
                        run["over"] = True
            if ends or any(line.startswith(EXPIRED) for line in notice.lines):
                del state["watches"][notice.task]
        elif notice.task in state["runs"]:
            if ends:
                del state["runs"][notice.task]
        elif ends or any(line.startswith(EXPIRED) for line in notice.lines):
            state["ended"] = [*state["ended"], notice.task][-ENDED_KEPT:]


def catch_up(state: State, transcript: FilePath | None) -> None:
    """Read the session transcript's new notices into the state."""
    if transcript and transcript != state["transcript"]:
        state["transcript"], state["offset"] = transcript, Offset(0)
    path = Path(state["transcript"]) if state["transcript"] else None
    if path is not None and path.is_file():
        notices, state["offset"] = read_notices(path, state["offset"])
        apply(state, notices)


# State -------------------------------------------------------------------------


def state_path(session: SessionId) -> Path:
    return STATE_DIR / ("session-" + re.sub(r"[^\w-]", "_", session)[:160] + ".json")


def fresh(transcript: FilePath | None) -> State:
    return State(transcript=transcript or FilePath(""), offset=Offset(0), runs={}, watches={}, over_dirs=[],
                 ended=[], note=Prose(""))


def counted(value: Count | Offset | None) -> bool:
    return type(value) is int and value >= 0


def well_formed(data: State) -> bool:
    """Say whether a state read back has the shape the hook writes."""
    return (isinstance(data, dict) and isinstance(data.get("transcript"), str) and counted(data.get("offset"))
            and isinstance(data.get("note"), str)
            and isinstance(data.get("runs"), dict) and isinstance(data.get("watches"), dict)
            and isinstance(data.get("over_dirs"), list) and isinstance(data.get("ended"), list)
            and all(isinstance(run, dict) and isinstance(run.get("directory"), str)
                    and isinstance(run.get("named"), str) and counted(run.get("refused"))
                    and type(run.get("armed")) is bool and type(run.get("over")) is bool
                    for run in data["runs"].values())
            and all(isinstance(directory, str) for directory in [*data["watches"].values(), *data["over_dirs"]])
            and all(isinstance(task, str) for task in data["ended"]))


@contextmanager
def locked() -> Iterator[None]:
    """Hold the state directory's lock."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = os.open(STATE_DIR, os.O_RDONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)


@contextmanager
def held(session: SessionId, transcript: FilePath | None) -> Iterator[State]:
    """Hold the session's state under the lock; settle its runs' counts; write it back,
    or remove it once no run, watch or over directory is recorded."""
    path = state_path(session)
    with locked():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        state = cast("State", data) if well_formed(cast("State", data)) else fresh(transcript)
        yield state
        owed = {task for task, _ in owing(state)}
        for task, run in state["runs"].items():
            if task not in owed:
                run["refused"] = Count(0)
        if state["runs"] or state["watches"] or state["over_dirs"]:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state), encoding="utf-8")
            temporary.replace(path)
        else:
            path.unlink(missing_ok=True)


def dropped(session: SessionId) -> None:
    """Remove the session's state under the lock."""
    with locked():
        state_path(session).unlink(missing_ok=True)


# What is owed ------------------------------------------------------------------


def watched(state: State, directory: DirPath) -> bool:
    return directory in state["watches"].values()


def owing(state: State) -> list[tuple[TaskId, Run]]:
    """The live runs that owe something: a watch, or their stop."""
    return [(task, run) for task, run in sorted(state["runs"].items())
            if run["over"] or not watched(state, run["directory"])]


def payable(state: State, demanded: Demand) -> list[tuple[TaskId, Run]]:
    """The owing runs whose debt can be demanded now: every run over its ceiling, and the
    runs missing a watch while a watcher can be named."""
    return [(task, run) for task, run in owing(state) if run["over"] or demanded.watcher is not None]


def finding(state: State, lead: Prose, demanded: Demand) -> Prose:
    """What the payable runs owe, each with the call that pays it, and the stand-down for the rest."""
    lines: list[Prose] = []
    for task, run in payable(state, demanded):
        if run["over"]:
            lines.append(Prose(f"run {task} ({run['named']}): its watch reported OVER LIMIT. Stop the run now "
                               f"with TaskStop, task_id {task}."))
        else:
            why = "its watch ended" if run["armed"] else "it has no watch"
            lines.append(Prose(f"run {task}: {why}. Start the watch with the Monitor tool:\n"
                               f"    command: {demanded.watcher} {run['named']}\n"
                               f"    timeout_ms: {MONITOR_TIMEOUT_MS}, re-armed on each expiry while the run lasts"))
    note = f"\nThe last Monitor that named the watcher did not count: {state['note']}." if state["note"] else ""
    rest = f"\n{standing_down(demanded)}" if len(lines) < len(owing(state)) else ""
    return Prose(f"workflow-watch-guard: {lead}\n" + "\n".join(f"  {each}" for each in lines)
                 + f"\nOn the watcher's OVER LIMIT line, stop the run at once with TaskStop. Monitor, TaskStop and "
                 f"ToolSearch go through meanwhile.{note}{rest}")


def standing_down(demanded: Demand) -> Prose:
    return Prose(f"workflow-watch-guard: no watch can be demanded, so no call is refused for a missing one: "
                 f"{demanded.reason}")


def context(event_name: HookEvent, text: Prose) -> None:
    """A note the model reads beside the call, at exit 0."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}))


# The events --------------------------------------------------------------------


def launched(state: State, response: WorkflowResponse | MonitorResponse | StopResponse | Prose | None) -> None:
    """Record a run from the Workflow tool's response; raise when it names no task id
    and directory, or no plain spelling of the directory."""
    launch = cast("WorkflowResponse", response) if isinstance(response, dict) else WorkflowResponse()
    task, given = launch.get("taskId"), launch.get("transcriptDir")
    if not isinstance(task, str) or not task or not isinstance(given, str) or not given:
        raise RuntimeError("the Workflow result names no task id and transcript dir; the run is not recorded")
    directory = resolved(Word(given))
    named = next((spelling for spelling in (Word(given), Word(directory)) if plain_path(spelling)), None)
    if named is None:
        raise RuntimeError(f"the run's transcript dir {given} has no plain spelling a watch could be written with; "
                           "the run is not recorded")
    if task not in state["ended"]:
        state["runs"][task] = Run(directory=directory, named=DirPath(named), armed=watched(state, directory),
                                  over=directory in state["over_dirs"], refused=Count(0))
        if watched(state, directory):
            state["note"] = Prose("")


def armed(state: State, watch: Watch, task: TaskId | None) -> Prose | None:
    """Record a Monitor that is a watch; return why one naming the watcher does not hold a run."""
    note = Prose("")
    if watch.directory is None:
        note = watch.reason
    elif not task:
        note = Prose("the Monitor's result names no task id")
    elif task in state["ended"]:
        note = Prose("it ended before its start was recorded")
    else:
        state["watches"][task] = watch.directory
        for run in state["runs"].values():
            if run["directory"] == watch.directory:
                run["armed"] = True
        if not any(run["directory"] == watch.directory for run in state["runs"].values()):
            note = Prose(f"{watch.directory} is no live run's transcript dir")
    state["note"] = note if state["runs"] else Prose("")
    return Prose(f"workflow-watch-guard: this Monitor does not hold a run: {state['note']}.") if state["note"] else None


def stopped(state: State, event: Event) -> None:
    """End the run or the watch a TaskStop stopped."""
    request = cast("StopInput", event.get("tool_input") or {})
    response = event.get("tool_response")
    task = (cast("StopResponse", response).get("task_id") if isinstance(response, dict) else None) \
        or request.get("task_id") or request.get("shell_id")
    if task:
        state["runs"].pop(task, None)
        state["watches"].pop(task, None)


def reconciled(state: State, tasks: list[BackgroundTask]) -> None:
    """Keep only the runs and watches the Stop event lists as still in flight."""
    live = {each.get("id") for each in tasks if isinstance(each, dict) and each.get("status") not in TERMINAL}
    state["runs"] = {task: run for task, run in state["runs"].items() if task in live}
    state["watches"] = {task: directory for task, directory in state["watches"].items() if task in live}


def kill_switch_write(event: Event) -> bool:
    if event.get("tool_name") not in ("Write", "Edit"):
        return False
    target = cast("WriteInput", event.get("tool_input") or {}).get("file_path")
    return isinstance(target, str) and os.path.realpath(target) == os.path.realpath(KILL_SWITCH)


def after_tool(event: Event, session: SessionId, transcript: FilePath | None) -> Decision:
    """Record what a Workflow, Monitor or TaskStop call started or ended."""
    tool = event.get("tool_name")
    note: Prose | None = None
    told: Prose | None = None
    if tool == "Workflow":
        with held(session, transcript) as state:
            launched(state, event.get("tool_response"))
            catch_up(state, transcript)
            if owing(state) and not KILL_SWITCH.exists():
                demanded = demand()
                if payable(state, demanded):
                    note = finding(state, Prose("before any other call, a workflow run owes this:"), demanded)
                else:
                    told = standing_down(demanded)
    elif tool == "Monitor":
        command = cast("MonitorInput", event.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or not mentions_watcher(command):
            return Decision(ALLOW)
        watch = watch_of(command)
        if watch.directory is None and not state_path(session).exists():
            return Decision(ALLOW)  # not a watch, and no run to tell
        response = event.get("tool_response")
        task = cast("MonitorResponse", response).get("taskId") if isinstance(response, dict) else None
        with held(session, transcript) as state:
            note = armed(state, watch, task)
    elif tool == "TaskStop" and state_path(session).exists():
        with held(session, transcript) as state:
            catch_up(state, transcript)  # first: a stopped watch's last notice may mark its runs over
            stopped(state, event)
    if note:
        context("PostToolUse", note)
    return Decision(REPORTED, told) if told else Decision(ALLOW)


def decide(state: State, event: Event) -> Decision:
    """Answer a PreToolUse or a Stop from the state."""
    owed = owing(state)
    if KILL_SWITCH.exists():
        return Decision(ALLOW)
    is_stop = event.get("hook_event_name") == "Stop"
    if not is_stop and (event.get("tool_name") in ALWAYS_ALLOWED or kill_switch_write(event)):
        return Decision(ALLOW)
    demanded = demand()
    payable_now = payable(state, demanded)
    idle = Decision(REPORTED, standing_down(demanded)) if len(payable_now) < len(owed) else Decision(ALLOW)
    if is_stop:
        due = [run for _, run in payable_now if run["refused"] < STOPS_REFUSED[run["over"]]]
        if not due:
            return idle
        for run in due:
            run["refused"] = Count(run["refused"] + 1)
        return Decision(REFUSE, finding(state, Prose("the turn does not end yet: a workflow run owes this:"),
                                        demanded))
    if not payable_now:
        return idle
    return Decision(REFUSE, finding(state, Prose(f"{event.get('tool_name')} is refused while a workflow run owes "
                                                 "this:"), demanded))


def before(event: Event, session: SessionId, transcript: FilePath | None) -> Decision:
    """Answer a PreToolUse or a Stop: refuse while a run owes its watch or its stop."""
    if not state_path(session).exists():
        return Decision(ALLOW)
    with held(session, transcript) as state:
        catch_up(state, transcript)  # first: a watch's last notice may mark its runs over
        tasks = event.get("background_tasks")
        if event.get("hook_event_name") == "Stop" and isinstance(tasks, list):
            reconciled(state, tasks)
        return decide(state, event)


def handle(event: Event) -> Decision:
    name = event.get("hook_event_name")
    session = event.get("session_id") or SessionId("none")
    transcript = event.get("transcript_path")
    if name == "SessionStart":
        if event.get("source") in NEW_PROCESS:
            dropped(session)
        return Decision(ALLOW)
    if event.get("agent_id"):
        return Decision(ALLOW)
    if name == "PostToolUse":
        return after_tool(event, session, transcript)
    return before(event, session, transcript)


def main() -> ExitStatus:
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return ALLOW  # a hook that cannot read the event must not block the session
    if not isinstance(event, dict) or event.get("hook_event_name") not in ("PreToolUse", "PostToolUse", "Stop",
                                                                            "SessionStart"):
        return ALLOW
    try:
        decision = handle(cast("Event", event))
    except Exception as exc:  # noqa: BLE001 - an unguarded call is reported, never silent
        print(f"workflow-watch-guard: the call went unchecked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return REPORTED
    if decision.message is not None:
        print(decision.message, file=sys.stderr)
    return decision.status


if __name__ == "__main__":
    sys.exit(main())
