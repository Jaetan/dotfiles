#!/home/nicolas/.local/bin/python3.14
"""Refuse a Workflow launch that does not name one or two prepared briefs, each one goal of at most ten items,
or whose script runs an agent its briefs do not prompt.

THE RULE (user, 2026-11-03, ~/.claude/CLAUDE.md): every workflow agent has one
pinpoint goal finished in a few tool calls, and a launch names one or two of
them. An agent handed a group of goals enumerates them all and spends the
workflow's budget before it returns; a note in the instructions holds only
while it is remembered, so this hook holds the rule at every launch.

WHAT A LAUNCH NAMES. A PreToolUse event for the Workflow tool, whatever starts
the script (`script`, `scriptPath` or `name`, with or without
`resumeFromRunId`), goes through only when, a built-in's launch below aside,
its `args` is an object, or a string holding one in JSON, with
  briefs  an absolute path to a JSON object of label to brief text, and
  labels  a list of one or two strings, each a key of that object.
Each brief it names holds
  one line `GOAL: <text>`, the text one sentence: it ends at its first `.`,
          `!` or `?` followed by a space or the line's end, with nothing
          after, so the dot of `next/x.py` or `3.14` ends nothing;
  one line `ITEMS (<n>): <a>, <b>, ...`, n from 1 to 10, listing exactly n
          items, none empty: separated by `; ` when the list holds one at
          its top level and otherwise by `, `, where a separator inside a
          pair of backticks or of brackets is part of its item (a bracket
          with no partner, as in a prose `agent(`, pairs nothing).
Every other key of `args` is the workflow's own and is never read.

WHAT ITS SCRIPT RUNS. The script is `script`, else the file `scriptPath`
(relative to the event's cwd), else the saved workflow `name`, read from
~/.claude/workflows/<name>.js, else $CLAUDE_PROJECT_DIR/.claude/workflows/<name>.js.
tree-sitter-javascript parses it once, a string or a comment holding `agent(`
calling nothing, and the launch goes through only when
  the tree holds no ERROR and no MISSING node;
  the script calls `agent(...)`, by that identifier, at least once;
  every such call lies in the body of a loop or callback over args.labels:
          `for (const X of args.labels)`, a function passed first to
          `args.labels.map`, `.forEach` or `.flatMap`, or a function passed
          after a first argument `args.labels` or `args.labels.map(...)` to
          `pipeline` or `parallel`, its first parameter X;
  X is referenced in the call's first argument, the prompt, directly or
          through a variable declared in that body whose value references it;
  the script calls no `workflow(...)`, a nested workflow whose script the
          hook does not read.

A BUILT-IN WORKFLOW. A launch by `name` alone (no `script`, no `scriptPath`)
of a name in BUILT_IN, with no saved workflow of that name in the two folders
above, runs a script built into Claude Code that reads its args as its own,
deep-research a plain question: the hook skips the script check for it, never
reads its args, and reads its one brief from its `description`, a field the
Workflow tool ignores, holding exactly one whitespace-separated token
`briefs=<absolute path>#<label>`, the label what follows the token's last `#`;
that brief is held to the rules above. A saved workflow of that name is read
and checked as any other launch, its description unread: Claude Code runs one
that parses in the built-in's place, and the hook refuses one that does not
parse, where Claude Code keeps the built-in.

A NAME FOUND NOWHERE. A launch by a name in neither folder and not in BUILT_IN
is refused. Before refusing, the hook reads the installed Claude Code (the file
`claude` on PATH resolves to, else the newest file under
~/.local/share/claude/versions) for the names it registers, and says when the
name is one of them, BUILT_IN being out of date. The registering function is the
one whose body pushes `{source:"built-in"` to `bundledWorkflows`; each call of
it whose first argument opens a template `export const meta = {` registers the
`name` of its second argument: a string, or a variable followed through its
last assignment before the call, within the call's chunk (from the last
`// @bun` before it), to a string. No such function, no such call, or a call
whose name reaches no string leaves the binary unreadable, and the refusal says
so. tests/check_builtin_workflows.py compares BUILT_IN with those names.

WHAT IT ANSWERS. Exit 2 with `BLOCKED: ` and the rule on stderr, then one line
per problem naming the brief's label or the script's line; exit 0 and no output
for a launch that keeps the rule, and for every other tool.

KILL SWITCH: while ~/.claude/workflow-launch-guard.off exists every launch
goes through.

GAPS IT LEAVES. A script calling `agent` under another name (an alias, a
member such as `ctx.agent`) runs agents the hook does not see; a variable is
followed by its name, so an X shadowed inside the loop still counts as X. A
built-in's token cannot name a path or a label holding whitespace, nor a label
holding `#`.

FAILURE: an event the hook cannot read, or an error of its own, lets the call
through and says so on stderr with exit 1, as the kit's other guards do. A
briefs file or a script that cannot be read refuses the launch, since it shows
no brief or no agent, and so does a tree-sitter package that does not import:
requirements.txt beside the hook pins both.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import mmap
import os
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, cast

import _shell

try:
    from tree_sitter import Language, Node, Parser
    from tree_sitter_javascript import language as javascript
except ImportError as missing:  # refused at every launch, naming the package, never a silent pass
    UNIMPORTABLE: Prose | None = cast("Prose", f"`{missing.name}` does not import ({type(missing).__name__}: {missing})")
else:
    UNIMPORTABLE = None

# A brief's label; the text of a brief, of one of its lines or of a part of
# one; a path as the launch spells it; a place in a text; a line of a script;
# a line the hook prints; the hook's answer; a script's bytes; an identifier
# and a string of Claude Code's binary; the version of Claude Code installed.
Label = NewType("Label", str)
Text = NewType("Text", str)
FilePath = NewType("FilePath", str)
Offset = NewType("Offset", int)
Line = NewType("Line", int)
Prose = NewType("Prose", str)
ExitStatus = NewType("ExitStatus", int)
Source = NewType("Source", bytes)
Identifier = NewType("Identifier", bytes)
Quoted = NewType("Quoted", bytes)
Version = NewType("Version", str)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Mark = Literal[";", ","]
type Briefs = dict[Label, Text | None]  # a label's brief text, None where the file holds no string


class LaunchArgs(TypedDict, total=False):
    """The args a launch names its briefs by."""

    briefs: FilePath
    labels: list[Label]


class LaunchInput(TypedDict, total=False):
    """The part of a Workflow call's input the hook reads."""

    args: LaunchArgs | Text
    script: Text
    scriptPath: FilePath
    name: Text
    description: Text


