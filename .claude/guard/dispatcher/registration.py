"""The guard kit's hook registration: its one command is registered under each hook event a rule reads, and no other.

The rules are those of a rules folder, the kit's rules/ unless another is named, found and loaded as the dispatcher
finds and loads them, a rule switched off with the others. At a tool event the entry's matcher names every tool the
rules there read, sorted and joined by `|`. events() gives each event with its matcher, entries() the settings file's
`hooks` mapping registering a command under each, and missing() one line for each event and tool a rule reads that no
command hook of a settings file running exactly that command covers, no line when it misses nothing. A matcher that
is absent, empty, `*` or `.*` matches every tool, as Claude Code reads one; any other covers a tool whose whole name
matches it as a regular expression, so `Write` and `Edit|Write` cover the names they spell, and a matcher that is no
regular expression covers nothing.

EXCEPTIONS, each with the failure it holds:
  * a rule with TOOLS None reads every tool, so its event takes the matcher `*` and only a match-all matcher covers
    it: a matcher of names would leave a tool no name spells, an MCP tool or one a later Claude Code adds, unjudged.
  * an event that carries no tool takes no matcher, and only a match-all matcher covers it: the rules there read
    every firing of the event, and no tool name says which firings a matcher would keep.
"""

import os
import re
from typing import NewType, NotRequired, TypedDict

from dispatcher.core import RULES, FolderPath, HookEvent, Rule, ToolName, loaded, rule_files
from dispatcher.verdict import Prose

Command = NewType("Command", str)
"""The text a command hook runs: the kit's dispatcher as a settings file spells it."""
Matcher = NewType("Matcher", str)
"""A hook entry's matcher: the tools at which its hooks run."""
HookKind = NewType("HookKind", str)
"""A hook's type in a settings file: command, or a kind that runs no command."""

TOOL_EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch"})
COMMAND = HookKind("command")
EVERY_TOOL = Matcher("*")
MATCH_ALL = frozenset({Matcher(""), EVERY_TOOL, Matcher(".*")})


class Hook(TypedDict):
    """One hook of an entry: its kind and the command it runs."""

    type: HookKind
    command: Command


class Entry(TypedDict):
    """One entry under an event: its matcher, absent where it matches every tool, and its hooks."""

    matcher: NotRequired[Matcher]
    hooks: list[Hook]


type Hooks = dict[HookEvent, list[Entry]]


class SettingsFile(TypedDict):
    """A settings file, read for its hooks alone."""

    hooks: NotRequired[Hooks]


def rules_in(folder: FolderPath) -> list[Rule]:
    """Every rule file of ``folder``, found and loaded as the dispatcher does, the switches unread; a KitFault where
    one cannot load."""
    return [loaded(FolderPath(os.path.join(folder, entry)), name) for entry, name in rule_files(folder)]


def reads(folder: FolderPath) -> dict[HookEvent, frozenset[ToolName] | None]:
    """Each event a rule names, in sorted order, with the tools its rules read there, None for every tool."""
    rules = rules_in(folder)
    found: dict[HookEvent, frozenset[ToolName] | None] = {}
    for event in sorted({event for rule in rules for event in rule.events}):
        at = [rule for rule in rules if event in rule.events]
        found[event] = (None if event not in TOOL_EVENTS or any(rule.tools is None for rule in at)
                        else frozenset(tool for rule in at for tool in rule.tools or ()))
    return found


def events(rules: FolderPath = RULES) -> dict[HookEvent, Matcher | None]:
    """Each event a rule reads, with its entry's matcher, None at an event that carries no tool."""
    return {event: (None if event not in TOOL_EVENTS else EVERY_TOOL if tools is None
                    else Matcher("|".join(sorted(tools))))
            for event, tools in reads(rules).items()}


def entries(command: Command, rules: FolderPath = RULES) -> Hooks:
    """The settings file's ``hooks`` mapping: ``command`` registered under each event a rule reads."""
    hooks: Hooks = {}
    for event, matcher in events(rules).items():
        entry: Entry = {"hooks": [{"type": COMMAND, "command": command}]}
        if matcher is not None:
            entry["matcher"] = matcher
        hooks[event] = [entry]
    return hooks


def covers(matcher: Matcher | None, tool: ToolName | None) -> bool:
    """Whether an entry with ``matcher`` runs at ``tool``, None standing for every tool."""
    if matcher is None or matcher in MATCH_ALL:
        return True
    if tool is None:
        return False
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return False


def missing(settings: SettingsFile, command: Command, rules: FolderPath = RULES) -> list[Prose]:
    """One line for each event and tool a rule reads that no command hook of ``settings`` running exactly ``command``
    covers."""
    lines: list[Prose] = []
    for event, tools in reads(rules).items():
        matchers = [entry.get("matcher") for entry in settings.get("hooks", {}).get(event, [])
                    if any(hook["type"] == COMMAND and hook["command"] == command for hook in entry["hooks"])]
        if not matchers:
            lines.append(Prose(f"{event}: no hook runs {command}"))
            continue
        for tool in [None] if tools is None else sorted(tools):
            if not any(covers(matcher, tool) for matcher in matchers):
                lines.append(Prose(f"{event}: no hook running {command} matches {tool or 'every tool'}"))
    return lines
