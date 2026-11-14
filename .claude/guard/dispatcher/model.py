"""The call model: every simple command a Bash or Monitor call runs, wrappers peeled, each placed in its shell.

build() reads the call's text with the shell reader and walks the tree in the order the shell runs it. Each simple
command becomes a Command:
  - words: the command and its arguments, past the wrappers that only run it (the table of dispatcher/wrappers.py, whose
    rows the peeler reads; wrappers names them, outermost first), each with its value where the call makes it known and
    its prefix, the value up to the first part or character the shell would expand (dispatcher/words.py), and after them
    one word more, nothing of it known, for the words a wrapper whose row appends reads from its input, the row's
    command where its words name none; each wrapper peeled is a Command of its own just before, its words from its name
    on, its wrappers the ones outside it;
  - shells: the process boundaries between the call's own shell and the one the command runs in, outermost first:
    "subshell" for `( )`, "background" for a `&` job or a coprocess, "substitution" for `$( )`, backquotes and
    `<( )`, "pipeline" for a pipeline element, each run in a process of its own, "carrier" for the text `bash -c`,
    `sh -c` or `dash -c` runs, "script" for a shell script run by its path or through bash, sh or dash, "python" for
    a program the Python code a call runs starts, "find" for the command a find action runs; an empty tuple is the
    call's own shell. bash 5.3's `${ cmd;}` and `${| cmd;}` run in the current shell, as do the text `eval` runs and
    a script `source` or `.` reads;
  - processes: for each entry of shells, the instance of that boundary the command runs inside, a ProcessId unique
    within the call: two commands naming one run in that one process, and a loop body walked twice names the ones
    its first pass named;
  - pipelines: each pipeline the command runs in an element of, outermost first, a Seat naming the pipeline by a
    PipelineId unique within the call and the element's position from 0; a command inside an
    element that is a group, a subshell, a compound, a job or a carrier is in that pipeline, one inside a
    substitution in none outside it;
  - loops: each while, until, for and select loop the command runs inside, outermost first, a Loop naming the loop by
    a LoopId unique within the call, its kind and the part of it holding the command: its condition (a while's or
    an until's test list, a for's or a select's words or arithmetic, their substitutions included) or its body. A
    loop body walked twice names its loop once; a carrier, script, Python start or find action a command inside a
    loop runs is inside it, as is a substitution in its words or body and a function body defined there, which is
    judged where it is defined; a command after the loop is in none;
  - cwd: the directory it runs in, followed through the call's own `cd`, `pushd` and `popd`, None once unknown;
  - streams: where its standard input, output and errors end. The rule: the ends the command's process received,
    then the redirections of each group, compound or subshell holding it, outermost first, then its own, each left
    to right as bash applies them, for descriptors 0, 1 and 2 alone; a redirection naming or copying any other
    descriptor (`3>&1`, `1>&3`, `{fd}>f`) makes all three unknown where it stands, so a descriptor swap reads as
    unknown. Its exceptions are listed on Streams.
The call's text is read as bash 5.3 runs it, since the Bash tool runs every call in bash, which has lastpipe off and so
runs every element of a pipeline in a process of its own. An `sh -c` or `dash -c` text is read as the POSIX text dash
runs: as bash runs it, but that dash has no `pushd`, `popd` or `builtin` and no `time` keyword, so the first two change
no directory, `builtin` is no wrapper, and the command `time` names keeps its changes to itself. A function body is
judged as run where it is defined, since the reader nests `f() { a; } && b` into the body; a loop body as run once, from
the state its second pass would start in where one pass changes it. Text a carrier runs is read as more of the call, to
MAX_DEPTH carriers deep; a carrier whose text the call does not make known is a Gap, as is any command whose name is zsh
or fish, whatever its words, since the kit reads bash alone; its form is to run the text as bash. The shell text a
wrapper runs (its row's text option, or its command words joined) is read so too, in a carrier of the dialect of the
shell its row names, a program or the one an environment variable names; a Gap where the call does not make the text or
that shell known, or the shell is not bash, sh or dash.
A shell script the call runs is read as more of the call too, through the same depth and text limits (the mechanics
are dispatcher/scripts.py's): one run by a path (a name holding `/`) whose shebang names bash, sh or dash, or that
has none and so runs in its shell's dialect; one an interpreter (bash, sh or dash) runs as its operand, in that
interpreter's dialect; one such an interpreter with no operand and no -c text reads on its input, its words past its
options its positional parameters, the heredoc or here-string of its own or the file a `<` of its own names (read as
a script run by its path is); each in a script shell of its own that starts in the directory with the command's
assignments as its parameters, its positional parameters bound from the command's words. A script `source` or `.`
reads runs in the current shell, its state carried on, with the arguments after its name as its parameters where it
has any. A script's text is the last the call writes into its path before running it (a heredoc, printf, echo, `:`
or tee into it, read from the call's own text), else its file on disk. A script whose path the call does not make
known, that is neither on disk nor written by the call, that the call writes with text it does not make known, that
cannot be read or decoded, or that bash cannot parse is a Gap naming a readable form, as is one whose shebang names
zsh or fish and an interpreter's input the call does not make known (a pipe, a substitution, its own input, unknown
text); one whose shebang names another program, and a binary, are left to their own reader, and a bare name is never
read. A script whose path names a descriptor (/dev/stdin, /dev/stdout, /dev/stderr, /dev/fd/N, /proc/self/fd/N,
/proc/thread-self/fd/N), itself or through the symbolic links it passes, is never read, whether a command sources it,
runs it by its path or hands it to a shell or Python as its operand or `<` file: a Gap naming the script's own path
as the form that passes. A FIFO, a socket or a device but /dev/null is never opened: a Gap naming a readable form. A
script its project declares
unread (ReadSettings.unread), run by its path, through bash, sh or dash, or sourced, and not written by the call, is a
program the kit does not read: its command is recorded as any other, its text gives no command and no Gap.
The Python code a call runs is read for the processes it starts (the reading is dispatcher/python.py's): the text of
`python -c`, the heredoc or here-string a python with no operand or `-` reads, a script a Python interpreter runs, is
fed on its input by a `<` of its own, or runs by its path under a Python shebang (found as a shell script is), and
the file beneath the directory that `python -m M` names, under neither -I nor -P, an installed module never read; a
module whose name or directory the call does not make known under neither is a Gap. Each start whose program and
arguments are literal is a command of a python process of its own, a depth more, run in its cwd= or the
interpreter's directory, its environment the interpreter's under its env=, its program wrappers peeled and its
program read as a call's is (a carrier, a script, more Python); a start the code does not spell is counted on the
Call and passes. Code the call does not make known is a Gap, as is code Python cannot parse.
A find is read as GNU find 4.10 reads its words: -H, -L, -P, -D and its value, -O<level> and a `--` ending them, the
start points up to a word of `-` and more or a lone `(` or `!`, then the expression, each primary past its arguments.
The command each -exec, -execdir, -ok and -okdir action runs, its words up to a `;` word or, for -exec and -execdir,
a `+` word right after a `{}` word, is a command of a find process of its own: each `{}` within a word is a part the
call does not make known that begins as every path find passes it does (a start point and a `/`, the common start of
several, `./` for -execdir and -okdir and where find has none); it receives find's environment and descriptors, its
input /dev/null for -ok and -okdir, runs in find's directory for -exec and -ok and in one the call does not make known
for -execdir and -okdir, and has its wrappers peeled and its program read as a call's is. An action with no end or
holding a word the call does not make known, a word of the expression the call does not make known, and a script path
an action names that the call does not make known are Gaps.
A text the reader cannot parse, a carrier past the depth limit, a call longer than MAX_CALL_TEXT or one that holds
more than MAX_COMMANDS commands to follow is Unreadable; a node the model does not hold is a KitFault.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from os import readlink
from os.path import commonprefix, isfile, islink, join, normpath
from types import MappingProxyType
from typing import Literal, NamedTuple, NewType

from dispatcher.python import PythonText, SleepLoop, Start, invoked, starts
from dispatcher.scripts import (MISSING, Positionals, ScriptPath, bound, heredoc, input_parameters, interpreter, kept,
                                on_disk, operand, positionals, printed, teed)
from dispatcher.tree import Count, Node, ReaderPath, Seconds, ShellText, read_tree
from dispatcher.verdict import KitFault, Prose, Unreadable
from dispatcher.words import Directory, Environment, State, VarName, joined, lookup, word_prefix, word_value
from dispatcher.wrappers import UNKNOWN_SHELL, WRAPPERS, Dialect, Runner, Wrapper

Shell = Literal["subshell", "background", "substitution", "pipeline", "carrier", "script", "python", "find"]
ProcessId = NewType("ProcessId", int)
PipelineId = NewType("PipelineId", int)
LoopId = NewType("LoopId", int)
LoopKind = Literal["while", "until", "for", "select"]
LoopPart = Literal["condition", "body"]
Descriptor = NewType("Descriptor", int)
Reach = Literal["file", "null", "pipe", "substitution", "call", "text", "closed", "unknown"]
Opening = Literal["read", "write", "append", "update"]

CALL_DIALECT: Dialect = "bash"  # the Bash tool runs every call in bash 5.3
MAX_DEPTH = 4
MAX_CALL_TEXT = 512 * 1024
MAX_COMMANDS = 100_000
CARRIERS: dict[ShellText, Dialect] = {ShellText("bash"): "bash", ShellText("sh"): "sh", ShellText("dash"): "sh"}
WRITES = frozenset({">", ">|", ">>", "&>", "&>>"})
APPENDS = frozenset({">>", "&>>"})
INPUTS = frozenset({"<", "<>", "<<", "<<-", "<<<", "<&"})
FILE_INPUTS = frozenset({"<", "<>"})
CHANGES_DIRECTORY = frozenset({"cd", "pushd", "popd"})
DASH_CHANGES_DIRECTORY = frozenset({"cd"})
SUBSTITUTIONS = frozenset({"cmdsubst", "procsubst"})
TOO_LONG_FORM = Prose("Write the long text to a file with the Write tool and give the call its path.")
TOO_DEEP_FORM = Prose("Run the innermost text as the call itself, or from a script file.")
GAP_FORM = Prose("Write the text out in the call itself, or put it in a script file and run that file by its path.")
FOREIGN_FORM = Prose("Run the text as bash: write it out in the call itself as bash reads it, or pass it to bash -c.")
WRAPPED_SHELL_FORM = Prose("Run the text with bash -c as the command the wrapper runs.")
SCRIPT_PATH_FORM = Prose("Run the script by a path the call makes known, from a directory the call makes known.")
MODULE_FORM = Prose("Run the module by a name the call makes known, from a directory the call makes known.")
SCRIPT_FIND_FORM = Prose("Run a script that is on disk, or write it earlier in the call with a heredoc, printf, echo "
                         "or tee into its path.")
SCRIPT_WRITE_FORM = Prose("Write the script with text the call makes known: a heredoc whose delimiter is quoted, or "
                          "printf, echo or tee of known words, or write it before the call with the Write tool.")
SCRIPT_READ_FORM = Prose("Run a script the kit can read: a UTF-8 text file the hook's user may open.")
DESCRIPTOR_FORM = Prose("Run the script by its own path, not by a path naming a descriptor such as /dev/stdin "
                        "or /dev/fd/N.")
SCRIPT_PARSE_FORM = Prose("Write the script as bash reads it, or write its commands out in the call itself.")
SCRIPT_FOREIGN_FORM = Prose("Run the script as bash: write it as bash reads it under a bash shebang, or run it with "
                            "bash.")
PYTHON_PARSE_FORM = Prose("Write the code as Python 3.14 parses it, or start its commands from the call itself.")
FIND_FORM = Prose("Write the find expression out in the call, each action's command ended by a `;` word, or for -exec "
                  "and -execdir by a `+` word right after a `{}` word.")
PYTHON_NAME = re.compile(r"python(?:\d+(?:\.\d+)?t?)?")
DESCRIPTOR = re.compile(r"[0-9]+")
MOVED = re.compile(r"([0-9]+)-")
DEVICE = re.compile(r"/dev/fd/([0-9]+)")
DESCRIPTOR_PATH = re.compile(r"/(?:dev/fd|proc/(?:self|thread-self)/fd)/([0-9]+)")
MAX_LINKS = 40


def options(spelled: ShellText) -> frozenset[ShellText]:
    """The options a space-separated list names."""
    return frozenset(ShellText(option) for option in spelled.split())


CARRIER_VALUED = options(ShellText("--rcfile --init-file"))
FOREIGN_SHELLS = frozenset({ShellText("zsh"), ShellText("fish")})
FIND_LEADING = options(ShellText("-H -L -P"))
FIND_ACTIONS = options(ShellText("-exec -execdir -ok -okdir"))
FIND_PROMPTED = options(ShellText("-ok -okdir"))
FIND_ELSEWHERE = options(ShellText("-execdir -okdir"))
FIND_VALUED = options(ShellText("-amin -anewer -atime -cmin -cnewer -context -ctime -files0-from -fls -fprint -fprint0 "
                                "-fstype -gid -group -ilname -iname -inum -ipath -iregex -iwholename -links -lname "
                                "-maxdepth -mindepth -mmin -mtime -name -path -perm -printf -regex -regextype "
                                "-samefile -size -type -uid -used -user -wholename -xtype"))


class Word(NamedTuple):
    """A word as written, its value as the command receives it where the call makes it known, and its prefix."""

    text: ShellText
    value: ShellText | None
    prefix: ShellText | None

    @property
    def spelled(self) -> ShellText:
        """The value where known, the text as written where not."""
        return self.text if self.value is None else self.value

    def folder(self, cwd: Directory | None) -> Directory | None:
        """The folder the word reaches from ``cwd``, absolute: its value where known, else its prefix cut back to its
        last `/`, ``cwd`` itself where none is left; None where the prefix is, or a relative one meets no ``cwd``."""
        if self.prefix is None:
            return None
        reached = self.prefix if self.value is not None else self.prefix[:self.prefix.rfind("/") + 1]
        if reached.startswith("/"):
            return Directory(normpath(reached))
        return None if cwd is None else Directory(normpath(join(cwd, reached)))


def word_of(node: Node, state: State, env: Environment) -> Word:
    """A word of the tree as the model records it."""
    return Word(node["text"], word_value(node, state, env), word_prefix(node, state, env))


class Assignment(NamedTuple):
    """A NAME=VALUE before a command, the value None where unknown."""

    name: VarName
    value: ShellText | None


class Environ(NamedTuple):
    """What the call does to the environment a command starts with, read for the shell a wrapper's text runs in
    (Walk.shell_of) and a script's parameters (Walk.run). assigns holds the NAME=VALUE words it receives, in order:
    what its process received from the command that started it, each name its shell has assigned since holding the
    value assigned, then the command's own prefixes, then each env wrapper's, outermost first, a value None where
    unknown and the last of a name the one it sees; unsets the names an env -u removes from the environment the
    call's own shell exports, None for an unknown one, and cleared whether an env -i or `-` empties it, both counting
    the starts of the processes it runs in (their env wrappers, a Python start's env=)."""

    assigns: tuple[Assignment, ...] = ()
    unsets: tuple[VarName | None, ...] = ()
    cleared: bool = False

    def then(self, inner: "Environ") -> "Environ":
        """These changes followed by those of ``inner``, a wrapper run inside them: its emptying, or else its
        unsets, then its assignments."""
        if inner.cleared:
            return Environ(inner.assigns, (), True)
        assigns = self.assigns
        for name in inner.unsets:
            assigns = tuple(kept if name is not None else kept._replace(value=None)
                            for kept in assigns if kept.name != name)
        return Environ((*assigns, *inner.assigns), () if self.cleared else (*self.unsets, *inner.unsets),
                       self.cleared)


class Redirect(NamedTuple):
    """A redirection of a command: its operator, its descriptor, its target word and a heredoc's body."""

    op: ShellText
    fd: ShellText | None
    target: Word
    heredoc: ShellText | None


class Input(NamedTuple):
    """What a command's redirections feed its standard input: the text of a heredoc or here-string, None where the
    call does not make it known, or the file a `<` or `<>` names."""

    text: ShellText | None = None
    file: Word | None = None


class Seat(NamedTuple):
    """An element of a pipeline: the pipeline and the element's position from 0."""

    pipeline: PipelineId
    position: Count


class Loop(NamedTuple):
    """A loop a command runs inside: the loop, its kind, and the part of it holding the command."""

    loop: LoopId
    kind: LoopKind
    part: LoopPart


class End(NamedTuple):
    """Where a descriptor ends, by its reach: a file its redirection's word names, opened as ``opening`` says, the word
    kept whether its value is known or not (file); /dev/null (null); a pipe of the pipeline ``pipeline`` names, None
    for a pipe to the process that started the command, a coprocess's to its shell or a Python start's to its
    interpreter (pipe); a command or process substitution that reads or feeds it
    (substitution); the call's own descriptor, whose output and errors the tool result shows (call); a heredoc's or
    here-string's text (text); closed; or nowhere the call makes known (unknown)."""

    reach: Reach
    file: Word | None = None
    opening: Opening | None = None
    pipeline: PipelineId | None = None


INPUT, OUTPUT, ERRORS = Descriptor(0), Descriptor(1), Descriptor(2)
NULL = End("null")
CALLED = End("call")
SUBSTITUTED = End("substitution")
TEXT = End("text")
CLOSED = End("closed")
UNKNOWN = End("unknown")
COPROCESS = End("pipe")
CALL_ENDS: Mapping[Descriptor, End] = MappingProxyType({INPUT: CALLED, OUTPUT: CALLED, ERRORS: CALLED})
STARTED_ENDS: Mapping[Descriptor, End] = MappingProxyType({INPUT: UNKNOWN, OUTPUT: UNKNOWN, ERRORS: UNKNOWN})
DEVICES: dict[ShellText, Descriptor] = {ShellText("/dev/stdin"): INPUT, ShellText("/dev/stdout"): OUTPUT,
                                        ShellText("/dev/stderr"): ERRORS}


def named_descriptor(path: ScriptPath) -> Descriptor | None:
    """The descriptor the normalised path ``path``, led by one slash, names by its spelling, which opening it opens
    anew: /dev/stdin, /dev/stdout, /dev/stderr, /dev/fd/N, /proc/self/fd/N or /proc/thread-self/fd/N; None for any
    other path."""
    named = DESCRIPTOR_PATH.fullmatch(path)
    return DEVICES.get(ShellText(path)) if named is None else Descriptor(int(named.group(1)))


def linked_descriptor(path: ScriptPath) -> Descriptor | None:
    """The descriptor opening the normalised path ``path`` opens anew: the one it names, led by any number of slashes,
    or the one it names once the symbolic links it passes through, a folder's among them, are replaced by their
    targets as the kernel follows them, at most MAX_LINKS; None for any other path."""
    folder, rest = "/", path.split("/")
    for _ in range(MAX_LINKS + 1):
        named = named_descriptor(ScriptPath(normpath(join(folder, *rest))))
        while named is None and rest and not islink(join(folder, rest[0])):
            folder, rest = normpath(join(folder, rest[0])), rest[1:]
        if named is not None or not rest:
            return named
        target = readlink(join(folder, rest[0]))
        folder, rest = "/" if target.startswith("/") else folder, [*target.split("/"), *rest[1:]]
    return None


def past_streams(node: Node, word: Word) -> bool:
    """Whether the redirection ``node``, its word read as ``word``, names or copies a descriptor past fd 2: a
    `{name}` or a number past 2 before its operator, or a `>&` or `<&` word that is such a number, moved or not."""
    spelled = node.get("fd")
    if spelled is not None and (not DESCRIPTOR.fullmatch(spelled) or int(spelled) > ERRORS):
        return True
    copied = None if word.value is None or node.get("op") not in (">&", "<&") else word.value.removesuffix("-")
    return copied is not None and DESCRIPTOR.fullmatch(copied) is not None and int(copied) > ERRORS


class Streams(NamedTuple):
    """Where a command's standard input, output and errors end, by the rule of the module docstring: the ends its
    process received (the call's own, or the ends of the command that started its carrier, script or Python process),
    then each enclosing statement's redirections outermost first (a group, a subshell, a compound, `time`, a function
    definition's own, `eval` and `source`) and each pipeline element's seat as the element is entered (fd 0 a pipe but
    in the first, fd 1 but in the last, fd 2 joining fd 1 after the element's redirections for `|&`), then its own
    redirections left to right, for descriptors 0, 1 and 2 alone; a redirection naming or copying any other
    descriptor makes all three unknown where it stands (past_streams()).
    Exceptions, each holding a case the rule alone would misplace:
      - its shell's `exec` redirections are kept for the commands after it, so `exec > X; a` ends a's output in X,
        and a descriptor that paths through an `if` or `case` keep differently is unknown after them;
      - a word naming a descriptor's path (/dev/stdout, /dev/fd/N) copies that descriptor's end, so
        `a > X 2> /dev/stdout` ends a's errors in X;
      - a substitution captures fd 1 or feeds fd 0, so `a > >(b)` ends a's output in the substitution;
      - a `&` job reads /dev/null, so `a &` reads nothing of the call's input;
      - a Python start's ends are unknown, its keywords unread;
      - a redirection where a function is called reaches no command of its body, which is judged where it is
        defined.
    """

    input: End
    output: End
    errors: End


class Command(NamedTuple):
    """One simple command the call runs, as the module docstring describes; a statement of assignments or
    redirections alone is one of no words, run where it stands with its redirections, bash's `$(< file)` among
    them. A Python interpreter's command carries in sleeps each loop of the Python code it runs that sleeps
    (dispatcher/python.py's SleepLoop), and in pauses whether that code sleeps outside them; every other command has
    neither."""

    words: tuple[Word, ...]
    wrappers: tuple[ShellText, ...]
    redirs: tuple[Redirect, ...]
    shells: tuple[Shell, ...]
    function: ShellText | None
    cwd: Directory | None
    text: ShellText
    processes: tuple[ProcessId, ...]
    pipelines: tuple[Seat, ...]
    loops: tuple[Loop, ...]
    streams: Streams
    sleeps: tuple[SleepLoop, ...] = ()
    pauses: bool = False

    @property
    def name(self) -> ShellText | None:
        """The command's name where known."""
        return self.words[0].value if self.words else None

    @property
    def in_own_shell(self) -> bool:
        """Whether the call's own shell runs it."""
        return not self.shells


class Gap(NamedTuple):
    """Text the call runs that the model cannot read: what holds it, the command as written, and the form the kit
    reads it in."""

    what: Prose
    text: ShellText
    form: Prose


class PythonCode(NamedTuple):
    """A Python text the model read, and whether the call carries it: spelled in the call (a -c text, an
    interpreter's input) or written by it, rather than found in a file on disk."""

    text: PythonText
    carried: bool


class Call(NamedTuple):
    """A call's text, its commands in the order they run, what the model could not read, the count of process
    starts in the Python code it runs that the code does not spell, and that Python code, each text the model read
    in the order it read them."""

    text: ShellText
    commands: tuple[Command, ...]
    gaps: tuple[Gap, ...]
    unspelled: Count = Count(0)
    python: tuple[PythonCode, ...] = ()


class Place(NamedTuple):
    """Where a statement runs: the boundaries crossed, the function holding it, its shell's dialect, carrier depth,
    the instance of each boundary, the pipeline elements it is in, the loops it runs inside, the script's positional
    parameters, None where the call does not bind them, and what the command that started its script, carrier or
    Python process did to the environment that process received, and where the descriptors it received end."""

    shells: tuple[Shell, ...]
    function: ShellText | None
    dialect: Dialect
    depth: Count
    processes: tuple[ProcessId, ...] = ()
    pipelines: tuple[Seat, ...] = ()
    loops: tuple[Loop, ...] = ()
    positionals: Positionals | None = None
    environ: Environ = Environ()
    ends: Mapping[Descriptor, End] = CALL_ENDS

    def into(self, shell: Shell, process: ProcessId) -> "Place":
        """The place one more boundary in, that boundary's instance ``process``."""
        return self._replace(shells=(*self.shells, shell), processes=(*self.processes, process))


class ShellState(State):
    """A shell's directory and parameters, and the ends its own redirections give its descriptors over those its
    process received: an `exec`'s kept, the others held while their command or compound runs."""

    __slots__ = ("ends",)

    def __init__(self, cwd: Directory | None, names: dict[VarName, ShellText | None] | None = None) -> None:
        super().__init__(cwd, names)
        self.ends: dict[Descriptor, End] = {}

    def copy(self) -> "ShellState":
        """A copy a subshell changes without touching this one."""
        state = ShellState(self.cwd, self.names)
        state.ends = dict(self.ends)
        return state

    def holds(self, ends: Mapping[Descriptor, End]) -> dict[Descriptor, End | None]:
        """Give the descriptors of ``ends`` those ends, returning what each held, None for its process's."""
        saved = {fd: self.ends.get(fd) for fd in ends}
        self.ends.update(ends)
        return saved

    def restores(self, saved: Mapping[Descriptor, End | None]) -> None:
        """Give each descriptor of ``saved`` back what it held."""
        for fd, end in saved.items():
            if end is None:
                _ = self.ends.pop(fd, None)
            else:
                self.ends[fd] = end

    def merges(self, states: Sequence["ShellState"]) -> None:
        """Become the state after paths that end in ``states``: what they agree on, unknown where they differ."""
        found = {fd: {state.ends.get(fd, UNKNOWN) for state in states} for state in states for fd in state.ends}
        self.become(joined(states))
        self.ends = {fd: ends.pop() if len(ends) == 1 else UNKNOWN for fd, ends in found.items()}


class ReadSettings(NamedTuple):
    """What reading a call needs: the shell reader and its time limit, the environment parameters fall back to, and
    the absolute paths of the scripts its project declares the kit runs unread."""

    reader: ReaderPath
    timeout: Seconds
    env: Environment
    unread: frozenset[ScriptPath] = frozenset()


def target_of(words: Sequence[Word], cwd: Directory | None) -> Directory | None:
    """The directory `cd`, `pushd` or `popd` ``words`` change to from ``cwd``, None where the call does not name it:
    `popd` and a bare `cd` or `cd -`, an unknown operand, a relative one from an unknown directory, more than one
    operand."""
    if words[0].value == "popd":
        return None
    operands = list(words[1:])
    while operands and operands[0].spelled.startswith("-"):
        if operands.pop(0).spelled == "--":
            break
    operand = operands[0].value if len(operands) == 1 else None
    if operand is None or (cwd is None and not operand.startswith("/")):
        return None
    return Directory(normpath(join(cwd or "/", operand)))


def first_value(words: Sequence[Word]) -> ShellText | None:
    """The value of the first of ``words``, None where it is unknown or there is none."""
    return words[0].value if words else None


def wraps(row: Wrapper, dialect: Dialect | None) -> bool:
    """Whether ``row`` is a wrapper where a shell of ``dialect`` runs it, None for a Python start, which runs
    programs alone."""
    return not row.builtin or dialect in row.builtin


def literal(text: ShellText) -> Word:
    """A word whose value is ``text`` as written."""
    return Word(text, text, text)


def given(word: Word, attached: ShellText) -> Word:
    """The value an option word ``word`` gives after its name, ``attached`` as written where ``word``'s value is
    known; else not known, ``attached`` its known prefix."""
    return literal(attached) if word.value is not None else Word(word.text, None, attached)


class Taken:
    """What a wrapper's options give as they are read: the options given, whether one empties the environment, the
    names removed from it, the shell text an option gives with the shell that runs it, and the shell the command
    words run in, joined, None where they run as a command."""

    def __init__(self, row: Wrapper) -> None:
        self.row = row
        self.given: set[ShellText] = set()
        self.cleared = False
        self.unsets: list[VarName | None] = []
        self.text: tuple[Word | None, Runner] | None = None
        self.runner = row.joins

    def took(self, option: ShellText, value: Word | None) -> None:
        """Record ``option`` given with ``value``, None where it takes none or its word is missing."""
        row = self.row
        self.given.add(option)
        self.cleared = self.cleared or option in row.clears
        if option in row.unsets:
            self.unsets.append(None if value is None or value.value is None else VarName(value.value))
        if option in row.switches:
            self.runner = row.switches[option]
        if option in row.texts:
            self.text = (value, row.texts[option])

    def spelled(self, name: ShellText) -> ShellText:
        """The long option ``name`` is: with `--` where the row is long_only and names it so alone."""
        row = self.row
        if name.startswith("--"):
            return name
        doubled = ShellText("-" + name)
        known = (row.valued.keys() | row.optional | row.stops | row.ends | row.texts.keys() | row.switches.keys()
                 | row.clears | row.unsets)
        return doubled if name not in known and doubled in known else name

    def read(self, words: Sequence[Word], index: Count) -> Count | None:
        """Read the options from ``words[index]`` on: the index of the first word past them, None where one stops
        the wrapper; a word not known is an option where its known prefix starts with `-`, read as far as that prefix
        spells it, its value not known and its name, where that prefix holds no `=` after `--`, a flag's; any other
        ends them, as `--` does, taken where the row names it among its ends."""
        row = self.row
        while index < len(words):
            word = words[index]
            value = word.value if word.value is not None else word.prefix
            if value is None:
                break
            if value == "--" and word.value is not None:
                if value in row.ends:
                    self.took(value, None)
                return Count(index + 1)
            if value.startswith("--") or (row.long_only and value.startswith("-") and len(value) > 2):
                name, equals, attached = value.partition("=")
                if not equals and word.value is None:
                    index = Count(index + 1)
                    continue
                option = self.spelled(ShellText(name))
                if option in row.stops:
                    return None
                if option in row.ends:
                    self.took(option, None)
                    return Count(index + 1)
                count = row.valued.get(option, Count(0))
                if count and not equals:
                    self.took(option, first_word(words[index + 1:]))
                    index = Count(index + 1 + count)
                else:
                    self.took(option, given(word, ShellText(attached)) if equals else None)
                    index = Count(index + max(count, 1))
                continue
            if value.startswith("-") and (len(value) > 1 or word.value is None):
                index = Count(index + 1)
                for position, letter in enumerate(value[1:], 1):
                    option = ShellText("-" + letter)
                    attached = ShellText(value[position + 1:])
                    if option in row.stops:
                        return None
                    count = row.valued.get(option, Count(0))
                    if count:
                        joined = bool(attached) or word.value is None
                        self.took(option, given(word, attached) if joined else first_word(words[index:]))
                        index = Count(index + count - (1 if joined else 0))
                        break
                    if option in row.optional:
                        self.took(option, None)
                        break
                    self.took(option, None)
                continue
            break
        return index


INPUT_WORDS = Word(ShellText("the words a wrapper reads from its input"), None, None)
"""The word a wrapper whose row appends adds to its command for what it reads from its input."""


def first_word(words: Sequence[Word]) -> Word | None:
    """The first of ``words``, None where there is none."""
    return words[0] if words else None


ESCAPED = re.compile(r"[^A-Za-z0-9_$-]")


def joined_word(words: Sequence[Word], escapes: bool) -> Word:
    """The text ``words`` make joined by spaces, each character but a letter, digit, `_`, `-` and `$` escaped with a
    backslash where ``escapes``; its value None where a word's is."""
    text = ShellText(" ".join(word.text for word in words))
    values = [word.value for word in words]
    if any(value is None for value in values):
        return Word(text, None, None)
    known = ShellText(" ".join(ESCAPED.sub(lambda found: "\\" + found.group(), value or "") if escapes
                               else value or "" for value in values))
    return literal(known)


class Reading(NamedTuple):
    """A wrapper's words read: the command it runs, none where it runs none the kit follows, what it does to that
    command's environment, and the shell text it runs instead with the shell that runs it, where it runs one."""

    after: tuple[Word, ...]
    changes: Environ
    text: Word | None = None
    runner: Runner = UNKNOWN_SHELL


def past_options(words: Sequence[Word], row: Wrapper) -> Reading:
    """A wrapper's words read as its row says: past its options, its subcommand and their options, its numbered
    operand, a lone `-` that empties the environment, its NAME=VALUE words, then, where no end option ended its
    options, its operands and an end option or a text option; an operand is skipped whatever its value. A row that
    appends adds INPUT_WORDS to the command, its own command where the words name none."""
    taken = Taken(row)
    index = taken.read(words, Count(1))
    while index is not None and row.subcommand is not None and first_value(words[index:]) == row.subcommand:
        taken.given.add(row.subcommand)
        index = taken.read(words, Count(index + 1))
    if index is None:
        return Reading((), Environ())
    if row.numbered and (number := first_value(words[index:])) is not None and number.isdecimal():
        index = Count(index + 1)
    if first_value(words[index:]) == "-" and "-" in row.clears:
        taken.cleared, index = True, Count(index + 1)
    assigns: list[Assignment] = []
    while row.assigns and index < len(words) and "=" in (prefix := words[index].prefix or ""):
        value = words[index].value
        assigns.append(Assignment(VarName(prefix.partition("=")[0]),
                                  None if value is None else ShellText(value.partition("=")[2])))
        index = Count(index + 1)
    if not taken.given & row.ends:
        index = Count(index + row.operands)
        option = first_value(words[index:])
        if option is not None and option in row.ends:
            taken.took(option, None)
            index = Count(index + 1)
        elif option is not None and option in row.texts:
            taken.took(option, first_word(words[index + 1:]))
    changes = Environ(tuple(assigns), tuple(taken.unsets), taken.cleared)
    if taken.text is not None:
        given, runner = taken.text
        return Reading((), changes, given or literal(ShellText("")), runner)
    if row.needs and not taken.given & row.needs:
        return Reading((), Environ())
    after = tuple(words[index:])
    if taken.runner is not None and after:
        return Reading((), changes, joined_word(after, row.escapes), taken.runner)
    if row.appends is not None:
        return Reading((*(after or (literal(row.appends),)), INPUT_WORDS), changes)
    return Reading(after, changes)


class Carried(NamedTuple):
    """Shell text a wrapper runs: the word holding it, its value None where the call does not make it known, the
    shell that runs it as the row names it, and what the call has done to the environment that shell starts
    with."""

    word: Word
    runner: Runner
    environ: Environ


class Peel(NamedTuple):
    """A command's words past every wrapper that only runs the command after it: each wrapper peeled, its words from
    its name on, outermost first, the command, the wrappers' names, what the call has done to the command's environment, and the shell
    text the innermost wrapper runs instead of a command, where it runs one."""

    layers: tuple[tuple[Word, ...], ...]
    words: tuple[Word, ...]
    wrappers: tuple[ShellText, ...]
    environ: Environ
    carried: Carried | None


def peel(words: tuple[Word, ...], environ: Environ = Environ(), dialect: Dialect | None = CALL_DIALECT) -> Peel:
    """``words`` past every wrapper of the table that only runs the command after it, where a shell of ``dialect``
    runs them (None for a Python start), ``environ`` what the command's process received and its own prefixes do to
    its environment."""
    layers: list[tuple[Word, ...]] = []
    wrappers: list[ShellText] = []
    rest = words
    while rest and rest[0].value is not None:
        base = ShellText(rest[0].value.rsplit("/", 1)[-1])
        row = WRAPPERS.get(base)
        if row is None or not wraps(row, dialect):
            break
        reading = past_options(rest, row)
        if reading.text is not None:
            carried = Carried(reading.text, reading.runner, environ.then(reading.changes))
            return Peel(tuple(layers), rest, tuple(wrappers), environ, carried)
        if not reading.after:
            break
        layers.append(rest)
        wrappers.append(base)
        rest = reading.after
        environ = environ.then(reading.changes)
    return Peel(tuple(layers), rest, tuple(wrappers), environ, None)


def carried_text(words: tuple[Word, ...]) -> Word | None:
    """The word a `bash -c`, `sh -c` or `dash -c` runs as its text, read as these shells read their options: a c in
    a - or + bundle makes the first word past the options the text, each o or O takes the next word as its value,
    a lone + is an option word with no letters, and - or -- ends the options; None where the words run no -c
    text."""
    index = 1
    command = False
    while index < len(words):
        value = words[index].value
        if value is None:
            return words[index] if command else None
        if value in ("-", "--"):
            index += 1
            break
        if value in CARRIER_VALUED:
            index += 2
            continue
        if value.startswith("--"):
            index += 1
            continue
        if value[:1] in ("-", "+"):
            letters = value[1:]
            command = command or "c" in letters
            index += 1 + letters.count("o") + letters.count("O")
            continue
        break
    return words[index] if command and index < len(words) else None


def foreign_shell(words: tuple[Word, ...]) -> ShellText | None:
    """zsh or fish where the command ``words`` run is either shell, by its name alone, whatever its options and
    operands; None where it is neither."""
    name = words[0].value if words else None
    base = None if name is None else ShellText(name.rsplit("/", 1)[-1])
    return base if base in FOREIGN_SHELLS else None


def find_starts(words: Sequence[Word]) -> tuple[tuple[Word, ...], Count]:
    """The start points of the find ``words`` and where its expression begins, as GNU find 4.10 reads them: past -H,
    -L, -P, -D and its value, -O<level> and a `--` ending them, a word the call does not make known a start point."""
    index = 1
    while index < len(words):
        value = words[index].value or ShellText("")
        if value in FIND_LEADING or value.startswith("-O"):
            index += 1
        elif value == "-D":
            index += 2
        elif value == "--":
            index += 1
            break
        else:
            break
    first = index
    while index < len(words) and not opens_expression(words[index].value or ShellText("")):
        index += 1
    return words[first:index], Count(index)


def found_prefix(roots: Sequence[Word]) -> ShellText | None:
    """How every path a find with the start points ``roots`` passes to an -exec or -ok `{}` begins, as GNU find 4.10
    walks them: the common start of each start point and a `/`, the folder every such path reaches, `./` where there
    is none; None where a start point's prefix is not known."""
    prefixes: list[ShellText] = []
    for root in roots:
        if root.value is not None:
            prefixes.append(ShellText(root.value.rstrip("/") + "/"))
        elif root.prefix is not None:
            prefixes.append(root.prefix)
        else:
            return None
    return ShellText(commonprefix(prefixes or [ShellText("./")]))


def opens_expression(value: ShellText) -> bool:
    """Whether GNU find 4.10 reads ``value`` as the first word of its expression rather than a start point: `-` and
    more, or a lone `(` or `!`."""
    return (len(value) > 1 and value.startswith("-")) or value in ("(", "!")


def find_arguments(primary: ShellText) -> Count:
    """How many words after the find primary ``primary`` GNU find 4.10 reads as its arguments."""
    if primary == "-fprintf":
        return Count(2)
    return Count(1 if primary in FIND_VALUED or primary.startswith("-newer") else 0)


def action_end(words: Sequence[Word], start: Count, batched: bool) -> Count | None:
    """Where the find action whose command starts at ``start`` ends: its `;` word, or for a batched one a `+` word
    right after a `{}` word; None where nothing ends it."""
    for index in range(start, len(words)):
        value = words[index].value
        if value == ";" or (batched and value == "+" and words[index - 1].value == "{}"):
            return Count(index)
    return None


def placed(word: Word, passed: ShellText | None) -> Word:
    """A word of a find action as find passes it, each `{}` in it a part the call does not make known that begins
    with ``passed``, None where that is not known."""
    if word.value is None or "{}" not in word.value:
        return word
    head = ShellText(word.value[:word.value.index("{}")])
    return Word(word.text, None, None if passed is None and not head else ShellText(head + (passed or "")))


class Walk:
    """One call's walk: the commands found so far, the gaps, the trees read, the commands followed, the last
    process, pipeline and loop identifiers issued, the files the call has written so far, each with its text, None
    where the call does not make it known, the Python starts not spelled and the Python code read."""

    def __init__(self, settings: ReadSettings) -> None:
        self.settings = settings
        self.commands: list[Command] = []
        self.gaps: list[Gap] = []
        self.unspelled = Count(0)
        self.python_read: list[PythonCode] = []
        self.trees: dict[ShellText, Node] = {}
        self.read = Count(0)
        self.followed = Count(0)
        self.last_process = ProcessId(0)
        self.last_pipeline = PipelineId(0)
        self.last_loop = LoopId(0)
        self.written: dict[ScriptPath, ShellText | None] = {}

    def new_process(self) -> ProcessId:
        """An identifier no process of the call holds yet."""
        self.last_process = ProcessId(self.last_process + 1)
        return self.last_process

    def new_pipeline(self) -> PipelineId:
        """An identifier no pipeline of the call holds yet."""
        self.last_pipeline = PipelineId(self.last_pipeline + 1)
        return self.last_pipeline

    def new_loop(self) -> LoopId:
        """An identifier no loop of the call holds yet."""
        self.last_loop = LoopId(self.last_loop + 1)
        return self.last_loop

    def script(self, text: ShellText, state: ShellState, place: Place, path: ScriptPath | None = None,
               holder: ShellText | None = None) -> None:
        """Read ``text`` and walk its statements in ``state`` at ``place``, its positional parameters bound where the
        place binds them; the text of the script file at ``path``, where bash cannot parse it, is a gap of the
        command ``holder``."""
        tree = self.trees.get(text)
        self.charge(text, place, tree is None)
        if tree is None:
            try:
                tree = read_tree(text, self.settings.reader, self.settings.timeout)
            except Unreadable as fault:
                if path is None or holder is None:
                    raise
                self.gaps.append(Gap(Prose(f"in the script {path}, {fault.reason}"), holder, SCRIPT_PARSE_FORM))
                return
            self.trees[text] = tree
        if place.positionals is not None:
            place = place._replace(positionals=kept(tree, place.positionals))
            tree = bound(tree, place.positionals)
        self.stmts(tree.get("stmts", []), state, place)

    def follow(self) -> None:
        """Count one more command to follow; Unreadable past MAX_COMMANDS."""
        self.followed = Count(self.followed + 1)
        if self.followed > MAX_COMMANDS:
            raise Unreadable(Prose(f"it holds more than {MAX_COMMANDS} commands to follow"), TOO_LONG_FORM)

    def charge(self, text: ShellText, place: Place, new: bool = True) -> None:
        """Read ``text`` at ``place``, counting it toward the text limit where ``new``; Unreadable past the depth or
        the text limit."""
        if place.depth > MAX_DEPTH:
            raise Unreadable(Prose(f"it nests carriers (bash -c, sh -c, eval), scripts and Python code more than "
                                   f"{MAX_DEPTH} deep"), TOO_DEEP_FORM)
        if new:
            self.read = Count(self.read + len(text))
            if self.read > MAX_CALL_TEXT:
                raise Unreadable(Prose(f"its text, with the text its carriers, scripts and Python code run, is "
                                       f"longer than {MAX_CALL_TEXT // 1024} KiB"), TOO_LONG_FORM)

    def stmts(self, stmts: Sequence[Node], state: ShellState, place: Place) -> None:
        """Walk statements in order."""
        for stmt in stmts:
            self.stmt(stmt, state, place)

    def stmt(self, stmt: Node, state: ShellState, place: Place, errors: bool = False) -> None:
        """Walk one statement: a `&` job or a coprocess in a background shell of its own, a `&` job's input
        /dev/null; a compound's redirections held while it runs, fd 2 then joining fd 1 where ``errors``, as `|&`
        makes it."""
        if stmt.get("background") or stmt.get("coprocess"):
            state, place = state.copy(), place.into("background", self.new_process())
            if stmt.get("background"):
                state.ends[INPUT] = NULL
        command = stmt.get("cmd")
        redirs = stmt.get("redirs", [])
        if command is None:
            self.bare(redirs, ShellText(stmt["text"]), state, place, errors)
        elif command.get("type") == "call":
            self.call(command, redirs, state, place, errors)
        else:
            self.substitutions(redirs, state, place)
            self.wrote((), redirs, state, alone=True)
            saved = state.holds(self.opened(redirs, state, place, errors))
            self.compound(command, state, place)
            state.restores(saved)

    def bare(self, redirs: Sequence[Node], text: ShellText, state: ShellState, place: Place, errors: bool) -> None:
        """A statement of redirections alone, ``text``: its substitutions first, then a command of no words run with
        its redirections held, fd 2 then joining fd 1 where ``errors``, as bash runs it."""
        self.substitutions(redirs, state, place)
        saved = state.holds(self.opened(redirs, state, place, errors))
        self.record((), (), tuple(self.redirect(r, state) for r in redirs), state, place, text)
        self.wrote((), redirs, state)
        state.restores(saved)

    def compound(self, command: Node, state: ShellState, place: Place) -> None:
        """Walk a command other than a simple one, each body in the shell and the state it runs in."""
        match command.get("type"):
            case "andor":
                for item in command.get("items", []):
                    self.stmt(item["stmt"], state, place)
            case "time":
                self.stmt(command["stmt"], state if place.dialect == "bash" else state.copy(), place)
            case "pipeline":
                items = command.get("items", [])
                pipeline = self.new_pipeline()
                pipe = End("pipe", pipeline=pipeline)
                for index, item in enumerate(items):
                    seat = Seat(pipeline, Count(index))
                    seated = place._replace(pipelines=(*place.pipelines, seat))
                    element = state.copy()
                    if index > 0:
                        element.ends[INPUT] = pipe
                    if index < len(items) - 1:
                        element.ends[OUTPUT] = pipe
                    joins = [after.get("op") for after in items[index + 1:index + 2]] == ["|&"]
                    self.stmt(item["stmt"], element, seated.into("pipeline", self.new_process()), joins)
            case "subshell":
                self.stmts(command.get("stmts", []), state.copy(), place.into("subshell", self.new_process()))
            case "block":
                self.stmts(command.get("stmts", []), state, place)
            case "coproc":
                coprocess = state.copy()
                coprocess.ends.update({INPUT: COPROCESS, OUTPUT: COPROCESS})
                self.stmt(command["stmt"], coprocess, place.into("background", self.new_process()))
            case "if":
                self.branches(command, state, place)
            case "case":
                self.cases(command, state, place)
            case "while":
                self.loops(command, state, place)
            case "for":
                self.loops(command, state, place)
            case "function":
                self.function(command, state, place)
            case "arith_cmd" | "test" | "let":
                self.substitutions([command], state, place)
            case "decl":
                self.declaration(command, state, place)
            case other:
                raise KitFault(Prose(f"the call model does not hold a {other} node"))

    @staticmethod
    def as_list(cond: "list[Node] | Node | None") -> list[Node]:
        """An if or while condition, a list of statements."""
        if cond is None:
            return []
        if isinstance(cond, list):
            return cond
        raise KitFault(Prose("a condition is a node where statements were expected"))

    def branches(self, command: Node, state: ShellState, place: Place) -> None:
        """An if: each condition after the ones before it, each body from its condition, the paths joined."""
        exits: list[ShellState] = []
        for clause in command.get("clauses", []):
            self.stmts(self.as_list(clause.get("cond")), state, place)
            branch = state.copy()
            self.stmts(clause.get("then", []), branch, place)
            exits.append(branch)
        otherwise = state.copy()
        self.stmts(command.get("else") or [], otherwise, place)
        state.merges([*exits, otherwise])

    def cases(self, command: Node, state: ShellState, place: Place) -> None:
        """A case: each item's body from the state after the word, the paths and no match joined."""
        self.substitutions([command["word"]], state, place)
        exits = [state.copy()]
        for item in command.get("items", []):
            self.substitutions(item.get("patterns", []), state, place)
            branch = state.copy()
            self.stmts(item.get("stmts", []), branch, place)
            exits.append(branch)
        state.merges(exits)

    def loops(self, command: Node, state: ShellState, place: Place) -> None:
        """A while, until, for or select: its words, its loop variable unknown, then its body as a loop; its test
        list and its words in its condition, its body in its body."""
        loop, kind = self.new_loop(), loop_kind(command)
        tested = place._replace(loops=(*place.loops, Loop(loop, kind, "condition")))
        inside = place._replace(loops=(*place.loops, Loop(loop, kind, "body")))
        cond = self.as_list(command.get("cond")) if command.get("type") == "while" else []
        if command.get("type") == "for":
            head = command["loop"]
            self.substitutions([head], state, tested)
            name = head.get("name")
            if isinstance(name, str):
                state.names[VarName(name)] = None
        body = command.get("do") or []

        def once(here: ShellState) -> None:
            self.stmts(cond, here, tested)
            self.stmts(body, here, inside)

        self.loop(once, state)

    def loop(self, body: Callable[[ShellState], None], state: ShellState) -> None:
        """A loop body judged once; where that pass changes the state, judged again from the join, issuing the
        identifiers the first pass issued."""
        commands, gaps, unspelled, python = len(self.commands), len(self.gaps), self.unspelled, len(self.python_read)
        issued = self.last_process, self.last_pipeline, self.last_loop
        trial = state.copy()
        body(trial)
        if trial.same(state) and trial.ends == state.ends:
            return
        del self.commands[commands:]
        del self.gaps[gaps:]
        del self.python_read[python:]
        self.unspelled = unspelled
        self.last_process, self.last_pipeline, self.last_loop = issued
        start = state.copy()
        start.merges([state, trial])
        body(start)
        state.merges([state, start])

    def function(self, command: Node, state: ShellState, place: Place) -> None:
        """A function definition: its body judged as run here, in the defining shell, the paths joined."""
        name = command.get("name")
        body = command["body"]
        if isinstance(body, str):
            raise KitFault(Prose("a function node's body is text, not a statement"))
        inside = state.copy()
        self.stmt(body, inside, place._replace(function=ShellText(name) if isinstance(name, str) else None,
                                               positionals=None))
        state.merges([state, inside])

    def substitutions(self, nodes: Sequence[Node], state: ShellState, place: Place) -> None:
        """Walk every substitution inside ``nodes``, in order, in no pipeline: `${ ;}` and `${| ;}` in the current
        shell, the others in a substitution's subshell; each reads the output of its commands, but `${| ;}`, and a
        `>( )` feeds their input instead."""
        alone = place._replace(pipelines=())
        for node in nodes:
            for nested in self.nested(node):
                form = nested.get("form")
                if form in ("brace", "brace_reply"):
                    saved = state.holds({OUTPUT: SUBSTITUTED} if form == "brace" else {})
                    self.stmts(nested.get("stmts", []), state, alone)
                    state.restores(saved)
                else:
                    inner = state.copy()
                    inner.ends[INPUT if nested.get("op") == ">(" else OUTPUT] = SUBSTITUTED
                    self.stmts(nested.get("stmts", []), inner, alone.into("substitution", self.new_process()))

    @staticmethod
    def nested(node: Node) -> list[Node]:
        """The substitutions inside a node, outermost only, in source order."""
        found: list[Node] = []
        pending: list[Node] = [node]
        while pending:
            here = pending.pop()
            if here.get("type") in SUBSTITUTIONS and here is not node:
                found.append(here)
                continue
            children: list[Node] = []
            for value in here.values():
                if isinstance(value, dict):
                    children.append(value)
                elif isinstance(value, list):
                    children.extend(item for item in value if isinstance(item, dict))
            pending.extend(reversed(children))
        return found

    def redirect(self, node: Node, state: ShellState) -> Redirect:
        """A redirection as the model records it."""
        word = node["word"]
        hdoc = node.get("hdoc")
        body = hdoc.get("body") if hdoc is not None else None
        return Redirect(ShellText(node.get("op") or ""), node.get("fd"), word_of(word, state, self.settings.env),
                        body if isinstance(body, str) else None)

    @staticmethod
    def table(state: ShellState, place: Place) -> dict[Descriptor, End]:
        """Where each descriptor of the shell at ``place`` ends in ``state``."""
        return {**place.ends, **state.ends}

    def opened(self, redirs: Sequence[Node], state: ShellState, place: Place, errors: bool
               ) -> dict[Descriptor, End]:
        """The ends ``redirs`` give descriptors 0, 1 and 2, performed left to right from those of the shell at
        ``place`` in ``state``, then fd 2 joining fd 1 where ``errors``; one naming or copying any other descriptor
        (past_streams()) makes all three unknown, as STARTED_ENDS holds them."""
        table = self.table(state, place)
        opened: dict[Descriptor, End] = {}
        for node in redirs:
            word = word_of(node["word"], state, self.settings.env)
            ends = STARTED_ENDS if past_streams(node, word) else self.ended(node, word, table)
            table.update(ends)
            opened.update(ends)
        if errors:
            opened[ERRORS] = table[OUTPUT]
        return opened

    def ended(self, node: Node, word: Word, table: Mapping[Descriptor, End]) -> dict[Descriptor, End]:
        """The ends one redirection gives the descriptors it names from ``table``: a heredoc's or here-string's text;
        for `>&` and `<&`, closed for `-`, a copy of the descriptor digits name, moved where a `-` follows them, and
        for a `>&` naming no descriptor of its own the file its word names, as `&>` does; unknown where the call does
        not make the word known or bash refuses it."""
        op, spelled = node.get("op"), node.get("fd")
        fd = Descriptor(int(spelled)) if spelled is not None else INPUT if op in INPUTS else OUTPUT
        both = spelled is None and op == ">&"
        if op in ("<<", "<<-", "<<<"):
            return {fd: TEXT}
        if op in (">&", "<&"):
            value = word.value
            if value is None:
                return {fd: UNKNOWN, ERRORS: UNKNOWN} if both else {fd: UNKNOWN}
            if value == "-":
                return {fd: CLOSED}
            if DESCRIPTOR.fullmatch(value):
                return {fd: table[Descriptor(int(value))]}
            if (moved := MOVED.fullmatch(value)) is not None:
                source = Descriptor(int(moved.group(1)))
                return {fd: table.get(source, UNKNOWN), source: CLOSED}
            if not both:
                return {fd: UNKNOWN}
        end = self.target(node, word, table)
        return {OUTPUT: end, ERRORS: end} if both or op in ("&>", "&>>") else {fd: end}

    @staticmethod
    def target(node: Node, word: Word, table: Mapping[Descriptor, End]) -> End:
        """The end a redirection's word names, from ``table``: the process substitution it is, /dev/null, the
        descriptor /dev/stdin, /dev/stdout, /dev/stderr or /dev/fd/N names, else the file it names, opened as its
        operator opens it."""
        if word.text[:2] in ("<(", ">("):
            return SUBSTITUTED
        value = word.value
        if value == "/dev/null":
            return NULL
        if value is not None and value in DEVICES:
            return table[DEVICES[value]]
        if value is not None and (device := DEVICE.fullmatch(value)) is not None:
            return table.get(Descriptor(int(device.group(1))), UNKNOWN)
        op = node.get("op")
        opening: Opening = "append" if op in APPENDS else "update" if op == "<>" else "read" if op == "<" else "write"
        return End("file", word, opening)

    def declaration(self, command: Node, state: ShellState, place: Place) -> None:
        """`export`, `declare`, `local` and their kin: the parameters they set, and the command they are."""
        args = command.get("args", [])
        self.substitutions(args, state, place)
        words: list[Word] = [Word(command["variant"], command["variant"], command["variant"])]
        for arg in args:
            value = arg.get("value")
            reading = word_of(value, state, self.settings.env) if isinstance(value, dict) else None
            name = arg.get("name")
            if isinstance(name, str) and not arg.get("naked"):
                known = None if reading is None else reading.value
                state.names[VarName(name)] = None if arg.get("append") or arg.get("array") else known
                start = "" if reading is None or reading.prefix is None else reading.prefix
                words.append(Word(arg["text"], None if known is None else ShellText(f"{name}={known}"),
                                  ShellText(f"{name}={start}")))
            else:
                words.append(reading if reading is not None else Word(arg["text"], arg["text"], arg["text"]))
        self.record(tuple(words), (), (), state, place, command["text"])

    def call(self, command: Node, redirs: Sequence[Node], state: ShellState, place: Place, errors: bool
             ) -> None:
        """A simple command: its substitutions first, then the command with its redirections held in its shell, fd 2
        then joining fd 1 where ``errors``, then what it does to its shell; an `exec` that runs no command keeps the
        redirections."""
        self.follow()
        assigns, args = command.get("assigns", []), command.get("args", [])
        self.substitutions([*assigns, *args, *redirs], state, place)
        saved = state.holds(self.opened(redirs, state, place, errors))
        env = self.settings.env
        words = tuple(word_of(arg, state, env) for arg in args)
        values: list[Assignment] = []
        for assign in assigns:
            value = assign.get("value")
            name = assign.get("name")
            if isinstance(name, str):
                simple = not assign.get("append") and not assign.get("index") and not assign.get("array")
                known = word_value(value, state, env) if simple and isinstance(value, dict) else None
                values.append(Assignment(VarName(name), ShellText("") if simple and value is None else known))
        recorded = tuple(self.redirect(r, state) for r in redirs)
        if not words:
            for assignment in values:
                state.names[assignment.name] = assignment.value
        got = peel(words, inherited(place, state).then(Environ(tuple(values))), place.dialect)
        rest, environ = got.words, got.environ
        self.unwrap(got, recorded, state, place, command["text"])
        self.wrote(rest, redirs, state)
        self.acts(rest, environ, command, redirs, state, place)
        if first_value(rest) != "exec":
            state.restores(saved)

    def acts(self, rest: tuple[Word, ...], environ: Environ, command: Node, redirs: Sequence[Node], state: ShellState,
             place: Place) -> None:
        """What the simple command ``command`` does to its shell, its words past its wrappers ``rest``, or reads as
        more of the call."""
        if not rest:
            return
        bash = place.dialect == "bash"
        name = rest[0].value
        if name in (CHANGES_DIRECTORY if bash else DASH_CHANGES_DIRECTORY):
            state.cwd = target_of(rest, state.cwd)
        elif name == "eval":
            self.evaluated(rest, state, place, command["text"])
        elif name == "." or (name == "source" and bash):
            self.sourced(rest, state, place, command["text"])
        elif name is None and "/" in rest[0].text:
            self.gaps.append(Gap(Prose("the path of the script it runs is not known from the call"), command["text"],
                                 SCRIPT_PATH_FORM))
        else:
            self.program(rest, environ, redirs, state, place, command["text"])

    def program(self, words: tuple[Word, ...], environ: Environ, redirs: Sequence[Node], state: ShellState, place: Place,
                text: ShellText) -> None:
        """What the program ``words`` run reads as more of the call: a carrier's text, an interpreter's script,
        Python's code, the commands a find's actions run, a script run by its path; zsh or fish a gap."""
        name = words[0].value
        base = None if name is None else ShellText(name.rsplit("/", 1)[-1])
        if base in CARRIERS and carried_text(words) is not None:
            self.carried(words, CARRIERS[base], environ, state, place, text)
        elif base in CARRIERS:
            self.interpreted(words, CARRIERS[base], environ, redirs, state, place, text)
        elif base is not None and PYTHON_NAME.fullmatch(base):
            self.python(words, environ, redirs, state, place, text)
        elif base == "find":
            self.finds(words, environ, state, place, text)
        elif (shell := foreign_shell(words)) is not None:
            self.gaps.append(Gap(Prose(f"it runs {shell}, which the kit does not read"), text, FOREIGN_FORM))
        elif name is not None and "/" in name:
            self.ran(words, environ, state, place, text)

    def evaluated(self, words: tuple[Word, ...], state: ShellState, place: Place, text: ShellText) -> None:
        """The text `eval` runs, read in the shell and the state of the `eval` itself."""
        if len(words) < 2:
            return
        if any(word.value is None for word in words[1:]):
            self.gaps.append(Gap(Prose("the text eval runs is not known from the call"), text, GAP_FORM))
            return
        joined_text = ShellText(" ".join(word.value or "" for word in words[1:]))
        self.script(joined_text, state, place._replace(depth=Count(place.depth + 1)))

    def carried(self, words: tuple[Word, ...], dialect: Dialect, environ: Environ, state: ShellState, place: Place,
                text: ShellText) -> None:
        """The text a `bash -c`, `sh -c` or `dash -c` run with ``environ`` runs, read in a carrier that starts in the
        directory alone and receives ``environ``."""
        word = carried_text(words)
        if word is None:
            return
        if word.value is None:
            self.gaps.append(Gap(Prose(f"the text {words[0].spelled} -c runs is not known from the call"), text,
                                 GAP_FORM))
            return
        self.carrier(word.value, dialect, environ, state, place)

    def carrier(self, carried: ShellText, dialect: Dialect, environ: Environ, state: ShellState, place: Place) -> None:
        """Read ``carried`` in a carrier of ``dialect`` that starts in the directory alone and receives
        ``environ``."""
        inner = place.into("carrier", self.new_process())._replace(dialect=dialect, depth=Count(place.depth + 1),
                                                                   positionals=None, environ=environ,
                                                                   ends=self.table(state, place))
        self.script(carried, ShellState(state.cwd), inner)

    def unwrap(self, got: Peel, redirs: tuple[Redirect, ...], state: ShellState, place: Place, text: ShellText) -> None:
        """Record each wrapper ``got`` peels, then the command it runs, then read the shell text the innermost
        wrapper runs where it runs one."""
        for depth, layer in enumerate(got.layers):
            self.record(layer, got.wrappers[:depth], redirs, state, place, text)
        self.record(got.words, got.wrappers, redirs, state, place, text)
        if got.carried is not None:
            self.wrapped(got.words[0].spelled, got.carried, state, place, text)

    def wrapped(self, holder: ShellText, carried: Carried, state: ShellState, place: Place, text: ShellText) -> None:
        """The shell text the wrapper ``holder`` runs, read as a `bash -c` text is in the dialect of the shell its
        row names; a gap where the call does not make the text or that shell known, or the kit does not read it."""
        if carried.word.value is None:
            self.gaps.append(Gap(Prose(f"the text {holder} runs is not known from the call"), text, GAP_FORM))
            return
        shell = self.shell_of(carried, state)
        if shell is None:
            self.gaps.append(Gap(Prose(f"the shell {holder} runs its text in is not known from the call"), text,
                                 WRAPPED_SHELL_FORM))
            return
        base = ShellText(shell.rsplit("/", 1)[-1])
        dialect = CARRIERS.get(base)
        if dialect is None:
            self.gaps.append(Gap(Prose(f"{holder} runs its text in {base}, which the kit does not read"), text,
                                 FOREIGN_FORM))
            return
        self.carrier(carried.word.value, dialect, carried.environ, state, place)

    def shell_of(self, carried: Carried, state: ShellState) -> ShellText | None:
        """The shell a wrapper's text runs in: the program its row names, or the value of the environment variable
        the row names in the environment that shell starts with; None where the call does not make it known."""
        runner = carried.runner
        if not runner.startswith("$"):
            return ShellText(runner) if runner else None
        name = VarName(runner[1:])
        environ = carried.environ
        for assignment in reversed(environ.assigns):
            if assignment.name == name:
                return assignment.value
        if environ.cleared or None in environ.unsets or name in environ.unsets:
            return None
        return lookup(name, state, self.settings.env)

    def wrote(self, words: tuple[Word, ...], redirs: Sequence[Node], state: ShellState, alone: bool = False) -> None:
        """Record the files a command whose words are ``words`` writes from ``state``'s directory: each output
        redirection's target, the last to take its stdout holding what it prints, an earlier one nothing, one of
        another descriptor unknown text; and a tee's operands its input. ``alone`` where the redirections are a
        compound command's, whose output the call does not make known."""
        env = self.settings.env
        given = self.fed(redirs, state).text
        values = [word.value for word in words]
        output = None if alone else printed(values, given)
        if values and values[0] == "tee":
            files, appends = teed(values)
            for file in files:
                self.write(file, given, appends, state)
        outputs = [node for node in redirs if node.get("op") in WRITES]
        stdout = [node.get("fd") in (None, "1") for node in outputs]
        last = max((index for index, taken in enumerate(stdout) if taken), default=None)
        for index, node in enumerate(outputs):
            text = (output if index == last else ShellText("")) if stdout[index] else None
            self.write(word_value(node["word"], state, env), text, node.get("op") in APPENDS, state)

    def fed(self, redirs: Sequence[Node], state: ShellState) -> Input:
        """What the last redirection of ``redirs`` onto descriptor 0 feeds it from ``state``: a heredoc's or
        here-string's text, the file a `<` or `<>` names, or nothing the call makes known (a copy of another
        descriptor, or no such redirection)."""
        env = self.settings.env
        given = Input()
        for node in redirs:
            op, fd = node.get("op"), node.get("fd")
            onto = fd if fd is not None else "0" if op in INPUTS else None
            if onto != "0":
                continue
            hdoc = node.get("hdoc")
            if op in ("<<", "<<-") and hdoc is not None:
                given = Input(heredoc(hdoc, state, env))
            elif op == "<<<":
                value = word_value(node["word"], state, env)
                given = Input(None if value is None else ShellText(value + "\n"))
            elif op in FILE_INPUTS:
                given = Input(file=word_of(node["word"], state, env))
            else:
                given = Input()
        return given

    def write(self, target: ShellText | None, text: ShellText | None, appends: bool, state: ShellState) -> None:
        """Record that the call writes ``text`` into ``target`` from ``state``'s directory, after what it holds where
        it ``appends``; a target the call does not make known is not recorded."""
        if target is None or (state.cwd is None and not target.startswith("/")):
            return
        path = ScriptPath(normpath(join(state.cwd or "/", target)))
        if appends:
            before = self.written[path] if path in self.written else self.disk_text(path)
            text = None if before is None or text is None else ShellText(before + text)
        self.written[path] = text

    @staticmethod
    def disk_text(path: ScriptPath) -> ShellText | None:
        """The text a file holds before the call appends to it: its own, nothing where it is missing, None where
        the kit cannot read it as text."""
        found = on_disk(path, Count(MAX_CALL_TEXT))
        return ShellText("") if found.trouble == MISSING else found.text

    def located(self, word: Word, state: ShellState, holder: ShellText, searched: tuple[ShellText, ...] = ()
                ) -> ScriptPath | None:
        """The path of the script ``word`` names from ``state``'s directory, a name without `/` looked for in each
        folder of ``searched`` in turn first (a relative one from the directory), where the call writes it or it is
        on disk; None, with a gap of the command ``holder``, where the call does not make it known."""
        name = word.value
        if name is None:
            self.gaps.append(Gap(Prose("the path of the script it runs is not known from the call"), holder,
                                 SCRIPT_PATH_FORM))
            return None
        if "/" not in name:
            for folder in searched:
                if state.cwd is None and not folder.startswith("/"):
                    continue
                path = ScriptPath(normpath(join(state.cwd or "/", folder, name)))
                if path in self.written or isfile(path):
                    return path
        if state.cwd is None and not name.startswith("/"):
            self.gaps.append(Gap(Prose(f"the script {name} is run from a directory the call does not make known"),
                                 holder, SCRIPT_PATH_FORM))
            return None
        return ScriptPath(normpath(join(state.cwd or "/", name)))

    def found(self, path: ScriptPath, holder: ShellText) -> ShellText | None:
        """The text of the script at ``path`` as the call leaves it: the last the call writes into it, else its file;
        None where it has none to read, with a gap of the command ``holder`` where the kit cannot find or read it or
        where the path names a descriptor, itself or through the links it passes (linked_descriptor), never read."""
        if linked_descriptor(path) is not None:
            self.gaps.append(Gap(Prose(f"the script {path} names a descriptor, which the kit does not read"), holder,
                                 DESCRIPTOR_FORM))
            return None
        if path in self.written:
            text = self.written[path]
            if text is None:
                self.gaps.append(Gap(Prose(f"the call writes the script {path} with text it does not make known"),
                                     holder, SCRIPT_WRITE_FORM))
            return text
        disk = on_disk(path, Count(MAX_CALL_TEXT))
        if disk.trouble is not None:
            form = SCRIPT_FIND_FORM if disk.trouble == MISSING else SCRIPT_READ_FORM
            self.gaps.append(Gap(Prose(f"the script {path} {disk.trouble}"), holder, form))
        return disk.text

    def script_at(self, path: ScriptPath, holder: ShellText) -> ShellText | None:
        """The text of the script at ``path``, as found() gives it to the command ``holder``; none, and no gap, for a
        script declared unread that the call does not write."""
        if path in self.settings.unread and path not in self.written:
            return None
        return self.found(path, holder)

    def path_list(self, state: ShellState) -> tuple[ShellText, ...]:
        """The folders of PATH as ``state`` holds it, none where the call does not make it known."""
        value = lookup(VarName("PATH"), state, self.settings.env)
        return () if value is None else tuple(ShellText(folder) for folder in value.split(":"))

    def ran(self, words: tuple[Word, ...], environ: Environ, state: ShellState, place: Place,
            text: ShellText) -> None:
        """A script run by its path, read from where script_at() reads it, in the dialect its shebang names, or its
        shell's where it has none; one whose shebang names zsh or fish is a gap, one naming another program is left
        to it."""
        path = self.located(words[0], state, text)
        script = None if path is None else self.script_at(path, text)
        if script is None:
            return
        shell = interpreter(script)
        if shell in FOREIGN_SHELLS:
            self.gaps.append(Gap(Prose(f"it runs {path}, a {shell} script, which the kit does not read"), text,
                                 SCRIPT_FOREIGN_FORM))
            return
        if shell is not None and PYTHON_NAME.fullmatch(shell):
            self.pythoned(PythonText(script), environ, state, place, text, Prose(f"of the script {path}"), path)
            return
        dialect = place.dialect if shell is None else CARRIERS.get(shell)
        if dialect is not None:
            self.run(script, dialect, words, environ, state, place, path, text)

    def interpreted(self, words: tuple[Word, ...], dialect: Dialect, environ: Environ, redirs: Sequence[Node],
                    state: ShellState, place: Place, text: ShellText) -> None:
        """The script an interpreter (bash, sh or dash) runs as its operand, read in that interpreter's dialect, a name
        without `/` looked for in the directory, then on PATH; with no operand, the one it reads on its input."""
        index = operand([word.value for word in words])
        if index is None:
            self.inputted(words, dialect, environ, redirs, state, place, text)
            return
        searched = (*(() if state.cwd is None else (ShellText(state.cwd),)), *self.path_list(state))
        path = self.located(words[index], state, text, searched)
        script = None if path is None else self.script_at(path, text)
        if script is not None:
            self.run(script, dialect, words[index:], environ, state, place, path, text)

    def inputted(self, words: tuple[Word, ...], dialect: Dialect, environ: Environ, redirs: Sequence[Node],
                 state: ShellState, place: Place, text: ShellText) -> None:
        """The script an interpreter with no operand and no -c text reads on its input, its name then the words from
        its first positional parameter on as its words: the heredoc's or here-string's text of its own, or the file a
        `<` of its own names where descriptor 0 ends in it, read as a script run by its path; any other input a gap,
        and nothing under -n or -o noexec."""
        start = input_parameters([word.value for word in words])
        if start is None:
            return
        given = self.fed(redirs, state)
        file = given.file if self.table(state, place)[INPUT].reach in ("file", "null") else None
        path = None if file is None else self.located(file, state, text)
        script = given.text if file is None else None if path is None else self.found(path, text)
        if script is not None:
            self.run(script, dialect, (words[0], *words[start:]), environ, state, place, path, text)
        elif file is None:
            self.gaps.append(Gap(Prose(f"the script {words[0].spelled} reads on its input is not known from the call"),
                                 text, GAP_FORM))

    def run(self, script: ShellText, dialect: Dialect, words: tuple[Word, ...], environ: Environ, state: ShellState,
            place: Place, path: ScriptPath | None, text: ShellText) -> None:
        """The script ``script`` at ``path`` (None for a text on an interpreter's input) run as ``words`` with
        ``environ``, its name then its arguments, in a script shell of its own that receives ``environ`` and starts in
        the directory with its assignments as its parameters."""
        names = {assignment.name: assignment.value for assignment in environ.assigns}
        inner = place.into("script", self.new_process())._replace(
            dialect=dialect, depth=Count(place.depth + 1),
            positionals=positionals(words[0].value, [word.value for word in words[1:]]), environ=environ,
            ends=self.table(state, place))
        self.script(script, ShellState(state.cwd, names), inner, path, text)

    def python(self, words: tuple[Word, ...], environ: Environ, redirs: Sequence[Node], state: ShellState, place: Place,
               text: ShellText) -> None:
        """The code a Python interpreter runs: its -c text, its script operand or the file a `<` of its own names where
        descriptor 0 ends in it (each read as a script), the heredoc or here-string it reads as its input, or the file
        beneath the directory its -m module names; a -c text, input or module the call does not make known is a
        gap."""
        run = invoked([word.value for word in words])
        if run is None:
            return
        value = words[run.index].value if run.index < len(words) else None
        spelled = None if value is None else ShellText(value[run.offset:])
        file: Word | None = None
        path: ScriptPath | None = None
        match run.kind:
            case "code":
                where, code = Prose(f"{words[0].spelled} -c runs"), spelled
            case "input" | "script":
                given = self.fed(redirs, state) if run.kind == "input" else Input(file=words[run.index])
                where = Prose(f"{words[0].spelled} reads on its input")
                code, file = given.text, given.file
            case "module":
                path = None if run.safe_path else self.module_file(spelled, words[0].spelled, state, text)
                code = None if path is None else self.found(path, text)
                where = Prose(f"of the module file {path}")
        if file is not None:
            path = self.located(file, state, text)
            code = None if path is None else self.found(path, text)
            where = Prose(f"of the script {path}")
        elif code is None and run.kind != "module":
            self.gaps.append(Gap(Prose(f"the code {where} is not known from the call"), text, GAP_FORM))
        if code is not None:
            self.pythoned(PythonText(code), environ, state, place, text, where, path)

    def module_file(self, name: ShellText | None, python: ShellText, state: ShellState, holder: ShellText
                    ) -> ScriptPath | None:
        """The file beneath ``state``'s directory the module ``name`` that the interpreter ``python`` runs names (its
        __main__.py for a package), where the call writes it or it is on disk; None for any other module, and for a
        name holding `/` or an empty part, which Python finds nowhere; None with a gap of the command ``holder``
        where the call does not make the name or the directory known, which Python looks in first."""
        if name is None:
            self.gaps.append(Gap(Prose(f"the module {python} -m runs is not known from the call"), holder,
                                 MODULE_FORM))
            return None
        if "/" in name or "" in name.split("."):
            return None
        if state.cwd is None:
            self.gaps.append(Gap(Prose(f"the module {name} is run from a directory the call does not make known"),
                                 holder, MODULE_FORM))
            return None
        base = join(state.cwd, *name.split("."))
        for candidate in (base + ".py", join(base, "__main__.py")):
            path = ScriptPath(normpath(candidate))
            if path in self.written or isfile(path):
                return path
        return None

    def pythoned(self, code: PythonText, environ: Environ, state: ShellState, place: Place, holder: ShellText,
                 where: Prose, file: ScriptPath | None = None) -> None:
        """Each command the Python code ``code`` starts, read from ``file`` where it is a file's, run by the
        interpreter the command ``holder`` runs with ``environ`` from ``state``'s directory, in a python process of
        its own that receives ``environ``; code Python cannot parse is a gap; the interpreter's command, the last
        recorded since a command is recorded just before what it runs is read, carries the code's loops that sleep
        and whether it sleeps outside them."""
        inner = place.into("python", self.new_process())._replace(dialect="sh", depth=Count(place.depth + 1),
                                                                  positionals=None, environ=environ)
        self.charge(ShellText(code), inner)
        reading = starts(code)
        if reading.fault is not None:
            self.gaps.append(Gap(Prose(f"Python cannot parse the code {where}: {reading.fault}"), holder,
                                 PYTHON_PARSE_FORM))
            return
        self.unspelled = Count(self.unspelled + reading.unspelled)
        self.python_read.append(PythonCode(code, file is None or file in self.written))
        self.commands[-1] = self.commands[-1]._replace(sleeps=reading.sleeps, pauses=reading.pauses)
        for start in reading.starts:
            self.launched(start, state, inner)

    def launched(self, start: Start, state: ShellState, place: Place) -> None:
        """The command a Python start runs from ``state``'s directory in the environment its python process received,
        under its own cwd= and env=, each of its descriptors reaching an end the call does not make known, its
        program wrappers peeled, then read as a call reads the programs it runs."""
        self.follow()
        place = place._replace(ends=STARTED_ENDS)
        cwd = state.cwd if start.cwd_known else None
        if start.cwd is not None:
            cwd = None if cwd is None and not start.cwd.startswith("/") else Directory(normpath(join(cwd or "/",
                                                                                                     start.cwd)))
        env = start.env
        changes = Environ(tuple(Assignment(name, value) for name, value in env.pairs), () if env.known else (None,),
                          env.replaced)
        words = tuple(Word(word, word, word) for word in start.words)
        got = peel(words, place.environ.then(changes), None)
        here = ShellState(cwd)
        self.unwrap(got, (), here, place, start.text)
        if got.words:
            self.program(got.words, got.environ, (), here, place, start.text)

    def finds(self, words: tuple[Word, ...], environ: Environ, state: ShellState, place: Place, text: ShellText
              ) -> None:
        """The command each -exec, -execdir, -ok and -okdir action of the find ``words`` run with ``environ`` from
        ``state``'s directory runs, each other primary of its expression read past its arguments; a gap where an
        action has no end or holds a word the call does not make known, or such a word stands where a primary does,
        the rest unread."""
        roots, index = find_starts(words)
        start = found_prefix(roots)
        while index < len(words):
            primary = words[index].value
            index += 1
            if primary is None:
                self.gaps.append(Gap(Prose("a word of the find expression is not known from the call"), text,
                                     FIND_FORM))
                return
            if primary not in FIND_ACTIONS:
                index += find_arguments(primary)
                continue
            end = action_end(words, Count(index), primary not in FIND_PROMPTED)
            if end is None or any(word.value is None for word in words[index:end]):
                why = "has no end" if end is None else "holds a word not known from the call"
                self.gaps.append(Gap(Prose(f"the find {primary} action {why}"), text, FIND_FORM))
                return
            self.acted(words[index:end], primary, start, environ, state, place, text)
            index = end + 1

    def acted(self, action: tuple[Word, ...], primary: ShellText, start: ShellText | None, environ: Environ,
              state: ShellState, place: Place, text: ShellText) -> None:
        """The command the find action ``primary`` runs, ``action`` its words, ``start`` how the paths find passes an
        -exec or -ok `{}` begin: a command of a find process of its own that receives ``environ`` and find's
        descriptors, its input /dev/null for -ok and -okdir, run from find's directory for -exec and -ok and from one
        the call does not make known for -execdir and -okdir, its wrappers peeled, then read as a call reads the
        programs it runs; a script path the call does not make known a gap."""
        if not action:
            return
        ends = self.table(state, place)
        if primary in FIND_PROMPTED:
            ends[INPUT] = NULL
        inner = place.into("find", self.new_process())._replace(dialect="sh", environ=environ, ends=ends)
        here = ShellState(None if primary in FIND_ELSEWHERE else state.cwd)
        passed = ShellText("./") if primary in FIND_ELSEWHERE else start
        got = peel(tuple(placed(word, passed) for word in action), inner.environ, None)
        self.unwrap(got, (), here, inner, text)
        first = got.words[0]
        if first.value is None and "/" in first.text:
            self.gaps.append(Gap(Prose("the path of the script it runs is not known from the call"), text,
                                 SCRIPT_PATH_FORM))
        else:
            self.program(got.words, got.environ, (), here, inner, text)

    def sourced(self, words: tuple[Word, ...], state: ShellState, place: Place, text: ShellText
                ) -> None:
        """The script `source` or `.` reads, in the current shell and state, from where script_at() reads it, a name
        without `/` looked for on PATH (or the folders a bash 5.3 -p names), then in the directory; the arguments
        after its name, where it has any, are its positional parameters."""
        args = list(words[1:])
        searched = self.path_list(state)
        if len(args) > 1 and args[0].value == "-p":
            searched = () if args[1].value is None else tuple(ShellText(f) for f in args[1].value.split(":"))
            args = args[2:]
        if args and args[0].value == "--":
            args = args[1:]
        if not args:
            return
        path = self.located(args[0], state, text, (*searched, *(() if state.cwd is None else (ShellText(state.cwd),))))
        script = None if path is None else self.script_at(path, text)
        if script is None:
            return
        at = place.positionals
        if len(args) > 1:
            at = positionals(None if at is None else at.zero, [word.value for word in args[1:]])
        self.script(script, state, place._replace(depth=Count(place.depth + 1), positionals=at), path, text)

    def record(self, words: tuple[Word, ...], wrappers: tuple[ShellText, ...], redirs: tuple[Redirect, ...],
               state: ShellState, place: Place, text: ShellText) -> None:
        """Add one command, run from ``state``'s directory at ``place``."""
        ends = self.table(state, place)
        self.commands.append(Command(words, wrappers, redirs, place.shells, place.function, state.cwd, text,
                                     place.processes, place.pipelines, place.loops,
                                     Streams(ends[INPUT], ends[OUTPUT], ends[ERRORS])))


def loop_kind(command: Node) -> LoopKind:
    """The kind of the while or for node ``command``: an until where a while node says until, a select where a for
    node says select."""
    if command.get("type") == "while":
        return "until" if command.get("until") else "while"
    return "select" if command.get("select") else "for"


def inherited(place: Place, state: State) -> Environ:
    """What the process at ``place`` received from the command that started it, each name assigned taking the value
    ``state``'s shell holds for it now, since a shell exports a name it received."""
    names = state.names
    return place.environ._replace(assigns=tuple(given._replace(value=names.get(given.name, given.value))
                                                for given in place.environ.assigns))


def build(text: ShellText, cwd: Directory | None, settings: ReadSettings) -> Call:
    """The call model of ``text`` run from ``cwd``."""
    walk = Walk(settings)
    walk.script(text, ShellState(cwd), Place((), None, CALL_DIALECT, Count(0)))
    return Call(text, tuple(walk.commands), tuple(walk.gaps), walk.unspelled, tuple(walk.python_read))
