"""The ignored-declaration rule, through the dispatcher with the kit's own rules: a work tree's `.claude/guard.toml`
that git ignores is named once per claude process, with the rule hiding it and the fix, and the call goes on; one git
keeps, a work tree with none, a folder outside every repository and an event naming no folder give nothing; git that
fails is a fault of the kit; an event the rule does not judge loads neither the check nor the state.

Each case has a home, its repositories, a sessions folder and a proc tree of its own in the temporary folder, two
claude processes standing in it, and every git it runs, the rule's own included, reads no user or system
configuration."""

import errno
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import NewType
from unittest import mock

from declarations.ignored import hidden
from declarations.lookup import NO_PROJECT, Facts
from declarations.schema import Machine
from dispatcher.core import AgentId, EventBytes, FolderPath, HookEvent, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import KitFault, Prose
from dispatcher.words import Environment, VarName
from state.session import BootId, Pid, Places, ProcessKey, Session, StartTicks
from tests.test_declarations import NONE_DECLARED
from tests.test_session_state import proc_tree, stat_line

KIT = Path(__file__).resolve().parents[1]
ENTRY = KIT / "dispatcher" / "dispatch.py"
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
NO_CONFIG = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
IgnoreText = NewType("IgnoreText", str)
RepoName = NewType("RepoName", str)
GitAnswer = NewType("GitAnswer", bytes)
Line = NewType("Line", int)

FIRST, SECOND = Pid(10), Pid(11)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")
DECLARED = "schema = 1\n"
FOLDER_RULE = IgnoreText("build/\n.claude/\n")
FIXED = IgnoreText(".claude/*\n!.claude/guard.toml\n")
REPORTER = """import sys

from dispatcher.verdict import Prose, context

EVENTS = frozenset({"Stop"})
TOOLS = None


def judge(event):
    held = [name for name in ("declarations.ignored", "declarations.lookup", "state.session") if name in sys.modules]
    return context(Prose(" ".join(held) or "none"))
"""


def git(cwd: Path, *words: ShellText) -> None:
    """Run git in ``cwd`` with no user or system configuration."""
    _ = subprocess.run(["git", "-c", "init.defaultBranch=main", *words], cwd=cwd, env={**os.environ, **NO_CONFIG},
                       check=True, capture_output=True)


def note(repo: Path, pattern: Prose, source: Path, line: Line) -> Prose:
    """The note naming the declaration of ``repo`` hidden by ``pattern`` at ``source``:``line``."""
    return Prose(f"guard ignored-declaration: git ignores the declaration {repo / '.claude' / 'guard.toml'}, by "
                 f"`{pattern}` at {source}:{line}, so no clone, branch or review carries it. The fix: "
                 "`!.claude/guard.toml` after that rule, and `.claude/*` in place of a rule ignoring the `.claude/` "
                 "folder whole, since git re-includes no file inside a folder it ignores.")


def folder_note(repo: Path) -> Prose:
    """The note naming the declaration of ``repo`` hidden by FOLDER_RULE's `.claude/` line."""
    return note(repo, Prose(".claude/"), repo / ".gitignore", Line(2))


