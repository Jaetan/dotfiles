#!/usr/bin/env python3
"""Regression cases for pr-watch-guard.py.

Every case pipes one PreToolUse event for a Bash call to the hook, run by its path as the harness runs it,
and compares the answer with the rule: exit 2 refuses a bare `gh pr checks --watch` and a `pr-checks-watch`
whose verdict cannot reach the task (stdout to a file or a pipe, a command after it joined by `;`, a newline
or `||`, or the call run in the foreground), naming each; exit 0 lets the rest through; exit 1 reports an
event the hook could not read. The live cases start a process that sleeps under a watcher's command line, in a
directory the case names, and expect a second watch of the same pull request in the same tree refused.

Usage: test_pr_watch_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple, NewType

# A case's name; prose a case names itself by or expects; the text of a call; an event as the hook reads it;
# the hook's exit status.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
CallText = NewType("CallText", str)
EventText = NewType("EventText", str)
CommandWord = NewType("CommandWord", str)  # a word of a process's command line
CommandLine = NewType("CommandLine", str)  # a command line written with single spaces
Directory = NewType("Directory", str)  # a working directory as the event spells it
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "pr-watch-guard.py" if GIVEN.is_dir() else GIVEN
CWD = Directory("/home/nicolas/dev/agda/aletheia")


class Case(NamedTuple):
    """One call, whether it runs in the background, the answer the rule gives, and words the refusal names."""

    id: CaseId
    text: CallText
    background: bool
    want: ExitStatus
    what: Prose
    names: tuple[Prose, ...] = ()


class Outcome(NamedTuple):
    """What the hook answered."""

    status: ExitStatus
    stderr: Prose


CASES = [
    Case(CaseId("c1"), CallText('gh pr checks 409 --watch --fail-fast --interval 20 > /tmp/x.log 2>&1; '
                                'echo "EXIT=$?" >> /tmp/x.log; cp /tmp/x.log /tmp/y.log'),
         True, REFUSE, Prose("the shape that read #409 as green: a bare watch behind a chain"),
         (Prose("`gh pr checks --watch` run bare"),)),
    Case(CaseId("c2"), CallText("gh pr checks 409"), False, ALLOW, Prose("a plain listing is not a watch")),
    Case(CaseId("c3"), CallText("gh pr checks 409 --json name,state --jq '.[]'"), False, ALLOW,
         Prose("a JSON listing is not a watch")),
    Case(CaseId("c4"), CallText("gh pr checks 409 --watch"), True, REFUSE,
         Prose("a bare watch in the background is still bare"), (Prose("run bare"),)),
    Case(CaseId("c5"), CallText("bash -c 'gh pr checks 409 --watch --fail-fast'"), True, REFUSE,
         Prose("a bare watch under a carrier is read"), (Prose("run bare"),)),
    Case(CaseId("c6"), CallText("pr-checks-watch 409"), True, ALLOW, Prose("the watcher alone, in the background")),
    Case(CaseId("c7"), CallText('pr-checks-watch 409 --record "$S/pr409-checks.log"'), True, ALLOW,
         Prose("the record option keeps the tables without a redirection")),
    Case(CaseId("c8"), CallText("pr-checks-watch 409"), False, REFUSE,
         Prose("a foreground watch dies at the tool's cap"), (Prose("foreground"),)),
    Case(CaseId("c9"), CallText("pr-checks-watch 409; echo done"), True, REFUSE,
         Prose("a `;` hands the echo's status to the task"), (Prose("followed by `;`"),)),
    Case(CaseId("c10"), CallText("pr-checks-watch 409\necho done"), True, REFUSE,
         Prose("a newline is a `;`"), (Prose("followed by a newline"),)),
    Case(CaseId("c11"), CallText("pr-checks-watch 409 || true"), True, REFUSE,
         Prose("`||` swallows a red"), (Prose("followed by `||`"),)),
    Case(CaseId("c12"), CallText("pr-checks-watch 409 | tee /tmp/x"), True, REFUSE,
         Prose("a pipe takes the lines and the status"), (Prose("piped into"),)),
    Case(CaseId("c13"), CallText("pr-checks-watch 409 > /tmp/x 2>&1"), True, REFUSE,
         Prose("stdout to a file empties the task's output"), (Prose("stdout is sent to /tmp/x"),)),
    Case(CaseId("c14"), CallText("pr-checks-watch 409 >> /tmp/x"), True, REFUSE,
         Prose("appending is a redirection too"), (Prose("stdout is sent to /tmp/x"),)),
    Case(CaseId("c15"), CallText("pr-checks-watch 409 2>/dev/null"), True, ALLOW,
         Prose("stderr alone may go elsewhere")),
    Case(CaseId("c16"), CallText("pr-checks-watch 409 && echo done"), True, ALLOW,
         Prose("`&&` keeps a failure's status")),
    Case(CaseId("c17"), CallText("pr-checks-watch 409;"), True, ALLOW, Prose("a trailing `;` runs nothing after")),
    Case(CaseId("c18"), CallText("(pr-checks-watch 409); echo after"), True, REFUSE,
         Prose("a command after the subshell takes the status"), (Prose("followed by `;`"),)),
    Case(CaseId("c19"), CallText("~/.local/bin/pr-checks-watch 409"), True, ALLOW,
         Prose("the watcher by its path")),
    Case(CaseId("c20"), CallText('pr-checks-watch "$PR"'), True, ALLOW,
         Prose("a number the call does not spell is still the watcher alone")),
    Case(CaseId("c21"), CallText("pr-checks-watch 409 &"), True, REFUSE,
         Prose("a job of the call's own shell ends the call at once with status 0"), (Prose("job of the call"),)),
    Case(CaseId("c22"), CallText("cd /home/nicolas/dev/agda/aletheia && pr-checks-watch 409"), True, ALLOW,
         Prose("a command before it leaves the status to it")),
    Case(CaseId("c23"), CallText("pr-checks-watch 409 > /tmp/x; echo done"), False, REFUSE,
         Prose("three problems in one call are each named"),
         (Prose("stdout is sent to /tmp/x"), Prose("followed by `;`"), Prose("foreground"))),
    Case(CaseId("c24"), CallText("echo 'pr-checks-watch 409; echo done'"), True, ALLOW,
         Prose("a quoted word naming the watcher is text")),
    Case(CaseId("c25"), CallText("git log --oneline -3"), False, ALLOW, Prose("a call with no watch")),
    Case(CaseId("c26"), CallText("git add -N .local/bin/pr-checks-watch; echo staged"), False, ALLOW,
         Prose("the watcher's path as an argument is text (the hook's first false positive)")),
    Case(CaseId("c27"), CallText("X=1 pr-checks-watch 409; echo done"), True, REFUSE,
         Prose("an assignment before the watcher leaves it in command position"), (Prose("followed by `;`"),)),
    Case(CaseId("c28"), CallText("echo pr-checks-watch; true"), False, ALLOW,
         Prose("the watcher's name as an argument is text")),
    Case(CaseId("c29"), CallText("if true; then pr-checks-watch 409; fi; echo done"), True, REFUSE,
         Prose("after `then` a command stands"), (Prose("followed by `;`"),)),
    Case(CaseId("c30"), CallText("gh pr checks 409 --watch=true"), True, REFUSE,
         Prose("the flag spelled with a value is the watch"), (Prose("run bare"),)),
    Case(CaseId("c31"), CallText("echo start\npr-checks-watch 409; echo done"), True, REFUSE,
         Prose("a watcher on a later line stands where a command does"), (Prose("followed by `;`"),)),
    Case(CaseId("c32"), CallText("if true; then pr-checks-watch 409; fi"), True, ALLOW,
         Prose("the if's status is the watcher's: `fi` is no command")),
    Case(CaseId("c33"), CallText("{ pr-checks-watch 409; }"), True, ALLOW,
         Prose("a brace group's status is the watcher's")),
    Case(CaseId("c34"), CallText("{ pr-checks-watch 409; }; echo done"), True, REFUSE,
         Prose("a command after the group takes the status"), (Prose("followed by `;`"),)),
    Case(CaseId("c35"), CallText("setsid -f pr-checks-watch 409"), True, REFUSE,
         Prose("a detached watch leaves the call nothing to wait on"), (Prose("job of the call"),)),
    Case(CaseId("c36"), CallText("nohup pr-checks-watch 409 & echo started"), True, REFUSE,
         Prose("nohup and `&` detach it too"), (Prose("job of the call"),)),
    Case(CaseId("c37"), CallText("pr-checks-watch 409 &!"), True, REFUSE,
         Prose("zsh's disown is a job too"), (Prose("job of the call"),)),
]


class LiveCase(NamedTuple):
    """A process sleeping under ``live`` in ``live_cwd``, a background call of ``text`` from ``cwd``, the answer,
    and words the refusal names (``{pid}`` stands for the sleeping process's)."""

    id: CaseId
    live: tuple[CommandWord, ...]
    live_cwd: Directory
    text: CallText
    cwd: Directory
    want: ExitStatus
    what: Prose
    names: tuple[Prose, ...] = ()


# A watcher runs as its interpreter with the script's path after it, as the kernel starts a shebang script.
INTERPRETER = CommandWord("/home/nicolas/.local/bin/python3.14")
WATCHER = CommandWord("/home/nicolas/.local/bin/pr-checks-watch")
SCRATCH = Path(tempfile.mkdtemp(prefix="pr-watch-guard-"))
ELSEWHERE = Directory(str(SCRATCH / "elsewhere"))  # a directory in no git tree
SUBDIR = Directory(f"{CWD}/tools")


def argv(line: CommandLine) -> tuple[CommandWord, ...]:
    """The words of a command line written with single spaces."""
    return tuple(CommandWord(word) for word in line.split(" "))


LIVE_CASES = [
    LiveCase(CaseId("l1"), (INTERPRETER, WATCHER, *argv(CommandLine("458 --record a.log"))), CWD,
             CallText('pr-checks-watch 458 --record "$S/b.log"'), CWD, REFUSE,
             Prose("a second watch of a pull request whose first is running (the 2026-10-27 slip)"),
             (Prose("a watch of #458 is already running"), Prose("pid {pid}"))),
    LiveCase(CaseId("l2"), (INTERPRETER, WATCHER, *argv(CommandLine("458"))), CWD,
             CallText("pr-checks-watch 457"), CWD, ALLOW, Prose("a watch of another pull request")),
    LiveCase(CaseId("l3"), (INTERPRETER, WATCHER, *argv(CommandLine("458"))), ELSEWHERE,
             CallText("pr-checks-watch 458"), CWD, ALLOW,
             Prose("the same number watched outside this tree is another repository's")),
    LiveCase(CaseId("l4"), argv(CommandLine("vim pr-checks-watch 458")), CWD, CallText("pr-checks-watch 458"), CWD,
             ALLOW, Prose("a process naming the script as an argument is not a watch")),
    LiveCase(CaseId("l5"), (INTERPRETER, WATCHER, *argv(CommandLine("458"))), CWD,
             CallText(f"cd {CWD} && pr-checks-watch 458"), ELSEWHERE, REFUSE,
             Prose("the tree is the one the watcher would run in, after the call's cd"),
             (Prose("a watch of #458 is already running"),)),
    LiveCase(CaseId("l6"), (INTERPRETER, WATCHER, *argv(CommandLine("--record a.log 458"))), CWD,
             CallText("pr-checks-watch 458"), CWD, REFUSE,
             Prose("the running watch's number read past its --record value"),
             (Prose("a watch of #458 is already running"),)),
    LiveCase(CaseId("l7"), (INTERPRETER, WATCHER, *argv(CommandLine("458"))), SUBDIR,
             CallText("pr-checks-watch 458"), CWD, REFUSE,
             Prose("a watch started in a subdirectory of the tree is the tree's"),
             (Prose("a watch of #458 is already running"),)),
    LiveCase(CaseId("l8"), (INTERPRETER, WATCHER, *argv(CommandLine("--record=a.log 458"))), CWD,
             CallText("pr-checks-watch 458"), CWD, REFUSE,
             Prose("the record option spelled with `=` is no operand"),
             (Prose("a watch of #458 is already running"),)),
    LiveCase(CaseId("l9"), (WATCHER, *argv(CommandLine("458"))), CWD, CallText("pr-checks-watch 458"), CWD, REFUSE,
             Prose("a watcher whose command is its own name, no interpreter before it"),
             (Prose("a watch of #458 is already running"),)),
]


def sleeper() -> Path:
    """Build a program that sleeps until killed, under whatever command line it is started with."""
    src = SCRATCH / "sleeper.c"
    src.write_text("#include <unistd.h>\nint main(void){for(;;)pause();}\n")
    out = SCRATCH / "sleeper"
    subprocess.run(["cc", "-O0", "-o", str(out), str(src)], check=True)
    return out


def event(text: CallText, background: bool, cwd: Directory = CWD) -> EventText:
    """Return the PreToolUse event for a Bash call of ``text`` from ``cwd``."""
    return EventText(json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": cwd,
        "tool_input": {"command": text, "run_in_background": background},
    }))


