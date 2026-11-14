"""A Python type hint a call writes is held to its project's own hints gate: an edit's before it lands, a shell call's
at once after.

THE RULE (user, 2026-09-26, widened 2026-09-29). A hint is a universal claim, and a project declaring a
[gates.precise_hints] gate (declarations/schema.py) holds its files to that gate. Before an Edit, Write or MultiEdit,
the file's text after the edit is written to a temporary file of the file's suffix, handed to the gate as the file's
REL and removed after the run. After a Bash call, each file the call changed is handed where it stands. A finding or
an unreadable answer refuses, with the gate's own text and where its rule is written; a clean answer passes. Files
reach the gate only as dispatcher/gate_run.py hands them, so a file of a project declaring no gate, or one rel()
leaves out, is judged as nothing. A refusal after a Bash call comes once the call has run: its writes stand.

EXCEPTIONS (3), each with the failure it holds:
  * an edit the tool cannot apply passes unjudged: an Edit or MultiEdit whose old text the file does not hold once,
    or more than once without replace_all, or of a file that does not stand, or input of another shape. The tool
    refuses it itself, and the gate would judge a text that never lands.
  * a file a Bash call changed that no longer stands as a file is not handed: a deleted file holds no hint, and the
    gate would answer that it cannot read it.
  * a Bash call's files past FILES_PER_RULE are refused as unchecked, the refusal naming how many, the first NAMED and
    the command that judges each: cold gate runs past that count outrun the dispatcher's wall.
"""

import tempfile
from pathlib import Path
from typing import NewType, cast

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.gate_run import FindingVerb, Handed, Noun, changed, handed, limited, run, said, unchecked
from dispatcher.verdict import Prose, Verdict, refuse

FileText = NewType("FileText", str)
JsonKey = NewType("JsonKey", str)
JsonNumber = NewType("JsonNumber", float)
type Json = None | bool | JsonNumber | FileText | list[Json] | dict[JsonKey, Json]

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse", "PostToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Edit"), ToolName("Write"), ToolName("MultiEdit")})
BASH, WRITE, MULTI_EDIT = ToolName("Bash"), ToolName("Write"), ToolName("MultiEdit")
CONTENT, EDITS, OLD, NEW, EVERY = map(JsonKey, ("content", "edits", "old_string", "new_string", "replace_all"))
ENCODING = "utf-8"
FINDING, NOUN = FindingVerb("refuses a hint"), Noun("files")
EDITED = """the hints gate refuses the text this edit would leave, so the edit is not made:

{answer}

Type each hint it names precisely, as its rule says, and edit again."""
WRITTEN = """files this call wrote are refused; the call has run and its writes stand, so fix each before building on it:

{answers}"""


def handed_to(event: Event, path: Path) -> Handed | None:
    """The hints gate of the project of ``path``, with the REL the file is handed as, as handed() pairs them."""
    return handed(event.declaration(path).project.precise_hints, path)


def edited(event: Event, path: Path) -> FileText | None:
    """The text of ``path`` after the edit the event asks for; None where the tool cannot apply it."""
    asked = cast("dict[JsonKey, Json]", event.payload.get("tool_input"))
    if event.tool == WRITE:
        content = asked.get(CONTENT)
        return content if isinstance(content, str) else None
    edits = asked.get(EDITS) if event.tool == MULTI_EDIT else [asked]
    if not path.is_file() or not isinstance(edits, list):
        return None
    text = FileText(path.read_text(encoding=ENCODING))
    for edit in edits:
        if not isinstance(edit, dict):
            return None
        old, new, every = edit.get(OLD), edit.get(NEW), edit.get(EVERY)
        if not isinstance(old, str) or not old or not isinstance(new, str):
            return None
        found = text.count(old)
        if found == 0 or (found > 1 and not every):
            return None
        text = FileText(text.replace(old, new))
    return text


def before(event: Event) -> Verdict | None:
    """Refuse an edit whose text after it the file's gate refuses; changed() names an edit's file alone before a call
    runs, since only a Bash call's response carries the files it changed."""
    paths = changed(event)
    handed = handed_to(event, paths[0]) if paths else None
    text = None if handed is None else edited(event, paths[0])
    if handed is None or text is None:
        return None
    with tempfile.NamedTemporaryFile("w", encoding=ENCODING, suffix=paths[0].suffix) as staged:
        _ = staged.write(text)
        staged.flush()
        answer = run(handed[0], Path(staged.name), handed[1])
    return None if answer.kind == "clean" else refuse(Prose(EDITED.format(answer=said(paths[0], handed, answer, FINDING))))


def after(event: Event) -> Verdict | None:
    """Refuse each file a Bash call changed that its gate refuses, and those past the bound as unchecked."""
    standing: dict[Path, Handed] = {}
    for path in changed(event):
        handed = handed_to(event, path) if path.is_file() else None
        if handed is not None:
            standing[path] = handed
    first, rest = limited(tuple(standing))
    refused: list[Prose] = []
    for path in first:
        answer = run(standing[path][0], path, standing[path][1])
        if answer.kind != "clean":
            refused.append(said(path, standing[path], answer, FINDING))
    if rest:
        refused.append(unchecked(rest, standing, NOUN))
    return refuse(Prose(WRITTEN.format(answers="\n".join(refused)))) if refused else None


def judge(event: Event) -> Verdict | None:
    """Before a call, hand an edit's text after it to its gate; after a Bash call, the files it changed."""
    if event.name == "PreToolUse":
        return before(event)
    return after(event) if event.tool == BASH else None
