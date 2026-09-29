#!/usr/bin/env python3
"""Regression cases for precise-hints-guard.py.

Every case pipes a synthetic event into the hook, an edit before it runs or a
Bash call after it, and compares its exit code with what the rule says: 2
refuses the edit or reports the Bash write, 0 lets it through. The
repositories the calls land in are built here, under a fresh temporary
directory: git work trees holding the gate's own modules, copied from the
repository that owns them, a record allowing one `str` in one file, and an
interpreter that runs the gate under that repository's virtual environment,
all committed, with the files a Bash call left behind untracked beside them in
one of them. Nothing is written to the files the events name; the hook only
reads them.

Usage: test_precise_hints_guard.py [HOOK_DIR] [GATE_REPO]
  HOOK_DIR   the hooks directory (default: the parent of this tests/ directory)
  GATE_REPO  the repository whose tools/check_precise_hints.py is tested
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

# A path as an event spells it; text a file holds or an edit writes; an event
# as raw text, for the one that cannot be read; prose a case expects in the
# refusal or names itself by; a case's name; the hook's exit status.
FilePath = NewType("FilePath", str)
FileText = NewType("FileText", str)
RawEvent = NewType("RawEvent", str)
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)


class Replacement(TypedDict, total=False):
    """One replacement an Edit asks for, or one of a MultiEdit's."""

    old_string: FileText
    new_string: FileText
    replace_all: bool


class Request(Replacement, total=False):
    """What an Edit, a Write or a MultiEdit asks for."""

    file_path: FilePath
    content: FileText
    edits: list[Replacement]


FileCount = NewType("FileCount", int)


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
    tool_name: Literal["Edit", "Write", "MultiEdit", "Bash"]
    tool_input: Request
    tool_response: BashResponse
    cwd: FilePath


class Outcome(NamedTuple):
    """What the hook answered: its exit status, and what it wrote to stderr."""

    status: ExitStatus
    stderr: Prose


class Case(NamedTuple):
    """One event, the status the rule wants, text the answer must hold, and the checkout judging a kit."""

    id: CaseId
    event: Event | RawEvent
    want: ExitStatus
    what: Prose
    says: Prose = Prose("")
    checker: Path | None = None


HOOKS = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent).resolve()
GUARD = HOOKS / "precise-hints-guard.py"
SOURCE = Path(sys.argv[2] if len(sys.argv) > 2 else Path.home() / "dev" / "agda" / "aletheia")
GATE_MODULES = ("__init__.py", "_common.py", "_ratchet.py", "check_precise_hints.py")
INTERPRETER = Path("python") / ".venv" / "bin" / "python"

BASE = Path(tempfile.mkdtemp(prefix="hints-guard-test-")).resolve()
R = BASE / "repo"  # a repository holding the gate, with files a Bash call left untracked
CLEAN = BASE / "clean"  # a repository holding the gate and nothing untracked
KIT = BASE / "kitrepo"  # a repository without the gate, whose hooks/ directory keeps its own record
OLD = BASE / "old"  # a checkout whose gate does not take --root and --record
BARE = BASE / "bare"  # a repository without it
OUT = BASE / "outside"  # no repository at all
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
HOOK_ENV = dict(os.environ, TMPDIR=str(BASE / "tmp"), ALETHEIA_REPO=str(SOURCE))

RECORD = """hints:
  - file: tools/recorded.py
    text: "str"
    count: 1
"""
RECORDED = "from pathlib import Path\n\n\ndef f(a: str, b: Path) -> None: ...\n\n\ndef g(c: Path) -> None: ...\n"
PROBE_HEADER = "#!/usr/bin/env bash\npy=python/.venv/bin/python\n"


def git_init(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, env=GIT_ENV)


def gated(repo: Path) -> None:
    """Build a repository holding the gate, its record and one recorded file, all committed."""
    git_init(repo)
    (repo / "tools").mkdir()
    for name in GATE_MODULES:
        shutil.copy(SOURCE / "tools" / name, repo / "tools" / name)
    (repo / "docs").mkdir()
    (repo / "docs" / "PYTHON_IMPRECISE_HINTS.yaml").write_text(RECORD)
    (repo / "tools" / "recorded.py").write_text(RECORDED)
    interpreter = repo / "python" / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    # A wrapper rather than a link: a link's own directory holds no pyvenv.cfg,
    # so the interpreter would not find the environment the gate needs.
    interpreter.write_text(f'#!/bin/sh\nexec "{SOURCE}/python/.venv/bin/python" "$@"\n')
    interpreter.chmod(0o755)
    for step in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                 "commit", "-qm", "gated"]):
        subprocess.run(["git", "-C", str(repo), *step], check=True, capture_output=True, env=GIT_ENV)


