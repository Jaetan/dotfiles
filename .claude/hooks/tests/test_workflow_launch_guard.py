#!/usr/bin/env python3
"""Regression cases for workflow-launch-guard.py.

Every case pipes one PreToolUse event for a Workflow call to the hook, run by
its path as the harness runs it, with HOME a fresh temporary directory so the
kill switch is the case's own, the call naming briefs of a briefs file written
under that directory, its script one the hook parses: inline, a file, or a saved
workflow under that HOME or the case's own CLAUDE_PROJECT_DIR. Its PATH holds a
`claude` resolving to a fake Claude Code binary written under that directory, so
a launch by a name found nowhere reads the fake, never the installed binary. It
compares the answer with the rule: exit 2 refuses a launch, printing `BLOCKED: `
and the rule, then a line naming the brief's label or the script's line and what
it breaks; exit 0 with no output lets the launch through, and every call of
another tool.

Usage: test_workflow_launch_guard.py [GUARD]
  GUARD  the guard to test, or a hooks directory holding it (default: ..)
Exit 0 when every case agrees with its expectation.
"""

import importlib.metadata
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple, NewType

# A case's name; prose a case names itself by or expects, a brief's line among
# it; a brief's label; a brief's text; a call's input, its args, or an event as
# JSON text; the hook's exit status; a line of a script; text of a fake binary;
# the name of a fake install; the version a fake install is named by.
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
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Start = Literal["script", "scriptPath", "name"]

HOOKS = Path(__file__).resolve().parent.parent
GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else HOOKS).resolve()
GUARD = GIVEN / "workflow-launch-guard.py" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="workflow-launch-guard-test-")).resolve()
HOME = BASE / "home"
KILL_SWITCH = HOME / ".claude" / "workflow-launch-guard.off"
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
             "GOAL and an ITEMS list of at most 10 (user rule, 2026-11-03).\n")
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
SPARSE = 256 << 20  # bytes of a fake binary past CAP, written sparse
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


def brief(*lines: Prose) -> BriefText:
    """A brief of ``lines`` followed by the FACTS and RETURN lines every brief carries."""
    return BriefText("\n".join(lines) + "\n" + FACTS)


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
    "number": 5,
}


class Case(NamedTuple):
    """A launch's input, the answer the rule wants, and a line the refusal must print."""

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
         search: Path = BIN, cap: AddressSpace | None = None) -> Outcome:
    """Pipe one ``event`` event of ``tool`` with ``tool_input`` to the hook, HOME the case's own, PATH ``search``."""
    return piped(EventText(json.dumps({
        "hook_event_name": event, "tool_name": tool, "cwd": str(BASE),
        "tool_input": json.loads(tool_input), "session_id": "test",
    })), search=search, cap=cap)


def piped(text: EventText, path: Path | None = None, cap: AddressSpace | None = None, search: Path = BIN) -> Outcome:
    """Pipe ``text`` to the hook as its event, held to SPENT, ``path`` first on its PYTHONPATH, PATH ``search``, and its address space ``cap`` where given."""
    env = {**os.environ, "HOME": str(HOME), "CLAUDE_PROJECT_DIR": str(PROJECT), "PATH": str(search)}
    if path is not None:
        env["PYTHONPATH"] = str(path)
    def capped() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (SPENT, SPENT))
        if cap is not None:
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

    done = subprocess.run([str(GUARD)], input=text, capture_output=True, text=True, check=False, env=env,
                          preexec_fn=capped)
    return Outcome(ExitStatus(done.returncode), Prose(done.stdout), Prose(done.stderr))


def refused(case_id: CaseId, what: Prose, tool_input: InputText, says: Prose) -> Case:
    """A launch the rule refuses, printing ``says``."""
    return Case(case_id, what, tool_input, REFUSE, says)


def allowed(case_id: CaseId, what: Prose, tool_input: InputText) -> Case:
    """A launch the rule lets through with no output."""
    return Case(case_id, what, tool_input, ALLOW, NOTHING)


