#!/usr/bin/env python3
"""Regression cases for workflow-launch-guard.py.

Every case pipes one PreToolUse event for a Workflow call to a copy of the hook
in a kit of its own under a fresh temporary directory, run by its path as the
harness runs it, the kit holding a copy of workflow-token-watch and a SHAPE
registry the suite writes, so the registry the user rules decides no case. HOME
is a folder of that directory, so the kill switch is the case's own, the call
naming briefs of a briefs file written under that directory, its script one the
hook parses: inline, a file, or a saved workflow under that HOME or the case's
own CLAUDE_PROJECT_DIR. Its PATH holds a `claude` resolving to a fake Claude Code
binary written under that directory, so a launch by a name found nowhere reads
the fake, never the installed binary; the past runs its briefs' SHAPE and SIZE
lines are held to are agent transcripts written under that HOME, each prompted
from a briefs file there, of the SHAPE the launch ledger written there records,
counted by the kit's workflow-token-watch. It compares
the answer with the rule:
exit 2 refuses a launch, printing `BLOCKED: ` and the rule, then one line per
problem, no more, naming the brief's label or the script's line and what it
breaks; exit 0 with no output lets the launch through, and every call of
another tool. The ledger cases run each under a HOME of its own and read back
the rows a launch let through appends to its ~/.claude/workflow-launches.jsonl,
and what a refused launch leaves there, a ledger that cannot be read or holds a
complete line that is no row refusing every launch, and a torn tail after its
last newline cut by the next launch, kept in ~/.claude/workflow-launches.torn.

Usage: test_workflow_launch_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation; exit 2, before any case, when
no folder holding GUARD holds .local/bin/workflow-token-watch, the watch each
kit's copy of the guard counts with, as for a copy under a TMPDIR outside the kit.
"""

import hashlib
import importlib.metadata
import itertools
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict

# A case's name; prose a case names itself by or expects, a brief's line among
# it; a brief's label; a brief's text; a call's input, its args, or an event as
# JSON text; the hook's exit status; a line of a script; text of a fake binary;
# the name of a fake install or of a kit; the version a fake install is named
# by.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
Label = NewType("Label", str)
BriefText = NewType("BriefText", str)
InputText = NewType("InputText", str)
ArgsText = NewType("ArgsText", str)
EventText = NewType("EventText", str)
ExitStatus = NewType("ExitStatus", int)
LineNumber = NewType("LineNumber", int)
Code = NewType("Code", str)
Tag = NewType("Tag", str)
Version = NewType("Version", str)
AddressSpace = NewType("AddressSpace", int)  # bytes of a process's address space
CpuSeconds = NewType("CpuSeconds", int)  # seconds of a process's processor time
Tokens = NewType("Tokens", int)  # the `briefs=` tokens a description holds
Thousands = NewType("Thousands", int)  # the thousands of new tokens a SIZE line states
Spent = NewType("Spent", int)  # new tokens a model call records under one field of its usage
CallId = NewType("CallId", str)  # a model call's message id
TranscriptLine = NewType("TranscriptLine", str)  # a line of an agent's transcript
Ledgered = NewType("Ledgered", bytes)  # what a HOME's launch ledger holds, or the part of it one launch appended
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Start = Literal["script", "scriptPath", "name"]

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "workflow-launch-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="workflow-launch-guard-test-")).resolve()
HOME = BASE / "home"
KILL_SWITCH = HOME / ".claude" / "workflow-launch-guard.off"
LEDGER = Path(".claude") / "workflow-launches.jsonl"  # below a HOME: the ledger a launch let through appends to
TORN = LEDGER.with_name("workflow-launches.torn")  # below a HOME: the torn tails a launch cut from the ledger
STAMP = "%Y-%m-%dT%H:%M:%SZ"  # a ledger row's time: UTC to the second
STAMPED = re.compile(rb'\{"time": "([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)"')  # a ledger line's start
BRIEFS = BASE / "briefs.json"
HASHED = BASE / "odd#name.json"  # BRIEFS's table under a name holding `#`
NOT_JSON = BASE / "not-json.json"
A_LIST = BASE / "a-list.json"
GONE = BASE / "gone.json"
LONG = BASE / ("a-briefs-file-whose-name-runs-past-what-a-problem-line-shows-of-a-label-" * 2 + "x.json")
PROJECT = BASE / "project"
SCRIPT_FILE = BASE / "x.js"
SAVED = HOME / ".claude" / "workflows" / "x.js"
PROJECT_SAVED = PROJECT / ".claude" / "workflows" / "in-project.js"
PLANT = BASE / "plant"  # a tree_sitter_javascript that fails to import, put first on PYTHONPATH
FAULT = BASE / "fault"  # a tree_sitter_javascript that imports and whose language() raises
NESTED = BASE / "nested.json"  # nested past the decoder's stack
BIG = BASE / "big.json"  # past CAP once decoded
CAP = AddressSpace(128 << 20)  # address space a case runs the hook under; a good launch was measured to run under 64 MiB
DEEP = "[" * 100_000  # JSON text nested past the decoder's stack
WIDE = 5000  # digits of a count past what int() converts
SPENT = CpuSeconds(10)  # processor time every hook run is held to, so a hook that never ends fails its case
PINS = {"tree-sitter": "0.25.2", "tree-sitter-javascript": "0.25.0"}
INTERPRETER = "/home/nicolas/.local/bin/python3.14"
RULE = Prose("BLOCKED: a Workflow launch names one or two briefs of a prepared file, each with a one-sentence "
             "GOAL, an ITEMS list of at most 10, a SHAPE, a SIZE measured on that SHAPE or a lone PILOT, a "
             "DECISIONS file, `PROOF: main thread` and `ADVISOR: none` (user rules, 2026-11-03 and 2026-11-05).\n")
NOTHING = Prose("")
RESEARCH = Prose("deep-research")
# How a built-in's launch names its brief, as its refusals say it.
WAY = Prose("where a launch of the built-in workflow `deep-research`, whose args the hook does not read, names its one "
            "brief by one token `briefs=<absolute path>#<label>` in its `description`")
ADVISED = Prose("its one brief in the launch's `description` by one token\n`briefs=/abs/path/briefs.json#<label>`.")

# Fake Claude Code binaries the hook reads for the workflows it registers: one
# install per tag, its `claude` on a PATH of its own.
INSTALLS = BASE / "installs"
BINS = BASE / "bins"
STANDARD_TAG = Tag("standard")
BIN = BINS / STANDARD_TAG  # the PATH of every case that names no other
NO_BIN = BASE / "no-bin"  # a PATH holding no `claude`
VERSIONS = HOME / ".local" / "share" / "claude" / "versions"
STANDARD_VERSION = Version("9.9.9")
FAULTY_VERSION = Version("1.0.0")
UNREADABLE = Prose("cannot read Claude Code's built-in workflows")
SPARSE = 256 << 20  # bytes of a fake binary, and of OVERSIZED's one line, past CAP, written sparse
BAD = Prose("export const meta = {name: 'x'}\n")  # a script calling no agent
BAD_SCRIPT = BASE / "bad.js"
REGISTER = Code('function Q1$(o,e,r){v$().bundledWorkflows.push({source:"built-in",...e,script:o})}')
PLUGIN = Code('function P9(o,e,r){v$().bundledWorkflows.push({source:"plugin",...e,script:o})}')
Q1 = Code("Q1$")
FIRST_CALL = "Q1$(`export const meta = {"


def call(function: Code, meta: Code, named: Code) -> Code:
    """A call of ``function`` whose template opens `export const meta = {` and holds ``meta``, its object ``named``."""
    return Code(f"{function}(`export const meta = {{{meta}`,{{{named}}})")


def chunk(*parts: Code) -> Code:
    """A module of a fake binary: the line opening each, then ``parts``."""
    return Code("\0// @bun @bytecode\n// Version: 9.9.9\n" + "".join(parts) + "\n")


STANDARD = Code("".join([
    "\x7fELF a fake Claude Code\n",
    chunk(REGISTER, PLUGIN),
    chunk(Code('var e="stale-one";function z(){}var i="aliased-one",e=i,t="d";obj.e="not-e";var k=\'quoted-var\';'
               "function a(){"),
          call(Q1, Code("\n  name: '${e}',\n}\n// Run as Workflow({name: 'decoy-inside', args: '<q>'}).\n"
                        "const q = \\`inner \\${x}\\`\n"), Code("name:e,description:t")),
          Code(";"), call(Q1, Code("}"), Code("name:k")), Code("}")),
    chunk(call(Q1, Code("\n  name: 'fresh-one',\n}\n"), Code('name:"fresh-one",description:"x"'))),
    chunk(call(Q1, Code("}"), Code("name:'single-one'"))),
    chunk(call(Q1, Code("}"), Code('name:"deep-research"'))),
    chunk(call(Q1, Code('}\nconst root = "C:\\\\"\n\\\\'), Code('name:"slashed-one"'))),
    chunk(Code('const doc = "Example:\\nexport const meta = {\\n  name: \'review-changes\',\\n}";\n'),
          call(Code("R2s"), Code("\n  name: 'other-fn',\n}"), Code('name:"other-fn"')),
          call(Code("P9"), Code("}"), Code('name:"plugin-one"')),
          call(Code("xQ1$"), Code("}"), Code('name:"suffix-one"')),
          Code("obj."), call(Q1, Code("}"), Code('name:"member-one"')),
          call(Code("$Q1$"), Code("}"), Code('name:"dollar-one"'))),
]))
# Fakes whose names the hook cannot read, each by one fault, with the reason it
# gives; None stands for the first call of Q1$ naming no string.
FAULTY: dict[Tag, tuple[Code, Prose | None]] = {
    Tag("no-function"): (chunk(PLUGIN, call(Q1, Code("}"), Code('name:"lost"'))),
                         Prose('no function pushes {source:"built-in"} to bundledWorkflows')),
    Tag("no-call"): (chunk(REGISTER, Code('const doc = "export const meta = {";')),
                     Prose("no call of Q1$ registers a script")),
    Tag("earlier-chunk"): (Code(chunk(REGISTER, Code('var z="from-earlier";'))
                                + chunk(call(Q1, Code("}"), Code("name:z")))), None),
    Tag("member-value"): (chunk(REGISTER, Code('var w="member-name",z=w.length;'), call(Q1, Code("}"), Code("name:z"))),
                          None),
    Tag("member-name"): (chunk(REGISTER, Code('var q="dotted";'), call(Q1, Code("}"), Code("name:q.value"))), None),
    Tag("no-marker"): (Code(REGISTER + 'var e="unmarked";' + call(Q1, Code("}"), Code("name:e"))), None),
    Tag("cycle"): (chunk(REGISTER, Code("var e=f,f=e;"), call(Q1, Code("}"), Code("name:e"))), None),
    Tag("no-name-key"): (chunk(REGISTER, call(Q1, Code("}"), Code('description:"x"'))), None),
    Tag("unclosed"): (chunk(REGISTER, Code('Q1$(`export const meta = {}\\`,{name:"never-closed"})')), None),
    Tag("empty"): (Code(""), Prose("ValueError: cannot mmap an empty file")),
}
SPARSE_TAG = Tag("past-the-cap")

FACTS = Prose("FACTS: the hook is next/workflow-launch-guard.py, read with python3.14.\nRETURN: the diff.\n")
# The ITEMS line of a brief the rule was ruled on, verbatim: `; ` between items
# that hold commas, and a `;` inside backticks.
MODEL_ITEMS = Prose(
    "ITEMS (6): the per-agent tally, each agent-*.jsonl's own sum by the counting the total already uses; "
    "the per-agent ceiling AGENT_CEILING = 700,000; the line `OVER LIMIT: <agent file name> <n> new tokens, "
    "agent ceiling 700,000; stop the workflow` printed once and the watcher exiting, as for the total; the usage "
    "docstring naming both ceilings; suite cases in tests/test_workflow_token_watch.py (an agent past 700k while "
    "the total is under 2M prints the agent line and exits, two agents each under 700k summing past it do not, "
    "the total line unchanged); the mutants of tests/mut/workflow_token_watch.json extended to the new code, all "
    "killed."
)
GOAL = Prose("GOAL: the guard refuses a launch naming three briefs.")

# A script that keeps the rule: each agent runs per label, prompted by it.
GOOD = Prose("export const meta = {name: 'x'}\nfor (const label of args.labels) {\n"
             "  await agent(`brief ${label} of ${args.briefs}`)\n}\n")
# The script the rule was ruled on, verbatim: its prompt a const built from the label.
MODEL_SCRIPT = Prose("""export const meta = {
  name: 'kill-mutation-survivors',
  description: 'Kill surviving mutants, one agent per file group, strictly in sequence in one tree',
  phases: [{ title: 'Kill', detail: 'one agent per brief, each finishing before the next starts' }],
}
const SCHEMA = {
  type: 'object',
  properties: {
    entries: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          row: { type: 'string' },
          verdict: { type: 'string', enum: ['killed', 'restructured_and_killed', 'equivalent_decision', 'unreachable_decision', 'not_done'] },
          tests: { type: 'array', items: { type: 'string' } },
          evidence: { type: 'array', items: { type: 'string' } },
          note: { type: 'string' },
        },
        required: ['row', 'verdict', 'tests', 'evidence'],
      },
    },
    decision_points: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          site: { type: 'string' },
          question: { type: 'string' },
          alternatives: { type: 'array', items: { type: 'string' } },
          evidence: { type: 'string' },
        },
        required: ['site', 'question', 'alternatives', 'evidence'],
      },
    },
    files_changed: { type: 'array', items: { type: 'string' } },
    final_checks: { type: 'string' },
  },
  required: ['entries', 'decision_points', 'files_changed', 'final_checks'],
}
phase('Kill')
const results = []
for (const label of args.labels) {
  const prompt = `Your task brief is the entry "${label}" of the JSON object in ${args.briefs} (keys are labels, values are briefs). Read it first with: python3 -c "import json;print(json.load(open('${args.briefs}'))['${label}'])" and then do exactly what it says. Use "${label}" as your label for log directories. Work only on your own rows; another agent ran before you and may have edited other files, which you leave alone.`
  const r = await agent(prompt, { label, phase: 'Kill', schema: SCHEMA })
  results.push({ label, result: r })
}
return results
""")
ONE = Prose("ITEMS (1): a")
# The runs a SIZE line's source names, kept under HOME as Claude Code keeps a
# workflow's transcripts, one session folder each, a run in two after a /clear.
WATCH = Path(".local") / "bin" / "workflow-token-watch"  # below a folder holding the guard: the watch it counts with
PROJECTS = HOME / ".claude" / "projects"
SESSION = PROJECTS / "-home-x-project" / "session-1" / "subagents" / "workflows"
LATER = PROJECTS / "-home-x-project" / "session-2" / "subagents" / "workflows"
MEASURED = SESSION / "wf_dc866405-e65"  # the run SIZED names
LARGEST = MEASURED / "agent-b.jsonl"  # its largest agent: 82,000 new tokens as the watch counts them
MOVED = Prose("wf_moved")  # a run whose agent-c spent 100,000 over two session folders
MOVED_AGENT = SESSION / MOVED / "agent-c.jsonl"  # its first copy, the one holding its prompt
HEAVY = SESSION / "wf_heavy-2" / "agent-b.jsonl"  # the most an agent of SHAPE dbc-heavy spent, 300,000, of three runs
SKIPS = SESSION / "wf_skips"  # a run whose agents are mostly bound to no ledger row
SKIP_SMALL = SKIPS / "agent-s1.jsonl"  # its one agent bound to a row: 5,000 new tokens
NAIVE = SKIPS / "agent-naive.jsonl"  # its agent whose first timestamp carries no offset
UNDATED = SKIPS / "agent-undated.jsonl"  # its agent whose first timestamp is no ISO 8601 time
NUMBERED = SKIPS / "agent-numbered.jsonl"  # its agent whose first timestamp is a number
FOLDER = SKIPS / "agent-folder.jsonl"  # a transcript's name on a folder
BLOCKS = SESSION / "wf_blocks" / "agent-k.jsonl"  # an agent prompted in a list of text blocks: 4,000 new tokens
RELAYED = SESSION / "wf_relayed" / "agent-r.jsonl"  # an agent whose prompt follows a relayed request: 6,000 new tokens
ONE_LINE = SESSION / "wf_one-line" / "agent-o.jsonl"  # its request and its prompt on one line: 7,000 new tokens
ELSEWHERE = SESSION / "wf_elsewhere" / "agent-e.jsonl"  # its request naming another briefs file: 8,000 new tokens
PRECEDED = SESSION / "wf_preceded" / "agent-p.jsonl"  # its prompt after lines carrying no message: 9,000 new tokens
MIXED = SESSION / "wf_mixed" / "agent-m.jsonl"  # its prompt among blocks no reader takes it from: 11,000 new tokens
OVERSIZED = SESSION / "wf_oversized" / "agent-z.jsonl"  # a transcript of one line past CAP, written for its one case
OVERRAN = SESSION / "wf_overran" / "agent-v.jsonl"  # the one agent of SHAPE overran-kind: 800,001, one past the ceiling
AT_CEILING = SESSION / "wf_at-ceiling" / "agent-t.jsonl"  # the one agent of SHAPE at-ceiling-kind: 800,000, the ceiling
EDITED = SESSION / "wf_edited" / "agent-d.jsonl"  # launched as edited-kind, its brief's SHAPE edited since: 12,000
RELAUNCHED = SESSION / "wf_relaunched-2" / "agent-g.jsonl"  # its brief launched again as relaunched-kind: 15,000
TWICE = SESSION / "wf_twice" / "agent-w.jsonl"  # its brief launched twice in one second, the later as twice-kind: 16,000
RESET = SESSION / "wf_reset" / "agent-j.jsonl"  # its brief's later-timed row, an earlier line, as reset-kind: 17,000
RECORD = BASE / "record.jsonl"  # a transcript's file, no run directory
PAST = HOME / "past" / "briefs.json"  # the briefs file the past runs' agents were prompted from
# When the past runs were launched, their ledger rows' time; when each of their
# agents starts, its transcript's first timestamp, but where STARTS says; when
# brief skip-late was launched, after its agent started; when brief relaunched
# was launched again, RELAUNCHED starting at that very second.
LAUNCHED = datetime(2026, 1, 1, tzinfo=UTC)
STARTED = LAUNCHED + timedelta(minutes=1)
LATE = STARTED + timedelta(seconds=1)
AGAIN = STARTED + timedelta(hours=1)
REQUESTED = HOME / "requested" / "briefs.json"  # the briefs file ELSEWHERE's relayed request names
# The SHAPE, SIZE, DECISIONS, PROOF and ADVISOR lines a brief carries unless a case drops or replaces one.
SHAPED = Prose("SHAPE: guard-code")
SIZED = Prose("SIZE: 82k from wf_dc866405-e65")
LOGGED = Prose(f"DECISIONS: {BASE / 'decisions.jsonl'}")
PROVED = Prose("PROOF: main thread")
UNADVISED = Prose("ADVISOR: none")
PILOT = Prose("SIZE: PILOT")
SIZE_FORMS = Prose("`<n>k [from <source>]` nor `PILOT`")
NEVER = Prose("a workflow agent never consulting the advisor")  # the reason every ADVISOR refusal ends on
# The forms the advice names a brief's SHAPE, SIZE, DECISIONS, PROOF and ADVISOR lines by, the count it holds a SIZE
# to, and why a brief carries no advisor.
FORMS = (Prose("`SHAPE: <name>`"), Prose("`SIZE: <n>k`"), Prose("` from <source>`"), Prose("`SIZE: PILOT`"),
         Prose("`DECISIONS: <absolute path>.jsonl`"), Prose("`PROOF: main thread`"), Prose(f"`{UNADVISED}`"), NEVER)
