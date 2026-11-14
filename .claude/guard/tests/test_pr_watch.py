"""The pr-watch rule, through the dispatcher with every other rule switched off: a Bash call that runs `gh pr checks`
with --watch, runs pr-checks-watch other than alone in a background call with its stdout the call's own, or arms a
second watch of a pull request a running watcher watches in the same tree, is refused, naming each problem; the rest
pass. The c and o cases carry the old pr-watch guard's cases on the watch's shape and the l cases its second watches,
under their old names, those whose verdict the blunt rule moved left out; N cases are new. Each second-watch case
reads a proc tree of its own."""

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
Folder = Literal["root", "tools", "elsewhere", "aside", "link"]
BARE, FOREGROUND, JOB = Prose("is `gh pr checks --watch`:"), Prose("runs in the foreground"), Prose("runs as a job")
ALONE, OUTPUT = Prose("is not the only command of the call's text"), Prose("sends its stdout elsewhere")
WATCH_ALONE = Prose("  * `pr-checks-watch 409` is not the only command")
ADVICE = Prose("  pr-checks-watch N\n")
SECOND = Prose("this call arms a second watch of a pull request whose watch is running.")
NOTIFIED = Prose("its notification will come to this context when it ends")
# A watcher runs as its interpreter with the script's path after it, as the kernel starts a shebang script.
INTERPRETER = ShellText("/home/nicolas/.local/bin/python3.14")
WATCHER = ShellText("/home/nicolas/.local/bin/pr-checks-watch")
PID = Pid(4242)


class Case(NamedTuple):
    """A call, its run_in_background where the tool input gives one, and what its refusal names."""

    call: ShellText
    background: bool | None
    names: tuple[Prose, ...] = ()


type Cases = dict[CaseId, Case]


class Live(NamedTuple):
    """A watcher process the case's proc tree shows and the folder it runs in, the call and the folder it runs from,
    the tree a refusal names, None where it names none, and whether the call's shape is refused too."""

    process: tuple[ShellText, ...]
    runs_in: Folder
    call: ShellText
    cwd: Folder
    tree: Folder | None
    advice: bool = False


type Lives = dict[CaseId, Live]


class Places(NamedTuple):
    """A scratch folder holding the project root, a git tree, the home folder with the watcher in it, the switch
    folder and an empty proc tree."""

    base: Path
    root: Path
    home: Path
    off: Path
    proc: Path

    def folder(self, name: Folder) -> Path:
        """The folder ``name`` stands for: the root, its subfolder tools, two folders in no tree, a link to the root."""
        return {"root": self.root, "tools": self.root / "tools", "elsewhere": self.base / "elsewhere",
                "aside": self.base / "aside", "link": self.base / "link"}[name]


def places() -> Places:
    """Fresh folders, every rule but pr-watch switched off, so a case judges this rule alone."""
    base = Path(tempfile.mkdtemp()).resolve()
    found = Places(base, base / "project", base / "home", base / "off", base / "proc")
    for folder in (found.root / "tools", found.folder("elsewhere"), found.folder("aside"),
                   found.home / ".config" / "claude-guard", found.home / ".local" / "bin", found.off, found.proc):
        folder.mkdir(parents=True)
    git = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    _ = subprocess.run(["git", "init", "-q", str(found.root)], env=git, check=True, capture_output=True)
    found.folder("link").symlink_to(found.root)
    _ = (found.home / ".config" / "claude-guard" / "machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
    _ = (found.home / ".local" / "bin" / "pr-checks-watch").write_text("#!/usr/bin/python3\nimport sys\n",
                                                                       encoding="utf-8")
    for module in (KIT / "rules").glob("*.py"):
        if not module.stem.startswith("_") and module.stem != "pr_watch":
            (found.off / module.stem.replace("_", "-")).touch()
    return found


def settings(at: Places, proc: Path | None = None) -> Settings:
    """The dispatcher's settings over the kit's rules, the switches in ``at``, processes read from ``proc``, the empty
    proc tree of ``at`` where None."""
    return Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)),
                    ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(at.home))}), home=at.home,
                    places=StatePlaces(at.base / "sessions", at.proc if proc is None else proc))


