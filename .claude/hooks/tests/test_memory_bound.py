#!/home/nicolas/.local/bin/python3.14
"""The guard hooks' memory bound: no call makes a hook take the host's memory.

usage: test_memory_bound.py [HOOKS_DIR]    (default: the hooks directory, parent of this tests/ directory)
Exit 0 when every case holds; 1 with the failing cases printed otherwise.

A value that doubles in a loop (`X=$X$X`) once took a hook to 24.5 GB and the WSL VM down with it. Every hook
child here runs under an outer RLIMIT_AS of OUTER, so a hook without its own bound fails a case instead of
reaching the OOM killer. The doubling commands are assembled at run time, so the call that runs this file does
not hold one. mut/run_muts.py with mut/round7.json shows each memory-bound line failing a check here when mutated.
"""
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

D = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
OUTER = 3 << 30
REPO = "/home/nicolas/dev/agda/aletheia"
TMP = Path(os.environ.get("TMPDIR", "/tmp")) / f"test-memory-bound-{os.getpid()}"


def outer_cap() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (OUTER, OUTER))


# Runs the hook as its child and reports the child's peak resident memory, so each hook is measured alone.
WRAP = ("import resource, subprocess, sys; r = subprocess.run(sys.argv[1:]); "
        "sys.stderr.write(f'\\nPEAK_MIB={resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss >> 10}\\n'); "
        "sys.exit(r.returncode)")


def hook(name: str, command: str, event_name: str = "PreToolUse", key: str | None = None) \
        -> tuple[int | None, str, float, int]:
    """(exit status, standard error, seconds, peak resident MiB) of one hook answering one event; `key` pairs a
    Post event with the Pre event that left its snapshot."""
    event = {"hook_event_name": event_name, "tool_name": "Bash", "session_id": f"membound-{time.monotonic_ns()}",
             "cwd": REPO, "tool_input": {"command": command}}
    if key:
        event["tool_use_id"] = key
    t = time.monotonic()
    try:
        p = subprocess.run([sys.executable, "-c", WRAP, sys.executable, str(D / name)], input=json.dumps(event),
                           capture_output=True, text=True, preexec_fn=outer_cap, timeout=120, check=False,
                           env=dict(os.environ, TMPDIR=str(TMP)))
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT", time.monotonic() - t, -1
    err, _, peak = p.stderr.rpartition("\nPEAK_MIB=")
    return p.returncode, err, time.monotonic() - t, int(peak)


def to_32k() -> str:
    return "X=a; " + ("X=" + "$X" * 2 + "; ") * 15  # 2**15 = 32768 bytes: under the value cap


NEST = ("X=a; for i in 1 2 3 4 5 6 7 8; do for j in 1 2 3 4 5 6 7 8; do X=" + "$X" * 2
        + "; done; done; cargo test")
LINE = "X=a; " + ("X=" + "$X" * 2 + "; ") * 40 + "cargo test"
REFS = to_32k() + "Y=" + "$X" * 100000 + "; cargo test"  # 3.2 GB if the word were built
sys.path.insert(0, str(D))
import _shell  # noqa: E402 - the bound of the hooks under test, 1 GiB where they predate it

MAX_MEMORY = getattr(_shell, "MAX_MEMORY", 1 << 30)
WORDS = MAX_MEMORY // 32768 + 8192  # an echo whose text passes the bound by 256 MiB: only the bound stops it
JOIN = to_32k() + "echo" + " $X" * WORDS + " > /tmp/membound.sh; bash /tmp/membound.sh; cargo test"
SUBST = to_32k() + "Y=$(dirname" + " $X" * WORDS + "); cargo test"  # a substitution's words, joined
EDGE = "ls -la; " * 4900 + "true"
HEREDOC = "python3 - <<'EOF'\n" + "x = 1\n" * 60000 + "EOF"
READ_MSG = "internal error"
CAP_MSG = "MiB to read it"
SMALL = 256  # MiB: a reading the value cap ends; ordinary calls measured 48-64 MiB of address space
BOUND = (MAX_MEMORY >> 20) + 64  # MiB: the hook's own bound, and the interpreter's slack past it


