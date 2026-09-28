"""Time the guard and the loop hook on the shapes round three measured as slow; each run capped at 60 s.

Usage: python3 cost4.py HOOKS_DIR
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(sys.argv[1])
S = Path(tempfile.mkdtemp(prefix="cost4-"))
(S / "self.sh").write_text((f"bash {S}/self.sh; " * 16) + "\n")
(S / "big.sh").write_text(("echo " + "x" * 70 + "\n") * 3300)  # about 242 KB

(S / "selfsrc.sh").write_text(f"source {S}/selfsrc.sh\n" * 4 + "[[ x ]]\n" * 20000)
(S / "selfpy.py").write_text("import subprocess\n" + "".join(f"subprocess.run(['python3', '{S}/selfpy.py'])\n" for _ in range(10)))
(S / "bigpy.py").write_text("x = 1\n" * 6000)
SHAPES = {
    "perl: 50000 escaped quotes": "perl -e '" + "\\'" * 50000,
    "15 Monitor pgrep patterns grep cannot settle": "; ".join(["while pgrep -f $'x{1,1000}{1,1000}\\x79N' >/dev/null; do sleep 5; done"] * 15),
    "40 os.system(bash big.sh) from Python": "python3 - <<'PY'\nimport os\n" + "".join(f"os.system('bash {S}/big.sh')\n" for _ in range(40)) + "PY",
    "a file sourcing itself 4 times + 20000 tests": f"source {S}/selfsrc.sh",
    "32 functions of 8x8 loops around 2000 tests": "f() { " + "".join(f"for a{k} in 1 2 3 4 5 6 7 8; do " for k in range(2)) + 'echo "$( ' + "[[ x ]]; " * 2000 + ' )"' + "; done" * 2 + "; }; " + "f; " * 32,
    "1 MB of [[ x ]]": "[[ x ]]\n" * 131072,
    "${ x900 around 500 KB": "echo " + "${" * 900 + "x" * 500000 + "}" * 900,
    "a Python script running itself 10 times": f"python3 {S}/selfpy.py",
    "a 6000-line Python script in 8x8 loops": "".join(f"for v{k} in 1 2 3 4 5 6 7 8; do " for k in range(2)) + f"python3 {S}/bigpy.py" + "; done" * 2,
    "nested $(( $(wc -l x18": '"$(( $(wc -l ' * 18,
    "20000 assignments": "; ".join(f"A{i}=1; echo $A{i}" for i in range(20000)),
    "py fallback: 50000 escaped quotes": "python3 - <<'PY'\nx = '" + "\\'" * 50000 + "\nPY",
    "py fallback: 100000 spaces": "python3 - <<'PY'\nx = a" + " " * 100000 + "b\n)(\nPY",
    "py fallback: 5000 open(": "python3 - <<'PY'\n" + "open(" * 5000 + "\nPY",
    "py fallback: 20000 run(": "python3 - <<'PY'\n" + 'subprocess.run("x",' * 20000 + "\nPY",
    "perl: 20000 escaped quotes": "perl -e '" + "\\'" * 20000 + "'",
    "perl: 20000 open": "perl - <<'PL'\n" + "open " * 20000 + "\nPL",
    "6 nested for loops of 8": "".join(f"for v{k} in 1 2 3 4 5 6 7 8; do " for k in range(6)) + "echo x"
                                + "; done" * 6,
    "a script running itself 16x per line": f"bash {S}/self.sh",
    "a 242 KB script run 50 times": "; ".join([f"bash {S}/big.sh"] * 50),
}


def run(hook: str, command: str, tool: str = "Bash") -> tuple[str, float]:
    event = {"hook_event_name": "PreToolUse", "tool_name": tool,
             "tool_input": {"command": command, "run_in_background": True}, "cwd": str(S)}
    t0 = time.monotonic()
    try:
        p = subprocess.run([str(HOOKS / hook)], input=json.dumps(event), capture_output=True, text=True, timeout=60)
        rc = str(p.returncode)
    except subprocess.TimeoutExpired:
        rc = "TIMEOUT"
    return rc, time.monotonic() - t0


for name, command in SHAPES.items():
    for hook in ("heavy-run-guard.py", "no-polling-loops.py"):
        rc, took = run(hook, command, "Monitor" if "Monitor" in name else "Bash")
        print(f"{name:40} {hook:20} rc={rc:7} {took:6.2f}s", flush=True)
