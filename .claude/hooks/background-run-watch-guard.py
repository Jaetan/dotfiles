#!/home/nicolas/.local/bin/python3.14
"""Hold every heavy background run of the main thread to a live stall watch, from launch to end.

THE RULE (user, 2026-10-25): a heavy run started in the background gets, the turn it starts, a Monitor
running `~/.local/bin/log-stall-watch LOG SILENCE`, which says when the run's log has gone SILENCE
seconds without growing and ends once the log ends in the run's `EXIT=` line, re-armed each time the
Monitor expires (after 30 minutes at most) while the run lasts. The watch is that one program, because
only a watch that reads how long the log has been silent catches a hang (user, 2026-10-25: *a guard
that requires a check of how long the log has been silent*), and a program is what the hook can check
a Monitor runs. A run that hangs sends no notice, so nothing else says it hung: on 2026-10-25 the
probe store hung four hours in one probe while the agent answered that it was waiting, and on
2026-10-10 a run of about 7 minutes was waited on for 81. The rule was a memory, kept only while
remembered; this hook holds it on every call.

WHAT IT RECORDS, after a tool call of the main thread (a subagent's events carry agent_id and are not
held):
  Bash      a run: a call whose result names a background task (backgroundTaskId: started with
            run_in_background, or moved there by the harness at its timeout) and whose command
            `_commands.expand` reads as running a heavy command, by the one table heavy-run-guard
            holds heavy commands to. Its log is the file the heavy command's standard output ends
            in, resolved where the command runs. A run whose command cannot be read whole, or whose
            output ends in no file, is recorded without a log.
  Monitor   a watch: the Monitor's task id and the live runs it holds, when the command is the
            watcher alone, written as the shell will read it: one simple command of plain words (no
            quote, escape, glob, brace or variable other than a leading `~/`, `$HOME/` or
            `${HOME}/`), the watcher by its absolute path, the log by its absolute path with no `.`
            or `..` part, a SILENCE from 1 to 1800 seconds, then at most an interval above 0 and at
            most 60 seconds. It holds the live runs whose log is that file, and a run without a log
            whatever file it names. While a run is recorded, a Monitor that names the watcher and
            holds no run is told why.
  TaskStop  the end of the run or the watch it stopped.

WHAT ENDS A RUN OR A WATCH. The session transcript records every background task's notice as a
`queue-operation` enqueue line holding one notice, whose task and status are read from its head,
before its summary: a status (completed, failed, killed, stopped) ends the task, and an event line
starting "[Monitor expired" ends a watch. A task whose end is read before its start is recorded is not
recorded. The Stop event lists the tasks still in flight: a run or a watch it does not list as such has
ended. A SessionStart of a new process (startup or resume) drops the session's state, since nothing the
last process started still runs.

WHAT IT REFUSES. Before every tool call of the main thread, while a live run has no live watch: every
call but Monitor, TaskStop and ToolSearch (which loads the two when they are deferred), at exit 2,
naming each run, its log and a watch to start. At the end of a turn (Stop), the same debt keeps the turn
going once for each run; a run's count starts again whenever it is watched.

KILL SWITCH: while ~/.claude/background-run-watch-guard.off exists the hook refuses nothing (it still
records), and a Write or Edit of that file goes through whatever is owed, so a guard in error can be
turned off from inside the session. While the watcher is not executable, or has no plain path to name
it by, no watch can be demanded: the hook then lets every call through, saying so.

GAPS IT LEAVES. A watch started before its run's start is recorded does not hold the run. A run whose
log has no plain spelling cannot be watched: it owes until it ends or is stopped. A heavy run a
subagent starts, or a script starts out of the reader's sight, is not tracked; one started with `&`
inside a call is not either, and heavy-run-guard refuses it.

STATE lives in the temporary directory, one file per session, read and written under the directory's
lock; a session's file is removed once no run or watch is recorded, and a file of another shape is read
as empty. FAILURE: an event the hook cannot read is allowed; an error of the hook's own allows the call
and says so on stderr with exit 1, as the kit's other guards do: a hook that fails must never block the
session silently. The notice reader and the locked state mirror workflow-watch-guard.py's.
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

# A background task's id; a path as an event spells it, or resolved; a shell command line, or a word of
# one; a session's id; a notice's status or event line; prose the hook prints; a byte offset into the
# transcript; how many turn ends a run's debt has kept going; a watch's interval in seconds; the hook's
# answer: 0 lets the call through, 2 refuses it (or keeps the turn going), 1 reports an error or a
# stand-down.
TaskId = NewType("TaskId", str)
FilePath = NewType("FilePath", str)
CommandLine = NewType("CommandLine", str)
Word = NewType("Word", str)
SessionId = NewType("SessionId", str)
Status = NewType("Status", str)
EventText = NewType("EventText", str)
Prose = NewType("Prose", str)
Offset = NewType("Offset", int)
Count = NewType("Count", int)
Seconds = NewType("Seconds", float)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type HookEvent = Literal["PreToolUse", "PostToolUse", "Stop", "SessionStart"]

STATE_DIR = Path(tempfile.gettempdir()) / "claude-background-run-watch-guard"
KILL_SWITCH = Path.home() / ".claude" / "background-run-watch-guard.off"
MONITOR_TIMEOUT_MS = 1_800_000
STOPS_REFUSED = Count(1)  # turn ends a run's missing watch may keep going
ENDED_KEPT = 64  # ends read before their task was recorded, newest kept
ALWAYS_ALLOWED = frozenset({"Monitor", "TaskStop", "ToolSearch"})
TERMINAL = frozenset({"completed", "failed", "killed", "stopped"})
NEW_PROCESS = frozenset({"startup", "resume"})
OUTPUT_OPS = frozenset({">", ">>", ">|", "&>", "&>>"})  # the redirections that send standard output to a file
WATCHER = Path.home() / ".local" / "bin" / "log-stall-watch"
SILENCE_NAMED = 900  # the silence a refusal's watch names, above the slowest step measured (the probe store's 487 s)
LONGEST_SILENCE = 1800
LONGEST_INTERVAL = Seconds(60.0)
OPERATORS = frozenset(";&|()<>\n")
QUOTES = frozenset("'\"\\")
HOME_PREFIX = re.compile(r"^(?:~|\$HOME|\$\{HOME\})(?=/)")
PLAIN = re.compile(r"[A-Za-z0-9_./+,@%:-]+")  # characters both shells pass on as written
NOTICE_START = "<task-notification>"
TASK = re.compile(r"<task-id>([^<]*)</task-id>")
STATUS = re.compile(r"<status>([^<]*)</status>")
EXPIRED = "[Monitor expired"


class Run(TypedDict):
    """A live run: its log resolved ("" when none was read), the heavy command it runs, whether a watch
    ever held it, and how many turn ends its present debt has kept going."""

    log: FilePath
    label: Prose
    armed: bool
    refused: Count


class Watch(TypedDict):
    """A live watch: the runs it holds."""

    runs: list[TaskId]


class State(TypedDict):
    """What the hook keeps for one session."""

    transcript: FilePath
    offset: Offset
    runs: dict[TaskId, Run]
    watches: dict[TaskId, Watch]
    ended: list[TaskId]


class Notice(NamedTuple):
    """One task notification: the task, its status when it ended, its event lines."""

    task: TaskId
    status: Status | None
    lines: list[EventText]


class Decision(NamedTuple):
    """An answer to a PreToolUse or a Stop: its status, and what it prints on stderr."""

    status: ExitStatus
    message: Prose | None = None


class WatchOf(NamedTuple):
    """What a Monitor command watches: a log, or None with the reason it does not count."""

    log: FilePath | None
    reason: Prose


class Demand(NamedTuple):
    """The spelling of the watcher a refusal names, or None with the reason no watch can be demanded."""

    watcher: Word | None
    reason: Prose


class BashInput(TypedDict, total=False):
    command: CommandLine


class BashResponse(TypedDict, total=False):
    backgroundTaskId: TaskId


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
    cwd: FilePath
    agent_id: TaskId
    tool_name: Word
    tool_input: BashInput | MonitorInput | StopInput | WriteInput
    tool_response: BashResponse | MonitorResponse | StopResponse | Prose
    source: Word
    background_tasks: list[BackgroundTask]


class QueueEntry(TypedDict, total=False):
    """A transcript line that queues a notice."""

    type: Literal["queue-operation"]
    operation: Literal["enqueue", "remove"]
    content: Prose


# Runs, logs and watches ----------------------------------------------------------


def resolved(word: Word, cwd: FilePath | None) -> FilePath:
    """A path word as the shell reads it where it runs: from the home directory after a leading `~/`,
    `$HOME/` or `${HOME}/`, from ``cwd`` when relative."""
    text = HOME_PREFIX.sub(lambda _: str(Path.home()), word)
    return FilePath(os.path.realpath(os.path.join(cwd or os.getcwd(), text)))


def run_of(command: CommandLine, cwd: FilePath | None) -> tuple[Prose, FilePath] | None:
    """The heavy command a call runs and its log ("" when none is read), or None when it runs none."""
    import _commands  # noqa: PLC0415 - read for a background call alone: every other event skips its load

    try:
        invocations = _commands.expand(command, cwd)
    except _shell.BudgetExceeded as exc:
        invocations = getattr(exc, "found", [])
    except (_shell.ParseError, MemoryError):
        invocations = []
    for invocation in invocations:
        if invocation.heavy is None:
            continue
        log = FilePath("")
        for redirect in invocation.cmd.redirects:  # in bash's order: the last one sends the output
            target = redirect.target
            if redirect.op in OUTPUT_OPS and redirect.fd in (None, 1) and target is not None \
                    and not target.process_substitution:
                written = Word(target.value if target.value is not None else target.text)
                log = FilePath("") if written.startswith("/dev/") else resolved(written, invocation.cmd.cwd or cwd)
        return Prose(invocation.heavy.name), log
    found = _commands.heavy_in_text(command, cwd)  # text the reader could not follow: a run with no log read
    return (Prose(found.name), FilePath("")) if found is not None else None


def expanded(word: Word) -> Word:
    """The word with a leading `~/`, `$HOME/` or `${HOME}/` taken from the home directory."""
    return Word(HOME_PREFIX.sub(lambda _: str(Path.home()), word))


def plain_path(path: Word) -> bool:
    """Say whether a path is one the shell passes on as written: plain characters, absolute, with no `.`
    or `..` part."""
    return bool(PLAIN.fullmatch(path)) and path.startswith("/") and not {".", ".."} & set(path.split("/"))


def named_watcher() -> Word | None:
    """The plain spelling of the watcher a refusal names: its path, else the path it links to."""
    return next((spelling for spelling in (Word(str(WATCHER)), Word(os.path.realpath(WATCHER)))
                 if plain_path(spelling)), None)


def demand() -> Demand:
    """The watcher a refusal can name, or why no watch can be demanded now."""
    if not os.access(os.path.realpath(WATCHER), os.X_OK):
        return Demand(None, Prose(f"the watcher {WATCHER} is not executable"))
    spelling = named_watcher()
    if spelling is None:
        return Demand(None, Prose(f"the watcher {WATCHER} has no plain path to name it by"))
    return Demand(spelling, Prose(""))


def watch_of(command: CommandLine) -> WatchOf:
    """Read a Monitor command as a watch: the log it watches, or why it is not one."""
    text = command.strip(" \t")
    if OPERATORS & set(text):
        return WatchOf(None, Prose("the watcher is not alone in it: no pipe, list, redirection, subshell or second "
                                   "line"))
    if QUOTES & set(text):
        return WatchOf(None, Prose("it quotes or escapes a word: give every word bare"))
    words = [Word(word) for word in re.split(r"[ \t]+", text)] if text else []
    if any("$" in HOME_PREFIX.sub("", word) or "`" in word for word in words):
        return WatchOf(None, Prose("it expands a variable or a command other than a leading $HOME/"))
    if not 3 <= len(words) <= 4:
        return WatchOf(None, Prose("it is not `log-stall-watch LOG SILENCE [SECONDS]`"))
    program, log = expanded(words[0]), expanded(words[1])
    if not (plain_path(program) and plain_path(log)):
        return WatchOf(None, Prose("the watcher's or the log's path is not plain and absolute: no glob, brace, `#`, "
                                   "`~` inside, control character, relative path, or `.` or `..` part"))
    if os.path.realpath(program) != os.path.realpath(WATCHER):
        return WatchOf(None, Prose(f"its program is not the watcher by its path, {WATCHER}"))
    if not words[2].isdigit() or not 1 <= int(words[2]) <= LONGEST_SILENCE:
        return WatchOf(None, Prose(f"its silence {words[2]} is not a whole number of seconds from 1 to "
                                   f"{LONGEST_SILENCE}"))
    if len(words) > 3:
        try:
            interval = Seconds(float(words[3]))
        except ValueError:
            return WatchOf(None, Prose(f"its interval {words[3]} is not a number"))
        if not 0 < interval <= LONGEST_INTERVAL:
            return WatchOf(None, Prose(f"its interval {words[3]} is not above 0 and at most {LONGEST_INTERVAL:g} "
                                       "seconds"))
    return WatchOf(FilePath(os.path.realpath(log)), Prose(""))


def watched(state: State, task: TaskId) -> bool:
    return any(task in watch["runs"] for watch in state["watches"].values())


def owing(state: State) -> list[tuple[TaskId, Run]]:
    """The live runs no live watch holds."""
    return [(task, run) for task, run in sorted(state["runs"].items()) if not watched(state, task)]


# Notices -----------------------------------------------------------------------


def event_lines(text: Prose) -> list[EventText]:
    """The lines of every closed <event> in a notice's text, read in one pass."""
    return [EventText(line) for part in text.split("</event>")[:-1] if "<event>" in part
            for line in part.rpartition("<event>")[2].splitlines()]


