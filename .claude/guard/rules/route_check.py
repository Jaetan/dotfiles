"""Every Bash and Monitor call is refused while its shell has no claude-call route, and every one after a call that
claude-call never recorded.

THE RULE (user, 2026-11-08 and 2026-11-10). Claude Code runs every Bash and Monitor call in the shell CLAUDE_CODE_SHELL
names, chosen once per claude process, and claude-call takes the call only where that shell is its wrapper,
route.WRAPPER, writing the call's line to the process's ledger (state/route.py) before it starts the call's scope. So a
call of any agent is refused at PreToolUse while the process's state holds the flag, then while, in the hook's
environment, CLAUDE_CODE_SHELL is not an absolute path named `bash` that resolves to the wrapper and is an executable
file, or CLAUDE_CODE_SHELL_PREFIX or BASH_ENV is set at all, empty included, each a second route beside the wrapper.
A call it passes keeps the ledger's end under its tool_use_id. At PostToolUse and PostToolUseFailure the call's line is
awaited past that end, or past the ledger's start where none was kept, and the end is forgotten; a call with no line
sets the flag, naming its text, its tool_use_id and the ledger, and is refused. The flag is a `notice` FLAG in the
process's shared state file, which /clear does not clear, so only a new claude process or the rule's switch file ends
it. Each refusal names what it found and the ways out.

EXCEPTIONS: none.

KNOWN FALSE PASS: two calls of identical text in flight at once, one of them recorded, satisfy each other.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import NewType

from dispatcher.core import WHOLE_KIT, Event, FolderPath, HookEvent, ToolInput, ToolName, state_places
from dispatcher.tree import ShellText
from dispatcher.verdict import Prose, RuleName, Verdict, refuse
from dispatcher.words import Environment, VarName
from state import route
from state.session import FieldName, FieldValue, RecordId, Session, process_key

ToolUseId = NewType("ToolUseId", str)
"""The id Claude Code gives one tool call, the same at its PreToolUse and its PostToolUse."""

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
SHELL = VarName("CLAUDE_CODE_SHELL")
NAME = "bash"
SECOND_ROUTES = {
    VarName("CLAUDE_CODE_SHELL_PREFIX"): Prose(
        "so every call reaches the wrapper as the prefix's line, which it never hands to claude-call"),
    VarName("BASH_ENV"): Prose("so every bash the session starts runs that file first, a second route beside the wrapper"),
}
SWITCH = RuleName("route-check")
LIVE_OFF = FolderPath("~/.config/claude-guard/off")
FLAG = RecordId("route-unrecorded")
OFFSET = "route-offset:"
TEXT, CALL, LEDGER, AT = FieldName("text"), FieldName("tool_use_id"), FieldName("ledger"), FieldName("at")
KEPT = 300
"""How many characters of the unrecorded call's text the flag keeps."""
ADVICE = """this call's shell has no claude-call route, and a call that never reaches the wrapper {wrapper} runs outside
claude-call's scope, memory cap, view and log.

{problems}

Set CLAUDE_CODE_SHELL to {wrapper}, unset CLAUDE_CODE_SHELL_PREFIX and BASH_ENV, and start a new claude process,
since Claude Code picks its shell once per process.
Or switch this rule off with the file {rule}, or the whole kit with {kit}."""
UNRECORDED = """a Bash or Monitor call of this claude process left no claude-call record, so it ran outside
claude-call's scope, memory cap, view and log, and every later Bash and Monitor call of the process is refused.

  * the call {text!r}, tool_use_id {call!r}, has no line in the ledger {ledger}

Fix the route, then start a new claude process, since this flag lives in the process's state, which /clear does not
clear. Or switch this rule off with the file {rule}."""


def shell_problem(shell: ShellText) -> Prose | None:
    """The first check the shell ``shell`` fails of being the wrapper, as a refusal's line; None where it is."""
    wrapper = os.path.realpath(route.WRAPPER)
    if not shell:
        broken = "no path, so Claude Code picks a shell of its own"
    elif not os.path.isabs(shell):  # resolved against the hook's directory, which is not the shell's
        broken = "not an absolute path"
    elif os.path.basename(shell) != NAME:  # Claude Code keeps bash's behaviour behind tests of this name
        broken = f"named {os.path.basename(shell)!r}, not `{NAME}`"
    elif (resolved := os.path.realpath(shell)) != wrapper:
        broken = f"resolving to {resolved}, not the wrapper's {wrapper}"
    elif not (os.path.isfile(shell) and os.access(shell, os.X_OK)):
        broken = "not an executable file, so Claude Code passes it over"
    else:
        return None
    return Prose(f"  * {SHELL} is {shell!r}, {broken}, and the wrapper is never reached")


def problems(env: Environment) -> list[Prose]:
    """Each problem of the route ``env`` sets, a line each."""
    found = [shell_problem(env.get(SHELL, ShellText("")))]
    found += [Prose(f"  * {name} is {env[name]!r}, {broken}") for name, broken in SECOND_ROUTES.items() if name in env]
    return [problem for problem in found if problem is not None]


def unrecorded(flag: Mapping[FieldName, FieldValue], off: FolderPath) -> Verdict:
    """The refusal of the flag ``flag``, naming the unrecorded call and the ways out."""
    return refuse(Prose(UNRECORDED.format(text=flag[TEXT], call=flag[CALL], ledger=flag[LEDGER],
                                          rule=os.path.join(off, SWITCH))))


def judge(event: Event) -> Verdict | None:
    """At PreToolUse, refuse a call while the flag stands or its shell has no route, else keep the ledger's end; after
    the call, set the flag and refuse where the ledger holds no line of it."""
    off = LIVE_OFF if event.off is None else event.off
    places = state_places(event.places)
    ledger = route.ledger(places, process_key(event.reading.env, places.proc))
    session = event.state()
    given = event.payload.get("tool_use_id")
    call = ToolUseId(given) if isinstance(given, str) else None
    kept = None if call is None else RecordId(OFFSET + call)
    if event.name == "PreToolUse":
        return before(event, session, ledger, kept, off)
    offset = next((route.Offset(int(record.fields[AT])) for record in session.records("notice")
                   if record.ident == kept), route.Offset(0))
    if kept is not None:
        _ = session.forget("notice", kept)
    text = event.payload.get("tool_input", ToolInput()).get("command", ShellText(""))
    if route.awaited(ledger, offset, route.digest(text)):
        return None
    flag = {TEXT: FieldValue(text[:KEPT]), CALL: FieldValue(call or ""), LEDGER: FieldValue(str(ledger))}
    session.record("notice", FLAG, flag, shared=True)
    return unrecorded(flag, off)


def before(event: Event, session: Session, ledger: Path, kept: RecordId | None, off: FolderPath) -> Verdict | None:
    """The PreToolUse arm: the flag's refusal, else the route's, else None with the ledger's end kept as ``kept``."""
    flag = next((record.fields for record in session.records("notice") if record.ident == FLAG), None)
    if flag is not None:
        return unrecorded(flag, off)
    found = problems(event.reading.env)
    if found:
        return refuse(Prose(ADVICE.format(wrapper=route.WRAPPER, problems="\n".join(found),
                                          rule=os.path.join(off, SWITCH), kit=os.path.join(off, WHOLE_KIT))))
    if kept is not None:
        session.record("notice", kept, {AT: FieldValue(str(route.end(ledger)))})
    return None
