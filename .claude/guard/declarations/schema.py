"""The two declaration files and every key they hold, checked as read: an unknown key or a wrong type is a KitFault.

A repository declares, in `.claude/guard.toml` at its root, what only that project can name:

    schema = 1                  the schema version the file is written to; required, and 1 is the one this kit reads
    [records]
    paths = [PATH, ...]         folders of run records, kept output a recursive search must not read; relative to
                                the main work tree's root, `~/` from the home folder, or absolute
    [bulk]
    paths = [PATH, ...]         generated folders inside each checkout a recursive search skips; relative to the
                                work tree the call is in, since every checkout has its own
    [unread_scripts]
    paths = [PATH, ...]         the project's own scripts the kit runs as programs without reading their text, each
                                run by its path, through bash, sh or dash, or sourced, where the call does not write
                                it: its command is judged as any other is; relative to the work tree the call is in,
                                as a heavy script is
    [[heavy]]                   a command the project adds to the kit's own table of heavy tools, matched by one of
    module = "a.b"                `python -m a.b`, and a file whose path is a/b.py or ends in /a/b.py, run by
                                  Python or by its path,
    script = "dir/x.sh"           the script at that path in the work tree, run by its path, through bash, sh or
                                  dash, or sourced,
    argv = ["cabal", "run"]       a command named by the first word whose leading operands are the others, in
                                  order, an option before or between them skipped, and the word after it where
                                  valued_options names it,
    program = "unit_tests"        a program of that file name, alone or followed by `-` and a version of digits and
                                  dots, wherever it is run from, or a script of that file name a Python
                                  interpreter runs;
                                where several rows run a command, the project's and the kit's, the one with the most
                                words of its own (an argv row's words, one for any other) decides, light or heavy,
                                and at equal count the project's;
    writes = true                 it writes outputs in the tree it runs in (default false);
    light_with = ["--scope"]      options that make a run of it light, as --help, -h, --version and -V do, each alone
                                  or with `=VALUE`;
    light_targets = ["help"]      operands that make a run of it light where every operand it gives is one;
    verbose_flags = ["-V"]        options that ask for verbose output, not for a version;
    targeted_by = "catch2-operands"  a run naming its targets narrowly is light, reading its tool's valued options as
                                  valued_options does: under catch2-operands one known operand, as Catch2 reads a
                                  test or a tag; under pytest-operands operands, every one a known value ending in
                                  `.py` or holding `::`; under python-files operands, every one a known value
                                  ending in `.py`; neither of these two holding a glob character (`*`, `?`, `[`);
    valued_options = ["--runs"]   options taking a value, so neither the option nor its value is read as a light
                                  option or an operand;
                                a run any row runs is heavy but where these make it light
    [gates.test_determinism]    the gate a test file written is handed to, protocol "determinism-1": asked which
                                paths are tests (--is-test PATH...) and handed each test written (--file PATH --as
                                REL); exit 1 a finding, 2 unreadable
    [gates.precise_hints]       the gate a Python file written is handed to, protocol "hints-1": handed the text after
                                an edit (--file PATH --as REL); exit 1 a hint its record refuses, 2 unreadable
      run = ["venv/bin/python", "-m", "tools.x"]   the gate's command line; a first word holding a slash is
                                relative to its cwd
      cwd = PATH                the folder it runs in, relative to the root of the work tree the file is in (its
                                default), `~/` or absolute
      as_from = PATH            the folder each REL it is handed is relative to, read as cwd is: relative to the root
                                of the work tree the file is in (its default), `~/` or absolute
      protocol = "..."          how the kit hands it a file and reads its answer: the one form named above
      rule_source = "..."       where the rule it holds is written, as a refusal names it
      suffixes = [".py"]        precise_hints only: the file suffixes handed to it
    [probes]
    record = PATH               the project's probe record, a tab-separated file whose first line names the
                                columns probe, verdict and seconds; read as a gate's cwd is, and never asked to exist

The machine declares, in `~/.config/claude-guard/machine.toml`, what only the machine can name:

    schema = 1                  as above
    [cpus]
    agents = "0-19"             the CPUs agents' work runs on, as taskset -c and AllowedCPUs spell a list; the job
                                bound of a tool that sizes itself from the CPU count is how many they are
    [slice]
    name = "claude.slice"       the systemd slice every call's scope runs in
    [memory]
    call_max = "4G"             MemoryMax of a call's scope, a size of digits and an optional K, M, G or T (1024)
    heavy_max = "24G"           MemoryMax of a call running a command its project declares heavy; at least call_max,
                                which it defaults to
    [records]
    paths = [PATH, ...]         run-record folders outside every repository, `~/` from the home folder or absolute;
                                at least one
    [rewrite]
    hook = [WORD, ...]          the command line of the PreToolUse hook that rewrites a Bash call's command, as
                                settings.json registers it: its first word an absolute path or a name found on PATH

A repository's keys are optional but `schema`, and a path no repository declares gets the machine's facts alone. The
machine file is required, and each of its five tables, so no rule holding a machine fact passes a call because the
fact went unread: a table holds its facts, each one above required but `heavy_max`, or holds `absent = "<why>"`
alone, naming why this machine has none of them: its facts are then None, its record folders none.
"""

