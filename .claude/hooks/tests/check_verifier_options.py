#!/home/nicolas/.local/bin/python3.14
"""Compare merge-dribble-guard.py's reading of pr-all-green's options with pr-all-green's own parser.

The guard reads a merge, `pr-all-green N --head SHA --squash-with-body FILE`, as pr-all-green's argparse reads it:
its long options (VerifierOption) and those taking a value (TAKES_VALUE). An option the tool has and the guard
lacks is read by the guard as no option and its value as a positional word, which may be taken for the pull
request; a prefix the guard resolves to one option may be ambiguous to the tool. So the two must name the same
options. Run it whenever either file changes.

Usage: check_verifier_options.py [GUARD] [TOOL]
  GUARD  merge-dribble-guard.py, or a hooks directory holding it (default: ..)
  TOOL   pr-all-green, or a directory holding it (default: ~/.local/bin/pr-all-green)
Exit 0 when the two agree; 1 when they differ.
"""

import argparse
import importlib.machinery
import importlib.util
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import NewType, TypeAliasType, cast, get_args

ExitStatus = NewType("ExitStatus", int)
Option = NewType("Option", str)
ModuleName = NewType("ModuleName", str)
Metavar = NewType("Metavar", str)
AGREE, DIFFER = ExitStatus(0), ExitStatus(1)
# A long option in argparse's help, and the metavar it shows when it takes a value.
OPTION = re.compile(r"(--[a-z][a-z-]*)(?: ([A-Z][A-Z_]*))?")

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "merge-dribble-guard.py" if GIVEN.is_dir() else GIVEN
TOLD = Path(sys.argv[2] if len(sys.argv) > 2 else Path.home() / ".local/bin/pr-all-green").resolve()
TOOL = TOLD / "pr-all-green" if TOLD.is_dir() else TOLD


def load(name: ModuleName, path: Path) -> ModuleType:
    """The module at ``path``, its own directory first on sys.path for the modules it imports."""
    sys.path.insert(0, str(path.parent))
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main() -> ExitStatus:
    """Print each side's options and every difference."""
    sys.dont_write_bytecode = True
    guard, tool = load(ModuleName("merge_dribble_guard"), GUARD), load(ModuleName("pr_all_green"), TOOL)
    parser = cast("Callable[[], argparse.ArgumentParser]", tool.parser)
    alias = cast("TypeAliasType", guard.VerifierOption)
    help_text = parser().format_help()
    listed = [cast("list[tuple[Option, Metavar]]", OPTION.findall(line))
              for line in help_text.partition("\noptions:\n")[2].splitlines()]
    long_tool = {name for found in listed for name, _ in found}
    value_tool = {name for found in listed for name, metavar in found if metavar}
    long_guard = set(cast("tuple[Option, ...]", get_args(alias.__value__)))
    value_guard = set(cast("frozenset[Option]", guard.TAKES_VALUE))
    print(f"{TOOL}: {', '.join(sorted(long_tool))}; taking a value: {', '.join(sorted(value_tool))}")
    print(f"{GUARD}: {', '.join(sorted(long_guard))}; taking a value: {', '.join(sorted(value_guard))}")
    problems = [f"the tool has {o} and the guard does not" for o in sorted(long_tool - long_guard)]
    problems += [f"the guard has {o} and the tool does not" for o in sorted(long_guard - long_tool)]
    problems += [f"{o} takes a value in one and not the other" for o in sorted(value_tool ^ value_guard)]
    for line in problems:
        print(f"DIFFER: {line}")
    return DIFFER if problems else AGREE


if __name__ == "__main__":
    sys.exit(main())
