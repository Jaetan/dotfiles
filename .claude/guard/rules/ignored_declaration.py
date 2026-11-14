"""A repository's declaration that git ignores is named once per claude process, so that a `.claude/guard.toml` no
clone, branch or review carries cannot go unnoticed.

THE RULE (user, 2026-11-07). A project keeps its declaration in `.claude/guard.toml`; one that ignores the `.claude/`
folder whole ignores `.claude/*` and re-includes `!.claude/guard.toml` instead, since git re-includes no file inside a
folder it ignores. At SessionStart, and at the first call the process makes from a work tree it has not checked, the
rule asks git whether it ignores that work tree's declaration (declarations/ignored.py) and keeps in the process's
session state that the work tree is checked, whatever the answer, so each is checked once per process whichever of its
agents calls. An ignored declaration gets one note naming the file, the rule hiding it and the fix; the call goes on.
Outside every repository the rule reads no state. The record is a `notice` whose id starts with IDENT.
"""

from pathlib import Path
from typing import Final

from dispatcher.core import Event, HookEvent
from dispatcher.verdict import Prose, Verdict, context

EVENTS: frozenset[HookEvent] = frozenset({"SessionStart", "PreToolUse"})
TOOLS = None
KIND: Final = "notice"
IDENT = "ignored-declaration:"
NOTE = ("git ignores the declaration {file}, by `{pattern}` at {source}:{line}, so no clone, branch or review carries "
        "it. The fix: `!.claude/guard.toml` after that rule, and `.claude/*` in place of a rule ignoring the `.claude/` "
        "folder whole, since git re-includes no file inside a folder it ignores.")


def judge(event: Event) -> Verdict | None:
    """Name the declaration of the event's work tree where git ignores it, the first time the process checks it."""
    if event.cwd is None:
        return None
    facts = event.declaration(Path(event.cwd))
    if facts.work_root is None:
        return None
    from declarations.ignored import hidden  # imported for an event this rule judges, never at its load
    from state.session import RecordId

    ident = RecordId(IDENT + str(facts.work_root))
    session = event.state()
    if any(record.ident == ident for record in session.records(KIND)):
        return None
    hiding = hidden(facts)
    session.record(KIND, ident, {})
    if hiding is None:
        return None
    return context(Prose(NOTE.format(file=hiding.declaration, pattern=hiding.pattern, source=hiding.source,
                                     line=hiding.line)))