from datetime import date, datetime, time
from pathlib import Path
from typing import Literal, NamedTuple, NewType

from dispatcher.verdict import KitFault, Prose

TomlKey = NewType("TomlKey", str)
TomlText = NewType("TomlText", str)
TomlInteger = NewType("TomlInteger", int)
TomlFloat = NewType("TomlFloat", float)
type TomlValue = TomlText | TomlInteger | TomlFloat | bool | datetime | date | time | list[TomlValue] | TomlTable
type TomlTable = dict[TomlKey, TomlValue]

PathText = NewType("PathText", str)
Word = NewType("Word", str)
ModuleName = NewType("ModuleName", str)
ProgramName = NewType("ProgramName", str)
Suffix = NewType("Suffix", str)
SliceName = NewType("SliceName", str)
CpuNumber = NewType("CpuNumber", int)
ByteCount = NewType("ByteCount", int)
Count = NewType("Count", int)
Protocol = Literal["determinism-1", "hints-1"]
TargetedBy = Literal["catch2-operands", "pytest-operands", "python-files"]
GateName = Literal["test_determinism", "precise_hints"]
Anchor = Literal["relative", "home"]

SCHEMA = 1
PROTOCOLS: dict[GateName, Protocol] = {"test_determinism": "determinism-1", "precise_hints": "hints-1"}
TARGETED_BY: tuple[TargetedBy, ...] = ("catch2-operands", "pytest-operands", "python-files")
SIZE_UNITS = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
MATCHERS = ("module", "script", "argv", "program")
PROJECT_KEYS = frozenset({"schema", "records", "bulk", "probes", "heavy", "gates", "unread_scripts"})
HEAVY_KEYS = frozenset({*MATCHERS, "writes", "light_with", "light_targets", "verbose_flags", "targeted_by",
                        "valued_options"})
GATE_KEYS = frozenset({"run", "cwd", "as_from", "protocol", "rule_source", "suffixes"})


class Heavy(NamedTuple):
    """A heavy command as declared: one matcher set, a script still relative to the work tree, and its modifiers."""

    module: ModuleName | None
    script: PathText | None
    argv: tuple[Word, ...]
    program: ProgramName | None
    writes: bool
    light_with: tuple[Word, ...]
    light_targets: tuple[Word, ...]
    verbose_flags: tuple[Word, ...]
    targeted_by: TargetedBy | None
    valued_options: tuple[Word, ...]


class GateEntry(NamedTuple):
    """A gate as declared, its cwd and as_from still path texts (None for the main work tree's root)."""

    run: tuple[Word, ...]
    cwd: PathText | None
    as_from: PathText | None
    protocol: Protocol
    rule_source: Prose
    suffixes: tuple[Suffix, ...]


class ProjectEntries(NamedTuple):
    """A repository's declaration as written, its paths not yet resolved against a work tree."""

    records: tuple[PathText, ...]
    bulk: tuple[PathText, ...]
    heavy: tuple[Heavy, ...]
    test_determinism: GateEntry | None
    precise_hints: GateEntry | None
    unread_scripts: tuple[PathText, ...]
    probe_record: PathText | None


class FactTable(NamedTuple):
    """A machine table's keys: the facts it must hold unless it declares `absent`, and those it may."""

    required: tuple[TomlKey, ...]
    optional: tuple[TomlKey, ...]

    @property
    def named(self) -> Prose:
        """The table's facts as a refusal names them."""
        return Prose(" and ".join(self.required) + "".join(f", optionally {key}" for key in self.optional))


ABSENT = TomlKey("absent")
MACHINE_TABLES: dict[TomlKey, FactTable] = {
    TomlKey("cpus"): FactTable((TomlKey("agents"),), ()),
    TomlKey("slice"): FactTable((TomlKey("name"),), ()),
    TomlKey("memory"): FactTable((TomlKey("call_max"),), (TomlKey("heavy_max"),)),
    TomlKey("records"): FactTable((TomlKey("paths"),), ()),
    TomlKey("rewrite"): FactTable((TomlKey("hook"),), ()),
}
MACHINE_KEYS = frozenset({TomlKey("schema"), *MACHINE_TABLES})


