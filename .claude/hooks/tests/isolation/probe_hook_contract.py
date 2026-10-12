#!/home/nicolas/.local/bin/python3.14
"""Probes of the Claude Code hook contract the per-call wrapper rests on, which the hooks reference leaves unsaid.

usage: probe_hook_contract.py [MODEL]   (default claude-haiku-4-5-20251001; each case is one short headless session)

Each case runs `claude -p` in an empty directory with only this probe's settings (`--setting-sources project` finds
none there, `--settings` adds the probe hook) and no saved session. The probe hook logs every event it sees; on a
PreToolUse whose command holds ORIGINAL it answers `updatedInput` with ORIGINAL replaced by REWRITTEN, and no
`permissionDecision`; a command outside the expected set is denied. Questions:
  rewrite     is `updatedInput` applied without a `permissionDecision`?
  rules-orig  do permission rules match the original text (allowed: `Bash(touch ORIGINAL.txt)` only)?
  rules-new   ... or the rewritten text (allowed: `Bash(touch REWRITTEN.txt)` only)?
  no-grant    with Bash not allowed at all, does the rewrite grant the call (it must not)?
  (`echo` is approved as read-only whatever the rules say, so the rule cases run `touch`, which needs approval.)
  post        does PostToolUse see the original or the rewritten input?
  background  is a `run_in_background` call rewritten?
  eof         does a foreground call end at its shell's exit, or when a job holding its output exits?
Exit 0 when every case ran; the answers are printed, not asserted, since they are what is being learnt.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-4-5-20251001"
WORK = Path(tempfile.mkdtemp(prefix="hook-contract-", dir=os.environ.get("TMPDIR")))
HOOK = WORK / "hook.py"
EVENTS = WORK / "events.jsonl"
EXPECTED = ("echo ORIGINAL", "echo REWRITTEN", "touch ORIGINAL.txt", "touch REWRITTEN.txt", "(sleep 6 &); echo ORIGINAL-eof", "(sleep 6 &); echo REWRITTEN-eof")

HOOK.write_text(f'''import json, sys, time
e = json.load(sys.stdin)
e["_at"] = time.time()
with open({str(EVENTS)!r}, "a") as f:
    f.write(json.dumps(e) + "\\n")
if e.get("hook_event_name") != "PreToolUse" or e.get("tool_name") != "Bash":
    sys.exit(0)
cmd = e["tool_input"].get("command", "")
if not any(cmd.startswith(x) for x in {EXPECTED!r}):
    print(json.dumps({{"hookSpecificOutput": {{"hookEventName": "PreToolUse", "permissionDecision": "deny",
                      "permissionDecisionReason": "probe: unexpected command"}}}}))
    sys.exit(0)
if "ORIGINAL" in cmd:
    new = dict(e["tool_input"], command=cmd.replace("ORIGINAL", "REWRITTEN"))
    print(json.dumps({{"hookSpecificOutput": {{"hookEventName": "PreToolUse", "updatedInput": new}}}}))
''')
hook_cmd = f"{sys.executable} {HOOK}"
SETTINGS = WORK / "settings.json"
SETTINGS.write_text(json.dumps({"hooks": {ev: [{"matcher": "Bash", "hooks": [{"type": "command", "command": hook_cmd}]}]
                                          for ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure")}}))
CWD = WORK / "cwd"
CWD.mkdir()


def session(label: str, command: str, allowed: str | None, background: bool = False) -> dict:
    EVENTS.unlink(missing_ok=True)
    for f in CWD.iterdir():
        f.unlink()
    how = " with run_in_background set to true" if background else ""
    prompt = (f"Use the Bash tool exactly once{how} to run this command verbatim, changing nothing: {command}\n"
              "Then reply with the tool's output and nothing else.")
    argv = ["claude", "-p", prompt, "--model", MODEL, "--setting-sources", "project", "--settings", str(SETTINGS),
            "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk"]
    if allowed:
        argv += ["--allowedTools", allowed]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_") and k not in ("CLAUDECODE", "CLAUDE_CALL", "LD_PRELOAD", "ASAN_OPTIONS")}
    t = time.monotonic()
    p = subprocess.run(argv, cwd=CWD, env=env, capture_output=True, text=True, timeout=240)
    took = time.monotonic() - t
    events = [json.loads(line) for line in EVENTS.read_text().splitlines()] if EVENTS.exists() else []
    results = []
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("type") == "user":
            for c in m.get("message", {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    results.append(body if isinstance(body, str) else json.dumps(body))
    pre = [e for e in events if e["hook_event_name"] == "PreToolUse"]
    post = [e for e in events if e["hook_event_name"].startswith("PostToolUse")]
    out = {"label": label, "rc": p.returncode, "took_s": round(took, 1),
           "pre_inputs": [e["tool_input"] for e in pre], "post": [(e["hook_event_name"], e["tool_input"]) for e in post],
           "results": [r[:160] for r in results], "stderr": p.stderr.strip()[-200:]}
    out["files"] = sorted(f.name for f in CWD.iterdir())
    if pre and post:
        out["pre_to_post_s"] = round(post[0]["_at"] - pre[0]["_at"], 2)
    print(json.dumps(out, indent=1), flush=True)
    return out


try:
    r = session("rewrite", "echo ORIGINAL", "Bash")
    print(f"ANSWER rewrite: applied without permissionDecision = {any('REWRITTEN' in x for x in r['results'])}")
    print(f"ANSWER post: PostToolUse input = {r['post']}")
    r = session("rules-orig", "touch ORIGINAL.txt", "Bash(touch ORIGINAL.txt)")
    print(f"ANSWER rules-orig: files = {r['files']}")
    r = session("rules-new", "touch ORIGINAL.txt", "Bash(touch REWRITTEN.txt)")
    print(f"ANSWER rules-new: files = {r['files']}")
    r = session("no-grant", "touch ORIGINAL.txt", None)
    print(f"ANSWER no-grant: files = {r['files']}")
    r = session("background", "echo ORIGINAL", "Bash", background=True)
    print(f"ANSWER background: pre inputs = {r['pre_inputs']}")
    r = session("eof", "(sleep 6 &); echo ORIGINAL-eof", "Bash")
    print(f"ANSWER eof: PreToolUse to PostToolUse {r.get('pre_to_post_s')} s (about 6 s: the call waits for the job's EOF)")
finally:
    shutil.rmtree(WORK, ignore_errors=True)
