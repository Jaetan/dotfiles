"""The call model of a commit dribble, built once for the event that asks it (rules/_dribble.py).

model() builds `bash DRIBBLE` from the dribble's folder once for an event however often that event asks, and again
for another event or for the dribble at another size or modification time; where no dribble is, it gives None and
builds nothing. Through the dispatcher, one PostToolUse event whose dribble the entry-points and merge-dribble rules
both read builds it once. The entry-points rule builds nothing for a dribble older than HEAD, and each rule takes a
dribble gone before its model is built as no dribble.

Each case has a git work tree with one commit dated 2020-01-01 and a dribble newer than it, a sessions folder and a
proc tree of its own in the temporary folder, and build replaced by a stand-in that counts its calls and builds."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NewType
from unittest import mock

from dispatcher.core import Event, EventBytes, FolderPath, Settings, answer, payload_of
from dispatcher.model import Call, ReadSettings, build
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import Directory, Environment, VarName
from rules import _dribble, entry_points, merge_dribble
from rules._dribble import Nanoseconds
from state.session import Pid, Places, StartTicks
from tests.test_entry_points import GIT, HEAD
from tests.test_session_state import proc_tree, stat_line

Script = NewType("Script", str)
type Built = tuple[ShellText, Directory | None, ReadSettings]

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = frozenset({"entry_points.py", "merge_dribble.py"})
DRIBBLE = ".commands-to-run.sh"
MERGE = Script("gh pr merge 5\n")
PID = Pid(10)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")


class Case(unittest.TestCase):
    """A case with a git work tree holding a dribble newer than HEAD, and build counted."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_DATE": f"@{HEAD} +0000", "GIT_COMMITTER_DATE": f"@{HEAD} +0000"}
        for line in ("init -q", "commit -q --allow-empty -m c"):
            _ = subprocess.run([*GIT, "-C", str(self.repo), *shlex.split(line)], env=env, check=True,
                               capture_output=True, timeout=30)
        self.dribble = self.repo / DRIBBLE
        _ = self.dribble.write_text(MERGE, encoding="utf-8")
        self.places = Places(self.root / "sessions", proc_tree(self.root, BOOT, {PID: stat_line(PID, START)}))
        env_read: Environment = {VarName("HOME"): ShellText(str(self.root)),
                                 VarName("CLAUDE_PID"): ShellText(str(PID))}
        self.reading = ReadSettings(READER, Seconds(5.0), env_read)
        self.builds: list[Built] = []
        _ = self.enterContext(mock.patch.object(_dribble, "build", side_effect=self.counted))

    def counted(self, text: ShellText, cwd: Directory | None, settings: ReadSettings) -> Call:
        """Build as build does, counting the call."""
        self.builds.append((text, cwd, settings))
        return build(text, cwd, settings)

    def raw(self) -> EventBytes:
        """The bytes of a PostToolUse event of a Bash call in the work tree."""
        return EventBytes(json.dumps({"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash",
                                      "cwd": str(self.repo), "tool_input": {"command": "true"}}).encode())

    def event(self) -> Event:
        """A PostToolUse event of a Bash call in the work tree."""
        return Event(payload_of(self.raw()), self.reading, self.root, self.places)

    def rewrite(self, text: Script, when: Nanoseconds) -> None:
        """Write ``text`` as the dribble with the modification time ``when``."""
        _ = self.dribble.write_text(text, encoding="utf-8")
        os.utime(self.dribble, ns=(when, when))


class Model(Case):
    """model() builds once per event and version of the dribble, and nothing where no dribble is."""

    def test_the_dribble_is_built_as_bash_runs_it_from_its_folder(self) -> None:
        _ = _dribble.model(self.dribble, self.event())
        self.assertEqual(self.builds, [(ShellText(f"bash {shlex.quote(str(self.dribble))}"),
                                        Directory(str(self.repo)), self.reading)])

    def test_a_dribble_asked_twice_in_one_event_is_built_once(self) -> None:
        event = self.event()
        first = _dribble.model(self.dribble, event)
        self.assertIsNotNone(first)
        self.assertIs(_dribble.model(self.dribble, event), first)
        self.assertEqual(len(self.builds), 1)

    def test_another_event_builds_it_again(self) -> None:
        _ = _dribble.model(self.dribble, self.event())
        _ = _dribble.model(self.dribble, self.event())
        self.assertEqual(len(self.builds), 2)

    def test_two_dribbles_of_one_size_and_time_in_one_event_are_built_each(self) -> None:
        event = self.event()
        when = Nanoseconds(self.dribble.stat().st_mtime_ns)
        other = self.root / DRIBBLE
        _ = other.write_text(MERGE, encoding="utf-8")
        os.utime(other, ns=(when, when))
        first = _dribble.model(self.dribble, event)
        self.assertIsNot(_dribble.model(other, event), first)
        self.assertEqual(len(self.builds), 2)

    def test_no_dribble_gives_none_and_builds_nothing(self) -> None:
        self.dribble.unlink()
        self.assertIsNone(_dribble.model(self.dribble, self.event()))
        self.assertEqual(self.builds, [])

    def test_a_dribble_of_another_size_is_built_again(self) -> None:
        event = self.event()
        when = Nanoseconds(self.dribble.stat().st_mtime_ns)
        first = _dribble.model(self.dribble, event)
        self.rewrite(Script("gh pr merge 50\n"), when)
        self.assertIsNot(_dribble.model(self.dribble, event), first)
        self.assertEqual(len(self.builds), 2)

    def test_a_dribble_of_another_modification_time_is_built_again(self) -> None:
        event = self.event()
        when = Nanoseconds(self.dribble.stat().st_mtime_ns)
        first = _dribble.model(self.dribble, event)
        self.rewrite(Script("gh pr merge 6\n"), Nanoseconds(when + 1))
        self.assertIsNot(_dribble.model(self.dribble, event), first)
        self.assertEqual(len(self.builds), 2)


class Rules(Case):
    """The two rules reading the dribble share its one model, and each takes a dribble gone as none."""

    def test_one_event_both_rules_read_builds_the_dribble_once(self) -> None:
        off = self.root / "off"
        off.mkdir()
        for rule in (KIT / "rules").glob("[!_]*.py"):
            if rule.name not in OWN:
                (off / rule.stem.replace("_", "-")).touch()
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(off)), self.reading, self.root,
                            self.places)
        got = answer(self.raw(), settings)
        self.assertEqual(got.status, 2, got.stderr)
        self.assertIn("  * `gh pr merge 5` merges with `gh pr merge`", got.stderr)
        self.assertEqual(len(self.builds), 1)

    def test_the_entry_points_rule_builds_nothing_for_a_dribble_older_than_head(self) -> None:
        os.utime(self.dribble, (HEAD - 10, HEAD - 10))
        self.assertIsNone(entry_points.pending(self.repo, self.event()))
        self.assertEqual(self.builds, [])

    def test_a_dribble_gone_before_its_model_is_built_is_none_to_both_rules(self) -> None:
        with mock.patch.object(entry_points, "model", return_value=None):
            self.assertIsNone(entry_points.pending(self.repo, self.event()))
        with mock.patch.object(merge_dribble, "model", return_value=None):
            self.assertIsNone(merge_dribble.judge(self.event()))


if __name__ == "__main__":
    _ = unittest.main()
