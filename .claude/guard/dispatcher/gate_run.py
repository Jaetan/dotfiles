"""How a rule hands a file to a project's declared gate, and which files a call changed: one way for every gate rule.

A gate is a project's own checker, declared in its `.claude/guard.toml` (declarations/schema.py). run(gate, path, rel)
runs the gate's command with `--file PATH --as REL` added, in the gate's cwd, stdin empty, in the dispatcher's own
environment, and reads its exit: 0 the file is clean, 1 a finding, 2 the gate cannot read the file or its record,
each answer carrying the gate's text, stdout and stderr together. tests(gate, paths) asks a determinism gate once
which paths are tests (`--is-test REL...`): exit 0 answers the asked paths whose REL it printed, one a line, and exit 1 none.
A gate that runs past GATE_TIMEOUT, cannot start or exits any other way is a KitFault naming its command, never a
pass. rel(gate, path) is the REL a file is handed as, relative to the gate's as_from folder. changed(event) names
the files a call changed, each once, in order: an Edit, Write or MultiEdit its tool_input's file_path, a Bash call
the changedFiles of its tool_response's bashEditDiff, absolute text alone counting; any other call none. A rule's
refusal is built of the same pieces in every gate rule, the rule giving only its own words: handed(gate, path) pairs
its declared gate with the file's REL, said() is the item for a gate's finding or unreadable answer, command() the
line that judges a file by hand, and unchecked() the item for the paths past the bound, naming how many, the bound and
the first NAMED commands.

EXCEPTIONS, each with the failure it holds:
  * a bashEditDiff whose moreFiles is above 0 adds every file `git status` lists in the work tree of the event's cwd
    as modified, added or untracked, a rename or a copy by its new path, a deleted file left out; git past
    GATE_TIMEOUT or failing is a KitFault: a call changing more files than its diff lists leaves the rest unchecked.
  * rel gives None for a path outside as_from or, for a hints gate, without one of its suffixes, and such a file is
    not handed: a gate handed a file of another project or language judges what it holds no rule for.
  * limited(paths) gives the first FILES_PER_RULE paths a rule hands and the rest, which it refuses naming: cold gate
    runs past that count outrun the dispatcher's wall, which every rule of an event shares.
  * a gate's text is cut at OUTPUT_LIMIT bytes with a line saying so: a gate printing without end fills the refusal.
"""

import os
import shlex
import subprocess
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, NewType

from dispatcher.core import ToolName
from dispatcher.tree import Count, Seconds, ShellText
from dispatcher.verdict import KitFault, Prose

if TYPE_CHECKING:
    from declarations.lookup import Gate
    from declarations.schema import Word
    from dispatcher.core import Event

RelPath = NewType("RelPath", str)
"""A path as a gate is handed it: relative to the gate's as_from folder."""
ExitCode = NewType("ExitCode", int)
AnswerKind = Literal["clean", "finding", "unreadable"]
FindingVerb = NewType("FindingVerb", str)
"""What a rule's refusal says its gate does on a finding, such as `refuses a hint`."""
Noun = NewType("Noun", str)
"""What a rule calls the files it hands its gate, such as `files`."""
type Handed = tuple[Gate, RelPath]

GATE_TIMEOUT = Seconds(2.0)
OUTPUT_LIMIT = Count(64 << 10)
FILES_PER_RULE = Count(4)
ANSWERS: dict[ExitCode, AnswerKind] = {ExitCode(0): "clean", ExitCode(1): "finding", ExitCode(2): "unreadable"}
EDIT_TOOLS = frozenset({ToolName("Edit"), ToolName("Write"), ToolName("MultiEdit")})
NAMED = Count(10)
INDENT = "      "
UNCHECKED = """  * {count} more {noun} this call wrote stand unchecked, past the {bound} a call hands its gate; judge each with
    its gate, the first {named} here:"""


class Answer(NamedTuple):
    """A gate's answer for one file, and its text."""

    kind: AnswerKind
    text: Prose


class Limited(NamedTuple):
    """The paths a rule hands to its gate, and the rest, which it refuses naming."""

    first: tuple[Path, ...]
    rest: tuple[Path, ...]