class Reference(NamedTuple):
    """The briefs file a launch names and the labels of it, in order."""

    briefs: FilePath
    labels: list[Label]


class Installed(NamedTuple):
    """The installed Claude Code: its version, its binary, and the workflow names it registers."""

    version: Version
    binary: Path
    names: frozenset[Text]


KILL_SWITCH = Path.home() / ".claude" / "workflow-launch-guard.off"
MOST_BRIEFS = 2
TOKEN = "briefs="  # the start of the token naming a built-in's brief in its description
FORM = f"`{TOKEN}<absolute path>#<label>`"
MOST_ITEMS = 10
GOAL_LEAD = "GOAL: "
ITEMS_LINE = re.compile(r"ITEMS \(([0-9]+)\):(.*)")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
CLOSER_OF = {")": "(", "]": "[", "}": "{"}  # each closing bracket's opening one
QUOTE = "`"
SHOWN = 80  # characters a problem line shows of a label or a quoted text
PINS = Path(__file__).parent / "requirements.txt"
LABELS = ("args", "labels")  # the object and the property every agent's loop runs over
EACH = frozenset({"map", "forEach", "flatMap"})  # the methods of args.labels whose first argument runs per label
RUNNERS = frozenset({"pipeline", "parallel"})  # the calls whose later arguments run per item of the first
FUNCTIONS = frozenset({"arrow_function", "function_expression"})
NAMES = frozenset({"identifier", "shorthand_property_identifier"})  # the nodes that read a variable

