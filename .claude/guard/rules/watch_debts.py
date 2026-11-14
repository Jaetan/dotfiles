"""A heavy background run owes a stall watch, a workflow a token watch, and a run over its ceiling a TaskStop.

THE RULE (user, 2026-10-03, 2026-10-25 and 2026-11-10). A heavy run the main thread starts in the background is
watched from its start to its end by a Monitor running `~/.local/bin/log-stall-watch LOG SILENCE [INTERVAL]` on the
file its output ends in, and a workflow it launches by one running `~/.local/bin/workflow-token-watch DIR [CEILING
[INTERVAL]]` on its transcript folder. While a live run has no live watch of its row on its target, or is over its
ceiling, every call of the main thread but Monitor, TaskStop and ToolSearch is refused, and so is the end of its turn,
naming each run, its target and the call that pays it: that Monitor, or TaskStop of the run. A run with no target owes
until it ends. A watch lives until its Monitor expires or ends, so the debt opens again while the run lives.

THE RECORDS, kept in the session state (state/session.py) at PostToolUse of the main thread:
  run    of a Bash call whose response names a backgroundTaskId and whose call model holds a command heavy()
         (dispatcher/heavy.py) answers for under the declaration of its directory, its target the file the first such
         command's output ends in (rules/heavy_output.py's placed()); of a Workflow call, under its taskId, its target
         its transcriptDir;
  watch  of a Monitor call, under its taskId, whose call model is one command of known words and no redirection: the
         watcher by its absolute path (`~/` and `$HOME/` read as the model reads them), an absolute target, then the
         numbers its row takes (SILENCE 1 to 1800, or CEILING 1 to 2,000,000, then INTERVAL above 0 and at most 60).
A TaskStop ends the run or watch its input names, a Stop each run and watch its tasks in flight do not list (none
where it carries no list), and every target is resolved. At each event of the main thread the task notices
dispatcher/notices.py reads past the offset a `notice` record keeps come first: an OVER LIMIT marks every run on its
watch's target over, an expiry ends the watch, and a notice ending a task ends its run or watch. An agent's event is
judged as nothing.

EXCEPTIONS, each with the failure it holds:
  - a task whose end the same event reads is not recorded: else a run failing at once, its end queued before its
    PostToolUse, owes forever;
  - an event naming no transcript is judged as nothing, as the old hooks passed an event they could not read: else
    no notice could end its debt, and the refusal would never lift;
  - a transcript missing or unreadable gives no notice: else a session whose transcript is not yet written has every
    call refused as a fault of the kit;
  - a debt keeps the turn going at most once while its run lacks a watch and three times while it is over its ceiling,
    counted in the run's record and started again whenever the run owes nothing: else a debt that cannot be paid
    loops the session;
  - a Write, Edit or MultiEdit of the switch file of this rule or of the whole kit passes whatever is owed: else a rule
    in error could not be switched off from inside the session;
  - while a row's watcher is not executable, no watch of that row is owed, the calls it would refuse passing with a
    note saying so; a run with no target or over its ceiling is still refused, its cure needing no watcher: else a
    broken watcher refuses every call for a watch no Monitor can run.
"""

import re
from collections.abc import Mapping, Sequence
from os import X_OK, access
from os.path import join, realpath
from pathlib import Path
from shlex import quote
from typing import NamedTuple, NewType

