#!/home/nicolas/.local/bin/python3.14
"""Hold every watch of a pull request's checks to pr-checks-watch, with its status the task's own.

THE RULE (user, 2026-10-15). A pull request opened or pushed is babysat: a background watch ends at the first
red check or when every check has ended, and what it found is read from where the harness keeps a task's
result, its exit status and its own output. The watch is `pr-checks-watch N` (in ~/.local/bin), which waits for
the checks to end and then takes pr-all-green's verdict on the head it read first, the reading a merge goes
through: exit 0 GREEN, 1 RED, 2 UNREAD, 3 INCOMPLETE, 4 on a usage error, the status its own and the lines that
explain it on its stdout.

WHY A HOOK AND NOT A NOTE. The rule was two memories. On 2026-10-15 the watch of #409 was armed as they say,
`gh pr checks 409 --watch --fail-fast --interval 20`, and did its job: it ended at the first red check and
wrote EXIT=1 to its log. The call chained `; echo "EXIT=$?" >> log; cp log ...` after it, so the task's exit
status was the cp's, and its output was empty because every line went to the log. After a clear, the next
context read "completed (exit code 0)" over no output as green. The memory's own prescription, written at the
call site, was the shape that masked the verdict.

WHAT IS REFUSED, in a Bash call read by `_commands.expand` (carriers and scripts included):
  * `gh pr checks ... --watch` run bare: its status says nothing about the rollup once a check has failed or
    none is registered, and its tables are not a verdict. Run `pr-checks-watch N` instead.
  * `pr-checks-watch` whose verdict cannot reach the task: its stdout sent to a file or piped into another
    command; a command after it joined by `;`, a newline or `||`, which makes that command's status the
    task's; the watcher started as a job of the call's own shell (`&`, `&!`, `&|`, nohup, setsid -f), which
    ends the call at once with status 0 and no output while the job runs on, the #409 failure in a new coat;
    or the call run in the foreground, where the Bash tool's cap of ten minutes kills a watch of a full lane
    set before its verdict (run_in_background is read from the tool input). `&&` after it keeps a failure's
    status and is allowed, as is a redirection of stderr alone; a compound's closing word (`fi`, `done`,
    `esac`, `}`) after it is not a command, since the compound's status is the watcher's.
The command after it is read at the call's top level, where the task's status is decided; the pipe, the
redirection, the job and the bare gh anywhere the reader follows.

A SECOND WATCH OF ONE PULL REQUEST is refused too: `pr-checks-watch N` while a process of the same watcher for
the same N runs in the same git tree (read from /proc: the watcher by its script's name as a command or as its
interpreter's first operand, N its first operand past `--record`'s value, the tree the one holding its working
directory). The watch armed before a /clear runs on and notifies the new context, but a call cannot see it: `ps`
in the tool's sandbox showed none four times (2026-10-15, 10-22, 10-24, 10-27), a memory saying so each time, and
a duplicate was armed each time. The hook runs outside the sandbox, where /proc shows it.

Text the reader cannot follow goes ahead with a note on stderr; a fault of the hook's own is reported (exit 1)
and the call goes ahead. Exit 2 refuses, naming each problem and the shape to use.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import NamedTuple, NewType

import _commands as C
import _shell

# The text of a Bash call; a word of a command as it receives it; a directory as the event spells it; where
# a redirection sends a stream, as written; a line the hook prints; a position in the lexer's tokens; the
# hook's answer.
CallText = NewType("CallText", str)
CommandWord = NewType("CommandWord", str)
Directory = NewType("Directory", str)
RedirectTarget = NewType("RedirectTarget", str)
Prose = NewType("Prose", str)
TokenIndex = NewType("TokenIndex", int)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

WATCHER = "pr-checks-watch"
# The operators after the watcher that hand the task another command's status (a pipe and a `&` are read from
# the command itself), the one that keeps its own, and the words that close a compound command, after which
# the scan goes on: the compound's status is the watcher's.
MASKING_OPERATORS = frozenset({";", "||", ";;", ";&", ";;&"})
KEEPING_OPERATORS = frozenset({"&&"})
CLOSERS = frozenset({"fi", "done", "esac", "}"})
STDOUT_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", ">&"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=.*", re.S)  # a variable set before the command

ADVICE = """BLOCKED: this call watches a pull request's checks in a way whose verdict cannot be trusted.

{problems}

Watch with the script whose exit status and stdout are the verdict, alone or before `&&`, in the background:

  pr-checks-watch N --record "$S/prN-checks.log"