COUNTED = Prose("the most that any past agent whose brief carried the same SHAPE spent as\n"
                "workflow-token-watch counts them, never the workflow's own subagent_tokens")
# The harness's frame around a runner's prompt, as a real agent transcript records it.
PREAMBLE = Prose("[Workflow harness — computed task] The task text below was computed at runtime by a workflow "
                 "script. The computed task text follows:\n  ")
# The user request the harness relays as an agent's first message, before the runner's prompt, quoting another's.
RELAY = Prose("[Workflow harness — user request] The harness relays, verbatim and indented below, the user request "
              "that triggered this workflow run:\n  run the brief whose prompt opens: ")


class Usage(TypedDict, total=False):
    """A model call's usage as a transcript records it, or one part of it."""

    input_tokens: Spent
    cache_creation_input_tokens: Spent
    cache_read_input_tokens: Spent
    output_tokens: Spent
    iterations: list["Usage"]


def called(call: CallId, usage: Usage) -> TranscriptLine:
    """A transcript line recording model call ``call`` at ``usage``."""
    return TranscriptLine(json.dumps({"type": "assistant", "message": {"id": call, "role": "assistant", "usage": usage}}))


def spent(tokens: Spent) -> Usage:
    """A usage of ``tokens`` input tokens alone."""
    return Usage(input_tokens=tokens)


def prompt(label: Label, briefs: Path = PAST) -> Prose:
    """A runner's prompt naming brief ``label`` of ``briefs``, on one line."""
    return Prose(f'Your task brief is the entry "{label}" of the JSON object in {briefs} (keys are labels, values are '
                 "briefs). Read it first and then do exactly what it says.")


def asked(label: Label, briefs: Path = PAST, preamble: Prose = Prose(""), blocks: bool = False) -> TranscriptLine:
    """A transcript's user message prompting its agent with brief ``label`` of ``briefs`` as a runner does, after
    ``preamble``, its content a list of one text block where ``blocks`` says so, else a string."""
    text = f"{preamble}{prompt(label, briefs)}"
    content = [{"type": "text", "text": text}] if blocks else text
    return TranscriptLine(json.dumps({"type": "user", "message": {"role": "user", "content": content}}))


ASKED = TranscriptLine(json.dumps({"type": "user", "message": {"role": "user", "content": "go"}}))  # records no call
# The first user message of an agent's later copy, kept after a /clear: a tool's result, no prompt.
RESUMED = TranscriptLine(json.dumps({"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "done"}]}}))


def timed(at: datetime) -> TranscriptLine:
    """A transcript line carrying no message, its timestamp ``at`` to the millisecond as Claude Code writes one."""
    return TranscriptLine(json.dumps({"type": "queue-operation",
                                      "timestamp": at.isoformat(timespec="milliseconds").replace("+00:00", "Z")}))


class Launch(NamedTuple):
    """A past launch of a brief as its ledger row records it: its time and the SHAPE the brief carried."""

    at: datetime
    shape: Prose


# The briefs file the past runs' agents were prompted from, as it is now: the
# SHAPE each brief carries.
PAST_TABLE = {Label(label): BriefText(f"SHAPE: {shape}\n") for label, shape in (
    ("code", "guard-code"), ("light", "light"), ("quiet", "quiet-kind"), ("moved", "moved-kind"),
    ("heavy", "dbc-heavy"), ("other", "other-kind"), ("skip-small", "skip-kind"), ("skip-heavy", "skip-kind"),
    ("blocks", "blocks-kind"), ("relayed", "relayed-kind"), ("decoy", "decoy-kind"), ("one-line", "one-line-kind"),
    ("elsewhere", "elsewhere-kind"), ("preceded", "preceded-kind"), ("mixed", "mixed-kind"),
    ("overran", "overran-kind"), ("at-ceiling", "at-ceiling-kind"), ("retired", "retired-kind"),
    ("edited", "edited-later-kind"), ("relaunched", "first-kind"), ("skip-unrowed", "unrowed-kind"),
    ("skip-late", "late-kind"), ("twice", "twice-kind"), ("reset", "reset-back-kind"))}
# The rows of the past launches' ledger, written under HOME: one at LAUNCHED per
# brief of PAST_TABLE, naming PAST and the SHAPE its text carries, but where
# LAUNCHES gives a brief its own: edited launched as a SHAPE its text no longer
# carries, skip-unrowed by no row, skip-late only after its agent started,
# relaunched again as another SHAPE, twice launched in one second as two SHAPEs,
# reset at a time set back before its earlier line's. A row of REQUESTED's
# brief edited follows, timed before EDITED starts, under the SHAPE PAST's text
# of it now carries, then a seed row naming PAST by a path relative to the
# folder the hook runs in, as no launch writes one.
LAUNCHES: dict[Label, list[Launch]] = {
    Label("edited"): [Launch(LAUNCHED, Prose("edited-kind"))], Label("skip-unrowed"): [],
    Label("skip-late"): [Launch(LATE, Prose("late-kind"))],
    Label("relaunched"): [Launch(LAUNCHED, Prose("first-kind")), Launch(AGAIN, Prose("relaunched-kind"))],
    Label("twice"): [Launch(LAUNCHED, Prose("twice-first-kind")), Launch(LAUNCHED, Prose("twice-kind"))],
    Label("reset"): [Launch(LAUNCHED + timedelta(seconds=1), Prose("reset-kind")),
                     Launch(LAUNCHED, Prose("reset-back-kind"))]}


def logged(briefs: Path, label: Label, row: Launch, seeded: bool = False) -> Ledgered:
    """The ledger line of brief ``label`` of ``briefs`` launched as ``row`` records, its text its SHAPE line alone,
    a seed row where ``seeded`` says so."""
    return Ledgered((json.dumps({"time": row.at.strftime(STAMP), "briefs": str(briefs), "label": label,
                                 "shape": row.shape,
                                 "sha256": hashlib.sha256(f"SHAPE: {row.shape}\n".encode()).hexdigest(),
                                 **({"seeded": True} if seeded else {})}) + "\n").encode())


PAST_LEDGER = Ledgered(b"".join([
    *(logged(PAST, label, row) for label, text in PAST_TABLE.items()
      for row in LAUNCHES.get(label, [Launch(LAUNCHED, Prose(text.removeprefix("SHAPE: ").rstrip()))])),
    logged(REQUESTED, Label("edited"), Launch(LAUNCHED + timedelta(seconds=1), Prose("edited-later-kind"))),
    logged(Path(os.path.relpath(PAST)), Label("skip-heavy"), Launch(LAUNCHED, Prose("skip-kind")), seeded=True)]))
SKIPPED = Spent(700_000)  # what each agent of SKIPS bound to no ledger row spent
# Each run's transcripts, every agent prompted from a brief of PAST but where
# said, main() writing each after its first line: STARTS's, else one timed at
# STARTED. LARGEST, prompted inside the harness's frame, holds a call recorded
# twice, counted at its last usage with its cache reads left out (32,000), and a
# call that consulted the advisor, counted over its parts (50,000); a count of
# every line's top level would read 33,210. Its run's other agents, named
# before and after it, spent less: 40,000 and 30,000. SHAPE dbc-heavy ran in
# three runs, its largest agent HEAVY in the second, by path, between two
# smaller ones, the third run holding an agent of another SHAPE that spent more.
TRANSCRIPTS: dict[Path, list[TranscriptLine]] = {
    LARGEST: [asked(Label("code"), preamble=PREAMBLE),
              called(CallId("m1"), Usage(input_tokens=Spent(1000), output_tokens=Spent(10))),
              called(CallId("m1"), Usage(input_tokens=Spent(10_000), cache_creation_input_tokens=Spent(20_000),
                                         cache_read_input_tokens=Spent(900_000), output_tokens=Spent(2000))),
              called(CallId("m2"), Usage(input_tokens=Spent(100), output_tokens=Spent(100), iterations=[
                  Usage(input_tokens=Spent(30_000), output_tokens=Spent(1000)),
                  Usage(input_tokens=Spent(15_000), cache_creation_input_tokens=Spent(3000),
                        output_tokens=Spent(1000))]))],
    MEASURED / "agent-a.jsonl": [asked(Label("code")), called(CallId("m3"), spent(Spent(40_000)))],
    MEASURED / "agent-c.jsonl": [asked(Label("code")), called(CallId("m9"), spent(Spent(30_000)))],
    SESSION / "wf_a" / "agent-x.jsonl": [asked(Label("light")), called(CallId("m4"), spent(Spent(1000)))],
    SESSION / "wf_quiet" / "agent-q.jsonl": [asked(Label("quiet"))],
    # agent-c's calls over two session folders, m5 in both: 50,000 + 10,000 + 40,000.
    MOVED_AGENT: [asked(Label("moved")), called(CallId("m5"), spent(Spent(50_000))),
                  called(CallId("m6"), spent(Spent(10_000)))],
    LATER / MOVED / "agent-c.jsonl": [RESUMED, called(CallId("m5"), spent(Spent(50_000))),
                                      called(CallId("m7"), spent(Spent(40_000)))],
    RECORD: [called(CallId("m8"), spent(Spent(1000)))],
    SESSION / "wf_heavy-1" / "agent-h.jsonl": [asked(Label("heavy")), called(CallId("h1"), spent(Spent(150_000)))],
    SESSION / "wf_heavy-2" / "agent-a.jsonl": [asked(Label("heavy")), called(CallId("h2"), spent(Spent(120_000)))],
    HEAVY: [asked(Label("heavy")), called(CallId("h3"), spent(Spent(300_000)))],
    SESSION / "wf_heavy-2" / "agent-c.jsonl": [asked(Label("heavy")), called(CallId("h4"), spent(Spent(90_000)))],
    LATER / "wf_heavy-3" / "agent-a.jsonl": [asked(Label("other")), called(CallId("h5"), spent(Spent(600_000)))],
    LATER / "wf_heavy-3" / "agent-b.jsonl": [asked(Label("heavy")), called(CallId("h6"), spent(Spent(200_000)))],
    SKIP_SMALL: [asked(Label("skip-small")), called(CallId("s1"), spent(Spent(5000)))],
    # Agents of SKIPS bound to no ledger row: brief skip-unrowed, which no row
    # names; skip-late, whose one row is timed after the agent starts and
    # before a later line; skip-heavy, whose rows name it as skip-kind, named by
    # the relative path its seed row names, or only after a first call; and
    # skip-heavy named by NAIVE, UNDATED and NUMBERED, which STARTS starts at no
    # time.
    **{SKIPS / f"agent-s{at}.jsonl": [*prompt, called(CallId(f"s{at}"), spent(SKIPPED))]
       for at, prompt in enumerate((
           [asked(Label("skip-unrowed"))], [asked(Label("skip-late")), timed(LATE + timedelta(seconds=1))],
           [asked(Label("skip-heavy"), briefs=Path(os.path.relpath(PAST)))],
           [ASKED, called(CallId("s0"), spent(Spent(10))), asked(Label("skip-heavy"))]), start=2)},
    NAIVE: [asked(Label("skip-heavy")), called(CallId("n1"), spent(SKIPPED))],
    UNDATED: [asked(Label("skip-heavy")), called(CallId("u2"), spent(SKIPPED))],
    NUMBERED: [asked(Label("skip-heavy")), called(CallId("w1"), spent(SKIPPED))],
    EDITED: [asked(Label("edited")), called(CallId("d1"), spent(Spent(12_000)))],
    # Brief relaunched's agents: one started before its second launch, and RELAUNCHED.
    SESSION / "wf_relaunched-1" / "agent-f.jsonl": [asked(Label("relaunched")), called(CallId("f1"), spent(Spent(14_000)))],
    RELAUNCHED: [asked(Label("relaunched")), called(CallId("g1"), spent(Spent(15_000)))],
    TWICE: [asked(Label("twice")), called(CallId("tw1"), spent(Spent(16_000)))],
    RESET: [asked(Label("reset")), called(CallId("j1"), spent(Spent(17_000)))],
    BLOCKS: [asked(Label("blocks"), blocks=True), called(CallId("k1"), spent(Spent(4000)))],
    RELAYED: [asked(Label("decoy"), preamble=RELAY), asked(Label("relayed"), preamble=PREAMBLE),
              called(CallId("r1"), spent(Spent(6000)))],
    # The request's quoted prompt and the agent's own, one line of one message.
    ONE_LINE: [asked(Label("one-line"), preamble=Prose(f"{RELAY}{prompt(Label('decoy'))} ")),
               called(CallId("o1"), spent(Spent(7000)))],
    ELSEWHERE: [asked(Label("decoy"), briefs=REQUESTED, preamble=RELAY), asked(Label("elsewhere"), preamble=PREAMBLE),
                called(CallId("e1"), spent(Spent(8000)))],
    # Lines passed over before the first timestamp and the prompt, STARTS's the
    # first: no JSON, JSON that is no object, an object carrying no message and
    # no timestamp, JSON nested past the decoder's stack.
    PRECEDED: [TranscriptLine("[1]"), TranscriptLine(json.dumps({"type": "queue-operation"})), TranscriptLine(DEEP),
               timed(STARTED), asked(Label("preceded")), called(CallId("p1"), spent(Spent(9000)))],
    # A text block whose text is no string, the prompt's text block, then a
    # block of another type carrying a prompt naming another brief.
    OVERRAN: [asked(Label("overran")), called(CallId("v1"), spent(Spent(800_001)))],
    AT_CEILING: [asked(Label("at-ceiling")), called(CallId("t1"), spent(Spent(800_000)))],
    SESSION / "wf_retired" / "agent-u.jsonl": [asked(Label("retired")), called(CallId("u1"), spent(Spent(900_000)))],
    MIXED: [TranscriptLine(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": 5}, {"type": "text", "text": prompt(Label("mixed"))},
        {"type": "tool_result", "tool_use_id": "t1", "text": prompt(Label("decoy"))}]}})),
        called(CallId("x1"), spent(Spent(11_000)))],
}
# The first line of a transcript not opening on a line timed at STARTED:
# RELAUNCHED's at AGAIN, NAIVE's at STARTED with no offset, UNDATED's at a word,
# NUMBERED's at a number, PRECEDED's no JSON, its timed line following.
STARTS = {RELAUNCHED: timed(AGAIN), NAIVE: timed(STARTED.replace(tzinfo=None)),
          UNDATED: TranscriptLine(json.dumps({"type": "queue-operation", "timestamp": "yesterday"})),
          NUMBERED: TranscriptLine(json.dumps({"type": "queue-operation", "timestamp": 5})),
          PRECEDED: TranscriptLine("not json")}
# The kits a copy of the guard runs in, each holding a watch and a SHAPE
# registry: MAIN_KIT, whose copy every case runs but where a case names another,
# holds a copy of the kit's watch and SHAPES.
KITS = BASE / "kits"
MAIN_KIT = Tag("main")
KIT_GUARD = KITS / MAIN_KIT / ".claude" / "hooks" / GUARD.name
REGISTRY = Path(".claude") / "workflows" / "shapes.toml"  # below a kit: the SHAPE registry its guard reads
LISTED = KITS / MAIN_KIT / REGISTRY
RETIRED = Prose("its work is done by narrower SHAPEs")  # the reason retired-kind's row gives
# A row for every SHAPE a brief of TABLE names but unlisted-kind, retired-kind's retired.
SHAPES = Code("".join(f'[{shape}]\ndefinition = "the work of {shape}"\n' for shape in (
    "guard-code", "light", "never-run", "dbc-heavy", "quiet-kind", "moved-kind", "skip-kind", "blocks-kind",
    "relayed-kind", "one-line-kind", "elsewhere-kind", "preceded-kind", "mixed-kind", "probe-23", "overran-kind",
    "at-ceiling-kind", "edited-kind", "unrowed-kind", "late-kind", "first-kind", "relaunched-kind", "twice-kind", "reset-kind")) +f'[retired-kind]\ndefinition = "the work of retired-kind"\nretired = "{RETIRED}"\n')
