#!/usr/bin/env python3
"""Regression cases for entry-point-run-guard.py.

Every case builds a git work tree of its own under a fresh temporary directory,
commits it at a fixed time, changes what the case changes, writes the dribble,
and writes a transcript holding the Bash commands the case says the session
issued, each at the time the case gives. It then pipes a PostToolUse event to
the hook and compares the answer with what the rule says: exit 2 reports an
entry point the commit changes and the session has not run since its last
edit, exit 0 lets the call through. Every time is set explicitly, the files'
with os.utime and HEAD's through GIT_COMMITTER_DATE, so no case reads a clock.

Usage: test_entry_point_run_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

# A shell command line; text a file holds; a path relative to the work tree; an
# argument handed to git; a transcript timestamp; a path as JSON spells it; an
# event as the hook reads it on stdin; prose a case names itself by or expects;
# a case's name; seconds since the epoch; the hook's exit status.
CommandLine = NewType("CommandLine", str)
FileText = NewType("FileText", str)
RelPath = NewType("RelPath", str)
GitArgument = NewType("GitArgument", str)
Stamp = NewType("Stamp", str)
PathText = NewType("PathText", str)
EventText = NewType("EventText", str)
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
Instant = NewType("Instant", int)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Tool = Literal["Bash", "Write"]

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "entry-point-run-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="entry-point-guard-test-")).resolve()
COMMITTED = Instant(1_800_000_000)  # HEAD's commit
EDITED = Instant(COMMITTED + 100)  # the entry point's last edit
DRIBBLE_AT = Instant(COMMITTED + 200)  # the dribble's write
BEFORE = Instant(COMMITTED + 50)  # a run before the edit
AFTER = Instant(COMMITTED + 150)  # a run after it
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_DATE": f"@{COMMITTED} +0000", "GIT_COMMITTER_DATE": f"@{COMMITTED} +0000"}
HOOK_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

MAIN = FileText('import sys\n\n\ndef main() -> int:\n    return 0\n\n\nif __name__ == "__main__":\n'
                '    sys.exit(main())\n')
COMMIT_DRIBBLE = FileText("#!/usr/bin/env bash\nset -euo pipefail\ngit add -u\ngit commit -S -q -m change\n"
                          "git push\n")


class BashInput(TypedDict):
    command: CommandLine
    description: Prose


class Block(TypedDict):
    type: Literal["tool_use"]
    name: Literal["Bash"]
    input: BashInput


class Message(TypedDict):
    content: list[Block]


class Transcribed(TypedDict):
    """One transcript line, as the harness writes it for a Bash call."""

    type: Literal["assistant"]
    timestamp: Stamp
    cwd: PathText
    message: Message


class Issue(NamedTuple):
    """A Bash command the transcript says the session issued, its time and directory.

    ``{repo}`` in the command stands for the case's work tree."""

    command: CommandLine
    at: Instant
    cwd: Path | None = None


type Builder = Callable[[Path], list[Issue]]


class Case(NamedTuple):
    """One scenario: what it changes, what was run, the answer the rule wants, and text stderr holds."""

    id: CaseId
    what: Prose
    want: ExitStatus
    says: Prose
    build: Builder
    dribble: FileText = COMMIT_DRIBBLE
    dribble_at: Instant = DRIBBLE_AT
    tool: Tool = "Bash"


class Outcome(NamedTuple):
    status: ExitStatus
    stderr: Prose


def put(path: Path, text: FileText, at: Instant = COMMITTED, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)
    os.utime(path, (at, at))


def git(repo: Path, *arguments: GitArgument) -> None:
    subprocess.run(["git", "-C", str(repo), *arguments], check=True, capture_output=True, env=GIT_ENV)