It ends at the first red check or when every check has ended, then takes pr-all-green's verdict on the head it
read first, the reading a merge goes through: exit 0 GREEN, 1 RED, 2 UNREAD, 3 INCOMPLETE (re-arm), 4 on a
usage error. The task's notification carries that status, and its output the lines that explain it."""

DUPLICATE = """BLOCKED: this call arms a second watch of a pull request whose first watch is running.

{problems}

The running watch notifies this context when it ends, a clear between them or not: wait for that notification.
A call's `ps` cannot see a watch an earlier call started; the hook reads /proc outside the sandbox. Only if that
watch is known to be wrong, end it first (`kill PID`) and arm again."""


# A process number; a pull request as the watcher's operand spells it; a git tree's root.
ProcessId = NewType("ProcessId", int)
PullRequest = NewType("PullRequest", str)
Tree = NewType("Tree", str)


class LiveWatch(NamedTuple):
    """A watcher process running now: its number, the pull request it watches and the tree it runs in."""

    pid: ProcessId
    pr: PullRequest
    tree: Tree


def _is_watcher(argv: list[CommandWord]) -> bool:
    """Say whether ``argv`` runs pr-checks-watch, by name or by path."""
    return bool(argv) and C.prog(argv[0]) == WATCHER


def _bare_watch(argv: list[CommandWord]) -> bool:
    """Say whether ``argv`` is `gh pr checks` in watch mode."""
    return (len(argv) >= 3 and C.prog(argv[0]) == "gh" and argv[1:3] == ["pr", "checks"]
            and any(arg == "--watch" or arg.startswith("--watch=") for arg in argv[3:]))


def _stdout_target(cmd: _shell.Command) -> RedirectTarget | None:
    """Where ``cmd``'s stdout goes when a redirection sends it anywhere but the task, else None."""
    for redirect in cmd.redirects:
        if redirect.op in STDOUT_REDIRECTS and redirect.fd in (None, 1):
            target = redirect.target
            return RedirectTarget(target.text if target is not None else redirect.op)
    return None


def _followed_by_a_command(toks: list[_shell._Tok], index: TokenIndex) -> Prose | None:
    """The operator after the watcher at ``index`` that hands another command the status, or None."""
    k = index + 1
    while k < len(toks):
        tok = toks[k]
        if tok.kind in ("word", "redir", "arith"):
            k += 1
            continue
        if tok.kind == "eof":
            return None
        operator = "\n" if tok.kind == "nl" else str(tok.value)
        if operator == ")":  # a subshell's end: what follows the group decides the status
            k += 1
            continue
        if operator not in MASKING_OPERATORS and operator != "\n":
            return None
        j = k + 1
        while j < len(toks) and toks[j].kind == "nl":
            j += 1
        if j >= len(toks) or toks[j].kind == "eof":
            return None
        closer = toks[j].value
        if toks[j].kind == "word" and isinstance(closer, _shell._WordTok) and closer.plain in CLOSERS:
            k = j + 1  # the compound ends here with the watcher's status; what follows it decides
            continue
        return Prose("a newline" if operator == "\n" else f"`{operator}`")
    return None


def masked_at_top_level(text: CallText) -> list[Prose]:
    """One line per watcher at the call's top level followed by a command that takes the status.

    A word names the watcher only where a command stands: at the start, after an operator or a newline,
    after a word that leads a command (`then`, `do`, `!`, `{` ...) and after assignments; the same path as an
    argument (`git add .local/bin/pr-checks-watch`) is text.
    """
    found: list[Prose] = []
    toks = _shell._Lexer(text).run()
    at_command = True
    for index, tok in enumerate(toks):
        if tok.kind == "op":
            at_command = True
            continue
        if tok.kind == "nl":
            at_command = True
            continue
        if tok.kind != "word":
            continue
        word = tok.value
        plain = word.plain if isinstance(word, _shell._WordTok) else None
        if at_command and plain is not None and _ASSIGNMENT.fullmatch(plain):
            continue
        leads = plain in _shell._LEADS_COMMAND
        if at_command and plain is not None and C.prog(plain) == WATCHER:
            after = _followed_by_a_command(toks, TokenIndex(index))
            if after is not None:
                found.append(Prose(f"  * `{WATCHER}` is followed by {after} and another command, whose status "
                                   "becomes the task's; end the call with the watch, or join with `&&`"))
        at_command = leads
    return found


def _watched_pr(args: list[CommandWord]) -> PullRequest | None:
    """The pull request a watcher's operands name: the first that is no option and no `--record` value."""
    words = iter(args)
    for word in words:
        if word == "--record":
            next(words, None)
        elif not word.startswith("-"):
            return PullRequest(word)
    return None