ADD_ONLY = Prose("a row of the kit's SHAPE registry that only the user's ruling adds")  # what the advice says of a SHAPE
# The registries a copy of the guard refuses every launch for, in a kit of its
# own: by kit, the registry's text (None for a folder in its place) and what the
# refusal says after its path.
BROKEN_REGISTRIES: dict[Tag, tuple[Code | None, Prose]] = {
    Tag("registry-folder"): (None, Prose("cannot be read: IsADirectoryError: ")),
    Tag("registry-syntax"): (Code("[a\n"), Prose("cannot be read: TOMLDecodeError: ")),
    Tag("registry-deep"): (Code("a = " + "[" * 100_000), Prose("cannot be read: RecursionError: ")),
    Tag("registry-name"): (Code('[Guard_Code]\ndefinition = "x"\n'),
                           Prose("is malformed: its row `Guard_Code` is no SHAPE name of lower-case letters, digits and "
                                 "hyphens")),
    Tag("registry-value"): (Code('bare = "x"\n'), Prose("is malformed: its row `bare` is no table")),
    Tag("registry-key"): (Code('[a]\ndefinition = "x"\nretire = "y"\n'),
                          Prose("is malformed: its row `a` holds `retire`, where a row holds `definition` and `retired`")),
    Tag("registry-undefined"): (Code('[a]\nretired = "y"\n'),
                                Prose("is malformed: its row `a` has no `definition` of one line")),
    Tag("registry-blank"): (Code('[a]\ndefinition = "  "\n'),
                            Prose("is malformed: its row `a` has no `definition` of one line")),
    Tag("registry-two-lines"): (Code('[a]\ndefinition = """x\ny"""\n'),
                                Prose("is malformed: its row `a` has no `definition` of one line")),
    Tag("registry-flag"): (Code('[a]\ndefinition = "x"\nretired = true\n'),
                           Prose("is malformed: its row `a` has a `retired` that is no reason of one line")),
}
SPARSE_REGISTRY = Tag("registry-past-the-cap")  # a registry of SPARSE bytes, read under CAP
SHOWN = 80  # characters a problem line shows of a quoted name, its last three `...` where the name is cut
LONG_ROW = "a-row-name-running-past-what-a-problem-line-shows-" * 2 + "x"  # a SHAPE name longer than SHOWN
LONG_ADVISOR = "none, unless the agent is stuck on a case " * 2 + "x"  # an ADVISOR value longer than SHOWN
# The malformed registries whose refusal quotes the part of the row it is chosen
# by, as BROKEN_REGISTRIES are given: a name refused at a character after a valid
# start, a name past SHOWN characters cut, the first by sort of two keys no row
# holds.
QUOTED_REGISTRIES: dict[Tag, tuple[Code | None, Prose]] = {
    Tag("registry-name-tail"): (Code('[guard_code]\ndefinition = "x"\n'),
                                Prose("is malformed: its row `guard_code` is no SHAPE name of lower-case letters, digits "
                                      "and hyphens")),
    Tag("registry-name-long"): (Code(f'{LONG_ROW} = "x"\n'),
                                Prose(f"is malformed: its row `{LONG_ROW[: SHOWN - 3]}...` is no table")),
    Tag("registry-keys"): (Code('[a]\nzone = "z"\ndefinition = "x"\nretire = "y"\n'),
                           Prose("is malformed: its row `a` holds `retire`, where a row holds `definition` and `retired`")),
}
# The malformed registries whose row's `retired` is a string of no one line, as
# BROKEN_REGISTRIES are given: blanks, and two lines.
UNREASONED_REGISTRIES: dict[Tag, tuple[Code | None, Prose]] = {
    Tag(f"registry-retired-{kind}"): (Code(f'[a]\ndefinition = "x"\nretired = {retired}\n'),
                                      Prose("is malformed: its row `a` has a `retired` that is no reason of one line"))
    for kind, retired in (("blank", '"  "'), ("two-lines", '"""x\ny"""'))
}
# A kit whose .claude/hooks is a relative symlink to MAIN_KIT's, as ~/.claude/hooks
# links into the dotfiles, beside it a SHAPE registry holding no row; and the
# folder a kit holding no SHAPE registry is written in, the system's temporary
# folder whatever TMPDIR names, since a TMPDIR inside the kit has a registry in
# a folder holding BASE.
LINKED = KITS / "linked"
OUTSIDE = Path("/tmp")
# The watches a copy of the guard finds broken, in a kit of its own: by kit, the
# watch's text (None for a folder in its place) and the error it is refused with.
BROKEN_WATCHES: dict[Tag, tuple[Code | None, Prose]] = {
    Tag("folder"): (None, Prose("IsADirectoryError")),
    Tag("syntax"): (Code("def (\n"), Prose("SyntaxError")),
    Tag("import"): (Code("import a_module_no_kit_holds\n"), Prose("ModuleNotFoundError")),
}
FIELDS = Ledgered(b'"time": "2026-11-05T00:00:00Z", "briefs": "/past.json", "label": "past", "shape": "light", '
                  b'"sha256": "00"')  # the fields of a ledger row a past launch appended, as JSON writes them
SEED_ROW = Ledgered(b"{" + FIELDS + b', "seeded": true}\n')  # a row the ledger was seeded with


def seeded_then(fields: Ledgered, old: Ledgered = Ledgered(b""), new: Ledgered = Ledgered(b"")) -> Ledgered:
    """A ledger of SEED_ROW, then a line holding the object of ``fields``, ``old`` in them written ``new``."""
    return Ledgered(SEED_ROW + b"{" + (fields.replace(old, new) if old else fields) + b"}\n")


# The ledgers a copy of the guard refuses every launch for, each under a HOME of
# its own beside the past runs: by HOME, the ledger's bytes (None for a folder
# in its place) and what the refusal says after the ledger's path. Each line
# that is no row, and the one that is no UTF-8, follows SEED_ROW, the refusal
# naming line 2; LOCKED_LEDGER's ledger may be written and not read, and
# SPARSE_LEDGER's is SPARSE bytes, read under CAP.
LOCKED_LEDGER, SPARSE_LEDGER = Tag("ledger-locked"), Tag("ledger-past-the-cap")
BROKEN_LEDGERS: dict[Tag, tuple[Ledgered | None, Prose]] = {
    Tag("ledger-folder"): (None, Prose("cannot be read: IsADirectoryError: ")),
    LOCKED_LEDGER: (SEED_ROW, Prose("cannot be read: PermissionError: ")),
    Tag("ledger-latin"): (Ledgered(SEED_ROW + b'{"time": "caf\xe9"}\n'),
                          Prose("cannot be read: its line 2: UnicodeDecodeError: ")),
    SPARSE_LEDGER: (Ledgered(b""), Prose("cannot be read: MemoryError: ")),
    Tag("ledger-not-json"): (Ledgered(SEED_ROW + b"{not json\n"),
                             Prose("is malformed: its line 2 is no JSON: JSONDecodeError: ")),
    Tag("ledger-list"): (Ledgered(SEED_ROW + b'["time"]\n'), Prose("is malformed: its line 2 is no JSON object")),
    Tag("ledger-no-hash"): (seeded_then(FIELDS, Ledgered(b', "sha256": "00"')),
                            Prose("is malformed: its line 2 has no `sha256`")),
    Tag("ledger-number"): (seeded_then(FIELDS, Ledgered(b'"label": "past"'), Ledgered(b'"label": 5')),
                           Prose("is malformed: its line 2 has a `label` that is no string")),
    Tag("ledger-seeded-word"): (seeded_then(Ledgered(FIELDS + b', "seeded": "yes"')),
                                Prose("is malformed: its line 2 has a `seeded` that is not `true`")),
    Tag("ledger-unpadded"): (seeded_then(FIELDS, Ledgered(b"11-05T"), Ledgered(b"11-5T")),
                             Prose("is malformed: its line 2 has a `time` `2026-11-5T00:00:00Z` not in the form "
                                   f"`{STAMP}`, UTC to the second")),
    Tag("ledger-extra"): (seeded_then(Ledgered(FIELDS + b', "extra\\nkey": "x"')),
                          Prose("is malformed: its line 2 holds `extra\\nkey`, where a row holds `time`, `briefs`, "
                                "`label`, `shape`, `sha256` and `seeded`")),
}
# The ledgers refused as BROKEN_LEDGERS's are, given alike, their cases numbered
# after the torn ledgers': a line nested past the decoder's stack, and a time
# holding a newline and a quote, spelled as JSON spells it, on one line.
LATER_LEDGERS: dict[Tag, tuple[Ledgered | None, Prose]] = {
    Tag("ledger-deep"): (Ledgered(SEED_ROW + DEEP.encode() + b"\n"),
                         Prose("is malformed: its line 2 is no JSON: RecursionError: ")),
    Tag("ledger-escaped-time"): (seeded_then(FIELDS, Ledgered(b"T00:00:00Z"), Ledgered(b'T00:00:00Z\\n\\"')),
                                 Prose('is malformed: its line 2 has a `time` `2026-11-05T00:00:00Z\\n\\"` not in the '
                                       f"form `{STAMP}`, UTC to the second")),
}


def brief(*lines: Prose, shape: Prose | None = SHAPED, size: Prose | None = SIZED, decisions: Prose | None = LOGGED,
          proof: Prose | None = PROVED, advisor: Prose | None = UNADVISED) -> BriefText:
    """A brief of ``lines``, then its SHAPE, SIZE, DECISIONS, PROOF and ADVISOR lines, each left out for None, then
    FACTS and RETURN."""
    carried = [line for line in (shape, size, decisions, proof, advisor) if line is not None]
    return BriefText("\n".join([*lines, *carried]) + "\n" + FACTS)


TABLE = {
    "good": brief(GOAL, Prose("ITEMS (2): the refusal, the pass-through")),
    "other": brief(Prose("GOAL: the guard lets a launch of one good brief through."), Prose("ITEMS (1): the pass")),
    "dotted": brief(Prose("GOAL: does next/x.py read python 3.14 and stop at the end?"), ONE),
    "bang": brief(Prose("GOAL: the hook refuses it!   "), ONE),
    "ten": brief(GOAL, Prose("ITEMS (10): a, b, c, d, e, f, g, h, i, j")),
    "model": brief(GOAL, MODEL_ITEMS),
    "unpaired": brief(GOAL, Prose("ITEMS (3): an agent( call found, its line named; a (paired; group) item; the last")),
    "numbers": brief(GOAL, Prose("ITEMS (2): 700,000 tokens, 2,000,000 tokens")),
    "zeros": brief(GOAL, Prose("ITEMS (03): a, b, c")),
    "no-goal": brief(ONE),
    "two-sentences": brief(Prose("GOAL: the hook refuses it. Then it stops."), ONE),
    "no-end": brief(Prose("GOAL: the hook refuses it"), ONE),
    "empty-goal": brief(Prose("GOAL: ."), ONE),
    "two-goals": brief(GOAL, Prose("GOAL: the guard also reads the script."), ONE),
    "indented-goal": brief(Prose("  GOAL: the hook refuses it."), ONE),
    "no-items": brief(GOAL),
    "zero-items": brief(GOAL, Prose("ITEMS (0): a")),
    "eleven": brief(GOAL, Prose("ITEMS (11): a, b, c, d, e, f, g, h, i, j, k")),
    "fewer": brief(GOAL, Prose("ITEMS (3): a, b")),
    "more": brief(GOAL, Prose("ITEMS (2): a, b, c")),
    "trailing": brief(GOAL, Prose("ITEMS (2): a, b,")),
    "listless": brief(GOAL, Prose("ITEMS (1): ")),
    "two-lists": brief(GOAL, ONE, Prose("ITEMS (1): b")),
    "uncounted": brief(GOAL, Prose("ITEMS: a, b")),
    "huge": brief(GOAL, Prose("ITEMS (99999999999): a")),
    "neither": brief(Prose("the hook refuses it, a, b")),
    "zeros-wide": brief(GOAL, Prose("ITEMS (0003): a, b, c")),
    "blank-item": brief(GOAL, Prose("ITEMS (3): a, , b")),
    "quoted-bracket": brief(GOAL, Prose("ITEMS (2): a `(` b, c)")),
    "closer": brief(GOAL, Prose("ITEMS (2): a), b")),
    "kinds": brief(GOAL, Prose("ITEMS (2): a [b, c] d, e {f, g} h")),
    "wide-count": brief(GOAL, Prose("ITEMS (" + "9" * WIDE + "): a")),
    "no-size": brief(GOAL, ONE, size=None),
    "two-sizes": brief(GOAL, ONE, Prose("SIZE: 90k from wf_1c0aef82-a34")),
    "size-zero": brief(GOAL, ONE, size=Prose("SIZE: 0k from wf_a")),
    "size-over": brief(GOAL, ONE, size=Prose("SIZE: 801k from wf_a")),
    "size-wide": brief(GOAL, ONE, size=Prose("SIZE: " + "9" * WIDE + "k from wf_a")),
    "no-source": brief(GOAL, ONE, size=Prose("SIZE: 82k")),
    "unitless": brief(GOAL, ONE, size=Prose("SIZE: 82 from wf_a")),
    "glossed": brief(GOAL, ONE, size=Prose("SIZE: 82k from wf_a, a read-only agent")),
    "relative-source": brief(GOAL, ONE, size=Prose("SIZE: 82k from wf_dc866405-e65/agent-1.jsonl")),
    "bare-run": brief(GOAL, ONE, size=Prose("SIZE: 82k from wf_")),
    "size-one": brief(GOAL, ONE, shape=Prose("SHAPE: light"), size=Prose("SIZE: 1k from wf_a")),
    "size-ceiling": brief(GOAL, ONE, size=Prose(f"SIZE: 800k from {MEASURED}")),
    "pilot": brief(GOAL, ONE, shape=Prose("SHAPE: never-run"), size=PILOT),
    "spaced": brief(GOAL, ONE, shape=Prose(SHAPED + " "), size=Prose(SIZED + "  "), decisions=Prose(LOGGED + " "),
                    proof=Prose(PROVED + " "), advisor=Prose(UNADVISED + "  ")),
    "at-count": brief(GOAL, ONE),
    "over-count": brief(GOAL, ONE, size=Prose("SIZE: 83k from wf_dc866405-e65")),
    "under-count": brief(GOAL, ONE, size=Prose("SIZE: 81k from wf_dc866405-e65")),
    "unknown-run": brief(GOAL, ONE, size=Prose("SIZE: 82k from wf_0f0f0f0f-404")),
    "quiet-run": brief(GOAL, ONE, size=Prose("SIZE: 82k from wf_quiet")),
    "moved-run": brief(GOAL, ONE, size=Prose(f"SIZE: 100k from {MOVED}")),
    "moved-under": brief(GOAL, ONE, size=Prose(f"SIZE: 99k from {MOVED}")),
    "path-under": brief(GOAL, ONE, size=Prose(f"SIZE: 81k from {MEASURED}")),
    "record-path": brief(GOAL, ONE, size=Prose(f"SIZE: 82k from {RECORD}")),
    "no-decisions": brief(GOAL, ONE, decisions=None),
    "two-decisions": brief(GOAL, ONE, LOGGED),
    "relative-decisions": brief(GOAL, ONE, decisions=Prose("DECISIONS: decisions.jsonl")),
    "json-decisions": brief(GOAL, ONE, decisions=Prose("DECISIONS: /x/decisions.json")),
    "no-proof": brief(GOAL, ONE, proof=None),
    "two-proofs": brief(GOAL, ONE, PROVED),
    "other-proof": brief(GOAL, ONE, proof=Prose("PROOF: the suite green on next/")),
    "glossed-proof": brief(GOAL, ONE, proof=Prose("PROOF: main thread, after this agent")),
    "no-advisor": brief(GOAL, ONE, advisor=None),
    "two-advisors": brief(GOAL, ONE, UNADVISED),
    "other-advisor": brief(GOAL, ONE, advisor=Prose("ADVISOR: none, unless stuck")),
    "long-advisor": brief(GOAL, ONE, advisor=Prose(f"ADVISOR: {LONG_ADVISOR}")),
    "no-shape": brief(GOAL, ONE, shape=None),
    "two-shapes": brief(GOAL, ONE, SHAPED),
    "upper-shape": brief(GOAL, ONE, shape=Prose("SHAPE: Guard_Code")),
    "empty-shape": brief(GOAL, ONE, shape=Prose("SHAPE: ")),
    "worded-shape": brief(GOAL, ONE, shape=Prose("SHAPE: guard code")),
    "cheap-cite": brief(GOAL, ONE, shape=Prose("SHAPE: dbc-heavy"), size=Prose("SIZE: 160k from wf_a")),
    "own-cheaper": brief(GOAL, ONE, shape=Prose("SHAPE: dbc-heavy"), size=Prose("SIZE: 250k from wf_heavy-1")),
    "heavy-at": brief(GOAL, ONE, shape=Prose("SHAPE: dbc-heavy"), size=Prose("SIZE: 300k")),
    "heavy-cited": brief(GOAL, ONE, shape=Prose("SHAPE: dbc-heavy"), size=Prose("SIZE: 300k from wf_heavy-2")),
    "heavy-under": brief(GOAL, ONE, shape=Prose("SHAPE: dbc-heavy"), size=Prose("SIZE: 299k")),
    "never-sized": brief(GOAL, ONE, shape=Prose("SHAPE: never-run")),
    "measured-pilot": brief(GOAL, ONE, size=PILOT),
    "quiet-sized": brief(GOAL, ONE, shape=Prose("SHAPE: quiet-kind"), size=Prose("SIZE: 5k")),
    "quiet-pilot": brief(GOAL, ONE, shape=Prose("SHAPE: quiet-kind"), size=PILOT),
    "moved-shape": brief(GOAL, ONE, shape=Prose("SHAPE: moved-kind"), size=Prose("SIZE: 100k")),
    "moved-shape-under": brief(GOAL, ONE, shape=Prose("SHAPE: moved-kind"), size=Prose("SIZE: 99k")),
    "skip-at": brief(GOAL, ONE, shape=Prose("SHAPE: skip-kind"), size=Prose("SIZE: 5k")),
    "skip-under": brief(GOAL, ONE, shape=Prose("SHAPE: skip-kind"), size=Prose("SIZE: 4k")),
    "blocks-under": brief(GOAL, ONE, shape=Prose("SHAPE: blocks-kind"), size=Prose("SIZE: 3k")),
    "relayed-under": brief(GOAL, ONE, shape=Prose("SHAPE: relayed-kind"), size=Prose("SIZE: 5k")),
    "one-line-under": brief(GOAL, ONE, shape=Prose("SHAPE: one-line-kind"), size=Prose("SIZE: 6k")),
    "elsewhere-under": brief(GOAL, ONE, shape=Prose("SHAPE: elsewhere-kind"), size=Prose("SIZE: 7k")),
    "preceded-under": brief(GOAL, ONE, shape=Prose("SHAPE: preceded-kind"), size=Prose("SIZE: 8k")),
    "mixed-under": brief(GOAL, ONE, shape=Prose("SHAPE: mixed-kind"), size=Prose("SIZE: 10k")),
    "edited-under": brief(GOAL, ONE, shape=Prose("SHAPE: edited-kind"), size=Prose("SIZE: 11k")),
    "unrowed-pilot": brief(GOAL, ONE, shape=Prose("SHAPE: unrowed-kind"), size=PILOT),
    "late-pilot": brief(GOAL, ONE, shape=Prose("SHAPE: late-kind"), size=PILOT),
    "relaunched-under": brief(GOAL, ONE, shape=Prose("SHAPE: relaunched-kind"), size=Prose("SIZE: 14k")),
    "first-at": brief(GOAL, ONE, shape=Prose("SHAPE: first-kind"), size=Prose("SIZE: 14k")),
    "twice-under": brief(GOAL, ONE, shape=Prose("SHAPE: twice-kind"), size=Prose("SIZE: 15k")),
    "reset-under": brief(GOAL, ONE, shape=Prose("SHAPE: reset-kind"), size=Prose("SIZE: 16k")),
    "digit-pilot": brief(GOAL, ONE, shape=Prose("SHAPE: probe-23"), size=PILOT),
    "unshaped-pilot": brief(GOAL, ONE, shape=None, size=PILOT),
    "registered": brief(GOAL, ONE, shape=Prose("SHAPE: light"), size=Prose("SIZE: 1k")),
    "lone-surrogate": brief(GOAL, Prose("ITEMS (1): a \ud800 half"), shape=Prose("SHAPE: light"), size=Prose("SIZE: 1k")),
    "unlisted-pilot": brief(GOAL, ONE, shape=Prose("SHAPE: unlisted-kind"), size=PILOT),
    "unlisted-sized": brief(GOAL, ONE, shape=Prose("SHAPE: unlisted-kind")),
    "retired": brief(GOAL, ONE, shape=Prose("SHAPE: retired-kind"), size=Prose("SIZE: 800k")),
    "overran-sized": brief(GOAL, ONE, shape=Prose("SHAPE: overran-kind"), size=Prose("SIZE: 800k")),
    "overran-pilot": brief(GOAL, ONE, shape=Prose("SHAPE: overran-kind"), size=PILOT),
    "at-ceiling": brief(GOAL, ONE, shape=Prose("SHAPE: at-ceiling-kind"), size=Prose("SIZE: 800k")),
    "number": 5,
}


