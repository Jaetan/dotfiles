#!/home/nicolas/.local/bin/python3.14
"""Apply each mutant to a copy of the hooks and report which cases of the two suites die.

Usage: [MUT_SUITE=SUITE] run_muts.py MUTS.json HOOKS_DIR [WORKERS]
MUT_SUITE names the suite each copy runs (default: test_guards.py, followed by the memory
suite); a suite is run as `SUITE COPY_DIR` and prints a MISS line per case that dies and a
closing "... disagree" line.
A mutant is [file, old, new] with `old` unique in the file, or [file, old, new, nth] replacing the nth
(0-based) of several occurrences. Each copy holds HOOKS_DIR's *.py, its requirements.txt when it has one (a hook reads its pins
beside itself), and the mutated file, which need not end in .py (the token watcher does not). Each copy lives in a temporary directory; each suite runs under
RLIMIT_AS = CAP, so a mutant that makes a hook's reading grow without bound fails its own suite, not the host.
The memory suite's unbounded mutants reach about 2.6 GB each: keep WORKERS x 2.6 GB well inside the host's RAM.
The suites run first on an unmutated copy: a case failing there exits 2 with no mutant verdict, since that case
would read every mutant KILLED. Each suite runs in a namespace keeping the current user, as the plain suite runs.
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
from typing import Literal, NamedTuple, NewType

DEFAULT_SUITE = Path(__file__).resolve().parent.parent / "test_guards.py"
SUITE = Path(os.environ.get("MUT_SUITE", DEFAULT_SUITE)).resolve()
MEMORY = DEFAULT_SUITE.with_name("test_memory_bound.py")
CAP = 4 << 30
muts = json.loads(Path(sys.argv[1]).read_text())
HOOKS = Path(sys.argv[2]).resolve()
WORK = Path(tempfile.mkdtemp(prefix="guard-mutants-"))


def limit() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (CAP, CAP))


Found = Literal["KILLED", "SURVIVED", "CRASHED"]  # what the suites show on one copy
Miss = NewType("Miss", str)  # a case that died on a copy, or why its run crashed


class Verdict(NamedTuple):
    found: Found
    miss: list[Miss]


def copied(d: Path) -> None:
    """Copy into ``d`` the hooks and every file a mutant names, a script without the .py suffix included."""
    d.mkdir()
    for p in [*HOOKS.glob("*.py"), *HOOKS.glob("requirements.txt")]:
        shutil.copy2(p, d / p.name)
    for f in {mut[0] for mut in muts}:
        if not (d / f).exists():
            shutil.copy2(HOOKS / f, d / f)


def verdict(d: Path) -> Verdict:
    """Run the suites on the copy ``d``: KILLED with the cases that die, SURVIVED, or CRASHED with why."""
    # -c keeps the current user inside the namespace, so a file's mode refuses there as it refuses in the plain
    # suite: under -r the suite runs as the namespace's root, which no mode refuses.
    r = subprocess.run(["unshare", "-cpf", "--mount-proc", sys.executable, str(SUITE), str(d)], capture_output=True,
                       text=True, check=False, preexec_fn=limit)
    miss = [Miss(ln.split()[1]) for ln in r.stdout.splitlines() if ln.startswith("MISS")]
    if not any(ln.endswith(" disagree") for ln in r.stdout.splitlines()):
        return Verdict("CRASHED", [Miss(ln) for ln in (r.stderr.strip().splitlines() or ["no output"])[-1:]])
    if SUITE != DEFAULT_SUITE:
        return Verdict("KILLED" if miss else "SURVIVED", miss)
    m = subprocess.run([sys.executable, str(MEMORY), str(d)], capture_output=True, text=True, check=False,
                       preexec_fn=limit)
    if not any(ln.endswith(("failing", "failing:")) or " failing: " in ln for ln in m.stdout.splitlines()):
        return Verdict("CRASHED", [Miss("memory suite: " + ((m.stderr.strip().splitlines() or ["no output"])[-1]))])
    miss += [Miss("mem:" + ln[5:].split(":")[0]) for ln in m.stdout.splitlines() if ln.startswith("FAIL")]
    return Verdict("KILLED" if miss else "SURVIVED", miss)


def one(k: int) -> tuple[int, str, str, list[str]]:
    f, old, new, *nth = muts[k]
    d = WORK / f"m{k}"
    copied(d)
    s = (d / f).read_text()
    n = s.count(old)
    if (not nth and n != 1) or (nth and n <= nth[0]):
        return k, f, f"ANCHOR x{n}", []
    at = -1
    for _ in range((nth[0] if nth else 0) + 1):
        at = s.index(old, at + 1)
    (d / f).write_text(s[:at] + new + s[at + len(old):])
    found = verdict(d)
    return k, f, found.found, found.miss[:6]


try:
    # An unmutated copy that fails a case would read every mutant KILLED: no verdict is given over it.
    copied(WORK / "baseline")
    if (baseline := verdict(WORK / "baseline")).found != "SURVIVED":
        print(f"BASELINE {baseline.found}: the unmutated copy fails {baseline.miss}; no mutant verdict is given",
              flush=True)
        sys.exit(2)
    with ThreadPoolExecutor(int(sys.argv[3]) if len(sys.argv) > 3 else 6) as ex:
        for k, f, shown, miss in ex.map(one, range(len(muts))):
            print(k, f, shown, miss, flush=True)
finally:
    shutil.rmtree(WORK, ignore_errors=True)