class Machine(NamedTuple):
    """The machine's facts, every path absolute; None or no record where its table declares `absent`."""

    agent_cpus: frozenset[CpuNumber] | None
    slice: SliceName | None
    call_max: ByteCount | None
    heavy_max: ByteCount | None
    records: tuple[Path, ...]
    rewrite_hook: tuple[Word, ...] | None

    @property
    def job_bound(self) -> Count | None:
        """How many jobs a tool sizing itself from the CPU count may start: the agents' CPUs, None where absent."""
        return None if self.agent_cpus is None else Count(len(self.agent_cpus))


class Reading:
    """One declaration file's checks, each refusal naming the file and the key."""

    __slots__ = ("file",)

    def __init__(self, file: Path) -> None:
        self.file = file

    def fault(self, key: TomlKey, says: Prose) -> KitFault:
        """A KitFault naming this file and ``key``."""
        return KitFault(Prose(f"the declaration {self.file} is refused at `{key}`: {says}"))

    def table(self, raw: TomlValue, key: TomlKey, known: frozenset[TomlKey]) -> TomlTable:
        """``raw`` as a table holding no key but ``known``."""
        if not isinstance(raw, dict):
            raise self.fault(key, Prose("it must be a table"))
        unknown = sorted(set(raw) - known)
        if unknown:
            name = f"{key}.{unknown[0]}" if key else unknown[0]
            raise self.fault(TomlKey(name), Prose(f"no such key; the keys here are {', '.join(sorted(known))}"))
        return raw

    def words(self, raw: TomlTable, key: TomlKey, where: TomlKey) -> tuple[Word, ...]:
        """The list of non-empty strings at ``key``, empty where absent."""
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise self.fault(TomlKey(f"{where}.{key}"), Prose("it must be a list of non-empty strings"))
        return tuple(Word(item) for item in value if isinstance(item, str))

    def text(self, raw: TomlTable, key: TomlKey, where: TomlKey) -> TomlText | None:
        """The non-empty string at ``key``, None where absent."""
        value = raw.get(key)
        if value is not None and not (isinstance(value, str) and value):
            raise self.fault(TomlKey(f"{where}.{key}"), Prose("it must be a non-empty string"))
        return value

    def paths(self, raw: TomlTable, key: TomlKey, where: TomlKey, anchors: frozenset[Anchor]) -> tuple[PathText, ...]:
        """The list of paths at ``key``, each in one of the forms ``anchors`` admits."""
        found = tuple(PathText(word) for word in self.words(raw, key, where))
        for path in found:
            if anchor_of(path) not in anchors:
                raise self.fault(TomlKey(f"{where}.{key}"), Prose(f"{path!r} is not {FORMS[anchors]}"))
        return found

    def version(self, raw: TomlTable) -> None:
        """Refuse a file that names no schema, or one this kit does not read."""
        value = raw.get(TomlKey("schema"))
        if type(value) is not int or value != SCHEMA:
            raise self.fault(TomlKey("schema"), Prose(f"it must be {SCHEMA}, the schema this kit reads"))


FORMS: dict[frozenset[Anchor], Prose] = {
    frozenset({"relative", "home"}): Prose("relative, `~/` or absolute"),
    frozenset({"relative"}): Prose("a relative path"),
    frozenset({"home"}): Prose("`~/` or absolute"),
}


def anchor_of(path: PathText) -> Anchor:
    """Whether a path text is resolved against a work tree (`relative`) or stands alone (`home`, `~/` or absolute)."""
    if path.startswith(("/", "~/")) or path == "~":
        return "home"
    return "relative"