class Case(NamedTuple):
    """A launch's input, the answer the rule wants, and every problem line the refusal prints."""

    id: CaseId
    what: Prose
    tool_input: InputText
    want: ExitStatus
    says: Prose


class Outcome(NamedTuple):
    """The hook's exit status and what it printed."""

    status: ExitStatus
    stdout: Prose
    stderr: Prose


def launch(*labels: Label, briefs: Path = BRIEFS, start: Start = "script") -> InputText:
    """A Workflow call's input started by ``start``, naming ``labels`` of ``briefs``."""
    begun = {"script": GOOD, "scriptPath": str(SCRIPT_FILE), "name": "x"}[start]
    return InputText(json.dumps({start: begun, "args": {"briefs": str(briefs), "labels": list(labels)}}))


def raw(args: ArgsText) -> InputText:
    """A Workflow call's input whose args is the JSON text ``args``."""
    return InputText('{"script": ' + json.dumps(GOOD) + ', "args": ' + args + "}")


def scripted(script: Prose) -> InputText:
    """A Workflow call's input running ``script`` inline, naming brief `good`."""
    return InputText(json.dumps({"script": script, "args": {"briefs": str(BRIEFS), "labels": ["good"]}}))


def started(start: Start, begun: Prose) -> InputText:
    """A Workflow call's input whose script ``start`` is ``begun``, naming brief `good`."""
    return InputText(json.dumps({start: begun, "args": {"briefs": str(BRIEFS), "labels": ["good"]}}))


def by_name(name: Prose, *labels: Label) -> InputText:
    """A Workflow call's input launching ``name`` alone, its args naming ``labels`` of BRIEFS."""
    return InputText(json.dumps({"name": name, "args": {"briefs": str(BRIEFS), "labels": list(labels)}}))


def token(label: Label, briefs: Path = BRIEFS) -> Prose:
    """The description token naming brief ``label`` of ``briefs``."""
    return Prose(f"briefs={briefs}#{label}")


def described(description: Prose | None, args: ArgsText | None = None) -> InputText:
    """A Workflow call's input launching RESEARCH alone with ``description`` and the JSON text ``args``, each where given."""
    fields = [f'"name": {json.dumps(RESEARCH)}']
    if description is not None:
        fields.append(f'"description": {json.dumps(description)}')
    if args is not None:
        fields.append(f'"args": {args}')
    return InputText("{" + ", ".join(fields) + "}")


def held(count: Tokens) -> Prose:
    """The refusal's problem for a built-in's launch whose description holds ``count`` tokens, not one."""
    return Prose(f"  * its description holds {count} tokens `briefs=...`, {WAY}\n")


def malformed(word: Prose) -> Prose:
    """The refusal's problem for a built-in's launch whose description's one token ``word`` is malformed."""
    return Prose(f"  * its description's token `{word}` names no absolute path and label, {WAY}\n")


def line_of(script: Prose, needle: Prose) -> LineNumber:
    """The 1-based line of ``script`` holding ``needle``, which it holds once."""
    assert script.count(needle) == 1, needle
    return LineNumber(script[: script.index(needle)].count("\n") + 1)


def hook(tool_input: InputText, tool: Prose = Prose("Workflow"), event: Prose = Prose("PreToolUse"),
         search: Path = BIN, cap: AddressSpace | None = None, guard: Path = KIT_GUARD, home: Path = HOME) -> Outcome:
    """Pipe one ``event`` event of ``tool`` with ``tool_input`` to ``guard``, HOME ``home``, PATH ``search``."""
    return piped(EventText(json.dumps({
        "hook_event_name": event, "tool_name": tool, "cwd": str(BASE),
        "tool_input": json.loads(tool_input), "session_id": "test",
    })), search=search, cap=cap, guard=guard, home=home)


def piped(text: EventText, path: Path | None = None, cap: AddressSpace | None = None, search: Path = BIN,
          guard: Path = KIT_GUARD, home: Path = HOME) -> Outcome:
    """Pipe ``text`` to ``guard`` as its event, held to SPENT, HOME ``home``, ``path`` first on its PYTHONPATH, PATH ``search``, and its address space ``cap`` where given."""
    env = {**os.environ, "HOME": str(home), "CLAUDE_PROJECT_DIR": str(PROJECT), "PATH": str(search)}
    if path is not None:
        env["PYTHONPATH"] = str(path)
    def capped() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (SPENT, SPENT))
        if cap is not None:
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

    done = subprocess.run([str(guard)], input=text, capture_output=True, text=True, check=False, env=env,
                          preexec_fn=capped)
    return Outcome(ExitStatus(done.returncode), Prose(done.stdout), Prose(done.stderr))


def refused(case_id: CaseId, what: Prose, tool_input: InputText, says: Prose) -> Case:
    """A launch the rule refuses, printing ``says``."""
    return Case(case_id, what, tool_input, REFUSE, says)


def allowed(case_id: CaseId, what: Prose, tool_input: InputText) -> Case:
    """A launch the rule lets through with no output."""
    return Case(case_id, what, tool_input, ALLOW, NOTHING)


def listed(problem: Prose) -> Prose:
    """The refusal's line for ``problem``; one ending `: ` stands for every line it starts, the interpreter's message
    following."""
    return Prose(f"  * {problem}\n")


def named(label: Label, problem: Prose) -> Prose:
    """The refusal's line for brief ``label`` breaking the rule as ``problem`` says."""
    return listed(Prose(f"brief `{label}`: {problem}"))


def nowhere(name: Prose) -> Prose:
    """The refusal's problem for a launch of ``name``, a workflow saved in neither folder."""
    return Prose(f"its saved workflow `{name}`, looked for at {HOME}/.claude/workflows/{name}.js and "
                 f"{PROJECT}/.claude/workflows/{name}.js, cannot be read: no such regular file")


def unlisted(name: Prose, version: Version = STANDARD_VERSION) -> Prose:
    """The refusal's line for a launch of ``name``, a built-in of Claude Code ``version`` that BUILT_IN lacks."""
    return Prose(f"  * {name} is a built-in workflow of Claude Code {version} that BUILT_IN does not list: "
                 "add it to BUILT_IN in workflow-launch-guard.py\n")


def cases() -> list[Case]:
    """Every launch case: a refusal names its brief and the rule, a pass prints nothing."""
    good, other = Label("good"), Label("other")
    no_args = listed(Prose("its args is no object naming `briefs` and `labels`"))
    no_briefs = listed(Prose("its args names no `briefs`, the absolute path of a prepared briefs file"))
    no_labels = listed(Prose("its args names no `labels`, a list of one or two brief labels"))
    no_goal = Prose("it has no line starting `GOAL: `")
    no_sentence = Prose("its GOAL is no sentence ending at a `.`, `!` or `?`")
    no_items = Prose("it has no line `ITEMS (<n>): <a>, <b>, ...`")
    return [
        # The event: a launch by any start, with or without a resumed run.
        allowed(CaseId("A01"), Prose("a script naming one brief that keeps the rule"), launch(good)),
        allowed(CaseId("A02"), Prose("a script path naming two briefs that keep the rule"),
                launch(good, other, start="scriptPath")),
        allowed(CaseId("A03"), Prose("a saved workflow by name, resuming a run"),
                InputText(json.dumps({"name": "x", "resumeFromRunId": "r1",
                                      "args": {"briefs": str(BRIEFS), "labels": [good]}}))),
        allowed(CaseId("A04"), Prose("args written as a JSON string holding the object"),
                raw(ArgsText(json.dumps(json.dumps({"briefs": str(BRIEFS), "labels": [good]}))))),
        # The args: an object naming an absolute briefs path and one or two labels.
        refused(CaseId("R01"), Prose("a launch with no args"), InputText(json.dumps({"script": GOOD})), no_args),
        refused(CaseId("R02"), Prose("args a list"), raw(ArgsText('["good"]')), no_args),
        refused(CaseId("R03"), Prose("args a string holding no JSON object"), raw(ArgsText('"briefs good"')), no_args),
        refused(CaseId("R04"), Prose("args naming no briefs file"), raw(ArgsText('{"labels": ["good"]}')), no_briefs),
        refused(CaseId("R05"), Prose("a relative briefs path"), launch(good, briefs=Path("briefs.json")), no_briefs),
        refused(CaseId("R06"), Prose("args naming no labels"), raw(ArgsText(json.dumps({"briefs": str(BRIEFS)}))),
                no_labels),
        refused(CaseId("R07"), Prose("labels a string"),
                raw(ArgsText(json.dumps({"briefs": str(BRIEFS), "labels": good}))), no_labels),
        refused(CaseId("R08"), Prose("a label that is no string"),
                raw(ArgsText(json.dumps({"briefs": str(BRIEFS), "labels": [good, 7]}))), no_labels),
        refused(CaseId("R09"), Prose("no label"), launch(),
                listed(Prose("it names 0 briefs, where a launch names 1 to 2"))),
        refused(CaseId("R10"), Prose("three labels"), launch(good, other, Label("ten")),
                listed(Prose("it names 3 briefs, where a launch names 1 to 2"))),
        # The briefs file: each label a key, the file a readable JSON object of strings.
        refused(CaseId("R11"), Prose("a label the file lacks"), launch(good, Label("nope")),
                named(Label("nope"), Prose(f"no such label in {BRIEFS}"))),
        refused(CaseId("R12"), Prose("a briefs file that does not exist"), launch(good, briefs=GONE),
                listed(Prose(f"its briefs file {GONE} cannot be read: no such regular file"))),
        refused(CaseId("R13"), Prose("a briefs file holding no JSON"), launch(good, briefs=NOT_JSON),
                listed(Prose(f"its briefs file {NOT_JSON} cannot be read: JSONDecodeError: "))),
        refused(CaseId("R14"), Prose("a briefs file holding a JSON list"), launch(good, briefs=A_LIST),
                listed(Prose(f"its briefs file {A_LIST} is not a JSON object of label to brief text"))),
        refused(CaseId("R15"), Prose("a brief that is no string"), launch(Label("number")),
                named(Label("number"), Prose(f"its text in {BRIEFS} is not a string"))),
        refused(CaseId("R16"), Prose("a briefs path that is a directory"), launch(good, briefs=BASE),
                listed(Prose(f"its briefs file {BASE} cannot be read: no such regular file"))),
        refused(CaseId("R33"), Prose("a long briefs path is named whole"), launch(good, briefs=LONG),
                listed(Prose(f"its briefs file {LONG} cannot be read: no such regular file"))),
        # The GOAL: one line, one sentence.
        refused(CaseId("R17"), Prose("a brief with no GOAL line"), launch(Label("no-goal")),
                named(Label("no-goal"), no_goal)),
        refused(CaseId("R18"), Prose("a GOAL of two sentences"), launch(Label("two-sentences")),
                named(Label("two-sentences"),
                      Prose("its GOAL holds more than one sentence: `Then it stops.` follows the first"))),
        refused(CaseId("R19"), Prose("a GOAL ending at no stop"), launch(Label("no-end")),
                named(Label("no-end"), no_sentence)),
        refused(CaseId("R20"), Prose("a GOAL of a stop alone"), launch(Label("empty-goal")),
                named(Label("empty-goal"), no_sentence)),
        refused(CaseId("R21"), Prose("two GOAL lines"), launch(Label("two-goals")),
                named(Label("two-goals"), Prose("it has 2 GOAL lines, where a brief has one goal"))),
        refused(CaseId("R22"), Prose("a GOAL line that does not start the line"), launch(Label("indented-goal")),
                named(Label("indented-goal"), no_goal)),
        allowed(CaseId("A05"), Prose("a GOAL whose dots are inside a path and a number, ending at `?`"),
                launch(Label("dotted"))),
        allowed(CaseId("A06"), Prose("a GOAL ending at `!` and trailing spaces"), launch(Label("bang"))),
        # The ITEMS: one line `ITEMS (<n>): ...`, n from 1 to 10, exactly n items.
        refused(CaseId("R23"), Prose("a brief with no ITEMS line"), launch(Label("no-items")),
                named(Label("no-items"), no_items)),
        refused(CaseId("R24"), Prose("zero items declared"), launch(Label("zero-items")),
                named(Label("zero-items"), Prose("its ITEMS line declares 0 items, where a brief holds 1 to 10"))),
        refused(CaseId("R25"), Prose("eleven items declared"), launch(Label("eleven")),
                named(Label("eleven"), Prose("its ITEMS line declares 11 items, where a brief holds 1 to 10"))),
        refused(CaseId("R26"), Prose("fewer items listed than declared"), launch(Label("fewer")),
                named(Label("fewer"), Prose("its ITEMS line declares 3 items and lists 2"))),
        refused(CaseId("R27"), Prose("more items listed than declared"), launch(Label("more")),
                named(Label("more"), Prose("its ITEMS line declares 2 items and lists 3"))),
        refused(CaseId("R28"), Prose("a trailing separator"), launch(Label("trailing")),
                named(Label("trailing"), Prose("its ITEMS list holds an empty item"))),
        refused(CaseId("R29"), Prose("an ITEMS line listing nothing"), launch(Label("listless")),
                named(Label("listless"), Prose("its ITEMS line lists no item"))),
        refused(CaseId("R30"), Prose("two ITEMS lines"), launch(Label("two-lists")),
                named(Label("two-lists"), Prose("it has 2 ITEMS lines, where a brief has one list"))),
        refused(CaseId("R31"), Prose("an ITEMS line with no count"), launch(Label("uncounted")),
                named(Label("uncounted"), no_items)),
        refused(CaseId("R32"), Prose("a count of eleven digits"), launch(Label("huge")),
                named(Label("huge"), Prose("its ITEMS line declares 99999999999 items, where a brief holds 1 to 10"))),
        allowed(CaseId("A07"), Prose("ten items"), launch(Label("ten"))),
        allowed(CaseId("A08"), Prose("the ruled model's list: `; ` between items holding commas, a `;` in backticks"),
                launch(Label("model"))),
        allowed(CaseId("A09"), Prose("an unpaired `(` pairs nothing, a paired one hides its `;`"),
                launch(Label("unpaired"))),
        allowed(CaseId("A10"), Prose("commas inside numbers separate nothing"), launch(Label("numbers"))),
        allowed(CaseId("A11"), Prose("a count written with leading zeros"), launch(Label("zeros"))),
        allowed(CaseId("A12"), Prose("a count of four digits, three of them leading zeros"),
                launch(Label("zeros-wide"))),
        refused(CaseId("R34"), Prose("an item of blanks between two separators"), launch(Label("blank-item")),
                named(Label("blank-item"), Prose("its ITEMS list holds an empty item"))),
        allowed(CaseId("A13"), Prose("a bracket inside backticks pairs with nothing outside them"),
                launch(Label("quoted-bracket"))),
        allowed(CaseId("A14"), Prose("a closing bracket with no partner pairs nothing"), launch(Label("closer"))),
        allowed(CaseId("A15"), Prose("square brackets and braces hide their separators"), launch(Label("kinds"))),
        refused(CaseId("R35"), Prose("a count of more digits than int() converts is shown cut"),
                launch(Label("wide-count")), named(Label("wide-count"), Prose(
                    "its ITEMS line declares " + "9" * 77 + "... items, where a brief holds 1 to 10"))),
        refused(CaseId("R36"), Prose("a label of 80 characters is named whole"), launch(Label("x" * 80)),
                named(Label("x" * 80), Prose(f"no such label in {BRIEFS}"))),
        refused(CaseId("R37"), Prose("a label of 81 characters is cut to 77 and an ellipsis"),
                launch(Label("y" * 81)), named(Label("y" * 77 + "..."), Prose(f"no such label in {BRIEFS}"))),
        refused(CaseId("R38"), Prose("a briefs file nested past the decoder's stack"), launch(good, briefs=NESTED),
                listed(Prose(f"its briefs file {NESTED} cannot be read: RecursionError: "))),
        refused(CaseId("R39"), Prose("a briefs file that is a regular file and cannot be read"),
                launch(good, briefs=Path("/proc/self/mem")),
                listed(Prose("its briefs file /proc/self/mem cannot be read: OSError: [Errno 5] Input/output error"))),
        refused(CaseId("R40"), Prose("args a string nested past the decoder's stack"),
                raw(ArgsText(json.dumps(DEEP))), no_args),
        refused(CaseId("R41"), Prose("a launch naming no script"),
                InputText(json.dumps({"args": {"briefs": str(BRIEFS), "labels": [good]}})),
                listed(Prose("it names no script: none of `script`, `scriptPath` and `name`"))),
        *line_cases(),
        *script_cases(),
        *built_in_cases(),
    ]