def last(err: str) -> str:
    return err.strip().splitlines()[-1][:120] if err.strip() else ""

failures: list[str] = []


def check(label: str, ok: bool, detail: str) -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {detail}")
    if not ok:
        failures.append(label)


def read_through(label: str, command: str) -> None:
    """The reading finishes by the value cap: the guard refuses the unlogged cargo test, no memory path taken."""
    rc, err, dt, peak = hook("heavy-run-guard.py", command)
    check(f"{label}, guard", rc == 2 and READ_MSG not in err and CAP_MSG not in err and peak < SMALL,
          f"rc={rc} {dt:.1f}s {peak} MiB {last(err)}")
    rc, err, dt, peak = hook("no-polling-loops.py", command)
    check(f"{label}, polling", rc == 0 and READ_MSG not in err and peak < SMALL, f"rc={rc} {dt:.1f}s {peak} MiB")


TMP.mkdir(parents=True, exist_ok=True)
read_through("nested doubling", NEST)
read_through("straight-line doubling", LINE)
read_through("100000 references to a 32 KiB value", REFS)
read_through(f"a substitution naming a 32 KiB value {WORDS} times", SUBST)

rc, err, dt, peak = hook("heavy-run-guard.py", JOIN)
check(f"echo of {WORDS} 32-KiB words, guard", rc == 2 and CAP_MSG in err and READ_MSG not in err and peak < BOUND,
      f"rc={rc} {dt:.1f}s {peak} MiB {last(err)}")
rc, err, dt, peak = hook("no-polling-loops.py", JOIN)
check(f"echo of {WORDS} 32-KiB words, polling", rc in (0, 2) and READ_MSG not in err and peak < BOUND,
      f"rc={rc} {dt:.1f}s {peak} MiB")

probe = (f"import sys; sys.path.insert(0, {str(D)!r}); import _shell; _shell.bound_memory(); "
         "b = bytearray(2 << 30); print('allocated')")
p = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, preexec_fn=outer_cap, check=False)
check("bound_memory refuses 2 GiB", p.returncode != 0 and "MemoryError" in p.stderr, f"rc={p.returncode}")

for label, command, want in (("git status", "git status", 0),
                             ("logged pinned cargo test", "taskset -c 0-19 cargo test > /tmp/x.log 2>&1", 0),
                             ("bare cargo test", "cargo test", 2),
                             ("4900 commands", EDGE, 0),
                             ("360 KB heredoc to python", HEREDOC, 0)):
    rc, err, dt, peak = hook("heavy-run-guard.py", command)
    check(f"{label}, guard", rc == want and CAP_MSG not in err and READ_MSG not in err and peak < SMALL,
          f"rc={rc} want {want} {dt:.1f}s {peak} MiB")
    rc, err, dt, peak = hook("no-polling-loops.py", command)
    check(f"{label}, polling", rc == 0 and READ_MSG not in err and peak < SMALL, f"rc={rc} {dt:.1f}s {peak} MiB")

for label, command in (("git status", "git status"), ("nested doubling", NEST)):
    for post in ("PostToolUse", "PostToolUseFailure"):  # the guard's after-call check, on the Pre call's snapshot
        key = f"membound-{post}-{time.monotonic_ns()}"
        hook("heavy-run-guard.py", command, "PreToolUse", key)
        rc, err, dt, peak = hook("heavy-run-guard.py", command, post, key)
        check(f"{label}, guard {post}", rc == 0 and READ_MSG not in err and peak < SMALL,
              f"rc={rc} {dt:.1f}s {peak} MiB {last(err)}")

print(f"{len(failures)} failing" + (": " + ", ".join(failures) if failures else ""))
sys.exit(1 if failures else 0)
