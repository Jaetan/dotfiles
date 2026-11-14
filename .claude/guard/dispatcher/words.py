"""What the call itself makes known: a word's value as its command receives it, and the state a shell holds.

A word has a value when every part of it is known from the call's own text: literals with their quoting removed,
a parameter the call assigned a known value or the environment holds, `$PWD` where the directory is known, and a
leading `~` or `~/`. Anything else is unknown, None: a substitution, arithmetic, a glob or brace pattern, an ANSI-C
string with an escape, a parameter operator, an unquoted value that splitting or globbing could change. A glob is an
unquoted `*` or `?`, or an unquoted `[` that a `]` follows later in its word: an unescaped one in the same text, one
anywhere in a later literal, or any later part neither literal nor quoted; bash reads any other `[` as itself.

A word's prefix is its value up to the first part or character the shell would expand: the value itself where
known, else the known parts from the start, cut where a literal's glob starts or at its first unquoted `{`, and at
the first part the call does not make known. It is None where nothing known comes before a brace or such a part, since
either may begin with a `/`; a glob's matches stay in the folder its prefix names.

A State is one shell's directory and parameters. A subshell starts with a copy, a carrier with the directory alone;
where control flow joins, a value the paths disagree on becomes unknown.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Literal, NewType

from dispatcher.tree import Node, NodeType, ShellText

VarName = NewType("VarName", str)
Directory = NewType("Directory", str)
Offset = NewType("Offset", int)
"""A character's position in a text, counted from 0."""
type Environment = Mapping[VarName, ShellText]
type Cut = Literal["glob", "expansion"]

SPLIT = frozenset(" \t\n")
QUOTED: frozenset[NodeType] = frozenset({"single", "double", "ansi_c"})
ESCAPED = re.compile(r"\\.")
DOUBLE_ESCAPES = frozenset('$`"\\\n')


class State:
    """One shell's working directory and the parameters the call set in it, None where unknown."""

    __slots__ = ("cwd", "names")

    def __init__(self, cwd: Directory | None, names: dict[VarName, ShellText | None] | None = None) -> None:
        self.cwd = cwd
        self.names: dict[VarName, ShellText | None] = dict(names or {})

    def copy(self) -> "State":
        """A copy a subshell changes without touching this one."""
        return State(self.cwd, self.names)

    def become(self, other: "State") -> None:
        """Take ``other``'s directory and parameters."""
        self.cwd, self.names = other.cwd, dict(other.names)

    def same(self, other: "State") -> bool:
        """Whether ``other`` holds the same directory and parameters."""
        return self.cwd == other.cwd and self.names == other.names


def joined(states: Sequence[State]) -> State:
    """The state after paths that end in ``states``: what they agree on, and unknown where they differ."""
    first = states[0]
    cwd = first.cwd if all(s.cwd == first.cwd for s in states) else None
    names: dict[VarName, ShellText | None] = {}
    for name in dict.fromkeys(n for s in states for n in s.names):
        values = {s.names.get(name, ShellText("\0absent")) for s in states}
        names[name] = values.pop() if len(values) == 1 else None
    return State(cwd, names)


def lookup(name: VarName, state: State, env: Environment) -> ShellText | None:
    """A parameter's value: the call's own, `PWD` the shell's directory, then the environment; None if unknown."""
    if name in state.names:
        return state.names[name]
    if name == "PWD":
        return None if state.cwd is None else ShellText(state.cwd)
    return env.get(name)


def glob_at(text: ShellText, opened: bool = False, closed: bool = False, escapes: bool = False) -> Offset | None:
    """Where ``text`` starts a glob, as the module docstring describes: its first `*` or `?`, its first `[` a `]`
    follows in ``text`` or, ``closed``, in a later part, and 0 where it holds a `]` and, ``opened``, an earlier part
    may hold a `[`; None where it starts none. ``escapes`` makes a backslash quote the next character."""
    plain = ESCAPED.sub("  ", text) if escapes else text
    starts = [plain.index(char) for char in "*?" if char in plain]
    bracket = plain.find("[")
    if bracket >= 0 and (closed or "]" in plain[bracket:]):
        starts.append(bracket)
    if opened and "]" in plain:
        starts.append(0)
    starts = [Offset(start) for start in starts]
    return min(starts, default=None)


def closes(parts: Sequence[Node]) -> bool:
    """Whether ``parts``, unquoted after a `[` in its word, may hold the `]` that makes it a glob."""
    for part in parts:
        kind, raw = part.get("type"), part.get("value")
        if kind not in QUOTED and not (kind == "lit" and isinstance(raw, str) and "]" not in raw):
            return True
    return False


def unquoted(raw: ShellText, closed: bool) -> ShellText | None:
    """An unquoted literal with its backslashes removed, or None where it is a glob or a brace pattern; ``closed``
    where a later part may hold a `]`."""
    if glob_at(raw, closed=closed, escapes=True) is not None:
        return None
    out: list[ShellText] = []
    opened = False
    listed = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            if index + 1 < len(raw) and raw[index + 1] != "\n":
                out.append(ShellText(raw[index + 1]))
            index += 2
            continue
        if char == "{":
            opened, listed = True, False
        elif opened and (char == "," or raw.startswith("..", index)):
            listed = True
        elif char == "}" and opened and listed:
            return None
        out.append(ShellText(char))
        index += 1
    return ShellText("".join(out))


def in_double(raw: ShellText) -> ShellText:
    """A literal inside double quotes with the backslashes that escape there removed."""
    out: list[ShellText] = []
    index = 0
    while index < len(raw):
        if raw[index] == "\\" and index + 1 < len(raw) and raw[index + 1] in DOUBLE_ESCAPES:
            if raw[index + 1] != "\n":
                out.append(ShellText(raw[index + 1]))
            index += 2
            continue
        out.append(ShellText(raw[index]))
        index += 1
    return ShellText("".join(out))


