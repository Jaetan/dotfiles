#!/usr/bin/env python3
"""Regression cases for test-determinism-guard.py.

Every case pipes a synthetic event into the hook, as the harness sends it after
a tool ran, and compares its answer with what the rule says: exit 2 reports a
test written with a time or thread site its record does not allow, exit 1 an
error of the hook's own, exit 0 lets the call through, with the rule added to
the model's context when the call read or wrote a test and with nothing
otherwise. The repositories the events name are built here, under a fresh
temporary directory: git work trees holding the gate's own modules, copied from
the repository that owns them, a record allowing one sleep in one test, and an
interpreter that runs the gate under that repository's virtual environment.
The hook runs after the call, so each file an event says was written is written
here first, as the call would have left it.

Usage: test_test_determinism_guard.py [HOOK_DIR] [GATE_REPO]
  HOOK_DIR   the hooks directory (default: the parent of this tests/ directory)
  GATE_REPO  the repository whose tools/check_test_determinism.py is tested
             (default: ~/dev/agda/aletheia)
Exit 0 when every case agrees with its expectation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

# A path as an event spells it; a shell command line; text a file holds; an
# event as raw text, for the one that cannot be read; prose a case expects or
# names itself by; a case's name; a count of files; the hook's exit status.
FilePath = NewType("FilePath", str)
CommandLine = NewType("CommandLine", str)
FileText = NewType("FileText", str)
RawEvent = NewType("RawEvent", str)
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
FileCount = NewType("FileCount", int)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)


class Request(TypedDict, total=False):
    """The part of a tool's input the hook reads."""

    file_path: FilePath
    command: CommandLine


class EditDiff(TypedDict, total=False):
    """The files a Bash call changed, as the harness reports them."""

    changedFiles: list[FilePath]
    moreFiles: FileCount


class BashResponse(TypedDict, total=False):
    """What the harness reports after a Bash call, as far as the hook reads it."""

    bashEditDiff: EditDiff


class Event(TypedDict, total=False):
    """A tool event as the harness hands it to the hook."""

    hook_event_name: Literal["PreToolUse", "PostToolUse"]
    tool_name: Literal["Read", "Edit", "Write", "MultiEdit", "Bash"]
    tool_input: Request
    tool_response: BashResponse
    cwd: FilePath


class Outcome(NamedTuple):
    """What the hook answered: its exit status, and what it wrote to each stream."""

    status: ExitStatus
    stdout: Prose
    stderr: Prose


class Case(NamedTuple):
    """One event, the status the rule wants, whether the rule must reach the context, and text stderr holds."""

    id: CaseId
    event: Event | RawEvent
    want: ExitStatus
    reminds: bool
    what: Prose
    says: Prose = Prose("")


HOOKS = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent).resolve()
GUARD = HOOKS / "test-determinism-guard.py"
SOURCE = Path(sys.argv[2] if len(sys.argv) > 2 else Path.home() / "dev" / "agda" / "aletheia")
GATE_MODULES = ("__init__.py", "_common.py", "_ratchet.py", "check_cpp_index_loops.py",
                "check_test_determinism.py")
INTERPRETER = Path("python") / ".venv" / "bin" / "python"

BASE = Path(tempfile.mkdtemp(prefix="determinism-guard-test-")).resolve()
R = BASE / "repo"  # a repository holding the gate, with a test a Bash call left untracked
CLEAN = BASE / "clean"  # a repository holding the gate and nothing untracked
EDITED = BASE / "edited"  # a repository holding the gate, whose recorded test one case edits
BROKEN = BASE / "broken"  # a repository whose gate crashes
BARE = BASE / "bare"  # a repository without the gate
OUT = BASE / "outside"  # no repository at all
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
HOOK_ENV = dict(os.environ, TMPDIR=str(BASE / "tmp"))

RECORD = FileText("""sites:
  - file: python/tests/test_recorded.py
    text: "time: a clock or sleep from module time"
    count: 1
""")
RECORDED = FileText("import time\n\n\ndef test_x() -> None:\n    time.sleep(1)\n")
CLEAN_GO = FileText("package x\n\nimport \"testing\"\n\nfunc TestX(t *testing.T) {}\n")
GOROUTINE = FileText("package x\n\nimport \"testing\"\n\nfunc TestY(t *testing.T) { go func() {}() }\n")
SLEEPS = FileText("import time\ntime.sleep(1)\n")


def git_init(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, env=GIT_ENV)


