#!/home/nicolas/.local/bin/python3.14
"""Refuse a Workflow launch that does not name one or two prepared briefs, each one goal of at most ten items
with a shape and a size measured on it, a decisions file, the closing proof left to the main thread and no
advisor, or whose script runs an agent its briefs do not prompt.

THE RULE (user, 2026-11-03, ~/.claude/CLAUDE.md): every workflow agent has one
pinpoint goal finished in a few tool calls, and a launch names one or two of
them. An agent handed a group of goals enumerates them all and spends the
workflow's budget before it returns; a note in the instructions holds only
while it is remembered, so this hook holds the rule at every launch.

THE RULINGS (user, 2026-11-05): a brief names its kind of work, its shape, and
states a size no past agent of that shape spent more than, so no cheaper run
can be picked; a shape never measured runs as one pilot agent first. Every
brief names the file its agent appends each decision to as it takes it, so a
stopped agent's choices survive, and leaves the whole suites, every gate and
untouched lints to the main thread. Its agent never consults the advisor, each
call of which re-sends the agent's whole transcript uncached (measured
2026-11-05: 143k to 273k new tokens a call). The brief carries the three
lines, so they hold whatever script launches it.

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
          with no partner, as in a prose `agent(`, pairs nothing);
  one line `SHAPE: <name>`, the name of lower-case letters, digits and
          hyphens: the kind of work its agent does, a row of the SHAPE
          registry (WHAT A SHAPE IS CHECKED AGAINST, below);
  one line `SIZE: <n>k`, n a whole number from 1 to 800, the thousands of
          new tokens an agent of its SHAPE spends (800k being
          workflow-token-watch's per-agent ceiling), n times 1000 at least the
          count of every past agent of its SHAPE, and of every agent of the
          run that an optional ` from <source>` after it names, source one
          token: `wf_` and its id, or the absolute path of its run directory
          (WHAT A SIZE IS CHECKED AGAINST, below); or the line `SIZE: PILOT`,
          a SHAPE no past run measured, which passes only in a launch naming
          that brief alone;
  one line `DECISIONS: <path>`, the path absolute and ending `.jsonl`: the
          file its agent appends each decision to as it takes it;
  one line `PROOF: main thread`: its agent runs only its own files' tests and
          the one gate its claim names, the main thread every suite and gate;
  one line `ADVISOR: none`: its agent never consults the advisor.
A count may carry leading zeros; blanks around a SHAPE, SIZE, DECISIONS, PROOF
or ADVISOR value are dropped; the DECISIONS path is not looked for on disk.
Every other key of `args` is the workflow's own and is never read.

WHAT A SHAPE IS CHECKED AGAINST (user rulings, 2026-11-05). The SHAPE registry
is .claude/workflows/shapes.toml in the nearest folder holding this file's
resolved path: a TOML table per SHAPE name holding a one-line `definition` and,
where no brief of it launches, a one-line `retired` reason, and no other key; a
row is added only by the user's ruling. A brief whose SHAPE names no row is
refused, naming the registry's path; one whose row is retired is refused,
quoting its reason; neither has its SIZE measured against that SHAPE. A SHAPE
whose largest past agent spent more than 800k, the per-agent ceiling past which
the watch stops an agent, is refused whatever its SIZE, its work having overrun
one agent and to be split into narrower SHAPEs by the user's ruling. A registry
that cannot be read, or holding a row that is no such table, refuses every
launch, naming the error.

WHAT A SIZE IS CHECKED AGAINST (user rulings, 2026-11-05, after a workflow's own
subagent_tokens was measured two to three times below the watch's count, and
after a heavy brief passed by citing a light run). Runs are read with
workflow-token-watch's own counting, the watch loaded from
.local/bin/workflow-token-watch in the nearest folder holding this file's
resolved path, never a copy of its code. A run is every directory
~/.claude/projects/*/*/subagents/workflows/wf_<id> of one id, one per session
its transcripts were kept in, read by one count: each agent-*.jsonl is an
agent, the calls of one file name read from several directories counted
together and once each. A past agent's brief is the one the opening turn of one
of its transcripts, its user messages before the first assistant message, names
last as a runner's prompt does, `Your task brief is the entry "<label>" of the
JSON object in <absolute path>`, after any user request the harness relays
first. Its SHAPE is the one the launch ledger (WHAT IT RECORDS, below) records
in the latest row naming that briefs file and label of those timed at or before
the transcript starts, at the `timestamp` of its first line holding a JSON
object that carries one, both read as times; no briefs file is read. A
transcript naming no brief, starting at no time or that no row binds leaves its
agent of no SHAPE, never a refusal; an absent ledger holds no row, and one
refusing every launch (WHAT IT RECORDS) measures no SHAPE.
A size whose thousands fall below the count of the largest past agent of its
SHAPE, over every run, is refused, naming that count, the agent's transcript
(the least path it was read from) and the SHAPE; a sized brief of a SHAPE no
agent that recorded a call carries is refused as naming no measured run of it,
and a PILOT of a SHAPE one carries is refused, naming that largest count. A
source names the directories of its `wf_<id>`, or the one its absolute path
names, and is held alike to their largest agent, whatever their briefs; a source
naming no directory, or none whose transcripts record a call, is refused. Every
sized brief, and every PILOT naming a SHAPE, is refused while the watch does not
load.

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

WHAT IT RECORDS (user ruling, 2026-11-05). A launch judged to keep the rule
appends, before its answer, one line per brief it names, in its order, to the
launch ledger ~/.claude/workflow-launches.jsonl beside the kill switch: the JSON
object {"time": "<UTC ISO 8601 to the second, ending Z>", "briefs": "<the
briefs file's absolute path as the launch names it>", "label": "<label>",
"shape": "<SHAPE>", "sha256": "<hex SHA-256 of the brief's text as UTF-8, a lone
surrogate passed through>"}, the lines of one launch sharing one time, the rows
the ledger was seeded with also carrying "seeded": true. ~/.claude is created
where missing, and no complete row is ever changed: a torn tail, the bytes after
the ledger's last newline or all of one holding none (a write cut short), is
first appended to
~/.claude/workflow-launches.torn as {"time": "<the launch's time>", "torn": "<its
bytes, undecodable ones backslash-escaped>"}, then cut away. A launch any check
refuses leaves the ledger as it was. A ledger it cannot append to, a file it may
not write or a ~/.claude it cannot create, refuses the launch, naming the
ledger's path and the error, and so does a torn tail the .torn file cannot take,
naming both paths, the ledger left as it was. A ledger that cannot be read (a
folder in its place, a file it may not read, bytes that are not UTF-8, one past
the address space) or holding a complete line that is no such row (no JSON
object, one of the five keys missing or no string, a key besides them and
`seeded`, a `seeded` other than true, a time in another form) refuses every
launch, naming the ledger's path, the line's number where there is one, and
the error; a torn tail is passed over, and an absent ledger is no fault.

KILL SWITCH: while ~/.claude/workflow-launch-guard.off exists every launch
goes through.

GAPS IT LEAVES. A script calling `agent` under another name (an alias, a
member such as `ctx.agent`) runs agents the hook does not see; a variable is
followed by its name, so an X shadowed inside the loop still counts as X. A
built-in's token cannot name a path or a label holding whitespace, nor a label
holding `#`, and a SIZE line's source cannot name a path holding whitespace. A
prompt naming its brief in other words, or by a path holding whitespace, names
none, and a launch the kill switch lets through records no row, its agents of
no SHAPE.

FAILURE: an event the hook cannot read, or an error of its own, lets the call
through and says so on stderr with exit 1, as the kit's other guards do. A
briefs file or a script that cannot be read refuses the launch, since it shows
no brief or no agent, and so does a tree-sitter package that does not import:
requirements.txt beside the hook pins both.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import itertools
import json
import mmap
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from importlib.machinery import ModuleSpec, SourceFileLoader
from importlib.util import module_from_spec
from pathlib import Path
from typing import Literal, NamedTuple, NewType, Protocol, TypedDict, cast

import _shell

try:
    from tree_sitter import Language, Node, Parser
    from tree_sitter_javascript import language as javascript
except ImportError as missing:  # refused at every launch, naming the package, never a silent pass
    UNIMPORTABLE: Prose | None = cast("Prose", f"`{missing.name}` does not import ({type(missing).__name__}: {missing})")
else:
    UNIMPORTABLE = None

# A brief's label; the text of a brief, of one of its lines or of a part of
# one; a path as the launch spells it; a place in a text; a line of a script
# or of the ledger; a line the hook prints; the hook's answer; a script's
# bytes; an identifier and a string of Claude Code's binary; the version of Claude Code installed;
# a number a brief's line states; new tokens an agent spent; the file name of
# an agent's transcript; the kind of work a brief names; a workflow run's id;
# a line of the ledger as read.
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
Count = NewType("Count", int)
Tokens = NewType("Tokens", int)
Transcript = NewType("Transcript", str)
Shape = NewType("Shape", str)
Run = NewType("Run", str)
Ledgered = NewType("Ledgered", bytes)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Mark = Literal[";", ","]
type Briefs = dict[Label, Text | None]  # a label's brief text, None where the file holds no string
type Agents = dict[Run, set[Transcript]]  # by past run, the transcript names of its agents of one SHAPE


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


class Launched(NamedTuple):
    """A brief a launch names, as its ledger line records it: its briefs file, its label, its SHAPE and its text."""

    briefs: FilePath
    label: Label
    shape: Shape
    text: Text


class Logged(NamedTuple):
    """A row of the launch ledger as history reads it: when a launch let its brief through, and the brief's SHAPE."""

    time: datetime
    shape: Shape