# The workflows built into Claude Code that a launch may name: each runs a script
# the hook cannot read on args of its own, so its launch by name alone skips the
# script check and names its brief in its description. tests/check_builtin_workflows.py
# reads the names the installed Claude Code registers and exits 1 when they differ
# from this set.
BUILT_IN = frozenset({"deep-research"})
VERSIONS = Path.home() / ".local" / "share" / "claude" / "versions"
UNREADABLE_BINARY = "cannot read Claude Code's built-in workflows"
REGISTERS = re.compile(
    rb'function ([\w$]+)\([\w$]+,[\w$]+,[\w$]+\)\{[\w$]+\(\)\.bundledWorkflows\.push\(\{source:"built-in"')
META = b"(`export const meta = {"  # a registering call's first argument: a template opening on the script's meta
NAMED = re.compile(rb'`,\{name:(?:"([^"\\]+)"|\'([^\'\\]+)\'|([A-Za-z_$][\w$]*)[,}])')  # the template's end, the name
ASSIGNED = rb'=(?:"([^"\\]+)"|\'([^\'\\]+)\'|([A-Za-z_$][\w$]*)[,;])'  # after a variable: the string or variable it takes
CONTINUES = re.compile(rb"[\w$.]")  # a byte that makes the name after it part of a longer identifier or a member
CHUNK = b"// @bun"  # the line opening each module of the binary
BACKSLASH = ord("\\")

ADVICE = """BLOCKED: a Workflow launch names one or two briefs of a prepared file, each with a one-sentence GOAL and an ITEMS list of at most 10 (user rule, 2026-11-03).

{problems}

Give the launch args {{"briefs": "/abs/path/briefs.json", "labels": ["<label>"]}}
naming one or two labels of that file, a JSON object of label to brief text, and
write each brief with a line `GOAL: <one sentence>.` and a line
`ITEMS (<n>): <a>, <b>, ...` listing its n items, n from 1 to 10 (`; ` between
items when an item holds a comma). A larger task is more briefs, run as more
workflows of one or two agents. The script runs each agent inside
`for (const label of args.labels)` or a `.map` over args.labels, its prompt
built from that label, and starts no nested workflow(). A workflow built into
Claude Code and listed in the hook's BUILT_IN, launched by `name` alone, runs no
script the hook reads and takes args of its own: give it its own args and name
its one brief in the launch's `description` by one token
`briefs=/abs/path/briefs.json#<label>`."""


def shown(text: Text) -> Prose:
    """Return ``text`` cut to SHOWN characters for a problem line."""
    return Prose(text if len(text) <= SHOWN else text[: SHOWN - 3] + "...")


def covered(text: Text, spans: list[tuple[Offset, Offset]]) -> list[bool]:
    """Return, per offset of ``text``, whether one of ``spans`` (both ends included) covers it."""
    change = [0] * (len(text) + 1)
    for start, end in spans:
        change[start] += 1
        change[end + 1] -= 1
    depth, inside = 0, list[bool]()
    for step in change[: len(text)]:
        depth += step
        inside.append(depth > 0)
    return inside


def grouped(text: Text) -> list[bool]:
    """Return, per offset of ``text``, whether it lies inside a pair of backticks or of brackets.

    Backticks pair in order, a last odd one opening nothing. Outside them a
    closing bracket pairs with the nearest unpaired opening one of its kind,
    and a bracket left unpaired, as the `(` of a prose `agent(`, groups nothing.
    """
    ticks = [Offset(at) for at, char in enumerate(text) if char == QUOTE]
    spans = list(zip(ticks[::2], ticks[1::2], strict=False))
    quoted = covered(text, spans)
    waiting = {opener: list[Offset]() for opener in CLOSER_OF.values()}
    for at, char in enumerate(text):
        if quoted[at]:
            continue
        if char in waiting:
            waiting[char].append(Offset(at))
        elif char in CLOSER_OF and waiting[CLOSER_OF[char]]:
            spans.append((waiting[CLOSER_OF[char]].pop(), Offset(at)))
    return covered(text, spans)