from dispatcher.core import WHOLE_KIT, Event, HookEvent, TaskId, TaskKey, ToolInput, ToolName
from dispatcher.heavy import heavy
from dispatcher.model import Command
from dispatcher.notices import Notice, Offset, notices
from dispatcher.tree import Seconds, ShellText
from dispatcher.verdict import Prose, RuleName, Verdict, context, refuse
from dispatcher.words import VarName
from dispatcher.writes import FilePath
from rules.heavy_output import placed
from state.session import FieldName, FieldValue, Kind, Record, RecordId, Session

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse", "PostToolUse", "Stop"})
TOOLS = None
Target = NewType("Target", str)
Bound = NewType("Bound", int)
Count = NewType("Count", int)
BASH, MONITOR, WORKFLOW, TASK_STOP = (ToolName(name) for name in ("Bash", "Monitor", "Workflow", "TaskStop"))
PAYING = frozenset({MONITOR, TASK_STOP, ToolName("ToolSearch")})
EDITS = frozenset(ToolName(name) for name in ("Write", "Edit", "MultiEdit"))
SWITCHES = (RuleName("watch-debts"), WHOLE_KIT)
STOPS_REFUSED: Mapping[bool, Count] = {False: Count(1), True: Count(3)}  # by whether the run is over its ceiling
STARTING: Mapping[ToolName | None, TaskKey] = {BASH: "backgroundTaskId", WORKFLOW: "taskId", MONITOR: "taskId"}
KEPT: tuple[Kind, ...] = ("run", "watch")
ROW, TARGET, OVER, STOPS = FieldName("row"), FieldName("target"), FieldName("over"), FieldName("stops")
STALL, TOKEN = FieldValue("stall"), FieldValue("token")
OFFSET = RecordId("watch-debts")
TRANSCRIPT, AT = FieldName("transcript"), FieldName("offset")
WHOLE = re.compile(r"[0-9]+")
DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")
LONGEST = Seconds(60.0)
TURN = Prose("the turn does not end yet")
ADVICE = """{lead} while a live run owes its watch: a heavy background run is watched by `log-stall-watch` on
its log and a workflow by `workflow-token-watch` on its transcript folder, from start to end, and a run over its
ceiling is stopped.

{problems}

Every call but Monitor, TaskStop and ToolSearch is refused until each debt is paid. A watch lasts until its Monitor
expires or ends, and the run owes it again while it lives."""
NOTE = """no call is refused for a watch whose watcher cannot run:

{problems}"""


class Watcher(NamedTuple):
    """A row's watcher: its program under ~/.local/bin, the run it watches as a refusal names it, the highest whole
    number it takes after its target, whether that number is owed, and the words a refusal names after the target."""

    program: ShellText
    what: Prose
    highest: Bound
    owed: bool
    named: ShellText


WATCHERS: Mapping[FieldValue, Watcher] = {
    STALL: Watcher(ShellText("log-stall-watch"), Prose("heavy background run"), Bound(1800), True, ShellText(" 900")),
    TOKEN: Watcher(ShellText("workflow-token-watch"), Prose("workflow"), Bound(2_000_000), False, ShellText(""))}


def fits(watcher: Watcher, numbers: Sequence[ShellText]) -> bool:
    """Whether ``numbers``, the words after a watch's target, are what ``watcher`` takes: a whole number from 1 to its
    highest, owed or not, then at most an interval above 0 and at most LONGEST seconds."""
    if not numbers:
        return not watcher.owed
    whole, *interval = numbers
    return (len(interval) <= 1 and WHOLE.fullmatch(whole) is not None and 1 <= int(whole) <= watcher.highest
            and all(DECIMAL.fullmatch(word) is not None and 0 < float(word) <= LONGEST for word in interval))


def program(event: Event, watcher: Watcher) -> Target:
    """The path ``watcher`` runs from, under the home the call model reads."""
    return Target(join(event.reading.env.get(VarName("HOME"), ShellText("/")), ".local", "bin", watcher.program))


def runnable(event: Event) -> frozenset[FieldValue]:
    """The rows whose watcher is executable, so their watch can be owed."""
    return frozenset(row for row, watcher in WATCHERS.items() if access(program(event, watcher), X_OK))


def watch_of(event: Event) -> tuple[FieldValue, Target] | None:
    """The row and target of a Monitor call that is a watcher alone, as the module docstring reads it; None for any
    other."""
    commands = event.call().commands
    if len(commands) != 1 or commands[0].redirs:
        return None
    known = [word.value for word in commands[0].words if word.value is not None]
    if len(known) != len(commands[0].words) or len(known) < 2 or not known[1].startswith("/"):
        return None
    run, target, *numbers = known
    for row, watcher in WATCHERS.items():
        if run == program(event, watcher) and fits(watcher, numbers):
            return row, Target(realpath(target))
    return None


def log_of(command: Command) -> Target | None:
    """The file ``command``'s output ends in, placed where it runs and resolved; None where it ends in no file the
    call makes known."""
    end = command.streams.output
    path = None if end.file is None else placed(end.file, command.cwd)
    return None if path is None else Target(realpath(path))


