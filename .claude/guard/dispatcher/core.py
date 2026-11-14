"""One hook event, judged: the event read, the rules that name it run, their verdicts combined into one answer.

A rule is a module in the rules folder, `NAME.py` with NAME not starting with `_`, switched off by a file
`<off>/NAME` (underscores spelled as dashes) and the whole kit by `<off>/all`; `<off>/unreadable` switches off the
refusal of a call the kit cannot read, a rule that needs that call's model then passing it. A rule declares
  EVENTS  a frozenset of the hook events it judges,
  TOOLS   a frozenset of the tools it judges, or None for every tool and for events with no tool,
  judge   a function of the Event, giving a Verdict or None.
The answer: exit 2 with every refusal's text on stderr when any rule refuses, or when the event cannot be read, the
call cannot be read or the kit fails (each named; a rule that cannot load or raises is a fault of the kit, but for a
MemoryError, which the dispatcher names as its cap), notes following the refusals; a JSON additionalContext on
stdout when rules only add notes, but a systemMessage at a Stop or SubagentStop, which shows the user and ends the
turn where a Stop's additionalContext would keep it going; exit 0 and nothing otherwise.
The call model of a Bash or Monitor call is built before any rule runs at PreToolUse, so a call the kit cannot read
is refused whatever rules apply; at any other event it is built when a rule first asks for it. A call is one the kit
cannot read too where a text it runs through `bash -c`, `sh -c` or `eval` is not made known by the call, or where it
runs zsh or fish at all, whatever their words, since the kit reads bash alone (each a Gap of the model): no rule sees its model,
and the refusal names each gap's holder and each form the kit reads its text in.
An event gives a rule the declaration holding for a path (declarations/lookup.py, one Declarations per event) and
the session state of its claude process as its agent keeps it (state/session.py), each module imported and built
only when first asked; a declaration or a state that cannot be read is a KitFault naming its file. The machine's facts
are asked before any rule runs at PreToolUse of a Bash or Monitor call, so a machine file missing or breaking the
schema refuses each such call whatever rules apply; a path's project facts never read the machine file, so a call of
any other tool meets its fault only where a rule asks a machine fact. Each SessionStart sweeps the state of every
claude process that has ended.
An event gives a rule, besides, what its payload says, each typed: `response` the tool's response (its task ids
and transcript folder), `transcript` the transcript's path, `in_flight` the tasks a Stop lists as not ended,
`main_thread` whether no agent_id names a subagent, and `off` the switch folder the dispatcher names. A key absent or
holding a value of another shape reads as none: an empty response, no path, no task, the main thread's, no folder.

EXCEPTIONS, each with the failure it holds:
  * at a Stop or SubagentStop whose stop_hook_active is true, every fault of the kit, an Unreadable a rule raises
    included, is a note and no refusal, while a rule's own refusal still refuses: exit 2 keeps a turn going and
    Claude Code fires that Stop again with stop_hook_active true, so a broken kit refusing each time loops the turn's
    end for ever.
"""

import importlib.util
import json
import os
from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING, Literal, NamedTuple, NewType, TypedDict

from dispatcher.model import Call, ReadSettings, build
from dispatcher.notices import TERMINAL, TaskId, TaskStatus
from dispatcher.scripts import ScriptPath
from dispatcher.tree import ShellText
from dispatcher.verdict import KitFault, Prose, RuleName, Unreadable, Verdict
from dispatcher.words import Directory, Environment, VarName
from dispatcher.writes import FilePath

if TYPE_CHECKING:
    from pathlib import Path

    from declarations.lookup import Declarations, Facts
    from declarations.schema import Machine
    from state.session import Places, Session

HookEvent = Literal["PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch", "Stop", "SubagentStart",
                    "SubagentStop", "SessionStart", "SessionEnd", "UserPromptSubmit", "Notification", "PreCompact"]