def below(thousands: Thousands, largest: Spent, transcript: Path) -> Prose:
    """The problem of a SIZE of ``thousands``k under ``largest``, the count of the agent whose transcript is ``transcript``."""
    return Prose(f"its SIZE line states {thousands}k, below the {largest:,} new tokens {transcript} spent, as "
                 "workflow-token-watch counts them")


def topped(thousands: Thousands, largest: Spent, transcript: Path, shape: Prose) -> Prose:
    """The problem of a SIZE of ``thousands``k under ``largest``, the most an agent of SHAPE ``shape`` spent, in
    ``transcript``."""
    return Prose(f"its SIZE line states {thousands}k, below the {largest:,} new tokens {transcript} spent on a brief of "
                 f"SHAPE `{shape}`, the most of any past agent of that SHAPE as workflow-token-watch counts them")


def faulted(case_id: CaseId, label: Label, what: Prose, problem: Prose) -> Case:
    """A launch of brief ``label`` alone, refused naming it and ``problem``."""
    return refused(case_id, what, launch(label), named(label, problem))


def line_cases() -> list[Case]:
    """The SIZE, DECISIONS, PROOF and ADVISOR lines: one each, a measured size or a lone pilot, a .jsonl path, the
    main thread, none."""
    states = Prose("where an agent spends 1k to 800k, the per-agent ceiling")
    no_run = Prose("is neither a workflow run `wf_<id>` nor the absolute path of a run directory")
    no_path = Prose("no absolute path ending `.jsonl`")
    unmeasured = Prose("its SIZE line's source names no measured run: ")

    def under(label: Label) -> Prose:
        """The refusal's lines for brief ``label``, whose 81k is below LARGEST, its source's largest agent and its
        SHAPE's."""
        return Prose(named(label, below(Thousands(81), Spent(82_000), LARGEST))
                     + named(label, topped(Thousands(81), Spent(82_000), LARGEST, Prose("guard-code"))))

    return [
        faulted(CaseId("R43"), Label("no-size"), Prose("a brief with no SIZE line"),
                Prose("it has no line `SIZE: <n>k [from <source>]` or `SIZE: PILOT`")),
        faulted(CaseId("R44"), Label("two-sizes"), Prose("two SIZE lines"),
                Prose("it has 2 SIZE lines, where a brief has one size")),
        faulted(CaseId("R45"), Label("size-zero"), Prose("a size of 0k"), Prose(f"its SIZE line states 0k, {states}")),
        faulted(CaseId("R46"), Label("size-over"), Prose("a size past the per-agent ceiling"),
                Prose(f"its SIZE line states 801k, {states}")),
        faulted(CaseId("R47"), Label("size-wide"), Prose("a size of more digits than int() converts is shown cut"),
                Prose(f"its SIZE line states {'9' * 77}...k, {states}")),
        allowed(CaseId("A25"), Prose("a size naming no source, at its SHAPE's largest agent"), launch(Label("no-source"))),
        faulted(CaseId("R49"), Label("unitless"), Prose("a size with no `k`"),
                Prose(f"its SIZE line reads `82 from wf_a`, neither {SIZE_FORMS}")),
        faulted(CaseId("R50"), Label("glossed"), Prose("words after the source"),
                Prose(f"its SIZE line reads `82k from wf_a, a read-only agent`, neither {SIZE_FORMS}")),
        faulted(CaseId("R51"), Label("relative-source"), Prose("a relative record path, a run id at its head"),
                Prose(f"its SIZE line's source `wf_dc866405-e65/agent-1.jsonl` {no_run}")),
        faulted(CaseId("R52"), Label("bare-run"), Prose("a run id with nothing after `wf_`"),
                Prose(f"its SIZE line's source `wf_` {no_run}")),
        allowed(CaseId("A18"), Prose("a size of 1k from a workflow run whose one agent spent 1,000"),
                launch(Label("size-one"))),
        allowed(CaseId("A19"), Prose("a size of 800k, the ceiling, from the absolute path of a run directory"),
                launch(Label("size-ceiling"))),
        allowed(CaseId("A22"), Prose("a size at the run's largest agent's count, as the watch counts it"),
                launch(Label("at-count"))),
        allowed(CaseId("A23"), Prose("a size above the run's largest agent's count"), launch(Label("over-count"))),
        refused(CaseId("R62"), Prose("a size below the run's largest agent's count, its other agents under it"),
                launch(Label("under-count")), under(Label("under-count"))),
        refused(CaseId("R63"), Prose("a run id whose directory exists in no session"), launch(Label("unknown-run")),
                named(Label("unknown-run"), Prose(f"{unmeasured}no directory {PROJECTS}/*/*/subagents/workflows/"
                                                  "wf_0f0f0f0f-404"))),
        refused(CaseId("R64"), Prose("a run whose transcripts record no call"), launch(Label("quiet-run")),
                named(Label("quiet-run"), Prose(f"{unmeasured}no agent-*.jsonl in {SESSION / 'wf_quiet'} records "
                                                "a call"))),
        allowed(CaseId("A24"), Prose("a run in two session folders, its agent's calls counted together, once each"),
                launch(Label("moved-run"))),
        refused(CaseId("R65"), Prose("a size below an agent's calls over two session folders"),
                launch(Label("moved-under")),
                named(Label("moved-under"), below(Thousands(99), Spent(100_000), SESSION / MOVED / "agent-c.jsonl"))),
        refused(CaseId("R66"), Prose("a size below the count of the run directory a path names"),
                launch(Label("path-under")), under(Label("path-under"))),
        refused(CaseId("R67"), Prose("a path naming a transcript's file, no run directory"), launch(Label("record-path")),
                named(Label("record-path"), Prose(f"{unmeasured}no agent-*.jsonl in {RECORD} records a call"))),
        allowed(CaseId("A20"), Prose("a PILOT, the one brief its launch names"), launch(Label("pilot"))),
        refused(CaseId("R53"), Prose("a PILOT beside a sized brief"), launch(Label("good"), Label("pilot")),
                named(Label("pilot"), Prose("its SIZE line reads `PILOT`, where a pilot is the one brief its launch "
                                            "names"))),
        allowed(CaseId("A21"), Prose("SIZE, DECISIONS, PROOF and ADVISOR lines ending in blanks"),
                launch(Label("spaced"))),
        faulted(CaseId("R54"), Label("no-decisions"), Prose("a brief with no DECISIONS line"),
                Prose("it has no line `DECISIONS: <absolute path>.jsonl`, the file its agent appends each decision "
                      "to as it takes it")),
        faulted(CaseId("R55"), Label("two-decisions"), Prose("two DECISIONS lines"),
                Prose("it has 2 DECISIONS lines, where a brief has one decisions file")),
        faulted(CaseId("R56"), Label("relative-decisions"), Prose("a relative DECISIONS path"),
                Prose(f"its DECISIONS line names `decisions.jsonl`, {no_path}")),
        faulted(CaseId("R57"), Label("json-decisions"), Prose("a DECISIONS path not ending .jsonl"),
                Prose(f"its DECISIONS line names `/x/decisions.json`, {no_path}")),
        faulted(CaseId("R58"), Label("no-proof"), Prose("a brief with no PROOF line"),
                Prose("it has no line `PROOF: main thread`, leaving every suite, gate and untouched lint to the "
                      "main thread")),
        faulted(CaseId("R59"), Label("two-proofs"), Prose("two PROOF lines"),
                Prose("it has 2 PROOF lines, where a brief has one proof")),
        faulted(CaseId("R60"), Label("other-proof"), Prose("a PROOF line naming another proof"),
                Prose("its PROOF line reads `the suite green on next/`, where it reads `main thread`")),
        faulted(CaseId("R61"), Label("glossed-proof"), Prose("a PROOF line glossing the main thread"),
                Prose("its PROOF line reads `main thread, after this agent`, where it reads `main thread`")),
        faulted(CaseId("R94"), Label("no-advisor"), Prose("a brief with no ADVISOR line"),
                Prose(f"it has no line `{UNADVISED}`, {NEVER}")),
        faulted(CaseId("R95"), Label("two-advisors"), Prose("two ADVISOR lines"),
                Prose(f"it has 2 ADVISOR lines, where a brief has the one line `{UNADVISED}`, {NEVER}")),
        faulted(CaseId("R96"), Label("other-advisor"), Prose("an ADVISOR line glossing none"),
                Prose(f"its ADVISOR line reads `none, unless stuck`, where it reads `none`, {NEVER}")),
        faulted(CaseId("R97"), Label("long-advisor"), Prose("an ADVISOR line past what a problem line shows, cut"),
                Prose(f"its ADVISOR line reads `{LONG_ADVISOR[: SHOWN - 3]}...`, where it reads `none`, {NEVER}")),
        *shape_cases(),
    ]


# The problem of a brief with no SHAPE line.
NO_SHAPE = Prose("it has no line `SHAPE: <name>`, the kind of work its agent does in lower-case letters, digits and "
                 "hyphens")


def unshaped(shape: Prose) -> Prose:
    """The problem of a SHAPE line whose name ``shape`` is no name of lower-case letters, digits and hyphens."""
    return Prose(f"its SHAPE line reads `{shape}`, no name of lower-case letters, digits and hyphens")


def unmeasured_shape(thousands: Thousands, shape: Prose) -> Prose:
    """The problem of a SIZE of ``thousands``k for SHAPE ``shape``, which no past run measured."""
    return Prose(f"its SIZE line states {thousands}k, where no past run measured an agent whose brief carries SHAPE "
                 f"`{shape}`: a SHAPE never measured runs as `SIZE: PILOT`, the one brief its launch names")


def shape_cases() -> list[Case]:
    """The SHAPE line, and a SIZE held to the largest agent of every past run whose brief carried its SHAPE."""
    heavy = Prose("dbc-heavy")
    return [
        faulted(CaseId("R68"), Label("no-shape"), Prose("a brief with no SHAPE line"), NO_SHAPE),
        faulted(CaseId("R69"), Label("two-shapes"), Prose("two SHAPE lines"),
                Prose("it has 2 SHAPE lines, where a brief has one shape")),
        faulted(CaseId("R70"), Label("upper-shape"), Prose("a SHAPE of capitals and an underscore"),
                unshaped(Prose("Guard_Code"))),
        faulted(CaseId("R71"), Label("empty-shape"), Prose("a SHAPE line naming nothing"), unshaped(Prose(""))),
        faulted(CaseId("R72"), Label("worded-shape"), Prose("a SHAPE of two words"), unshaped(Prose("guard code"))),
        faulted(CaseId("R73"), Label("cheap-cite"), Prose("a size below its SHAPE's largest agent, citing a cheaper "
                                                          "run of another SHAPE that it covers"),
                topped(Thousands(160), Spent(300_000), HEAVY, heavy)),
        faulted(CaseId("R74"), Label("own-cheaper"), Prose("a size below its SHAPE's largest agent, citing a cheaper "
                                                           "run of its own SHAPE"),
                topped(Thousands(250), Spent(300_000), HEAVY, heavy)),
        allowed(CaseId("A26"), Prose("a size at its SHAPE's largest agent over three runs, naming no source, an agent "
                                     "of another SHAPE in one of them spending more"), launch(Label("heavy-at"))),
        allowed(CaseId("A27"), Prose("a size at its SHAPE's largest agent, citing that agent's run"),
                launch(Label("heavy-cited"))),
        faulted(CaseId("R75"), Label("heavy-under"), Prose("a size naming no source below its SHAPE's largest agent"),
                topped(Thousands(299), Spent(300_000), HEAVY, heavy)),
        faulted(CaseId("R76"), Label("never-sized"), Prose("a sized brief of a SHAPE no past run carries, its source "
                                                           "a measured run"),
                unmeasured_shape(Thousands(82), Prose("never-run"))),
        faulted(CaseId("R77"), Label("measured-pilot"), Prose("a lone PILOT of a SHAPE a past run measured"),
                Prose(f"its SIZE line reads `PILOT`, where SHAPE `guard-code` was measured: its SIZE states at least "
                      f"the 82,000 new tokens {LARGEST} spent, the most of any past agent of that SHAPE as "
                      "workflow-token-watch counts them")),
        faulted(CaseId("R78"), Label("quiet-sized"), Prose("a sized brief of a SHAPE whose one past agent recorded no "
                                                           "call"), unmeasured_shape(Thousands(5), Prose("quiet-kind"))),
        allowed(CaseId("A28"), Prose("a lone PILOT of a SHAPE whose one past agent recorded no call"),
                launch(Label("quiet-pilot"))),
        allowed(CaseId("A31"), Prose("a lone PILOT of a SHAPE holding digits"), launch(Label("digit-pilot"))),
        allowed(CaseId("A29"), Prose("a size at its SHAPE's agent's calls over two session folders, once each"),
                launch(Label("moved-shape"))),
        faulted(CaseId("R79"), Label("moved-shape-under"), Prose("a size below its SHAPE's agent's calls over two "
                                                                 "session folders, its first copy named"),
                topped(Thousands(99), Spent(100_000), MOVED_AGENT, Prose("moved-kind"))),
        allowed(CaseId("A30"), Prose("agents naming their SHAPE's brief by a relative path a seed row names, or only "
                                     "after a first call, "
                                     "first timed with no offset, by a word or by a number, and a transcript's name "
                                     "on a folder, are skipped"), launch(Label("skip-at"))),
        faulted(CaseId("R80"), Label("skip-under"), Prose("a size below the one agent of its SHAPE a ledger row binds"),
                topped(Thousands(4), Spent(5000), SKIP_SMALL, Prose("skip-kind"))),
        faulted(CaseId("R81"), Label("blocks-under"), Prose("a size below an agent prompted in a list of text blocks"),
                topped(Thousands(3), Spent(4000), BLOCKS, Prose("blocks-kind"))),
        faulted(CaseId("R82"), Label("relayed-under"), Prose("a size below an agent whose prompt follows a relayed "
                                                             "request quoting another prompt"),
                topped(Thousands(5), Spent(6000), RELAYED, Prose("relayed-kind"))),
        faulted(CaseId("R83"), Label("one-line-under"), Prose("a size below an agent whose prompt follows, on one line, "
                                                              "a relayed request quoting another prompt"),
                topped(Thousands(6), Spent(7000), ONE_LINE, Prose("one-line-kind"))),
        faulted(CaseId("R84"), Label("elsewhere-under"), Prose("a size below an agent whose prompt follows a relayed "
                                                               "request naming another briefs file"),
                topped(Thousands(7), Spent(8000), ELSEWHERE, Prose("elsewhere-kind"))),
        faulted(CaseId("R85"), Label("preceded-under"), Prose("a size below an agent whose first timestamp and prompt "
                                                              "follow lines of no JSON, no object, no message nor "
                                                              "timestamp, and nesting past the decoder's stack"),
                topped(Thousands(8), Spent(9000), PRECEDED, Prose("preceded-kind"))),
        faulted(CaseId("R86"), Label("mixed-under"), Prose("a size below an agent whose prompt's text block sits "
                                                           "between a text block of no string and a block of another "
                                                           "type carrying another prompt"),
                topped(Thousands(10), Spent(11_000), MIXED, Prose("mixed-kind"))),
        faulted(CaseId("R92"), Label("edited-under"), Prose("a size below an agent of the SHAPE its brief's ledger row "
                                                            "records, its briefs file naming another since, as a later "
                                                            "row of its label in another briefs file does"),
                topped(Thousands(11), Spent(12_000), EDITED, Prose("edited-kind"))),
        allowed(CaseId("A34"), Prose("a lone PILOT of a SHAPE whose one agent's brief no ledger row names"),
                launch(Label("unrowed-pilot"))),
        allowed(CaseId("A35"), Prose("a lone PILOT of a SHAPE whose one agent's only ledger row is timed after it "
                                     "starts, before a later line of it"), launch(Label("late-pilot"))),
        faulted(CaseId("R93"), Label("relaunched-under"), Prose("a size below an agent started in the second of its "
                                                                "brief's later ledger row, which names its SHAPE"),
                topped(Thousands(14), Spent(15_000), RELAUNCHED, Prose("relaunched-kind"))),
        allowed(CaseId("A36"), Prose("a size at an agent started before its brief's later ledger row, the agent started "
                                     "after it spending more"), launch(Label("first-at"))),
        faulted(CaseId("R98"), Label("twice-under"), Prose("a size below an agent of the SHAPE of the later of its "
                                                           "brief's two ledger rows timed in one second"),
                topped(Thousands(15), Spent(16_000), TWICE, Prose("twice-kind"))),
        faulted(CaseId("R99"), Label("reset-under"), Prose("a size below an agent of the SHAPE of its brief's "
                                                           "later-timed ledger row, which a line timed before it "
                                                           "follows"),
                topped(Thousands(16), Spent(17_000), RESET, Prose("reset-kind"))),
        *registry_cases(),
    ]


