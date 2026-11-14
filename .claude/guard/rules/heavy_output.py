"""A heavy run keeps its output whole: its output and errors end in a file, its input is no pipe, no Monitor call
and no Python code starts it, it has its call to itself and no head runs beside it.

THE RULE (user, 2026-09-25; the form without taskset, 2026-11-08). A long run whose output reaches the tool result, a
pipe or a device is read cut short and run again to read the rest; one whose output ends whole in a log is read in
later calls as often as needed. Of each command of a Bash or Monitor call that heavy() (dispatcher/heavy.py) answers a
row or `unknown` for under the declaration of the command's directory (the session's where the call does not make it
known), a command whose name the call does not make known being judged as a heavy one, the output and the errors
(Command.streams) end in a file: an End of reach `file` whose path, placed against the command's directory and
normalised, lies outside /dev/ and /proc/. Every other reach, and every device or proc path, is refused, named as the
reach or the path. Its input is no pipe. A Monitor call watches work and starts none, so it is refused every such
command whatever its streams. Python code starting such a command (Command.shells holding "python") is refused it
whatever its streams, the refusal saying to run it from the shell, `> "$LOG" 2>&1`, alone in its call: the call
model reads none of a Python start's stream keywords. A `&` job is judged as any command, its lifetime being
rules/job_lifetime.py's, and the CPUs a run takes are the session check's (rules/session_cpus.py). A call running
such a command runs nothing else, so the run's log is queried in later calls: every command of the call's own text
that runs a word or reads a file whole as bash's `$(< FILE)` does, its carriers included and the scripts and Python
code it runs excluded, is one of them or one an exception admits; each other is named, a `$(< FILE)` so spelled. No
command the call model holds, scripts and Python starts included, is head, which reads a run's output cut short.

EXCEPTIONS, each with the failure it holds:
  - a `file` end whose word's value the call does not make known, such as `"$LOG"`, or a relative path from a
    directory it does not make known, is a file, else the form the refusal names, `> "$LOG" 2>&1`, is refused;
  - beside a heavy command, a name of BESIDE (by its base name) runs, else the `cd` before the run and the `echo` of
    its exit status after it are named as other work;
  - beside it, a command that only starts the command after it, a wrapper the model peeled, a carrier's start or the
    start of a script, Python or find action a heavy command runs in, counts as the command it starts, else `taskset`
    or `bash -c` before a heavy command, or the start of the script running it, is named as other work;
  - beside it, a command inside a substitution that runs before the first of them runs, the model not recording what
    a substitution feeds, else `LOG=$(mktemp)` before the run is named as other work.
"""

from collections.abc import Collection, Mapping, Sequence
from os.path import join, normpath
from pathlib import Path

from declarations.schema import PathText
from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.heavy import heavy
from dispatcher.model import SUBSTITUTED, Command, End, ProcessId, Reach, Redirect, Shell, Word
from dispatcher.tree import ShellText
from dispatcher.verdict import Prose, Verdict, refuse
from dispatcher.words import Directory
from rules.job_lifetime import base, starts_shell, wraps

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
MONITOR = ToolName("Monitor")
DEVICES = ("/dev/", "/proc/")
WHERE: Mapping[Reach, Prose] = {
    "null": Prose("/dev/null"), "pipe": Prose("a pipe"), "substitution": Prose("a command or process substitution"),
    "call": Prose("the tool result"), "text": Prose("a heredoc or here-string"), "closed": Prose("a closed descriptor"),
    "unknown": Prose("an end the call does not make known")}
BESIDE: tuple[ShellText, ...] = tuple(map(ShellText, ("cd", "pushd", "popd", "echo", "printf", "mkdir", "export", "set",
                                                         "true", ":")))
HEAD = ShellText("head")
PYTHON: Shell = "python"
OUTSIDE: frozenset[Shell] = frozenset({"script", PYTHON})
STARTED = Prose('Python code starts it, so run it from the shell, `> "$LOG" 2>&1`, alone in its call')
ADVICE = """this call runs heavy commands without keeping their output whole in a log read in later calls.

{{problems}}

A heavy run's output and errors go whole to a log, so a long run never has to run twice to be read. Give the run a
call of its own, run with run_in_background, in the form

  <heavy command> > "$LOG" 2>&1; echo "EXIT=$?" >> "$LOG"

its input no pipe, its log a file outside /dev/ and /proc/, no command beside it but
{beside}, a substitution only before it and no head in the call,
and query the log in later calls with grep -c, grep -n or sed -n of a range.
A Monitor call watches work and starts none.""".format(beside=", ".join(f"`{name}`" for name in BESIDE))