class Measured(NamedTuple):
    """The new tokens the agent that spent the most spent, as the watch counts them, and its transcript."""

    tokens: Tokens
    transcript: Path


class Row(NamedTuple):
    """A row of the SHAPE registry: the work one agent of its SHAPE does, and why no brief of it launches, if none does."""

    definition: Text
    retired: Text | None


class Installed(NamedTuple):
    """The installed Claude Code: its version, its binary, and the workflow names it registers."""

    version: Version
    binary: Path
    names: frozenset[Text]


class Tally(Protocol):
    """workflow-token-watch's count of a workflow's calls by agent, as far as the guard reads it."""

    offsets: dict[Path, Offset]  # each transcript read, by its path

    def read(self, directory: Path) -> Tokens:
        """Read the agent transcripts of ``directory``; return the new tokens of every call read so far."""
        ...

    def agents(self) -> dict[Transcript, Tokens]:
        """Return each agent's new tokens, by its transcript's file name."""
        ...


KILL_SWITCH = Path.home() / ".claude" / "workflow-launch-guard.off"
LEDGER = KILL_SWITCH.with_name("workflow-launches.jsonl")  # a line per brief of every launch let through
TORN = KILL_SWITCH.with_name("workflow-launches.torn")  # a line per torn tail a launch cut from LEDGER
STAMP = "%Y-%m-%dT%H:%M:%SZ"  # a ledger line's time: UTC to the second
ROW_KEYS = ("time", "briefs", "label", "shape", "sha256")  # the keys of every ledger row, each a string
SEEDED = "seeded"  # the key a row the ledger was seeded with adds, true
MOST_BRIEFS = 2
TOKEN = "briefs="  # the start of the token naming a built-in's brief in its description
FORM = f"`{TOKEN}<absolute path>#<label>`"
MOST_ITEMS = Count(10)
GOAL_LEAD = Text("GOAL: ")
ITEMS_LINE = re.compile(r"ITEMS \(([0-9]+)\):(.*)")
SHAPE_LEAD = Text("SHAPE: ")
SHAPE_NAME = re.compile(r"[a-z0-9-]+")  # after SHAPE_LEAD: the kind of work a brief's agent does
SIZE_LEAD = Text("SIZE: ")
SIZE_FORM = re.compile(r"([0-9]+)k(?: from (\S+))?")  # after SIZE_LEAD: thousands of new tokens, a run measured
SIZED = Text("<n>k [from <source>]")  # SIZE_FORM as a refusal shows it
MOST_SIZE = Count(800)  # thousands of new tokens an agent spends at most: workflow-token-watch's AGENT_CEILING
PILOT = Text("PILOT")  # the SIZE of a shape never measured
RUN_ID = re.compile(r"wf_[0-9A-Za-z_-]+")  # a workflow run, as its transcript directory is named
PROJECTS = Path(".claude") / "projects"  # below HOME: <project>/<session>/subagents/workflows/<run>
SESSIONS = "*/*/subagents/workflows"  # below PROJECTS: the folder each session keeps its runs in
AGENTS = "wf_*/agent-*.jsonl"  # below a session's folder: each agent's transcript, in its run's directory
ENTRY = re.compile(r'Your task brief is the entry "(.+?)" of the JSON object in (\S+)')  # a runner's prompt: label, briefs
WATCH = Path(".local") / "bin" / "workflow-token-watch"  # below a folder holding the guard: the kit's watch
WATCH_MODULE = "workflow_token_watch"
REGISTRY = Path(".claude") / "workflows" / "shapes.toml"  # below a folder holding the guard: the kit's SHAPE registry
DEFINITION, RETIRED = "definition", "retired"  # the keys of a registry row: the work it names, why it no longer launches
THOUSAND = 1000  # new tokens in a SIZE line's `k`
CEILING = Tokens(MOST_SIZE * THOUSAND)  # new tokens past which workflow-token-watch stops an agent
DECISIONS_LEAD = Text("DECISIONS: ")
JOURNAL = ".jsonl"  # the suffix of a decisions file
PROOF_LEAD = Text("PROOF: ")
MAIN_THREAD = Text("main thread")  # the one PROOF a brief states
ADVISOR_LEAD = Text("ADVISOR: ")
NO_ADVISOR = Text("none")  # the one ADVISOR a brief states
NEVER = Prose("a workflow agent never consulting the advisor")  # the reason every ADVISOR refusal ends on
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