def separators(text: Text, mark: Mark) -> list[Offset]:
    """Return where ``mark`` separates items of ``text``: outside every group, a space or the end after it."""
    inside = grouped(text)
    return [
        Offset(at)
        for at, char in enumerate(text)
        if char == mark and not inside[at] and (at + 1 == len(text) or text[at + 1].isspace())
    ]


def items(text: Text) -> list[Text]:
    """Return the items of an ITEMS list, split at its semicolons where it has any, else at its commas."""
    cuts = separators(text, ";") or separators(text, ",")
    bounds = [-1, *cuts, len(text)]
    return [Text(text[start + 1 : end].strip()) for start, end in itertools.pairwise(bounds)]


def goal_problems(lines: list[Text]) -> list[Prose]:
    """Return what keeps the brief's GOAL line from holding one sentence."""
    goals = [Text(line[len(GOAL_LEAD) :]) for line in lines if line.startswith(GOAL_LEAD)]
    if not goals:
        return [Prose(f"it has no line starting `{GOAL_LEAD}`")]
    if len(goals) > 1:
        return [Prose(f"it has {len(goals)} GOAL lines, where a brief has one goal")]
    goal = goals[0]
    end = SENTENCE_END.search(goal)
    if end is None or not goal[: end.start()].strip():
        return [Prose("its GOAL is no sentence ending at a `.`, `!` or `?`")]
    rest = Text(goal[end.end() :].strip())
    if rest:
        return [Prose(f"its GOAL holds more than one sentence: `{shown(rest)}` follows the first")]
    return []


def items_problems(lines: list[Text]) -> list[Prose]:
    """Return what keeps the brief's ITEMS line from listing n items, n from 1 to MOST_ITEMS."""
    found = [match for line in lines if (match := ITEMS_LINE.fullmatch(line))]
    if not found:
        return [Prose("it has no line `ITEMS (<n>): <a>, <b>, ...`")]
    if len(found) > 1:
        return [Prose(f"it has {len(found)} ITEMS lines, where a brief has one list")]
    digits, listed = found[0].groups()
    significant = Text(digits.lstrip("0") or "0")
    if len(significant) > len(str(MOST_ITEMS)) or not 1 <= int(significant) <= MOST_ITEMS:
        return [Prose(f"its ITEMS line declares {shown(significant)} items, where a brief holds 1 to {MOST_ITEMS}")]
    declared = int(significant)
    listed = Text(listed.strip())
    if not listed:
        return [Prose("its ITEMS line lists no item")]
    parts = items(listed)
    if not all(parts):
        return [Prose("its ITEMS list holds an empty item")]
    if len(parts) != declared:
        return [Prose(f"its ITEMS line declares {declared} items and lists {len(parts)}")]
    return []


def brief_problems(brief: Text) -> list[Prose]:
    """Return what keeps ``brief`` from holding one GOAL sentence and one ITEMS list of at most MOST_ITEMS."""
    lines = [Text(line) for line in brief.splitlines()]
    return goal_problems(lines) + items_problems(lines)


def read_briefs(path: FilePath) -> Briefs | Prose:
    """Return the briefs file at ``path`` as label to brief text, or why it is no such object."""
    source = Path(path)
    if not source.is_file():
        return Prose(f"its briefs file {path} cannot be read: no such regular file")
    try:
        with source.open(encoding="utf-8") as handle:
            table = json.load(handle)
    except (OSError, ValueError, MemoryError, RecursionError) as exc:
        return Prose(f"its briefs file {path} cannot be read: {shown(Text(f'{type(exc).__name__}: {exc}'))}")
    if not isinstance(table, dict):
        return Prose(f"its briefs file {path} is not a JSON object of label to brief text")
    return {Label(key): Text(value) if isinstance(value, str) else None for key, value in table.items()}


