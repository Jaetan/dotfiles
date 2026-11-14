"""A watch of a pull request's checks hands its verdict to the task: `pr-checks-watch N` alone, in the background.

THE RULE (user, 2026-10-15, made blunt 2026-11-09). A pull request opened or pushed is babysat by `pr-checks-watch N`
(~/.local/bin), whose exit status and stdout are pr-all-green's verdict on the head it read first: exit 0 GREEN, 1
RED, 2 UNREAD, 3 INCOMPLETE, 4 on a usage error. The failure this holds: a `gh pr checks --watch` that ended at a red
check, chained `; echo "EXIT=$?" >> log; cp log ...`, left the task the cp's status 0 over an empty output, which the
next context read as green.

A Bash call is refused where any command of the call model runs `gh pr checks` with a word `--watch` or
`--watch=VALUE`, whatever its other words. A call running pr-checks-watch, by its base name or path or as the script
a Python, bash, sh or dash runs, is refused unless the watcher is the only command of the call's own text (a wrapper
is a command of its own, the commands of a script file the call runs are not the call's) and the call runs with
run_in_background true, so neither another command's status nor the tool's ten-minute cap reaches the task.

EXCEPTIONS, each with the failure it holds:
  * a watcher run as a job, in a background shell (`&`, a coprocess), is refused alone: the call ends at once with
    status 0 while the watch runs on;
  * a watcher whose stdout does not reach the call's own is refused alone: the task's output holds no verdict;
  * a SECOND WATCH, a watcher of N while a watcher process for the same N runs in the same git tree, is refused: a
    call's sandbox shows no other process, so its `ps` never sees a watch an earlier call armed, and duplicates were
    armed. The hook reads the proc tree of the session state's places (state/session.py): a watcher as this rule
    reads one, N its first operand that is no option and no --record value, the tree the nearest folder at or above
    the working directory holding a `.git`, that folder itself outside every tree. A watcher whose N or folder the
    call does not make known arms none.
"""

import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NamedTuple

from declarations.lookup import start_of
from dispatcher.core import Event, HookEvent, ToolName, state_places
from dispatcher.model import CALLED, Command, Word
from dispatcher.python import invoked
from dispatcher.scripts import operand
from dispatcher.tree import Count, ShellText
from dispatcher.verdict import Prose, Verdict, refuse
from rules.job_lifetime import base
from state.session import Pid

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash")})
WATCHER = ShellText("pr-checks-watch")
SHELLS = frozenset({ShellText("bash"), ShellText("sh"), ShellText("dash")})
PYTHON = re.compile(r"python[0-9.]*")
BARE = Prose("is `gh pr checks --watch`: its status says nothing of the rollup once a check has failed or none is "
             "registered, and its tables are no verdict")
ALONE = Prose("is not the only command of the call's text (a wrapper, a list, a pipeline or a substitution beside it), "
              "so the task can take another command's status")
JOB = Prose("runs as a job (`&` or a coprocess), which ends the call at once with status 0 while the watch runs on")
OUTPUT = Prose("sends its stdout elsewhere than the call's own, so the task's output holds no verdict")
FOREGROUND = Prose("runs in the foreground, where the Bash tool's ten-minute cap kills the watch before its verdict")
NOTIFIED = Prose("its notification will come to this context when it ends")
ADVICE = """this call watches a pull request's checks in a shape whose verdict cannot reach the task.

{problems}

Watch with the script whose exit status and stdout are the verdict, alone in a call run with run_in_background:

  pr-checks-watch N

(--record FILE keeps its tables in FILE too). It ends at the first red check or when every check has ended, then takes
pr-all-green's verdict on the head it read first: exit 0 GREEN, 1 RED, 2 UNREAD, 3 INCOMPLETE (re-arm), 4 on a usage
error. The task's notification carries that status, and its output the lines that explain it."""
DUPLICATE = """this call arms a second watch of a pull request whose watch is running.

{problems}

Wait for the running watch's notification, which comes to this context whether or not it was cleared since. A call's
`ps` shows no watch an earlier call armed, its sandbox showing no other process; the hook reads /proc outside it. Only
where that watch is known to be wrong, end it first (`kill PID`) and arm again."""


class Watch(NamedTuple):
    """A watch of a pull request: the pull request as the watcher's operand spells it, None where unknown, and the
    git tree it runs in."""

    pr: ShellText | None
    tree: Path


class Live(NamedTuple):
    """A watcher process the proc tree shows: its pid and its watch."""

    pid: Pid
    watch: Watch


