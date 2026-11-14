"""Whether git ignores a repository's declaration, which no clone, branch or review then carries.

hidden(FACTS) gives the ignore rule hiding the declaration FACTS came from (lookup.py), None where no declaration
holds or git does not ignore it. git answers, `git check-ignore -v` run in the work tree holding the file with its
index read, so a declaration git tracks is carried whatever rule matches it, and a match of a negated rule
(`!PATTERN`) is one git re-includes. git that cannot run, outruns GIT_TIMEOUT or gives an answer this does not read
is a KitFault.
"""

import os
import subprocess
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher.verdict import KitFault, Prose

from declarations.lookup import CLEARED, DECLARATION, GIT_TIMEOUT, Facts

LineNumber = NewType("LineNumber", int)
Pattern = NewType("Pattern", str)
SAID = 5  # -z closes the ignore file, the line, the pattern and the path each by a NUL, an empty text after


class Hiding(NamedTuple):
    """An ignored declaration and the rule hiding it: the ignore file, the rule's line there and its pattern."""

    declaration: Path
    source: Path
    line: LineNumber
    pattern: Pattern


def hidden(facts: Facts) -> Hiding | None:
    """The rule hiding the declaration of ``facts`` from git, None where there is none or git keeps it."""
    file = facts.declaration
    if file is None:
        return None
    tree = file.parent.parent
    environ = {name: value for name, value in os.environ.items() if name not in CLEARED}
    try:
        done = subprocess.run(["git", "-C", str(tree), "check-ignore", "-v", "-z", "--stdin"],
                              input=bytes(DECLARATION) + b"\0", capture_output=True, env=environ,
                              timeout=GIT_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise KitFault(Prose(f"git outran its {GIT_TIMEOUT:g} s limit saying whether it ignores {file}")) from None
    except OSError as fault:
        raise KitFault(Prose(f"git cannot run: {fault.strerror}")) from None
    if done.returncode == 1:
        return None
    said = done.stdout.decode("utf-8", "surrogateescape").split("\0")
    if done.returncode != 0 or len(said) != SAID or not said[1].isdecimal():
        why = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise KitFault(Prose(f"git cannot say whether it ignores {file}: {why[0] if why else 'no answer it reads'}"))
    source, line, pattern, _, _ = said
    if pattern.startswith("!"):
        return None
    return Hiding(file, tree / source, LineNumber(int(line)), Pattern(pattern))