def saved(name: Text) -> list[Path]:
    """Return where a workflow saved as ``name`` is looked for, in order: ~/.claude/workflows, then the project's."""
    places = [Path.home() / ".claude" / "workflows" / f"{name}.js"]
    if project := os.environ.get("CLAUDE_PROJECT_DIR"):
        places.append(Path(project) / ".claude" / "workflows" / f"{name}.js")
    return places


def built_in(tool_input: LaunchInput) -> Text | None:
    """Return the BUILT_IN name ``tool_input`` launches by `name` alone, no workflow being saved as it; else None."""
    name = tool_input.get("name")
    alone = not isinstance(tool_input.get("script"), str) and not isinstance(tool_input.get("scriptPath"), str)
    if not alone or not isinstance(name, str) or name not in BUILT_IN:
        return None
    return None if any(place.is_file() for place in saved(Text(name))) else Text(name)


def args_reference(tool_input: LaunchInput) -> Reference | Prose:
    """Return the briefs file and the one or two labels a launch's args name, or why they name none."""
    args = tool_input.get("args")
    if isinstance(args, str):
        with contextlib.suppress(ValueError, RecursionError):
            args = json.loads(args)
    if not isinstance(args, dict):
        return Prose("its args is no object naming `briefs` and `labels`")
    briefs, labels = args.get("briefs"), args.get("labels")
    if not isinstance(briefs, str) or not Path(briefs).is_absolute():
        return Prose("its args names no `briefs`, the absolute path of a prepared briefs file")
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        return Prose("its args names no `labels`, a list of one or two brief labels")
    if not 1 <= len(labels) <= MOST_BRIEFS:
        return Prose(f"it names {len(labels)} briefs, where a launch names 1 to {MOST_BRIEFS}")
    return Reference(FilePath(briefs), [Label(label) for label in labels])


def description_reference(tool_input: LaunchInput, workflow: Text) -> Reference | Prose:
    """Return the briefs file and the label the description of built-in ``workflow``'s launch names, or why it names none."""
    description = tool_input.get("description")
    words = description.split() if isinstance(description, str) else []
    tokens = [Text(word) for word in words if word.startswith(TOKEN)]
    way = Prose(f"where a launch of the built-in workflow `{workflow}`, whose args the hook does not read, names its "
                f"one brief by one token {FORM} in its `description`")
    if len(tokens) != 1:
        return Prose(f"its description holds {len(tokens)} tokens `{TOKEN}...`, {way}")
    path, _, label = tokens[0].removeprefix(TOKEN).rpartition("#")
    if not label or not Path(path).is_absolute():
        return Prose(f"its description's token `{shown(tokens[0])}` names no absolute path and label, {way}")
    return Reference(FilePath(path), [Label(label)])


def launch_problems(tool_input: LaunchInput) -> list[Prose]:
    """Return what keeps a Workflow launch from naming one or two briefs that keep the rule, a built-in's one."""
    workflow = built_in(tool_input)
    reference = args_reference(tool_input) if workflow is None else description_reference(tool_input, workflow)
    if not isinstance(reference, Reference):
        return [reference]
    briefs, labels = reference
    table = read_briefs(briefs)
    if not isinstance(table, dict):
        return [table]
    found: list[Prose] = []
    for label in labels:
        named = Prose(f"brief `{shown(Text(label))}`")
        if label not in table:
            found.append(Prose(f"{named}: no such label in {briefs}"))
            continue
        brief = table[label]
        if brief is None:
            found.append(Prose(f"{named}: its text in {briefs} is not a string"))
            continue
        found.extend(Prose(f"{named}: {problem}") for problem in brief_problems(brief))
    return found


def text_of(node: Node) -> Text:
    """Return the source text of ``node``."""
    return Text((node.text or b"").decode(errors="replace"))


def line_of(node: Node) -> Line:
    """Return the 1-based line ``node`` starts on."""
    return Line(node.start_point.row + 1)


def walk(root: Node) -> Iterator[Node]:
    """Yield ``root`` and every node under it, in source order."""
    waiting = [root]
    while waiting:
        node = waiting.pop()
        yield node
        waiting.extend(reversed(node.children))