HOOK_EVENTS: frozenset[HookEvent] = frozenset(HookEvent.__args__)
ToolName = NewType("ToolName", str)
AgentId = NewType("AgentId", str)
SessionId = NewType("SessionId", str)
FolderPath = NewType("FolderPath", str)
RuleFile = NewType("RuleFile", str)
"""A rule file's name in its rules folder, `<name>.py`."""
EventBytes = NewType("EventBytes", bytes)
ExitStatus = Literal[0, 2]
TaskKey = Literal["taskId", "backgroundTaskId", "task_id"]
TASK_KEYS: tuple[TaskKey, ...] = TaskKey.__args__
SessionSource = Literal["startup", "resume", "clear", "compact"]
SHELL_TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
WHOLE_KIT = RuleName("all")
UNREADABLE = RuleName("unreadable")
RESERVED = frozenset({WHOLE_KIT, UNREADABLE})
RULES = FolderPath(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "rules"))
"""The kit's own rules folder: the dispatcher's default and the registration's."""
FAULT_TAIL = "The call is refused, since a kit that cannot judge a call passes nothing."

ToolInput = TypedDict("ToolInput", {"command": ShellText, "run_in_background": bool, "description": Prose,
                                    "file_path": FilePath, "task_id": TaskId, "shell_id": TaskId}, total=False)
ToolResponse = TypedDict("ToolResponse", {"taskId": TaskId, "transcriptDir": FolderPath, "backgroundTaskId": TaskId,
                                          "task_id": TaskId}, total=False)
BackgroundTask = TypedDict("BackgroundTask", {"id": TaskId, "status": TaskStatus}, total=False)
Payload = TypedDict("Payload", {"hook_event_name": HookEvent, "tool_name": ToolName, "tool_input": ToolInput,
                                "cwd": Directory, "agent_id": AgentId, "session_id": SessionId,
                                "transcript_path": FilePath, "tool_response": ToolResponse | Prose,
                                "background_tasks": list[BackgroundTask], "source": SessionSource,
                                "stop_hook_active": bool}, total=False)
STOPS: frozenset[HookEvent] = frozenset({"Stop", "SubagentStop"})


class Settings(NamedTuple):
    """Where the rules and the switches are, what reading a call needs, the home folder the declarations read the
    machine file and `~/` from, and where the session state lies; None for the process's own home and state."""

    rules: FolderPath
    off: FolderPath
    reading: ReadSettings
    home: Path | None = None
    places: Places | None = None


class Answer(NamedTuple):
    """The dispatcher's answer: its exit status, stdout and stderr."""

    status: ExitStatus
    stdout: Prose
    stderr: Prose


def known(call: Call) -> Call:
    """``call``, where it makes known every text it runs; an Unreadable naming each gap's holder and each form the
    kit reads such a text in where it does not."""
    if call.gaps:
        raise Unreadable(Prose("; ".join(f"{gap.what}: `{gap.text}`" for gap in call.gaps)),
                         Prose(" ".join(dict.fromkeys(gap.form for gap in call.gaps))))
    return call


def state_places(places: Places | None) -> Places:
    """Where the session state lies: ``places``, or the live state's where None."""
    from state.session import LIVE  # imported where the state is asked or swept, never per event

    return LIVE if places is None else places