class Case(unittest.TestCase):
    """A case with a home, a switch folder, a sessions folder and a proc tree holding FIRST and SECOND."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("home", "off"):
            (self.root / name).mkdir()
        machine = self.root / "home" / ".config" / "claude-guard" / "machine.toml"
        machine.parent.mkdir(parents=True)
        _ = machine.write_text(NONE_DECLARED, encoding="utf-8")
        proc = proc_tree(self.root, BOOT, {pid: stat_line(pid, START) for pid in (FIRST, SECOND)})
        self.places = Places(self.root / "sessions", proc)
        _ = self.enterContext(mock.patch.dict(os.environ, NO_CONFIG))

    def repository(self, name: RepoName, ignores: IgnoreText | None, *, declared: bool = True) -> Path:
        """A git work tree named ``name`` with a `.claude/` folder, ``ignores`` as its .gitignore where given, and
        its declaration where ``declared``."""
        repo = self.root / name
        (repo / ".claude").mkdir(parents=True)
        git(repo, ShellText("init"), ShellText("-q"))
        if ignores is not None:
            _ = (repo / ".gitignore").write_text(ignores, encoding="utf-8")
        if declared:
            _ = (repo / ".claude" / "guard.toml").write_text(DECLARED, encoding="utf-8")
        return repo

    def heard(self, cwd: Path | None, name: HookEvent = "PreToolUse", pid: Pid | None = FIRST,
              agent: AgentId | None = None) -> Prose | None:
        """The note the kit's rules give an event ``name`` in ``cwd`` (none where None) of process ``pid`` (no
        CLAUDE_PID where None) from ``agent``, a Read call at PreToolUse; None where they pass it with nothing."""
        env: Environment = {VarName("HOME"): ShellText(str(self.root / "home"))}
        if pid is not None:
            env[VarName("CLAUDE_PID")] = ShellText(str(pid))
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.root / "off")),
                            ReadSettings(READER, Seconds(5.0), env), self.root / "home", self.places)
        fields = {"hook_event_name": name, "session_id": "s"}
        if cwd is not None:
            fields["cwd"] = str(cwd)
        if name == "PreToolUse":
            fields["tool_name"] = "Read"
        if agent is not None:
            fields["agent_id"] = agent
        got = answer(EventBytes(json.dumps(fields).encode()), settings)
        self.assertEqual((got.status, got.stderr), (0, ""))
        return Prose(json.loads(got.stdout)["hookSpecificOutput"]["additionalContext"]) if got.stdout else None


class Ignored(Case):
    """A declaration git ignores is named with the ignore file, the line and the pattern hiding it, and the fix."""

    def test_a_folder_rule_is_named_at_session_start(self) -> None:
        repo = self.repository(RepoName("repo"), FOLDER_RULE)
        self.assertEqual(self.heard(repo, "SessionStart"), folder_note(repo))

    def test_a_call_from_inside_the_work_tree_names_it(self) -> None:
        repo = self.repository(RepoName("repo"), FOLDER_RULE)
        (repo / "src").mkdir()
        self.assertEqual(self.heard(repo / "src"), folder_note(repo))
        excluded = self.repository(RepoName("excluded"), None)
        exclude = excluded / ".git" / "info" / "exclude"
        _ = exclude.write_text(exclude.read_text(encoding="utf-8") + ".claude\n", encoding="utf-8")
        line = Line(len(exclude.read_text(encoding="utf-8").splitlines()))
        self.assertEqual(self.heard(excluded), note(excluded, Prose(".claude"), exclude, line))

    def test_the_hook_s_own_git_variables_are_not_read(self) -> None:
        repo = self.repository(RepoName("repo"), FOLDER_RULE)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "nowhere")}):
            self.assertEqual(self.heard(repo), folder_note(repo))


class Kept(Case):
    """A declaration git keeps is not named: no rule matches it, the fix re-includes it, or git tracks it."""

    def test_a_declaration_git_keeps_is_not_named(self) -> None:
        tracked = self.repository(RepoName("tracked"), FOLDER_RULE)
        git(tracked, ShellText("add"), ShellText("-f"), ShellText(".claude/guard.toml"))
        repos = {"no rule matches it": self.repository(RepoName("plain"), None),
                 "the fix re-includes it": self.repository(RepoName("fixed"), FIXED), "git tracks it": tracked}
        for case, repo in repos.items():
            with self.subTest(case=case):
                self.assertIsNone(self.heard(repo))


class Undeclared(Case):
    """Nothing is named for a work tree with no declaration, and outside every repository no state is read."""

    def test_nothing_is_named_without_a_declaration(self) -> None:
        self.assertIsNone(self.heard(self.repository(RepoName("bare"), FOLDER_RULE, declared=False)))

    def test_outside_every_repository_no_state_is_read(self) -> None:
        (self.root / "plain").mkdir()
        self.assertIsNone(self.heard(self.root / "plain", pid=None))
        self.assertIsNone(self.heard(None, "SessionStart", pid=None))


class Once(Case):
    """A process checks a work tree once, whichever of its agents calls, and each work tree on its own, keeping the
    check as a notice, which no rule reading watches meets."""

    def first(self) -> Session:
        """The session state of FIRST as its main thread keeps it."""
        return Session(self.places, ProcessKey(BootId(BOOT.replace("-", "")), FIRST, START), None)

    def test_a_work_tree_is_named_once_per_process(self) -> None:
        repo = self.repository(RepoName("repo"), FOLDER_RULE)
        self.assertEqual(self.heard(repo), folder_note(repo))
        self.assertIsNone(self.heard(repo, agent=AgentId("a1")))
        self.assertIsNone(self.heard(repo, "SessionStart"))
        self.assertIsNone(self.heard(repo))
        self.assertEqual([record.ident for record in self.first().records("notice")], [f"ignored-declaration:{repo}"])

    def test_the_check_is_a_notice_and_no_watch(self) -> None:
        repo = self.repository(RepoName("repo"), FOLDER_RULE)
        self.assertEqual(self.heard(repo), folder_note(repo))
        self.assertEqual({kind: [record.ident for record in self.first().records(kind)]
                          for kind in ("run", "watch", "workflow", "notice")},
                         {"run": [], "watch": [], "workflow": [], "notice": [f"ignored-declaration:{repo}"]})

    def test_each_work_tree_is_named(self) -> None:
        first, second = (self.repository(RepoName(name), FOLDER_RULE) for name in ("first", "second"))
        self.assertEqual(self.heard(first), folder_note(first))
        self.assertEqual(self.heard(second), folder_note(second))
        self.assertIsNone(self.heard(first))

    def test_a_work_tree_checked_stays_checked(self) -> None:
        repo = self.repository(RepoName("repo"), None)
        self.assertIsNone(self.heard(repo))
        _ = (repo / ".gitignore").write_text(FOLDER_RULE, encoding="utf-8")
        self.assertIsNone(self.heard(repo))
        self.assertEqual(self.heard(repo, pid=SECOND), folder_note(repo))


class Processes(Case):
    """Each claude process names the declaration once of its own."""

    def test_each_process_names_it(self) -> None:
        repo = self.repository(RepoName("repo"), FOLDER_RULE)
        self.assertEqual(self.heard(repo, pid=FIRST), folder_note(repo))
        self.assertIsNone(self.heard(repo, pid=FIRST))
        self.assertEqual(self.heard(repo, pid=SECOND), folder_note(repo))
        self.assertIsNone(self.heard(repo, pid=SECOND))


class Faults(unittest.TestCase):
    """git that cannot run, outruns its limit, fails or gives an answer the check does not read is a KitFault."""

    def test_git_failing_is_a_kit_fault(self) -> None:
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root)
        file = root / ".claude" / "guard.toml"
        file.parent.mkdir()
        facts = Facts(lambda: Machine(None, None, None, None, ()), NO_PROJECT, file, root)
        said = GitAnswer(b".gitignore\x002\x00.claude/\x00.claude/guard.toml\x00")
        cases = {
            "it outran its limit": (subprocess.TimeoutExpired(["git"], 10.0), None,
                                    f"git outran its 10 s limit saying whether it ignores {file}"),
            "it cannot run": (FileNotFoundError(errno.ENOENT, "No such file or directory"), None,
                              "git cannot run: No such file or directory"),
            "a failing status": (None, subprocess.CompletedProcess([], 128, said, b"fatal: broken\nhint: more\n"),
                                 f"git cannot say whether it ignores {file}: fatal: broken"),
            "a short answer": (None, subprocess.CompletedProcess([], 0, b".gitignore\x002\x00", b""),
                               f"git cannot say whether it ignores {file}: no answer it reads"),
            "a line that is no number": (None, subprocess.CompletedProcess([], 0, said.replace(b"2", b"two"), b""),
                                         f"git cannot say whether it ignores {file}: no answer it reads"),
        }
        for case, (raised, done, reason) in cases.items():
            with self.subTest(case=case), mock.patch("declarations.ignored.subprocess.run", side_effect=raised,
                                                     return_value=done), self.assertRaises(KitFault) as caught:
                _ = hidden(facts)
            self.assertEqual(caught.exception.reason, reason)
        with mock.patch("declarations.ignored.subprocess.run",
                        return_value=subprocess.CompletedProcess([], 1, b"", b"")) as run:
            self.assertIsNone(hidden(facts))
        self.assertEqual(run.call_args.kwargs["timeout"], 10.0)
        with self.assertRaises(KitFault) as caught:
            _ = hidden(facts)
        self.assertTrue(caught.exception.reason.startswith(f"git cannot say whether it ignores {file}: fatal: "),
                        caught.exception.reason)


class Light(Case):
    """dispatch.py loading the rule for an event it does not judge imports neither the check nor the state."""

    def test_an_event_the_rule_does_not_judge_loads_neither(self) -> None:
        rules = self.root / "rules"
        rules.mkdir()
        (rules / "ignored_declaration.py").symlink_to(KIT / "rules" / "ignored_declaration.py")
        _ = (rules / "z.py").write_text(REPORTER, encoding="utf-8")
        stop = json.dumps({"hook_event_name": "Stop", "cwd": str(self.root), "session_id": "s"}).encode()
        done = subprocess.run([str(ENTRY), f"--rules={rules}"], input=stop, capture_output=True, check=False,
                              env={**os.environ, "HOME": str(self.root / "home")})
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        self.assertEqual(json.loads(done.stdout), {"systemMessage": "guard z: none"})


if __name__ == "__main__":
    _ = unittest.main()
