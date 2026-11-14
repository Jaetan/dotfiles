"""The writes-during-runs rule, through the dispatcher with every other rule switched off: a Bash or Monitor call that
writes into the git work tree of a live heavy run, a process the case's own proc tree shows, is refused naming the run
and the write; a write outside every tree, into another repository, with no live run or beside a light one passes,
as does a git command that only reads."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NamedTuple, NewType

from dispatcher.core import Answer, EventBytes, FolderPath, Settings, ToolInput, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from state.session import Pid
from state.session import Places as StatePlaces
from tests.test_declarations import NONE_DECLARED

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
CaseId = NewType("CaseId", str)
ProcLine = NewType("ProcLine", bytes)
Folder = Literal["root", "tools", "nested", "other", "broken", "elsewhere"]
Placeholder = Literal["ROOT", "OTHER", "ELSEWHERE"]
FOLDERS: tuple[Folder, ...] = ("root", "tools", "nested", "other", "broken", "elsewhere")
PLACEHOLDERS: dict[Placeholder, Folder] = {"ROOT": "root", "OTHER": "other", "ELSEWHERE": "elsewhere"}
PID = Pid(4242)
PYTEST = (ShellText("/usr/bin/python3"), ShellText("-m"), ShellText("pytest"), ShellText("tests/"))
DECLARED = 'schema = 1\n[[heavy]]\nprogram = "mybuild"\n[[heavy]]\nmodule = "tools.ci"\n[[heavy]]\nscript = "tools/run.sh"\n'
HEADLINE = Prose("this call writes into the git work tree of a live heavy run")
ADVICE = Prose("Wait for the run's completion notification, then make the edit; draft it in the scratchpad meanwhile.")
RUNS = Prose(f"where pid {PID} runs `")
EIGHTY_SEVEN = ShellText("x" * 87)  # the length that brings PYTEST's line to 121 characters
READ_ONLY = ("status", "diff", "show", "log", "ls-files", "ls-tree", "grep", "rev-parse", "cat-file", "blame", "describe")


class Case(NamedTuple):
    """A call, ROOT, OTHER and ELSEWHERE in it standing for those folders, the process its proc tree shows, the folder
    that process runs in, the folder the call runs from, and its tool."""

    call: ShellText
    process: tuple[ShellText, ...] = PYTEST
    runs_in: Folder = "root"
    cwd: Folder = "root"
    tool: ShellText = ShellText("Bash")


class Refusal(NamedTuple):
    """A refused case and what its refusal names besides the run and the advice."""

    case: Case
    names: tuple[Prose, ...]


type Refused = dict[CaseId, Refusal]
type Passed = dict[CaseId, Case]


class Places(NamedTuple):
    """A scratch folder holding the folders a case names, the home folder and the switch folder."""

    base: Path
    home: Path
    off: Path

    def folder(self, name: Folder) -> Path:
        """The folder ``name`` stands for: a repository's root declaring three heavy rows, its subfolder tools, a
        repository nested in it, another repository, one whose declaration breaks the schema, and a folder in no
        tree."""
        return {"root": self.base / "project", "tools": self.base / "project" / "tools",
                "nested": self.base / "project" / "nested", "other": self.base / "other",
                "broken": self.base / "broken", "elsewhere": self.base / "elsewhere"}[name]


def places() -> Places:
    """Fresh folders, every rule but writes-during-runs switched off, so a case judges this rule alone."""
    base = Path(tempfile.mkdtemp()).resolve()
    found = Places(base, base / "home", base / "off")
    for folder in (*map(found.folder, FOLDERS), found.home / ".config" / "claude-guard", found.off):
        folder.mkdir(parents=True)
    git = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    for repository in ("root", "nested", "other", "broken"):
        _ = subprocess.run(["git", "init", "-q", str(found.folder(repository))], env=git, check=True,
                           capture_output=True)
    for repository, declaration in (("root", DECLARED), ("broken", "schema = 99\n")):
        (found.folder(repository) / ".claude").mkdir()
        _ = (found.folder(repository) / ".claude" / "guard.toml").write_text(declaration, encoding="utf-8")
    _ = (found.home / ".config" / "claude-guard" / "machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
    for module in (KIT / "rules").glob("*.py"):
        if not module.stem.startswith("_") and module.stem != "writes_during_runs":
            (found.off / module.stem.replace("_", "-")).touch()
    return found


def line(process: tuple[ShellText, ...]) -> ProcLine:
    """A command line as /proc/PID/cmdline holds it: each word ended by a NUL."""
    return ProcLine(b"".join(word.encode() + b"\0" for word in process))


class Rule(unittest.TestCase):
    """Each case refused naming the run and each of its writes and the form that passes, or passed."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def proc(self, case: CaseId, process: tuple[ShellText, ...], runs_in: Folder) -> Path:
        """A proc tree of the case's own: process PID running ``process`` in ``runs_in``, beside an entry that is no
        pid and a process whose folder cannot be read, each running the same command line."""
        tree = self.at.base / f"proc-{case}"
        for name, linked in ((str(PID), True), ("self", True), ("1", False)):
            entry = tree / name
            entry.mkdir(parents=True)
            _ = (entry / "cmdline").write_bytes(line(process))
            if linked:
                (entry / "cwd").symlink_to(self.at.folder(runs_in))
        return tree

    def judged(self, case_id: CaseId, case: Case, proc: Path | None = None) -> Answer:
        """The dispatcher's answer to a PreToolUse event for the case, processes read from ``proc``, the case's own
        proc tree where None."""
        call = case.call
        for name, folder in PLACEHOLDERS.items():
            call = ShellText(call.replace(name, str(self.at.folder(folder))))
        given: ToolInput = {"command": call}
        fields = {"hook_event_name": "PreToolUse", "tool_name": case.tool, "cwd": str(self.at.folder(case.cwd)),
                  "tool_input": given}
        places = StatePlaces(self.at.base / "sessions", self.proc(case_id, case.process, case.runs_in)
                             if proc is None else proc)
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.at.off)),
                            ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(self.at.home))}),
                            home=self.at.home, places=places)
        return answer(EventBytes(json.dumps(fields).encode()), settings)

    def check(self, refused: Refused, passed: Passed) -> None:
        """Every refused case answered 2, its stderr naming the run, the advice and each of its names, never the
        process whose folder cannot be read; every passed one 0."""
        for case_id, (case, names) in refused.items():
            with self.subTest(case=case_id, call=case.call):
                got = self.judged(case_id, case)
                self.assertEqual(got.status, 2, got.stderr)
                for name in (HEADLINE, ADVICE, RUNS, *names):
                    self.assertIn(name, got.stderr)
                self.assertNotIn(Prose("pid 1 "), got.stderr)
        for case_id, case in passed.items():
            with self.subTest(case=case_id, call=case.call):
                got = self.judged(case_id, case)
                self.assertEqual((got.status, got.stderr), (0, ""))

    def test_a_write_into_the_runs_tree(self) -> None:
        tree = Prose(f"lies in {self.at.folder('root')}, {RUNS}")
        self.check({
            CaseId("W1"): Refusal(Case(ShellText("echo x > notes.txt")),
                                  (Prose(f"  * `notes.txt` (created) {tree}/usr/bin/python3 -m pytest tests/`"),)),
            CaseId("W2"): Refusal(Case(ShellText("cp /tmp/a tools/b")), (Prose("`tools/b` (destination)"),)),
            CaseId("W3"): Refusal(Case(ShellText("git add -A")), (Prose("the repository `git add -A` writes"),)),
            CaseId("W4"): Refusal(Case(ShellText("echo x > notes.txt"), tool=ShellText("Monitor")),
                                  (Prose("`notes.txt`"),)),
            CaseId("W5"): Refusal(Case(ShellText("cd ROOT && touch x"), cwd="elsewhere"), (Prose("`x` ("),)),
            CaseId("W6"): Refusal(Case(ShellText("git -C ROOT add x"), cwd="elsewhere"), (Prose("the repository"),)),
            CaseId("W7"): Refusal(Case(ShellText("git -c user.name=t -C ROOT add x"), cwd="elsewhere"),
                                  (Prose("the repository"),)),
            CaseId("W8"): Refusal(Case(ShellText("echo x > notes.txt"), runs_in="tools"), (tree,)),
            CaseId("W9"): Refusal(Case(ShellText("echo x > OTHER/../project/notes.txt"), cwd="other"), (tree,)),
            CaseId("W10"): Refusal(Case(ShellText("cd ROOT && git add x"), cwd="elsewhere"), (Prose("the repository"),)),
            CaseId("W11"): Refusal(Case(ShellText("git -C ../project add x"), cwd="elsewhere"),
                                   (Prose("the repository"),)),
            CaseId("W12"): Refusal(Case(ShellText("/usr/bin/git add x")), (Prose("the repository"),)),
        }, {
            CaseId("P1"): Case(ShellText("echo x > ELSEWHERE/x")),
            CaseId("P2"): Case(ShellText("echo x > OTHER/x")),
            CaseId("P3"): Case(ShellText("git -C OTHER add x")),
            CaseId("P4"): Case(ShellText("git -C OTHER diff -C ROOT")),
            CaseId("P5"): Case(ShellText("echo x > /dev/null; rg -n x .")),
            CaseId("P6"): Case(ShellText("echo x > nested/notes.txt")),
            CaseId("P7"): Case(ShellText('"$CMD" x')),
            CaseId("P8"): Case(ShellText("git -C OTHER commit -C ROOT")),
        })

    def test_a_git_command_that_only_reads(self) -> None:
        repository = Prose("the repository `git")
        self.check({
            CaseId("G1"): Refusal(Case(ShellText("git branch new")), (Prose("the repository `git branch new` writes"),)),
            CaseId("G2"): Refusal(Case(ShellText("git commit -m x")), (repository,)),
            CaseId("G3"): Refusal(Case(ShellText("git checkout main")), (repository,)),
            CaseId("G4"): Refusal(Case(ShellText("git stash")), (repository,)),
            CaseId("G5"): Refusal(Case(ShellText("git frobnicate")), (repository,)),
            CaseId("G6"): Refusal(Case(ShellText('git "$SUB"')), (repository,)),
            CaseId("G7"): Refusal(Case(ShellText('git branch "$B"')), (repository,)),
            CaseId("G8"): Refusal(Case(ShellText("git")), (repository,)),
            CaseId("G9"): Refusal(Case(ShellText("git log | head > notes.txt")), (Prose("`notes.txt` (created)"),)),
            CaseId("G10"): Refusal(Case(ShellText("git -C ROOT branch -D old"), cwd="elsewhere"), (repository,)),
        }, {
            **{CaseId(f"R-{name}"): Case(ShellText(f"git {name}")) for name in READ_ONLY},
            **{CaseId(f"RC-{name}"): Case(ShellText(f"git -C ROOT {name}"), cwd="elsewhere") for name in READ_ONLY},
            CaseId("R1"): Case(ShellText("git branch")),
            CaseId("R2"): Case(ShellText("git branch -a")),
            CaseId("R3"): Case(ShellText("git -c core.pager=cat log")),
            CaseId("R4"): Case(ShellText("git log --oneline | head -5")),
        })

    def test_a_write_the_call_does_not_make_known(self) -> None:
        self.check({
            CaseId("U1"): Refusal(Case(ShellText('echo x > "$F"')),
                                  (Prose('`"$F"` (created, a path the call does not make known)'),)),
            CaseId("U2"): Refusal(Case(ShellText("{ a >&3; } 3> .env")),
                                  (Prose("an output end of `a` the call does not make known"),)),
            CaseId("U3"): Refusal(Case(ShellText("a 2>&$FD")), (Prose("an output end of `a` the call does not make known"),)),
            CaseId("U4"): Refusal(Case(ShellText('git -C "$D" add x')), (Prose("the repository"),)),
            CaseId("U8"): Refusal(Case(ShellText("git -C")), (Prose("the repository"),)),
            CaseId("U9"): Refusal(Case(ShellText("a 1>&$FD")), (Prose("an output end of `a` the call does not make known"),)),
        }, {
            CaseId("U5"): Case(ShellText('echo x > "$F"'), cwd="elsewhere"),
            CaseId("U6"): Case(ShellText("{ a >&3; } 3> .env"), cwd="elsewhere"),
            CaseId("U7"): Case(ShellText("a < /dev/null")),
        })

    def test_each_live_run(self) -> None:
        self.check({
            CaseId("L1"): Refusal(Case(ShellText("echo x > notes.txt"), (ShellText("mybuild"), ShellText("all"))),
                                  (Prose("runs `mybuild all`"),)),
            CaseId("L2"): Refusal(Case(ShellText("echo x > notes.txt"), (ShellText("python3"), ShellText("-m"),
                                                                         ShellText("tools.ci"))), ()),
            CaseId("L3"): Refusal(Case(ShellText("echo x > notes.txt"), (ShellText("bash"), ShellText("tools/run.sh"))),
                                  ()),
            CaseId("L4"): Refusal(Case(ShellText("echo x > notes.txt"), (ShellText("go"), ShellText("test"),
                                                                         ShellText("./..."))), ()),
            CaseId("L5"): Refusal(Case(ShellText("echo x > notes.txt"), (ShellText("bash"), ShellText("-c"),
                                                                         ShellText("pytest; if"))), ()),
        }, {
            CaseId("L6"): Case(ShellText("echo x > notes.txt"), (ShellText("vim"), ShellText("notes.txt"))),
            CaseId("L7"): Case(ShellText("echo x > notes.txt"), (*PYTEST[:3], ShellText("--collect-only"))),
            CaseId("L8"): Case(ShellText("echo x > notes.txt"), (ShellText("bash"), ShellText("-c"),
                                                                 ShellText("echo; if"))),
            CaseId("L9"): Case(ShellText("echo x > notes.txt"), runs_in="other"),
            CaseId("L10"): Case(ShellText("echo x > notes.txt"), runs_in="nested"),
            CaseId("L11"): Case(ShellText("echo x > notes.txt"), runs_in="broken"),
            CaseId("L12"): Case(ShellText("echo x > ELSEWHERE/y"), runs_in="elsewhere", cwd="elsewhere"),
        })

    def test_the_runs_line_cut_short(self) -> None:
        whole = (*PYTEST, ShellText(EIGHTY_SEVEN[:-1]))
        long = (*PYTEST, EIGHTY_SEVEN)
        self.check({
            CaseId("C1"): Refusal(Case(ShellText("echo x > notes.txt"), whole), (Prose(f"runs `{' '.join(whole)}`"),)),
            CaseId("C2"): Refusal(Case(ShellText("echo x > notes.txt"), long),
                                  (Prose(f"runs `{' '.join(long)[:120]}...`"),)),
        }, {})

    def test_a_call_writing_into_no_tree_reads_no_proc_tree(self) -> None:
        got = self.judged(CaseId("N1"), Case(ShellText("echo x > ELSEWHERE/x")), proc=self.at.base / "no-proc")
        self.assertEqual((got.status, got.stderr), (0, ""))
