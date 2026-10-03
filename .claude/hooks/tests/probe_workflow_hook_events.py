#!/home/nicolas/.local/bin/python3.14
"""Record the hook events and transcript lines a workflow launch, its watch and its stop produce.

usage: probe_workflow_hook_events.py [MODEL]   (default claude-sonnet-5-5; two short headless sessions)

workflow-watch-guard.py rests on shapes the hooks reference leaves unsaid: what PostToolUse's
tool_response holds for Workflow, Monitor and TaskStop; whether a TaskStop of a task that already
ended reaches a hook at all; what the Stop event's background_tasks lists while a run and its watch
are live; whether a matcher of "*" reaches every tool; whether a workflow agent's events carry
agent_id; and how the transcript records a workflow's end. Each session runs `claude -p` in an empty
directory with only this probe's settings (`--setting-sources project` finds none there, `--settings`
adds a hook that logs every event it sees, under the matcher "*"):
  stops     launch a workflow whose one agent makes one Bash call, watch it, stop the run (by then
            ended), stop the watch, end the turn
  inflight  launch a workflow whose one agent sleeps, watch it, end the turn with both live
Each event is printed, then every task-notification line and tool result of the session's transcript;
the sessions' project directory under ~/.claude/projects is removed afterwards.
Exit 0 when both sessions ran and logged a Workflow PostToolUse; the shapes are printed, not asserted,
since they are what is being learnt.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NewType

# The probe's exit status; a session's label; a prompt or a workflow script, as text.
ExitStatus = NewType("ExitStatus", int)
Label = NewType("Label", str)
Text = NewType("Text", str)

MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5-5"
WORK = Path(tempfile.mkdtemp(prefix="workflow-hook-events-", dir=os.environ.get("TMPDIR")))
HOOK = WORK / "hook.py"
EVENTS = WORK / "events.jsonl"
WATCHER = Path.home() / ".local" / "bin" / "workflow-token-watch"
EVENT_NAMES = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionStart")
SHOWN = ("hook_event_name", "tool_name", "tool_input", "tool_response", "error", "agent_id", "agent_type", "source",
         "background_tasks", "stop_hook_active")

HOOK.write_text(f'''import json, sys
e = json.load(sys.stdin)
with open({str(EVENTS)!r}, "a") as f:
    f.write(json.dumps(e) + "\\n")
''')
SETTINGS = WORK / "settings.json"
SETTINGS.write_text(json.dumps({"hooks": {name: [{"matcher": "*", "hooks": [
    {"type": "command", "command": f"{sys.executable} {HOOK}"}]}] for name in EVENT_NAMES}}))
CWD = WORK / "cwd"
CWD.mkdir()


def script(name: Text, command: Text) -> Text:
    return Text(f"export const meta = {{ name: '{name}', description: 'probe: one agent, one Bash call' }}\\n"
                f"await agent('Run the Bash command {command} once, then reply with the word ok.', "
                "{model: 'haiku'})\\nreturn 1")


def prompt(workflow: Text, then: Text) -> Text:
    return Text("This is a test of tool events; the user asks you to run a workflow. Make exactly these tool "
                "calls, one at a time, in order, and nothing else:\n"
                f'1. Workflow, with the script "{workflow}" (each \\n is a newline). It spawns one agent.\n'
                f'2. Monitor, with command "{WATCHER} <the transcript dir the Workflow result names> 2000000 1", '
                f'description "probe watch", timeout_ms 60000.\n{then}')


STOPS = prompt(script(Text("probe-stops"), Text("true")),
               Text("3. TaskStop, with task_id the workflow's task id.\n"
                    "4. TaskStop, with task_id the Monitor's task id.\nThen reply with the word done."))
INFLIGHT = prompt(script(Text("probe-inflight"), Text("sleep 40")),
                  Text("Then, without waiting and without stopping anything, reply with the word done."))


def session(label: Label, text: Text) -> bool:
    """Run one headless session; print its events and transcript; say whether a launch was logged."""
    EVENTS.unlink(missing_ok=True)
    argv = ["claude", "-p", text, "--model", MODEL, "--setting-sources", "project", "--settings", str(SETTINGS),
            "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions"]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_CODE_") and k not in ("CLAUDECODE", "CLAUDE_CALL", "LD_PRELOAD", "ASAN_OPTIONS")}
    done = subprocess.run(argv, cwd=CWD, env=env, capture_output=True, text=True, timeout=300, check=False)
    print(f"=== {label}: claude -p exit {done.returncode}; stderr tail: {done.stderr.strip()[-300:]!r}")
    events = [json.loads(line) for line in EVENTS.read_text().splitlines()] if EVENTS.exists() else []
    transcript = None
    for event in events:
        transcript = transcript or event.get("transcript_path")
        print("EVENT", json.dumps({k: event[k] for k in SHOWN if k in event})[:1500])
    if transcript and Path(transcript).is_file():
        print(f"TRANSCRIPT {transcript}")
        for line in Path(transcript).read_text(encoding="utf-8").splitlines():
            if "<task-notification>" in line:
                entry = json.loads(line)
                print("NOTICE", entry.get("type"), entry.get("operation"), json.dumps(entry.get("content"))[:700])
            elif '"toolUseResult"' in line:
                print("RESULT", json.dumps(json.loads(line).get("toolUseResult"))[:700])
    return any(e.get("hook_event_name") == "PostToolUse" and e.get("tool_name") == "Workflow" for e in events)


def main() -> ExitStatus:
    ran = [session(Label("stops"), STOPS), session(Label("inflight"), INFLIGHT)]
    return ExitStatus(0 if all(ran) else 1)


try:
    sys.exit(main())
finally:
    shutil.rmtree(Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(CWD)), ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
