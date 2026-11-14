"""Every test a call writes is handed to its project's determinism gate once the call has run, and every test a call
writes or reads gets the rule as a note.

THE RULE (user, 2026-10-02). Every test is fully deterministic: it reads no clock and waits on no duration, and starts
no thread, goroutine or concurrent task; the clock or the scheduler is injected and driven step by step. A project
declaring a [gates.test_determinism] gate (declarations/schema.py) holds its tests to it, the gate deciding which
files are tests and what a site is. After an Edit, Write, MultiEdit or Bash call, the files it changed are asked of the
gate once (--is-test), and each test among them is handed where it stands. A finding or an unreadable answer refuses,
with the gate's own text and where its rule is written; otherwise the rule is a note naming the tests. After a Read,
or a Bash call that changed no test, the paths the call names that the gate calls tests get the note. Files reach the
gate only as dispatcher/gate_run.py hands them, so a path of a project declaring no gate, or one rel() leaves out, is
judged as nothing. A refusal comes once the call has run: its writes stand.

EXCEPTIONS (3), each with the failure it holds:
  * a changed file that no longer stands as a file is not asked: a deleted file holds no site, and the gate would
    answer that it cannot read it.
  * a Bash call names a path only by a word whose known value holds a `/` or ends in a suffix and reaches an
    existing file or folder from its command's directory, a command whose directory is unknown naming none: a bare
    word such as `python` or `go` names a command or a module as often as the folder of that name.
  * tests past FILES_PER_RULE are refused as unchecked, the refusal naming how many, the first NAMED and the command
    that judges each: cold gate runs past that count outrun the dispatcher's wall.
"""

import re
from collections.abc import Iterable, Iterator
from os.path import isabs, join, normpath
from pathlib import Path
from typing import TYPE_CHECKING

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.gate_run import (FindingVerb, Handed, Noun, RelPath, changed, handed, limited, run, said, tests,
                                 unchecked)
from dispatcher.verdict import Prose, Verdict, context, refuse

if TYPE_CHECKING:
    from declarations.lookup import Gate
    from dispatcher.model import Command

type Found = dict[Path, Handed]

EVENTS: frozenset[HookEvent] = frozenset({"PostToolUse"})
READ, BASH = ToolName("Read"), ToolName("Bash")
TOOLS = frozenset({READ, ToolName("Edit"), ToolName("Write"), ToolName("MultiEdit"), BASH})
SUFFIX = re.compile(r"\w\.\w+$")
FINDING, NOUN = FindingVerb("refuses a site"), Noun("tests")
RULE = ("{paths} {verb} a test. Every test is fully deterministic, because the tests run on machines of very "
        "different power ({source}): it reads no clock and waits on no duration (no sleep, timer, deadline or timeout "
        "deciding an outcome) and starts no thread, goroutine or concurrent task. Inject the clock or the scheduler and "
        "drive it step by step. A larger timeout, a retry, a ceiling or a tolerance argued from machine load is never a "
        "fix, in any language or mutation lane.")
WRITTEN = """tests this call wrote are refused; the call has run and its writes stand, so rewrite each now: inject the
clock or the scheduler and drive it, or drop the process. A row in the gate's record or its dismissals needs the
user's approval.

{answers}"""


def handed_to(event: Event, path: Path) -> Handed | None:
    """The determinism gate of the project of ``path``, with the REL the file is handed as, as handed() pairs them."""
    return handed(event.declaration(path).project.test_determinism, path)


def tests_of(event: Event, paths: Iterable[Path]) -> Found:
    """The ``paths`` their gate names as tests, each with its gate and REL, each gate asked once."""
    asked: dict[Gate, dict[Path, RelPath]] = {}
    for path in paths:
        handed = handed_to(event, path)
        if handed is not None:
            asked.setdefault(handed[0], {})[path] = handed[1]
    return {path: (gate, rels[path]) for gate, rels in asked.items() for path in tests(gate, tuple(rels))}


def spelled(command: Command) -> Iterator[Path]:
    """The existing files and folders the words of ``command`` name by a path spelled as one."""
    cwd = command.cwd
    if cwd is None:
        return
    for word in command.words:
        value = word.value
        if value is not None and ("/" in value or SUFFIX.search(value)):
            path = Path(normpath(join(cwd, value)))
            if path.exists():
                yield path


def named(event: Event) -> tuple[Path, ...]:
    """The paths a Read or a Bash call names: a Read's file, absolute text alone counting, and every path a
    command's words spell."""
    if event.tool == BASH:
        return tuple(path for command in event.call().commands for path in spelled(command))
    given = event.payload.get("tool_input")
    path = given.get("file_path") if isinstance(given, dict) else None
    return (Path(path),) if isinstance(path, str) and isabs(path) else ()


def note(found: Found) -> Verdict:
    """The rule as a note naming the ``found`` tests, a sentence per gate citing where its rule is written."""
    by_gate: dict[Gate, list[RelPath]] = {}
    for gate, as_rel in found.values():
        by_gate.setdefault(gate, []).append(as_rel)
    return context(Prose("\n".join(RULE.format(paths=", ".join(rels), verb="is" if len(rels) == 1 else "are each",
                                               source=gate.rule_source) for gate, rels in by_gate.items())))


def written(found: Found) -> Verdict:
    """Refuse each written test its gate refuses, and those past the bound as unchecked; else the note."""
    first, rest = limited(tuple(found))
    refused: list[Prose] = []
    for path in first:
        answer = run(found[path][0], path, found[path][1])
        if answer.kind != "clean":
            refused.append(said(path, found[path], answer, FINDING))
    if rest:
        refused.append(unchecked(rest, found, NOUN))
    return refuse(Prose(WRITTEN.format(answers="\n".join(refused)))) if refused else note(found)


def judge(event: Event) -> Verdict | None:
    """After a call, hand the tests it wrote to their gate; where it wrote none, note the tests a Read or a Bash call
    names."""
    found = tests_of(event, (path for path in changed(event) if path.is_file()))
    if found:
        return written(found)
    if event.tool not in (READ, BASH):
        return None
    found = tests_of(event, named(event))
    return note(found) if found else None
