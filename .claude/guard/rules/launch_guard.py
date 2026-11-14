"""Every Workflow launch passes the old launch guard, run as a child process: the guard's verdict is the kit's.

THE RULE (user, 2026-11-07: the old kit's sound parts are wrapped as rules). A PreToolUse event of the Workflow tool,
the main thread's or an agent's, runs LAUNCH_GUARD under INTERPRETER, the event's JSON on its stdin, in the event's
cwd and the environment the dispatcher was given; its stdout goes nowhere. Its exit 0 passes the launch, its exit 2
refuses it with the guard's own text from stderr. Any other exit, a run past LAUNCH_TIMEOUT or a guard that cannot
start is a KitFault naming the guard and what it said, so the launch is refused where the old kit let it through. The
guard runs as a child because tree_sitter, which it imports, does not import in the dispatcher's isolated interpreter.
A guard file that is missing is refused in the interpreter's words, which exit 2 naming it.

EXCEPTIONS (2), each with the failure it holds:
  * an event naming no cwd runs the guard in the kit's own folder: a guard given no folder runs in the dispatcher's,
    wherever Claude Code started the hook, and reads a relative script path against it.
  * the guard's text is cut at OUTPUT_LIMIT bytes with a line saying so: a guard printing without end fills the
    refusal.
"""

import json
import subprocess
from pathlib import Path

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.gate_run import OUTPUT_LIMIT, ExitCode
from dispatcher.tree import Seconds
from dispatcher.verdict import KitFault, Prose, Verdict, refuse

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Workflow")})
INTERPRETER = Path("/home/nicolas/.local/bin/python3.14")
LAUNCH_GUARD = Path("/home/nicolas/dotfiles/.claude/hooks/workflow-launch-guard.py")
LAUNCH_TIMEOUT = Seconds(6.0)
KIT = Path(__file__).resolve().parents[1]


def judge(event: Event) -> Verdict | None:
    """Pass the launch on the guard's exit 0, refuse it with the guard's text on its exit 2; a KitFault otherwise."""
    named = f"{INTERPRETER} {LAUNCH_GUARD}"
    try:
        done = subprocess.run([INTERPRETER, LAUNCH_GUARD], input=json.dumps(event.payload).encode(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=event.cwd or KIT,
                              env=dict(event.reading.env), timeout=LAUNCH_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise KitFault(Prose(f"the launch guard {named} outran its {LAUNCH_TIMEOUT:g} s limit")) from None
    except OSError as fault:
        raise KitFault(Prose(f"the launch guard {named} cannot start: {fault.strerror}")) from None
    text = done.stderr[:OUTPUT_LIMIT].decode("utf-8", "replace").rstrip("\n")
    if len(done.stderr) > OUTPUT_LIMIT:
        text += f"\n(the launch guard's text is cut here, at {OUTPUT_LIMIT >> 10} KiB)"
    code = ExitCode(done.returncode)
    if code == 0:
        return None
    if code == 2:
        return refuse(Prose(text))
    raise KitFault(Prose(f"the launch guard {named} exited {code}: {text.strip() or 'silent'}"))
