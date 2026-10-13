#!/home/nicolas/.local/bin/python3.14
"""Refuse a Bash call that moves the shared shell's directory anywhere but the project root.

THE RULE (user, 2026-10-14). Every Bash call of a session runs in one shell
whose working directory persists from call to call, and the calls one
response sends run at once against it. A `cd` one of them makes therefore
moves the others while they run, and their failures read as defects of the
change under test: `No module named 'tools'`, `FileNotFoundError` on a path
that exists, `taskset: failed to execute .venv/bin/basedpyright`. So a call
changes directory only inside a subshell, `(cd DIR && ...)`, where the change
dies with it, or names absolute paths. The one `cd` it may make in its own
shell is to the project root, where the shell starts anyway, so it moves no
sibling off the root.

WHY A HOOK AND NOT A NOTE. The rule was a memory from 2026-09-26 and was
broken twice on 2026-10-14 in one shape: a gate that runs from python/,
written `cd <repo>/python && taskset ... basedpyright ...`, sent beside a call
rooted at the repository. Nothing at the call site says which calls run beside
it; a hook sees one call at a time, so it refuses the bare `cd` whether or not
a sibling exists.

HOW A CALL IS READ. The text is read by `_shell.py` as the tool's zsh reads
it, the call's own shell given a scope of its own. A command in that scope is
one the shell itself runs: the call's top level, a brace group, an if, while,
for or case body, a function the call defines and calls, and the text `eval`
runs, which the hook reads in that scope. A subshell, a `&` job, a
substitution and a pipeline element each run in a process of their own, so a
`cd` there moves nothing another call sees, but for the last element of a
pipeline, which zsh runs in the current shell (`ls | cd /tmp` leaves it in
/tmp); a carrier (`bash -c`, a script) is a process of its own. In the call's scope,
`cd`, `pushd` and `popd`, also behind `builtin` or `command`, are refused
unless the directory they change to is the project root, $CLAUDE_PROJECT_DIR:
`cd` with no operand goes to $HOME, `cd -` and `popd` to a directory the call
does not name (`popd`'s operands are places on its stack, not directories),
and an operand the reader cannot know could be anywhere, so each of those is
refused too. Options before the operand (`-L`, `-P`, `-q`) are read past.

Text the reader cannot follow goes ahead, the hook saying so on stderr, as the
kit's other guards let through what they cannot judge; a fault of the hook's
own is reported and the call goes ahead. Exit 0 lets the call through, 2
refuses it naming each `cd` and the subshell form, 1 reports a fault.
"""

from __future__ import annotations

import json
import os
import sys
from typing import NewType

import _shell