def heavy_entry(reading: Reading, raw: TomlValue, index: Count) -> Heavy:
    """One [[heavy]] entry, exactly one matcher set."""
    where = TomlKey(f"heavy[{index}]")
    entry = reading.table(raw, where, HEAVY_KEYS)
    named = [key for key in MATCHERS if key in entry]
    if len(named) != 1:
        raise reading.fault(where, Prose(f"it must set exactly one of {', '.join(MATCHERS)}, not {len(named)}"))
    script = reading.text(entry, TomlKey("script"), where)
    if script is not None and anchor_of(PathText(script)) != "relative":
        raise reading.fault(TomlKey(f"{where}.script"), Prose(f"{script!r} is not {FORMS[frozenset({'relative'})]}"))
    argv = reading.words(entry, TomlKey("argv"), where)
    if "argv" in entry and not argv:
        raise reading.fault(TomlKey(f"{where}.argv"), Prose("it must name at least one word"))
    writes = entry.get("writes", False)
    if not isinstance(writes, bool):
        raise reading.fault(TomlKey(f"{where}.writes"), Prose("it must be true or false"))
    targeted = reading.text(entry, TomlKey("targeted_by"), where)
    if targeted is not None and targeted not in TARGETED_BY:
        raise reading.fault(TomlKey(f"{where}.targeted_by"), Prose(f"it must be one of {', '.join(TARGETED_BY)}"))
    module = reading.text(entry, TomlKey("module"), where)
    program = reading.text(entry, TomlKey("program"), where)
    return Heavy(ModuleName(module) if module else None, PathText(script) if script else None, argv,
                 ProgramName(program) if program else None, writes,
                 reading.words(entry, TomlKey("light_with"), where), reading.words(entry, TomlKey("light_targets"), where),
                 reading.words(entry, TomlKey("verbose_flags"), where), targeted if targeted in TARGETED_BY else None,
                 reading.words(entry, TomlKey("valued_options"), where))


def gate_entry(reading: Reading, raw: TomlValue, name: GateName) -> GateEntry:
    """The gate ``name``, speaking the one protocol the kit knows for it."""
    where = TomlKey(f"gates.{name}")
    keys = GATE_KEYS if name == "precise_hints" else GATE_KEYS - {"suffixes"}
    entry = reading.table(raw, where, frozenset(TomlKey(key) for key in keys))
    run = reading.words(entry, TomlKey("run"), where)
    if not run:
        raise reading.fault(TomlKey(f"{where}.run"), Prose("it must name the gate's command line"))
    protocol = entry.get("protocol")
    if protocol != PROTOCOLS[name]:
        raise reading.fault(TomlKey(f"{where}.protocol"), Prose(f"it must be {PROTOCOLS[name]!r}, the one this kit "
                                                                f"speaks with {name}"))
    cwd = reading.text(entry, TomlKey("cwd"), where)
    as_from = reading.text(entry, TomlKey("as_from"), where)
    source = reading.text(entry, TomlKey("rule_source"), where)
    return GateEntry(run, PathText(cwd) if cwd else None, PathText(as_from) if as_from else None, PROTOCOLS[name],
                     Prose(source or ""),
                     tuple(Suffix(word) for word in reading.words(entry, TomlKey("suffixes"), where)))


def project_entries(raw: TomlTable, file: Path) -> ProjectEntries:
    """A repository's declaration, every key checked."""
    reading = Reading(file)
    top = reading.table(raw, TomlKey(""), frozenset(TomlKey(key) for key in PROJECT_KEYS))
    reading.version(top)
    records = reading.table(top.get(TomlKey("records"), {}), TomlKey("records"), frozenset({TomlKey("paths")}))
    bulk = reading.table(top.get(TomlKey("bulk"), {}), TomlKey("bulk"), frozenset({TomlKey("paths")}))
    unread = reading.table(top.get(TomlKey("unread_scripts"), {}), TomlKey("unread_scripts"),
                           frozenset({TomlKey("paths")}))
    probes = reading.table(top.get(TomlKey("probes"), {}), TomlKey("probes"), frozenset({TomlKey("record")}))
    record = reading.text(probes, TomlKey("record"), TomlKey("probes"))
    heavy = top.get(TomlKey("heavy"), [])
    if not isinstance(heavy, list):
        raise reading.fault(TomlKey("heavy"), Prose("it must be an array of tables, [[heavy]]"))
    gates = reading.table(top.get(TomlKey("gates"), {}), TomlKey("gates"), frozenset(TomlKey(name) for name in PROTOCOLS))
    determinism, hints = (gate_entry(reading, gates[TomlKey(name)], name) if name in gates else None
                          for name in PROTOCOLS)
    return ProjectEntries(
        reading.paths(records, TomlKey("paths"), TomlKey("records"), frozenset({"relative", "home"})),
        reading.paths(bulk, TomlKey("paths"), TomlKey("bulk"), frozenset({"relative"})),
        tuple(heavy_entry(reading, entry, Count(index)) for index, entry in enumerate(heavy, 1)), determinism, hints,
        reading.paths(unread, TomlKey("paths"), TomlKey("unread_scripts"), frozenset({"relative"})),
        PathText(record) if record else None)


def cpu_list(reading: Reading, text: TomlText | None, key: TomlKey) -> frozenset[CpuNumber] | None:
    """The CPUs a list like `0-19,22` names, None where absent."""
    if text is None:
        return None
    found: set[CpuNumber] = set()
    for part in text.split(","):
        low, dash, high = part.partition("-")
        if not low.isdecimal() or (dash and not high.isdecimal()) or (dash and int(high) < int(low)):
            raise reading.fault(key, Prose(f"{text!r} is not a CPU list like 0-19,22"))
        found.update(CpuNumber(cpu) for cpu in range(int(low), int(high if dash else low) + 1))
    return frozenset(found)