def answered(gate: Gate, words: Sequence[Word]) -> tuple[ExitCode, Prose]:
    """The exit and the text of the gate's command run with ``words`` added; a KitFault where it outruns GATE_TIMEOUT
    or cannot start."""
    named = " ".join(gate.run)
    try:
        done = subprocess.run([*gate.run, *words], cwd=gate.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=GATE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise KitFault(Prose(f"the gate {named} outran its {GATE_TIMEOUT:g} s limit")) from None
    except OSError as fault:
        raise KitFault(Prose(f"the gate {named} cannot start: {fault.strerror}")) from None
    text = done.stdout[:OUTPUT_LIMIT].decode("utf-8", "replace")
    if len(done.stdout) > OUTPUT_LIMIT:
        text += f"\n(the gate's output is cut here, at {OUTPUT_LIMIT >> 10} KiB)"
    return ExitCode(done.returncode), Prose(text)


def exited(gate: Gate, code: ExitCode, text: Prose) -> KitFault:
    """The fault of a gate that exited ``code``, which its protocol does not name."""
    said = text.strip()
    return KitFault(Prose(f"the gate {' '.join(gate.run)} exited {code}: {said.splitlines()[0] if said else 'silent'}"))


def run(gate: Gate, path: Path, rel: RelPath) -> Answer:
    """The gate's answer for the file at ``path``, handed as ``rel``."""
    code, text = answered(gate, ("--file", str(path), "--as", rel))
    if code not in ANSWERS:
        raise exited(gate, code, text)
    return Answer(ANSWERS[code], text)


def tests(gate: Gate, paths: Sequence[Path]) -> tuple[Path, ...]:
    """The ``paths`` a determinism gate names as tests, in the order asked: each asked as its rel(), since a gate reads
    a path relative to its project, and named where the gate prints that rel; a path rel() leaves out is not asked, and
    with none to ask no gate runs."""
    asked = {path: as_rel for path in paths if (as_rel := rel(gate, path)) is not None}
    if not asked:
        return ()
    code, text = answered(gate, ("--is-test", *asked.values()))
    if code == 1:
        return ()
    if code != 0:
        raise exited(gate, code, text)
    printed = frozenset(text.splitlines())
    return tuple(path for path, as_rel in asked.items() if as_rel in printed)


def rel(gate: Gate, path: Path) -> RelPath | None:
    """``path`` relative to the gate's as_from folder; None outside it, or for a hints gate without its suffix."""
    if gate.protocol == "hints-1" and not path.name.endswith(gate.suffixes):
        return None
    return RelPath(str(path.relative_to(gate.as_from))) if path.is_relative_to(gate.as_from) else None


def listed(root: Path) -> list[Path]:
    """Every file `git status` lists in the work tree ``root`` but a deleted one, a rename or a copy by its new path."""
    from declarations.lookup import CLEARED  # loaded by the declaration that named root

    environ = {name: value for name, value in os.environ.items() if name not in CLEARED}
    try:
        done = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                              capture_output=True, env=environ, timeout=GATE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise KitFault(Prose(f"git outran its {GATE_TIMEOUT:g} s limit listing the changes of {root}")) from None
    except OSError as fault:
        raise KitFault(Prose(f"git cannot run: {fault.strerror}")) from None
    if done.returncode != 0:
        why = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise KitFault(Prose(f"git cannot list the changes of {root}: {why[0] if why else 'no answer'}"))
    paths: list[Path] = []
    fields = iter(os.fsdecode(done.stdout).split("\0"))
    for entry in fields:
        status, name = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            _ = next(fields, None)
        if name and "D" not in status:
            paths.append(root / name)
    return paths


def changed(event: Event) -> tuple[Path, ...]:
    """The files the event's call changed, each once, in order."""
    if event.tool in EDIT_TOOLS:
        given = event.payload.get("tool_input")
        path = given.get("file_path") if isinstance(given, dict) else None
        return (Path(path),) if isinstance(path, str) and os.path.isabs(path) else ()
    response = event.payload.get("tool_response")
    diff = response.get("bashEditDiff") if isinstance(response, dict) else None
    if event.tool != "Bash" or not isinstance(diff, dict):
        return ()
    files = diff.get("changedFiles")
    texts = files if isinstance(files, list) else []
    paths = [Path(text) for text in texts if isinstance(text, str) and os.path.isabs(text)]
    more = diff.get("moreFiles")
    if isinstance(more, int) and not isinstance(more, bool) and more > 0 and event.cwd is not None:
        root = event.declaration(Path(event.cwd)).work_root
        if root is not None:
            paths += listed(root)
    return tuple(dict.fromkeys(paths))


def limited(paths: Sequence[Path]) -> Limited:
    """The first FILES_PER_RULE of ``paths``, and the rest."""
    return Limited(tuple(paths[:FILES_PER_RULE]), tuple(paths[FILES_PER_RULE:]))


def handed(gate: Gate | None, path: Path) -> Handed | None:
    """``gate`` and the REL ``path`` is handed to it as; None where the project declares no gate or rel() leaves the
    file out."""
    as_rel = None if gate is None else rel(gate, path)
    return None if gate is None or as_rel is None else (gate, as_rel)


def said(path: Path, handed: Handed, answer: Answer, finding: FindingVerb) -> Prose:
    """What a refusal says of the gate's ``answer`` for the file ``path``, ``finding`` the rule's words for a finding."""
    gate, _ = handed
    verb = finding if answer.kind == "finding" else "cannot read it"
    return Prose(f"  * {path}: the gate {shlex.join(gate.run)} {verb}, its rule at {gate.rule_source}:\n"
                 + textwrap.indent(answer.text.rstrip(), INDENT))


def command(path: Path, handed: Handed) -> ShellText:
    """The command that judges the file ``path`` with its gate."""
    gate, as_rel = handed
    words = shlex.join([*gate.run, "--file", str(path), "--as", as_rel])
    return ShellText(f"(cd {shlex.quote(str(gate.cwd))} && {words})")


def unchecked(rest: Sequence[Path], handed_of: Mapping[Path, Handed], noun: Noun) -> Prose:
    """What a refusal says of the ``rest`` past FILES_PER_RULE, ``noun`` the rule's word for them."""
    named = "\n".join(INDENT + command(path, handed_of[path]) for path in rest[:NAMED])
    return Prose(UNCHECKED.format(count=len(rest), noun=noun, bound=FILES_PER_RULE, named=NAMED) + "\n" + named)