class Event:
    """A hook event as rules read it, with the call model of a Bash or Monitor call, the declarations and the session
    state each built once, on first ask, and the switch folder the dispatcher names (None for an event built without
    the dispatcher)."""

    __slots__ = ("built", "declarations", "home", "name", "off", "payload", "places", "project_root", "reading",
                 "session")

    def __init__(self, payload: Payload, reading: ReadSettings, home: Path | None = None,
                 places: Places | None = None) -> None:
        self.payload = payload
        self.name: HookEvent = payload["hook_event_name"]
        self.reading = reading
        root = reading.env.get(VarName("CLAUDE_PROJECT_DIR"))
        self.project_root = Directory(root) if root else None
        self.built: Call | Unreadable | KitFault | None = None
        self.home = home
        self.places = places
        self.off: FolderPath | None = None
        self.declarations: Declarations | None = None
        self.session: Session | None = None

    def declarations_of(self) -> Declarations:
        """The event's one Declarations, built on first ask."""
        if self.declarations is None:
            from declarations.lookup import Declarations  # imported for an event that asks it, never at the kit's load

            self.declarations = Declarations(home=self.home)
        return self.declarations

    def declaration(self, path: Path) -> Facts:
        """What the declarations hold for the absolute ``path``, the machine file read when its facts are first asked;
        KitFault naming the file where a declaration cannot be read or breaks the schema."""
        return self.declarations_of().for_path(path)

    def machine(self) -> Machine:
        """The machine's facts; KitFault naming the machine file where it is missing or breaks the schema."""
        return self.declarations_of().machine()

    def state(self) -> Session:
        """The session state of the claude process this event's hook runs under, as the event's agent keeps it;
        StateFault, a KitFault, where the process cannot be keyed."""
        if self.session is None:
            from state.session import Session  # imported for an event a rule asks it of, never per event

            self.session = Session.of(self, state_places(self.places))
        return self.session

    @property
    def tool(self) -> ToolName | None:
        """The tool the event is about, None for an event about no tool."""
        tool = self.payload.get("tool_name")
        return tool if isinstance(tool, str) else None

    @property
    def cwd(self) -> Directory | None:
        """The directory the session's shell is in when the event fires."""
        cwd = self.payload.get("cwd")
        return cwd if isinstance(cwd, str) and cwd else None

    @property
    def response(self) -> ToolResponse:
        """The tool's response, each of its task ids and its transcript folder kept where it is non-empty text; an
        empty one where the event carries none or one of another shape."""
        raw = self.payload.get("tool_response")
        said = ToolResponse()
        if isinstance(raw, dict):
            for key in TASK_KEYS:
                if isinstance(task := raw.get(key), str) and task:
                    said[key] = TaskId(task)
            if isinstance(folder := raw.get("transcriptDir"), str) and folder:
                said["transcriptDir"] = FolderPath(folder)
        return said

    @property
    def transcript(self) -> FilePath | None:
        """The session transcript's path, None where the event names none."""
        path = self.payload.get("transcript_path")
        return path if isinstance(path, str) and path else None

    @property
    def in_flight(self) -> frozenset[TaskId] | None:
        """The tasks a Stop lists as not ended: each listed with a non-empty text id and a status that is not a terminal
        one, a status absent or not text counting as not ended; None where the event carries no list, so a rule never
        reads a Stop that says nothing of its tasks as one listing none."""
        tasks = self.payload.get("background_tasks")
        if not isinstance(tasks, list):
            return None
        return frozenset(TaskId(ident) for task in tasks
                         if isinstance(task, dict) and isinstance(ident := task.get("id"), str) and ident
                         and not (isinstance(status := task.get("status"), str) and status in TERMINAL))

    @property
    def main_thread(self) -> bool:
        """Whether the event is the main thread's: no agent_id of non-empty text names a subagent."""
        agent = self.payload.get("agent_id")
        return not (isinstance(agent, str) and agent)

    def call(self) -> Call:
        """The call model of this Bash or Monitor call; Unreadable where it cannot be read or runs a text it does not
        make known, KitFault where the kit cannot build it."""
        if self.built is None:
            try:
                self.built = known(build(self.command(), self.cwd, self.reading._replace(unread=self.unread())))
            except (Unreadable, KitFault) as fault:
                self.built = fault
        if isinstance(self.built, (Unreadable, KitFault)):
            raise self.built
        return self.built

    def unread(self) -> frozenset[ScriptPath]:
        """The scripts the declaration of the call's directory names the kit runs unread, none where the event names
        no directory; KitFault naming the file where that declaration cannot be read or breaks the schema."""
        cwd = self.cwd
        if cwd is None:
            return frozenset()
        from pathlib import Path  # imported for a call that is built, as the declarations are

        return frozenset(ScriptPath(str(path)) for path in self.declaration(Path(cwd)).project.unread_scripts)

    def command(self) -> ShellText:
        """The command text of a Bash or Monitor call."""
        tool_input = self.payload.get("tool_input")
        text = tool_input.get("command") if isinstance(tool_input, dict) else None
        if self.tool not in SHELL_TOOLS or not isinstance(text, str):
            raise KitFault(Prose(f"the {self.tool} call carries no command text the kit can read"))
        return text


