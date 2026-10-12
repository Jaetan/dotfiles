#!/home/nicolas/.local/bin/python3.14
"""Probe of wrapping each Bash call from zsh's startup file instead of rewriting its text.

usage: probe_zshenv_exec.py [MODEL]   (default claude-haiku-4-5-20251001; each stage is one short headless session)

The Bash tool runs `zsh -c '<snapshot source> && eval <call> < /dev/null && pwd -P >| <cwd file>'` with CLAUDECODE=1,
and zsh reads $ZDOTDIR/.zshenv first, with the -c string in $ZSH_EXECUTION_STRING. A guarded line there can exec a
wrapper around the whole line, so the permission system still sees the call's own text. Nothing of the user's is
edited: each nested session gets ZDOTDIR=<a scratch directory> and only this probe's settings.

  inventory  every zsh started with CLAUDECODE set: a hook, a foreground call, a background call, a Monitor call
  exec       through a stub wrapper (a scope under claude.slice, a log, a view of a throwaway repository, no
             sync-back): a cd persists to the next call, `Bash(touch ORIGINAL.txt)` allows the wrapped call, a
             background call runs, a job the call leaves is stopped
  kill       the harness times a call out: what is left (the call's scope, its snapshot)
Exit 0 when every check holds.
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
WORK = Path(tempfile.mkdtemp(prefix="zshenv-exec-", dir=os.environ.get("TMPDIR")))
ZDOT, SNAPS, LOGS = WORK / "zdot", WORK / "snaps", WORK / "logs"
for d in (ZDOT, SNAPS, LOGS):
    d.mkdir()
INVENTORY = WORK / "inventory.log"
WRAPPER = WORK / "wrapper.py"
failures: list[str] = []
SNAPSHOTS_BEFORE = set(Path.home().glob(".claude/shell-snapshots/snapshot-zsh-*"))


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{': ' + detail if detail else ''}", flush=True)
    if not ok:
        failures.append(label)


# The guarded line: harness shape only, never inside a wrapped call, a kill switch, and fail open when the wrapper
# is missing (a failed exec would end the shell).
ZSHENV = f'''if [[ -n $CLAUDECODE ]]; then
  print -r -- "$EPOCHREALTIME wrapped=${{CLAUDE_CALL:-0}} parent=$(</proc/$PPID/comm) args=${{(q)@}} exec=${{(q)ZSH_EXECUTION_STRING}}" >> {INVENTORY}
fi
if [[ -n $CLAUDECODE && -z $CLAUDE_CALL && -n $WRAP && ! -e {WORK}/off && -x {WRAPPER}
      && $ZSH_EXECUTION_STRING == "source "*/shell-snapshots/snapshot-zsh-*" && pwd -P >| "* ]]; then
  exec {WRAPPER} "$ZSH_EXECUTION_STRING"
fi
'''
(ZDOT / ".zshenv").write_text("zmodload zsh/datetime\n" + ZSHENV)

WRAPPER.write_text(f'''#!{sys.executable}
"""Stub wrapper: the call in a scope under claude.slice and a view of its repository, its output to a log."""
import os, signal, subprocess, sys, threading, time
call = sys.argv[1]
env = dict(os.environ, CLAUDE_CALL="1")
ident = f"{{time.time_ns()}}-{{os.getpid()}}"
unit = f"probe-call-{{ident}}"
log = open("{LOGS}/" + ident + ".log", "ab", buffering=0)
top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
view = []
if top:
    snap = "{SNAPS}/" + ident
    os.makedirs(snap)
    subprocess.run(f"git ls-files -z --cached --others --exclude-standard | tar --format=posix --null -T - -cf - "
                   f"| tar -xf - -C {{snap}}", shell=True, cwd=top, check=True)
    view = ["bwrap", "--dev-bind", "/", "/", "--bind", snap, top, "--bind", top + "/.git", top + "/.git",
            "--chdir", os.getcwd()]
    out = subprocess.run(["git", "-C", top, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
                         capture_output=True, text=True).stdout
    for d in (p.rstrip("/") for p in out.split("\\0") if p.endswith("/")):
        os.makedirs(os.path.join(snap, d), exist_ok=True)
        view += ["--bind", os.path.join(top, d), os.path.join(top, d)]
argv = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--slice=claude.slice", f"--unit={{unit}}",
        "-p", "OOMPolicy=continue", "-p", "TimeoutStopSec=2s", "--", *view, "/usr/bin/zsh", "-c", call]
def stop(*_):
    subprocess.run(["systemctl", "--user", "stop", unit + ".scope"], capture_output=True)
def on_signal(n, f):
    with open("{WORK}/signals", "a") as g:
        g.write(f"{{unit}} {{n}}\\n")
    stop()
    os._exit(128 + n)
for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(s, on_signal)
p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def pump(src, dst):
    while chunk := os.read(src.fileno(), 65536):
        log.write(chunk)
        os.write(dst, chunk)
threads = [threading.Thread(target=pump, args=(p.stdout, 1)), threading.Thread(target=pump, args=(p.stderr, 2))]
for t in threads:
    t.start()
rc = p.wait()
stop()
for t in threads:
    t.join()
sys.exit(rc if rc >= 0 else 128 - rc)
''')
WRAPPER.chmod(0o755)

REPO = WORK / "repo"
(REPO / "sub").mkdir(parents=True)
(REPO / "build").mkdir()
(REPO / ".gitignore").write_text("build/\n")
(REPO / "sub" / "a.txt").write_text("a\n")
for args in (["init", "-q"], ["add", "-A"],
             ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base"]):
    subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True)

HOOK = WORK / "hook.sh"
HOOK.write_text("#!/bin/sh\ncat > /dev/null\n")
HOOK.chmod(0o755)
SETTINGS = WORK / "settings.json"
SETTINGS.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
                                                                                       "command": str(HOOK)}]}]}}))


def session(label: str, prompt: str, allowed: list[str], wrap: bool) -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_") and k not in ("CLAUDECODE", "CLAUDE_CALL", "LD_PRELOAD", "ASAN_OPTIONS")}
    env["ZDOTDIR"] = str(ZDOT)
    if wrap:
        env["WRAP"] = "1"
    argv = ["claude", "-p", prompt, "--model", MODEL, "--setting-sources", "project", "--settings", str(SETTINGS),
            "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--allowedTools", *allowed]
    p = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
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
    print(f"--- {label}: rc {p.returncode}, results {json.dumps([r[:200] for r in results], indent=1)}", flush=True)
    return results


STEPS = ("Make these tool calls one at a time, in order, each exactly as written, changing nothing:\n"
         "1. Bash: cd sub\n2. Bash: pwd\n3. Bash: touch ORIGINAL.txt\n"
         "4. Bash with run_in_background set to true: echo BG-DONE\n"
         "5. Monitor: echo MONITOR-OK\n"
         "Then reply with each tool's output, one per line.")
ALLOWED = ["Bash(touch ORIGINAL.txt)", "Monitor"]

try:
    session("inventory (no wrapper)", STEPS, ALLOWED, wrap=False)
    lines = INVENTORY.read_text().splitlines() if INVENTORY.exists() else []
    print(f"     inventory: {len(lines)} zsh starts with CLAUDECODE set")
    for ln in lines:
        print("       " + ln[:230])
    harness = sum(1 for ln in lines if "shell-snapshots/snapshot-zsh-" in ln and "pwd\\ -P" in ln)
    print(f"     of which in the harness shape: {harness}")
    INVENTORY.unlink(missing_ok=True)
    (REPO / "sub" / "ORIGINAL.txt").unlink(missing_ok=True)

    results = session("exec (stub wrapper)", STEPS, ALLOWED, wrap=True)
    lines = INVENTORY.read_text().splitlines() if INVENTORY.exists() else []
    check("the harness lines went through the wrapper", any("wrapped=1" in ln for ln in lines),
          f"{sum('wrapped=1' in ln for ln in lines)} wrapped starts")
    text = "\n".join(results)
    check("a cd persists to the next call through the real harness", str(REPO / "sub") in text)
    snaps = sorted(SNAPS.iterdir())
    touched = [s for s in snaps if (s / "sub" / "ORIGINAL.txt").exists()]
    check("`Bash(touch ORIGINAL.txt)` allows the wrapped call (it ran, in a snapshot)", bool(touched),
          f"{len(snaps)} snapshots")
    check("the real tree is untouched without sync-back", not (REPO / "sub" / "ORIGINAL.txt").exists())
    logged = {f.name: f.read_text(errors="replace") for f in LOGS.iterdir()}
    check("a background call runs through the wrapper", any("BG-DONE" in t for t in logged.values()))
    # the permission question is the touch case's; this one only needs the call to run
    leftover = session("leftover", "Make one Bash tool call running exactly: tail -f /dev/null & echo started $!\n"
                       "Then reply with the tool's output.", ["Bash"], wrap=True)
    time.sleep(0.5)
    pid = next((w.split()[1] for r in leftover for w in r.splitlines() if w.startswith("started ")), None)
    check("a job the call left running is stopped", pid is not None and not Path(f"/proc/{pid}").exists(), str(pid))
    check("a Monitor call runs through the wrapper", any("MONITOR-OK" in t for t in logged.values()))
    logs = sorted(LOGS.iterdir())
    check("each wrapped call has a log", len(logs) >= 6, f"{len(logs)} logs")

    before = {s.name for s in SNAPS.iterdir()}
    t0 = time.monotonic()
    session("kill", "Make one Bash tool call with its timeout parameter set to 3000 milliseconds, running exactly: "
            "python3 -c 'import time; time.sleep(29.875)'\nThen reply with the tool's output.", ["Bash(python3:*)"],
            wrap=True)
    print(f"     kill: the session took {time.monotonic() - t0:.1f} s")
    time.sleep(0.5)
    sig = (WORK / "signals").read_text().split() if (WORK / "signals").exists() else []
    print(f"     kill: signals the wrappers caught {sig or '(none: killed outright, or never signalled)'}")
    left = subprocess.run(["systemctl", "--user", "list-units", "--all", "--no-legend", "probe-call-*"],
                          capture_output=True, text=True).stdout.strip()
    new = sorted({s.name for s in SNAPS.iterdir()} - before)
    print(f"     kill: scopes left {left or '(none)'}; snapshots left {new}")
finally:
    subprocess.run("systemctl --user stop 'probe-call-*'", shell=True, capture_output=True)
    # what the nested sessions leave: their task directories and their shell snapshots
    for d in Path("/tmp/claude-1000").glob("-tmp-" + WORK.name + "*"):
        shutil.rmtree(d, ignore_errors=True)
    for f in set(Path.home().glob(".claude/shell-snapshots/snapshot-zsh-*")) - SNAPSHOTS_BEFORE:
        f.unlink(missing_ok=True)
    subprocess.run(["chmod", "-R", "u+rwx", str(WORK)], capture_output=True)
    shutil.rmtree(WORK, ignore_errors=True)
print(f"{len(failures)} failing" + (": " + ", ".join(failures) if failures else ""))
sys.exit(1 if failures else 0)