ADVICE = """BLOCKED: a Workflow launch names one or two briefs of a prepared file, each with a one-sentence GOAL, an ITEMS list of at most 10, a SHAPE, a SIZE measured on that SHAPE or a lone PILOT, a DECISIONS file, `PROOF: main thread` and `ADVISOR: none` (user rules, 2026-11-03 and 2026-11-05).

{problems}

Give the launch args {{"briefs": "/abs/path/briefs.json", "labels": ["<label>"]}}
naming one or two labels of that file, a JSON object of label to brief text, and
write each brief with a line `GOAL: <one sentence>.` and a line
`ITEMS (<n>): <a>, <b>, ...` listing its n items, n from 1 to 10 (`; ` between
items when an item holds a comma). Each brief also holds a line
`SHAPE: <name>`, the kind of work its agent does in lower-case letters, digits
and hyphens, a row of the kit's SHAPE registry that only the user's ruling adds
to; a line `SIZE: <n>k`, n the thousands of new tokens, 1 to 800, at
least the most that any past agent whose brief carried the same SHAPE spent as
workflow-token-watch counts them, never the workflow's own subagent_tokens, and
where ` from <source>` follows, naming a run by its `wf_<id>` or the absolute
path of its run directory, at least that run's largest agent too; or else a line
`SIZE: PILOT` for a SHAPE no past run measured, its launch then naming that
brief alone; a line
`DECISIONS: <absolute path>.jsonl`, the file its agent appends each decision to
as it takes it; a line `PROOF: main thread`, its agent running only its own
files' tests and the one gate its claim names; and a line `ADVISOR: none`,
a workflow agent never consulting the advisor. A larger task is more briefs, run
as more workflows of one or two agents. The script runs each agent inside
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


def lone(lines: list[Text], lead: Text, missing: Prose, what: Prose) -> Text | list[Prose]:
    """Return what follows ``lead`` on the one line of ``lines`` it starts, or the problem keeping it from one.

    The problem is ``missing`` for no such line, and a count of them for more, where a brief has ``what``.
    """
    found = [Text(line[len(lead) :]) for line in lines if line.startswith(lead)]
    if not found:
        return [missing]
    if len(found) > 1:
        return [Prose(f"it has {len(found)} {lead.removesuffix(': ')} lines, where a brief has {what}")]
    return found[0]


def whole(digits: Text, most: Count) -> Count | None:
    """Return the number ``digits`` spell, leading zeros dropped, where it lies from 1 to ``most``; else None.

    Digits longer than ``most`` are refused before int() reads them, int() refusing past 4300 of them.
    """
    significant = Text(digits.lstrip("0") or "0")
    if len(significant) > len(str(most)) or not 1 <= int(significant) <= most:
        return None
    return Count(int(significant))


def goal_problems(lines: list[Text]) -> list[Prose]:
    """Return what keeps the brief's GOAL line from holding one sentence."""
    goal = lone(lines, GOAL_LEAD, Prose(f"it has no line starting `{GOAL_LEAD}`"), Prose("one goal"))
    if isinstance(goal, list):
        return goal
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
    declared = whole(Text(digits), MOST_ITEMS)
    if declared is None:
        return [Prose(f"its ITEMS line declares {shown(Text(digits))} items, where a brief holds 1 to {MOST_ITEMS}")]
    listed = Text(listed.strip())
    if not listed:
        return [Prose("its ITEMS line lists no item")]
    parts = items(listed)
    if not all(parts):
        return [Prose("its ITEMS list holds an empty item")]
    if len(parts) != declared:
        return [Prose(f"its ITEMS line declares {declared} items and lists {len(parts)}")]
    return []


