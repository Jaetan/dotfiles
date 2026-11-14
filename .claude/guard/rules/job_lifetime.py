"""Nothing a call starts outlives it unseen, or dies unseen when it ends.

THE RULE (user, 2026-11-09). Every Bash or Monitor call runs in a scope stopped when its shell exits: whatever is left
in it gets SIGTERM, then SIGKILL at 2 s (measured 2026-11-08), so a job nothing reaps dies unseen and a unit started
outside it runs on unwatched. A command of the call model, wrappers peeled and carriers and scripts read, is refused
where it is

  a background job (a `&` job or a coprocess, nohup's among them: a Command whose shells hold "background") that no
  later command of the very shell that started it, in the same function body, reaps by the words `wait` alone, with no
  wrapper;

  disown; a setsid given -f or --fork, alone or in a single-dash bundle, before its first word not led by `-`; a
  start-stop-daemon given -b or --background before `--`;

  a tmux whose words name new-session or new with -d, or new-window, neww, split-window, splitw, respawn-pane,
  respawnp, respawn-window or respawnw;

  a systemctl whose words name start, restart, try-restart, reload-or-restart, try-reload-or-restart or isolate, or
  enable or reenable with --now;

  a systemd-run with none of --wait, --pipe, -P, --pty, -t, --pty-late, --shell, -S or --scope before its first word
  not led by `-`, no option's value skipped.

No `$!`, job spec, kill, trap or option prefix reaps or names anything, so some calls that end every job they start
are refused in the name of a small model.

EXCEPTIONS (1)

  A trap's text, its first operand past a leading `--` where another follows, a double-quoted one read as the text it
  holds, is judged as more of the call, its jobs reaped within it alone: without it `trap 'sleep 9 &' EXIT` passes.
"""

import re
from collections.abc import Sequence
from typing import NamedTuple

from dispatcher.core import Event, HookEvent, ToolName, known
from dispatcher.model import GAP_FORM, Call, Command, ProcessId, Word, build
from dispatcher.tree import Count, ShellText
from dispatcher.verdict import Prose, Unreadable, Verdict, refuse

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})

Chain = tuple[ProcessId, ...]


def spellings(spelled: ShellText) -> frozenset[ShellText]:
    """The words a space-separated list holds."""
    return frozenset(ShellText(word) for word in spelled.split())


STARTED_SHELLS = spellings(ShellText("carrier script python"))
WAIT = [ShellText("wait")]
TMUX_SESSIONS = spellings(ShellText("new-session new"))
TMUX_PANES = spellings(ShellText(
    "new-window neww split-window splitw respawn-pane respawnp respawn-window respawnw"))
STARTING = spellings(ShellText("start restart try-restart reload-or-restart try-reload-or-restart isolate"))
ENABLING = spellings(ShellText("enable reenable"))
BACKGROUND = spellings(ShellText("-b --background"))
WAITING = spellings(ShellText("--wait --pipe -P --pty -t --pty-late --shell -S --scope"))
QUOTED_BODY = re.compile(r'(?<!\\)(?:\\\\)*"')
ESCAPED = re.compile(r"\\([\"\\$`])|\\\n")
LONG_SHOWN = 100
ESCAPES = Prose("which escapes the call's scope: it runs on after the call, unwatched")
DIES = Prose("which the call's end kills before anything reads it")
REAP_FORM = Prose("End the shell that started it with `wait`, or run a long run with run_in_background.")
FORK_FORM = Prose("Run it in the foreground, or a long run with run_in_background.")
ADVICE = """this call starts what outlives it unseen, or what its end kills unseen.

{problems}

Every call runs in a scope stopped when its shell exits: what is left in it gets SIGTERM, then SIGKILL 2 s later,
nohup and setsid notwithstanding, and only a unit escapes it, running on with nothing reading it. Run a long run with
the Bash tool's run_in_background, whose notification says when it ends; end the shell that starts jobs side by side
with a plain `wait`; start a unit with systemd-run --wait (or --pipe), which the call waits for."""


class Job(NamedTuple):
    """A background job: the process its `&` or coprocess forks, the shell that started it, and its first command and
    that command's index."""

    process: ProcessId
    shell: Chain
    first: Count
    head: Command


def base(words: Sequence[Word]) -> ShellText | None:
    """The base name of the command ``words`` run, None where the call does not make it known."""
    return ShellText(words[0].value.rsplit("/", 1)[-1]) if words and words[0].value else None


def shown(words: Sequence[Word]) -> ShellText:
    """The words as a refusal quotes them, cut at LONG_SHOWN characters."""
    spelled = " ".join(word.spelled for word in words)
    return ShellText(spelled if len(spelled) <= LONG_SHOWN else spelled[:LONG_SHOWN] + "...")


def wraps(command: Command, after: Command | None) -> bool:
    """Whether ``command`` is a wrapper the model peeled, ``after`` the command it runs."""
    return (after is not None and after.processes == command.processes and after.text == command.text
            and len(after.wrappers) == len(command.wrappers) + 1)


