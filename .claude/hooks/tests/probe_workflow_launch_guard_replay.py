#!/home/nicolas/.local/bin/python3.14
"""Replay workflow-launch-guard.py on every Workflow launch this machine recorded.

Claim: the guard answers every recorded launch by its rule, refusing (exit 2) or
passing (exit 0), and never with a fault of its own (exit 1) or another status.

Reads every main-thread transcript ~/.claude/projects/*/*.jsonl (a subagent's
lives in a directory below), takes each tool_use block named Workflow of an
assistant line that is no sidechain, and pipes the guard the PreToolUse event
Claude Code would have handed it: the call's input, the line's cwd and session,
CLAUDE_PROJECT_DIR that cwd, each to a fresh process run by its path under the
caller's HOME. The guard reads the files a launch names as they are now, so a
script or briefs file changed or deleted since the launch is judged as it is.

Prints the launches refused, passed and reported, before SINCE and from it,
then per reason the launches refused for it: a reason is a problem line of the
refusal, its labels, paths, quoted texts and numbers written as placeholders,
with one line as the guard printed it.

Usage: probe_workflow_launch_guard_replay.py [GUARD] [SINCE]
  GUARD  the guard to replay (default: ../workflow-launch-guard.py)
  SINCE  the day the rule was ruled, ISO (default: 2026-11-03)
Exit 0 when the claim holds, 1 when a launch got another answer, 2 when the
kill switch is on or no launch was found, so nothing was checked.
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, NamedTuple, NewType

# A reason with its placeholders; a problem line as printed; an ISO timestamp; an
# event as JSON text; the guard's and the probe's exit status; what an answer is called.
Reason = NewType("Reason", str)
Line = NewType("Line", str)
Stamp = NewType("Stamp", str)
EventText = NewType("EventText", str)
GuardStatus = NewType("GuardStatus", int)
ExitStatus = NewType("ExitStatus", int)
Verdict = NewType("Verdict", str)
type Era = Literal["before", "since"]

HOOKS = Path(__file__).resolve().parent.parent
GUARD = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HOOKS / "workflow-launch-guard.py"
SINCE = Stamp(sys.argv[2] if len(sys.argv) > 2 else "2026-11-03")
PROJECTS = Path.home() / ".claude" / "projects"
KILL_SWITCH = Path.home() / ".claude" / "workflow-launch-guard.off"
WORKERS = 8
VERDICTS = {0: Verdict("passed"), 1: Verdict("reported"), 2: Verdict("refused")}
PLACEHOLDERS = (  # applied in order: a quoted text first, so a path or number inside it goes with it
    (re.compile(r"brief `[^`]*`"), "brief `<label>`"),
    (re.compile(r"`[^`]*` follows"), "`<text>` follows"),
    (re.compile(r"(its (?:script|briefs) file) \S+"), r"\1 <path>"),
    (re.compile(r"(no such label in) \S+"), r"\1 <path>"),
    (re.compile(r"(its text in) \S+"), r"\1 <path>"),
    (re.compile(r"[0-9]+"), "<n>"),
)


class Launch(NamedTuple):
    """A recorded Workflow call: where it was recorded, when, and the event the guard reads."""

    transcript: Path
    stamp: Stamp
    event: EventText


class Answer(NamedTuple):
    """The guard's exit status for a launch and the problem lines it printed."""

    launch: Launch
    status: GuardStatus
    problems: list[Line]


def launches() -> list[Launch]:
    """Every Workflow tool_use of a main-thread transcript, in file and line order."""
    found: list[Launch] = []
    for transcript in sorted(PROJECTS.glob("*/*.jsonl")):
        with transcript.open(encoding="utf-8", errors="replace") as handle:
            for text in handle:
                if '"Workflow"' not in text:
                    continue
                try:
                    record = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "assistant" or record.get("isSidechain"):
                    continue
                content = (record.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Workflow":
                        event = {"hook_event_name": "PreToolUse", "tool_name": "Workflow",
                                 "tool_input": block.get("input"), "cwd": record.get("cwd"),
                                 "session_id": record.get("sessionId"), "tool_use_id": block.get("id")}
                        found.append(Launch(transcript, Stamp(str(record.get("timestamp", ""))),
                                            EventText(json.dumps(event))))
    return found


def judged(launch: Launch) -> Answer:
    """Pipe ``launch``'s event to the guard."""
    cwd = json.loads(launch.event).get("cwd")
    env = {**os.environ, **({"CLAUDE_PROJECT_DIR": cwd} if isinstance(cwd, str) else {})}
    done = subprocess.run([str(GUARD)], input=launch.event, capture_output=True, text=True, check=False, env=env)
    problems = [Line(line[4:]) for line in done.stderr.splitlines() if line.startswith("  * ")]
    if done.returncode != 2:
        problems = [Line(line) for line in done.stderr.strip().splitlines()[-1:]]
    return Answer(launch, GuardStatus(done.returncode), problems)


def reason(line: Line) -> Reason:
    """``line`` with its labels, paths, quoted texts and numbers as placeholders."""
    text = str(line)
    for pattern, placeholder in PLACEHOLDERS:
        text = pattern.sub(placeholder, text)
    return Reason(text)


def main() -> ExitStatus:
    """Replay every launch and print the census."""
    if KILL_SWITCH.exists():
        print(f"the kill switch {KILL_SWITCH} is on: the guard refuses nothing, so nothing is checked")
        return ExitStatus(2)
    recorded = launches()
    if not recorded:
        print(f"no Workflow launch recorded under {PROJECTS}")
        return ExitStatus(2)
    with ThreadPoolExecutor(WORKERS) as pool:
        answers = list(pool.map(judged, recorded))
    tally: Counter[tuple[Era, Verdict]] = Counter()
    for answer in answers:
        era: Era = "before" if answer.launch.stamp < SINCE else "since"
        tally[era, VERDICTS.get(answer.status, Verdict(f"exit {answer.status}"))] += 1
    transcripts = len({answer.launch.transcript for answer in answers})
    print(f"{len(answers)} Workflow launches in {transcripts} main-thread transcripts, replayed on {GUARD}")
    eras: tuple[Era, Era] = ("before", "since")
    for era in eras:
        row = ", ".join(f"{tally[era, name]} {name}" for name in VERDICTS.values())
        print(f"  {era} {SINCE}: {sum(n for (e, _), n in tally.items() if e == era)} launches: {row}")
    reasons: Counter[Reason] = Counter()
    example: dict[Reason, Line] = {}
    for answer in answers:
        if answer.status == 2:
            for found, line in {reason(line): line for line in answer.problems}.items():
                reasons[found] += 1
                example.setdefault(found, line)
    print("refused launches per reason (a launch refused for several reasons counts under each):")
    for found, count in reasons.most_common():
        print(f"  {count:4} {found}")
        print(f"       e.g. {example[found][:200]}")
    odd = [answer for answer in answers if answer.status not in {0, 2}]
    for answer in odd:
        print(f"ANSWER {answer.status} {answer.launch.transcript.name} {answer.launch.stamp}: "
              f"{' | '.join(answer.problems)[:300]}")
    return ExitStatus(1 if odd else 0)


if __name__ == "__main__":
    sys.exit(main())