def shape_of(lines: list[Text]) -> Shape | list[Prose]:
    """Return the name the brief's one SHAPE line gives, or what keeps it from naming one."""
    stated = lone(lines, SHAPE_LEAD, Prose(f"it has no line `{SHAPE_LEAD}<name>`, the kind of work its agent does "
                                           "in lower-case letters, digits and hyphens"), Prose("one shape"))
    if isinstance(stated, list):
        return stated
    shape = Text(stated.strip())
    if SHAPE_NAME.fullmatch(shape) is None:
        return [Prose(f"its SHAPE line reads `{shown(shape)}`, no name of lower-case letters, digits and hyphens")]
    return Shape(shape)


def size_problems(lines: list[Text], alone: bool, shape: Shape | None) -> list[Prose]:
    """Return what keeps the brief's SIZE line from covering every past agent of its ``shape`` and of the run it
    names, or from a PILOT ``alone`` of a SHAPE never measured; ``shape`` is None where the brief names none the
    registry lets launch, or while the launch ledger its history is read from refuses every launch."""
    stated = lone(lines, SIZE_LEAD, Prose(f"it has no line `{SIZE_LEAD}{SIZED}` or `{SIZE_LEAD}{PILOT}`"),
                  Prose("one size"))
    if isinstance(stated, list):
        return stated
    size = Text(stated.strip())
    if size == PILOT:
        if not alone:
            return [Prose(f"its SIZE line reads `{PILOT}`, where a pilot is the one brief its launch names")]
        return pilot_problems(shape)
    form = SIZE_FORM.fullmatch(size)
    if form is None:
        return [Prose(f"its SIZE line reads `{shown(size)}`, neither `{SIZED}` nor `{PILOT}`")]
    digits, source = form.groups()
    if (thousands := whole(Text(digits), MOST_SIZE)) is None:
        return [Prose(f"its SIZE line states {shown(Text(digits))}k, where an agent spends 1k to {MOST_SIZE}k, "
                      "the per-agent ceiling")]
    if source is not None and RUN_ID.fullmatch(source) is None and not Path(source).is_absolute():
        return [Prose(f"its SIZE line's source `{shown(Text(source))}` is neither a workflow run `wf_<id>` nor the "
                      "absolute path of a run directory")]
    watch = token_watch()
    if isinstance(watch, str):
        return [unchecked(watch)]
    cited = [] if source is None else measured_problems(thousands, Text(source), watch)
    return cited + ([] if shape is None else shape_problems(thousands, shape, watch))


def unchecked(why: Prose) -> Prose:
    """Return the problem of a SIZE line the watch cannot check, not loading for ``why``."""
    return Prose(f"its SIZE line cannot be checked: {why}")


def pilot_problems(shape: Shape | None) -> list[Prose]:
    """Return what keeps a lone PILOT from naming a SHAPE no past run measured; ``shape`` None where it names none."""
    if shape is None:
        return []
    watch = token_watch()
    if isinstance(watch, str):
        return [unchecked(watch)]
    measured = shape_largest(shape, watch)
    if measured is None:
        return []
    return overran(shape, measured) or [
        Prose(f"its SIZE line reads `{PILOT}`, where SHAPE `{shape}` was measured: its SIZE states at least the "
              f"{measured.tokens:,} new tokens {measured.transcript} spent, the most of any past agent of that "
              "SHAPE as workflow-token-watch counts them")]


