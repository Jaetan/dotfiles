"""A turn does not end on a count of checks below its total that it leaves without a disposition.

THE RULE (user, 2026-10-10; rulings 2026-11-10). A check that did not pass is fixed now or becomes a new task in the
same turn. So at a Stop, each count of passes below its total in the turn's last message (`206 of 207 pass`, `passed
20 of 21`) is refused unless its paragraph, or its list item, says the checks have not ended (pending, still running,
queued, in progress) or names a task id (`N.43`) the task list holds now and did not hold at the previous turn's end.
A task already on the list, the one in progress included, disposes of nothing. Every attempt is refused alike.

The task list is `memory/TASKS.md` in the transcript's folder, its ids every task id its text holds. At SessionStart
and at each Stop the rule passes, it keeps them as one notice of its own, IDENT, so the previous turn's end is the last
Stop passed, or the session's start; a refused Stop keeps nothing. With no notice kept, only the not-ended words
dispose.

EXCEPTIONS (3), each with the failure it holds:
  * a count in a code span or a double quotation, straight or curly, is not read: it is an example the message cites,
    and reading it would refuse a result the message does not report, this rule's own text among them;
  * a session with no task list is judged as nothing and keeps nothing: it has no task to open, so every count it
    reports would be refused with one of its two ways out closed;
  * an agent's end, a SubagentStop, is not judged: its event carries no last message, and its final text goes to the
    thread that launched it, which disposes of what it reports.
"""

import re
from pathlib import Path
from typing import Final, NewType

from dispatcher.core import Event, HookEvent
from dispatcher.verdict import Prose, Verdict, refuse

EVENTS: frozenset[HookEvent] = frozenset({"SessionStart", "Stop"})
TOOLS = None
ListedId = NewType("ListedId", str)
"""A task id as the task list spells it, `N.43`."""
KIND: Final = "notice"
IDENT: Final = "task-ids"
FIELD: Final = "ids"
TASKS = Path("memory") / "TASKS.md"
_TASK_ID = re.compile(r"\b[A-Z]\.[0-9]+\b")
# A count of results that passed, in either order: "206 of 207 pass", "passed 20 of 21".
_COUNT_THEN_PASS = re.compile(
    r"\b([0-9]+) of (?:the )?([0-9]+)\b(?:\s+[A-Za-z-]+){0,3}?\s+(?:pass|passes|passed|passing|green|succeeded)\b",
    re.IGNORECASE)
_PASS_THEN_COUNT = re.compile(r"\b(?:pass|passes|passed|green|succeeded)\b[:,]?\s+([0-9]+) of (?:the )?([0-9]+)\b",
                              re.IGNORECASE)
_NOT_ENDED = re.compile(r"\b(?:pending|still running|queued|in progress)\b", re.IGNORECASE)
# What a message cites rather than reports: a code span, or a quotation in straight or curly double quotes.
_CITED = re.compile(r"`[^`]*`|\"[^\"]*\"|“[^”]*”")
_ITEM = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s")
ADVICE = """this turn's last message leaves checks that did not pass without a disposition.

{findings}

A check that did not pass is fixed now or becomes a new task: fix it, or open a task for it and name its new id in the
same paragraph or list item. A task the list already held at the previous turn's end disposes of nothing, and checks
that have not ended say so there: pending, still running, queued or in progress."""


def units(message: Prose) -> list[Prose]:
    """Split a message into paragraphs, each list item a unit of its own."""
    found: list[list[Prose]] = []
    for line in map(Prose, message.splitlines()):
        if not line.strip():
            found.append([])
        elif _ITEM.match(line) or not found:
            found.append([line])
        else:
            found[-1].append(line)
    return [Prose(" ".join(unit)) for unit in found]


def findings(message: Prose, new: frozenset[ListedId]) -> list[Prose]:
    """Name each count of passes below its total in ``message`` whose unit holds no not-ended word and no id of
    ``new``, the ids the list gained since the previous turn's end."""
    found: list[Prose] = []
    for unit in units(message):
        if _NOT_ENDED.search(unit) or new.intersection(_TASK_ID.findall(unit)):
            continue
        reported = _CITED.sub(" ", unit)
        for pattern in (_COUNT_THEN_PASS, _PASS_THEN_COUNT):
            for match in pattern.finditer(reported):
                if int(match[1]) < int(match[2]):
                    found.append(Prose(f"  * '{match[0]}' leaves {int(match[2]) - int(match[1])} without a "
                                       "disposition"))
    return found


def judge(event: Event) -> Verdict | None:
    """Refuse a Stop whose last message leaves a count below its total without a disposition; keep the list's ids at
    SessionStart and at each Stop passed."""
    if event.transcript is None or not (tasks := Path(event.transcript).parent / TASKS).is_file():
        return None
    from state.session import FieldName, FieldValue, RecordId  # imported for an event this rule judges, never at load

    held = frozenset(ListedId(ident) for ident in _TASK_ID.findall(tasks.read_text(encoding="utf-8")))
    session = event.state()
    if event.name == "Stop":
        kept = [record.fields[FieldName(FIELD)] for record in session.records(KIND) if record.ident == IDENT]
        new = held - frozenset(kept[0].split()) if kept else frozenset[ListedId]()
        said = event.payload.get("last_assistant_message")
        if found := findings(Prose(said if isinstance(said, str) else ""), new):
            return refuse(Prose(ADVICE.format(findings="\n".join(found))))
    session.record(KIND, RecordId(IDENT), {FieldName(FIELD): FieldValue(" ".join(sorted(held)))})
    return None