def callee(call: Node) -> Text | None:
    """Return the identifier ``call`` calls, None where it calls no bare identifier."""
    function = call.child_by_field_name("function")
    return text_of(function) if function is not None and function.type == "identifier" else None


def positional(arguments: Node) -> list[Node]:
    """Return the arguments of an ``arguments`` node, comments left out."""
    return [node for node in arguments.named_children if node.type != "comment"]


def is_labels(node: Node | None) -> bool:
    """Say whether ``node`` is the expression `args.labels`."""
    if node is None or node.type != "member_expression":
        return False
    owner, member = node.child_by_field_name("object"), node.child_by_field_name("property")
    return owner is not None and member is not None and (text_of(owner), text_of(member)) == LABELS


def is_labels_map(node: Node) -> bool:
    """Say whether ``node`` is a call `args.labels.map(...)`."""
    function = node.child_by_field_name("function")  # a call_expression's, the one node with the field
    return (function is not None and is_labels(function.child_by_field_name("object"))
            and text_of(cast("Node", function.child_by_field_name("property"))) == "map")


def first_parameter(function: Node) -> Text | None:
    """Return the text of ``function``'s first parameter, a pattern's text naming no variable; None for no parameter."""
    single = function.child_by_field_name("parameter")
    if single is not None:
        return text_of(single)
    listed = function.child_by_field_name("parameters")
    first = positional(listed)[:1] if listed is not None else []
    return text_of(first[0]) if first else None


def loop_variable(loop: Node) -> Text | None:
    """Return the text of X in `for (const X of args.labels)`, a pattern's naming no variable; None for another loop."""
    operator, left = loop.child_by_field_name("operator"), loop.child_by_field_name("left")
    if operator is None or text_of(operator) != "of" or not is_labels(loop.child_by_field_name("right")):
        return None
    return text_of(left) if left is not None else None


def callback_variable(function: Node) -> Text | None:
    """Return the first parameter of ``function`` where it is a callback run per label, else None."""
    arguments = function.parent
    call = arguments.parent if arguments is not None and arguments.type == "arguments" else None
    if arguments is None or call is None:
        return None
    passed = positional(arguments)
    at = passed.index(function)
    method = call.child_by_field_name("function")
    if method is not None and method.type == "member_expression":
        member = method.child_by_field_name("property")
        each = is_labels(method.child_by_field_name("object")) and member is not None and text_of(member) in EACH
        return first_parameter(function) if each and at == 0 else None
    over = callee(call) in RUNNERS and (is_labels(passed[0]) or is_labels_map(passed[0]))
    return first_parameter(function) if over else None


def label_scopes(call: Node) -> Iterator[tuple[Node, Text]]:
    """Yield each body enclosing ``call`` that runs once per label, with the variable naming the label."""
    child, parent = call, call.parent
    while parent is not None:
        if child == parent.child_by_field_name("body"):
            variable = (loop_variable(parent) if parent.type == "for_in_statement"
                        else callback_variable(parent) if parent.type in FUNCTIONS else None)
            if variable is not None:
                yield child, variable
        child, parent = parent, parent.parent


def references(expression: Node, scope: Node) -> set[Text]:
    """Return every name ``expression`` reads, through the values ``scope`` declares those names with."""
    declared: dict[Text, list[Node]] = {}
    for node in walk(scope):
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is not None and value is not None:  # a variable_declarator, the one node with both fields
            declared.setdefault(text_of(name), []).append(value)
    read: set[Text] = set()
    waiting = [expression]
    while waiting:
        for node in walk(waiting.pop()):
            if node.type in NAMES and (name := text_of(node)) not in read:
                read.add(name)
                waiting.extend(declared.get(name, []))
    return read


def prompted(call: Node) -> bool:
    """Say whether agent ``call`` runs per label with a first argument reading that label."""
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return False
    prompt = (positional(arguments) or [None])[0] if arguments.type == "arguments" else arguments
    return prompt is not None and any(variable in references(prompt, scope) for scope, variable in label_scopes(call))


