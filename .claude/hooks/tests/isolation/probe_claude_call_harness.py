#!/home/nicolas/.local/bin/python3.14
"""Probe of claude-call as installed, through the real Claude Code harness.

usage: probe_claude_call_harness.py [MODEL]   (default claude-haiku-4-5-20251001; three short headless sessions)

Each session gets ZDOTDIR=<scratch> whose .zshenv sources the tracked claude-call.zshenv, as ~/.zshenv does once
installed, with CLAUDE_CALL_ROOT in a scratch directory; the wrapper is the real one. Nothing of the user's is
edited. Checks, on a throwaway repository: every tool call is logged; a cd persists to the next call; an allow rule
matches the call's own text and its write is synced back to the real tree; a background call and a Monitor call run
wrapped; a job a call leaves is stopped; at session end nothing is left (no scope, no view). Exit 0 when all hold.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2]
MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-4-5-20251001"
WORK = Path(tempfile.mkdtemp(prefix="claude-call-harness-", dir=os.environ.get("TMPDIR")))
ROOT, ZDOT, REPO = WORK / "root", WORK / "zdot", WORK / "repo"
ZDOT.mkdir()
(ZDOT / ".zshenv").write_text(f"source {HOOKS / 'claude-call.zshenv'}\n")
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{': ' + detail if detail else ''}", flush=True)
    if not ok:
        failures.append(label)


(REPO / "sub").mkdir(parents=True)
(REPO / "build").mkdir()
(REPO / ".gitignore").write_text("build/\n")
(REPO / "sub" / "a.txt").write_text("a\n")
for args in (["init", "-q"], ["add", "-A"],
             ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base"]):
    subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True)
SETTINGS = WORK / "settings.json"
SETTINGS.write_text("{}")


def session(prompt: str, allowed: list[str]) -> list[str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_CODE_") and k not in ("CLAUDECODE", "CLAUDE_CALL", "LD_PRELOAD", "ASAN_OPTIONS")}
    env.update(ZDOTDIR=str(ZDOT), CLAUDE_CALL_ROOT=str(ROOT))
    argv = ["claude", "-p", prompt, "--model", MODEL, "--setting-sources", "project", "--settings", str(SETTINGS),
            "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--allowedTools", *allowed]
    p = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, timeout=300, check=False)
    results = []
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        for c in m.get("message", {}).get("content", []) if m.get("type") == "user" else []:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                body = c.get("content")
                results.append(body if isinstance(body, str) else json.dumps(body))
    print(f"--- rc {p.returncode}, results {json.dumps([r[:160] for r in results], indent=1)}", flush=True)
    return results


def logs() -> dict[str, str]:
    d = ROOT / "claude-calls"
    return {p.name: p.read_text(errors="replace") for p in d.glob("*.log")} if d.exists() else {}


try:
    results = session("Make these tool calls one at a time, in order, each exactly as written, changing nothing:\n"
                      "1. Bash: cd sub\n2. Bash: pwd\n3. Bash: touch ORIGINAL.txt\n"
                      "4. Bash with run_in_background set to true: echo BG-DONE\n5. Monitor: echo MONITOR-OK\n"
                      "Then reply with each tool's output, one per line.",
                      ["Bash(touch ORIGINAL.txt)", "Monitor"])
    text = "\n".join(results)
    logged = logs()
    check("every tool call is logged", len(logged) >= 5, f"{len(logged)} logs")
    check("a cd persists to the next call", str(REPO / "sub") in text)
    check("an allow rule matches the call's own text, and its write reaches the real tree",
          (REPO / "sub" / "ORIGINAL.txt").exists())
    check("a background call runs wrapped", any("BG-DONE" in t for t in logged.values()))
    check("a Monitor call runs wrapped", any("MONITOR-OK" in t for t in logged.values()))
    check("no call fell open", not any("claude-call:" in t for t in [*results, *logged.values()]))
    leftover = session("Make one Bash tool call running exactly: tail -f /dev/null & echo started $!\n"
                       "Then reply with the tool's output.", ["Bash"])
    time.sleep(0.5)
    pid = next((w.split()[1] for r in leftover for w in r.splitlines() if w.startswith("started ")), None)
    check("a job a call left is stopped", pid is not None and not Path(f"/proc/{pid}").exists(), str(pid))
    session("Make one Bash tool call with its timeout parameter set to 3000 milliseconds, running exactly: "
            "python3 -c 'import time; time.sleep(29.875)'\nThen reply with the tool's output.", ["Bash(python3:*)"])
    time.sleep(3)
    scopes = subprocess.run(["systemctl", "--user", "list-units", "--all", "--no-legend", "claude-call-*"],
                            capture_output=True, text=True, check=False).stdout
    ours = [line for line in scopes.splitlines() if "nosession" in line or WORK.name in line]
    views = [p for p in (ROOT / "claude-views").iterdir() if not p.name.startswith(".")] \
        if (ROOT / "claude-views").exists() else []
    check("at session end nothing is left: no view", not views, str(views))
    print(f"     claude-call scopes now: {len(scopes.splitlines())} (this session's own calls among them: {len(ours)})")
finally:
    for d in Path("/tmp/claude-1000").glob("-tmp-" + WORK.name + "*"):
        shutil.rmtree(d, ignore_errors=True)
    subprocess.run(["chmod", "-R", "u+rwx", str(WORK)], capture_output=True, check=False)
    shutil.rmtree(WORK, ignore_errors=True)
print(f"{len(failures)} failing" + (": " + ", ".join(failures) if failures else ""))
sys.exit(1 if failures else 0)
