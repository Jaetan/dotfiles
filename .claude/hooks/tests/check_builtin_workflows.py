#!/home/nicolas/.local/bin/python3.14
"""Compare the launch guard's BUILT_IN with the workflows the installed Claude Code registers.

Run it after every Claude Code update: workflow-launch-guard.py refuses a
launch by name of a built-in workflow BUILT_IN does not list, and keeps a name
the binary no longer registers. It reads the binary as the guard does, with the
guard's own reader: the file `claude` on PATH resolves to, else the newest file
under ~/.local/share/claude/versions. It prints the version, the names the
binary registers and BUILT_IN.

Usage: check_builtin_workflows.py [GUARD]
  GUARD  the guard whose BUILT_IN and reader to use, or a hooks directory holding it (default: ..)
Exit 0 when the two sets are equal; 1 when they differ or the binary cannot be read.
"""

import importlib.util
import sys
import time
from pathlib import Path
from typing import NewType

ExitStatus = NewType("ExitStatus", int)
AGREE, DIFFER = ExitStatus(0), ExitStatus(1)

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "workflow-launch-guard.py" if GIVEN.is_dir() else GIVEN


def main() -> ExitStatus:
    """Read the installed Claude Code's built-in workflows, print them beside BUILT_IN, and say whether they agree."""
    sys.path.insert(0, str(GUARD.parent))  # the guard imports _shell from beside itself
    spec = importlib.util.spec_from_file_location("workflow_launch_guard", GUARD)
    if spec is None or spec.loader is None:
        print(f"{GUARD} cannot be loaded as a module")
        return DIFFER
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    listed = ", ".join(sorted(guard.BUILT_IN))
    started = time.perf_counter()
    installed = guard.built_in_workflows()
    spent = time.perf_counter() - started
    if not isinstance(installed, guard.Installed):
        print(f"{installed}\nBUILT_IN:  {listed}")
        return DIFFER
    print(f"Claude Code {installed.version}: {installed.binary}, read in {spent:.2f} s")
    print(f"registers: {', '.join(sorted(installed.names))}")
    print(f"BUILT_IN:  {listed}")
    added, dropped = sorted(installed.names - guard.BUILT_IN), sorted(guard.BUILT_IN - installed.names)
    if added:
        print(f"registered and not in BUILT_IN, add to {GUARD}: {', '.join(added)}")
    if dropped:
        print(f"in BUILT_IN and registered no more: {', '.join(dropped)}")
    return DIFFER if added or dropped else AGREE


if __name__ == "__main__":
    sys.exit(main())