def _tree(directory: Directory) -> Tree:
    """The git tree holding ``directory``, or the directory itself outside every tree."""
    return Tree(_shell.tree_of(directory) or os.path.realpath(directory))


def live_watches() -> list[LiveWatch]:
    """Every watcher process running now, read from /proc."""
    found: list[LiveWatch] = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/cmdline", "rb") as fh:
                raw = fh.read()
            cwd = Directory(os.readlink(f"/proc/{entry.name}/cwd"))
        except OSError:
            continue  # gone, or not ours to read
        words = [CommandWord(word.decode(errors="replace")) for word in raw.split(b"\0") if word]
        if words and _is_watcher(words):
            operands = words[1:]
        elif len(words) > 1 and C.is_python(words[0]) and C.prog(words[1]) == WATCHER:
            operands = words[2:]
        else:
            continue
        pr = _watched_pr(operands)
        if pr is not None:
            found.append(LiveWatch(ProcessId(int(entry.name)), pr, _tree(cwd)))
    return found


def duplicates(text: CallText, cwd: Directory) -> list[Prose]:
    """One line per watcher the call starts whose pull request a running watcher in the same tree watches."""
    _shell.reset_budget()
    found: list[Prose] = []
    running: list[LiveWatch] | None = None
    for inv in C.expand(text, cwd):
        argv = [CommandWord(word) for word in inv.core.argv]
        if not _is_watcher(argv):
            continue
        pr = _watched_pr(argv[1:])
        if pr is None:
            continue
        if running is None:
            running = live_watches()
        tree = _tree(Directory(inv.cmd.cwd or cwd))
        found.extend(Prose(f"  * a watch of #{pr} is already running in {tree} (pid {watch.pid})")
                     for watch in running if watch.pr == pr and watch.tree == tree)
    return found


def problems(text: CallText, cwd: Directory, background: bool) -> list[Prose]:
    """One line per way the call's watch hides its verdict from the task."""
    _shell.reset_budget()
    found: list[Prose] = []
    for inv in C.expand(text, cwd):
        argv = [CommandWord(word) for word in inv.core.argv]
        if _bare_watch(argv):
            found.append(Prose("  * `gh pr checks --watch` run bare: its exit status and tables are no verdict"))
            continue
        if not _is_watcher(argv):
            continue
        if inv.cmd.pipe_out:
            found.append(Prose(f"  * `{WATCHER}` is piped into another command, which takes the status and "
                               "the verdict lines"))
        if inv.cmd.background or inv.cmd.disowned or inv.core.detached is not None:
            found.append(Prose(f"  * `{WATCHER}` is started as a job of the call's own shell, so the call ends "
                               "at once with status 0 and no output while the watch runs on; run the call "
                               "itself in the background, the watch in its foreground"))
        target = _stdout_target(inv.cmd)
        if target is not None:
            found.append(Prose(f"  * `{WATCHER}`'s stdout is sent to {target}, so the task's output loses the "
                               "verdict; keep the tables with --record instead"))
        if not background:
            found.append(Prose(f"  * `{WATCHER}` runs in the foreground, where the Bash tool's ten-minute cap "
                               "kills it before its verdict; set run_in_background"))
    found.extend(masked_at_top_level(text))
    return found


def main() -> ExitStatus:
    """Read the hook event on stdin and answer it."""
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"pr-watch-guard: the event could not be read, the call goes ahead: {exc}", file=sys.stderr)
        return REPORTED
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return ALLOW
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return ALLOW
    text = tool_input.get("command")
    if not isinstance(text, str) or not text.strip():
        return ALLOW
    background = tool_input.get("run_in_background") is True
    raw_cwd = event.get("cwd")
    cwd = Directory(raw_cwd if isinstance(raw_cwd, str) and raw_cwd else os.getcwd())
    try:
        found = problems(CallText(text), cwd, background)
        doubled = duplicates(CallText(text), cwd)
    except (_shell.ParseError, _shell.BudgetExceeded) as exc:
        print(f"pr-watch-guard: this call could not be read, it goes ahead unchecked: {exc}", file=sys.stderr)
        return ALLOW
    except Exception as exc:  # noqa: BLE001 - a fault of the reader: reported, never a silent block
        print(f"pr-watch-guard: internal error, the call goes ahead: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return REPORTED
    if found:
        print(ADVICE.format(problems="\n".join(dict.fromkeys(found))), file=sys.stderr)
    if doubled:
        print(DUPLICATE.format(problems="\n".join(dict.fromkeys(doubled))), file=sys.stderr)
    return REFUSE if found or doubled else ALLOW


if __name__ == "__main__":
    sys.exit(main())
