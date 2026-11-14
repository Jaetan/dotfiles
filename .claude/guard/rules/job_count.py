"""A tool of the job-count table runs only with a literal job count on its own command line.

THE RULE (user, 2026-10-10; table-only, 2026-11-09). taskset and claude.slice's AllowedCPUs bound where a call's
threads run, not how many jobs a tool starts: on 2026-10-10 four run-clang-tidy at 24 jobs each held claude.slice at
its memory cap in reclaim for 70 minutes. A Bash or Monitor call is refused when a command of its call model (wrappers
peeled, carriers and scripts read, or run by a Python interpreter as its module or script) is a tool of the table
below by base name, unless the last word of it in one of that tool's forms names a count from 1 to the machine's job
bound (Machine.job_bound, the count of the agents' CPUs) in digits. A count the call does not make known is none. ctest,
make and gmake run one job when given no count, so they pass where no word of theirs may name a job option (a word
starting --jobs or --parallel, a single-dash word holding j, or one whose start the call does not make known). A
machine declaring no agents' CPUs bounds nothing. Nothing else is read: no environment, no letter bundle, no
abbreviated option.

  run-clang-tidy  -j N, -jN, -j=N                         also run-clang-tidy-23 and run-clang-tidy.py
  mutmut run      --max-children N, --max-children=N      mutmut with a later word `run`
  ctest           -j N, -jN, -j=N, --parallel N, --parallel=N
  make, gmake     -j N, -jN, --jobs N, --jobs=N
  ninja           -j N, -jN

EXCEPTIONS (2), each a call that starts no job and would be refused without it:
  a tool given -h or --help prints its help;
  a ninja given -t, which runs the subtool named after it, or --version.
"""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import PYTHON_NAME, Word
from dispatcher.python import invoked
from dispatcher.tree import Count, ShellText
from dispatcher.verdict import Prose, Verdict, refuse

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
RUN_CLANG_TIDY = re.compile(r"run-clang-tidy(-[0-9]+)?(\.py)?")
ADVICE = """this call starts a tool without a literal job count from 1 to the agents' {bound} CPUs.

{problems}

taskset and claude.slice's AllowedCPUs bound where a call's threads run, not how many jobs a tool starts. Write each
tool's count on its own command line, N a number from 1 to {bound} shared with what runs beside it."""


class Forms(NamedTuple):
    """A tool's job-count forms: the options whose next word is the count, the starts a count follows in one word,
    the words that make it start no job, and whether it runs one job when given no count."""

    apart: tuple[ShellText, ...]
    joined: tuple[ShellText, ...]
    no_job: tuple[ShellText, ...]
    one_job: bool


class Named(NamedTuple):
    """The last job-count word of a command as written, and the count it names, None where it names none."""

    spelled: ShellText
    count: Count | None


def spellings(spelled: ShellText) -> tuple[ShellText, ...]:
    """The words a space-separated list holds, in order."""
    return tuple(ShellText(word) for word in spelled.split())


def forms(apart: ShellText, joined: ShellText, no_job: ShellText = ShellText("-h --help"), one_job: bool = False) -> Forms:
    """The forms the space-separated lists spell, and whether the tool runs one job when given no count."""
    return Forms(spellings(apart), spellings(joined), spellings(no_job), one_job)


MAKE = forms(ShellText("-j --jobs"), ShellText("-j --jobs="), one_job=True)
TABLE: dict[ShellText, Forms] = {
    ShellText("run-clang-tidy"): forms(ShellText("-j"), ShellText("-j= -j")),
    ShellText("mutmut"): forms(ShellText("--max-children"), ShellText("--max-children=")),
    ShellText("ctest"): forms(ShellText("-j --parallel"), ShellText("-j= -j --parallel="), one_job=True),
    ShellText("make"): MAKE,
    ShellText("gmake"): MAKE,
    ShellText("ninja"): forms(ShellText("-j"), ShellText("-j"), ShellText("-h --help -t --version")),
}


def base(word: Word) -> ShellText:
    """The base name ``word`` spells, empty where the call does not make it known."""
    return ShellText((word.value or "").rsplit("/", 1)[-1])


def program(words: tuple[Word, ...]) -> tuple[Word, ...]:
    """The words of the program ``words`` run: past a Python interpreter, its script or -m module first."""
    run = invoked([word.value for word in words]) if words and PYTHON_NAME.fullmatch(base(words[0])) else None
    if run is None or run.kind not in ("script", "module"):
        return words
    head = words[run.index]
    if head.value is not None and run.offset:
        module = ShellText(head.value[run.offset:])
        head = Word(module, module, module)
    return (head, *words[run.index + 1:])


def tool_of(words: tuple[Word, ...]) -> Forms | None:
    """The forms of the table's tool ``words`` run, None where they run none of its tools."""
    name = base(words[0]) if words else ShellText("")
    name = ShellText("run-clang-tidy") if RUN_CLANG_TIDY.fullmatch(name) else name
    if name == "mutmut" and all(word.value != "run" for word in words[1:]):
        return None
    return TABLE.get(name)


def literal(value: ShellText | None) -> Count | None:
    """The count ``value`` spells in digits, None where it spells none or the call does not make it known."""
    return Count(int(value)) if value is not None and value.isascii() and value.isdigit() else None


def last_named(words: Sequence[Word], tool: Forms) -> Named | None:
    """The last word of ``words`` in one of ``tool``'s forms with the count it names, None where none is."""
    found: Named | None = None
    for index, word in enumerate(words):
        if word.value in tool.apart:
            after = words[index + 1] if index + 1 < len(words) else None
            spelled = word.value if after is None else ShellText(f"{word.value} {after.spelled}")
            found = Named(spelled, None if after is None else literal(after.value))
            continue
        known = word.value if word.value is not None else word.prefix or ""
        start = next((start for start in tool.joined if known.startswith(start)), None)
        if start is not None:
            found = Named(word.spelled, None if word.value is None else literal(ShellText(word.value[len(start):])))
    return found


def may_count(word: Word) -> bool:
    """Whether ``word`` may name a job option: it starts --jobs or --parallel, is a single-dash word holding j, or
    the call does not make its start known."""
    known = word.value if word.value is not None else word.prefix or ""
    return (not known or known.startswith(("--jobs", "--parallel"))
            or (known.startswith("-") and not known.startswith("--") and "j" in known))


def problem(words: tuple[Word, ...], tool: Forms, bound: Count) -> Prose | None:
    """The refusal's line for the command running ``words``, None where it starts no job or names a count within."""
    if any(word.value in tool.no_job for word in words[1:]):
        return None
    if tool.one_job and not any(may_count(word) for word in words[1:]):
        return None
    named = last_named(words, tool)
    if named is not None and named.count is not None and 1 <= named.count <= bound:
        return None
    said = "with no job count" if named is None else f"with `{named.spelled}`"
    return Prose(f"  * `{words[0].spelled}` {said}: name `{tool.apart[0]} N`, N from 1 to {bound}")


def judge(event: Event) -> Verdict | None:
    """Refuse a call starting a tool of the table without a literal count from 1 to the machine's job bound."""
    bound = event.declaration(Path("/")).machine.job_bound
    if bound is None:
        return None
    lines = [line for command in event.call().commands
             if (tool := tool_of(words := program(command.words))) is not None
             and (line := problem(words, tool, bound)) is not None]
    if not lines:
        return None
    return refuse(Prose(ADVICE.format(bound=bound, problems="\n".join(lines))))
