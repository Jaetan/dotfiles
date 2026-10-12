#!/home/nicolas/.local/bin/python3.14
"""Tally of what falls through claude-call: the collector task's counts, and the evidence for deciding which guard
rules the runtime now holds.

usage: tally_claude_call.py [SINCE]   (an ISO time; default the install, 2026-09-28T18:31:00+09:00)

Reads the wrapper's record (~/dev/logs/claude-call-events.log, one line per fall-through) and every session transcript
under ~/.claude/projects changed since SINCE, streamed line by line: each Bash and Monitor call, and each guard-hook
refusal of one, sorted by the rule its message cites. Prints the calls for scale, the events per kind and the
refusals per rule, each with its latest example. Reads only; exits 0.

An event kind is counted with the sessions it came from: a condition of the whole session (no user manager, so
no scope) repeats on every call and reads as one session, not as a flood. A refusal is an error result naming a
hook and BLOCKED; the notes heavy-run-guard adds after a call ("heavy-run-guard (rule 4): these files moved ...")
are context, not refusals, and are not counted. Each rule is marked with what holds it once the wrap runs:
"wrap" where the runtime now holds the property, "kept" where the user's ruling keeps the hook's rule.
"""
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

SINCE = datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-09-28T18:31:00+09:00")
EVENTS = Path.home() / "dev" / "logs" / "claude-call-events.log"
TRANSCRIPTS = Path.home() / ".claude" / "projects"
# The tag each refusal message carries, in the words of heavy-run-guard.py and no-polling-loops.py.
RULES = (("rule 1: whole output to a file [wrap: the call's log]", "(rule 1"),
         ("rule 2: no head [kept]", "(rule 2"),
         ("rule 3: CPUs 0-19 [wrap: claude.slice]", "(rule 3"),
         ("alone in its call [wrap: the call's log]", "shares its call"),
         ("rule 4: no edit during a heavy run [wrap: the view, inside a repository]", "(rule 4"),
         ("polling loop [kept]", "a shell polling loop"),
         ("process by pattern [kept]", "looks for a process by PATTERN"),
         ("pidwait on itself [kept]", "pidwait -f"),
         ("job left running [wrap: the scope's stop]", "nothing in this call stops it"))


def when(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def events() -> tuple[Counter[str], dict[str, str], dict[str, set[str]]]:
    counts: Counter[str] = Counter()
    latest: dict[str, str] = {}
    sessions: dict[str, set[str]] = {}
    for line in EVENTS.read_text(encoding="utf-8").splitlines() if EVENTS.exists() else ():
        parts = line.split(" ", 3)
        stamp = when(parts[0]) if parts else None
        if len(parts) < 4 or stamp is None or stamp.astimezone(SINCE.tzinfo) < SINCE:
            continue
        counts[parts[2]] += 1
        latest[parts[2]] = line
        sessions.setdefault(parts[2], set()).add(parts[1])
    return counts, latest, sessions


def transcripts() -> tuple[Counter[str], Counter[str], dict[str, str], int]:
    calls: Counter[str] = Counter()
    refusals: Counter[str] = Counter()
    latest: dict[str, str] = {}
    files = 0
    for path in TRANSCRIPTS.rglob("*.jsonl"):
        if datetime.fromtimestamp(path.stat().st_mtime, SINCE.tzinfo) < SINCE:
            continue
        files += 1
        commands: dict[str, str] = {}
        with path.open(encoding="utf-8", errors="replace") as f:
            for raw in f:
                if '"tool_use"' not in raw and '"tool_result"' not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                stamp = when(entry.get("timestamp", ""))
                if stamp is None or stamp < SINCE:
                    continue
                content = entry.get("message", {}).get("content", [])
                for block in content if isinstance(content, list) else ():
                    if block.get("type") == "tool_use" and block.get("name") in ("Bash", "Monitor"):
                        calls[block["name"]] += 1
                        commands[block.get("id", "")] = str(block.get("input", {}).get("command", ""))
                    elif block.get("type") == "tool_result" and block.get("is_error"):
                        body = block.get("content")
                        text = body if isinstance(body, str) else json.dumps(body)
                        if "hook error" not in text or "BLOCKED" not in text:
                            continue
                        tags = [name for name, tag in RULES if tag in text] or ["unclassified"]
                        for name in tags:
                            refusals[name] += 1
                            local = stamp.astimezone(SINCE.tzinfo)
                            command = commands.get(block.get("tool_use_id", ""), "?").replace("\n", " ")
                            latest[name] = f"{local:%Y-%m-%d %H:%M} {command}"
    return calls, refusals, latest, files


counts, latest_events, sessions = events()
calls, refusals, latest_refusals, files = transcripts()
print(f"since {SINCE.isoformat()}")
print(f"calls: {calls['Bash']} Bash, {calls['Monitor']} Monitor, in {files} transcripts")
print(f"wrapper events: {sum(counts.values())}")
for kind, n in counts.most_common():
    print(f"  {n:5}  {kind} ({len(sessions[kind])} sessions)   latest: {latest_events[kind][:150]}")
print(f"guard refusals: {sum(refusals.values())} rule citations")
for name, n in refusals.most_common():
    print(f"  {n:5}  {name}   latest: {latest_refusals[name][:150]}")
