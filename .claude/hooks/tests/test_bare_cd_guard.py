#!/usr/bin/env python3
"""Regression cases for bare-cd-guard.py.

Every case pipes one PreToolUse event for a Bash call to the hook, run by its
path as the harness runs it, with the project root a fresh temporary
directory (holding python/) named in CLAUDE_PROJECT_DIR and the call's cwd that
root, and compares the answer with the rule: exit 2 refuses a call whose own
shell changes directory anywhere but the root, naming the `cd` and where it
goes; exit 0 lets the rest through.

Usage: test_bare_cd_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple, NewType

# A case's name; prose a case names itself by or expects; the text of a call;
# an event as the hook reads it; the hook's exit status.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
CallText = NewType("CallText", str)
EventText = NewType("EventText", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "bare-cd-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="bare-cd-guard-test-")).resolve()
ROOT = BASE / "project"
SUB = ROOT / "python"
OUT = BASE / "elsewhere"
UNNAMED = Prose("a directory this call does not name")
NOTHING = Prose("")


class Case(NamedTuple):
    """A call, the answer the rule wants, and a line the refusal must print."""

    id: CaseId
    what: Prose
    call: CallText
    want: ExitStatus
    says: Prose


class Outcome(NamedTuple):
    """The hook's exit status and what it printed."""

    status: ExitStatus
    stderr: Prose


def hook(call: CallText, tool: Prose = Prose("Bash"), root: Path | None = ROOT) -> Outcome:
    """Pipe one PreToolUse event for ``call`` to the hook, ``root`` the project root or unset."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if root is not None:
        env["CLAUDE_PROJECT_DIR"] = str(root)
    event = EventText(json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(ROOT),
        "tool_input": {"command": call},
    }))
    done = subprocess.run([str(GUARD)], input=event, capture_output=True, text=True, check=False, env=env)
    return Outcome(ExitStatus(done.returncode), Prose(done.stderr))


def moved(spelled: Prose, where: Path | Prose) -> Prose:
    """The refusal's whole line for a command ``spelled`` that moves the shell to ``where``."""
    return Prose(f"`{spelled}` moves the shared shell to {where}\n")


def refused(case_id: CaseId, what: Prose, call: CallText, spelled: Prose, where: Path | Prose) -> Case:
    """A case the rule refuses, its line naming ``spelled`` moving the shell to ``where``."""
    return Case(case_id, what, call, REFUSE, moved(spelled, where))


def allowed(case_id: CaseId, what: Prose, call: CallText) -> Case:
    """A case the rule lets through."""
    return Case(case_id, what, call, ALLOW, NOTHING)