def repository(root: Path) -> Path:
    """A committed work tree: an entry point, a library module, scripts, a test helper, probes."""
    repo = root / "repo"
    put(repo / "tools" / "__init__.py", FileText(""))
    put(repo / "tools" / "x.py", MAIN)
    put(repo / "tools" / "lib.py", FileText("VALUE = 1\n"))
    put(repo / "script.sh", FileText("#!/bin/sh\necho hi\n"), executable=True)
    put(repo / "notexec.sh", FileText("#!/bin/sh\necho hi\n"))
    put(repo / "python" / "tests" / "helper.py", MAIN)
    put(repo / "probes" / "tools_x.py--runs.sh",
        FileText('#!/usr/bin/env bash\ncd "$(dirname "$0")/.." || exit 2\npy=python3\n"$py" -m tools.x\n'),
        executable=True)
    put(repo / "probes" / "tools_x.py--imports.sh",
        FileText('#!/usr/bin/env bash\ncd "$(dirname "$0")/.." || exit 2\npython3 -c "import tools.x"\n'),
        executable=True)
    put(repo / "probes" / "run_all.sh", FileText('#!/usr/bin/env bash\nfor p in probes/*--*.sh; do "$p"; done\n'),
        executable=True)
    put(repo / ".gitignore", FileText(".commands-to-run.sh\n"))
    a = GitArgument
    git(repo, a("init"), a("-q"))
    git(repo, a("add"), a("-A"))
    git(repo, a("-c"), a("user.name=t"), a("-c"), a("user.email=t@t"), a("-c"), a("commit.gpgsign=false"),
        a("commit"), a("-qm"), a("base"))
    return repo


EDITED_MAIN = FileText(MAIN + "# edited\n")
EDITED_SCRIPT = FileText("#!/bin/sh\necho edited\n")
EDITED_LIBRARY = FileText("VALUE = 2\n")


def edit(repo: Path, rel: RelPath, text: FileText = EDITED_MAIN, executable: bool = False) -> None:
    put(repo / rel, text, EDITED, executable)


def iso(at: Instant) -> Stamp:
    return Stamp(datetime.fromtimestamp(at, UTC).isoformat().replace("+00:00", "Z"))


def transcript(path: Path, repo: Path, issued: list[Issue]) -> None:
    lines: list[FileText] = []
    for each in issued:
        command = CommandLine(each.command.replace("{repo}", str(repo)))
        entry = Transcribed(
            type="assistant", timestamp=iso(each.at), cwd=PathText(str(each.cwd or repo)),
            message=Message(content=[Block(type="tool_use", name="Bash",
                                           input=BashInput(command=command, description=Prose("x")))]))
        lines.append(FileText(json.dumps(entry) + "\n"))
    path.write_text("".join(lines))


def hook(event: EventText) -> Outcome:
    done = subprocess.run([sys.executable, str(GUARD)], input=event, capture_output=True, text=True,
                          check=False, env=HOOK_ENV)
    return Outcome(ExitStatus(done.returncode), Prose(done.stderr))


def event(repo: Path, transcript_path: Path, tool: Tool = "Bash") -> EventText:
    return EventText(json.dumps({"hook_event_name": "PostToolUse", "tool_name": tool, "cwd": str(repo),
                                 "transcript_path": str(transcript_path)}))


def run_case(case: Case) -> Outcome:
    root = BASE / case.id
    repo = repository(root)
    issued = case.build(repo)
    put(repo / ".commands-to-run.sh", case.dribble, case.dribble_at, executable=True)
    log = root / "transcript.jsonl"
    transcript(log, repo, issued)
    return hook(event(repo, log, case.tool))


def changed(rel: RelPath, *issued: Issue, text: FileText = EDITED_MAIN, executable: bool = False) -> Builder:
    def build(repo: Path) -> list[Issue]:
        edit(repo, rel, text, executable)
        return list(issued)
    return build


def run(command: CommandLine, at: Instant = AFTER, cwd: Path | None = None) -> Issue:
    return Issue(command, at, cwd)


