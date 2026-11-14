"""Shell scripts a call runs, read as more of the call: where a script's text comes from, the program its shebang
names, the script an interpreter's words name or the parameters it reads its input as a script with, what a command
writes into a file, and the positional parameters a script is read with.

on_disk reads a script's file: a missing one, one the hook cannot open or decode as UTF-8, and a FIFO, a socket or a
device, which it never opens, has a trouble naming why; /dev/null holds no text; a directory, and a file whose first
block holds a NUL byte or that starts as an ELF binary, is no script and has neither text nor trouble, being left to
its own reader. interpreter names the program a shebang runs, past `env`.

options walks the option words of a shell (bash, sh or dash) once, for operand and input_parameters alike. A word
starting with - or + is an option word; `--`, a lone `-` or any other word ends them. Each o or O of an option word
takes the next word as its value, and a long option is one word, --rcfile and --init-file taking the next. -n and
-o noexec run nothing, the last of each and its + form deciding; -s reads the script on the input (in its - form
alone), -c runs a text, and --help and --version print and run nothing.

printed is what a command prints where the call makes it known (nothing for `:` or `true`, `cat` or `tee` of its
input, echo and printf of known words in their plain forms), and heredoc the text a heredoc feeds its command.

kept and bound read a script's positional parameters as the call makes them known: kept is the parameters the
script's tree can be read with, $0 alone where its own statements (outside function bodies) hold shift, eval or a set
naming operands, which rebind them; bound rewrites the tree so that each plain $0, $1 ... ${10}, $#, $@ and $* whose
value is known becomes a single-quoted part holding it, an unquoted one only where splitting and globbing leave it
whole whatever the rest of its word holds, and a whole word "$@" among a command's arguments one word per
parameter. A function body keeps its own parameters and is left as written.
"""

from collections.abc import Mapping, Sequence
from os import makedev, stat, stat_result
from stat import S_ISCHR, S_ISDIR, S_ISFIFO, S_ISREG, S_ISSOCK
from typing import NamedTuple, NewType

from dispatcher.tree import Count, Node, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import SPLIT, Environment, State, glob_at, parameter

ScriptPath = NewType("ScriptPath", str)