def started(event: Event) -> tuple[Kind, FieldValue, Target | None] | None:
    """The record the PostToolUse ``event`` of a Bash, Workflow or Monitor call starts: its kind, its row and its
    target; None where it starts none."""
    if event.tool == BASH:
        logs = [log_of(command) for command in event.call().commands
                if heavy(command, event.declaration(Path(command.cwd or event.cwd or "/"))) is not None]
        return ("run", STALL, logs[0]) if logs else None
    if event.tool == WORKFLOW:
        folder = event.response.get("transcriptDir")
        return "run", TOKEN, None if folder is None else Target(realpath(folder))
    watch = watch_of(event)
    return None if watch is None else ("watch", *watch)


def kept(session: Session, kind: Kind) -> list[Record]:
    """The records of ``kind`` this rule keeps: those whose row is one of WATCHERS."""
    return [record for record in session.records(kind) if record.fields.get(ROW) in WATCHERS]


def read(path: FilePath, session: Session) -> list[Notice]:
    """The notices queued in the transcript at ``path`` past the offset kept for it, from its start for a transcript of
    another path, the offset moved past them; none where it cannot be read."""
    fields: Mapping[FieldName, FieldValue] = next(
        (record.fields for record in session.records("notice") if record.ident == OFFSET), {})
    start = Offset(int(fields[AT])) if fields.get(TRANSCRIPT) == path else Offset(0)
    try:
        found, stop = notices(Path(path), start)
    except OSError:
        return []
    if (fields.get(TRANSCRIPT), fields.get(AT)) != (path, str(stop)):
        session.record("notice", OFFSET, {TRANSCRIPT: FieldValue(path), AT: FieldValue(str(stop))})
    return found


def applied(session: Session, found: Sequence[Notice]) -> frozenset[TaskId]:
    """Apply each notice to the runs and watches kept, as the module docstring reads it; the tasks they end."""
    for notice in found:
        ident = RecordId(notice.task)
        watch = next((record for record in kept(session, "watch") if record.ident == ident), None)
        if watch is not None and notice.over_limit:
            for run in kept(session, "run"):
                if run.fields.get(TARGET) == watch.fields.get(TARGET):
                    session.record("run", run.ident, {**run.fields, OVER: FieldValue("yes")})
        if watch is not None and (notice.ends_task or notice.ends_watch):
            _ = session.forget("watch", ident)
        if notice.ends_task and any(run.ident == ident for run in kept(session, "run")):
            _ = session.forget("run", ident)
    return frozenset(notice.task for notice in found if notice.ends_task)


def record(event: Event, session: Session, ended: frozenset[TaskId]) -> None:
    """Keep the run or watch the PostToolUse ``event`` starts unless its task is among ``ended``, or forget the run and
    watch a TaskStop names."""
    if event.tool == TASK_STOP:
        given = event.payload.get("tool_input", ToolInput())
        stop = given.get("task_id") or given.get("shell_id") or ""
        for kind in KEPT:
            _ = session.forget(kind, RecordId(stop))
        return
    key = STARTING.get(event.tool)
    task = None if key is None else event.response.get(key)
    if task is None or task in ended or (start := started(event)) is None:
        return
    kind, row, target = start
    session.record(kind, RecordId(task), {ROW: row} | ({} if target is None else {TARGET: FieldValue(target)}))


def unlisted(session: Session, in_flight: frozenset[TaskId] | None) -> None:
    """Forget each run and watch kept that ``in_flight``, the tasks a Stop lists as not ended, does not list; none
    where the Stop carries no list."""
    if in_flight is None:
        return
    for kind in KEPT:
        for each in kept(session, kind):
            if each.ident not in in_flight:
                _ = session.forget(kind, each.ident)


class Debt(NamedTuple):
    """A live run that owes: its record, the line naming what pays it, and whether it is waived, a missing watch whose
    watcher cannot run."""

    run: Record
    line: Prose
    waived: bool