def cases() -> list[Case]:
    c, p, r, sh = CaseId, Prose, RelPath, CommandLine
    x = r("tools/x.py")
    return [
        Case(c("E01"), p("no commit in the dribble"), ALLOW, p(""), changed(x),
             dribble=FileText("#!/usr/bin/env bash\ngit push\n")),
        Case(c("E02"), p("a dribble older than HEAD, already run"), ALLOW, p(""), changed(x),
             dribble_at=Instant(COMMITTED - 10)),
        Case(c("E03"), p("an entry point changed and never run"), REFUSE, p("tools/x.py  (python -m tools.x)"),
             changed(x)),
        Case(c("E04"), p("run as a module after the edit"), ALLOW, p(""), changed(x, run(sh("python3 -m tools.x")))),
        Case(c("E05"), p("run only before the edit"), REFUSE, p("tools/x.py"),
             changed(x, run(sh("python3 -m tools.x"), BEFORE))),
        Case(c("E06"), p("run through cd, taskset and a redirection from elsewhere"), ALLOW, p(""),
             changed(x, run(sh("cd {repo} && taskset -c 0-19 python/.venv/bin/python -m tools.x > /tmp/l 2>&1; "
                               "echo EXIT=$?"), cwd=Path("/")))),
        Case(c("E07"), p("run as a script with arguments"), ALLOW, p(""),
             changed(x, run(sh("python3 tools/x.py --flag")))),
        Case(c("E08"), p("only its help read"), REFUSE, p("tools/x.py"),
             changed(x, run(sh("python3 -m tools.x --help")))),
        Case(c("E09"), p("named and grepped, never run"), REFUSE, p("tools/x.py"),
             changed(x, run(sh("sed -n 1p tools/x.py; grep -- '-m tools.x' notes")))),
        Case(c("E10"), p("a heredoc body spelling the run"), REFUSE, p("tools/x.py"),
             changed(x, run(sh("cat > notes <<'EOF'\npython3 -m tools.x\nEOF\necho done")))),
        Case(c("E11"), p("imported by python -c"), REFUSE, p("tools/x.py"),
             changed(x, run(sh('python3 -c "import tools.x"')))),
        Case(c("E12"), p("a probe whose text runs it"), ALLOW, p(""),
             changed(x, run(sh("probes/tools_x.py--runs.sh")))),
        Case(c("E13"), p("a probe of the same subject that only imports it"), REFUSE, p("tools/x.py"),
             changed(x, run(sh("probes/tools_x.py--imports.sh")))),
        Case(c("E14"), p("the whole probe store run"), ALLOW, p(""),
             changed(x, run(sh("./probes/run_all.sh > /tmp/p 2>&1")))),
        Case(c("E15"), p("a changed executable script run by its path"), ALLOW, p(""),
             changed(r("script.sh"), run(sh("./script.sh")), text=EDITED_SCRIPT, executable=True)),
        Case(c("E16"), p("a changed executable script never run"), REFUSE, p("script.sh  (script.sh)"),
             changed(r("script.sh"), text=EDITED_SCRIPT, executable=True)),
        Case(c("E17"), p("a script handed to bash"), ALLOW, p(""),
             changed(r("script.sh"), run(sh("bash script.sh arg")), text=EDITED_SCRIPT, executable=True)),
        Case(c("E18"), p("a .py entry point run by a path the shell refuses (not executable)"), REFUSE,
             p("tools/x.py"), changed(x, run(sh("./tools/x.py")))),
        Case(c("E19"), p("a library module changed"), ALLOW, p(""), changed(r("tools/lib.py"), text=EDITED_LIBRARY)),
        Case(c("E20"), p("a helper under tests changed"), ALLOW, p(""), changed(r("python/tests/helper.py"))),
        Case(c("E21"), p("a script with a shebang that is not executable"), ALLOW, p(""),
             changed(r("notexec.sh"), text=EDITED_SCRIPT)),
        Case(c("E22"), p("a new untracked entry point never run"), REFUSE, p("tools/new.py"),
             changed(r("tools/new.py"))),
        Case(c("E23"), p("a loop variable is not resolved"), REFUSE, p("loop variable"),
             changed(x, run(sh('for p in probes/*.sh; do "$p"; done')))),
        Case(c("E24"), p("a program spelled through a variable the command set"), ALLOW, p(""),
             changed(x, run(sh('S=probes && "$S/tools_x.py--runs.sh"')))),
        Case(c("E25"), p("a commit through git -c inside an if"), REFUSE, p("tools/x.py"), changed(x),
             dribble=FileText("#!/usr/bin/env bash\nif true; then\n  git -c user.name=t commit -qm m\nfi\n")),
        Case(c("E26"), p("env, nice and timeout around the run"), ALLOW, p(""),
             changed(x, run(sh("env FOO=1 nice -n 5 timeout -s KILL 30 python3 -m tools.x")))),
        Case(c("E27"), p("the module run from outside the work tree"), REFUSE, p("tools/x.py"),
             changed(x, run(sh("python3 -m tools.x"), cwd=Path("/")))),
        Case(c("E28"), p("a Write call is checked like a Bash one"), REFUSE, p("tools/x.py"), changed(x),
             tool="Write"),
        Case(c("E29"), p("a run issued in the same second as the edit does not count"), REFUSE,
             p("tools/x.py"), changed(x, run(sh("python3 -m tools.x"), EDITED))),
        Case(c("E30"), p("a namespace package's module run from the directory that names it"), ALLOW, p(""),
             changed(r("python/benchmarks/y.py"), run(sh("cd {repo}/python && python3 -m benchmarks.y"),
                                                     cwd=Path("/")))),
        Case(c("E31"), p("a namespace package's module run where its name resolves to nothing"), REFUSE,
             p("python/benchmarks/y.py"), changed(r("python/benchmarks/y.py"), run(sh("python3 -m benchmarks.y")))),
        Case(c("E32"), p("a namespace package's __main__ run by the package's name"), ALLOW, p(""),
             changed(r("python/benchmarks/__main__.py"), run(sh("cd {repo}/python && python3 -m benchmarks"),
                                                            cwd=Path("/")))),
    ]