def overran(shape: Prose, spent_most: Spent, transcript: Path) -> Prose:
    """The problem of SHAPE ``shape``, whose largest past agent, in ``transcript``, spent ``spent_most``, past the
    per-agent ceiling."""
    return Prose(f"its SHAPE `{shape}` overran one agent: {transcript} spent {spent_most:,} new tokens, past the "
                 "800,000 per-agent ceiling, as workflow-token-watch counts them; its work must be split into narrower "
                 f"SHAPEs of the registry {LISTED} by the user's ruling")


def registry_cases() -> list[Case]:
    """A brief's SHAPE held to the kit's SHAPE registry: a row, not retired, its largest agent within the ceiling."""
    unlisted = Prose(f"its SHAPE `unlisted-kind` is no row of the SHAPE registry {LISTED}: a new SHAPE is added to it "
                     "only by the user's ruling")
    return [
        allowed(CaseId("A32"), Prose("a SHAPE that is a row of the registry, not retired, within the ceiling"),
                launch(Label("registered"))),
        faulted(CaseId("R87"), Label("unlisted-pilot"), Prose("a PILOT of a SHAPE no row of the registry names"),
                unlisted),
        faulted(CaseId("R88"), Label("unlisted-sized"), Prose("a sized brief of a SHAPE no row names, its SIZE not "
                                                              "measured against that SHAPE"), unlisted),
        faulted(CaseId("R89"), Label("retired"), Prose("a SHAPE whose row is retired, its reason quoted, its agent past "
                                                       "the ceiling and its SIZE not measured"),
                Prose(f"its SHAPE `retired-kind` is retired in the SHAPE registry {LISTED}: {RETIRED}")),
        faulted(CaseId("R90"), Label("overran-sized"), Prose("a sized brief of a SHAPE whose largest agent passed the "
                                                             "ceiling, unmarked"),
                overran(Prose("overran-kind"), Spent(800_001), OVERRAN)),
        faulted(CaseId("R91"), Label("overran-pilot"), Prose("a PILOT of a SHAPE whose largest agent passed the "
                                                             "ceiling, unmarked"),
                overran(Prose("overran-kind"), Spent(800_001), OVERRAN)),
        allowed(CaseId("A33"), Prose("a size of 800k for a SHAPE whose largest agent spent the ceiling, not past it"),
                launch(Label("at-ceiling"))),
    ]


def drawn_outside(script: Prose, needle: Prose) -> Prose:
    """The refusal's line for the agent call of ``script`` at ``needle``, run outside every loop over labels."""
    return Prose(f"  * its script calls agent() at line {line_of(script, needle)} outside every loop or callback "
                 "over `args.labels` whose variable its prompt, the first argument, reads\n")


def script_cases() -> list[Case]:
    """The script's cases: every agent runs per label, prompted by it, and no nested workflow runs."""
    head = Prose("export const meta = {name: 'x'}\n")
    stray = Prose(GOOD + "await agent('review the whole tree')\n")
    hidden = Prose(head + "// await agent(`brief ${label}`)\nconst note = \"agent(label)\"\n"
                   "const shown = `agent(${note})`\nfor (const label of args.labels) { log(label) }\n")
    quiet = Prose(GOOD + "// agent('a comment')\nconst said = \"agent('a string')\"\n")
    other_list = Prose(head + "for (const label of args.others) {\n  await agent(`brief ${label}`)\n}\n")
    indices = Prose(head + "for (const label in args.labels) {\n  await agent(`brief ${label}`)\n}\n")
    fixed = Prose(head + "for (const label of args.labels) {\n  await agent('the same prompt')\n}\n")
    options = Prose(head + "for (const label of args.labels) {\n  const prompt = 'the same prompt'\n"
                    "  await agent(prompt, { label })\n}\n")
    nested = Prose(GOOD + "await workflow('another', {})\n")
    unclosed = Prose(head + "for (const label of args.labels { await agent(label) }\n")
    broken = Prose(GOOD + "const = 5\n")
    other_map = Prose(head + "await parallel([1, 2].map((label) => agent(`brief ${label}`)))\n")
    other_runner = Prose(head + "await pipeline(args.others, async (label) => agent(`brief ${label}`))\n")
    second = Prose(head + "args.labels.map(render, (label) => agent(`brief ${label}`))\n")
    defaulted = Prose(head + "args.labels.map((label, run = agent(`brief ${label}`)) => run)\n")
    empty = Prose(head + "for (const label of args.labels) { await agent() }\n")
    unknown = Prose(head + "await other(args.labels, (label) => agent(`brief ${label}`))\n")
    two_errors = Prose(head + "const = 5\n" + GOOD + "const = 6\n")
    cycle = Prose(head + "for (const label of args.labels) {\n  const a = b\n  const b = a\n  await agent(a)\n}\n")
    assigned = Prose(head + "for (const label of args.labels) {\n  let prompt\n  prompt = `brief ${label}`\n"
                     "  await agent(prompt)\n}\n")
    return [
        allowed(CaseId("J01"), Prose("the ruled model script: a const prompt built from the loop's label"),
                scripted(MODEL_SCRIPT)),
        refused(CaseId("J02"), Prose("an inline prompt outside the loop, its line named"), scripted(stray),
                drawn_outside(stray, Prose("review the whole tree"))),
        refused(CaseId("J03"), Prose("agent( only in a comment, a string and a template calls no agent"),
                scripted(hidden), Prose("  * its script calls no agent()\n")),
        allowed(CaseId("J04"), Prose("agent( in a comment and a string outside the loop is no call"),
                scripted(quiet)),
        refused(CaseId("J05"), Prose("a loop over another list"), scripted(other_list),
                drawn_outside(other_list, Prose("await agent("))),
        refused(CaseId("J06"), Prose("a for-in loop over the labels' indices"), scripted(indices),
                drawn_outside(indices, Prose("await agent("))),
        refused(CaseId("J07"), Prose("the loop's variable missing from the prompt"), scripted(fixed),
                drawn_outside(fixed, Prose("await agent("))),
        refused(CaseId("J08"), Prose("a prompt variable followed to a value without the label, the label in the "
                                     "options"), scripted(options), drawn_outside(options, Prose("await agent("))),
        refused(CaseId("J09"), Prose("a nested workflow, its line named"), scripted(nested),
                Prose(f"  * its script calls workflow() at line {line_of(nested, Prose('workflow('))}, a nested "
                      "workflow whose script the hook does not read\n")),
        refused(CaseId("J10"), Prose("a saved workflow name resolving nowhere, named"),
                started("name", Prose("nowhere")), listed(nowhere(Prose("nowhere")))),
        allowed(CaseId("J11"), Prose("a saved workflow found under CLAUDE_PROJECT_DIR"),
                started("name", Prose("in-project"))),
        refused(CaseId("J12"), Prose("a script file that does not exist"),
                started("scriptPath", Prose(str(BASE / "gone.js"))),
                listed(Prose(f"its script file {BASE / 'gone.js'} cannot be read: no such regular file"))),
        refused(CaseId("J13"), Prose("a script with no agent call"), scripted(head),
                Prose("  * its script calls no agent()\n")),
        refused(CaseId("J14"), Prose("a script missing a `)`, its line named"), scripted(unclosed),
                Prose(f"  * its script does not parse: a missing `)` at line {line_of(unclosed, Prose('for ('))}\n")),
        refused(CaseId("J15"), Prose("a script holding a syntax error, its line named"), scripted(broken),
                Prose(f"  * its script does not parse: a syntax error at line {line_of(broken, Prose('const ='))}\n")),
        allowed(CaseId("J16"), Prose("parallel over args.labels.map, the agent in a thunk of the map's callback"),
                scripted(Prose(head + "await parallel(args.labels.map((label) => () => agent(`brief ${label}`)))\n"))),
        allowed(CaseId("J17"), Prose("args.labels.forEach with a function expression"),
                scripted(Prose(head + "args.labels.forEach(function (label) { agent(`brief ${label}`) })\n"))),
        allowed(CaseId("J18"), Prose("args.labels.flatMap with a bare parameter"),
                scripted(Prose(head + "await Promise.all(args.labels.flatMap(label => [agent(label)]))\n"))),
        allowed(CaseId("J19"), Prose("pipeline over args.labels, the callback's first parameter in the prompt"),
                scripted(Prose(head + "await pipeline(args.labels, async (label, at) => agent(`${at} ${label}`))\n"))),
        allowed(CaseId("J20"), Prose("parallel over args.labels.map(...), a later callback reading its item"),
                scripted(Prose(head + "await parallel(args.labels.map((l) => ({ l })), (item) => agent(item.l))\n"))),
        allowed(CaseId("J21"), Prose("an agent in a callback over another list inside the loop over labels"),
                scripted(Prose(head + "for (const label of args.labels) {\n"
                               "  await parallel([1, 2].map((n) => agent(`${label} ${n}`)))\n}\n"))),
        allowed(CaseId("J22"), Prose("a shorthand property reads the label"),
                scripted(Prose(head + "for (const label of args.labels) { await agent(render({ label })) }\n"))),
        refused(CaseId("J23"), Prose("a map callback over another list"), scripted(other_map),
                drawn_outside(other_map, Prose("agent("))),
        refused(CaseId("J24"), Prose("pipeline over another list"), scripted(other_runner),
                drawn_outside(other_runner, Prose("agent("))),
        refused(CaseId("J25"), Prose("a function passed second to args.labels.map, its thisArg"), scripted(second),
                drawn_outside(second, Prose("agent("))),
        refused(CaseId("J26"), Prose("an agent in a callback's default parameter, outside its body"),
                scripted(defaulted), drawn_outside(defaulted, Prose("agent("))),
        allowed(CaseId("J27"), Prose("a tagged template prompt reading the label"),
                scripted(Prose(head + "for (const label of args.labels) { await agent`brief ${label}` }\n"))),
        allowed(CaseId("J28"), Prose("a comment before the prompt is no argument"),
                scripted(Prose(head + "for (const label of args.labels) { await agent(/* brief */ `${label}`) }\n"))),
        refused(CaseId("J29"), Prose("an agent call with no argument"), scripted(empty),
                drawn_outside(empty, Prose("agent("))),
        allowed(CaseId("J30"), Prose("a relative script path, read from the event's cwd"),
                started("scriptPath", Prose(SCRIPT_FILE.name))),
        refused(CaseId("J31"), Prose("a script file that is a regular file and cannot be read"),
                started("scriptPath", Prose("/proc/self/mem")),
                listed(Prose("its script file /proc/self/mem cannot be read: OSError: [Errno 5] Input/output error"))),
        refused(CaseId("J32"), Prose("a call other than pipeline or parallel over args.labels"), scripted(unknown),
                drawn_outside(unknown, Prose("agent("))),
        refused(CaseId("J33"), Prose("of two syntax errors, the first in the source is named"), scripted(two_errors),
                Prose(f"  * its script does not parse: a syntax error at line {line_of(two_errors, Prose('const = 5'))}\n")),
        refused(CaseId("J34"), Prose("a variable declared with no value and assigned after is not followed"),
                scripted(assigned), drawn_outside(assigned, Prose("await agent("))),
        refused(CaseId("J35"), Prose("declarations reading each other in a cycle are followed once each"),
                scripted(cycle), drawn_outside(cycle, Prose("await agent("))),
    ]


def built_in_cases() -> list[Case]:
    """A built-in launched by name names its brief in its description; a name found nowhere reads the fake binary."""
    good, other = Label("good"), Label("other")
    question = ArgsText(json.dumps("what does the guard read?"))
    one = {"briefs": str(BRIEFS), "labels": [good]}
    as_args = ArgsText(json.dumps(one))  # a brief named the way a launch of a script names it
    bad_script = Prose("  * its script calls no agent()\n")
    return [
        allowed(CaseId("B01"), Prose("a built-in by name with no args, its description naming one brief"),
                described(token(good))),
        allowed(CaseId("B02"), Prose("a built-in by name, args its plain question, its description naming one brief"),
                described(token(good), question)),
        refused(CaseId("B03"), Prose("a built-in by name whose description names two briefs"),
                described(Prose(f"{token(good)} {token(other)}"), question), held(Tokens(2))),
        refused(CaseId("B19"), Prose("a built-in by name naming its brief in its args, its description absent"),
                described(None, as_args), held(Tokens(0))),
        refused(CaseId("B20"), Prose("a `briefs=` inside a word of the description is no token"),
                described(Prose(f"see-{token(good)} ({token(good)})"), as_args), held(Tokens(0))),
        refused(CaseId("B21"), Prose("a description that is no string holds no token"),
                InputText(json.dumps({"name": RESEARCH, "description": [token(good)], "args": one})), held(Tokens(0))),
        refused(CaseId("B22"), Prose("a token naming a relative path"),
                described(Prose(f"research briefs=briefs.json#{good}"), as_args),
                malformed(Prose(f"briefs=briefs.json#{good}"))),
        refused(CaseId("B23"), Prose("a token naming no label"), described(Prose("briefs=/x.json"), as_args),
                malformed(Prose("briefs=/x.json"))),
        refused(CaseId("B24"), Prose("a token whose label is empty"), described(Prose("briefs=/x.json#"), as_args),
                malformed(Prose("briefs=/x.json#"))),
        refused(CaseId("B25"), Prose("a token naming a label the file lacks"),
                described(token(Label("nope")), as_args), named(Label("nope"), Prose(f"no such label in {BRIEFS}"))),
        allowed(CaseId("B26"), Prose("the token among words split by spaces, a tab and a newline"),
                described(Prose(f"Research\tthe guard\n{token(good)}  now"), question)),
        allowed(CaseId("B27"), Prose("a path holding `#`, the label after the token's last one"),
                described(token(good, HASHED), question)),
        refused(CaseId("B28"), Prose("the description's brief held to the ITEMS rule"),
                described(token(Label("eleven")), as_args),
                named(Label("eleven"), Prose("its ITEMS line declares 11 items, where a brief holds 1 to 10"))),
        allowed(CaseId("B29"), Prose("the description's one brief a PILOT, alone"),
                described(token(Label("pilot")), question)),
        allowed(CaseId("A17"), Prose("a script launch's description, malformed tokens included, is never read"),
                InputText(json.dumps({"script": GOOD, "description": f"{token(good)} {token(other)} briefs=x#y",
                                      "args": one}))),
        refused(CaseId("R42"), Prose("a script launch naming its brief in its description alone names none"),
                InputText(json.dumps({"script": GOOD, "description": token(good)})),
                Prose("  * its args is no object naming `briefs` and `labels`\n")),
        refused(CaseId("B04"), Prose("a built-in's name beside an inline script runs that script, checked"),
                InputText(json.dumps({"name": RESEARCH, "script": BAD, "args": one})), bad_script),
        refused(CaseId("B05"), Prose("a built-in's name beside a script path runs that file, checked"),
                InputText(json.dumps({"name": RESEARCH, "scriptPath": str(BAD_SCRIPT), "args": one})), bad_script),
        refused(CaseId("B06"), Prose("a name that is no string is no built-in"),
                InputText(json.dumps({"name": [RESEARCH], "args": one})),
                listed(Prose("it names no script: none of `script`, `scriptPath` and `name`"))),
        refused(CaseId("B07"), Prose("a name the binary registers by a string, BUILT_IN lacking it"),
                by_name(Prose("fresh-one"), good), unlisted(Prose("fresh-one"))),
        refused(CaseId("B08"), Prose("a name the binary registers by a single-quoted string"),
                by_name(Prose("single-one"), good), unlisted(Prose("single-one"))),
        refused(CaseId("B09"), Prose("a name a variable holds through an alias, its last assignment, no member's"),
                by_name(Prose("aliased-one"), good), unlisted(Prose("aliased-one"))),
        refused(CaseId("B10"), Prose("a name a variable holds as a single-quoted string"),
                by_name(Prose("quoted-var"), good), unlisted(Prose("quoted-var"))),
        refused(CaseId("B11"), Prose("a name after a template ending in an escaped backslash"),
                by_name(Prose("slashed-one"), good), unlisted(Prose("slashed-one"))),
        refused(CaseId("B12"), Prose("a meta in a documentation string registers nothing"),
                by_name(Prose("review-changes"), good), Prose(f"  * {nowhere(Prose('review-changes'))}\n")),
        refused(CaseId("B13"), Prose("a `name:` inside a registering call's template registers nothing"),
                by_name(Prose("decoy-inside"), good), Prose(f"  * {nowhere(Prose('decoy-inside'))}\n")),
        refused(CaseId("B14"), Prose("a call of another function registers nothing"),
                by_name(Prose("other-fn"), good), Prose(f"  * {nowhere(Prose('other-fn'))}\n")),
        refused(CaseId("B15"), Prose("a call of a function pushing another source registers nothing"),
                by_name(Prose("plugin-one"), good), Prose(f"  * {nowhere(Prose('plugin-one'))}\n")),
        refused(CaseId("B16"), Prose("a call of a longer identifier ending in the function's name registers nothing"),
                by_name(Prose("suffix-one"), good), Prose(f"  * {nowhere(Prose('suffix-one'))}\n")),
        refused(CaseId("B17"), Prose("a call of a member named as the function registers nothing"),
                by_name(Prose("member-one"), good), Prose(f"  * {nowhere(Prose('member-one'))}\n")),
        refused(CaseId("B18"), Prose("a call of an identifier ending in `$` and the function's name registers nothing"),
                by_name(Prose("dollar-one"), good), Prose(f"  * {nowhere(Prose('dollar-one'))}\n")),
        allowed(CaseId("A16"), Prose("a script launch's args holding a field of its own"),
                InputText(json.dumps({"script": GOOD, "args": {**one, "question": question}}))),
    ]