def commit(repo: Path) -> None:
    for step in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                 "commit", "-qm", "gated"]):
        subprocess.run(["git", "-C", str(repo), *step], check=True, capture_output=True, env=GIT_ENV)


def put(path: Path, text: FileText) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def gated(repo: Path) -> None:
    """Build a repository holding the gate, its record, one recorded test and one clean one, all committed."""
    git_init(repo)
    for name in GATE_MODULES:
        put(repo / "tools" / name, FileText((SOURCE / "tools" / name).read_text()))
    put(repo / "docs" / "TEST_DETERMINISM.yaml", RECORD)
    put(repo / "docs" / "readme.md", FileText("no test here\n"))
    put(repo / "python" / "tests" / "test_recorded.py", RECORDED)
    put(repo / "go" / "x" / "a_test.go", CLEAN_GO)
    put(repo / "go" / "x" / "x.go", FileText("package x\n"))
    interpreter = repo / INTERPRETER
    # A wrapper rather than a link: a link's own directory holds no pyvenv.cfg,
    # so the interpreter would not find the environment the gate needs.
    put(interpreter, FileText(f'#!/bin/sh\nexec "{SOURCE}/python/.venv/bin/python" "$@"\n'))
    interpreter.chmod(0o755)
    commit(repo)


def setup() -> None:
    gated(R)
    gated(CLEAN)
    gated(EDITED)
    gated(BROKEN)
    put(BROKEN / "tools" / "check_test_determinism.py", FileText("import sys\nsys.exit(3)\n"))
    put(R / "go" / "x" / "bad_test.go", GOROUTINE)  # untracked, as a Bash call left it
    git_init(BARE)
    put(BARE / "go" / "x" / "bad_test.go", GOROUTINE)
    put(OUT / "go" / "bad_test.go", GOROUTINE)
    (BASE / "tmp").mkdir()


def hook(event: Event | RawEvent) -> Outcome:
    raw = event if isinstance(event, str) else json.dumps(event)
    p = subprocess.run([str(GUARD)], input=raw, capture_output=True, text=True, check=False,
                       timeout=120, env=HOOK_ENV)
    return Outcome(ExitStatus(p.returncode), Prose(p.stdout), Prose(p.stderr))


def reminded(outcome: Outcome) -> bool:
    """Say whether the hook added the rule to the model's context."""
    if not outcome.stdout.strip():
        return False
    answer = json.loads(outcome.stdout)
    output = answer.get("hookSpecificOutput", {}) if isinstance(answer, dict) else {}
    text = output.get("additionalContext", "") if isinstance(output, dict) else ""
    return output.get("hookEventName") == "PostToolUse" and "fully deterministic" in text


def tool(name: Literal["Read", "Edit", "Write", "MultiEdit"], path: Path, cwd: Path = R) -> Event:
    return {"hook_event_name": "PostToolUse", "tool_name": name,
            "tool_input": {"file_path": FilePath(str(path))}, "cwd": FilePath(str(cwd))}


def wrote(name: Literal["Edit", "Write", "MultiEdit"], path: Path, text: FileText, cwd: Path = R) -> Event:
    put(path if path.is_absolute() else cwd / path, text)
    return tool(name, path, cwd)


def bash(command: CommandLine, cwd: Path = R, changed: list[Path] | None = None,
         more: FileCount = FileCount(0)) -> Event:
    event: Event = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                    "tool_input": {"command": command}, "cwd": FilePath(str(cwd))}
    if changed is not None or more:
        event["tool_response"] = {"bashEditDiff": {
            "changedFiles": [FilePath(str(path)) for path in changed or []], "moreFiles": more}}
    return event


