"""The writer table: each command that writes the paths its words name, one row a writer, every field read from that
writer's --help on this machine (GNU coreutils 9.10 for tee, cp, mv, install, rm, unlink, shred, touch, mkdir,
truncate and dd, GNU sed 4.9, GNU Awk 5.3.2, which awk runs here, and perl 5.40.1's -h, perl reading -I's directory
from the next word too).

A row says how the writer's words are read, so the reader (dispatcher/writes.py) names no writer: how it writes each
file operand; the options taking a value, given after `=`, attached to a short letter or as the next word; the short
options whose value is optional, the rest of their word; the options that make it write each file operand as they say
instead, where their value is the one they name; the options giving its program, its first operand being the program
where none is given; whether its options end at its first operand, as perl's and gawk's do, rather than anywhere
before `--`; the options taking the folder it copies or moves its operands into, its last operand being where it
copies or moves the others where none is given, and whether it removes what it moves; the `KEY=` an operand begins
with to name a path it writes; and whether an operand NAME=VALUE is an assignment rather than a file. An option the
row does not name is read as a flag, as is a long option whose value is optional, given only after `=`.
"""

from collections.abc import Mapping
from typing import Literal, NamedTuple

from dispatcher.tree import ShellText
from dispatcher.wrappers import named

Kind = Literal["created", "appended", "edited", "removed", "destination", "moved"]
"""How a path is written: created or truncated, appended to, edited in place, removed, made a copy's or a move's
destination, or moved away as a move's source."""


class Switch(NamedTuple):
    """An option that makes a writer write each file operand as ``kind``, where its value is ``value``, any where
    None."""

    kind: Kind
    value: ShellText | None = None


def switching(spelled: ShellText, switch: Switch) -> dict[ShellText, Switch]:
    """The options a space-separated list names, each the switch ``switch``."""
    return dict.fromkeys(named(spelled), switch)


class Writer(NamedTuple):
    """How a writer writes the paths its words name, each field as the module docstring orders them."""

    kind: Kind | None = None
    valued: frozenset[ShellText] = frozenset()
    optional: frozenset[ShellText] = frozenset()
    switches: Mapping[ShellText, Switch] = {}
    programs: frozenset[ShellText] | None = None
    ordered: bool = False
    targets: frozenset[ShellText] = frozenset()
    moves: bool = False
    keyed: Mapping[ShellText, Kind] = {}
    assigns: bool = False


EDITED = Switch("edited")
SED_PROGRAMS = named(ShellText("-e --expression -f --file"))
PERL_PROGRAMS = named(ShellText("-e -E"))
GAWK_PROGRAMS = named(ShellText("-f --file -e --source -E --exec"))
TARGETS = named(ShellText("-t --target-directory"))
SUFFIX = named(ShellText("-S --suffix"))
GAWK = Writer(valued=GAWK_PROGRAMS | named(ShellText("-F --field-separator -v --assign -i --include -l --load")),
              optional=named(ShellText("-d -D -L -o -p")),
              switches=switching(ShellText("-i --include"), Switch("edited", ShellText("inplace"))),
              programs=GAWK_PROGRAMS, ordered=True, assigns=True)

WRITERS: dict[ShellText, Writer] = {
    ShellText("sed"): Writer(valued=SED_PROGRAMS | named(ShellText("-l --line-length")), optional=named(ShellText("-i")),
                             switches=switching(ShellText("-i --in-place"), EDITED), programs=SED_PROGRAMS),
    ShellText("perl"): Writer(valued=PERL_PROGRAMS | named(ShellText("-I")),
                              optional=named(ShellText("-0 -C -d -D -F -i -l -m -M -V -x")),
                              switches=switching(ShellText("-i"), EDITED), programs=PERL_PROGRAMS, ordered=True),
    ShellText("gawk"): GAWK,
    ShellText("awk"): GAWK,
    ShellText("tee"): Writer("created", switches=switching(ShellText("-a --append"), Switch("appended"))),
    ShellText("cp"): Writer(valued=SUFFIX | named(ShellText("--no-preserve --sparse")), targets=TARGETS),
    ShellText("mv"): Writer(valued=SUFFIX, targets=TARGETS, moves=True),
    ShellText("install"): Writer(valued=SUFFIX | named(ShellText("-g --group -m --mode -o --owner --strip-program")),
                                 switches=switching(ShellText("-d --directory"), Switch("created")), targets=TARGETS),
    ShellText("rm"): Writer("removed"),
    ShellText("unlink"): Writer("removed"),
    ShellText("shred"): Writer("edited", named(ShellText("-n --iterations --random-source -s --size")),
                               switches=switching(ShellText("-u --remove"), Switch("removed"))),
    ShellText("touch"): Writer("created", named(ShellText("-d --date -r --reference -t --time"))),
    ShellText("mkdir"): Writer("created", named(ShellText("-m --mode"))),
    ShellText("truncate"): Writer("created", named(ShellText("-r --reference -s --size"))),
    ShellText("dd"): Writer(keyed={ShellText("of="): "created"}),
}