def special_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases that are not one launch judged alone."""
    results: list[tuple[CaseId, Prose, bool]] = []
    passed = Outcome(ALLOW, NOTHING, NOTHING)
    bad = launch(Label("good"), Label("other"), Label("ten"))
    results.append((CaseId("S01"), Prose("a call of another tool passes untouched, printing nothing"),
                    hook(bad, tool=Prose("Bash")) == passed))
    results.append((CaseId("S02"), Prose("an event other than PreToolUse passes untouched"),
                    hook(bad, event=Prose("PostToolUse")) == passed))
    KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH.touch()
    try:
        switched = hook(bad)
    finally:
        KILL_SWITCH.unlink()
    results.append((CaseId("S03"), Prose("the kill switch lets a launch the rule refuses through"), switched == passed))
    unreadable = piped(EventText("not json"))
    results.append((CaseId("S04"), Prose("an event that cannot be read goes through, reported"),
                    unreadable.status == REPORTED and "could not be read" in unreadable.stderr))
    neither = Label("neither")
    both = hook(launch(neither))
    results.append((CaseId("S05"), Prose("a brief breaking GOAL and ITEMS is refused naming both"),
                    both.status == REFUSE and named(neither, Prose("it has no line starting `GOAL: `")) in both.stderr
                    and named(neither, Prose("it has no line `ITEMS (<n>): <a>, <b>, ...`")) in both.stderr))
    mixed = hook(launch(Label("good"), Label("no-goal")))
    results.append((CaseId("S06"), Prose("of two briefs, only the one breaking the rule is named"),
                    mixed.status == REFUSE and "brief `no-goal`" in mixed.stderr and "brief `good`" not in mixed.stderr))
    event = EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Workflow", "cwd": str(BASE),
                                  "tool_input": json.loads(launch(Label("good"))), "session_id": "test"}))
    unparsed = piped(event, PLANT)
    results.append((CaseId("S07"), Prose("a tree-sitter package that does not import refuses, naming it"),
                    unparsed.status == REFUSE and unparsed.stderr.startswith(RULE)
                    and "the package `tree_sitter_javascript` does not import" in unparsed.stderr))
    pins = GUARD.parent / "requirements.txt"
    lines = pins.read_text().splitlines() if pins.is_file() else []
    pinned = dict(line.split("==") for line in lines if line and not line.startswith("#"))
    results.append((CaseId("S08"), Prose("requirements.txt beside the hook pins both packages, as installed, "
                                         "naming the interpreter"),
                    {name: pinned.get(name) for name in PINS} == PINS
                    and any(INTERPRETER in line for line in lines if line.startswith("#"))
                    and all(importlib.metadata.version(name) == version for name, version in PINS.items())))
    results.extend(event_cases())
    results.extend(binary_cases())
    results.extend(watch_cases())
    results.extend(broken_registry_cases())
    results.extend(registry_place_cases())
    results.extend(ledger_cases())
    results.extend(unrecorded_cases())
    results.extend(unreadable_ledger_cases())
    results.extend(torn_ledger_cases())
    return results


def event_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of an event the hook reads before any launch, and of a fault of its own."""
    results: list[tuple[CaseId, Prose, bool]] = []
    passed = Outcome(ALLOW, NOTHING, NOTHING)
    deep = piped(EventText(DEEP))
    results.append((CaseId("S09"), Prose("an event nested past the decoder's stack goes through, reported"),
                    deep.status == REPORTED and "could not be read" in deep.stderr))
    results.append((CaseId("S10"), Prose("an event that is JSON but no object passes untouched"),
                    piped(EventText("[]")) == passed))
    good = json.loads(launch(Label("good")))
    bare = piped(EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Workflow", "cwd": str(BASE)})))
    results.append((CaseId("S11"), Prose("an event with no tool_input is refused as a launch naming nothing"),
                    bare.status == REFUSE and bare.stderr.startswith(RULE)
                    and "  * its args is no object naming `briefs` and `labels`\n" in bare.stderr
                    and "  * it names no script: none of `script`, `scriptPath` and `name`\n" in bare.stderr))
    results.append((CaseId("S12"), Prose("an event with no cwd judges an inline launch alike"),
                    piped(EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Workflow",
                                                "tool_input": good}))) == passed))
    event = EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Workflow", "cwd": str(BASE),
                                  "tool_input": good}))
    fault = piped(event, FAULT)
    results.append((CaseId("S13"), Prose("a fault of the hook's own lets the launch through, reported"),
                    fault.status == REPORTED and fault.stdout == NOTHING
                    and "internal error, the call goes ahead: RuntimeError: planted" in fault.stderr))
    capped = piped(EventText(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Workflow", "cwd": str(BASE),
                                         "tool_input": json.loads(launch(Label("good"), briefs=BIG))})), cap=CAP)
    results.append((CaseId("S14"), Prose("a briefs file past the address space refuses, naming MemoryError"),
                    capped.status == REFUSE and capped.stderr.startswith(RULE)
                    and f"its briefs file {BIG} cannot be read: MemoryError" in capped.stderr))
    OVERSIZED.parent.mkdir()
    with OVERSIZED.open("wb") as handle:
        handle.truncate(SPARSE)
    try:
        oversized = hook(launch(Label("good")), cap=CAP)
    finally:
        shutil.rmtree(OVERSIZED.parent)
    results.append((CaseId("S38"), Prose("a past agent whose transcript line is past the address space is of no SHAPE, "
                                         "the launch judged without it"),
                    oversized == passed))
    return results


def refuses_unreadable(outcome: Outcome, why: Prose) -> bool:
    """Say whether ``outcome`` refuses a launch of `anything-else`, found nowhere, the binary unreadable for ``why``."""
    return (outcome.status == REFUSE and outcome.stderr.startswith(RULE)
            and f"  * {nowhere(Prose('anything-else'))}; {UNREADABLE}: {why}\n" in outcome.stderr)


def binary_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of a built-in's brief, of a workflow saved under a built-in's name, and of a binary read otherwise."""
    results: list[tuple[CaseId, Prose, bool]] = []
    anything = Prose("anything-else")
    flawed = hook(described(token(Label("no-goal"))))
    results.append((CaseId("S15"), Prose("a built-in's brief breaking the rule is refused, no script looked for"),
                    flawed.status == REFUSE and named(Label("no-goal"), Prose("it has no line starting `GOAL: `"))
                    in flawed.stderr and "saved workflow" not in flawed.stderr))
    advised = hook(described(None))
    results.append((CaseId("S30"), Prose("the advice names the description's token a built-in's brief is named by"),
                    advised.status == REFUSE and ADVISED in advised.stderr))
    lined = hook(launch(Label("good"), Label("other"), Label("ten")))  # refused for its count, no brief line named
    results.append((CaseId("S32"), Prose("the advice names the SHAPE, SIZE, PILOT, DECISIONS, PROOF and ADVISOR "
                                         "lines, the count a SIZE is held to, the registry a SHAPE is a row of and "
                                         "why a workflow agent never consults the advisor"),
                    lined.status == REFUSE and all(form in lined.stderr for form in (*FORMS, COUNTED, ADD_ONLY))))
    shadow = HOME / ".claude" / "workflows" / f"{RESEARCH}.js"
    shadow.write_text(BAD)
    try:
        shadowed = hook(by_name(RESEARCH, Label("good")))
        shadow.write_text(GOOD)
        unread = hook(described(token(Label("good"))))
    finally:
        shadow.unlink()
    results.append((CaseId("S16"), Prose("a workflow saved under a built-in's name is read and checked"),
                    shadowed.status == REFUSE and "  * its script calls no agent()\n" in shadowed.stderr))
    results.append((CaseId("S31"), Prose("a workflow saved under a built-in's name reads its args, not its description"),
                    unread.status == REFUSE and "  * its args is no object naming `briefs` and `labels`\n"
                    in unread.stderr))
    for number, (tag, (code, reason)) in enumerate(FAULTY.items(), start=17):
        binary = INSTALLS / tag / FAULTY_VERSION
        why = reason if reason is not None else Prose(
            f"the call of Q1$ at byte {code.index(FIRST_CALL)} registers a name no string gives")
        results.append((CaseId(f"S{number}"), Prose(f"a binary unreadable by its fault `{tag}` refuses, saying so"),
                        refuses_unreadable(hook(by_name(anything, Label("good")), search=BINS / tag),
                                           Prose(f"{binary}: {why}"))))
    sparse = hook(by_name(anything, Label("good")), search=BINS / SPARSE_TAG, cap=CAP)
    results.append((CaseId("S27"), Prose("a binary past the address space refuses, naming the error"),
                    refuses_unreadable(sparse, Prose(f"{INSTALLS / SPARSE_TAG / FAULTY_VERSION}: OSError: "
                                                     "[Errno 12] Cannot allocate memory"))))
    results.append((CaseId("S28"), Prose("no `claude` on PATH and no versions folder refuses, saying so"),
                    refuses_unreadable(hook(by_name(anything, Label("good")), search=NO_BIN),
                                       Prose(f"no `claude` on PATH and no file under {VERSIONS}"))))
    older, newer, folder = VERSIONS / "8.0.2", VERSIONS / "8.0.1", VERSIONS / "8.0.3"
    VERSIONS.mkdir(parents=True)
    folder.mkdir()
    for at, (place, name) in enumerate(((older, "older-one"), (newer, "home-one"))):
        place.write_text(chunk(REGISTER, call(Q1, Code("}"), Code(f'name:"{name}"'))))
        os.utime(place, ns=(at * 10**9, at * 10**9))
    os.utime(folder, ns=(2 * 10**9, 2 * 10**9))
    found = hook(by_name(Prose("home-one"), Label("good")), search=NO_BIN)
    results.append((CaseId("S29"), Prose("with no `claude` on PATH the newest file under versions is read, by time"),
                    found.status == REFUSE and unlisted(Prose("home-one"), Version("8.0.1")) in found.stderr))
    return results


def kit(tag: Tag, watch: Code | None, registry: Code | None, root: Path = KITS) -> Path:
    """Write kit ``tag`` under ``root``, a copy of the guard and of its _shell.py, its watch ``watch`` and its SHAPE
    registry ``registry``, a folder in the place of each one None; return the guard's copy."""
    hooks = root / tag / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for kept in (GUARD, GUARD.parent / "_shell.py"):
        shutil.copy2(kept, hooks / kept.name)
    for place, text in ((root / tag / WATCH, watch), (root / tag / REGISTRY, registry)):
        place.parent.mkdir(parents=True)
        if text is None:
            place.mkdir()
        else:
            place.write_text(text)
    return hooks / GUARD.name


def watch_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of a copy of the guard whose nearest workflow-token-watch, in a kit of its own, does not load."""
    guards = {tag: kit(tag, code, SHAPES) for tag, (code, _) in BROKEN_WATCHES.items()}

    def unloaded(tag: Tag) -> Prose:
        """The problem of a SIZE line kit ``tag``'s watch cannot check, the interpreter's message following."""
        error = BROKEN_WATCHES[tag][1]
        return Prose(f"its SIZE line cannot be checked: {KITS / tag / WATCH} does not load: {error}: ")

    syntax, folder = Tag("syntax"), Tag("folder")
    judged = [
        *((tag, faulted(CaseId(f"S{number}"), Label("good"),
                        Prose(f"a watch that does not load, by `{tag}`, refuses a sized brief"), unloaded(tag)))
          for number, tag in enumerate(BROKEN_WATCHES, start=33)),
        (syntax, faulted(CaseId("S36"), Label("pilot"),
                         Prose("a watch that does not load refuses a PILOT, its SHAPE's runs unread"), unloaded(syntax))),
        (folder, faulted(CaseId("S37"), Label("unshaped-pilot"),
                         Prose("a watch that does not load leaves a lone PILOT naming no SHAPE refused for its SHAPE "
                               "alone"), NO_SHAPE)),
    ]
    return [(case.id, case.what, agrees(case, hook(case.tool_input, guard=guards[tag]))) for tag, case in judged]


def broken_registry_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of a copy of the guard whose SHAPE registry, in a kit of its own, cannot be read or holds a malformed
    row: every launch refused, naming it, a launch otherwise kept."""
    watched = Code((KITS / MAIN_KIT / WATCH).read_text())
    given = BROKEN_REGISTRIES | QUOTED_REGISTRIES | UNREASONED_REGISTRIES
    guards = {tag: kit(tag, watched, registry) for tag, (registry, _) in given.items()}
    guards[SPARSE_REGISTRY] = kit(SPARSE_REGISTRY, watched, Code(""))
    with (KITS / SPARSE_REGISTRY / REGISTRY).open("wb") as handle:
        handle.truncate(SPARSE)
    says = {tag: says for tag, (_, says) in given.items()}
    says[SPARSE_REGISTRY] = Prose("cannot be read: MemoryError: ")
    numbered = [*enumerate([*BROKEN_REGISTRIES, SPARSE_REGISTRY], start=39), *enumerate(QUOTED_REGISTRIES, start=87),
                *enumerate(UNREASONED_REGISTRIES, start=90)]

    def judged(case_id: CaseId, tag: Tag) -> Case:
        """A launch of brief `good` by kit ``tag``'s guard, refused for its registry alone."""
        return refused(case_id, Prose(f"a registry refused by `{tag}` refuses every launch, naming it"),
                       launch(Label("good")), listed(Prose(f"the SHAPE registry {KITS / tag / REGISTRY} {says[tag]}")))

    refusals = [(tag, judged(CaseId(f"S{number}"), tag)) for number, tag in numbered]
    return [(case.id, case.what, agrees(case, hook(case.tool_input, guard=guards[tag],
                                                    cap=CAP if tag == SPARSE_REGISTRY else None)))
            for tag, case in refusals]


def registry_place_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of where a copy of the guard looks for its SHAPE registry: the folders holding its resolved path, the
    nearest first, else its own folder's place."""
    (LINKED / ".claude").mkdir(parents=True)
    (LINKED / ".claude" / "hooks").symlink_to(Path("..") / ".." / MAIN_KIT / ".claude" / "hooks")
    (LINKED / REGISTRY).parent.mkdir()
    (LINKED / REGISTRY).write_text("")
    linked = hook(launch(Label("good")), guard=LINKED / ".claude" / "hooks" / GUARD.name)
    results = [(CaseId("S85"), Prose("a copy run through a symlinked folder reads the registry of its resolved path's "
                                     "folders, not the one beside the link"),
                linked == Outcome(ALLOW, NOTHING, NOTHING))]
    outside = Path(tempfile.mkdtemp(prefix="workflow-launch-guard-outside-", dir=OUTSIDE)).resolve()
    try:
        guard = kit(Tag("unlisted"), Code((KITS / MAIN_KIT / WATCH).read_text()), Code(""), root=outside)
        (outside / "unlisted" / REGISTRY).unlink()
        place = guard.parent / REGISTRY
        case = refused(CaseId("S86"), Prose("a copy no folder of whose path holds a registry refuses every launch, "
                                            "naming the place in its own folder"),
                       launch(Label("good")), listed(Prose(f"the SHAPE registry {place} cannot be read: "
                                                           "FileNotFoundError: ")))
        unlisted = not any((folder / REGISTRY).exists() for folder in guard.parents)
        results.append((case.id, case.what, unlisted and agrees(case, hook(case.tool_input, guard=guard))))
    finally:
        shutil.rmtree(outside, ignore_errors=True)
    return results