# The text of a Bash call; a directory as the call or the environment spells
# it; a line the hook prints; the hook's answer.
CallText = NewType("CallText", str)
Directory = NewType("Directory", str)
Prose = NewType("Prose", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

# The scope the call's own shell runs in: no scope the reader makes is negative.
OWN_SCOPE = -1
CHANGES_DIRECTORY = frozenset({"cd", "pushd", "popd"})
PREFIXES = frozenset({"builtin", "command"})

ADVICE = """BLOCKED: this call changes the directory of the shell every Bash call shares.

{problems}

The calls a response sends run at once in one shell, so a `cd` here moves the
others mid-run, and the shell keeps it for the calls after. Change directory
inside a subshell, where the change dies with the call:

  (cd /abs/dir && taskset -c 0-19 <command> > "$LOG" 2>&1; echo "EXIT=$?" >> "$LOG")

or name absolute paths and change no directory. A `cd` to the project root
({root}) is allowed, since the shell starts there."""


def _spelled(word: _shell.Word) -> Prose:
    """Return the word as the command receives it where known, as written where not."""
    return Prose(word.value if word.value is not None else (word.partial or word.text))


def _operands(words: list[_shell.Word]) -> list[_shell.Word]:
    """Return the words after the command's name, past its options, `-` among them."""
    rest = words[1:]
    while rest and _spelled(rest[0]).startswith("-"):
        if _spelled(rest[0]) == "--":
            return rest[1:]
        rest = rest[1:]
    return rest


def _target(words: list[_shell.Word], cwd: Directory | None) -> Directory | None:
    """Return the directory the command ``words`` changes to from ``cwd``, or None if unsaid."""
    if _spelled(words[0]) == "popd":
        return None
    operands = _operands(words)
    if len(operands) != 1 or cwd is None:
        return None
    value = operands[0].value
    if value is None:
        return None
    return Directory(os.path.normpath(os.path.join(cwd, os.path.expanduser(value))))


def _in_own_shell(command: _shell.Command) -> bool:
    """Say whether the call's own shell runs ``command``, a zsh pipeline's last element among them.

    The reader gives a command the call's own shell as its job's shell when
    that shell runs it, or runs the foreground pipeline it is an element of;
    of a pipeline's elements, zsh runs the last, the one piping into nothing,
    in that shell, and each other one in a subshell.
    """
    return command.job_scope == OWN_SCOPE and not command.pipe_out


def problems(text: CallText, cwd: Directory, root: Directory | None) -> list[Prose]:
    """Return one line per command of ``text`` that moves its own shell off ``root``."""
    _shell.reset_budget()
    return _moves(text, cwd, root)


def _moves(text: CallText, cwd: Directory | None, root: Directory | None) -> list[Prose]:
    """Return ``problems``' lines for ``text`` read from ``cwd`` in the call's own shell."""
    found: list[Prose] = []
    for command in _shell.commands(
        text, cwd, env=dict(os.environ), scope=OWN_SCOPE, dialect="zsh"
    ):
        words = command.words
        if not words or not _in_own_shell(command):
            continue
        if _spelled(words[0]) in PREFIXES and len(words) > 1:
            words = words[1:]
        if _spelled(words[0]) == "eval" and command.scope == OWN_SCOPE:
            evaluated = CallText(" ".join(_spelled(word) for word in words[1:]))
            before = Directory(command.cwd) if command.cwd is not None else None
            found.extend(_moves(evaluated, before, root))
            continue
        if _spelled(words[0]) not in CHANGES_DIRECTORY:
            continue
        before = Directory(command.cwd) if command.cwd is not None else None
        target = _target(words, before)
        if root is not None and target is not None and os.path.realpath(target) == os.path.realpath(root):
            continue
        where = "a directory this call does not name" if target is None else target
        spelled = " ".join(_spelled(word) for word in words)
        found.append(Prose(f"  * `{spelled}` moves the shared shell to {where}"))
    return found


def main() -> ExitStatus:
    """Read the hook event on stdin and answer it."""
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"bare-cd-guard: the event could not be read, the call goes ahead: {exc}", file=sys.stderr)
        return REPORTED
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return ALLOW
    tool_input = event.get("tool_input")
    text = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(text, str) or not text.strip():
        return ALLOW
    raw_cwd = event.get("cwd")
    cwd = Directory(raw_cwd if isinstance(raw_cwd, str) and raw_cwd else os.getcwd())
    raw_root = os.environ.get("CLAUDE_PROJECT_DIR")
    root = Directory(raw_root) if raw_root else None
    try:
        found = problems(CallText(text), cwd, root)
    except (_shell.ParseError, _shell.BudgetExceeded) as exc:
        print(f"bare-cd-guard: this call could not be read, it goes ahead unchecked: {exc}", file=sys.stderr)
        return ALLOW
    except Exception as exc:  # noqa: BLE001 - a fault of the reader: reported, never a silent block
        print(f"bare-cd-guard: internal error, the call goes ahead: {type(exc).__name__}: {exc}", file=sys.stderr)
        return REPORTED
    if found:
        print(ADVICE.format(problems="\n".join(found), root=root or "unset"), file=sys.stderr)
        return REFUSE
    return ALLOW


if __name__ == "__main__":
    sys.exit(main())
