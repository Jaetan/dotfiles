"""The shared shell does not move: a Bash call changes directory only in a subshell, or names absolute paths.

THE RULE (user, 2026-10-14). Every Bash call of a session runs in one shell whose directory persists from call to
call, and the calls one response sends run at once against it, so a `cd` one of them makes moves the others while
they run. The one directory change a call may make in its own shell is to the project root, CLAUDE_PROJECT_DIR in
every hook's environment, where the shell starts anyway.

A command of the call's own shell (the call model's in_own_shell: the top level, a brace group, an if, loop or case
body, a function body, judged where it is defined, the text eval runs, and `${ ;}`; bash runs every pipeline element
in a process of its own)
that is `cd`, `pushd` or `popd`, past wrappers such as `builtin` or `command`, is refused unless the directory it
changes to is the root. `popd`, a bare `cd`, `cd -` and an operand the call does not make known change to a directory
the call does not name, and are refused too; with no project root every change is.
"""

import os

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import CHANGES_DIRECTORY, target_of
from dispatcher.verdict import Prose, Verdict, refuse

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash")})
UNNAMED = "a directory this call does not name"
ADVICE = """this call changes the directory of the shell every Bash call shares.

{problems}

The calls a response sends run at once in one shell, so a `cd` here moves the others mid-run, and the shell keeps it
for the calls after. Change directory inside a subshell, where the change dies with the call:

  (cd /abs/dir && taskset -c 0-19 <command> > "$LOG" 2>&1; echo "EXIT=$?" >> "$LOG")

or name absolute paths and change no directory. A `cd` to the project root ({root}) is allowed, since the shell
starts there."""


def judge(event: Event) -> Verdict | None:
    """Refuse a Bash call whose own shell changes directory anywhere but the project root."""
    root = event.project_root
    found: list[Prose] = []
    for command in event.call().commands:
        if not command.in_own_shell or command.name not in CHANGES_DIRECTORY:
            continue
        target = target_of(command.words, command.cwd)
        if root is not None and target is not None and os.path.realpath(target) == os.path.realpath(root):
            continue
        spelled = " ".join(word.spelled for word in command.words)
        found.append(Prose(f"  * `{spelled}` moves the shared shell to {target or UNNAMED}"))
    if not found:
        return None
    return refuse(Prose(ADVICE.format(problems="\n".join(found), root=root or "unset")))
