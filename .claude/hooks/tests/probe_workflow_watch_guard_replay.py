#!/usr/bin/env python3
"""Replay workflow-watch-guard.py on the session that motivated it.

Claim: on the transcript of session f5143862 (2026-10-03), where two workflow runs were
launched on one transcript directory and neither was watched, the guard refuses the
main thread's first event after each launch; the debt of the first run (wob5mlbjl)
clears at its TaskStop, the debt of the second (wkvfq3rqa) at its completion notice,
and nothing is refused outside those two spans.

The replay feeds the guard the events Claude Code would have handed it, in transcript
order, each to a fresh process that runs the guard by its path: a PreToolUse per tool call of the main thread, a
PostToolUse per Workflow, Monitor or TaskStop result the transcript records as a
success, and a Stop at each turn's end (with no background_tasks list, which the
transcript does not keep). The transcript the guard reads grows line by line as it
did. The answers are what the guard would have said; the session ran without it.

Usage: probe_workflow_watch_guard_replay.py [GUARD] [TRANSCRIPT]
  GUARD       the guard to replay (default: ../workflow-watch-guard.py)
  TRANSCRIPT  the session's transcript (default: the f5143862 one)
The guard reads the caller's HOME, as it does live: while its kill switch exists it refuses nothing,
and while no watch can be demanded there it stands down, so the replay is not run in the first case
and its answers are not judged in the second.
Exit 0 when the claim holds, 1 when it does not, 2 when the transcript is missing, the kill switch is
on or the guard stood down, so nothing could be checked.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, cast

# A background task's id; a tool's name; a transcript timestamp; a tool-use id; a
# session's id; prose the probe prints; seconds since the epoch; milliseconds; the
# guard's and the probe's exit status.
TaskId = NewType("TaskId", str)
ToolName = NewType("ToolName", str)
Stamp = NewType("Stamp", str)
ToolUseId = NewType("ToolUseId", str)
SessionId = NewType("SessionId", str)
Prose = NewType("Prose", str)
Instant = NewType("Instant", float)
Millis = NewType("Millis", int)
ExitStatus = NewType("ExitStatus", int)
HOLDS, BROKEN, UNCHECKED = ExitStatus(0), ExitStatus(1), ExitStatus(2)
ALLOW, REFUSE = ExitStatus(0), ExitStatus(2)

type HookEvent = Literal["PreToolUse", "PostToolUse", "Stop"]

HOOKS = Path(__file__).resolve().parent.parent
GUARD = Path(sys.argv[1]) if len(sys.argv) > 1 else HOOKS / "workflow-watch-guard.py"
TRANSCRIPT = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    Path.home() / ".claude" / "projects" / "-home-nicolas-dev-agda-aletheia"
    / "f5143862-c30c-4043-9726-809d6e2d9157.jsonl")
FIRST, SECOND = TaskId("wob5mlbjl"), TaskId("wkvfq3rqa")
KILL_SWITCH = Path.home() / ".claude" / "workflow-watch-guard.off"
RECORDED = frozenset({"Workflow", "Monitor", "TaskStop"})


class ToolInput(TypedDict, total=False):
    """A tool call's input, passed to the guard as the transcript holds it."""

    command: Prose
    description: Prose
    timeout_ms: Millis
    task_id: TaskId
    script: Prose
    file_path: Prose


class ToolResponse(TypedDict, total=False):
    """A tool's structured result, as the transcript keeps it."""

    status: Prose
    taskId: TaskId
    transcriptDir: Prose
    timeoutMs: Millis
    persistent: bool
    task_id: TaskId
    message: Prose


class Block(TypedDict, total=False):
    type: Literal["tool_use", "tool_result", "text", "thinking"]
    id: ToolUseId
    name: ToolName
    input: ToolInput
    tool_use_id: ToolUseId


class Message(TypedDict, total=False):
    content: list[Block] | Prose
    stop_reason: Literal["tool_use", "end_turn"]


class Entry(TypedDict, total=False):
    type: Literal["assistant", "user", "queue-operation", "attachment", "system"]
    isSidechain: bool
    timestamp: Stamp
    sessionId: SessionId
    message: Message
    toolUseResult: ToolResponse | Prose


class HookInput(TypedDict, total=False):
    hook_event_name: HookEvent
    session_id: SessionId
    transcript_path: Prose
    tool_name: ToolName
    tool_input: ToolInput
    tool_response: ToolResponse
    stop_hook_active: bool


class Call(NamedTuple):
    name: ToolName
    given: ToolInput


class Answer(NamedTuple):
    """One event the guard answered: when, which, and what it said."""

    at: Instant
    event: HookEvent
    tool: ToolName
    status: ExitStatus
    lead: Prose


def instant(stamp: Stamp) -> Instant:
    return Instant(datetime.fromisoformat(stamp).timestamp())


def ask(event: HookInput, tmp: Path) -> tuple[ExitStatus, Prose]:
    done = subprocess.run([str(GUARD)], input=json.dumps(event), capture_output=True, text=True, check=False,
                          env={**os.environ, "TMPDIR": str(tmp)})
    lines = (done.stderr or done.stdout).strip().splitlines()
    return ExitStatus(done.returncode), Prose(lines[1].strip() if len(lines) > 1 else (lines[0] if lines else ""))


