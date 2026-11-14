"""A recursive search never starts at a run-record folder or at a folder holding one.

THE RULE (user, 2026-11-04; made blunt 2026-11-09). A run record holds gigabytes, and a recursive search reads it whole
before a filter after it sees a line: `grep -rn X python tools | grep -v ci-output` read the 82 GB under tools/ci-output,
and systemd-oomd killed unrelated calls. So a Bash or Monitor call is refused when a command of its call model searches
recursively from a start path whose folder is a run-record folder declared for that path, or holds one. A start path
whose folder the call does not make known is refused wherever a record is declared for the command's directory. What a
search skips is not read: --exclude-dir, -g, ignore files, -prune and -maxdepth change nothing. A search beneath a
record, in one run's folder, reads that folder alone and passes.

A recursive search is a grep, egrep or fgrep with a word before `--` that is -r, -R, --recursive,
--dereference-recursive, --directories=recurse, -d followed by recurse, or a single-dash bundle holding r or R; an
rgrep; an rg; or a find whose -exec, -execdir, -ok or -okdir runs a program outside NAME_ONLY, or which an xargs in a
later element of its pipeline feeds to such a program. A word is read by its start the call makes known (Word.prefix).
A grep's or rg's start paths are its operands past its pattern, the first operand unless a word before `--` is
--regexp, --file or a single-dash bundle holding e or f; a find's are its words past its -H, -L, -P, -D and -O options
up to its first word led by `-` or that is `(`, `!` or `,`; none means the command's directory. A path's folder is the
one Word.folder gives, and the records declared for it are its repository's and the machine's (declarations/lookup.py),
both with symbolic links resolved.

EXCEPTIONS
  * An rg with no start path passes where a pipe, a heredoc or here-string, or a file the call names feeds its input,
    since it then reads that input and no folder: `cmd | rg x` is the commonest filter. Fed nothing, /dev/null, a
    substitution or a file the call does not make known, it searches its directory as any other rg does.
"""

import os
from collections.abc import Sequence
from itertools import pairwise, takewhile
from pathlib import Path

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import FIND_ACTIONS, Command, Reach, Word
from dispatcher.tree import Count, ShellText
from dispatcher.verdict import Prose, Verdict, refuse

type Names = frozenset[ShellText]

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})


def names(spelled: Prose) -> Names:
    """The names ``spelled`` holds, split at spaces."""
    return frozenset(ShellText(name) for name in spelled.split())


GREPS = names(Prose("grep egrep fgrep"))
ALWAYS = names(Prose("rg rgrep"))
RECURSIVE = names(Prose("--recursive --dereference-recursive --directories=recurse"))
PATTERN_GIVEN = names(Prose("--regexp --file"))
NAME_ONLY = names(Prose("ls eza stat echo printf basename dirname realpath readlink du test [ true false rm rmdir mv "
                        "ln touch chmod chown mkdir"))
FIND_OPTIONS = ("-H", "-L", "-P", "-D", "-O")
EXPRESSION = names(Prose("( ! ,"))
READ_INPUT: frozenset[Reach] = frozenset({"pipe", "text"})
HERE = Word(ShellText("."), ShellText("."), ShellText("."))
SPELLED_MAX = 120

ADVICE = """a recursive search starts at a run-record folder or at a folder holding one.

{problems}

A run record holds gigabytes, and a recursive search reads it whole before a filter after it sees a line: one such
grep read the 82 GB under tools/ci-output and systemd-oomd killed unrelated calls. What a search skips is not read here
(--exclude-dir, -g, ignore files, -prune, -maxdepth). These pass:
  * a narrower path: one run's folder beneath the record, or a folder beside it;
  * git grep -n PATTERN -- PATHS;
  * rg -n PATTERN PATHS over named trees that hold no record."""


def base(word: Word) -> ShellText | None:
    """A command word's program name, its folder dropped; None where the call does not make it known."""
    return None if word.value is None else ShellText(word.value.rsplit("/", 1)[-1])


def split(words: Sequence[Word]) -> tuple[list[Word], list[Word]]:
    """A command's words past its name before `--`, and those after it."""
    end = next((index for index, word in enumerate(words) if word.value == "--"), len(words))
    return list(words[1:end]), list(words[end + 1:])


def heads(words: Sequence[Word]) -> list[ShellText]:
    """The start the call makes known of each word past a command's name before `--`."""
    return [word.prefix or ShellText("") for word in split(words)[0]]


def bundle(head: ShellText, letters: Prose) -> bool:
    """Whether ``head`` is a single-dash bundle holding one of ``letters``."""
    return head.startswith("-") and not head.startswith("--") and any(letter in head for letter in letters)