def shape_problems(thousands: Count, shape: Shape, watch: Callable[[], Tally]) -> list[Prose]:
    """Return what keeps ``thousands``k from covering the largest past agent whose brief carried ``shape``."""
    measured = shape_largest(shape, watch)
    if measured is None:
        return [Prose(f"its SIZE line states {thousands}k, where no past run measured an agent whose brief carries "
                      f"SHAPE `{shape}`: a SHAPE never measured runs as `{SIZE_LEAD}{PILOT}`, the one brief its "
                      "launch names")]
    if over := overran(shape, measured):
        return over
    if thousands * THOUSAND >= measured.tokens:
        return []
    return [Prose(f"its SIZE line states {thousands}k, below the {measured.tokens:,} new tokens {measured.transcript} "
                  f"spent on a brief of SHAPE `{shape}`, the most of any past agent of that SHAPE as "
                  "workflow-token-watch counts them")]


def overran(shape: Shape, measured: Measured) -> list[Prose]:
    """Return the problem of ``shape`` whose largest past agent, ``measured``, spent past CEILING, an agent the watch
    stops; none where it spent no more."""
    if measured.tokens <= CEILING:
        return []
    return [Prose(f"its SHAPE `{shape}` overran one agent: {measured.transcript} spent {measured.tokens:,} new "
                  f"tokens, past the {CEILING:,} per-agent ceiling, as workflow-token-watch counts them; its work must "
                  f"be split into narrower SHAPEs of the registry {nearest(REGISTRY)} by the user's ruling")]


def opening_request(transcript: Path) -> Text | None:
    """Return the text of the user messages ``transcript`` opens with, before its first assistant message, each a
    string or its text blocks; None where it cannot be read. A line that is no JSON is passed over, as the watch
    passes it."""
    said: list[Text] = []
    try:
        with transcript.open("rb") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                role = message.get("role") if isinstance(message, dict) else None
                if role == "assistant":
                    break
                if role != "user":
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    said.append(Text(content))
                    continue
                said.extend(Text(block["text"]) for block in (content if isinstance(content, list) else [])
                            if isinstance(block, dict) and block.get("type") == "text"
                            and isinstance(block.get("text"), str))
    except (OSError, MemoryError):
        return None
    return Text("\n".join(said))


def entry_of(transcript: Path) -> Reference | None:
    """Return the briefs file and the label ``transcript``'s opening turn names, as a runner's prompt opens: its
    last such sentence, the prompt following any request the harness relays before it; None where it names none
    by an absolute path."""
    request = opening_request(transcript)
    entries = list(ENTRY.finditer(request)) if request is not None else []
    if not entries or not Path(entries[-1].group(2)).is_absolute():
        return None
    return Reference(FilePath(entries[-1].group(2)), [Label(entries[-1].group(1))])


def started(transcript: Path) -> datetime | None:
    """Return when ``transcript`` starts: the `timestamp` of its first line holding a JSON object that carries one;
    None where that is no ISO 8601 time with an offset, no line carries one, or it cannot be read."""
    try:
        with transcript.open("rb") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(entry, dict) and "timestamp" in entry:
                    stamp = entry["timestamp"]
                    time = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
                    return time if time is not None and time.tzinfo is not None else None
    except (OSError, MemoryError, ValueError):
        return None
    return None


def spelled(text: Text) -> Prose:
    """Return ``text`` as JSON spells it between its quotes, cut for a problem line, which it keeps to one line."""
    return shown(Text(json.dumps(text)[1:-1]))