def watches(words: Sequence[Word]) -> bool:
    """Whether ``words`` run `gh pr checks` with a word --watch or --watch=VALUE, an unknown word read by its prefix."""
    spelled = [word.value or word.prefix or "" for word in words[3:]]
    return (base(words) == "gh" and [word.value for word in words[1:3]] == ["pr", "checks"]
            and any(text == "--watch" or text.startswith("--watch=") for text in spelled))


def watcher(words: Sequence[Word]) -> Count | None:
    """The position of pr-checks-watch's own word where ``words`` run it, by its name or path, or as the script a
    Python, bash, sh or dash runs; None where they do not."""
    name = base(words)
    values = [word.value for word in words]
    if name in SHELLS:
        at = operand(values)
    elif name is not None and PYTHON.fullmatch(name):
        run = invoked(values)
        at = run.index if run is not None and run.kind == "script" else None
    else:
        at = Count(0)
    return at if at is not None and base(words[at:]) == WATCHER else None


def problems(command: Command, own: Sequence[Command], background: bool) -> list[Prose]:
    """A line per way the watcher ``command`` keeps its verdict from the task, ``own`` the commands of the call's own
    text and ``background`` whether the call runs in the background."""
    failed = ((list(own) != [command], ALONE), ("background" in command.shells, JOB),
              (command.streams.output != CALLED, OUTPUT), (not background, FOREGROUND))
    spelled = " ".join(word.spelled for word in command.words)
    return [Prose(f"  * `{spelled}` {line}") for fails, line in failed if fails]


def pull_request(values: Sequence[ShellText | None]) -> ShellText | None:
    """The pull request a watcher whose operands have ``values`` watches: the first that is no option and no --record
    value, None where the call does not make that word known or there is none."""
    rest = iter(values)
    for value in rest:
        if value is None or not value.startswith("-"):
            return value
        if value == "--record":
            _ = next(rest, None)
    return None


def tree(folder: Path) -> Path:
    """The git tree holding ``folder``: the nearest folder at or above it, resolved and out of any `.git`, holding a
    `.git`; that folder itself outside every tree."""
    start = start_of(folder)
    return next((above for above in (start, *start.parents) if (above / ".git").exists()), start)


def running(proc: Path) -> Iterator[Live]:
    """Each watcher process the proc tree ``proc`` shows whose command line names its pull request."""
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw, cwd = (entry / "cmdline").read_bytes(), (entry / "cwd").readlink()
        except OSError:  # ended, or not the hook's user's to read
            continue
        words = [Word(text, text, text) for text in map(ShellText, raw.decode(errors="replace").split("\0")) if text]
        at = watcher(words)
        pr = None if at is None else pull_request([word.value for word in words[at + 1:]])
        if pr is not None:
            yield Live(Pid(int(entry.name)), Watch(pr, tree(cwd)))


def seconds(proc: Path, armed: Sequence[Watch]) -> list[Prose]:
    """A line per watcher process the proc tree ``proc`` shows whose watch is one of ``armed``."""
    return [Prose(f"  * a watch of #{live.watch.pr} is already running in {live.watch.tree} (pid {live.pid}): "
                  f"{NOTIFIED}") for live in running(proc) if live.watch in armed]


def judge(event: Event) -> Verdict | None:
    """Refuse a Bash call that watches a pull request's checks in a shape whose verdict cannot reach the task, or arms
    a second watch of a pull request a running watcher watches in the same tree."""
    commands = event.call().commands
    own = [command for command in commands if "script" not in command.shells]
    tool_input = event.payload.get("tool_input")
    background = isinstance(tool_input, dict) and tool_input.get("run_in_background") is True
    found: list[Prose] = []
    armed: list[Watch] = []
    for command in commands:
        if watches(command.words):
            found.append(Prose(f"  * `{' '.join(word.spelled for word in command.words)}` {BARE}"))
        elif (at := watcher(command.words)) is not None:
            found.extend(problems(command, own, background))
            if command.cwd is not None:
                armed.append(Watch(pull_request([word.value for word in command.words[at + 1:]]),
                                   tree(Path(command.cwd))))
    twice = seconds(state_places(event.places).proc, armed) if armed else []
    texts = [Prose(ADVICE.format(problems="\n".join(found)))] if found else []
    texts += [Prose(DUPLICATE.format(problems="\n".join(twice)))] if twice else []
    return refuse(Prose("\n\n".join(texts))) if texts else None
