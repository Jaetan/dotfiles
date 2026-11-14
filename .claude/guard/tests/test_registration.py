"""The kit's registration: events() gives each event a rule reads with the matcher its rules there need, entries()
registers a command under each, and missing() names what a settings file misses of it. The cases read a stand-in
rules folder of their own, one rule reading every tool at PreToolUse and Stop, two naming tools at PostToolUse and
one more tool event each, one of them at SessionStart too, beside a helper and a file that is no rule; and the kit's
own rules/."""

import os
import shutil
import tempfile
import unittest
from typing import NewType

from dispatcher.core import FolderPath, HookEvent, ToolName, loaded
from dispatcher.registration import (RULES, TOOL_EVENTS, Command, Entry, Hook, HookKind, Matcher, SettingsFile, entries,
                                     events, missing)
from dispatcher.verdict import KitFault, Prose, RuleName

FileName = NewType("FileName", str)
"""A file's name in a stand-in rules folder."""
FileText = NewType("FileText", str)
"""The text of a stand-in rules folder's file."""

CMD = Command("/kit/dispatcher/dispatch.py")
JUDGE = "\n\ndef judge(event):\n    return None\n"
STAND_IN = {
    FileName("a_every.py"): FileText('EVENTS = frozenset({"PreToolUse", "Stop"})\nTOOLS = None\n' + JUDGE),
    FileName("b_named.py"): FileText('EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolBatch"})\n'
                                     'TOOLS = frozenset({"Write", "Bash"})\n' + JUDGE),
    FileName("c_named.py"): FileText('EVENTS = frozenset({"PostToolUse", "PostToolUseFailure", "SessionStart"})\n'
                                     'TOOLS = frozenset({"Monitor"})\n' + JUDGE),
    FileName("_shared.py"): FileText("VALUE = 1\n"),
    FileName("notes.txt"): FileText("not a rule\n"),
}
STAND_IN_EVENTS: tuple[HookEvent, ...] = ("PostToolBatch", "PostToolUse", "PostToolUseFailure", "PreToolUse",
                                           "SessionStart", "Stop")


def folder_of(files: dict[FileName, FileText]) -> FolderPath:
    """A new temporary folder holding ``files``."""
    folder = tempfile.mkdtemp()
    for name, text in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as out:
            out.write(text)
    return FolderPath(folder)


def hook(command: Command, kind: HookKind = HookKind("command")) -> Hook:
    """A hook of ``kind`` running ``command``."""
    return {"type": kind, "command": command}


def entry(matcher: Matcher | None, *hooks: Hook) -> Entry:
    """An entry with ``matcher``, the key left out where it is None, holding ``hooks``."""
    made: Entry = {"hooks": list(hooks)}
    if matcher is not None:
        made["matcher"] = matcher
    return made


class StandIn(unittest.TestCase):
    """Each case reads the stand-in rules folder."""

    def setUp(self) -> None:
        self.rules = folder_of(STAND_IN)
        self.addCleanup(shutil.rmtree, self.rules)

    def settings(self, under: dict[HookEvent, list[Entry]] | None = None) -> SettingsFile:
        """The settings entries() makes for the stand-in, each event of ``under`` given its entries there instead."""
        return {"hooks": entries(CMD, self.rules) | (under or {})}


class Events(StandIn):
    """events() gives each event a rule names, sorted, with the matcher its rules there need."""

    def test_each_event_takes_the_matcher_its_rules_need(self) -> None:
        given = events(self.rules)
        self.assertEqual(given, {"PostToolBatch": "Bash|Write", "PostToolUse": "Bash|Monitor|Write",
                                 "PostToolUseFailure": "Monitor", "PreToolUse": "*", "SessionStart": None,
                                 "Stop": None})
        self.assertEqual(tuple(given), STAND_IN_EVENTS)
        self.assertNotIn("SessionEnd", given)

    def test_a_rule_that_cannot_load_is_a_fault(self) -> None:
        broken = folder_of({FileName("bad_rule.py"): FileText("EVENTS = 1\n")})
        self.addCleanup(shutil.rmtree, broken)
        with self.assertRaises(KitFault) as caught:
            events(broken)
        self.assertIn("the rule bad-rule does not declare", caught.exception.reason)


