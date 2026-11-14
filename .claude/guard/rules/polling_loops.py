"""A Bash call waits through the harness's notification or a Monitor, never through a loop of its own that polls.

THE RULE. A Bash call is refused where a loop it runs sleeps, waits with a timeout or spins, wherever that loop's code
lies: the call's own text, a script or module it runs, a file it writes, in a git work tree or not. Such a loop holds
the call while it runs, and a spinning one burns a CPU, while the harness re-invokes the session when a
run_in_background command exits and the Monitor tool is the loop made for a condition only an external system
changes. A project's own waiting tool is declared under `[unread_scripts]`, the last exception below. The call model
shows what refuses:
  - a command inside a while, until, for or select loop (Command.loops, its condition or its body) that sleeps or
    waits with a timeout, its wrappers peeled: sleep; tail, cat, read, inotifywait or pidwait under timeout; read with
    -t among the options before its names; inotifywait with -t or --timeout before a `--`; and such a command in the
    body of a function a command of the loop calls by name, through the functions that body calls (Command.function
    names the body's function);
  - a while or until loop that spins: one whose body runs `:`, true and continue alone, and one whose condition and
    body each run a command, every one a check (test, `[`, kill, pgrep, pidof, ps, grep, stat, ls, false) or `:` or
    true. The model holds no command for `[[`, `((` or let, so a part holding only those runs none, and a wrapper
    peeled is a command of neither kind;
  - a Python interpreter whose code loops around time.sleep or asyncio.sleep (Command.sleeps), and one whose code
    sleeps outside such a loop (Command.pauses) run in a shell loop, as a sleep there is.
A command is named by its first word's basename as spelled, so a lone `[`, which no glob expands, reads as itself. A
heredoc, a string or a commit message naming the shape is data the model holds no command for; a sleep through perl or
ruby counts for nothing, since the model reads neither language.

EXCEPTIONS, each with the refusal it would otherwise make:
  - a Monitor call is not judged: its loop polls by design, a `while kill -0 PID; do sleep 5; done` among them;
  - a while or until loop whose condition runs read alone does not spin: it ends with its input, as
    `while read -r l; do :; done < list` does;
  - a script the project declares under `[unread_scripts]`, run by its path, through bash, sh or dash, or sourced, and
    not written by the call, is never read, so the model holds none of its commands: the repository's own waiting
    tool, `bash tools/wait.sh`. The declaration covers no Python form: a script a Python interpreter runs is read.
"""

from collections.abc import Iterator
from os.path import basename
from typing import NewType

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import Command, Loop, LoopId, Word
from dispatcher.tree import ShellText
from dispatcher.verdict import Prose, Verdict, refuse
from dispatcher.wrappers import WRAPPERS

OptionLetter = NewType("OptionLetter", str)
type Bodies = dict[ShellText | None, list[Command]]
type Held = list[tuple[Loop, Command]]

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash")})
TIMED = frozenset(ShellText(name) for name in ("tail", "cat", "read", "inotifywait", "pidwait"))
SPINS = frozenset(ShellText(name) for name in (":", "true", "continue"))
CHECKS = frozenset(ShellText(name) for name in (":", "true", "test", "[", "kill", "pgrep", "pidof", "ps", "grep",
                                                "stat", "ls", "false"))
READ_VALUED = frozenset(OptionLetter(letter) for letter in "adinNptu")
INOTIFY_VALUED = frozenset(OptionLetter(letter) for letter in "eot")
ADVICE = """this call polls in a loop.

{problems}

A loop that sleeps, waits with a timeout or spins until something changes holds the call while it runs. Wait the way
the harness already offers instead:
  * Run the work with run_in_background: the harness re-invokes you when it exits, so no watcher is needed.
  * A pid you did not start with the tool: read its output file once per turn, or `wait` on it if it is a child of
    this shell.
  * A condition only an external system changes (CI, a deploy): watch it with the Monitor tool, whose loop is
    allowed."""