def stamped(text: Text) -> datetime | None:
    """Return the UTC time ``text`` writes in STAMP's form, the form STAMP writes it back in; else None."""
    try:
        time = datetime.strptime(text, STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None
    return time if time.strftime(STAMP) == text else None


def ledger_row(number: Line, line: Ledgered) -> tuple[FilePath, Label, Logged] | Prose:
    """Return the briefs file, the label and the launch that ``line``, the ledger's line ``number``, records; else why
    it refuses every launch: it is no UTF-8 or no JSON object, holds a key besides ROW_KEYS and SEEDED, lacks one of
    ROW_KEYS or holds one that is no string, holds a SEEDED that is not true, or a time not in STAMP's form."""
    malformed = Prose(f"the launch ledger {LEDGER} is malformed: its line {number}")
    try:
        row = json.loads(line.decode())
    except UnicodeDecodeError as exc:
        return Prose(f"the launch ledger {LEDGER} cannot be read: its line {number}: {type(exc).__name__}: {exc}")
    except (ValueError, RecursionError) as exc:
        return Prose(f"{malformed} is no JSON: {type(exc).__name__}: {exc}")
    if not isinstance(row, dict):
        return Prose(f"{malformed} is no JSON object")
    if unknown := sorted(set(row) - {*ROW_KEYS, SEEDED}):
        return Prose(f"{malformed} holds `{spelled(unknown[0])}`, where a row holds "
                     f"{', '.join(f'`{key}`' for key in ROW_KEYS)} and `{SEEDED}`")
    for key in ROW_KEYS:
        if key not in row:
            return Prose(f"{malformed} has no `{key}`")
        if not isinstance(row[key], str):
            return Prose(f"{malformed} has a `{key}` that is no string")
    if SEEDED in row and row[SEEDED] is not True:
        return Prose(f"{malformed} has a `{SEEDED}` that is not `true`")
    if (time := stamped(Text(row["time"]))) is None:
        return Prose(f"{malformed} has a `time` `{spelled(Text(row['time']))}` not in the form `{STAMP}`, UTC to the "
                     "second")
    return FilePath(row["briefs"]), Label(row["label"]), Logged(time, Shape(row["shape"]))


@functools.cache
def launch_ledger() -> dict[tuple[FilePath, Label], list[Logged]] | Prose:
    """Return the rows of LEDGER by the briefs file and label each names, in its order, none where it is absent, its
    torn tail after the last newline passed over; else why it refuses every launch: it cannot be read, or a complete
    line of it is no row (ledger_row)."""
    found: dict[tuple[FilePath, Label], list[Logged]] = {}
    try:
        with LEDGER.open("rb") as ledger:
            for number, line in enumerate(ledger, start=1):
                if not line.endswith(b"\n"):
                    break
                row = ledger_row(Line(number), Ledgered(line))
                if isinstance(row, str):
                    return row
                briefs, label, logged = row
                found.setdefault((briefs, label), []).append(logged)
    except FileNotFoundError:
        return {}
    except (OSError, MemoryError) as exc:
        return Prose(f"the launch ledger {LEDGER} cannot be read: {type(exc).__name__}: {exc}")
    return found


def ledger_fault() -> list[Prose]:
    """Return why the launch ledger refuses every launch: it cannot be read or holds a complete line that is no row;
    none where it reads or is absent."""
    rows = launch_ledger()
    return [rows] if isinstance(rows, str) else []


@functools.cache
def past_agents() -> dict[Shape, Agents]:
    """Return, by SHAPE, each past run holding an agent whose brief carried it, with those agents' transcript names.

    Read once per launch: every agent transcript of a run directory under HOME's PROJECTS takes the SHAPE of the
    latest row of the launch ledger naming the briefs file and label its opening turn names, of those timed at or
    before it starts, the later line of two at one time; one no row binds is of no SHAPE. No briefs file is read.
    A ledger refusing every launch, which ledger_fault reports, binds none.
    """
    ledger = launch_ledger()
    found: dict[Shape, Agents] = {}
    for transcript in sorted((Path.home() / PROJECTS).glob(f"{SESSIONS}/{AGENTS}")):
        run = Run(transcript.parent.name)
        named = entry_of(transcript) if RUN_ID.fullmatch(run) else None
        rows = ledger.get((named.briefs, named.labels[0]), []) if named is not None and isinstance(ledger, dict) else []
        start = started(transcript) if rows else None
        bound = [row for row in reversed(rows) if start is not None and row.time <= start]
        if bound:
            shape = max(bound, key=lambda row: row.time).shape
            found.setdefault(shape, {}).setdefault(run, set()).add(Transcript(transcript.name))
    return found


def shape_largest(shape: Shape, watch: Callable[[], Tally]) -> Measured | None:
    """Return the past agent of ``shape`` that spent the most, over every run, as the watch counts it; None where
    no agent of it recorded a call."""
    measured: list[Measured] = []
    for run, names in past_agents().get(shape, {}).items():
        tally = watch()
        for directory in run_directories(Text(run)):
            tally.read(directory)
        if (found := largest(tally, names)) is not None:
            measured.append(found)
    return max(measured, key=lambda found: found.tokens, default=None)


def largest(tally: Tally, names: set[Transcript] | None = None) -> Measured | None:
    """Return the agent of ``tally`` that spent the most, of ``names`` where given, with the least path its
    transcript was read from; None where no such agent recorded a call."""
    agents = {name: tokens for name, tokens in tally.agents().items() if names is None or name in names}
    if not agents:
        return None
    name, tokens = max(agents.items(), key=lambda agent: agent[1])
    return Measured(tokens, min(path for path in tally.offsets if path.name == name))


@functools.cache
def nearest(below: Path) -> Path:
    """Return ``below`` in the nearest folder holding this file's resolved path; else where it is looked for first."""
    places = [folder / below for folder in Path(__file__).resolve().parents]
    return next((place for place in places if place.exists()), places[0])


@functools.cache
def token_watch() -> Callable[[], Tally] | Prose:
    """Return the Tally of workflow-token-watch, loaded from WATCH in the nearest folder holding this file; else why not.

    A watch found nowhere is loaded from the nearest place it is looked for, and fails as missing there.
    """
    place = nearest(WATCH)
    loader = SourceFileLoader(WATCH_MODULE, str(place))
    module = module_from_spec(ModuleSpec(WATCH_MODULE, loader, origin=str(place)))
    try:
        loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as exc:
        return Prose(f"{place} does not load: {type(exc).__name__}: {exc}")
    return cast("Callable[[], Tally]", module.Tally)


def run_directories(source: Text) -> list[Path]:
    """Return the directories ``source`` names: a run id's in every session under HOME's PROJECTS, else the path."""
    if RUN_ID.fullmatch(source) is None:
        return [Path(source)]
    return sorted((Path.home() / PROJECTS).glob(f"{SESSIONS}/{source}"))


def measured_problems(thousands: Count, source: Text, watch: Callable[[], Tally]) -> list[Prose]:
    """Return what keeps ``thousands``k from covering the largest agent of the run ``source`` names, as ``watch`` counts."""
    directories = run_directories(source)
    unmeasured = Prose("its SIZE line's source names no measured run: ")
    if not directories:
        return [Prose(f"{unmeasured}no directory {Path.home() / PROJECTS}/{SESSIONS}/{source}")]
    tally = watch()
    for directory in directories:
        tally.read(directory)
    measured = largest(tally)
    if measured is None:
        return [Prose(f"{unmeasured}no agent-*.jsonl in {', '.join(map(str, directories))} records a call")]
    if thousands * THOUSAND >= measured.tokens:
        return []
    return [Prose(f"its SIZE line states {thousands}k, below the {measured.tokens:,} new tokens {measured.transcript} "
                  "spent, as workflow-token-watch counts them")]


def one_line(text: Text) -> bool:
    """Say whether ``text`` is one line holding more than blanks."""
    return bool(text.strip()) and text.splitlines() == [text]


@functools.cache
def shape_registry() -> dict[Shape, Row] | Prose:
    """Return the rows of the SHAPE registry, REGISTRY nearest this file, by SHAPE; else why it cannot be read.

    A row is a table named by a SHAPE name holding a one-line DEFINITION, a one-line RETIRED reason where no brief
    of it launches, and no other key; the first row that is not is reported.
    """
    place = nearest(REGISTRY)
    try:
        with place.open("rb") as handle:
            table = tomllib.load(handle)
    except (OSError, ValueError, MemoryError, RecursionError) as exc:
        return Prose(f"the SHAPE registry {place} cannot be read: {type(exc).__name__}: {exc}")
    rows: dict[Shape, Row] = {}
    for name, row in table.items():
        malformed = Prose(f"the SHAPE registry {place} is malformed: its row `{shown(Text(name))}`")
        if SHAPE_NAME.fullmatch(name) is None:
            return Prose(f"{malformed} is no SHAPE name of lower-case letters, digits and hyphens")
        if not isinstance(row, dict):
            return Prose(f"{malformed} is no table")
        if unknown := sorted(set(row) - {DEFINITION, RETIRED}):
            return Prose(f"{malformed} holds `{shown(Text(unknown[0]))}`, where a row holds `{DEFINITION}` and "
                         f"`{RETIRED}`")
        definition, retired = row.get(DEFINITION), row.get(RETIRED)
        if not isinstance(definition, str) or not one_line(Text(definition)):
            return Prose(f"{malformed} has no `{DEFINITION}` of one line")
        if retired is not None and (not isinstance(retired, str) or not one_line(Text(retired))):
            return Prose(f"{malformed} has a `{RETIRED}` that is no reason of one line")
        rows[Shape(name)] = Row(Text(definition), None if retired is None else Text(retired))
    return rows


def registry_fault() -> list[Prose]:
    """Return why the SHAPE registry refuses every launch: it cannot be read or holds a malformed row; none where it
    reads."""
    rows = shape_registry()
    return [rows] if isinstance(rows, str) else []


def registry_problems(shape: Shape) -> list[Prose]:
    """Return what keeps ``shape`` from launching by the SHAPE registry: no row names it, or its row is retired; none
    where the registry cannot be read, which registry_fault reports."""
    rows = shape_registry()
    if isinstance(rows, str):
        return []
    row = rows.get(shape)
    if row is None:
        return [Prose(f"its SHAPE `{shape}` is no row of the SHAPE registry {nearest(REGISTRY)}: a new SHAPE is added "
                      "to it only by the user's ruling")]
    if row.retired is not None:
        return [Prose(f"its SHAPE `{shape}` is retired in the SHAPE registry {nearest(REGISTRY)}: {row.retired}")]
    return []


def decisions_problems(lines: list[Text]) -> list[Prose]:
    """Return what keeps the brief's DECISIONS line from naming the absolute path of a JOURNAL file."""
    named = lone(lines, DECISIONS_LEAD, Prose(f"it has no line `{DECISIONS_LEAD}<absolute path>{JOURNAL}`, the file "
                                              "its agent appends each decision to as it takes it"),
                 Prose("one decisions file"))
    if isinstance(named, list):
        return named
    path = Text(named.strip())
    if not Path(path).is_absolute() or not path.endswith(JOURNAL):
        return [Prose(f"its DECISIONS line names `{shown(path)}`, no absolute path ending `{JOURNAL}`")]
    return []


def proof_problems(lines: list[Text]) -> list[Prose]:
    """Return what keeps the brief's PROOF line from leaving the closing proof to the main thread."""
    stated = lone(lines, PROOF_LEAD, Prose(f"it has no line `{PROOF_LEAD}{MAIN_THREAD}`, leaving every suite, gate "
                                           "and untouched lint to the main thread"), Prose("one proof"))
    if isinstance(stated, list):
        return stated
    proof = Text(stated.strip())
    if proof != MAIN_THREAD:
        return [Prose(f"its PROOF line reads `{shown(proof)}`, where it reads `{MAIN_THREAD}`")]
    return []


def advisor_problems(lines: list[Text]) -> list[Prose]:
    """Return what keeps the brief's ADVISOR line from saying its agent never consults the advisor."""
    stated = lone(lines, ADVISOR_LEAD, Prose(f"it has no line `{ADVISOR_LEAD}{NO_ADVISOR}`, {NEVER}"),
                  Prose(f"the one line `{ADVISOR_LEAD}{NO_ADVISOR}`, {NEVER}"))
    if isinstance(stated, list):
        return stated
    advisor = Text(stated.strip())
    if advisor != NO_ADVISOR:
        return [Prose(f"its ADVISOR line reads `{shown(advisor)}`, where it reads `{NO_ADVISOR}`, {NEVER}")]
    return []


def brief_problems(brief: Text, alone: bool) -> tuple[list[Prose], Shape | None]:
    """Return what keeps ``brief`` from holding its GOAL, ITEMS, SHAPE, SIZE, DECISIONS, PROOF and ADVISOR lines, and
    the SHAPE it names, None where it names none.

    ``alone`` says whether its launch names it alone, as it names a PILOT.
    """
    lines = [Text(line) for line in brief.splitlines()]
    shape = shape_of(lines)
    named, unnamed = (shape, []) if isinstance(shape, str) else (None, shape)
    barred = [] if named is None else registry_problems(named)
    return (goal_problems(lines) + items_problems(lines) + unnamed + barred
            + size_problems(lines, alone, None if barred or ledger_fault() else named) + decisions_problems(lines)
            + proof_problems(lines) + advisor_problems(lines)), named


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


def launch_problems(tool_input: LaunchInput) -> tuple[list[Prose], list[Launched]]:
    """Return what keeps a Workflow launch from naming one or two briefs that keep the rule, a built-in's one, and
    the briefs it names that name a SHAPE, in its order, as they were read."""
    workflow = built_in(tool_input)
    reference = args_reference(tool_input) if workflow is None else description_reference(tool_input, workflow)
    if not isinstance(reference, Reference):
        return [reference], []
    briefs, labels = reference
    table = read_briefs(briefs)
    if not isinstance(table, dict):
        return [table], []
    found: list[Prose] = []
    launched: list[Launched] = []
    alone = len(labels) == 1
    for label in labels:
        named = Prose(f"brief `{shown(Text(label))}`")
        if label not in table:
            found.append(Prose(f"{named}: no such label in {briefs}"))
            continue
        brief = table[label]
        if brief is None:
            found.append(Prose(f"{named}: its text in {briefs} is not a string"))
            continue
        problems, shape = brief_problems(brief, alone)
        found.extend(Prose(f"{named}: {problem}") for problem in problems)
        if shape is not None:
            launched.append(Launched(briefs, label, shape, brief))
    return found, launched


def keep_torn(tail: Ledgered, time: Text) -> list[Prose]:
    """Append ``tail``, LEDGER's torn tail, to TORN as a JSON line stamped ``time``; return why it cannot."""
    try:
        with TORN.open("a", encoding="utf-8") as torn:
            torn.write(json.dumps({"time": time, "torn": tail.decode(errors="backslashreplace")}) + "\n")
    except OSError as exc:
        return [Prose(f"the torn tail of the launch ledger {LEDGER} cannot be appended to {TORN}: "
                      f"{type(exc).__name__}: {exc}")]
    return []


def record(launched: list[Launched]) -> list[Prose]:
    """Append to LEDGER a line per brief of ``launched``, a launch every check keeps, creating its folder where
    missing, its torn tail first kept (keep_torn) and cut away; return why it cannot, which refuses the launch."""
    time = Text(datetime.now(UTC).strftime(STAMP))
    lines = "".join(json.dumps({"time": time, "briefs": brief.briefs, "label": brief.label, "shape": brief.shape,
                                "sha256": hashlib.sha256(brief.text.encode(errors="surrogatepass")).hexdigest()}) + "\n"
                    for brief in launched)
    try:
        LEDGER.parent.mkdir(exist_ok=True)
        with LEDGER.open("ab+") as ledger:
            ledger.seek(0)
            held = Ledgered(ledger.read())
            cut = held.rfind(b"\n") + 1
            if cut < len(held):
                if fault := keep_torn(Ledgered(held[cut:]), time):
                    return fault
                ledger.truncate(cut)
            ledger.write(lines.encode())
    except OSError as exc:
        return [Prose(f"the launch ledger {LEDGER} cannot be appended to: {type(exc).__name__}: {exc}")]
    return []


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
        fault = registry_fault() + ledger_fault()
        problems, launched = launch_problems(tool_input)
        found = fault + problems + script_problems(tool_input, cwd)
    except Exception as exc:  # noqa: BLE001 - a fault of the hook: reported, never a silent block
        print(f"workflow-launch-guard: internal error, the call goes ahead: {type(exc).__name__}: {exc}", file=sys.stderr)
        return REPORTED
    if not found:
        found = record(launched)
    if found:
        print(ADVICE.format(problems="\n".join(f"  * {problem}" for problem in found)), file=sys.stderr)
        return REFUSE
    return ALLOW


if __name__ == "__main__":
    sys.exit(main())
