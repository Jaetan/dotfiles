#!/home/nicolas/.local/bin/python3.14
"""Fire workflow-watch-guard.py through Claude Code itself, beside the kit's other hooks.

usage: probe_workflow_watch_guard_live.py [MODEL]   (default claude-sonnet-5-5; two short headless sessions)

Claim: registered as next/add-workflow-watch-guard.py registers it, beside every hook the
user's settings.json runs, the guard (noted) puts the Monitor command before the model at
the launch, and (refused) refuses the main thread's first Bash call with that command;
the model can pay the debt (ToolSearch, then a Monitor of exactly the named command), and
the refused call then goes through. The suite pipes events it builds; this is the one
check that the harness hands the guard those events and acts on its answers.

It runs `claude -p` in an empty directory with `--setting-sources project` (which finds no
settings there) and `--settings` naming a copy of ~/dotfiles/.claude/settings.json with
the guard registered by the add script (which leaves a settings file that already
registers it as it is). The workflow's one agent sleeps, so the run is live
when the model calls Bash. Two sessions: in the first the session's transcript must hold
the launch note (a hook_success attachment of the Workflow's PostToolUse; the stream does
not echo it) and the model's next call past ToolSearch must be a Monitor;
in the second, told to call Bash first, a Bash result must carry the refusal and the
command it names, a Monitor of that command must come after it, and the last Bash must go
through. The answer rests on a model following a prompt, so a failure is read from the
printed calls before it is called a defect of the guard. The sessions' own project
directory under ~/.claude/projects is removed afterwards.
Exit 0 when both sessions show it, 1 when one does not, 2 when a session did not run.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NewType, TypedDict, cast

# The probe's exit status; a model's name; a shell command line; prose; a tool's name.
ExitStatus = NewType("ExitStatus", int)
Model = NewType("Model", str)
CommandLine = NewType("CommandLine", str)
Prose = NewType("Prose", str)
ToolName = NewType("ToolName", str)
HOLDS, BROKEN, UNCHECKED = ExitStatus(0), ExitStatus(1), ExitStatus(2)

HOOKS = Path(__file__).resolve().parent.parent
MODEL = Model(sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5-5")
SETTINGS = Path.home() / "dotfiles" / ".claude" / "settings.json"
ADD = HOOKS / "next" / "add-workflow-watch-guard.py"
PROJECTS = Path.home() / ".claude" / "projects"
REFUSAL = "workflow-watch-guard: Bash is refused"
NOTE = "workflow-watch-guard: before any other call"
SCRIPT = ("export const meta = { name: 'probe-live-guard', description: 'probe: one agent that sleeps' }\\n"
          "await agent('Run the Bash command sleep 60 once, then reply with the word ok.', {model: 'haiku'})\\n"
          "return 1")
TASK = f"""This is a test of a hook; the user asks you to run a workflow. Make these tool calls, one at a time:
1. Workflow, with the script "{SCRIPT}" (each \\n is a newline). It spawns one agent.
2. Bash, with the command "ls".
When "ls" has run, stop with TaskStop every background task you started, then reply with the word done."""
NOTED = Prose(TASK + "\nIf a hook asks for something, do it first.")
REFUSED = Prose(TASK + "\nYour first call after the Workflow must be the Bash call, whatever any note says. If a "
                       "hook refuses a call, do exactly what its message says, then make the refused call again.")


class ToolInput(TypedDict, total=False):
    command: CommandLine


class TextPart(TypedDict, total=False):
    type: Literal["text"]
    text: Prose


class Block(TypedDict, total=False):
    type: Literal["tool_use", "tool_result", "text", "thinking"]
    id: Prose
    name: ToolName
    input: ToolInput
    tool_use_id: Prose
    content: Prose | list[TextPart]
    is_error: bool


class Message(TypedDict, total=False):
    content: list[Block] | Prose


class Entry(TypedDict, total=False):
    type: Literal["assistant", "user", "system", "result"]
    session_id: Prose
    message: Message


def text_of(block: Block) -> Prose:
    content = block.get("content")
    if isinstance(content, list):
        return Prose("".join(part.get("text", "") for part in content if isinstance(part, dict)))
    return Prose(content or "")


class Seen(TypedDict):
    """What one session showed: its calls and results in order, the launch note, the command a refusal named."""

    steps: list[Prose]
    noted: bool
    named: CommandLine
    monitors: list[CommandLine]


def session(settings: Path, cwd: Path, prompt: Prose) -> Seen | None:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_CODE_") and k not in ("CLAUDECODE", "CLAUDE_CALL", "LD_PRELOAD")}
    argv = ["claude", "-p", prompt, "--model", MODEL, "--setting-sources", "project", "--settings", str(settings),
            "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions"]
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    lines = done.stdout.splitlines()
    print(f"claude -p exit {done.returncode}, {len(lines)} stream lines; stderr tail: {done.stderr.strip()[-200:]!r}")
    if not lines:
        return None
    seen = Seen(steps=[], noted=False, named=CommandLine(""), monitors=[])
    uses: dict[Prose, Block] = {}
    for line in lines:
        try:
            entry = cast("Entry", json.loads(line))
        except ValueError:
            continue
        session_id = entry.get("session_id")
        if entry.get("type") == "system" and isinstance(session_id, str):
            for transcript in PROJECTS.glob(f"*/{session_id}.jsonl"):
                seen["noted"] = any('"PostToolUse:Workflow"' in held and NOTE in held and "command: " in held
                                    for held in transcript.read_text(encoding="utf-8").splitlines())
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                uses[block.get("id", Prose(""))] = block
                command = block.get("input", ToolInput()).get("command", CommandLine(""))
                if block.get("name") == "Monitor":
                    seen["monitors"].append(command)
                seen["steps"].append(Prose(f"call {block.get('name')}: {command[:160]}"))
            elif block.get("type") == "tool_result":
                use = uses.get(block.get("tool_use_id", Prose("")), Block())
                text = text_of(block)
                seen["steps"].append(Prose(f"  result of {use.get('name')} error={block.get('is_error', False)}: "
                                           f"{text[:160]!r}"))
                if use.get("name") == "Bash" and REFUSAL in text and not seen["named"]:
                    seen["named"] = CommandLine(next((part.split("command: ", 1)[1] for part in text.splitlines()
                                                      if "command: " in part), ""))
    for step in seen["steps"]:
        print(step)
    return seen


def main() -> ExitStatus:
    work = Path(tempfile.mkdtemp(prefix="workflow-watch-guard-live-", dir=os.environ.get("TMPDIR")))
    try:
        settings = work / "settings.json"
        shutil.copy(SETTINGS, settings)
        subprocess.run([sys.executable, str(ADD), str(settings)], check=True, capture_output=True)
        cwd = work / "cwd"
        cwd.mkdir()
        print("=== noted")
        noted = session(settings, cwd, NOTED)
        print("=== refused")
        refused = session(settings, cwd, REFUSED)
        if noted is None or refused is None:
            return UNCHECKED
        after = [step for step in noted["steps"][1:] if step.startswith("call ") and "ToolSearch" not in step]
        noted_ok = noted["noted"] and bool(after) and after[0].startswith("call Monitor")
        last_bash = [step for step in refused["steps"] if step.startswith("  result of Bash")]
        first_bash = next((at for at, step in enumerate(refused["steps"]) if step.startswith("  result of Bash")),
                          len(refused["steps"]))
        refusal_at = first_bash if REFUSAL in "".join(refused["steps"][first_bash:first_bash + 1]) \
            else len(refused["steps"])
        watched_after = any(step == f"call Monitor: {refused['named'][:160]}" for step in refused["steps"][refusal_at:])
        refused_ok = bool(refused["named"]) and watched_after and bool(last_bash) \
            and REFUSAL not in last_bash[-1] and "error=False" in last_bash[-1]
        print(f"noted: the launch note reached the model {noted['noted']}, its next calls were the watch "
              f"{noted_ok}; refused: the refusal named {refused['named']!r}, a Monitor of it after the refusal "
              f"{watched_after}, all of it {refused_ok}")
        return HOLDS if noted_ok and refused_ok else BROKEN
    finally:
        shutil.rmtree(PROJECTS / re.sub(r"[^A-Za-z0-9]", "-", str(work / "cwd")), ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