def replay(work: Path) -> list[Answer]:
    copy = work / "transcript.jsonl"
    tmp = work / "tmp"
    tmp.mkdir()
    calls: dict[ToolUseId, Call] = {}
    answers: list[Answer] = []
    with TRANSCRIPT.open(encoding="utf-8") as source, copy.open("w", encoding="utf-8") as grown:
        for line in source:
            grown.write(line)
            grown.flush()
            entry = cast("Entry", json.loads(line))
            message, stamp, session = entry.get("message"), entry.get("timestamp"), entry.get("sessionId")
            if entry.get("isSidechain") or not isinstance(message, dict) or not stamp or not session:
                continue
            base = HookInput(session_id=session, transcript_path=Prose(str(copy)))
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            if entry.get("type") == "assistant":
                for block in blocks:
                    if block.get("type") == "tool_use":
                        call = Call(block.get("name", ToolName("")), block.get("input", ToolInput()))
                        calls[block.get("id", ToolUseId(""))] = call
                        status, lead = ask(HookInput(**base, hook_event_name="PreToolUse", tool_name=call.name,
                                                     tool_input=call.given), tmp)
                        answers.append(Answer(instant(stamp), "PreToolUse", call.name, status, lead))
                if message.get("stop_reason") == "end_turn":
                    status, lead = ask(HookInput(**base, hook_event_name="Stop", stop_hook_active=False), tmp)
                    answers.append(Answer(instant(stamp), "Stop", ToolName(""), status, lead))
            elif entry.get("type") == "user":
                response = entry.get("toolUseResult")
                for block in blocks:
                    call = calls.get(block.get("tool_use_id", ToolUseId("")))
                    if block.get("type") == "tool_result" and call and call.name in RECORDED \
                            and isinstance(response, dict):
                        status, lead = ask(HookInput(**base, hook_event_name="PostToolUse", tool_name=call.name,
                                                     tool_input=call.given, tool_response=response), tmp)
                        answers.append(Answer(instant(stamp), "PostToolUse", call.name, status, lead))
    return answers


def moments() -> dict[Prose, Instant]:
    """When each run was launched and ended, from the transcript."""
    found: dict[Prose, Instant] = {}
    for line in TRANSCRIPT.open(encoding="utf-8"):
        entry = cast("Entry", json.loads(line))
        stamp, response = entry.get("timestamp"), entry.get("toolUseResult")
        if not stamp:
            continue
        if isinstance(response, dict) and response.get("status") == "async_launched":
            found.setdefault(Prose(f"launch {response.get('taskId')}"), instant(stamp))
        if isinstance(response, dict) and response.get("task_id") == FIRST \
                and "Successfully stopped" in response.get("message", ""):
            found.setdefault(Prose(f"end {FIRST}"), instant(stamp))
        if entry.get("type") == "queue-operation" and f"<task-id>{SECOND}</task-id>" in line \
                and "<status>completed</status>" in line:
            found.setdefault(Prose(f"end {SECOND}"), instant(stamp))
    return found


def judged(answers: list[Answer], when: dict[Prose, Instant]) -> list[Prose]:
    """What the replay got wrong against the claim."""
    keys = [Prose(f"{what} {task}") for task in (FIRST, SECOND) for what in ("launch", "end")]
    missing = [key for key in keys if key not in when]
    if missing:
        return [Prose(f"the transcript holds no {key}: not the recorded session") for key in missing]
    spans = [(when[keys[0]], when[keys[1]]), (when[keys[2]], when[keys[3]])]
    checked = [each for each in answers if each.event != "PostToolUse"]
    bad: list[Prose] = []
    for start, _ in spans:
        first = next((each for each in checked if each.at > start), None)
        if first is None or first.status != REFUSE:
            bad.append(Prose(f"the first event after the launch at {start} was not refused: {first}"))
    outside = [each for each in checked if each.status == REFUSE
               and not any(start < each.at <= end for start, end in spans)]
    if outside:
        bad.append(Prose(f"{len(outside)} refusal(s) outside a run's span, first {outside[0]}"))
    errors = [each for each in answers if each.status not in (ALLOW, REFUSE)]
    if errors:
        bad.append(Prose(f"{len(errors)} error(s) of the guard's own, first {errors[0]}"))
    return bad


def main() -> ExitStatus:
    if not TRANSCRIPT.is_file():
        print(f"nothing to replay: {TRANSCRIPT} is missing")
        return UNCHECKED
    if KILL_SWITCH.exists():
        print(f"nothing to replay: the guard's kill switch {KILL_SWITCH} is on")
        return UNCHECKED
    work = Path(tempfile.mkdtemp(prefix="workflow-watch-replay-"))
    try:
        answers = replay(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    stood_down = [each for each in answers if "no watch can be demanded" in each.lead]
    if stood_down:
        print(f"nothing judged: the guard stood down ({stood_down[0].lead[:160]})")
        return UNCHECKED
    refused = [each for each in answers if each.status == REFUSE]
    for each in refused:
        print(f"refused {datetime.fromtimestamp(each.at).isoformat(timespec='seconds')} {each.event} {each.tool}: "
              f"{each.lead[:120]}")
    bad = judged(answers, moments())
    if bad:
        print("the replay does not hold:")
        for line in bad:
            print(f"  {line}")
        return BROKEN
    print(f"PASS: {len(answers)} events replayed, {len(refused)} refused, each inside a run's span")
    return HOLDS


if __name__ == "__main__":
    sys.exit(main())