def named(label: Label, problem: Prose) -> Prose:
    """The refusal's line for brief ``label`` breaking the rule as ``problem`` says."""
    return Prose(f"  * brief `{label}`: {problem}\n")


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
    no_args = Prose("its args is no object naming `briefs` and `labels`")
    no_briefs = Prose("its args names no `briefs`, the absolute path of a prepared briefs file")
    no_labels = Prose("its args names no `labels`, a list of one or two brief labels")
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
        refused(CaseId("R09"), Prose("no label"), launch(), Prose("it names 0 briefs, where a launch names 1 to 2")),
        refused(CaseId("R10"), Prose("three labels"), launch(good, other, Label("ten")),
                Prose("it names 3 briefs, where a launch names 1 to 2")),
        # The briefs file: each label a key, the file a readable JSON object of strings.
        refused(CaseId("R11"), Prose("a label the file lacks"), launch(good, Label("nope")),
                named(Label("nope"), Prose(f"no such label in {BRIEFS}"))),
        refused(CaseId("R12"), Prose("a briefs file that does not exist"), launch(good, briefs=GONE),
                Prose(f"its briefs file {GONE} cannot be read: no such regular file")),
        refused(CaseId("R13"), Prose("a briefs file holding no JSON"), launch(good, briefs=NOT_JSON),
                Prose(f"its briefs file {NOT_JSON} cannot be read: JSONDecodeError")),
        refused(CaseId("R14"), Prose("a briefs file holding a JSON list"), launch(good, briefs=A_LIST),
                Prose(f"its briefs file {A_LIST} is not a JSON object of label to brief text")),
        refused(CaseId("R15"), Prose("a brief that is no string"), launch(Label("number")),
                named(Label("number"), Prose(f"its text in {BRIEFS} is not a string"))),
        refused(CaseId("R16"), Prose("a briefs path that is a directory"), launch(good, briefs=BASE),
                Prose(f"its briefs file {BASE} cannot be read: no such regular file")),
        refused(CaseId("R33"), Prose("a long briefs path is named whole"), launch(good, briefs=LONG),
                Prose(f"its briefs file {LONG} cannot be read: no such regular file")),
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
                Prose(f"its briefs file {NESTED} cannot be read: RecursionError")),
        refused(CaseId("R39"), Prose("a briefs file that is a regular file and cannot be read"),
                launch(good, briefs=Path("/proc/self/mem")),
                Prose("its briefs file /proc/self/mem cannot be read: OSError: [Errno 5] Input/output error")),
        refused(CaseId("R40"), Prose("args a string nested past the decoder's stack"),
                raw(ArgsText(json.dumps(DEEP))), no_args),
        refused(CaseId("R41"), Prose("a launch naming no script"),
                InputText(json.dumps({"args": {"briefs": str(BRIEFS), "labels": [good]}})),
                Prose("it names no script: none of `script`, `scriptPath` and `name`")),
        *script_cases(),
        *built_in_cases(),
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
                started("name", Prose("nowhere")), nowhere(Prose("nowhere"))),
        allowed(CaseId("J11"), Prose("a saved workflow found under CLAUDE_PROJECT_DIR"),
                started("name", Prose("in-project"))),
        refused(CaseId("J12"), Prose("a script file that does not exist"),
                started("scriptPath", Prose(str(BASE / "gone.js"))),
                Prose(f"its script file {BASE / 'gone.js'} cannot be read: no such regular file")),
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
                Prose("its script file /proc/self/mem cannot be read: OSError: [Errno 5] Input/output error")),
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
                Prose("it names no script: none of `script`, `scriptPath` and `name`")),
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
                    pinned == PINS and any(INTERPRETER in line for line in lines if line.startswith("#"))
                    and all(importlib.metadata.version(name) == version for name, version in PINS.items())))
    results.extend(event_cases())
    results.extend(binary_cases())
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


def agrees(case: Case, outcome: Outcome) -> bool:
    """Say whether ``outcome`` is the answer ``case`` wants: a refusal opens on the rule and prints its line."""
    if case.want == ALLOW:
        return outcome == Outcome(ALLOW, NOTHING, NOTHING)
    return (outcome.status == case.want and outcome.stdout == NOTHING and outcome.stderr.startswith(RULE)
            and case.says in outcome.stderr)


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
    bad = 0
    total = 0
    HOME.mkdir()
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