def notice_in(content: Prose) -> Notice | None:
    """Read the one notice a queued text holds. Its task and status come from its head, before its
    summary, so a result quoting other notices is not read as one."""
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
    """End the runs and watches the notices end, and remember the ends of tasks not recorded yet."""
    for notice in notices:
        ends = notice.status in TERMINAL
        expired = any(line.startswith(EXPIRED) for line in notice.lines)
        if notice.task in state["watches"]:
            if ends or expired:
                del state["watches"][notice.task]
        elif notice.task in state["runs"]:
            if ends:
                del state["runs"][notice.task]
        elif ends or expired:
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
    return State(transcript=transcript or FilePath(""), offset=Offset(0), runs={}, watches={}, ended=[])


def counted(value: Count | Offset | None) -> bool:
    return type(value) is int and value >= 0


def well_formed(data: State) -> bool:
    """Say whether a state read back has the shape the hook writes."""
    return (isinstance(data, dict) and isinstance(data.get("transcript"), str) and counted(data.get("offset"))
            and isinstance(data.get("runs"), dict) and isinstance(data.get("watches"), dict)
            and isinstance(data.get("ended"), list)
            and all(isinstance(run, dict) and isinstance(run.get("log"), str) and isinstance(run.get("label"), str)
                    and type(run.get("armed")) is bool and counted(run.get("refused"))
                    for run in data["runs"].values())
            and all(isinstance(watch, dict) and isinstance(watch.get("runs"), list)
                    and all(isinstance(task, str) for task in watch["runs"])
                    for watch in data["watches"].values())
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
    """Hold the session's state under the lock; settle its runs' counts; write it back, or remove it once
    no run or watch is recorded."""
    path = state_path(session)
    with locked():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        state = cast("State", data) if well_formed(cast("State", data)) else fresh(transcript)
        yield state
        for task, run in state["runs"].items():
            if watched(state, task):
                run["refused"] = Count(0)
        if state["runs"] or state["watches"]:
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


def finding(state: State, lead: Prose, watcher: Word) -> Prose:
    """What the owing runs owe, each with the watch that pays it."""
    lines: list[Prose] = []
    for task, run in owing(state):
        why = "its watch ended" if run["armed"] else "it has no watch"
        if not run["log"]:
            lines.append(Prose(f"run {task} ({run['label']}): {why}, and no log of it was read. Start the watch "
                               f"with the Monitor tool on the file the run writes: {watcher} LOG {SILENCE_NAMED}"))
        elif not plain_path(Word(run["log"])):
            lines.append(Prose(f"run {task} ({run['label']}): {why}, and its log {run['log']} has no plain spelling "
                               f"a watch can name. Stop it with TaskStop, task_id {task}, and start it again "
                               f"writing to a plain path."))
        else:
            lines.append(Prose(f"run {task} ({run['label']}): {why}. Start the watch with the Monitor tool:\n"
                               f"    command: {watcher} {run['log']} {SILENCE_NAMED}\n"
                               f"    timeout_ms: {MONITOR_TIMEOUT_MS}, re-armed on each expiry while the run lasts; "
                               f"a silence from 1 to {LONGEST_SILENCE} seconds above the run's slowest silent step. "
                               f"The watch ends on the log's `EXIT=` line, which the launch appends with "
                               f"`; echo \"EXIT=$?\" >> {run['log']}`; without it, stop the watch with TaskStop "
                               f"once the run's notice comes."))
    return Prose(f"background-run-watch-guard: {lead}\n" + "\n".join(f"  {each}" for each in lines)
                 + "\nA hung run sends no notice, so the watch is what says it hung. Monitor, TaskStop and ToolSearch "
                 "go through meanwhile.")


def standing_down(demanded: Demand) -> Prose:
    return Prose(f"background-run-watch-guard: no watch can be demanded, so no call is refused for a missing one: "
                 f"{demanded.reason}")


def context(event_name: HookEvent, text: Prose) -> None:
    """A note the model reads beside the call, at exit 0."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}))


# The events --------------------------------------------------------------------


def started(state: State, event: Event) -> bool:
    """Record a run from a Bash call's result; say whether one was recorded."""
    response = event.get("tool_response")
    task = cast("BashResponse", response).get("backgroundTaskId") if isinstance(response, dict) else None
    command = cast("BashInput", event.get("tool_input") or {}).get("command")
    if not isinstance(task, str) or not task or not isinstance(command, str) or task in state["ended"]:
        return False
    found = run_of(command, event.get("cwd"))
    if found is None:
        return False
    label, log = found
    state["runs"][task] = Run(log=log, label=label, armed=False, refused=Count(0))
    return True


def armed(state: State, watch: WatchOf, task: TaskId | None) -> Prose | None:
    """Record a Monitor that is a watch of a live run; return why one naming the watcher holds none."""
    reason = watch.reason
    if watch.log is not None:
        held_runs = [run_task for run_task, run in sorted(state["runs"].items())
                     if not run["log"] or run["log"] == watch.log]
        if not task:
            reason = Prose("the Monitor's result names no task id")
        elif task in state["ended"]:
            reason = Prose("it ended before its start was recorded")
        elif not held_runs:
            reason = Prose(f"{watch.log} is no live run's log")
        else:
            state["watches"][task] = Watch(runs=held_runs)
            for run_task in held_runs:
                state["runs"][run_task]["armed"] = True
            return None
    return Prose(f"background-run-watch-guard: this Monitor holds no run: {reason}.") if state["runs"] else None


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
    state["watches"] = {task: watch for task, watch in state["watches"].items() if task in live}


def kill_switch_write(event: Event) -> bool:
    if event.get("tool_name") not in ("Write", "Edit"):
        return False
    target = cast("WriteInput", event.get("tool_input") or {}).get("file_path")
    return isinstance(target, str) and os.path.realpath(target) == os.path.realpath(KILL_SWITCH)


def after_tool(event: Event, session: SessionId, transcript: FilePath | None) -> Decision:
    """Record what a Bash, Monitor or TaskStop call started or ended."""
    tool = event.get("tool_name")
    note: Prose | None = None
    told: Prose | None = None
    if tool == "Bash":
        response = event.get("tool_response")
        if not (isinstance(response, dict) and cast("BashResponse", response).get("backgroundTaskId")):
            return Decision(ALLOW)
        with held(session, transcript) as state:
            catch_up(state, transcript)
            if started(state, event) and owing(state) and not KILL_SWITCH.exists():
                demanded = demand()
                if demanded.watcher is None:
                    told = standing_down(demanded)
                else:
                    note = finding(state, Prose("before any other call, a background run owes this:"),
                                   demanded.watcher)
    elif tool == "Monitor" and state_path(session).exists():
        command = cast("MonitorInput", event.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or WATCHER.name not in command:
            return Decision(ALLOW)
        response = event.get("tool_response")
        task = cast("MonitorResponse", response).get("taskId") if isinstance(response, dict) else None
        with held(session, transcript) as state:
            catch_up(state, transcript)
            note = armed(state, watch_of(command), task)
    elif tool == "TaskStop" and state_path(session).exists():
        with held(session, transcript) as state:
            catch_up(state, transcript)
            stopped(state, event)
    if note:
        context("PostToolUse", note)
    return Decision(REPORTED, told) if told else Decision(ALLOW)


def decide(state: State, event: Event) -> Decision:
    """Answer a PreToolUse or a Stop from the state."""
    owed = owing(state)
    if KILL_SWITCH.exists() or not owed:
        return Decision(ALLOW)
    is_stop = event.get("hook_event_name") == "Stop"
    if not is_stop and (event.get("tool_name") in ALWAYS_ALLOWED or kill_switch_write(event)):
        return Decision(ALLOW)
    demanded = demand()
    if demanded.watcher is None:
        return Decision(REPORTED, standing_down(demanded))
    if is_stop:
        due = [run for _, run in owed if run["refused"] < STOPS_REFUSED]
        if not due:
            return Decision(ALLOW)
        for run in due:
            run["refused"] = Count(run["refused"] + 1)
        return Decision(REFUSE, finding(state, Prose("the turn does not end yet: a background run owes this:"),
                                        demanded.watcher))
    return Decision(REFUSE, finding(state, Prose(f"{event.get('tool_name')} is refused while a background run owes "
                                                 "this:"), demanded.watcher))


def before(event: Event, session: SessionId, transcript: FilePath | None) -> Decision:
    """Answer a PreToolUse or a Stop: refuse while a run owes its watch."""
    if not state_path(session).exists():
        return Decision(ALLOW)
    with held(session, transcript) as state:
        catch_up(state, transcript)
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
        print(f"background-run-watch-guard: the call went unchecked: {type(exc).__name__}: {exc}", file=sys.stderr)
        return REPORTED
    if decision.message is not None:
        print(decision.message, file=sys.stderr)
    return decision.status


if __name__ == "__main__":
    sys.exit(main())