def installed_binary() -> Path | Prose:
    """Return Claude Code's binary: the file `claude` on PATH resolves to, else the newest file under VERSIONS."""
    found = shutil.which("claude")
    if found is not None:
        return Path(found).resolve()
    with contextlib.suppress(OSError):
        files = [entry for entry in VERSIONS.iterdir() if entry.is_file()]
        if files:
            return max(files, key=lambda entry: entry.stat().st_mtime_ns)
    return Prose(f"no `claude` on PATH and no file under {VERSIONS}")


def template_end(data: mmap.mmap, start: Offset) -> Offset | None:
    """Return where the template literal whose text starts at ``start`` closes: its first backtick no backslash escapes."""
    at = data.find(b"`", start)
    while at >= 0:
        escapes = at
        while escapes > start and data[escapes - 1] == BACKSLASH:
            escapes -= 1
        if (at - escapes) % 2 == 0:
            return Offset(at)
        at = data.find(b"`", at + 1)
    return None


def resolved(data: mmap.mmap, variable: Identifier, call: Offset) -> Quoted | None:
    """Return the string ``variable`` holds at ``call``: its last assignment in the call's chunk, followed on."""
    start = data.rfind(CHUNK, 0, call)
    seen: set[Identifier] = set()
    while start >= 0 and variable not in seen:
        seen.add(variable)
        assigned = list(re.compile(rb"(?<![\w$.])" + re.escape(variable) + ASSIGNED).finditer(data, start, call))
        if not assigned:
            return None
        string, name = assigned[-1].group(1) or assigned[-1].group(2), assigned[-1].group(3)
        if name is None:
            return Quoted(string)
        variable = Identifier(name)
    return None


def call_name(data: mmap.mmap, tick: Offset) -> Text | None:
    """Return the name a registering call registers: the `name` after its template, which opens at ``tick``."""
    end = template_end(data, Offset(tick + 1))
    named = NAMED.match(data, end) if end is not None else None
    if named is None:
        return None
    string = named.group(1) or named.group(2)
    found = Quoted(string) if string is not None else resolved(data, Identifier(named.group(3)), tick)
    return Text(found.decode(errors="replace")) if found is not None else None


def registered_names(data: mmap.mmap) -> list[Text] | Prose:
    """Return the name each registering call of ``data`` registers, or why one cannot be read."""
    functions = sorted({Identifier(match.group(1)) for match in REGISTERS.finditer(data)})
    if not functions:
        return Prose('no function pushes {source:"built-in"} to bundledWorkflows')
    names: list[Text] = []
    for function in functions:
        at = data.find(function + META)
        while at >= 0:
            if not CONTINUES.fullmatch(data[at - 1 : at]):
                name = call_name(data, Offset(at + len(function) + 1))
                if name is None:
                    return Prose(f"the call of {function.decode()} at byte {at} registers a name no string gives")
                names.append(name)
            at = data.find(function + META, at + 1)
    if not names:
        return Prose(f"no call of {', '.join(function.decode() for function in functions)} registers a script")
    return names


def built_in_workflows() -> Installed | Prose:
    """Return the installed Claude Code with the workflow names it registers, or why they cannot be read."""
    binary = installed_binary()
    if not isinstance(binary, Path):
        return Prose(f"{UNREADABLE_BINARY}: {binary}")
    try:
        with binary.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            names = registered_names(data)
    except (OSError, ValueError) as exc:  # a map past the address space is an OSError
        return Prose(f"{UNREADABLE_BINARY}: {binary}: {type(exc).__name__}: {exc}")
    if not isinstance(names, list):
        return Prose(f"{UNREADABLE_BINARY}: {binary}: {names}")
    return Installed(Version(binary.name), binary, frozenset(names))


def unlisted(name: Text, missing: Prose) -> Prose:
    """Return why a launch of ``name``, saved nowhere and not in BUILT_IN, is refused: ``missing``, or what the binary says."""
    installed = built_in_workflows()
    if not isinstance(installed, Installed):
        return Prose(f"{missing}; {installed}")
    if name in installed.names:
        return Prose(f"{name} is a built-in workflow of Claude Code {installed.version} that BUILT_IN does not list: "
                     "add it to BUILT_IN in workflow-launch-guard.py")
    return missing