def name_of(command: Command) -> ShellText | None:
    """The basename of the command's first word as spelled, None for a command of no words."""
    return ShellText(basename(command.words[0].spelled)) if command.words else None


def spelled(command: Command) -> ShellText:
    """The command's words as spelled."""
    return ShellText(" ".join(word.spelled for word in command.words))


def timed(words: tuple[Word, ...], valued: frozenset[OptionLetter], long: ShellText | None) -> bool:
    """Whether the options among ``words`` past the name set a timeout: a t in a cluster of short options, each
    letter of ``valued`` taking the rest of its cluster or the next word as its value, or the long option ``long``;
    the options end at `--`, and where ``long`` is None, as read reads them, at the first word that is none."""
    index = 1
    while index < len(words):
        word = words[index].spelled
        index += 1
        if word == "--":
            return False
        if word.split("=", 1)[0] == long:
            return True
        if not word.startswith("-") or word.startswith("--"):
            if long is None:
                return False
            continue
        for at, letter in enumerate(word[1:], 2):
            if letter == "t":
                return True
            if letter in valued:
                index += at == len(word)
                break
    return False


def waits(command: Command) -> bool:
    """Whether the command sleeps or waits with a timeout, a Python interpreter whose code sleeps outside its loops
    among them."""
    name = name_of(command)
    if command.pauses or name == "sleep" or name in TIMED and ShellText("timeout") in command.wrappers:
        return True
    if name == "read":
        return timed(command.words, READ_VALUED, None)
    return name == "inotifywait" and timed(command.words, INOTIFY_VALUED, ShellText("--timeout"))


def reached(command: Command, bodies: Bodies) -> Iterator[Command]:
    """The command, then every command of the body of each function it calls by name, through the functions those
    bodies call, each body once."""
    names = {command.name}
    for _ in bodies:
        names |= {run.name for name in names for run in bodies.get(name, [])}
    yield command
    yield from (run for name in names for run in bodies.get(name, []))


def polls(held: Held, bodies: Bodies) -> Iterator[Prose]:
    """A line for each way the loop whose commands ``held`` lists, each with its part, polls."""
    kind = held[0][0].kind
    for loop, command in held:
        for run in reached(command, bodies):
            if waits(run):
                route = "" if run is command else f", through the function `{command.name}`"
                yield Prose(f"  * `{spelled(run)}` sleeps or waits with a timeout in the {loop.part} of the {kind} "
                            f"loop{route}")
    if kind not in ("while", "until"):
        return
    ran = [(loop.part, name_of(command)) for loop, command in held if name_of(command) not in WRAPPERS]
    body = [name for part, name in ran if part == "body"]
    condition = [name for part, name in ran if part == "condition"]
    if body and all(name in SPINS for name in body) and not (condition and all(name == "read" for name in condition)):
        yield Prose(f"  * the {kind} loop spins: its body runs `{'`, `'.join(dict.fromkeys(map(str, body)))}` alone")
    elif body and condition and all(name in CHECKS for name in body + condition):
        yield Prose(f"  * the {kind} loop polls: it runs `{'`, `'.join(dict.fromkeys(map(str, condition + body)))}` "
                    "alone, only checks")


def judge(event: Event) -> Verdict | None:
    """Refuse a Bash call holding a shell loop that sleeps, waits with a timeout or spins, or Python code whose loop
    sleeps."""
    bodies: Bodies = {}
    loops: dict[LoopId, Held] = {}
    found: list[Prose] = []
    for command in event.call().commands:
        if command.function is not None:
            bodies.setdefault(command.function, []).append(command)
        for loop in command.loops:
            loops.setdefault(loop.loop, []).append((loop, command))
        found.extend(Prose(f"  * `{spelled(command)}` runs Python code whose loop at line {sleeping.line} sleeps")
                     for sleeping in command.sleeps)
    for held in loops.values():
        found.extend(polls(held, bodies))
    if not found:
        return None
    return refuse(Prose(ADVICE.format(problems="\n".join(dict.fromkeys(found)))))