def parameter(part: Node, state: State, env: Environment, quoted: bool, closed: bool = True) -> ShellText | None:
    """A plain `$name` or `${name}`'s value; None for any operator, index or special parameter, or where unquoted it
    splits or globs, ``closed`` where a later part may hold a `]`."""
    name = part.get("name")
    if (not isinstance(name, str) or not (name[:1].isalpha() or name[:1] == "_") or part.get("excl")
            or part.get("length") or part.get("index") or part.get("slice") or part.get("repl")
            or part.get("names") or part.get("exp")):
        return None
    value = lookup(VarName(name), state, env)
    if value is None or (not quoted and (SPLIT.intersection(value) or glob_at(value, closed=closed) is not None)):
        return None
    return value


def tilde(raw: ShellText, env: Environment) -> ShellText | None:
    """A leading `~` or `~/` expanded to $HOME; None for `~user`, `~+`, `~-` or an unknown home."""
    if raw != "~" and not raw.startswith("~/"):
        return None
    home = env.get(VarName("HOME"))
    return None if home is None else ShellText(home + raw[1:])


def part_value(part: Node, state: State, env: Environment, quoted: bool, closed: bool) -> ShellText | None:
    """One part of a word as the command receives it, or None; ``closed`` where a later part may hold a `]`."""
    kind = part.get("type")
    raw = part.get("value")
    if kind == "lit" and isinstance(raw, str):
        return in_double(raw) if quoted else unquoted(raw, closed)
    if kind == "single" and isinstance(raw, str):
        return raw
    if kind == "ansi_c" and isinstance(raw, str):
        return None if "\\" in raw else raw
    if kind == "double":
        return concatenated(part.get("parts", []), state, env, quoted=True)
    if kind == "param":
        return parameter(part, state, env, quoted, closed)
    return None


def concatenated(parts: Sequence[Node], state: State, env: Environment, quoted: bool) -> ShellText | None:
    """Parts joined, or None if one is unknown."""
    values: list[ShellText] = []
    for index, part in enumerate(parts):
        value = part_value(part, state, env, quoted, closes(parts[index + 1:]))
        if value is None:
            return None
        values.append(value)
    return ShellText("".join(values))


def after_tilde(parts: Sequence[Node], env: Environment) -> tuple[ShellText, list[Node]] | None:
    """A leading `~` or `~/` as the home folder and the parts after it, the literal's rest in place of the literal;
    no home and the parts unchanged where the word starts with no tilde bash expands, none or one whose text up to
    its first `/` holds a backslash or runs into a later part, left as written; None where its tilde is not known."""
    raw = parts[0].get("value") if parts and parts[0].get("type") == "lit" else None
    if not isinstance(raw, str) or not raw.startswith("~"):
        return ShellText(""), list(parts)
    head = raw.split("/", 1)[0]
    if "\\" in head or ("/" not in raw and len(parts) > 1):
        return ShellText(""), list(parts)
    home = tilde(ShellText(head + ("/" if "/" in raw else "")), env)
    if home is None:
        return None
    return home, [Node(type="lit", value=ShellText(raw[len(head) + 1:])), *parts[1:]]


def word_value(word: Node, state: State, env: Environment) -> ShellText | None:
    """A word as its command receives it, quoting removed, or None where the call does not make it known."""
    split = after_tilde(word.get("parts", []), env)
    if split is None:
        return None
    home, parts = split
    rest = concatenated(parts, state, env, quoted=False)
    return None if rest is None else ShellText(home + rest)


def pattern_free(raw: ShellText, closed: bool) -> tuple[ShellText, Cut | None]:
    """An unquoted literal up to where its glob starts, a glob cut, or its first unescaped `{`, an expansion cut, with
    its backslashes removed, and the cut, None where it runs whole; ``closed`` where a later part may hold a `]`; the
    reader has already dropped a backslash-newline."""
    glob = glob_at(raw, closed=closed, escapes=True)
    out: list[ShellText] = []
    chars = iter(raw if glob is None else raw[:glob])
    for char in chars:
        if char == "\\":
            out.append(ShellText(next(chars, "")))
        elif char == "{":
            return ShellText("".join(out)), "expansion"
        else:
            out.append(ShellText(char))
    return ShellText("".join(out)), None if glob is None else "glob"


def known_prefix(parts: Sequence[Node], state: State, env: Environment, quoted: bool) -> tuple[ShellText, Cut | None]:
    """The value of ``parts`` up to the first character or part the shell would expand, and the cut there, None
    where every part is known whole."""
    values: list[ShellText] = []
    for index, part in enumerate(parts):
        raw = part.get("value")
        closed = closes(parts[index + 1:])
        if part.get("type") == "lit" and isinstance(raw, str) and not quoted:
            value, cut = pattern_free(raw, closed)
        elif part.get("type") == "double":
            value, cut = known_prefix(part.get("parts", []), state, env, quoted=True)
        else:
            known = part_value(part, state, env, quoted, closed)
            if known is None:
                return ShellText("".join(values)), "expansion"
            value, cut = known, None
        values.append(value)
        if cut is not None:
            return ShellText("".join(values)), cut
    return ShellText("".join(values)), None


def word_prefix(word: Node, state: State, env: Environment) -> ShellText | None:
    """A word's prefix, as the module docstring describes."""
    value = word_value(word, state, env)
    if value is not None:
        return value
    split = after_tilde(word.get("parts", []), env)
    if split is None:
        return None
    home, parts = split
    text, cut = known_prefix(parts, state, env, quoted=False)
    reached = ShellText(home + text)
    return None if not reached and cut == "expansion" else reached