def debts(session: Session, runnable: frozenset[FieldValue]) -> list[Debt]:
    """A debt for each live run that owes: one with no target, one over its ceiling, and one with no live watch of its
    row on its target, waived where its row is not ``runnable``."""
    watched = {(watch.fields.get(ROW), watch.fields.get(TARGET)) for watch in kept(session, "watch")}
    found: list[Debt] = []
    for run in kept(session, "run"):
        row, target = run.fields[ROW], run.fields.get(TARGET)
        watcher, stop = WATCHERS[row], f"stop it with TaskStop, task_id {run.ident}"
        if target is None:
            found.append(Debt(run, Prose(f"  * the {watcher.what} {run.ident} has no target to watch: it owes until it "
                                         f"ends, or {stop}"), False))
        elif OVER in run.fields:
            found.append(Debt(run, Prose(f"  * the {watcher.what} {run.ident} on {target} is over its ceiling: {stop}"),
                              False))
        elif (row, target) not in watched and row not in runnable:
            found.append(Debt(run, Prose(f"  * the {watcher.what} {run.ident} on {target} has no live {row} watch, and "
                                         f"none is demanded while `~/.local/bin/{watcher.program}` is not executable"),
                              True))
        elif (row, target) not in watched:
            found.append(Debt(run, Prose(f"  * the {watcher.what} {run.ident} on {target} has no live {row} watch: run "
                                         f"the Monitor `~/.local/bin/{watcher.program} {quote(target)}{watcher.named}`"),
                              False))
    return found


def stops(run: Record) -> Count:
    """The turns ``run``'s debt has kept going since it last owed nothing."""
    return Count(int(run.fields.get(STOPS, FieldValue("0"))))


def counted(session: Session, owed: Sequence[Debt]) -> None:
    """Start the count of turns kept going again for every run that owes nothing."""
    owing = {debt.run.ident for debt in owed}
    for run in kept(session, "run"):
        if STOPS in run.fields and run.ident not in owing:
            session.record("run", run.ident, {name: value for name, value in run.fields.items() if name != STOPS})


def kept_going(session: Session, owed: Sequence[Debt]) -> bool:
    """Whether a debt of ``owed`` keeps the turn going: one not waived whose run has kept it going fewer times than
    STOPS_REFUSED allows; each such run's count raised."""
    due = [debt.run for debt in owed if not debt.waived and stops(debt.run) < STOPS_REFUSED[OVER in debt.run.fields]]
    for run in due:
        session.record("run", run.ident, {**run.fields, STOPS: FieldValue(str(stops(run) + 1))})
    return bool(due)


def switching(event: Event) -> bool:
    """Whether ``event`` is a Write, Edit or MultiEdit of the switch file of this rule or of the whole kit; never for
    an event built without the dispatcher, which names no switch folder."""
    if event.tool not in EDITS or event.off is None:
        return False
    path = event.payload.get("tool_input", ToolInput()).get("file_path", FilePath(""))
    return realpath(path) in {realpath(join(event.off, name)) for name in SWITCHES}


def advice(lead: Prose, owed: Sequence[Debt]) -> Prose:
    """The refusal of a call or a turn's end, ``lead`` naming which, with a line for each debt."""
    return Prose(ADVICE.format(lead=lead, problems="\n".join(debt.line for debt in owed)))


def judge(event: Event) -> Verdict | None:
    """Read the notices, record what a PostToolUse starts or stops and what a Stop ends, start the counts of runs owing
    nothing again, then refuse a Stop or a PreToolUse while a run owes, as the module docstring reads it."""
    if not event.main_thread or event.transcript is None:
        return None
    session = event.state()
    ended = applied(session, read(event.transcript, session))
    if event.name == "PostToolUse":
        record(event, session, ended)
    if event.name == "Stop":
        unlisted(session, event.in_flight)
    owed = debts(session, runnable(event))
    counted(session, owed)
    if event.name == "Stop":
        return refuse(advice(TURN, owed)) if kept_going(session, owed) else None
    if event.name == "PostToolUse" or event.tool in PAYING or switching(event):
        return None
    if any(not debt.waived for debt in owed):
        return refuse(advice(Prose("this call is refused"), owed))
    return context(Prose(NOTE.format(problems="\n".join(debt.line for debt in owed)))) if owed else None
