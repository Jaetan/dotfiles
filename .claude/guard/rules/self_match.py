"""A process is never looked up in a way that can find the call's own shell, so a lookup never finds the call itself,
a job that ended never reads as running and pkill never kills the call.

THE RULE (user, 2026-11-09). The Bash tool runs a call, and the Monitor tool a script, as `bash -c LINE` with the
whole text in LINE, so a lookup matching whole command lines can find that shell, whatever its pattern. A command of
the call (the call model's commands: wrappers peeled, carriers, scripts and Python starts as the model holds them)
that is pgrep, pkill or pidwait by its base name, any version suffix, is refused where a word before `--` is -f,
--full, a single-dash bundle holding f, or a word the call does not make known, and no word before `--` is -A,
--ignore-ancestors or a single-dash bundle holding A, which leaves the lookup's ancestors out. A pipeline one element
of which runs ps by its base name and a later element of which runs grep, egrep, fgrep, rg, awk, gawk or mawk is
refused whatever its patterns.

EXCEPTIONS
  * -A passes no lookup that is a pipeline element, nor one in a call that starts a background job or a coprocess
    anywhere: bash runs a backgrounded group, loop or compound pipeline element in a forked copy of the call's shell,
    which carries the call's line and is no ancestor of the lookup.
"""

import re
from collections.abc import Sequence
from typing import Literal

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import Command, Word
from dispatcher.tree import ShellText
from dispatcher.verdict import Prose, Verdict, refuse

type Letter = Literal["f", "A"]
type LongName = Literal["--full", "--ignore-ancestors"]
type Values = list[ShellText | None]

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
LOOKUP = re.compile(r"(?:pgrep|pkill|pidwait)(?:-?[0-9]+(?:\.[0-9]+)*)?")
PS = ShellText("ps")
FILTERS = frozenset(ShellText(name) for name in ("grep", "egrep", "fgrep", "rg", "awk", "gawk", "mawk"))
WHOLE = "it matches whole command lines (-f, or a word this call does not make known) without -A"
FORKED = "under -A it is a pipeline element or this call starts a background job, whose forked shell -A keeps"
FILTERED = "a grep, rg or awk after it in its pipeline reads the line ps prints for this call's own shell"
ADVICE = """this call looks a process up in a way that can find its own shell.

{problems}

The Bash tool runs a call, and Monitor a script, as one shell whose command line holds the whole text, so pgrep,
pkill or pidwait matching whole command lines, or ps piped into grep, rg or awk, can find that shell: a job that
ended reads as running, pidwait waits on itself and pkill kills the call. These pass:

  * `pgrep -A -f PATTERN`, which leaves the call's own shells out, outside a pipeline in a call that starts no
    background job;
  * `pgrep -x NAME`, which matches the process name alone;
  * a pid, from `$!` or a run_in_background notification, tested with `kill -0 PID`;
  * ps with no filter after it in its pipeline."""


def base(command: Command) -> ShellText | None:
    """The base name of the program the command runs, None where the call does not make it known."""
    return None if command.name is None else ShellText(command.name.rsplit("/", 1)[-1])


def options(words: Sequence[Word]) -> Values:
    """The values of the words after the program up to `--`, None for a word the call does not make known."""
    values: Values = [word.value for word in words[1:]]
    return values[:values.index(ShellText("--"))] if ShellText("--") in values else values


def sets(values: Values, letter: Letter, long: LongName) -> bool:
    """Whether a value is ``long`` or a single-dash bundle holding ``letter``."""
    return any(value == long or (value is not None and value[:1] == "-" and value[1:2] != "-" and letter in value)
               for value in values)


def lookup(command: Command, commands: Sequence[Command]) -> Prose | None:
    """The refusal's line for a pgrep, pkill or pidwait that can find the call's own shell."""
    name = base(command)
    if name is None or LOOKUP.fullmatch(name) is None:
        return None
    values = options(command.words)
    if None not in values and not sets(values, "f", "--full"):
        return None
    if not sets(values, "A", "--ignore-ancestors"):
        return Prose(f"  * `{spelled(command)}`: {WHOLE}")
    if command.pipelines or any("background" in other.shells for other in commands):
        return Prose(f"  * `{spelled(command)}`: {FORKED}")
    return None


def listing(command: Command, commands: Sequence[Command]) -> Prose | None:
    """The refusal's line for a ps a later element of a pipeline it is in filters."""
    if base(command) != PS:
        return None
    if not any(base(other) in FILTERS and seat.pipeline == own.pipeline and seat.position > own.position
               for other in commands for seat in other.pipelines for own in command.pipelines):
        return None
    return Prose(f"  * `{spelled(command)}`: {FILTERED}")


def spelled(command: Command) -> ShellText:
    """The command's words as the call spells them."""
    return ShellText(" ".join(word.spelled for word in command.words))


def judge(event: Event) -> Verdict | None:
    """Refuse a Bash or Monitor call that looks a process up in a way that can find its own shell."""
    commands = event.call().commands
    found = [line for command in commands
             if (line := lookup(command, commands) or listing(command, commands)) is not None]
    if not found:
        return None
    return refuse(Prose(ADVICE.format(problems="\n".join(found))))
