"""Each Bash and Monitor call leaves claude-call a line naming its memory tier, heavy or light, before the call starts.

THE RULE (user, 2026-11-07 and 2026-11-14). claude-call caps each call's scope in two tiers: heavy_max for a call
running a command its project declares heavy, call_max for every other. At PreToolUse of a Bash or Monitor call, of any
agent, this rule appends to the claude process's tier file the line of the call's command text and its tier
(state/tiers.py): heavy where heavy() (dispatcher/heavy.py) answers for any command of the call model under the
declaration holding for that command's directory, a command the kit cannot name included; light otherwise. claude-call
looks the line up under the text it runs, which for a Bash call is the text the machine's rewrite hook makes of it
(machine.toml's [rewrite], rtk's PreToolUse hook): so the rule runs that hook as Claude Code does, the event's JSON on
its stdin, in the event's cwd and the environment the dispatcher was given, for HOOK_TIMEOUT at most, and where the
hook names a text of its own (hookSpecificOutput.updatedInput.command on its stdout, exit 0) it appends a second line,
the same tier, under that text. A hook printing nothing keeps the call's text, and a Monitor call goes to no hook, which
settings.json registers for Bash alone. The rule itself never refuses: a call claude-call finds no line for gets
heavy_max. A call the kit cannot read is refused before any rule runs, and a process the kit cannot key or a
declaration it cannot read is a fault of the kit, each refused by the dispatcher, so none of them leaves a line.

Exceptions (2), each with the failure it holds:
- a tier file that cannot be written is a note naming it and no refusal: claude-call then finds no line and caps the
  call at heavy_max anyway, so a refusal would only stop a call the cap already holds.
- a rewrite hook that cannot start, outruns HOOK_TIMEOUT, exits non-zero or prints anything else is a note naming it,
  and the call's own text keeps its one line: claude-call then finds no line for a text the hook rewrote and caps it
  at heavy_max, the safe side, so a refusal would only stop a call the cap already holds.
"""

import json
import shlex
import subprocess
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict

from dispatcher.core import Event, HookEvent, ToolName, state_places
from dispatcher.heavy import heavy
from dispatcher.tree import Seconds, ShellText
from dispatcher.verdict import Prose, Verdict, context
from state import tiers
from state.session import process_key

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
REWRITTEN = ToolName("Bash")
HOOK_TIMEOUT = Seconds(2.0)
UNWRITTEN = "the tier file {path} cannot be written ({fault}), so claude-call finds no line and caps this call at heavy_max."
UNREAD = ("the rewrite hook {hook} {fault}, so a text it runs in place of this call's finds no tier line and "
          "claude-call caps it at heavy_max.")
UNANSWERED = "printed no hookSpecificOutput.updatedInput.command"
HookStdout = NewType("HookStdout", bytes)
UpdatedInput = TypedDict("UpdatedInput", {"command": ShellText}, total=False)
HookOutput = TypedDict("HookOutput", {"updatedInput": UpdatedInput}, total=False)
HookAnswer = TypedDict("HookAnswer", {"hookSpecificOutput": HookOutput}, total=False)


class Rewrite(NamedTuple):
    """What the rewrite hook makes of a call: the text run in its place, None where it keeps the call's own or none
    is declared, and the note naming the hook where its answer cannot be read."""

    text: ShellText | None
    note: Prose | None


def tier(event: Event) -> tiers.Tier:
    """Heavy where heavy() answers for any command of the call under its directory's declaration, light otherwise."""
    return "heavy" if any(heavy(command, event.declaration(Path(command.cwd or event.cwd or "/"))) is not None
                          for command in event.call().commands) else "light"


def updated(stdout: HookStdout) -> ShellText | None:
    """The text a hook's answer names at hookSpecificOutput.updatedInput.command, None where it names none."""
    try:
        said: HookAnswer = json.loads(stdout)
    except ValueError:
        return None
    output = said.get("hookSpecificOutput") if isinstance(said, dict) else None
    given = output.get("updatedInput") if isinstance(output, dict) else None
    text = given.get("command") if isinstance(given, dict) else None
    return text if isinstance(text, str) else None


def rewrite(event: Event) -> Rewrite:
    """The text the machine's rewrite hook runs in place of a Bash call's, run as Claude Code runs it."""
    hook = event.machine().rewrite_hook
    if hook is None or event.tool != REWRITTEN:
        return Rewrite(None, None)
    named = shlex.join(hook)
    try:
        done = subprocess.run([*hook], input=json.dumps(event.payload).encode(), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, cwd=event.cwd, env=dict(event.reading.env),
                              timeout=HOOK_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return Rewrite(None, Prose(UNREAD.format(hook=named, fault=f"outran its {HOOK_TIMEOUT:g} s limit")))
    except OSError as fault:
        return Rewrite(None, Prose(UNREAD.format(hook=named, fault=f"cannot start ({fault.strerror or fault})")))
    if done.returncode != 0:
        return Rewrite(None, Prose(UNREAD.format(hook=named, fault=f"exited {done.returncode}")))
    if not done.stdout:
        return Rewrite(None, None)
    text = updated(HookStdout(done.stdout))
    if text is None:
        return Rewrite(None, Prose(UNREAD.format(hook=named, fault=UNANSWERED)))
    return Rewrite(None if text == event.command() else text, None)


def judge(event: Event) -> Verdict | None:
    """Append the call's tier line to its claude process's tier file, and the line of the text the rewrite hook runs
    in its place; a note naming the file where it cannot be written, or the hook where its answer cannot be read."""
    kind = tier(event)
    done = rewrite(event)
    places = state_places(event.places)
    path = tiers.tiers(places, process_key(event.reading.env, places.proc))
    try:
        for text in (event.command(), *(() if done.text is None else (done.text,))):
            tiers.append(path, tiers.line(tiers.digest(text), kind))
    except OSError as fault:
        return context(Prose(UNWRITTEN.format(path=path, fault=fault.strerror or fault)))
    return None if done.note is None else context(done.note)