def cases() -> list[Case]:
    c, p, t, sh, n = CaseId, Prose, FileText, CommandLine, FileCount
    tests = R / "python" / "tests"
    return [
        Case(c("D01"), tool("Read", R / "go" / "x" / "a_test.go"), ALLOW, True, p("a Go test read")),
        Case(c("D02"), tool("Read", R / "go" / "x" / "x.go"), ALLOW, False, p("a Go source that is no test")),
        Case(c("D03"), tool("Read", R / "docs" / "readme.md"), ALLOW, False, p("a document")),
        Case(c("D04"), wrote("Write", tests / "test_new.py", SLEEPS), REFUSE, False,
             p("a test written with a sleep the record does not allow"),
             p("time: a clock or sleep from module time")),
        Case(c("D05"), wrote("Write", tests / "test_fine.py", t("def test_x() -> None:\n    assert 1\n")),
             ALLOW, True, p("a test written with no site")),
        Case(c("D06"), wrote("Edit", tests / "test_recorded.py", t(RECORDED + "\n# edited\n")), ALLOW, True,
             p("an edit leaving the recorded test at its recorded count")),
        Case(c("D07"), wrote("Edit", EDITED / "python" / "tests" / "test_recorded.py",
                             t(RECORDED + "    time.sleep(2)\n"), EDITED),
             REFUSE, False, p("an edit adding a second sleep where the record allows one"), p("count: 2")),
        Case(c("D08"), wrote("MultiEdit", R / "go" / "x" / "m_test.go", t(GOROUTINE.replace("TestY", "TestM"))),
             REFUSE, False, p("a MultiEdit leaving a goroutine"), p("thread: a go statement")),
        Case(c("D09"), bash(sh("sed -n 1,5p go/x/a_test.go")), ALLOW, True, p("a Bash read of a test")),
        Case(c("D10"), bash(sh("grep -rn sleep python/tests")), ALLOW, True,
             p("a Bash read of a directory holding tests")),
        Case(c("D11"), bash(sh("cat docs/readme.md go/x/x.go")), ALLOW, False, p("a Bash read of no test")),
        Case(c("D12"), bash(sh("git status")), ALLOW, False, p("a Bash call naming no path")),
        Case(c("D13"), bash(sh("true"), changed=[R / "go" / "x" / "bad_test.go"]), REFUSE, False,
             p("a goroutine a Bash call wrote, reported after it"), p("bad_test.go")),
        Case(c("D14"), bash(sh("true"), more=n(1)), REFUSE, False,
             p("a call that changed more than it lists: the untracked files are read"), p("bad_test.go")),
        Case(c("D15"), bash(sh("true"), cwd=CLEAN, more=n(1)), ALLOW, False,
             p("a call that changed more than it lists, in a clean repository")),
        Case(c("D16"), bash(sh("cat go/x/a_test.go"), cwd=R / "go"), ALLOW, False,
             p("a path the call's directory does not hold")),
        Case(c("D17"), bash(sh("cat x/a_test.go"), cwd=R / "go"), ALLOW, True,
             p("a path relative to the call's directory")),
        Case(c("D18"), tool("Read", BARE / "go" / "x" / "bad_test.go", BARE), ALLOW, False,
             p("a repository without the gate")),
        Case(c("D19"), bash(sh("true"), cwd=BARE, changed=[BARE / "go" / "x" / "bad_test.go"]), ALLOW, False,
             p("a Bash write in a repository without the gate")),
        Case(c("D20"), tool("Read", OUT / "go" / "bad_test.go", OUT), ALLOW, False, p("no repository at all")),
        Case(c("D21"), RawEvent("{not json"), ALLOW, False, p("an event that cannot be read")),
        Case(c("D22"), {**tool("Read", R / "go" / "x" / "a_test.go"), "hook_event_name": "PreToolUse"},
             ALLOW, False, p("an event before the call, which the hook leaves alone")),
        Case(c("D23"), tool("Read", BROKEN / "go" / "x" / "a_test.go", BROKEN), REPORTED, False,
             p("a gate that crashes is reported, and the call goes ahead"), p("the gate exited 3")),
        Case(c("D24"), wrote("Write", Path("python/tests/test_rel.py"), SLEEPS), REFUSE, False,
             p("a written path relative to the call's directory"), p("test_rel.py")),
        Case(c("D25"), bash(sh("grep -rn python docs/readme.md && cd go")), ALLOW, False,
             p("a bare word naming a directory that holds tests is no path the call names")),
        Case(c("D26"), bash(sh("cat a_test.go"), cwd=R / "go" / "x"), ALLOW, True,
             p("a bare file name in the call's directory")),
    ]


def main() -> ExitStatus:
    if not (SOURCE / "tools" / "check_test_determinism.py").is_file():
        print(f"no gate to test at {SOURCE}/tools/check_test_determinism.py")
        return ExitStatus(2)
    try:
        setup()
        results = [(case, hook(case.event)) for case in cases()]
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    bad = 0
    for case, outcome in results:
        ok = outcome.status == case.want and reminded(outcome) == case.reminds and case.says in outcome.stderr
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {case.id:<5} want={case.want}/{case.reminds} "
              f"got={outcome.status}/{reminded(outcome)} {case.what}")
        if not ok and (outcome.stderr.strip() or outcome.stdout.strip()):
            print("        " + " | ".join((outcome.stderr + outcome.stdout).strip().splitlines()[-3:])[:300])
    print(f"{len(results)} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
