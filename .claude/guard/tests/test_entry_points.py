"""The entry-points rule, through the dispatcher with every other rule of the kit switched off: while a work tree holds
a pending commit dribble, a call is refused naming each changed entry point no run kept since its last edit; a run by
its path, through bash, through python by path or by -m kept after the edit passes, one before it does not; a change
that is no entry point, a deletion, no dribble, a dribble that commits nothing and one older than HEAD pass.

Each case has a git work tree with one commit dated 2020-01-01, a sessions folder and a proc tree of its own in the
temporary folder, one claude process standing in it."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NewType

from dispatcher.core import EventBytes, ExitStatus, FolderPath, HookEvent, Settings, ToolName, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import Environment, VarName
from state.session import Pid, Places, StartTicks
from tests.test_dispatcher import machine_home
from tests.test_session_state import proc_tree, stat_line

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = "entry_points.py"
Source = NewType("Source", str)
Name = NewType("Name", str)
Mode = NewType("Mode", int)
Epoch = NewType("Epoch", int)

PID = Pid(10)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")
HEAD = Epoch(1_577_836_800)
GUARDED = Source('"""A tool."""\n\nif __name__ == "__main__":\n    print("tool")\n')
EDITED = Source(GUARDED + "# edited\n")
SCRIPT = Source("#!/bin/sh\necho script\n")
BASH_TOO = Source(": <<'TEXT'\nif __name__ == \"__main__\":\n    pass\nTEXT\n")
COMMIT = Source("set -euo pipefail\ngit commit -S -F message.txt\n")
TOOL, SH, DRIBBLE = Name("tool.py"), Name("run.sh"), Name(".commands-to-run.sh")
PLAIN, EXECUTABLE = Mode(0o644), Mode(0o755)
GIT = ("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c",
       "core.hooksPath=/dev/null")


class Case(unittest.TestCase):
    """A case with a work tree holding a pending commit dribble, a switch folder turning off every rule but this one,
    and a proc tree holding PID."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.off = self.root / "off"
        self.off.mkdir()
        for rule in (KIT / "rules").glob("[!_]*.py"):
            if rule.name != OWN:
                (self.off / rule.stem.replace("_", "-")).touch()
        self.places = Places(self.root / "sessions", proc_tree(self.root, BOOT, {PID: stat_line(PID, START)}))
        self.home = machine_home(self)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git(ShellText("init -q"))
        _ = self.write(Name("README"), Source("readme\n"))
        self.commit()
        _ = self.write(DRIBBLE, COMMIT)

    def git(self, line: ShellText, folder: Path | None = None) -> None:
        """Run git ``line`` in ``folder``, the case's work tree where None, dated as HEAD is."""
        env = {**os.environ, "GIT_AUTHOR_DATE": f"@{HEAD} +0000", "GIT_COMMITTER_DATE": f"@{HEAD} +0000"}
        _ = subprocess.run([*GIT, "-C", str(folder or self.repo), *shlex.split(line)], env=env, check=True,
                           capture_output=True, timeout=30)

    def commit(self) -> None:
        """Commit every file of the work tree, dated as HEAD is."""
        self.git(ShellText("add -A"))
        self.git(ShellText("commit -q -m c"))

    def write(self, name: Name, text: Source, mode: Mode = PLAIN, folder: Path | None = None) -> Path:
        """Write ``text`` to ``name`` in ``folder``, the case's work tree where None, with ``mode``; its path."""
        path = (folder or self.repo) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def answered(self, tool: ToolName, name: HookEvent, command: ShellText,
                 cwd: Path | None) -> tuple[ExitStatus, Prose]:
        """The dispatcher's exit status and stderr for an event ``name`` of a ``tool`` call running ``command`` in
        ``cwd``, an event naming no folder where None."""
        env: Environment = {VarName("HOME"): ShellText(str(self.home)), VarName("CLAUDE_PID"): ShellText(str(PID))}
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), self.home, self.places)
        fields = {"hook_event_name": name, "session_id": "s", "tool_name": tool, "tool_input": {"command": command}}
        if cwd is not None:
            fields["cwd"] = str(cwd)
        got = answer(EventBytes(json.dumps(fields).encode()), settings)
        return got.status, got.stderr

    def ran(self, command: ShellText, cwd: Path | None = None) -> None:
        """Hand the rule a Bash call running ``command`` in ``cwd`` before it runs, and see it pass."""
        self.assertEqual(self.answered(ToolName("Bash"), "PreToolUse", command, cwd or self.repo), (0, ""))

    def heard(self, tool: ToolName = ToolName("Edit"), name: HookEvent = "PostToolUse",
              cwd: Path | None = None) -> list[Path] | None:
        """The entry points the refusal of the event names, None where the call passes with nothing."""
        status, stderr = self.answered(tool, name, ShellText("true"), cwd or self.repo)
        if status == 0:
            self.assertEqual(stderr, "")
            return None
        self.assertEqual(status, 2, stderr)
        return sorted(Path(line[4:].split(", run as ")[0]) for line in stderr.splitlines() if line.startswith("  * "))