def setup() -> None:
    gated(R)
    gated(CLEAN)
    (R / "tools" / "bash_bad.py").write_text("def f(a: str) -> None: ...\n")
    (R / "tools" / "bash_good.py").write_text("from pathlib import Path\ndef f(a: Path) -> None: ...\n")
    git_init(KIT)
    (KIT / "hooks").mkdir()
    (KIT / "hooks" / "PYTHON_IMPRECISE_HINTS.yaml").write_text(RECORD.replace("tools/recorded.py", "recorded.py"))
    (KIT / "hooks" / "recorded.py").write_text(RECORDED)
    (KIT / "hooks" / "bash_bad.py").write_text("def f(a: str) -> None: ...\n")
    (OLD / "tools").mkdir(parents=True)
    (OLD / "tools" / "__init__.py").write_text("")
    (OLD / "tools" / "check_precise_hints.py").write_text(
        "import sys\nprint('usage: check_precise_hints [--file FILE]', file=sys.stderr)\nsys.exit(2)\n")
    (OLD / "python" / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(R / INTERPRETER, OLD / INTERPRETER)
    git_init(BARE)
    OUT.mkdir()
    (OUT / "x.py").write_text("def f(a: str) -> None: ...\n")
    (BASE / "tmp").mkdir()


def hook(event: Event | RawEvent, checker: Path | None = None) -> Outcome:
    raw = event if isinstance(event, str) else json.dumps(event)
    env = HOOK_ENV if checker is None else {**HOOK_ENV, "ALETHEIA_REPO": str(checker)}
    p = subprocess.run([str(GUARD)], input=raw, capture_output=True, text=True, check=False,
                       timeout=120, env=env)
    return Outcome(ExitStatus(p.returncode), Prose(p.stderr))


def write(path: Path, content: FileText, cwd: Path = R) -> Event:
    return {"hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": FilePath(str(path)), "content": content}, "cwd": FilePath(str(cwd))}


def edit(path: Path, old: FileText, new: FileText, *, every: bool = False) -> Event:
    return {"hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": FilePath(str(path)), "old_string": old, "new_string": new,
                           "replace_all": every}, "cwd": FilePath(str(R))}


def multi(path: Path, edits: list[Replacement]) -> Event:
    return {"hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
            "tool_input": {"file_path": FilePath(str(path)), "edits": edits}, "cwd": FilePath(str(R))}


def swap(old: FileText, new: FileText) -> Replacement:
    return {"old_string": old, "new_string": new}


def after(event: Event) -> Event:
    return {**event, "hook_event_name": "PostToolUse"}


def bash_changed(changed: list[Path], more: FileCount, cwd: Path) -> Event:
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}, "cwd": FilePath(str(cwd)),
            "tool_response": {"bashEditDiff": {"changedFiles": [FilePath(str(path)) for path in changed],
                                               "moreFiles": more}}}