def judged(at: Places, call: ShellText, background: bool | None, tool: ShellText = ShellText("Bash"),
           cwd: Path | None = None, proc: Path | None = None) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call`` run from ``cwd``, the root where None,
    run_in_background ``background`` where it is not None, processes read from ``proc``."""
    given: ToolInput = {"command": call}
    if background is not None:
        given["run_in_background"] = background
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(at.root if cwd is None else cwd),
              "tool_input": given}
    return answer(EventBytes(json.dumps(fields).encode()), settings(at, proc))


def line(process: tuple[ShellText, ...]) -> ProcLine:
    """A command line as /proc/PID/cmdline holds it: each word ended by a NUL."""
    return ProcLine(b"".join(word.encode() + b"\0" for word in process))


class Rule(unittest.TestCase):
    """Each case refused naming each of its problems and the shape that passes, or passed."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def check(self, refused: Cases, passed: Cases) -> None:
        """Every refused case answered 2, its stderr naming each of its problems and the advice; every passed one 0."""
        for case, (call, background, names) in refused.items():
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, background)
                self.assertEqual(got.status, 2, got.stderr)
                for name in (*names, ADVICE):
                    self.assertIn(name, got.stderr)
        for case, (call, background, _) in passed.items():
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, background)
                self.assertEqual((got.status, got.stderr), (0, ""))

    def test_the_old_cases(self) -> None:
        self.check({
            CaseId("c1"): Case(ShellText('gh pr checks 409 --watch --fail-fast --interval 20 > /tmp/x.log 2>&1; '
                                         'echo "EXIT=$?" >> /tmp/x.log; cp /tmp/x.log /tmp/y.log'), True, (BARE,)),
            CaseId("c4"): Case(ShellText("gh pr checks 409 --watch"), True,
                               (Prose("  * `gh pr checks 409 --watch` is `gh pr checks --watch`"),)),
            CaseId("c5"): Case(ShellText("bash -c 'gh pr checks 409 --watch --fail-fast'"), True, (BARE,)),
            CaseId("c8"): Case(ShellText("pr-checks-watch 409"), False,
                               (Prose("  * `pr-checks-watch 409` runs in the foreground"),)),
            CaseId("c9"): Case(ShellText("pr-checks-watch 409; echo done"), True, (WATCH_ALONE,)),
            CaseId("c10"): Case(ShellText("pr-checks-watch 409\necho done"), True, (WATCH_ALONE,)),
            CaseId("c11"): Case(ShellText("pr-checks-watch 409 || true"), True, (WATCH_ALONE,)),
            CaseId("c12"): Case(ShellText("pr-checks-watch 409 | tee /tmp/x"), True, (WATCH_ALONE, OUTPUT)),
            CaseId("c13"): Case(ShellText("pr-checks-watch 409 > /tmp/x 2>&1"), True,
                                (Prose("  * `pr-checks-watch 409` sends its stdout elsewhere"),)),
            CaseId("c14"): Case(ShellText("pr-checks-watch 409 >> /tmp/x"), True, (OUTPUT,)),
            CaseId("c18"): Case(ShellText("(pr-checks-watch 409); echo after"), True, (ALONE,)),
            CaseId("c21"): Case(ShellText("pr-checks-watch 409 &"), True,
                                (Prose("  * `pr-checks-watch 409` runs as a job"),)),
            CaseId("c23"): Case(ShellText("pr-checks-watch 409 > /tmp/x; echo done"), False,
                                (OUTPUT, ALONE, FOREGROUND)),
            CaseId("c27"): Case(ShellText("X=1 pr-checks-watch 409; echo done"), True, (ALONE,)),
            CaseId("c29"): Case(ShellText("if true; then pr-checks-watch 409; fi; echo done"), True, (ALONE,)),
            CaseId("c30"): Case(ShellText("gh pr checks 409 --watch=true"), True, (BARE,)),
            CaseId("c31"): Case(ShellText("echo start\npr-checks-watch 409; echo done"), True, (ALONE,)),
            CaseId("c34"): Case(ShellText("{ pr-checks-watch 409; }; echo done"), True, (ALONE,)),
            CaseId("c35"): Case(ShellText("setsid -f pr-checks-watch 409"), True, (WATCH_ALONE,)),
            CaseId("c36"): Case(ShellText("nohup pr-checks-watch 409 & echo started"), True, (JOB, ALONE)),
        }, {
            CaseId("c2"): Case(ShellText("gh pr checks 409"), False),
            CaseId("c3"): Case(ShellText("gh pr checks 409 --json name,state --jq '.[]'"), False),
            CaseId("c6"): Case(ShellText("pr-checks-watch 409"), True),
            CaseId("c7"): Case(ShellText('pr-checks-watch 409 --record "$S/pr409-checks.log"'), True),
            CaseId("c15"): Case(ShellText("pr-checks-watch 409 2>/dev/null"), True),
            CaseId("c17"): Case(ShellText("pr-checks-watch 409;"), True),
            CaseId("c19"): Case(ShellText("~/.local/bin/pr-checks-watch 409"), True),
            CaseId("c20"): Case(ShellText('pr-checks-watch "$PR"'), True),
            CaseId("c24"): Case(ShellText("echo 'pr-checks-watch 409; echo done'"), True),
            CaseId("c25"): Case(ShellText("git log --oneline -3"), False),
            CaseId("c26"): Case(ShellText("git add -N .local/bin/pr-checks-watch; echo staged"), False),
            CaseId("c28"): Case(ShellText("echo pr-checks-watch; true"), False),
            CaseId("c33"): Case(ShellText("{ pr-checks-watch 409; }"), True),
        })

    def test_the_old_zsh_disown_is_refused_as_text_the_kit_cannot_read(self) -> None:
        got = judged(self.at, ShellText("pr-checks-watch 409 &!"), True)  # c37
        self.assertEqual(got.status, 2)
        self.assertNotIn(ADVICE, got.stderr)

    def test_the_old_tools_that_are_not_bash(self) -> None:
        read = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}}
        self.assertEqual(answer(EventBytes(json.dumps(read).encode()), settings(self.at)).status, 0)  # o1
        monitor = judged(self.at, ShellText("gh pr checks 409 --watch"), None, ShellText("Monitor"))  # o4
        self.assertEqual(monitor.status, 0)

    def test_gh_given_watch_whatever_its_other_words(self) -> None:
        self.check({
            CaseId("N01"): Case(ShellText("gh pr checks 409 -i 20 --watch"), True, (BARE,)),
            CaseId("N02"): Case(ShellText("gh pr checks 409 --watch=$W"), True, (BARE,)),
            CaseId("N03"): Case(ShellText("/usr/bin/gh pr checks --watch$W"), True, (BARE,)),
            CaseId("N04"): Case(ShellText("echo $(gh pr checks 409 --watch)"), True, (BARE,)),
            CaseId("N51"): Case(ShellText("gh pr checks 409 --watch=false"), True, (BARE,)),
            CaseId("N52"): Case(ShellText("gh pr checks 409 -- --watch"), True, (BARE,)),
        }, {
            CaseId("N08"): Case(ShellText("gh pr view 409 --watch"), True),
            CaseId("N09"): Case(ShellText("gh run checks 409 --watch"), True),
            CaseId("N53"): Case(ShellText("gh pr checks 409 --watcher --watch-x"), True),
            CaseId("N60"): Case(ShellText("echo pr checks 409 --watch"), True),
        })

    def test_the_watcher_run_by_an_interpreter(self) -> None:
        self.check({
            CaseId("N10"): Case(ShellText("python3.14 ~/.local/bin/pr-checks-watch 409; echo done"), True, (ALONE,)),
            CaseId("N11"): Case(ShellText("bash -e ~/.local/bin/pr-checks-watch 409; echo done"), True, (ALONE,)),
            CaseId("N12"): Case(ShellText("python3 -u ~/.local/bin/pr-checks-watch 409; echo done"), True, (ALONE,)),
            CaseId("N54"): Case(ShellText("sh ~/.local/bin/pr-checks-watch 409"), False, (FOREGROUND,)),
            CaseId("N55"): Case(ShellText("dash ~/.local/bin/pr-checks-watch 409"), False, (FOREGROUND,)),
        }, {
            CaseId("N13"): Case(ShellText("python3 -c pr-checks-watch; echo done"), True),
            CaseId("N14"): Case(ShellText("bash -c 'echo pr-checks-watch'; echo done"), True),
            CaseId("N26"): Case(ShellText("python3 -V; echo done"), True),
        })

    def test_the_only_command_of_the_calls_own_text(self) -> None:
        self.check({
            CaseId("N21"): Case(ShellText("nohup pr-checks-watch 409"), True, (WATCH_ALONE,)),
            CaseId("N22"): Case(ShellText("case a in a) pr-checks-watch 409;& b) echo b;; esac"), True, (ALONE,)),
            CaseId("N25"): Case(ShellText("timeout 3600 pr-checks-watch 409 && echo green; echo after"), True,
                                (ALONE,)),
            CaseId("N35"): Case(ShellText("pr-checks-watch 409 && echo ok; echo after"), True, (WATCH_ALONE,)),
            CaseId("N36"): Case(ShellText("pr-checks-watch 409 && echo ok || true"), True, (WATCH_ALONE,)),
            CaseId("N37"): Case(ShellText("pr-checks-watch 409 && if true; then echo ok; fi\necho after"), True,
                                (WATCH_ALONE,)),
            CaseId("N38"): Case(ShellText("echo $(pr-checks-watch 409 && echo ok; echo after)"), True,
                                (WATCH_ALONE, OUTPUT)),
            CaseId("N39"): Case(ShellText("pr-checks-watch 409 && (echo ok); echo after"), True, (WATCH_ALONE,)),
            CaseId("N43"): Case(ShellText("pr-checks-watch 409 && { echo ok; echo after; }"), True, (WATCH_ALONE,)),
            CaseId("N56"): Case(ShellText("timeout 3600 pr-checks-watch 409"), True, (WATCH_ALONE,)),
            CaseId("N57"): Case(ShellText("bash -c 'pr-checks-watch 409'"), True, (WATCH_ALONE,)),
        }, {
            CaseId("N58"): Case(ShellText("bash ~/.local/bin/pr-checks-watch 409"), True),
            CaseId("N59"): Case(ShellText("(pr-checks-watch 409)"), True),
        })

    def test_each_job(self) -> None:
        self.check({
            CaseId("N19"): Case(ShellText("coproc pr-checks-watch 409"), True, (JOB, OUTPUT)),
            CaseId("N20"): Case(ShellText("{ pr-checks-watch 409; } &"), True, (JOB,)),
        }, {})

    def test_each_end_of_its_stdout(self) -> None:
        self.check({
            CaseId("N15"): Case(ShellText("pr-checks-watch 409 > /dev/null"), True, (OUTPUT,)),
            CaseId("N16"): Case(ShellText("echo $(pr-checks-watch 409)"), True, (OUTPUT, ALONE)),
            CaseId("N17"): Case(ShellText("pr-checks-watch 409 >&-"), True, (OUTPUT,)),
            CaseId("N18"): Case(ShellText("pr-checks-watch 409 >&$FD"), True, (OUTPUT,)),
        }, {})

    def test_the_call_in_the_background(self) -> None:
        self.check({CaseId("N23"): Case(ShellText("pr-checks-watch 409"), None, (FOREGROUND,))}, {})

    def proc(self, case: CaseId, live: Live) -> Path:
        """A proc tree of the case's own: process PID running ``live.process`` in its folder, beside an entry that is
        no pid and a process whose folder cannot be read, each running the same command line."""
        tree = self.at.base / f"proc-{case}"
        for name, linked in ((str(PID), True), ("self", True), ("1", False)):
            entry = tree / name
            entry.mkdir(parents=True)
            _ = (entry / "cmdline").write_bytes(line(live.process))
            if linked:
                (entry / "cwd").symlink_to(self.at.folder(live.runs_in))
        return tree

    def check_live(self, cases: Lives) -> None:
        """Every case whose tree is named refused naming the running watch, its pid and its notification, the shape's
        advice named where the case says so; a case naming neither passed."""
        for case, live in cases.items():
            with self.subTest(case=case, call=live.call):
                call = ShellText(live.call.replace("ROOT", str(self.at.root)))
                got = judged(self.at, call, True, cwd=self.at.folder(live.cwd), proc=self.proc(case, live))
                if live.tree is None and not live.advice:
                    self.assertEqual((got.status, got.stderr), (0, ""))
                    continue
                self.assertEqual(got.status, 2, got.stderr)
                self.assertEqual(ADVICE in got.stderr, live.advice, got.stderr)
                self.assertEqual(SECOND in got.stderr, live.tree is not None, got.stderr)
                if live.tree is not None:
                    running = f"  * a watch of #458 is already running in {self.at.folder(live.tree)} (pid {PID}): "
                    self.assertIn(Prose(running + NOTIFIED), got.stderr)
                self.assertNotIn(Prose("(pid 1)"), got.stderr)

    def test_the_old_second_watches(self) -> None:
        self.check_live({
            CaseId("l1"): Live((INTERPRETER, WATCHER, ShellText("458"), ShellText("--record"), ShellText("a.log")),
                               "root", ShellText('pr-checks-watch 458 --record "$S/b.log"'), "root", "root"),
            CaseId("l2"): Live((INTERPRETER, WATCHER, ShellText("458")), "root", ShellText("pr-checks-watch 457"),
                               "root", None),
            CaseId("l3"): Live((INTERPRETER, WATCHER, ShellText("458")), "elsewhere", ShellText("pr-checks-watch 458"),
                               "root", None),
            CaseId("l4"): Live((ShellText("vim"), ShellText("pr-checks-watch"), ShellText("458")), "root",
                               ShellText("pr-checks-watch 458"), "root", None),
            CaseId("l5"): Live((INTERPRETER, WATCHER, ShellText("458")), "root",
                               ShellText("cd ROOT && pr-checks-watch 458"), "elsewhere", "root", advice=True),
            CaseId("l6"): Live((INTERPRETER, WATCHER, ShellText("--record"), ShellText("a.log"), ShellText("458")),
                               "root", ShellText("pr-checks-watch 458"), "root", "root"),
            CaseId("l7"): Live((INTERPRETER, WATCHER, ShellText("458")), "tools", ShellText("pr-checks-watch 458"),
                               "root", "root"),
            CaseId("l8"): Live((INTERPRETER, WATCHER, ShellText("--record=a.log"), ShellText("458")), "root",
                               ShellText("pr-checks-watch 458"), "root", "root"),
            CaseId("l9"): Live((WATCHER, ShellText("458")), "root", ShellText("pr-checks-watch 458"), "root", "root"),
        })

    def test_each_second_watch_read(self) -> None:
        self.check_live({
            CaseId("N27"): Live((INTERPRETER, WATCHER, ShellText("--record"), ShellText("a.log")), "root",
                                ShellText('pr-checks-watch "$PR"'), "root", None),
            CaseId("N28"): Live((INTERPRETER, WATCHER, ShellText("458")), "root",
                                ShellText("pr-checks-watch --record b.log"), "root", None),
            CaseId("N29"): Live((INTERPRETER, WATCHER, ShellText("458")), "elsewhere", ShellText("pr-checks-watch 458"),
                                "aside", None),
            CaseId("N30"): Live((INTERPRETER, WATCHER, ShellText("458")), "elsewhere", ShellText("pr-checks-watch 458"),
                                "elsewhere", "elsewhere"),
            CaseId("N31"): Live((INTERPRETER, WATCHER, ShellText("458")), "root", ShellText("pr-checks-watch 458"),
                                "link", "root"),
            CaseId("N33"): Live((ShellText("bash"), WATCHER, ShellText("458")), "root",
                                ShellText("pr-checks-watch 458"), "root", "root"),
            CaseId("N50"): Live((INTERPRETER, WATCHER, ShellText("458")), "root",
                                ShellText('cd "$X" && pr-checks-watch 458'), "root", None, advice=True),
            CaseId("N61"): Live((INTERPRETER, WATCHER, ShellText("458")), "root",
                                ShellText('pr-checks-watch "$O" 458'), "root", None),
        })

    def test_a_second_watch_shaped_wrong_names_both(self) -> None:
        live = Live((INTERPRETER, WATCHER, ShellText("458")), "root", ShellText("pr-checks-watch 458; echo done"),
                    "root", "root")
        got = judged(self.at, live.call, True, proc=self.proc(CaseId("N34"), live))
        self.assertEqual(got.status, 2, got.stderr)
        for name in (SECOND, ADVICE, Prose("`pr-checks-watch 458` is not the only command"), Prose(f"(pid {PID})")):
            self.assertIn(name, got.stderr)

    def test_a_call_with_no_watcher_reads_no_proc_tree(self) -> None:
        got = judged(self.at, ShellText("git log --oneline -3"), False, proc=self.at.base / "no-proc")  # N44
        self.assertEqual((got.status, got.stderr), (0, ""))