class Refused(Case):
    """Each changed entry point no run kept at its sha256 is refused while the dribble is pending."""

    def test_the_refusal_names_the_dribble_each_entry_point_and_its_form(self) -> None:
        tool = self.write(TOOL, GUARDED)
        script = self.write(SH, SCRIPT, EXECUTABLE)
        status, stderr = self.answered(ToolName("Edit"), "PostToolUse", ShellText("true"), self.repo)
        self.assertEqual(status, 2)
        self.assertIn(f"guard entry-points: the dribble {self.repo / DRIBBLE} commits entry points no run has read "
                      "since their last edit:\n\n"
                      f"  * {script}, run as `{script}`\n  * {tool}, run as `python3 {tool}`\n\n"
                      "Run each after its last edit, in the form named, by its path, through python or through bash, "
                      "sh or dash, and read\nits whole output: what an entry point prints is a claim about what the "
                      "code does, and an edit can make it false.", stderr)

    def test_a_modified_an_added_and_an_untracked_entry_point_never_run_are_refused(self) -> None:
        modified = self.write(Name("modified.py"), GUARDED)
        self.commit()
        _ = self.write(Name("modified.py"), EDITED)
        added = self.write(Name("added.py"), GUARDED)
        self.git(ShellText("add added.py"))
        untracked = self.write(Name("sub/untracked.py"), GUARDED)
        self.assertEqual(self.heard(), sorted([modified, added, untracked]))

    def test_an_executable_with_a_shebang_and_a_single_quoted_guard_are_entry_points(self) -> None:
        script = self.write(Name("run"), SCRIPT, EXECUTABLE)
        quoted = self.write(Name("quoted.py"), Source("if __name__ == '__main__' :\n    pass\n"))
        self.assertEqual(self.heard(), sorted([script, quoted]))

    def test_a_run_before_the_edit_is_refused(self) -> None:
        tool = self.write(TOOL, GUARDED)
        self.ran(ShellText("python3 tool.py"))
        _ = self.write(TOOL, EDITED)
        self.assertEqual(self.heard(), [tool])

    def test_only_the_last_run_of_a_path_is_kept(self) -> None:
        tool = self.write(TOOL, GUARDED)
        self.ran(ShellText("python3 tool.py"))
        _ = self.write(TOOL, EDITED)
        self.ran(ShellText("python3 tool.py"))
        _ = self.write(TOOL, GUARDED)
        self.assertEqual(self.heard(), [tool])

    def test_each_tool_that_writes_reads_it(self) -> None:
        tool = self.write(TOOL, GUARDED)
        for name in ("Bash", "Edit", "Write", "MultiEdit"):
            with self.subTest(tool=name):
                self.assertEqual(self.heard(ToolName(name)), [tool])

    def test_a_commit_tree_dribble_is_pending(self) -> None:
        _ = self.write(DRIBBLE, Source("git commit-tree -S HEAD^{tree} -m m\n"))
        tool = self.write(TOOL, GUARDED)
        self.assertEqual(self.heard(), [tool])

    def test_a_bare_name_is_no_run_by_its_path(self) -> None:
        script = self.write(Name("tool"), SCRIPT, EXECUTABLE)
        self.ran(ShellText("tool"))
        self.assertEqual(self.heard(), [script])

    def test_a_run_from_a_folder_not_known_is_not_kept(self) -> None:
        tool = self.write(TOOL, GUARDED)
        self.ran(ShellText(f'cd "$(date +%s)" && python3 {tool}'))
        self.assertEqual(self.heard(), [tool])

    def test_a_renamed_entry_point_is_its_new_path(self) -> None:
        _ = self.write(SH, SCRIPT, EXECUTABLE)
        _ = self.write(Name("xx/run.sh"), SCRIPT, EXECUTABLE)
        self.commit()
        self.git(ShellText("mv xx/run.sh moved.sh"))
        self.assertEqual(self.heard(), [self.repo / "moved.sh"])

    def test_a_tree_git_cannot_read_is_refused_naming_it(self) -> None:
        broken = self.root / "broken"
        (broken / ".git").mkdir(parents=True)
        _ = self.write(DRIBBLE, COMMIT, folder=broken)
        status, stderr = self.answered(ToolName("Edit"), "PostToolUse", ShellText("true"), broken)
        self.assertEqual(status, 2)
        self.assertIn(f"git status cannot read the work tree {broken}", stderr)

    def test_a_tree_with_no_commit_is_pending(self) -> None:
        fresh = self.root / "fresh"
        fresh.mkdir()
        self.git(ShellText("init -q"), fresh)
        _ = self.write(DRIBBLE, COMMIT, folder=fresh)
        tool = self.write(TOOL, GUARDED, folder=fresh)
        self.assertEqual(self.heard(cwd=fresh), [tool])