def cases() -> list[Case]:
    """Every case, refusals first."""
    cd_out = Prose(f"cd {OUT}")
    return [
        refused(CaseId("R01"), Prose("a cd outside the project"), CallText(f"cd {OUT} && ls"), cd_out, OUT),
        refused(CaseId("R02"), Prose("a cd into a directory of the project"), CallText("cd python && ls"),
                Prose("cd python"), SUB),
        refused(CaseId("R03"), Prose("a cd to an absolute directory under the root"),
                CallText(f"cd {SUB}; ls"), Prose(f"cd {SUB}"), SUB),
        refused(CaseId("R04"), Prose("a cd with no operand goes home"), CallText("cd"), Prose("cd"), UNNAMED),
        refused(CaseId("R05"), Prose("cd - goes to the directory before"), CallText("cd -"), Prose("cd -"),
                UNNAMED),
        refused(CaseId("R06"), Prose("pushd moves the shell"), CallText(f"pushd {OUT}"), Prose(f"pushd {OUT}"),
                OUT),
        refused(CaseId("R07"), Prose("popd moves it back to an unnamed directory"), CallText("popd"),
                Prose("popd"), UNNAMED),
        refused(CaseId("R08"), Prose("builtin cd is cd"), CallText(f"builtin cd {OUT}"), cd_out, OUT),
        refused(CaseId("R09"), Prose("command cd is cd"), CallText(f"command cd {OUT}"), cd_out, OUT),
        Case(CaseId("R10"), Prose("an operand the reader cannot know"),
             CallText('cd "$BARE_CD_GUARD_UNSET_VARIABLE"'), REFUSE, UNNAMED),
        refused(CaseId("R11"), Prose("a function the call defines and runs"),
                CallText(f"f() {{ cd {OUT}; }}; f"), cd_out, OUT),
        refused(CaseId("R12"), Prose("the text eval runs"), CallText(f"eval 'cd {OUT}'"), cd_out, OUT),
        refused(CaseId("R13"), Prose("a brace group"), CallText(f"{{ cd {OUT}; ls; }}"), cd_out, OUT),
        refused(CaseId("R14"), Prose("an if body"), CallText(f"if true; then cd {OUT}; fi"), cd_out, OUT),
        refused(CaseId("R15"), Prose("options before the operand are read past"), CallText(f"cd -P {OUT}"),
                Prose(f"cd -P {OUT}"), OUT),
        refused(CaseId("R16"), Prose("the last element of a pipeline runs in zsh's own shell"),
                CallText(f"ls | cd {OUT}"), cd_out, OUT),
        refused(CaseId("R17"), Prose("a cd to the root, then one off it"),
                CallText(f"cd {ROOT} && cd python"), Prose("cd python"), SUB),
        refused(CaseId("R18"), Prose("popd's operand is a place on its stack, not a directory"),
                CallText("popd +1"), Prose("popd +1"), UNNAMED),
        refused(CaseId("R19"), Prose("the directory is named as the shell resolves it"),
                CallText("cd python/../python"), Prose("cd python/../python"), SUB),
        refused(CaseId("R20"), Prose("-- ends the options, so the operand may start with a dash"),
                CallText("cd -- -dir"), Prose("cd -- -dir"), ROOT / "-dir"),
        allowed(CaseId("A01"), Prose("a cd inside a subshell"), CallText(f"(cd {OUT} && ls)")),
        allowed(CaseId("A02"), Prose("a cd to the project root"), CallText(f"cd {ROOT} && ls")),
        allowed(CaseId("A03"), Prose("a cd that lands on the root"), CallText(f"cd {SUB}/.. && ls")),
        allowed(CaseId("A04"), Prose("a call with no cd"), CallText(f"ls -la {OUT}")),
        allowed(CaseId("A05"), Prose("cd as an argument"), CallText(f"echo cd {OUT}")),
        allowed(CaseId("A06"), Prose("a cd inside a substitution"), CallText(f"x=$(cd {OUT} && pwd)")),
        allowed(CaseId("A07"), Prose("a cd in a background job"), CallText(f"cd {OUT} &")),
        allowed(CaseId("A08"), Prose("a cd a carrier runs"), CallText(f"bash -c 'cd {OUT}'")),
        allowed(CaseId("A09"), Prose("a cd in a heredoc's data"), CallText(f"cat <<'EOF'\ncd {OUT}\nEOF")),
        allowed(CaseId("A10"), Prose("the first element of a pipeline runs in a subshell"),
                CallText(f"cd {OUT} | cat")),
        allowed(CaseId("A11"), Prose("a subshell around a pipeline ending in cd"),
                CallText(f"(ls | cd {OUT})")),
        allowed(CaseId("A12"), Prose("a middle element of a pipeline runs in a subshell"),
                CallText(f"ls | cd {OUT} | cat")),
        allowed(CaseId("A13"), Prose("a pipeline sent to the background ends in no shell of the call's"),
                CallText(f"ls | cd {OUT} &")),
    ]


def special_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases that are not one call under the default root."""
    results: list[tuple[CaseId, Prose, bool]] = []
    results.append((CaseId("S01"), Prose("a tool other than Bash is left alone"),
                    hook(CallText(f"cd {OUT}"), tool=Prose("Monitor")).status == ALLOW))
    no_root = hook(CallText(f"cd {ROOT}"), root=None)
    results.append((CaseId("S02"), Prose("with no project root even a cd to the start is refused"),
                    no_root.status == REFUSE and moved(Prose(f"cd {ROOT}"), ROOT) in no_root.stderr))
    unreadable = subprocess.run([str(GUARD)], input="not json", capture_output=True, text=True, check=False)
    results.append((CaseId("S03"), Prose("an event that cannot be read goes through, reported"),
                    unreadable.returncode == REPORTED))
    unfollowed = hook(CallText("cd $(("))
    results.append((CaseId("S04"), Prose("text the reader cannot follow goes through"),
                    unfollowed.status == ALLOW and "could not be read" in unfollowed.stderr))
    refusal = hook(CallText(f"cd {OUT}"))
    results.append((CaseId("S05"), Prose("the refusal shows the subshell form and the root"),
                    "(cd /abs/dir && " in refusal.stderr and f"({ROOT})" in refusal.stderr))
    return results


def main() -> ExitStatus:
    """Run every case; print one line each, MISS for a disagreement, then the count."""
    bad = 0
    total = 0
    SUB.mkdir(parents=True)
    OUT.mkdir()
    try:
        for case in cases():
            outcome = hook(case.call)
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