def cases() -> list[Case]:
    rec = R / "tools" / "recorded.py"
    t, c, p, n = FileText, CaseId, Prose, FileCount
    return [
        Case(c("P01"), write(R / "tools" / "new.py", t("def f(a: str) -> None: ...\n")), REFUSE,
             p("a new file with a str")),
        Case(c("P02"), write(R / "tools" / "new.py", t("from pathlib import Path\ndef f(a: Path) -> None: ...\n")),
             ALLOW, p("a new file with precise hints")),
        Case(c("P03"), edit(rec, t("def g(c: Path)"), t("def g(c: str)")), REFUSE,
             p("a second str where the record allows one"), p('text: "str"')),
        Case(c("P04"), edit(rec, t("def g(c: Path) -> None: ..."), t("def g(c: Path) -> None:\n    return None")),
             ALLOW, p("an edit that adds no hint")),
        Case(c("P05"), edit(rec, t("a: str, "), t("")), ALLOW, p("an edit that types a recorded hint away")),
        Case(c("P06"), write(R / "probes" / "x--y.sh",
                             t(PROBE_HEADER + "\"$py\" - << 'EOF'\ndef g(z: int) -> None: ...\nEOF\n")), REFUSE,
             p("an int in a probe's Python"), p('text: "int"')),
        Case(c("P07"), write(R / "probes" / "x--y.sh", t(PROBE_HEADER + "\"$py\" - < script.py\n")), REFUSE,
             p("Python a probe hands its interpreter in a form the gate cannot read")),
        Case(c("P08"), write(R / "tools" / "broken.py", t("def f(:\n")), REFUSE, p("a file that does not parse")),
        Case(c("P09"), write(R / "README.md", t("x: str\n")), ALLOW, p("not Python")),
        Case(c("P10"), write(BARE / "tools" / "x.py", t("def f(a: str) -> None: ...\n"), cwd=BARE), ALLOW,
             p("a repository without the gate")),
        Case(c("P11"), write(OUT / "x.py", t("def f(a: str) -> None: ...\n"), cwd=OUT), ALLOW,
             p("no repository at all")),
        Case(c("P12"), edit(rec, t("not in the file"), t("x: str")), ALLOW, p("old text the file does not hold")),
        Case(c("P13"), edit(rec, t("Path"), t("str")), ALLOW, p("old text held twice without replace_all")),
        Case(c("P14"), edit(rec, t("Path"), t("str"), every=True), REFUSE,
             p("replace_all turning every Path into str")),
        Case(c("P15"), multi(rec, [swap(t("-> None: ...\n\n\ndef g"), t("-> None: ...\n\n\ndef g")),
                                   swap(t("def g(c: Path)"), t("def g(c: dict[str, Path])"))]), REFUSE,
             p("the second edit of a MultiEdit"), p("dict[str, Path]")),
        Case(c("P16"), RawEvent("{not json"), ALLOW, p("an event that cannot be read")),
        Case(c("P17"), after(write(R / "tools" / "new.py", t("x: str\n"))), ALLOW, p("an event after the call")),
        Case(c("P18"), write(R / ".archive" / "old.py", t("def f(a: str) -> None: ...\n")), ALLOW,
             p("the archive, which the gate does not read")),
        Case(c("P19"), write(Path("tools/new.py"), t("def f(a: dict[str, int]) -> None: ...\n")), REFUSE,
             p("a path relative to the call's directory"), p("dict[str, int]")),
        Case(c("P20"), write(R / "tools" / "alias.py", t("type Pair = tuple[int, int]\n")), REFUSE,
             p("an alias over ints"), p("type Pair = tuple[int, int]")),
        Case(c("P21"), bash_changed([R / "tools" / "bash_bad.py"], n(0), R), REFUSE,
             p("a str a Bash call wrote, reported after it"), p("bash_bad.py")),
        Case(c("P22"), bash_changed([R / "tools" / "bash_good.py"], n(0), R), ALLOW,
             p("precise hints a Bash call wrote")),
        Case(c("P23"), bash_changed([OUT / "x.py"], n(0), OUT), ALLOW, p("a Bash write outside any repository")),
        Case(c("P24"), bash_changed([], n(1), R), REFUSE,
             p("a call that changed more than it lists: the untracked files are read"), p("bash_bad.py")),
        Case(c("P25"), bash_changed([], n(1), CLEAN), ALLOW,
             p("a call that changed more than it lists, in a clean repository")),
        Case(c("P26"), bash_changed([R / "docs" / "PYTHON_IMPRECISE_HINTS.yaml"], n(0), R), ALLOW,
             p("a Bash write that is not Python")),
        Case(c("P27"), {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {},
                        "cwd": FilePath(str(R)), "tool_response": {}}, ALLOW,
             p("a Bash call the harness reports no diff for")),
        Case(c("P28"), {**bash_changed([R / "tools" / "bash_bad.py"], n(0), R), "hook_event_name": "PreToolUse"},
             ALLOW, p("a Bash call before it runs, which the hook leaves to the gate after it")),
        Case(c("P29"), write(KIT / "hooks" / "new.py", t("def f(a: str) -> None: ...\n"), cwd=KIT), REFUSE,
             p("a str in a directory held to its own record"), p("new.py")),
        Case(c("P30"), write(KIT / "hooks" / "new.py", t("from pathlib import Path\ndef f(a: Path) -> None: ...\n"),
                             cwd=KIT), ALLOW, p("precise hints in a directory held to its own record")),
        Case(c("P31"), edit(KIT / "hooks" / "recorded.py", t("def g(c: Path)"), t("def g(c: str)")), REFUSE,
             p("a second str where the directory's record allows one"), p('text: "str"')),
        Case(c("P32"), write(KIT / "other.py", t("def f(a: str) -> None: ...\n"), cwd=KIT), ALLOW,
             p("a file of that repository outside the directory with the record")),
        Case(c("P33"), bash_changed([KIT / "hooks" / "bash_bad.py"], n(0), KIT), REFUSE,
             p("a Bash write in a directory held to its own record"), p("bash_bad.py")),
        Case(c("P34"), write(KIT / "hooks" / "new.py", t("def f(a: str) -> None: ...\n"), cwd=KIT), REPORTED,
             p("a checkout whose gate refuses --root judges nothing, and says so"),
             p("refused its arguments"), checker=OLD),
    ]


def main() -> ExitStatus:
    if not (SOURCE / "tools" / "check_precise_hints.py").is_file():
        print(f"no gate to test at {SOURCE}/tools/check_precise_hints.py")
        return ExitStatus(2)
    try:
        setup()
        results = [(case, hook(case.event, case.checker)) for case in cases()]
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    bad = 0
    for case, outcome in results:
        ok = outcome.status == case.want and case.says in outcome.stderr
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {case.id:<5} want={case.want} got={outcome.status} {case.what}")
        if not ok and outcome.stderr.strip():
            print("        " + " | ".join(outcome.stderr.strip().splitlines()[-3:])[:300])
    print(f"{len(results)} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
