#!/usr/bin/env python3
"""Replay entry-point-run-guard.py on the session that motivated it.

Claim: on the transcript of session 166ae0fe (2026-10-02), the guard finds no
run of tools/install_hooks.py between its last edit before the sweep-first
commit dribble (issued 09:53:39Z) and that dribble (written by 11:32:56Z):
the installer was never run, which is how its stale summary shipped. Two
things that look like a run must not count. A probe whose subject is the file
ran in that window, but it imports the module and renders a constant; and two
heredoc bodies written in that window spell `-m tools.install_hooks` as text.
So the probe also checks the narrower alternatives would have been wrong: a
probe-subject rule clears the file, and a plain text match finds a "run".

Usage: probe_entry_point_run_guard_replay.py [GUARD] [TRANSCRIPT] [REPO]
  GUARD       the guard to replay (default: ../entry-point-run-guard.py)
  TRANSCRIPT  the session's transcript (default: the 166ae0fe one)
  REPO        the aletheia checkout (default: ~/dev/agda/aletheia)
Exit 0 when the claim holds, 1 when it does not, 2 when the transcript or the
checkout is missing, so nothing could be checked.
"""

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import NewType

# What a replay found wrong; the probe's exit status.
Prose = NewType("Prose", str)
ExitStatus = NewType("ExitStatus", int)
HOLDS, BROKEN, UNCHECKED = ExitStatus(0), ExitStatus(1), ExitStatus(2)

HOOKS = Path(__file__).resolve().parent.parent
GUARD = Path(sys.argv[1]) if len(sys.argv) > 1 else HOOKS / "entry-point-run-guard.py"
TRANSCRIPT = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    Path.home() / ".claude" / "projects" / "-home-nicolas-dev-agda-aletheia"
    / "166ae0fe-c2f8-4f40-aafd-e8d46e640d20.jsonl")
REPO = Path(sys.argv[3]) if len(sys.argv) > 3 else Path.home() / "dev" / "agda" / "aletheia"
EDITED = datetime.fromisoformat("2026-10-02T09:53:39.730Z").timestamp() + 1.0
DRIBBLE = datetime.fromisoformat("2026-10-02T11:32:56.752Z").timestamp()
TARGET = "tools/install_hooks.py"


def main() -> ExitStatus:
    if not TRANSCRIPT.is_file() or not (REPO / TARGET).is_file():
        print(f"nothing to replay: {TRANSCRIPT} or {REPO / TARGET} is missing")
        return UNCHECKED
    sys.path.insert(0, str(GUARD.parent))
    spec = importlib.util.spec_from_file_location("entry_point_run_guard", GUARD)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    issued, _ = guard.read_transcript(TRANSCRIPT, guard.Offset(0))
    window = [each for each in issued if each.at < DRIBBLE]
    need = guard.Needed(guard.RepoPath(TARGET), REPO / TARGET,
                        guard.module_names(REPO, REPO / TARGET))
    bad: list[Prose] = []
    if guard.ran(need, REPO, window, guard.Instant(EDITED)):
        bad.append(Prose("the guard counts a run of the installer that never happened"))

    subject = TARGET.replace("/", "_") + "--"
    probe_runs = [each for each in window if each.at > EDITED and each.run.kind != "module"
                  and Path(each.run.target).name.startswith(subject)]
    if not probe_runs:
        bad.append(Prose("no run of a probe whose subject is the installer: not the recorded window"))

    mentions = 0
    for line in TRANSCRIPT.open(encoding="utf-8"):
        if '"tool_use"' not in line or "-m tools.install_hooks" not in line:
            continue
        entry = json.loads(line)
        at = datetime.fromisoformat(entry.get("timestamp", "1970-01-01T00:00:00Z")).timestamp()
        mentions += EDITED < at < DRIBBLE
    if not mentions:
        bad.append(Prose("no command spelling `-m tools.install_hooks` in the window"))

    if bad:
        print("the replay does not hold:")
        for line in bad:
            print(f"  {line}")
        return BROKEN
    print(f"PASS: no run of {TARGET} found; {len(probe_runs)} subject-probe run(s) and "
          f"{mentions} text mention(s) in the window, none counted")
    return HOLDS


if __name__ == "__main__":
    sys.exit(main())