def size(reading: Reading, text: TomlText | None, key: TomlKey) -> ByteCount | None:
    """The bytes a size like `24G` names, None where absent."""
    if text is None:
        return None
    digits, unit = (text[:-1], text[-1:]) if text[-1:] in SIZE_UNITS else (text, "")
    if not digits.isdecimal() or not int(digits):
        raise reading.fault(key, Prose(f"{text!r} is not a size like 512M or 24G"))
    return ByteCount(int(digits) * SIZE_UNITS[unit])


def fact_table(reading: Reading, top: TomlTable, key: TomlKey) -> TomlTable:
    """The machine table ``key`` holding its facts, or empty where it declares `absent` alone with a reason."""
    facts = MACHINE_TABLES[key]
    if key not in top:
        raise reading.fault(key, Prose(f'the table is missing; it holds {facts.named}, or `absent = "<why>"` alone '
                                       "naming why this machine has none"))
    table = reading.table(top[key], key, frozenset({*facts.required, *facts.optional, ABSENT}))
    if ABSENT not in table:
        for name in facts.required:
            if name not in table:
                raise reading.fault(TomlKey(f"{key}.{name}"), Prose('it must be declared, or the table holds '
                                                                    '`absent = "<why>"` alone'))
        return table
    beside = sorted(set(table) - {ABSENT})
    if beside:
        raise reading.fault(TomlKey(f"{key}.absent"), Prose(f"it stands alone, not beside {', '.join(beside)}"))
    reason = table[ABSENT]
    if not isinstance(reason, str) or not reason.strip():
        raise reading.fault(TomlKey(f"{key}.absent"), Prose("it must be a string naming why this machine has none"))
    return {}


def machine_facts(raw: TomlTable, file: Path, home: Path) -> Machine:
    """The machine's declaration, every key checked and every path made absolute."""
    reading = Reading(file)
    top = reading.table(raw, TomlKey(""), MACHINE_KEYS)
    reading.version(top)
    cpus = fact_table(reading, top, TomlKey("cpus"))
    agents = cpu_list(reading, reading.text(cpus, TomlKey("agents"), TomlKey("cpus")), TomlKey("cpus.agents"))
    slice_table = fact_table(reading, top, TomlKey("slice"))
    name = reading.text(slice_table, TomlKey("name"), TomlKey("slice"))
    if name is not None and not name.endswith(".slice"):
        raise reading.fault(TomlKey("slice.name"), Prose(f"{name!r} is not a systemd slice's name"))
    memory = fact_table(reading, top, TomlKey("memory"))
    call = size(reading, reading.text(memory, TomlKey("call_max"), TomlKey("memory")), TomlKey("memory.call_max"))
    heavy = size(reading, reading.text(memory, TomlKey("heavy_max"), TomlKey("memory")), TomlKey("memory.heavy_max"))
    if call is not None and heavy is not None and heavy < call:
        raise reading.fault(TomlKey("memory.heavy_max"), Prose("it must be at least memory.call_max, which caps "
                                                               "every other call"))
    records = fact_table(reading, top, TomlKey("records"))
    paths = reading.paths(records, TomlKey("paths"), TomlKey("records"), frozenset({"home"}))
    if records and not paths:
        raise reading.fault(TomlKey("records.paths"), Prose('it must name a folder, or the table holds '
                                                            '`absent = "<why>"` alone'))
    rewrite = fact_table(reading, top, TomlKey("rewrite"))
    hook = reading.words(rewrite, TomlKey("hook"), TomlKey("rewrite"))
    if rewrite and not hook:
        raise reading.fault(TomlKey("rewrite.hook"), Prose("it must name the hook's command line, or the table holds "
                                                           '`absent = "<why>"` alone'))
    if hook and "/" in hook[0] and not hook[0].startswith("/"):
        raise reading.fault(TomlKey("rewrite.hook"), Prose(f"{hook[0]!r} is neither an absolute path nor a name found "
                                                           "on PATH"))
    return Machine(agents, SliceName(name) if name else None, call, call if heavy is None else heavy,
                   tuple(at_home(path, home) for path in paths), hook or None)


def at_home(path: PathText, home: Path) -> Path:
    """An absolute or `~/` path text as an absolute path."""
    return Path(path) if path.startswith("/") else home / path.removeprefix("~").lstrip("/")