class KitRules(unittest.TestCase):
    """Over the kit's own rules/, events() covers every rule's EVENTS and TOOLS, and the settings entries() makes
    miss nothing."""

    def test_every_rule_is_covered(self) -> None:
        given = events()
        for name in sorted(os.listdir(RULES)):
            stem, suffix = os.path.splitext(name)
            if suffix != ".py" or stem.startswith("_"):
                continue
            rule = loaded(FolderPath(os.path.join(RULES, name)), RuleName(stem.replace("_", "-")))
            for event in rule.events:
                with self.subTest(rule=stem, event=event):
                    self.assertIn(event, given)
                    matcher = given[event]
                    if event not in TOOL_EVENTS:
                        self.assertIsNone(matcher)
                    elif rule.tools is None:
                        self.assertEqual(matcher, "*")
                    else:
                        self.assertTrue(matcher == "*" or rule.tools <= set(str(matcher).split("|")))

    def test_the_settings_entries_makes_miss_nothing(self) -> None:
        self.assertEqual(missing({"hooks": entries(CMD)}, CMD), [])


class Entries(StandIn):
    """entries() registers the command under each event, with the event's matcher where it has one."""

    def test_the_command_stands_under_each_event(self) -> None:
        self.assertEqual(entries(CMD, self.rules), {
            "PostToolBatch": [{"matcher": "Bash|Write", "hooks": [{"type": "command", "command": CMD}]}],
            "PostToolUseFailure": [{"matcher": "Monitor", "hooks": [{"type": "command", "command": CMD}]}],
            "PostToolUse": [{"matcher": "Bash|Monitor|Write", "hooks": [{"type": "command", "command": CMD}]}],
            "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": CMD}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": CMD}]}],
            "Stop": [{"hooks": [{"type": "command", "command": CMD}]}]})


class Missing(StandIn):
    """missing() names every event and tool a rule reads that no command hook running exactly the command covers."""

    def test_the_settings_entries_makes_miss_nothing(self) -> None:
        self.assertEqual(missing(self.settings(), CMD, self.rules), [])

    def test_an_event_left_out_is_named(self) -> None:
        settings = self.settings()
        del settings["hooks"]["Stop"]
        self.assertEqual(missing(settings, CMD, self.rules), [f"Stop: no hook runs {CMD}"])
        self.assertEqual(missing({}, CMD, self.rules), [f"{event}: no hook runs {CMD}" for event in STAND_IN_EVENTS])

    def test_a_tool_its_matcher_does_not_cover_is_named(self) -> None:
        def lines(*made: Entry) -> list[Prose]:
            return missing(self.settings({"PostToolUse": list(made)}), CMD, self.rules)

        def named(*tools: ToolName) -> list[Prose]:
            return [Prose(f"PostToolUse: no hook running {CMD} matches {tool}") for tool in tools]

        bash, monitor, write = ToolName("Bash"), ToolName("Monitor"), ToolName("Write")
        self.assertEqual(lines(entry(Matcher("Monitor"), hook(CMD))), named(bash, write))
        self.assertEqual(lines(entry(Matcher("Bas|Monitor|Write"), hook(CMD))), named(bash))
        self.assertEqual(lines(entry(Matcher("("), hook(CMD))), named(bash, monitor, write))
        self.assertEqual(lines(entry(Matcher("B.sh|Monitor|Write"), hook(CMD))), [])
        self.assertEqual(lines(entry(Matcher("Bash"), hook(CMD)), entry(Matcher("Monitor|Write"), hook(CMD))), [])

    def test_an_entry_running_another_command_covers_nothing(self) -> None:
        for other in (hook(Command(f"{CMD} --rules=/x")), hook(CMD, HookKind("prompt"))):
            with self.subTest(hook=other):
                self.assertEqual(missing(self.settings({"Stop": [entry(None, other)]}), CMD, self.rules),
                                 [f"Stop: no hook runs {CMD}"])
        both = entry(None, hook(Command("/other")), hook(CMD))
        self.assertEqual(missing(self.settings({"Stop": [both]}), CMD, self.rules), [])

    def test_a_rule_reading_every_tool_is_not_covered_by_names(self) -> None:
        for matcher in (Matcher("Bash|Monitor|Write"), Matcher(".+")):
            with self.subTest(matcher=matcher):
                settings = self.settings({"PreToolUse": [entry(matcher, hook(CMD))]})
                self.assertEqual(missing(settings, CMD, self.rules),
                                 [f"PreToolUse: no hook running {CMD} matches every tool"])

    def test_an_event_carrying_no_tool_is_not_covered_by_names(self) -> None:
        settings = self.settings({"SessionStart": [entry(Matcher("Monitor"), hook(CMD))]})
        self.assertEqual(missing(settings, CMD, self.rules),
                         [f"SessionStart: no hook running {CMD} matches every tool"])

    def test_a_match_all_matcher_covers_every_tool_each_way(self) -> None:
        for matcher in (None, Matcher(""), Matcher("*"), Matcher(".*")):
            with self.subTest(matcher=matcher):
                settings = self.settings({event: [entry(matcher, hook(CMD))] for event in STAND_IN_EVENTS})
                self.assertEqual(missing(settings, CMD, self.rules), [])