class Rule(NamedTuple):
    """A loaded rule: its switch name, the events and tools it judges, and its judge."""

    name: RuleName
    events: frozenset[HookEvent]
    tools: frozenset[ToolName] | None
    judge: Callable[[Event], Verdict | None]


def switched_off(off: FolderPath, name: RuleName) -> bool:
    """Whether the switch file ``name`` stands in the switch folder."""
    return os.path.exists(os.path.join(off, name))


def loaded(path: FolderPath, name: RuleName) -> Rule:
    """The rule module at ``path``, its declarations checked; a KitFault where it cannot load or declares wrongly."""
    spec = importlib.util.spec_from_file_location(f"guard_rule_{name.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise KitFault(Prose(f"the rule {name} cannot be loaded from {path}"))
    module: ModuleType = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except MemoryError:
        raise
    except Exception as fault:  # noqa: BLE001 - a rule that cannot load is a fault the refusal names
        raise KitFault(Prose(f"the rule {name} cannot be loaded: {type(fault).__name__}: {fault}")) from None
    events, tools, judge = (getattr(module, field, None) for field in ("EVENTS", "TOOLS", "judge"))
    if (not isinstance(events, frozenset) or not events or not events <= HOOK_EVENTS
            or not (tools is None or isinstance(tools, frozenset)) or not callable(judge)):
        raise KitFault(Prose(f"the rule {name} does not declare EVENTS (hook events), TOOLS (a frozenset or None) "
                             "and judge"))
    return Rule(name, events, tools, judge)


class RuleEntry(NamedTuple):
    """A rule file of a rules folder and the rule's name."""

    file: RuleFile
    name: RuleName


def rule_files(folder: FolderPath) -> list[RuleEntry]:
    """Each rule file of ``folder`` in name order with its rule's name: a ``.py`` file whose name opens with no
    underscore, the name its stem with each ``_`` read as ``-``."""
    found: list[RuleEntry] = []
    for entry in sorted(os.listdir(folder)):
        stem, suffix = os.path.splitext(entry)
        if suffix == ".py" and not stem.startswith("_"):
            found.append(RuleEntry(RuleFile(entry), RuleName(stem.replace("_", "-"))))
    return found


def rules_for(event: Event, settings: Settings) -> tuple[list[Rule], list[Prose]]:
    """The rules switched on that judge ``event``, and a fault line for each rule that cannot load."""
    found: list[Rule] = []
    faults: list[Prose] = []
    for entry, name in rule_files(settings.rules):
        if name in RESERVED:
            faults.append(Prose(f"the rule file {entry} takes a switch name the kit reserves"))
            continue
        if switched_off(settings.off, name):
            continue
        try:
            rule = loaded(FolderPath(os.path.join(settings.rules, entry)), name)
        except KitFault as fault:
            faults.append(fault.reason)
            continue
        if event.name in rule.events and (rule.tools is None or event.tool in rule.tools):
            found.append(rule)
    return found, faults


def fault_text(reason: Prose) -> Prose:
    """A refusal naming a fault of the kit's own."""
    return Prose(f"guard kit: {reason}. {FAULT_TAIL}")


def unreadable_text(fault: Unreadable) -> Prose:
    """The one refusal of a call the kit cannot read, naming a readable form."""
    return Prose(f"guard kit: this call cannot be read: {fault.reason}.\n{fault.form}")


def refused(texts: list[Prose]) -> Answer:
    """Exit 2 with every refusal's text on stderr."""
    return Answer(2, Prose(""), Prose("\n\n".join(texts) + "\n"))


def noted(name: HookEvent, texts: list[Prose]) -> Answer:
    """Exit 0 with every note's text on stdout: a systemMessage at a Stop or SubagentStop, the ``name`` event's
    additionalContext at any other event."""
    text = "\n\n".join(texts)
    said = ({"systemMessage": text} if name in STOPS
            else {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}})
    return Answer(0, Prose(json.dumps(said) + "\n"), Prose(""))


