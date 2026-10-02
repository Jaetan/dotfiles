#!/home/nicolas/.local/bin/python3.14
"""Apply each mutant to a copy of the hooks and report which cases of the two suites die.

Usage: [MUT_SUITE=SUITE] run_muts.py MUTS.json HOOKS_DIR [WORKERS]
MUT_SUITE names the suite each copy runs (default: test_guards.py, followed by the memory
suite); a suite is run as `SUITE COPY_DIR` and prints a MISS line per case that dies and a
closing "... disagree" line.
A mutant is [file, old, new] with `old` unique in the file, or [file, old, new, nth] replacing the nth
(0-based) of several occurrences. Each copy lives in a temporary directory; each suite runs under
RLIMIT_AS = CAP, so a mutant that makes a hook's reading grow without bound fails its own suite, not the host.
The memory suite's unbounded mutants reach about 2.6 GB each: keep WORKERS x 2.6 GB well inside the host's RAM.
"""
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_SUITE = Path(__file__).resolve().parent.parent / "test_guards.py"
SUITE = Path(os.environ.get("MUT_SUITE", DEFAULT_SUITE)).resolve()
MEMORY = DEFAULT_SUITE.with_name("test_memory_bound.py")
CAP = 4 << 30
muts = json.loads(Path(sys.argv[1]).read_text())
HOOKS = Path(sys.argv[2]).resolve()
WORK = Path(tempfile.mkdtemp(prefix="guard-mutants-"))


def limit() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (CAP, CAP))


def one(k: int) -> tuple[int, str, str, list[str]]:
    f, old, new, *nth = muts[k]
    d = WORK / f"m{k}"
    d.mkdir()
    for p in HOOKS.glob("*.py"):
        shutil.copy2(p, d / p.name)
    s = (d / f).read_text()
    n = s.count(old)
    if (not nth and n != 1) or (nth and n <= nth[0]):
        return k, f, f"ANCHOR x{n}", []
    at = -1
    for _ in range((nth[0] if nth else 0) + 1):
        at = s.index(old, at + 1)
    (d / f).write_text(s[:at] + new + s[at + len(old):])
    r = subprocess.run(["unshare", "-rpf", "--mount-proc", sys.executable, str(SUITE), str(d)], capture_output=True,
                       text=True, check=False, preexec_fn=limit)
    miss = [ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith("MISS")]
    if not any(ln.endswith(" disagree") for ln in r.stdout.splitlines()):
        return k, f, "CRASHED", (r.stderr.strip().splitlines() or ["no output"])[-1:]  # the suite never finished
    if SUITE != DEFAULT_SUITE:
        return k, f, "KILLED" if miss else "SURVIVED", miss[:6]
    m = subprocess.run([sys.executable, str(MEMORY), str(d)], capture_output=True, text=True, check=False,
                       preexec_fn=limit)
    if not any(ln.endswith(("failing", "failing:")) or " failing: " in ln for ln in m.stdout.splitlines()):
        return k, f, "CRASHED", ["memory suite: " + ((m.stderr.strip().splitlines() or ["no output"])[-1])]
    miss += ["mem:" + ln[5:].split(":")[0] for ln in m.stdout.splitlines() if ln.startswith("FAIL")]
    return k, f, "KILLED" if miss else "SURVIVED", miss[:6]


try:
    with ThreadPoolExecutor(int(sys.argv[3]) if len(sys.argv) > 3 else 6) as ex:
        for k, f, verdict, miss in ex.map(one, range(len(muts))):
            print(k, f, verdict, miss, flush=True)
finally:
    shutil.rmtree(WORK, ignore_errors=True)