def text_of(label: Label) -> BriefText:
    """The text of TABLE's brief ``label``."""
    text = TABLE[label]
    assert isinstance(text, str), label
    return BriefText(text)


def ledgered(written: Ledgered, briefs: list[tuple[Label, Prose]], before: datetime, after: datetime) -> bool:
    """Say whether ``written`` is what one launch of ``briefs``, each a label of BRIEFS and its SHAPE, appends to
    the ledger: a line each, in order, naming BRIEFS, the label, the SHAPE and the SHA-256 of the brief's text as
    UTF-8, a lone surrogate passed through, all stamped with one UTC time to the second from ``before`` to
    ``after``."""
    stamp = STAMPED.match(written)
    time = stamp.group(1).decode() if stamp is not None else ""
    rows = "".join(json.dumps({"time": time, "briefs": str(BRIEFS), "label": label, "shape": shape, "sha256":
                               hashlib.sha256(text_of(label).encode(errors="surrogatepass")).hexdigest()}) + "\n"
                   for label, shape in briefs)
    return before.strftime(STAMP) <= time <= after.strftime(STAMP) and written == rows.encode()


def ledger_of(home: Path, kept: Path = LEDGER) -> Ledgered:
    """What ``home``'s ``kept``, its ledger by default, holds; nothing where it is no file."""
    return Ledgered((home / kept).read_bytes() if (home / kept).is_file() else b"")


def ledger_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of the ledger a launch let through appends to, each under a HOME of its own."""
    passed = Outcome(ALLOW, NOTHING, NOTHING)
    measured = BASE / "ledger-home"  # its ~/.claude holding the past runs and the ledger rows they were launched by
    (measured / ".claude").mkdir(parents=True)
    (measured / ".claude" / "projects").symlink_to(PROJECTS)
    (measured / LEDGER).write_bytes(PAST_LEDGER)
    pair = [(Label("good"), Prose("guard-code")), (Label("lone-surrogate"), Prose("light"))]
    before = datetime.now(UTC)
    two = hook(launch(*(label for label, _ in pair)), home=measured)
    after = datetime.now(UTC)
    fresh = BASE / "fresh-home"  # holding no ~/.claude
    fresh.mkdir()
    start = datetime.now(UTC)
    first = hook(launch(Label("pilot")), home=fresh)
    middle = datetime.now(UTC)
    kept = ledger_of(fresh)
    second = hook(launch(Label("digit-pilot")), home=fresh)
    end = datetime.now(UTC)
    grown = ledger_of(fresh)
    return [
        (CaseId("S50"), Prose("a launch of two briefs let through appends a ledger row for each, in order, naming its "
                              "briefs file, label, SHAPE and the SHA-256 of its text"),
         two == passed and ledger_of(measured).startswith(PAST_LEDGER)
         and ledgered(Ledgered(ledger_of(measured)[len(PAST_LEDGER):]), pair, before, after)),
        (CaseId("S51"), Prose("a second launch let through appends after the first's rows, leaving them byte for byte, "
                              "the first creating the missing ~/.claude, neither keeping a torn tail"),
         first == passed and second == passed and ledgered(kept, [(Label("pilot"), Prose("never-run"))], start, middle)
         and grown.startswith(kept)
         and ledgered(Ledgered(grown[len(kept):]), [(Label("digit-pilot"), Prose("probe-23"))], middle, end)
         and not (fresh / TORN).exists()),
    ]


def unrecorded_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of a launch that appends no ledger row, each under a HOME of its own, its ledger left as it was: one a
    check refuses, and one every check keeps whose ledger the guard cannot append to, refused naming its path and
    the error, a HOME that does not exist among them."""
    pilot = launch(Label("pilot"))  # a lone PILOT, let through under a HOME holding no past run
    seeded = Ledgered(b'{"time": "2026-11-05T00:00:00Z", "briefs": "/past.json", "label": "past", "shape": "light", '
                      b'"sha256": "00"}\n')  # a row a past launch appended
    locked, closed, bare, kept, absent = (BASE / f"{kind}-home" for kind in ("locked", "closed", "bare", "kept", "absent"))
    for home in (locked, kept):
        (home / LEDGER).parent.mkdir(parents=True)
        (home / LEDGER).write_bytes(seeded)
    closed.mkdir()
    bare.mkdir()
    (locked / LEDGER).chmod(0o444)
    closed.chmod(0o555)
    try:
        unappended = {home: hook(pilot, home=home) for home in (locked, closed, absent)}
    finally:
        (locked / LEDGER).chmod(0o644)
        closed.chmod(0o755)
    by_brief = hook(launch(Label("no-goal")), home=bare)
    by_script = hook(InputText(json.dumps({"script": BAD, "args": {"briefs": str(BRIEFS), "labels": ["pilot"]}})),
                     home=kept)

    def refuses(case_id: CaseId, home: Path, error: Prose) -> bool:
        """Say whether ``home``'s launch of `pilot` is refused for its ledger alone, the append failing on ``error``."""
        says = listed(Prose(f"the launch ledger {home / LEDGER} cannot be appended to: {error}: "))
        return agrees(refused(case_id, Prose("a ledger the guard cannot append to"), pilot, says), unappended[home])

    return [
        (CaseId("S53"), Prose("a ledger file the guard may not write refuses the launch, naming its path and the "
                              "error, its bytes left as they were"),
         refuses(CaseId("S53"), locked, Prose("PermissionError")) and ledger_of(locked) == seeded),
        (CaseId("S54"), Prose("a ~/.claude the guard cannot create refuses the launch, naming the ledger's path and "
                              "the error, ~/.claude left absent"),
         refuses(CaseId("S54"), closed, Prose("PermissionError")) and not (closed / ".claude").exists()),
        (CaseId("S92"), Prose("a HOME that does not exist refuses the launch, naming the ledger's path and the "
                              "error, the HOME left absent"),
         refuses(CaseId("S92"), absent, Prose("FileNotFoundError")) and not absent.exists()),
        (CaseId("S55"), Prose("a launch a check of its brief refuses leaves an absent ledger absent"),
         by_brief.status == REFUSE and not (bare / LEDGER).exists()),
        (CaseId("S56"), Prose("a launch a check of its script refuses leaves a present ledger byte for byte"),
         by_script.status == REFUSE and ledger_of(kept) == seeded),
    ]


def unreadable_ledger_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of a ledger that cannot be read or holds a line that is no row, each under a HOME of its own beside the
    past runs: a sized brief and a lone PILOT each refused for the ledger alone, naming its path, the ledger left as
    it was; and a ledger of seed rows, which binds the past runs and refuses no launch."""
    results: list[tuple[CaseId, Prose, bool]] = []
    launches = ((Label("good"), Prose("a sized brief")), (Label("pilot"), Prose("a lone PILOT")))
    numbered = [*zip(itertools.count(57, 2), BROKEN_LEDGERS.items()),
                *zip(itertools.count(94, 2), LATER_LEDGERS.items())]
    for first, (tag, (written, says)) in numbered:
        home = BASE / tag
        ledger = home / LEDGER
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "projects").symlink_to(PROJECTS)
        if written is None:
            ledger.mkdir()
        else:
            ledger.write_bytes(written)
        if tag == SPARSE_LEDGER:
            with ledger.open("wb") as handle:
                handle.truncate(SPARSE)
        if tag == LOCKED_LEDGER:
            ledger.chmod(0o200)
        try:
            outcomes = [hook(launch(label), home=home, cap=CAP if tag == SPARSE_LEDGER else None)
                        for label, _ in launches]
        finally:
            if tag == LOCKED_LEDGER:
                ledger.chmod(0o644)
        kept = (not any(ledger.iterdir()) if written is None else ledger.stat().st_size == SPARSE
                if tag == SPARSE_LEDGER else ledger.read_bytes() == written)
        for at, ((label, kind), outcome) in enumerate(zip(launches, outcomes, strict=True)):
            case = refused(CaseId(f"S{first + at}"),
                           Prose(f"a ledger refused by `{tag}` refuses {kind}, naming it, the ledger left as it was"),
                           launch(label), listed(Prose(f"the launch ledger {ledger} {says}")))
            results.append((case.id, case.what, agrees(case, outcome) and kept))
    seeded = BASE / "ledger-seeded"  # its ledger PAST_LEDGER's rows, each carrying `seeded`
    (seeded / ".claude").mkdir(parents=True)
    (seeded / ".claude" / "projects").symlink_to(PROJECTS)
    (seeded / LEDGER).write_bytes(b"".join((json.dumps({**json.loads(line), "seeded": True}) + "\n").encode()
                                           for line in PAST_LEDGER.splitlines()))
    passed = Outcome(ALLOW, NOTHING, NOTHING)
    sized, pilot = (hook(launch(label), home=seeded) for label, _ in launches)
    results.append((CaseId(f"S{57 + 2 * len(BROKEN_LEDGERS)}"),
                    Prose("a ledger of seed rows binds the past runs a sized brief is held to, and refuses neither it "
                          "nor a lone PILOT after it, the sized brief's row read back"),
                    sized == passed and pilot == passed))
    return results


def torn_ledger_cases() -> list[tuple[CaseId, Prose, bool]]:
    """Cases of a ledger ending in a torn tail, the bytes after its last newline, each under a HOME of its own: a
    lone PILOT let through, its row landing after the last complete row, the complete rows byte for byte, the tail
    kept in TORN as a JSON line with the row's time, after the lines TORN held; and refused, the ledger left as it
    was, for a complete line that is no row before the tail, no tail kept, or for a TORN the guard cannot append to,
    TORN left as it was."""
    pilot = launch(Label("pilot"))  # a lone PILOT, let through under a HOME holding no past run
    complete = Ledgered(SEED_ROW + b"{" + FIELDS + b"}\n")  # two complete rows
    half = Ledgered(b'{"time": "2026-11-05T00:00:00Z", "briefs": "/caf\xc3')  # a row cut inside a character
    earlier = Ledgered(b'{"time": "2026-11-05T00:00:00Z", "torn": "{"}\n')  # a tail a past launch kept
    torn = {  # by case, the ledger's complete rows, its tail, and what TORN holds before the launch
        CaseId("S80"): (Prose("a half row after the last newline"), complete, half, Ledgered(b"")),
        CaseId("S81"): (Prose("a NUL-filled tail"), complete, Ledgered(b"\0" * 4096), Ledgered(b"")),
        CaseId("S82"): (Prose("a ledger holding no newline"), Ledgered(b""), Ledgered(b"{" + FIELDS), Ledgered(b"")),
        CaseId("S93"): (Prose("a half row, TORN holding a past tail,"), complete, half, earlier),
    }
    results: list[tuple[CaseId, Prose, bool]] = []
    for case_id, (kind, rows, tail, held) in torn.items():
        home = BASE / f"torn-{case_id}"
        (home / LEDGER).parent.mkdir(parents=True)
        (home / LEDGER).write_bytes(rows + tail)
        if held:
            (home / TORN).write_bytes(held)
        before = datetime.now(UTC)
        outcome = hook(pilot, home=home)
        after = datetime.now(UTC)
        written = ledger_of(home)
        stamp = STAMPED.match(written, len(rows))
        kept = json.dumps({"time": stamp.group(1).decode() if stamp is not None else "",
                           "torn": tail.decode(errors="backslashreplace")}) + "\n"
        results.append((case_id, Prose(f"{kind} lets a launch through, its row after the last complete row, those "
                                       "byte for byte, the tail kept with the row's time after what TORN held"),
                        outcome == Outcome(ALLOW, NOTHING, NOTHING) and written.startswith(rows)
                        and ledgered(Ledgered(written[len(rows):]), [(Label("pilot"), Prose("never-run"))], before,
                                     after)
                        and ledger_of(home, TORN) == held + kept.encode()))
    malformed, locked = BASE / "torn-malformed-home", BASE / "torn-locked-home"
    broken = Ledgered(SEED_ROW + b"{not json\n" + half)
    for home, ledger in ((malformed, broken), (locked, Ledgered(complete + half))):
        (home / LEDGER).parent.mkdir(parents=True)
        (home / LEDGER).write_bytes(ledger)
    (locked / TORN).write_bytes(earlier)
    (locked / TORN).chmod(0o444)
    try:
        unkept = hook(pilot, home=locked)
    finally:
        (locked / TORN).chmod(0o644)
    still = refused(CaseId("S83"), Prose("a complete line that is no row before a torn tail still refuses, naming it"),
                    pilot, listed(Prose(f"the launch ledger {malformed / LEDGER} is malformed: its line 2 is no JSON: "
                                        "JSONDecodeError: ")))
    lost = refused(CaseId("S84"), Prose("a TORN the guard cannot append to refuses"), pilot,
                   listed(Prose(f"the torn tail of the launch ledger {locked / LEDGER} cannot be appended to "
                                f"{locked / TORN}: PermissionError: ")))
    results.extend([
        (still.id, Prose(f"{still.what}, the ledger left as it was and no tail kept"),
         agrees(still, hook(pilot, home=malformed)) and ledger_of(malformed) == broken
         and not (malformed / TORN).exists()),
        (lost.id, Prose(f"{lost.what} a launch, naming both paths and the error, the ledger and TORN left as they were"),
         agrees(lost, unkept) and ledger_of(locked) == complete + half and ledger_of(locked, TORN) == earlier),
    ])
    return results


def agrees(case: Case, outcome: Outcome) -> bool:
    """Say whether ``outcome`` is the answer ``case`` wants: a refusal opens on the rule and its `  * ` lines are the
    lines of ``case.says``, in order, each whole but one ending `: `, which the interpreter's message follows."""
    if case.want == ALLOW:
        return outcome == Outcome(ALLOW, NOTHING, NOTHING)
    printed = [line for line in outcome.stderr.splitlines() if line.startswith("  * ")]
    wanted = case.says.splitlines()
    return (outcome.status == case.want and outcome.stdout == NOTHING and outcome.stderr.startswith(RULE)
            and len(printed) == len(wanted)
            and all(line == want or (want.endswith(": ") and line.startswith(want))
                    for line, want in zip(printed, wanted, strict=True)))


def install(tag: Tag, version: Version, code: Code | None) -> None:
    """Write fake install ``tag`` of ``version`` holding ``code``, sparse past CAP for None, and its `claude`."""
    binary = INSTALLS / tag / version
    binary.parent.mkdir(parents=True)
    with binary.open("wb") as handle:
        if code is None:
            handle.truncate(SPARSE)
        else:
            handle.write(code.encode())
    binary.chmod(0o755)
    (BINS / tag).mkdir(parents=True)
    (BINS / tag / "claude").symlink_to(binary)


def main() -> ExitStatus:
    """Run every case; print one line each, MISS for a disagreement, then the count."""
    if not any((folder / WATCH).is_file() for folder in GUARD.resolve().parents):
        shutil.rmtree(BASE, ignore_errors=True)
        print(f"no folder holding {GUARD} holds {WATCH}, which the guard counts a run's tokens with: test the kit's "
              "guard, or a copy of it under a TMPDIR inside the kit", file=sys.stderr)
        return ExitStatus(2)
    bad = 0
    total = 0
    watched = next(folder / WATCH for folder in GUARD.resolve().parents if (folder / WATCH).is_file())
    kit(MAIN_KIT, Code(watched.read_text()), SHAPES)
    HOME.mkdir()
    for transcript, lines in TRANSCRIPTS.items():
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("".join(f"{line}\n" for line in [STARTS.get(transcript, timed(STARTED)), *lines]))
    FOLDER.mkdir()
    PAST.parent.mkdir(parents=True)
    PAST.write_text(json.dumps(PAST_TABLE))
    (HOME / LEDGER).write_bytes(PAST_LEDGER)
    REQUESTED.parent.mkdir(parents=True)
    REQUESTED.write_text(json.dumps({"decoy": PAST_TABLE[Label("decoy")]}))
    BRIEFS.write_text(json.dumps(TABLE))
    HASHED.write_text(json.dumps(TABLE))
    NOT_JSON.write_text("{not json")
    A_LIST.write_text(json.dumps(list(TABLE.values())))
    SCRIPT_FILE.write_text(GOOD)
    BAD_SCRIPT.write_text(BAD)
    for saved in (SAVED, PROJECT_SAVED):
        saved.parent.mkdir(parents=True)
        saved.write_text(GOOD)
    NESTED.write_text(DEEP)
    BIG.write_text("[" + ",".join(["{}"] * 3_000_000) + "]")
    FAULT.mkdir()
    (FAULT / "tree_sitter_javascript.py").write_text("def language():\n    raise RuntimeError('planted')\n")
    PLANT.mkdir()
    (PLANT / "tree_sitter_javascript.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'tree_sitter_javascript'\", name='tree_sitter_javascript')\n")
    install(STANDARD_TAG, STANDARD_VERSION, STANDARD)
    for tag, (code, _) in FAULTY.items():
        install(tag, FAULTY_VERSION, code)
    install(SPARSE_TAG, FAULTY_VERSION, None)
    NO_BIN.mkdir()
    try:
        for case in cases():
            outcome = hook(case.tool_input)
            total += 1
            ok = agrees(case, outcome)
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case.id:<4} want={case.want} got={outcome.status} {case.what}")
            if not ok and (outcome.stderr.strip() or outcome.stdout.strip()):
                print("        " + " | ".join((outcome.stdout + outcome.stderr).strip().splitlines()[:4])[:300])
        for case_id, what, ok in special_cases():
            total += 1
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case_id:<4} {what}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"{total} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