def kept_going(payload: Payload) -> bool:
    """Whether ``payload`` is a Stop or a SubagentStop a Stop hook has kept going: its stop_hook_active is true."""
    return payload.get("hook_event_name") in STOPS and payload.get("stop_hook_active") is True


def payload_of(raw: EventBytes) -> Payload:
    """The event's JSON object, its hook event named; a KitFault where it cannot be read."""
    try:
        payload: Payload = json.loads(raw)
    except ValueError as fault:
        raise KitFault(Prose(f"the hook event is not JSON: {fault}")) from None
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in HOOK_EVENTS:
        raise KitFault(Prose("the hook event names no hook event the kit knows"))
    return payload


def swept(places: Places | None) -> list[Prose]:
    """Remove the session state of every claude process that has ended; the fault where the sweep cannot run."""
    from state.session import sweep  # imported at SessionStart alone, never per event

    try:
        _ = sweep(state_places(places))
    except KitFault as fault:
        return [fault.reason]
    except OSError as fault:
        return [Prose(f"the session state cannot be swept: {fault}")]
    return []


def answer(raw: EventBytes, settings: Settings) -> Answer:
    """The answer to one hook event given as ``raw`` bytes."""
    if switched_off(settings.off, WHOLE_KIT):
        return Answer(0, Prose(""), Prose(""))
    try:
        event = Event(payload_of(raw), settings.reading, settings.home, settings.places)
    except KitFault as fault:
        return refused([fault_text(fault.reason)])
    event.off = settings.off
    rules, faults = rules_for(event, settings)
    refusals: list[Prose] = []
    notes: list[Prose] = []
    kit = notes if kept_going(event.payload) else refusals  # where the kit's own faults go
    kit.extend(fault_text(reason) for reason in faults)
    broken: list[Prose] = swept(settings.places) if event.name == "SessionStart" else []
    unread: Unreadable | None = None
    if event.name == "PreToolUse" and event.tool in SHELL_TOOLS:
        try:
            _ = event.call()
        except KitFault as fault:
            broken.append(fault.reason)
        except Unreadable as fault:
            unread = fault
        try:
            _ = event.machine()
        except KitFault as fault:
            broken.append(fault.reason)
    reads = not switched_off(settings.off, UNREADABLE)
    for rule in rules:
        try:
            verdict = rule.judge(event)
        except KitFault as fault:
            broken.append(fault.reason)
            continue
        except Unreadable as fault:
            unread = fault
            continue
        except MemoryError:
            raise
        except Exception as fault:  # noqa: BLE001 - a rule that raises is a fault the refusal names
            broken.append(Prose(f"the rule {rule.name} failed: {type(fault).__name__}: {fault}"))
            continue
        if verdict is None:
            continue
        if not isinstance(verdict, Verdict) or verdict.kind not in ("refuse", "context"):
            broken.append(Prose(f"the rule {rule.name} gave an answer that is not a Verdict"))
        else:
            (refusals if verdict.kind == "refuse" else notes).append(Prose(f"guard {rule.name}: {verdict.text}"))
    kit.extend(fault_text(reason) for reason in dict.fromkeys(broken))
    if unread is not None and reads:
        kit.append(unreadable_text(unread))
    if refusals:
        return refused([*refusals, *notes])
    if notes:
        return noted(event.name, notes)
    return Answer(0, Prose(""), Prose(""))


def environment() -> Environment:
    """The hook's own environment, as the call model's parameters fall back to it."""
    return {VarName(name): ShellText(value) for name, value in os.environ.items()}