def script_source(tool_input: LaunchInput, cwd: Path) -> Source | Prose:
    """Return the launch's script, from `script`, else the file `scriptPath`, else the saved workflow `name`."""
    script, script_path, name = tool_input.get("script"), tool_input.get("scriptPath"), tool_input.get("name")
    if isinstance(script, str):
        return Source(script.encode(errors="replace"))
    by_name: Text | None = None
    if isinstance(script_path, str):
        places, what = [cwd / script_path], Prose(f"its script file {script_path}")
    elif isinstance(name, str):
        by_name = Text(name)
        places = saved(by_name)
        what = Prose(f"its saved workflow `{shown(by_name)}`, looked for at {' and '.join(map(str, places))},")
    else:
        return Prose("it names no script: none of `script`, `scriptPath` and `name`")
    for place in places:
        if place.is_file():
            try:
                return Source(place.read_bytes())
            except OSError as exc:
                return Prose(f"{what} cannot be read: {shown(Text(f'{type(exc).__name__}: {exc}'))}")
    missing = Prose(f"{what} cannot be read: no such regular file")
    return missing if by_name is None else unlisted(by_name, missing)


def script_problems(tool_input: LaunchInput, cwd: Path) -> list[Prose]:
    """Return what keeps the launch's script from running every agent per label, prompted by that label."""
    if built_in(tool_input) is not None:
        return []
    source = script_source(tool_input, cwd)
    if not isinstance(source, bytes):
        return [source]
    if UNIMPORTABLE is not None:
        return [Prose(f"its script cannot be parsed: the package {UNIMPORTABLE}; install the pins of {PINS} "
                      f"under {sys.executable}")]
    root = Parser(Language(javascript())).parse(source).root_node
    nodes = list(walk(root))
    broken = next((node for node in nodes if node.is_error or node.is_missing), None)
    if broken is not None:
        what = f"a missing `{broken.type}`" if broken.is_missing else "a syntax error"
        return [Prose(f"its script does not parse: {what} at line {line_of(broken)}")]
    calls = [node for node in nodes if node.type == "call_expression" and callee(node) in {"agent", "workflow"}]
    found = [
        Prose(f"its script calls workflow() at line {line_of(call)}, a nested workflow whose script the hook "
              "does not read") if callee(call) == "workflow"
        else Prose(f"its script calls agent() at line {line_of(call)} outside every loop or callback over "
                   "`args.labels` whose variable its prompt, the first argument, reads")
        for call in calls
        if callee(call) == "workflow" or not prompted(call)
    ]
    if not any(callee(call) == "agent" for call in calls):
        found.append(Prose("its script calls no agent()"))
    return found


def main() -> ExitStatus:
    """Read the hook event on stdin and answer it."""
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (ValueError, RecursionError) as exc:
        print(f"workflow-launch-guard: the event could not be read, the call goes ahead: {exc}", file=sys.stderr)
        return REPORTED
    if not isinstance(event, dict) or event.get("hook_event_name") != "PreToolUse":
        return ALLOW
    if event.get("tool_name") != "Workflow" or KILL_SWITCH.exists():
        return ALLOW
    tool_input = cast("LaunchInput", event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {})
    cwd = Path(event["cwd"] if isinstance(event.get("cwd"), str) else ".")
    try:
        found = launch_problems(tool_input) + script_problems(tool_input, cwd)
    except Exception as exc:  # noqa: BLE001 - a fault of the hook: reported, never a silent block
        print(f"workflow-launch-guard: internal error, the call goes ahead: {type(exc).__name__}: {exc}", file=sys.stderr)
        return REPORTED
    if found:
        print(ADVICE.format(problems="\n".join(f"  * {problem}" for problem in found)), file=sys.stderr)
        return REFUSE
    return ALLOW


if __name__ == "__main__":
    sys.exit(main())