class Kept(Case):
    """A run after the last edit, in each form, lets the entry point pass."""

    def test_a_run_by_its_path_is_kept(self) -> None:
        _ = self.write(TOOL, Source("#!/usr/bin/env python3\n" + GUARDED), EXECUTABLE)
        self.ran(ShellText("./tool.py --help"))
        self.assertIsNone(self.heard())

    def test_a_run_by_its_absolute_path_from_another_folder_is_kept(self) -> None:
        script = self.write(SH, SCRIPT, EXECUTABLE)
        self.ran(ShellText(str(script)), self.root)
        self.assertIsNone(self.heard())

    def test_a_path_run_of_a_file_that_cannot_run_is_not_kept(self) -> None:
        tool = self.write(TOOL, BASH_TOO)
        self.ran(ShellText("./tool.py"))
        self.assertEqual(self.heard(), [tool])

    def test_a_run_through_bash_is_kept(self) -> None:
        _ = self.write(SH, SCRIPT, EXECUTABLE)
        self.ran(ShellText("bash -e run.sh"))
        self.assertIsNone(self.heard())

    def test_a_run_through_python_by_its_path_is_kept(self) -> None:
        _ = self.write(Name("sub/tool.py"), GUARDED)
        self.ran(ShellText("python3 -u ../sub/tool.py"), self.repo / "sub")
        self.assertIsNone(self.heard())

    def test_a_run_through_python_dash_m_is_kept(self) -> None:
        _ = self.write(Name("pkg/tool.py"), GUARDED)
        _ = self.write(Name("pkg/__main__.py"), GUARDED)
        self.ran(ShellText("python3 -m pkg.tool"))
        self.ran(ShellText("python3.14 -m pkg"))
        self.assertIsNone(self.heard())


    def test_a_run_of_a_link_s_target_keeps_the_link(self) -> None:
        _ = self.write(TOOL, GUARDED)
        (self.repo / "link.py").symlink_to(TOOL)
        self.ran(ShellText("python3 tool.py"))
        self.assertIsNone(self.heard())


