"""The paths a command of the call model writes, each a Write: the files its streams end in where they are opened for
writing, appending or update (dispatcher/model.py's End); the files its own redirections open so on any descriptor,
each as bash opens it whether or not a later redirection takes its descriptor over; and the paths its words name as
its row of the writer table (dispatcher/write_table.py) says, the row its name names by the part after its last `/`,
read here without naming any writer.

A command's words past its name are read as GNU's getopt reads them, by the part of each the call makes known (its
prefix): a word whose prefix does not begin with `-`, or that is `-`, is an operand, and so is every word after `--`,
or after the first operand where the row says its options end there; a word of two dashes is a long option named up
to its `=`, its value after it or, where the row says it takes one, the next word, a flag of no name where its name is
not known whole; any other is a cluster of short letters read up to the end of its prefix, the first letter taking a
value, or an optional one, ending it with the rest of the word, the next word given where no rest follows and the value
is not optional. A path's word is the operand naming it, or the word an option's `=`, short letter or `KEY=` begins,
as written, its value and prefix cut past them.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from os.path import join, normpath
from typing import NamedTuple, NewType

from dispatcher.model import APPENDS, DESCRIPTOR, MOVED, WRITES, Command, Opening, Redirect, Word, named_descriptor
from dispatcher.scripts import ScriptPath
from dispatcher.tree import Count, ShellText
from dispatcher.words import Directory
from dispatcher.write_table import WRITERS, Kind, Writer

FilePath = NewType("FilePath", str)
OPENED: Mapping[Opening, Kind] = {"write": "created", "append": "appended", "update": "edited"}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
SUBSTITUTION = ("<(", ">(")


class Write(NamedTuple):
    """A path a command writes: the word naming it, the absolute normalised path that word names from the command's
    directory, None where the call does not make it known, and how the command writes it."""

    word: Word
    path: FilePath | None
    kind: Kind


class Option(NamedTuple):
    """An option among a command's words: its name, and the word of its value, None where it has none."""

    name: ShellText
    value: Word | None


class Reading(NamedTuple):
    """A command's words past its name as its row reads them: its options in order, and its operands."""

    options: tuple[Option, ...]
    operands: tuple[Word, ...]


def writes(command: Command) -> tuple[Write, ...]:
    """Every path ``command`` writes, once each: its streams' first, then its own redirections', then its words'."""
    return tuple(dict.fromkeys((*streamed(command), *redirected(command), *worded(command))))


def write_of(word: Word, kind: Kind, cwd: Directory | None) -> Write:
    """The write of the path ``word`` names from ``cwd``."""
    value = word.value
    if value is None or (cwd is None and not value.startswith("/")):
        return Write(word, None, kind)
    return Write(word, FilePath(normpath(join(cwd or "/", value))), kind)


def streamed(command: Command) -> Iterator[Write]:
    """The files the command's streams end in, where they are opened for writing, appending or update."""
    for end in command.streams:
        kind = None if end.opening is None else OPENED.get(end.opening)
        if end.file is not None and kind is not None:
            yield write_of(end.file, kind, command.cwd)


def opens(redir: Redirect) -> Kind | None:
    """How a redirection writes the file its word names: `>`, `>|` and `&>` create it, `>>` and `&>>` append to it,
    `<>` edits it, and a `>&` naming no descriptor of its own nor any in its word creates it as `&>` does; None for
    any other."""
    value = redir.target.value
    if redir.op == ">&":
        copied = value is None or value == "-" or DESCRIPTOR.fullmatch(value) is not None
        return None if redir.fd is not None or copied or MOVED.fullmatch(value) is not None else "created"
    return "appended" if redir.op in APPENDS else "created" if redir.op in WRITES else "edited" if redir.op == "<>" else None


def redirected(command: Command) -> Iterator[Write]:
    """The files the command's own redirections open for writing, appending or update, on any descriptor: none for a
    process substitution, /dev/null or a path naming a descriptor."""
    for redir in command.redirs:
        kind = opens(redir)
        if kind is None or redir.target.text.startswith(SUBSTITUTION):
            continue
        write = write_of(redir.target, kind, command.cwd)
        if write.path is None or (write.path != "/dev/null" and named_descriptor(ScriptPath(write.path)) is None):
            yield write


def worded(command: Command) -> Iterator[Write]:
    """The paths the command's words name as its row of the writer table says, none where no row names it."""
    row = None if command.name is None else WRITERS.get(ShellText(command.name.rsplit("/", 1)[-1]))
    if row is not None:
        for word, kind in written(row, read(row, command.words[1:])):
            yield write_of(word, kind, command.cwd)


def cut(word: Word, count: Count) -> Word:
    """The part of ``word`` past its first ``count`` characters: the word as written, its value and prefix cut."""
    return Word(word.text, None if word.value is None else ShellText(word.value[count:]),
                None if word.prefix is None else ShellText(word.prefix[count:]))


def read(row: Writer, words: Sequence[Word]) -> Reading:
    """``words`` read by ``row`` as the module docstring says."""
    taking = row.valued | row.targets
    options: list[Option] = []
    operands: list[Word] = []
    rest = iter(words)
    ended = False
    for word in rest:
        known = word.prefix or ""
        if ended or not known.startswith("-") or word.value == "-":
            operands.append(word)
            ended = ended or row.ordered
        elif word.value == "--":
            ended = True
        elif known.startswith("--"):
            name, equals, _ = known.partition("=")
            if equals:
                options.append(Option(ShellText(name), cut(word, Count(len(name) + 1))))
            elif word.value is not None:
                options.append(Option(ShellText(name), next(rest, None) if name in taking else None))
        else:
            for at, letter in enumerate(known[1:], start=2):
                name = ShellText("-" + letter)
                if name in taking or name in row.optional:
                    attached = at < len(known) or word.value is None or name in row.optional
                    options.append(Option(name, cut(word, Count(at)) if attached else next(rest, None)))
                    break
                options.append(Option(name, None))
    return Reading(tuple(options), tuple(operands))


def written(row: Writer, reading: Reading) -> Iterator[tuple[Word, Kind]]:
    """The paths ``reading`` names, each with how ``row``'s writer writes it: the value of each `KEY=` operand the row
    keys; each file operand as the last switch given says, else as the row's kind; or, for a row with targets, the
    last target option's value or else the last of two operands or more as the destination, the other operands moved
    where the row moves them."""
    given = {option.name for option in reading.options}
    files = reading.operands[1:] if row.programs is not None and not given & row.programs else reading.operands
    files = tuple(word for word in files if not (row.assigns and ASSIGNMENT.match(word.prefix or "")))
    for word in files:
        for key, kind in row.keyed.items():
            if (word.prefix or "").startswith(key):
                yield cut(word, Count(len(key))), kind
    switched = [switch.kind for option in reading.options if (switch := row.switches.get(option.name)) is not None
                and (switch.value is None or (option.value is not None and option.value.value == switch.value))]
    targets = [option.value for option in reading.options if option.name in row.targets and option.value is not None]
    if switched:
        yield from ((word, switched[-1]) for word in files)
    elif row.targets and (targets or len(files) > 1):
        destination, sources = (targets[-1], files) if targets else (files[-1], files[:-1])
        yield destination, "destination"
        yield from ((word, "moved") for word in sources if row.moves)
    elif row.kind is not None:
        yield from ((word, row.kind) for word in files)
