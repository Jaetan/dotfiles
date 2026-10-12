#!/home/nicolas/.local/bin/python3.15
"""Run only test_guards.limits_cases() (rules 5-6) against HOOKS_DIR, the suite run_muts.py gives a limits mutant.

Usage: [MUT_SUITE=tests/mut/run_limits.py] run_limits.py HOOKS_DIR
Prints a line per case, MISS for one that disagrees, and a closing "... disagree" line; exit 1 on a disagreement.
"""
import shutil
import sys
from pathlib import Path

sys.argv = ["test_guards.py", sys.argv[1]]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_guards as tg  # noqa: E402
try:
    tg.setup()
    (tg.Q / "probes").mkdir(exist_ok=True)
    (tg.Q / "probes" / "run_all.sh").write_text('for p in probes/*.sh; do bash "$p"; done\n')
    rows = tg.limits_cases()
finally:
    shutil.rmtree(tg.BASE, ignore_errors=True)
bad = 0
for cid, want, got, what, err in rows:
    bad += want != got
    print(f"{'ok  ' if want == got else 'MISS'} {cid:<6} want={want} got={got} {what}")
    if want != got and err.strip():
        print("        " + err.strip().splitlines()[0][:150])
print(f"{len(rows)} cases, {bad} disagree")
sys.exit(1 if bad else 0)