BINARY_PROBE = 4096
UTF8_WIDTH = 4
NULL_DEVICE = makedev(1, 3)
MISSING = Prose("is neither on disk nor written by the call")
NOT_TEXT = Prose("is not UTF-8 text")
INTERPRETER_VALUED = frozenset({"--rcfile", "--init-file"})
INTERPRETER_STOPS = frozenset({"--help", "--version"})
ENV_VALUED = frozenset({"-u", "-C", "-a", "--unset", "--chdir", "--argv0"})
OPERATORS = ("excl", "length", "index", "slice", "repl", "names", "exp")
ESCAPES = {ShellText(key): ShellText(value) for key, value in {
    "n": "\n", "t": "\t", "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "r": "\r", "v": "\v", "e": "\x1b"}.items()}
FORMAT_ESCAPES = {**ESCAPES, ShellText('"'): ShellText('"'), ShellText("'"): ShellText("'")}
HEREDOC_ESCAPES = frozenset("$`\\")


class Found(NamedTuple):
    """A script file's text, or the trouble that keeps the kit from it; neither where it is no script."""

    text: ShellText | None
    trouble: Prose | None


class Positionals(NamedTuple):
    """A script's $0 and the parameters after it, known up to the first the call does not make known; exact where
    every one is known, so that $#, $@ and $* are."""

    zero: ShellText | None
    params: tuple[ShellText, ...]
    exact: bool


def on_disk(path: ScriptPath, limit: Count) -> Found:
    """The text of the file at ``path``; one past the bytes ``limit`` characters can take, as much of it as shows it
    holds more than ``limit`` characters."""
    try:
        status = stat(path)
        if not S_ISREG(status.st_mode):
            return unopened(status)
        with open(path, "rb") as handle:
            data = handle.read(limit * UTF8_WIDTH + 1)
    except (FileNotFoundError, NotADirectoryError):
        return Found(None, MISSING)
    except OSError as fault:
        return Found(None, Prose(f"cannot be read: {fault.strerror}"))
    if data.startswith(b"\x7fELF") or b"\0" in data[:BINARY_PROBE]:
        return Found(None, None)
    if len(data) > limit * UTF8_WIDTH:
        return Found(ShellText(data.decode("utf-8", "replace")), None)
    try:
        return Found(ShellText(data.decode("utf-8")), None)
    except UnicodeDecodeError:
        return Found(None, NOT_TEXT)


def unopened(status: stat_result) -> Found:
    """What a path whose status is ``status`` and that is no regular file holds, never opened: no script for a
    directory, no text for /dev/null (Linux's character device 1:3), a trouble naming a FIFO, a socket or another
    device, whose open can block or act."""
    if S_ISDIR(status.st_mode):
        return Found(None, None)
    if S_ISCHR(status.st_mode) and status.st_rdev == NULL_DEVICE:
        return Found(ShellText(""), None)
    kind = "a FIFO" if S_ISFIFO(status.st_mode) else "a socket" if S_ISSOCK(status.st_mode) else "a device"
    return Found(None, Prose(f"is {kind}, which the kit does not open"))


def interpreter(text: ShellText) -> ShellText | None:
    """The base name of the program ``text``'s shebang runs, past `env`, its options and its assignments; None where
    the text has no shebang, the empty text where its shebang names no program."""
    if not text.startswith("#!"):
        return None
    words = text[2:].split("\n", 1)[0].split()
    if words and words[0].rsplit("/", 1)[-1] == "env":
        words = words[1:]
        while words and (words[0].startswith("-") or "=" in words[0]):
            words = words[2:] if words[0] in ENV_VALUED else words[1:]
    return ShellText(words[0].rsplit("/", 1)[-1] if words else "")


class Options(NamedTuple):
    """What the option words of a shell (bash, sh or dash) say, as options() walks them: ``end``, the index of the
    first word past them; ``runs``, False under -n or -o noexec; ``stdin`` under -s, which reads its script on its
    input; ``text`` under -c, which runs a text; ``stops`` under --help or --version, which print and run nothing."""

    end: Count
    runs: bool
    stdin: bool
    text: bool
    stops: bool


def options(values: Sequence[ShellText | None]) -> Options:
    """The option words of a shell (bash, sh or dash) whose words have ``values``, read as the module docstring
    states."""
    index, runs, stdin, text, stops = 1, True, False, False, False
    while index < len(values):
        value = values[index]
        if value is None or value[:1] not in ("-", "+"):
            break
        index += 1
        if value in ("--", "-"):
            break
        if value.startswith("--"):
            index += 1 if value in INTERPRETER_VALUED else 0
            stops = stops or value in INTERPRETER_STOPS
            continue
        for letter in value[1:]:
            if letter in "oO":
                taken = values[index] if index < len(values) else None
                index += 1
                runs = value[0] == "+" if letter == "o" and taken == "noexec" else runs
            runs = value[0] == "+" if letter == "n" else runs
            stdin = stdin or (value[0] == "-" and letter == "s")
            text = text or letter == "c"
    return Options(Count(index), runs, stdin, text, stops)


def operand(values: Sequence[ShellText | None]) -> Count | None:
    """The index, among the words of a shell (bash, sh or dash) whose values are ``values``, of the script it runs:
    the first word past its options as options() walks them; None under -c, -s, -n or -o noexec, or with no word past
    its options. --help and --version are read as other long options, so that the script past them is read."""
    read = options(values)
    return read.end if read.runs and not (read.stdin or read.text) and read.end < len(values) else None


def input_parameters(values: Sequence[ShellText | None]) -> Count | None:
    """The index, among the words of a shell (bash, sh or dash) whose values are ``values`` that runs no operand and
    no -c text, of the first positional parameter of the script it reads on its input: the first word past its
    options as options() walks them; None under -c, -n or -o noexec, --help or --version, which run nothing."""
    read = options(values)
    return read.end if read.runs and not (read.text or read.stops) else None


def positionals(zero: ShellText | None, values: Sequence[ShellText | None]) -> Positionals:
    """The parameters a script run with $0 ``zero`` and arguments ``values`` holds."""
    known: list[ShellText] = []
    for value in values:
        if value is None:
            return Positionals(zero, tuple(known), False)
        known.append(value)
    return Positionals(zero, tuple(known), True)


def unescaped(text: ShellText, table: Mapping[ShellText, ShellText]) -> ShellText | None:
    """``text`` with each backslash escape of ``table`` replaced; None where it holds another."""
    out: list[ShellText] = []
    index = 0
    while index < len(text):
        char = ShellText(text[index])
        if char != "\\":
            out.append(char)
            index += 1
            continue
        escaped = table.get(ShellText(text[index + 1:index + 2]))
        if escaped is None:
            return None
        out.append(escaped)
        index += 2
    return ShellText("".join(out))


def echoed(args: Sequence[ShellText]) -> ShellText | None:
    """What bash's echo prints for ``args``: its -n, -e and -E options read, an escape -e reads that is not a plain
    one unknown."""
    newline, escapes = True, False
    index = 0
    while index < len(args) and len(args[index]) > 1 and args[index][0] == "-" and set(args[index][1:]) <= set("neE"):
        for letter in args[index][1:]:
            newline = newline and letter != "n"
            escapes = escapes if letter == "n" else letter == "e"
        index += 1
    text = ShellText(" ".join(args[index:]))
    if escapes:
        read = unescaped(text, ESCAPES)
        if read is None:
            return None
        text = read
    return ShellText(text + "\n" if newline else text)


def formatted(args: Sequence[ShellText]) -> ShellText | None:
    """What bash's printf prints for ``args``: its format's plain escapes, and %s, %b, %d, %i and %% with no flag or
    width, the format reused while arguments remain; nothing under -v, unknown for any other form."""
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        return None
    if args[0].startswith("-v"):
        return ShellText("")
    template, rest = args[0], list(args[1:])
    out: list[ShellText] = []
    while True:
        used = False
        index = 0
        while index < len(template):
            char = ShellText(template[index])
            if char == "\\":
                escaped = FORMAT_ESCAPES.get(ShellText(template[index + 1:index + 2]))
                if escaped is None:
                    return None
                out.append(escaped)
                index += 2
                continue
            if char != "%":
                out.append(char)
                index += 1
                continue
            spec = template[index + 1:index + 2]
            index += 2
            if spec == "%":
                out.append(ShellText("%"))
                continue
            if spec not in ("s", "b", "d", "i"):
                return None
            used = True
            value = rest.pop(0) if rest else ShellText("")
            if spec == "b":
                read = unescaped(value, ESCAPES)
                if read is None:
                    return None
                value = read
            elif spec in ("d", "i"):
                if value and not value.removeprefix("-").isdigit():
                    return None
                value = ShellText(str(int(value or "0")))
            out.append(value)
        if not used or not rest:
            return ShellText("".join(out))


def printed(values: Sequence[ShellText | None], given: ShellText | None) -> ShellText | None:
    """What the command whose words have ``values`` prints on its stdout, its input ``given``; None where the call
    does not make it known."""
    if not values:
        return ShellText("")
    known = [value for value in values if value is not None]
    if len(known) < len(values):
        return None
    name, args = known[0], known[1:]
    if name in (":", "true"):
        return ShellText("")
    if name == "cat":
        return given if args in ([], ["-"]) else None
    if name == "tee":
        return given
    if name == "echo":
        return echoed(args)
    if name == "printf":
        return formatted(args)
    return None


def teed(values: Sequence[ShellText | None]) -> tuple[tuple[ShellText | None, ...], bool]:
    """The files a tee whose words have ``values`` writes, None for one the call does not make known, and whether it
    appends."""
    files: list[ShellText | None] = []
    appends = False
    options = True
    for value in values[1:]:
        if options and value == "--":
            options = False
        elif options and value is not None and value.startswith("-") and len(value) > 1:
            appends = appends or value == "--append" or (not value.startswith("--") and "a" in value)
        else:
            files.append(value)
    return tuple(files), appends


def heredoc(hdoc: Node, state: State, env: Environment) -> ShellText | None:
    """The text a heredoc feeds its command: its body as written where its delimiter is quoted, else with its
    parameters expanded and its backslash escapes read, a <<- one's leading tabs stripped from each line of its text
as written (a literal after an expansion continuing that expansion's line); None where the call does not make that
text known."""
    body = hdoc.get("body")
    word = hdoc.get("word")
    if hdoc.get("delim_quoted") or not isinstance(word, dict):
        return ShellText(body) if isinstance(body, str) else None
    out: list[ShellText] = []
    for position, part in enumerate(word.get("parts", [])):
        raw = part.get("value")
        kind = part.get("type")
        if kind == "lit" and isinstance(raw, str):
            text = heredoc_literal(ShellText(raw))
            if hdoc.get("strip_tabs"):
                text = ShellText("\n".join(line.lstrip("\t") if position == 0 or number else line
                                           for number, line in enumerate(text.split("\n"))))
        elif kind == "single" and isinstance(raw, str):
            text = ShellText(raw)
        elif kind == "param":
            found = parameter(part, state, env, quoted=True)
            if found is None:
                return None
            text = found
        else:
            return None
        out.append(text)
    return ShellText("".join(out))


def heredoc_literal(raw: ShellText) -> ShellText:
    """A literal of a heredoc whose delimiter is unquoted, its `\\$`, `\\`` and `\\\\` read."""
    out: list[ShellText] = []
    index = 0
    while index < len(raw):
        if raw[index] == "\\" and index + 1 < len(raw) and raw[index + 1] in HEREDOC_ESCAPES:
            out.append(ShellText(raw[index + 1]))
            index += 2
            continue
        out.append(ShellText(raw[index]))
        index += 1
    return ShellText("".join(out))


def rebinds(args: Sequence[Node]) -> bool:
    """Whether a command whose words are ``args`` rebinds its shell's positional parameters: shift, eval, or a set
    naming an operand or `--`."""
    texts = [arg.get("text", ShellText("")) for arg in args]
    while texts and texts[0] in ("builtin", "command"):
        texts = texts[1:]
    if not texts or texts[0] not in ("shift", "eval", "set"):
        return False
    if texts[0] != "set":
        return True
    valued = False
    for text in texts[1:]:
        if valued:
            valued = False
        elif len(text) > 1 and text[0] in ("-", "+") and text[1:].isalpha():
            valued = text[-1] == "o"
        else:
            return True
    return False


def kept(tree: Node, at: Positionals) -> Positionals:
    """The parameters ``tree``, a script's, is read with: ``at``, or $0 alone where its own statements rebind
    them."""
    pending: list[Node] = [tree]
    while pending:
        here = pending.pop()
        if here.get("type") == "function":
            continue
        if here.get("type") == "call" and rebinds(here.get("args", [])):
            return Positionals(at.zero, (), False)
        for value in here.values():
            if isinstance(value, dict):
                pending.append(value)
            elif isinstance(value, list):
                pending.extend(item for item in value if isinstance(item, dict))
    return at


def value_of(name: ShellText, at: Positionals, quoted: bool) -> ShellText | None:
    """The value of the special parameter ``name`` under ``at``; None where it is not known, or is not one."""
    count = len(at.params)
    if name.isdigit():
        position = int(name)
        if position == 0:
            return at.zero
        if position <= count:
            return at.params[position - 1]
        return ShellText("") if at.exact else None
    if not at.exact:
        return None
    if name == "#":
        return ShellText(str(count))
    if name in ("@", "*") and count <= 1:
        return at.params[0] if at.params else ShellText("")
    if name == "*" and quoted:
        return ShellText(" ".join(at.params))
    return None


def single(value: ShellText, text: ShellText) -> Node:
    """A single-quoted part holding ``value``, written as ``text``."""
    return Node(type="single", value=value, text=text)


def spread(word: Node, at: Positionals) -> list[Node] | None:
    """The words a whole-word "$@", $@ or $* among a command's arguments gives, one per parameter; None where the
    word is not one, or the parameters are not all known or would split or glob."""
    parts = word.get("parts", [])
    quoted = len(parts) == 1 and parts[0].get("type") == "double"
    inner = parts[0].get("parts", []) if quoted else parts
    if len(parts) != 1 or len(inner) != 1 or inner[0].get("type") != "param" or not at.exact:
        return None
    part = inner[0]
    name = part.get("name")
    if name not in ("@", "*") or any(part.get(operator) for operator in OPERATORS) or (quoted and name == "*"):
        return None
    if not quoted and any(not value or SPLIT.intersection(value) or glob_at(value) is not None for value in at.params):
        return None
    text = word.get("text", ShellText(""))
    return [Node(type="word", text=text, parts=[single(value, text)]) for value in at.params]


def bound(node: Node, at: Positionals, quoted: bool = False) -> Node:
    """``node`` with each plain positional parameter whose value ``at`` makes known rewritten, as the module
    docstring describes; ``quoted`` where it stands inside double quotes or a heredoc."""
    kind = node.get("type")
    if kind == "function":
        return node
    if kind == "param":
        name = node.get("name")
        if not isinstance(name, str) or any(node.get(operator) for operator in OPERATORS):
            return node
        value = value_of(ShellText(name), at, quoted)
        if value is None or (not quoted and (not value or SPLIT.intersection(value)
                                             or glob_at(value, opened=True, closed=True) is not None)):
            return node
        return single(value, node.get("text", ShellText("")))
    inside = (quoted or kind == "double") and kind not in ("cmdsubst", "procsubst")
    out = Node()
    for key, value in node.items():
        here = inside or key == "hdoc"
        if isinstance(value, dict):
            out[key] = bound(value, at, here)
        elif isinstance(value, list) and key == "args" and kind == "call":
            args: list[Node] = []
            for arg in value:
                spread_words = spread(arg, at)
                args.extend([bound(arg, at, here)] if spread_words is None else spread_words)
            out[key] = args
        elif isinstance(value, list):
            out[key] = [bound(item, at, here) if isinstance(item, dict) else item for item in value]
        else:
            out[key] = value
    return out