def placed(word: Word, cwd: Directory | None) -> PathText | None:
    """The path ``word`` names from ``cwd``, normalised; None where its value, or ``cwd`` for a relative one, is not
    known from the call."""
    if word.value is None or (cwd is None and not word.value.startswith("/")):
        return None
    return PathText("/" + normpath(join(cwd or "/", word.value)).lstrip("/"))


def lost(end: End, cwd: Directory | None) -> Prose | None:
    """Where ``end`` takes a stream out of every file, None where it ends in one."""
    if end.reach != "file":
        return WHERE[end.reach]
    path = None if end.file is None else placed(end.file, cwd)
    if path is None or not path.startswith(DEVICES):
        return None
    return Prose(path)


def only_runs(command: Command, after: Command, inside: Collection[ProcessId]) -> bool:
    """Whether ``command`` only runs ``after``, the command next to it: a wrapper the model peeled, the start of a
    carrier, or the start of a script, Python or find action process one of ``inside`` names."""
    return wraps(command, after) or (starts_shell(command, after) or acts(command, after)) and (
        after.shells[-1] == "carrier" or after.processes[-1] in inside)


def acts(command: Command, after: Command) -> bool:
    """Whether ``command`` is a find starting the action process ``after`` runs in."""
    return (len(after.processes) == len(command.processes) + 1 and after.processes[:-1] == command.processes
            and after.shells[-1] == "find")


def read_whole(command: Command) -> ShellText | None:
    """The file ``command`` reads into its substitution as bash's `$(< FILE)` does, as written: a command of no words
    whose shell is a substitution reading its output, a `<( )` among them since the model holds no form, its one
    redirection a `<` onto descriptor 0; None for any other."""
    if command.words or command.shells[-1:] != ("substitution",) or command.streams.output != SUBSTITUTED:
        return None
    match command.redirs:
        case (Redirect(op="<", fd=None | "0", target=target),):
            return target.text
        case _:
            return None


def spelled(command: Command) -> ShellText:
    """``command`` as the refusal names it: its text, a `$(< FILE)` so spelled."""
    file = read_whole(command)
    return command.text if file is None else ShellText(f"$(< {file})")


def shared(commands: Sequence[Command], runs: Sequence[Command]) -> list[Prose]:
    """The refusal's lines for what ``commands`` run beside their heavy commands ``runs``: the other work, as the
    module docstring reads it, and each head."""
    if not runs:
        return []
    first = commands.index(runs[0])
    inside = {process for run in runs for process in run.processes}
    others = [spelled(command) for at, command in enumerate(commands)
              if (command.words or read_whole(command) is not None) and command not in runs
              and base(command.words) not in BESIDE and OUTSIDE.isdisjoint(command.shells)
              and not (at < first and "substitution" in command.shells)
              and not (at + 1 < len(commands) and only_runs(command, commands[at + 1], inside))]
    cuts = [command.text for command in commands if base(command.words) == HEAD]
    return [Prose(f"  * {what}: {', '.join(f'`{text}`' for text in dict.fromkeys(texts))}")
            for what, texts in (("it runs other work beside them", others),
                                ("it runs head, which reads output cut short", cuts)) if texts]


def streamed(command: Command) -> list[Prose]:
    """The faults of where ``command``'s output, errors and input end: each stream outside a file, an input pipe."""
    streams = command.streams
    faults = [Prose(f"{what} {where}") for what, where in (("its output ends in", lost(streams.output, command.cwd)),
                                                            ("its errors end in", lost(streams.errors, command.cwd)))
              if where is not None]
    return faults + [Prose("its input is a pipe")] if streams.input.reach == "pipe" else faults


def judge(event: Event) -> Verdict | None:
    """Refuse a call running a heavy command whose output or errors end outside a file, whose input is a pipe, which
    a Monitor call or Python code starts, beside other work, or beside head."""
    found: list[Prose] = []
    runs: list[Command] = []
    for command in event.call().commands:
        if heavy(command, event.declaration(Path(command.cwd or event.cwd or "/"))) is None:
            continue
        runs.append(command)
        python = PYTHON in command.shells
        faults = [] if python else streamed(command)
        if event.tool == MONITOR:
            faults.append(Prose("a Monitor call starts it"))
        if python:
            faults.append(STARTED)
        if faults:
            found.append(Prose(f"  * `{command.text}`: {', '.join(faults)}"))
    found.extend(shared(event.call().commands, runs))
    if not found:
        return None
    return refuse(Prose(ADVICE.format(problems="\n".join(found))))