def recursive_grep(words: Sequence[Word]) -> bool:
    """Whether a grep's words before `--` make it recurse."""
    said = heads(words)
    return any(head in RECURSIVE or bundle(head, Prose("rR")) for head in said) or ("-d", "recurse") in pairwise(said)


def operand_paths(words: Sequence[Word]) -> list[Word]:
    """A grep's or an rg's start paths: its operands past its pattern, the first operand unless a word before `--`
    gave one."""
    before, after = split(words)
    operands = [word for word in before if not (word.prefix or "").startswith("-") or word.value == "-"] + after
    given = any(head.split("=")[0] in PATTERN_GIVEN or bundle(head, Prose("ef")) for head in heads(words))
    return operands if given else operands[1:]


def find_paths(words: Sequence[Word]) -> list[Word]:
    """A find's start paths: its words past its leading options, -D with its value, up to its first word led by `-`
    or that is `(`, `!` or `,`."""
    at = 1
    while at < len(words) and (words[at].value or "").startswith(FIND_OPTIONS):
        at += 2 if words[at].value == "-D" else 1
    return list(takewhile(lambda word: not (word.prefix or "").startswith("-") and word.value not in EXPRESSION,
                          words[at:]))


def reads_contents(words: Sequence[Word]) -> bool:
    """Whether ``words`` run a program that may read the files it is given: any but a NAME_ONLY one, and one the
    call does not show."""
    return not words or base(words[0]) not in NAME_ONLY


def find_reads(commands: Sequence[Command], index: Count) -> bool:
    """Whether the find ``commands[index]`` hands what it walks to a program reading contents: one an action of its
    runs, or one an xargs in a later element of a pipeline it is in runs."""
    words = commands[index].words
    if any(word.value in FIND_ACTIONS and reads_contents(words[at + 1:at + 2]) for at, word in enumerate(words)):
        return True
    seats = commands[index].pipelines
    return any("xargs" in later.wrappers and reads_contents(later.words)
               and any(theirs.pipeline == mine.pipeline and theirs.position > mine.position
                       for mine in seats for theirs in later.pipelines)
               for later in commands[index + 1:])


def reads_input(command: Command) -> bool:
    """Whether an rg's input is a pipe, a heredoc or here-string, or a file the call names."""
    fed = command.streams.input
    return fed.reach in READ_INPUT or (fed.file is not None and fed.file.value is not None)


def start_paths(commands: Sequence[Command], index: Count) -> list[Word]:
    """Where ``commands[index]`` searches recursively from, HERE where it names nothing; none where it does not
    search recursively."""
    command = commands[index]
    words = command.words
    name = base(words[0]) if words else None
    if name == "find":
        return (find_paths(words) or [HERE]) if find_reads(commands, index) else []
    if name not in ALWAYS and not (name in GREPS and recursive_grep(words)):
        return []
    paths = operand_paths(words)
    if not paths and name == "rg" and reads_input(command):
        return []
    return paths or [HERE]


def records(event: Event, folder: Path) -> list[Path]:
    """Every run-record folder declared for ``folder``, symbolic links resolved."""
    return [Path(os.path.realpath(record)) for record in event.declaration(folder).records]


def spelled(words: Sequence[Word]) -> Prose:
    """A command as a refusal quotes it, cut at SPELLED_MAX characters."""
    said = " ".join(word.spelled for word in words)
    return Prose(said if len(said) <= SPELLED_MAX else said[:SPELLED_MAX - 3] + "...")


def problems(event: Event, command: Command, paths: Sequence[Word]) -> list[Prose]:
    """A line for each record a start path's folder is or holds, or, where the call does not make that folder known,
    for each record declared for the command's directory."""
    said = spelled(command.words)
    found: list[Prose] = []
    for path in paths:
        folder = path.folder(command.cwd)
        if folder is None:
            here = Path(command.cwd or event.cwd or "/")
            found += [Prose(f"  * `{said}` starts at `{path.spelled}`, a folder this call does not make known, while "
                            f"the run record {record} is declared for {here}") for record in records(event, here)]
        else:
            start = Path(os.path.realpath(folder))
            found += [Prose(f"  * `{said}` starts at `{path.spelled}`, which is or holds the run record {record}")
                      for record in records(event, start) if record == start or start in record.parents]
    return found


def judge(event: Event) -> Verdict | None:
    """Refuse a call a command of which searches recursively from a run-record folder or a folder holding one."""
    commands = event.call().commands
    found = [line for index in range(len(commands))
             for line in problems(event, commands[index], start_paths(commands, Count(index)))]
    if not found:
        return None
    return refuse(Prose(ADVICE.format(problems="\n".join(dict.fromkeys(found)))))