def special_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases that need more than one call, or no transcript; each says whether it agrees."""
    results: list[tuple[CaseId, Prose, bool]] = []
    x = RelPath("tools/x.py")

    root = BASE / "S01"
    repo = repository(root)
    edit(repo, x)
    put(repo / ".commands-to-run.sh", COMMIT_DRIBBLE, DRIBBLE_AT, executable=True)
    log = root / "transcript.jsonl"
    transcript(log, repo, [])
    first, second = hook(event(repo, log)), hook(event(repo, log))
    results.append((CaseId("S01"), Prose("the same finding is not repeated on the next call"),
                    first.status == REFUSE and second.status == ALLOW))

    put(repo / ".commands-to-run.sh", COMMIT_DRIBBLE, Instant(DRIBBLE_AT + 5), executable=True)
    third = hook(event(repo, log))
    results.append((CaseId("S02"), Prose("a rewritten dribble repeats the finding"), third.status == REFUSE))

    transcript(log, repo, [run(CommandLine("python3 -m tools.x"), AFTER)])
    fourth = hook(event(repo, log))
    results.append((CaseId("S03"), Prose("a run appended to the transcript clears it"), fourth.status == ALLOW))

    root = BASE / "S04"
    repo = repository(root)
    edit(repo, x)
    put(repo / ".commands-to-run.sh", COMMIT_DRIBBLE, DRIBBLE_AT, executable=True)
    missing = hook(event(repo, root / "absent.jsonl"))
    results.append((CaseId("S04"), Prose("no transcript is no evidence of a run"), missing.status == REFUSE))

    unreadable = hook(EventText("{not json"))
    results.append((CaseId("S05"), Prose("an event that cannot be read goes through"), unreadable.status == ALLOW))
    before = hook(EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(repo)})))
    results.append((CaseId("S06"), Prose("an event before the call is left alone"), before.status == ALLOW))
    return results


def main() -> ExitStatus:
    bad = 0
    total = 0
    try:
        for case in cases():
            outcome = run_case(case)
            total += 1
            ok = outcome.status == case.want and case.says in outcome.stderr
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case.id:<4} want={case.want} got={outcome.status} {case.what}")
            if not ok and outcome.stderr.strip():
                print("        " + " | ".join(outcome.stderr.strip().splitlines()[-4:])[:300])
        for case_id, what, ok in special_cases():
            total += 1
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case_id:<4} {what}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"{total} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