class Passing(Case):
    """What is no changed entry point, and a tree with no pending commit dribble, pass."""

    def test_a_change_that_is_no_entry_point_passes(self) -> None:
        _ = self.write(Name("notes.txt"), GUARDED)
        _ = self.write(Name("lib.py"), Source("TEXT = 'if __name__ == \"__main__\":'\n"))
        _ = self.write(Name("plain.sh"), SCRIPT)
        _ = self.write(Name("data"), Source("no shebang\n"), EXECUTABLE)
        self.assertIsNone(self.heard())

    def test_a_deleted_entry_point_passes(self) -> None:
        _ = self.write(TOOL, GUARDED)
        _ = self.write(Name("staged.py"), GUARDED)
        self.commit()
        (self.repo / TOOL).unlink()
        self.git(ShellText("rm -q staged.py"))
        self.assertIsNone(self.heard())

    def test_no_dribble_passes(self) -> None:
        (self.repo / DRIBBLE).unlink()
        _ = self.write(TOOL, GUARDED)
        self.assertIsNone(self.heard())

    def test_a_dribble_that_commits_nothing_passes(self) -> None:
        _ = self.write(DRIBBLE, Source("echo commit\ngit push origin HEAD\n"))
        _ = self.write(TOOL, GUARDED)
        self.assertIsNone(self.heard())

    def test_a_dribble_older_than_head_passes(self) -> None:
        os.utime(self.repo / DRIBBLE, (HEAD - 10, HEAD - 10))
        _ = self.write(TOOL, GUARDED)
        self.assertIsNone(self.heard())

    def test_a_dribble_in_head_s_second_is_older(self) -> None:
        os.utime(self.repo / DRIBBLE, (HEAD, HEAD))
        _ = self.write(TOOL, GUARDED)
        self.assertIsNone(self.heard())

    def test_other_tools_and_events_pass(self) -> None:
        _ = self.write(TOOL, GUARDED)
        self.assertIsNone(self.heard(ToolName("Read")))
        self.assertIsNone(self.heard(ToolName("Edit"), "PreToolUse"))
        self.assertEqual(self.answered(ToolName("Edit"), "PreToolUse", ShellText("python3 tool.py"), self.repo), (0, ""))
        self.assertIsNone(self.heard(ToolName("Bash"), "PreToolUse"))
        self.assertEqual(self.heard(), [self.repo / TOOL])

    def test_an_event_naming_no_folder_passes(self) -> None:
        _ = self.write(TOOL, GUARDED)
        self.assertEqual(self.answered(ToolName("Edit"), "PostToolUse", ShellText("true"), None), (0, ""))

    def test_a_call_running_no_entry_point_reads_no_state(self) -> None:
        _ = self.write(TOOL, GUARDED)
        self.places = Places(self.places.sessions, proc_tree(self.root / "other", BOOT, {}))
        self.ran(ShellText("X=1; cat tool.py; python3 -c pass"))

    def test_a_command_of_a_name_not_known_keeps_nothing(self) -> None:
        tool = self.write(TOOL, GUARDED)
        self.ran(ShellText('"$(date +%s)" tool.py'))
        self.assertEqual(self.heard(), [tool])

    def test_a_call_outside_any_work_tree_passes(self) -> None:
        _ = self.write(TOOL, GUARDED, folder=self.root)
        _ = self.write(DRIBBLE, COMMIT, folder=self.root)
        self.assertIsNone(self.heard(cwd=self.root))

    def test_a_run_of_another_file_keeps_nothing_for_it(self) -> None:
        tool = self.write(TOOL, GUARDED)
        _ = self.write(Name("other.py"), GUARDED)
        _ = self.write(Name("lib.py"), Source("VALUE = 1\n"))
        self.ran(ShellText("python3 other.py"))
        self.ran(ShellText("python3 lib.py"))
        self.ran(ShellText("python3 -c 'import tool'"))
        self.ran(ShellText("cat tool.py"))
        self.assertEqual(self.heard(), [tool])


if __name__ == "__main__":
    _ = unittest.main()