def starts_shell(command: Command, after: Command | None) -> bool:
    """Whether ``command`` starts the carrier, script or Python process ``after`` runs in."""
    return (after is not None and len(after.processes) == len(command.processes) + 1
            and after.processes[:-1] == command.processes and after.shells[-1] in STARTED_SHELLS)


def options(words: Sequence[ShellText]) -> list[ShellText]:
    """The words before the first not led by `-`."""
    for index, word in enumerate(words):
        if not word.startswith("-"):
            return list(words[:index])
    return list(words)


def form_line(command: Command) -> Prose | None:
    """The refusal's line for a detaching form ``command`` runs, None where it runs none."""
    name = base(command.words)
    said = [ShellText(word.value) if word.value is not None else ShellText("") for word in command.words[1:]]
    if name == "disown":
        return Prose(f"  * `{shown(command.words)}` takes a job out of its shell's table, so no `wait` reaps it, "
                     f"{DIES}. {REAP_FORM}")
    if name == "setsid" and any(word == "--fork" or (not word.startswith("--") and "f" in word)
                                for word in options(said)):
        return Prose(f"  * `{shown(command.words)}` forks a process no shell waits for, {DIES}. Run it without -f, or "
                     "a long run with run_in_background.")
    if name == "start-stop-daemon" and BACKGROUND & set(said[:said.index("--")] if "--" in said else said):
        return Prose(f"  * `{shown(command.words)}` forks a daemon that stays in the call's scope, {DIES}. "
                     f"{FORK_FORM}")
    if name == "tmux" and (TMUX_PANES & set(said) or (TMUX_SESSIONS & set(said) and "-d" in said)):
        return Prose(f"  * `{shown(command.words)}` starts a tmux session, window or pane, a scope of its own, "
                     f"{ESCAPES}. Run the command itself, or a long run with run_in_background.")
    if name == "systemctl" and (STARTING & set(said) or (ENABLING & set(said) and "--now" in said)):
        return Prose(f"  * `{shown(command.words)}` starts a unit, {ESCAPES}. Run the work as systemd-run --wait.")
    if name == "systemd-run" and not WAITING & set(options(said)):
        return Prose(f"  * `{shown(command.words)}` starts a unit it does not wait for, {ESCAPES}. Give it --wait (or "
                     "--pipe) before its command, and the call waits for the unit.")
    return None


def jobs_of(commands: Sequence[Command]) -> list[Job]:
    """Every background job the commands run, in the order each starts."""
    found: dict[ProcessId, Job] = {}
    for index, command in enumerate(commands):
        for depth, shell in enumerate(command.shells):
            if shell == "background" and command.processes[depth] not in found:
                found[command.processes[depth]] = Job(command.processes[depth], command.processes[:depth],
                                                      Count(index), command)
    return list(found.values())


def reaped(job: Job, commands: Sequence[Command]) -> bool:
    """Whether a later command of the shell that started ``job``, in the same function body, is a plain `wait`."""
    return any(command.processes == job.shell and command.function == job.head.function and not command.wrappers
               and [word.value for word in command.words] == WAIT for command in commands[job.first + 1:])


def job_line(job: Job) -> Prose:
    """The refusal's line for a job its shell does not reap."""
    head = ShellText(job.head.text if len(job.head.text) <= LONG_SHOWN else job.head.text[:LONG_SHOWN] + "...")
    return Prose(f"  * the background job starting `{head}` is reaped by no plain `wait` after it in the shell that "
                 f"started it, {DIES}. {REAP_FORM}")


def trap_text(command: Command) -> ShellText | None:
    """The text the trap ``command`` sets, as the module docstring reads it; None where it is no trap or sets none."""
    operands = command.words[1:]
    if base(command.words) != "trap":
        return None
    if operands and operands[0].value == "--":
        operands = operands[1:]
    if len(operands) < 2:
        return None
    word = operands[0]
    if word.value is not None:
        return word.value
    if len(word.text) > 1 and word.text[0] == word.text[-1] == '"' and not QUOTED_BODY.search(word.text[1:-1]):
        return ShellText(ESCAPED.sub(lambda found: found.group(1) or "", word.text[1:-1]))
    raise Unreadable(Prose(f"the text a trap runs is not made known by the call: `{word.text}`"), GAP_FORM)


def lines_of(event: Event, call: Call) -> list[Prose]:
    """Every refusal line of ``call``, each trap's text judged as a call of its own."""
    commands = call.commands
    lines = [line for command in commands if (line := form_line(command)) is not None]
    lines.extend(job_line(job) for job in jobs_of(commands) if not reaped(job, commands))
    for command in commands:
        text = trap_text(command)
        if text is None:
            continue
        try:
            sub = known(build(text, command.cwd, event.reading))
        except Unreadable as fault:
            raise Unreadable(Prose(f"the text the trap `{shown(command.words)}` runs: {fault.reason}"),
                             fault.form) from None
        lines.extend(lines_of(event, sub))
    return lines


def judge(event: Event) -> Verdict | None:
    """Refuse a call that starts what outlives it unseen, or what its end kills unseen."""
    lines = lines_of(event, event.call())
    if not lines:
        return None
    return refuse(Prose(ADVICE.format(problems="\n".join(dict.fromkeys(lines)))))