def answer(event_text: EventText) -> Outcome:
    """Run the hook over ``event_text`` and return its answer."""
    done = subprocess.run([str(GUARD)], input=event_text, capture_output=True, text=True, check=False)
    return Outcome(ExitStatus(done.returncode), Prose(done.stderr))


def main() -> ExitStatus:
    """Run every case and print one line each; exit 0 when all agree."""
    bad = 0
    for case in CASES:
        outcome = answer(event(case.text, case.background))
        missing = [name for name in case.names if name not in outcome.stderr]
        ok = outcome.status == case.want and not missing
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {case.id:<4} want={case.want} got={outcome.status} {case.what}")
        if not ok:
            print("        " + " | ".join(outcome.stderr.strip().splitlines()[-4:])[:300])
            for name in missing:
                print(f"        not named: {name}")
    others = [
        (CaseId("o1"), EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read",
                                             "tool_input": {"file_path": "/tmp/x"}})),
         ALLOW, Prose("a tool that is not Bash")),
        (CaseId("o2"), EventText("not json"), REPORTED, Prose("an event that is not JSON is reported")),
        (CaseId("o3"), EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                             "tool_input": {"command": "pr-checks-watch 409 && if true; then"}})),
         ALLOW, Prose("text the reader cannot follow goes ahead")),
        (CaseId("o4"), EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Monitor",
                                             "tool_input": {"command": "gh pr checks 409 --watch"}})),
         ALLOW, Prose("a command of a tool that is not Bash is not read")),
    ]
    for case_id, event_text, want, what in others:
        outcome = answer(event_text)
        ok = outcome.status == want
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {case_id:<4} want={want} got={outcome.status} {what}")
    Path(ELSEWHERE).mkdir()
    program = sleeper()
    for live in LIVE_CASES:
        # Popen returns once the child has exec'd, so its command line is in /proc when the hook reads it.
        proc = subprocess.Popen(list(live.live), executable=str(program), cwd=live.live_cwd)
        try:
            outcome = answer(event(live.text, True, live.cwd))
        finally:
            proc.kill()
            proc.wait()
        names = [Prose(name.replace("{pid}", str(proc.pid))) for name in live.names]
        missing = [name for name in names if name not in outcome.stderr]
        ok = outcome.status == live.want and not missing
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {live.id:<4} want={live.want} got={outcome.status} {live.what}")
        if not ok:
            print("        " + " | ".join(outcome.stderr.strip().splitlines()[-4:])[:300])
            for name in missing:
                print(f"        not named: {name}")
    total = len(CASES) + len(others) + len(LIVE_CASES)
    print(f"{total} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
