#!/home/nicolas/.local/bin/python3.14
"""Hold a commit dribble to a run of every entry point whose change it commits.

THE RULE (user, 2026-10-02): what an entry point prints (a summary, a refusal,
its help) is a claim about what the code does, and a change can make it false
without touching it. So before a dribble that commits is handed over, every
entry point the commit changes has been run, after its last edit, and its whole
output read.

WHY A HOOK AND NOT A NOTE. The rule was a memory ("run the adjacency pass,
executing each claim"), and a change to the pre-push hook still shipped with
the installer printing "every `git push` will run `tools/run_ci.py` first",
the behaviour the change had just replaced. Tests, probes, a sweep and a
review had all passed; nobody had run the installer. This hook makes the run
a condition the session cannot forget.

WHEN IT LOOKS. After every Bash, Edit, Write or MultiEdit call whose working
directory is in a git work tree holding a pending commit dribble: a
.commands-to-run.sh at the root that holds a `git commit` (or `commit-tree`)
line and is newer than HEAD's commit. Looking on every call, not only when the
dribble is written, catches an edit made after the dribble.

WHAT IT NEEDS RUN. Every entry point among the uncommitted changes (modified,
added or untracked; a deletion is not one): a .py file holding an
`if __name__ == "__main__":` guard, or an executable file starting with `#!`.
A file under a `tests` directory is run by its suite and left out.

WHAT COUNTS AS A RUN. This session's transcript is the evidence: each Bash
command the session issued, as the model wrote it, with the time it was issued
and the directory it ran in. A command runs an entry point when one of its
simple commands, after keywords, assignments and wrappers (env, taskset,
timeout, nice, time, command, exec, nohup) are set aside, is the entry point's
path (when the file is executable: the shell refuses one that is not, and
nothing ran), an interpreter given its path (python, bash, sh), or
`python -m` its module. Heredoc bodies are text, not commands; `cd` and plain `NAME=value`
assignments are followed; a program spelled through an unknown variable, such
as a loop's `"$p"`, is not resolved. Arguments of only `-h` or `--help` read
the help and do not count, nor does `python -c`. The run must be issued after
the file's last modification.

A probe stands in for an entry point when the probe ran after the entry
point's last edit (by its path, or through probes/run_all.sh) and the probe's
own text, read the same way from the repository root, runs the entry point.
A probe that imports the module, renders a constant or names the path does not
run it: that is how the installer's stale summary passed its probe.

WHAT IT SAYS. Exit 2 with the entry points not run and how each is run: the
call stands, and the message reaches the model at once. The same finding for
the same dribble is not repeated. State (where the transcript was last read,
the runs found, the last finding) lives in the work tree's git directory, so
the transcript is read once, incrementally.

An error of the hook's own is reported and the call goes ahead, as the other
guards here do: a hook that fails must never block the session silently.
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, cast

import _shell

# A path as an event or a command spells it; one relative to its repository; a
# Python module's dotted name; a shell command line, or a word of one; a shell
# variable's name; an argument handed to git and what git prints; a transcript
# entry's ISO timestamp; prose the hook prints; seconds since the epoch; a byte
# offset into the transcript; a finding's identity; the hook's answer: 0 lets
# the call through, 2 reports a finding, 1 reports an error of the hook's own.
FilePath = NewType("FilePath", str)
RepoPath = NewType("RepoPath", str)
ModuleName = NewType("ModuleName", str)
CommandLine = NewType("CommandLine", str)
Word = NewType("Word", str)
VarName = NewType("VarName", str)
GitArgument = NewType("GitArgument", str)
GitOutput = NewType("GitOutput", str)
Stamp = NewType("Stamp", str)
Prose = NewType("Prose", str)
Instant = NewType("Instant", float)
Offset = NewType("Offset", int)
FindingKey = NewType("FindingKey", str)
ExitStatus = NewType("ExitStatus", int)
ALLOW, REPORTED, REFUSE = ExitStatus(0), ExitStatus(1), ExitStatus(2)

type Tool = Literal["Read", "Edit", "Write", "MultiEdit", "Bash"]
type RunKind = Literal["program", "script", "module"]
type Variables = dict[VarName, Word]


class Event(TypedDict, total=False):
    """The event the harness hands the hook."""

    hook_event_name: Literal["PostToolUse"]
    tool_name: Tool
    cwd: FilePath
    transcript_path: FilePath


class BashInput(TypedDict, total=False):
    """A Bash call's input, as far as the hook reads it."""

    command: CommandLine


class Block(TypedDict, total=False):
    """One block of an assistant message: the hook reads the Bash tool calls."""

    type: Literal["tool_use", "text", "thinking"]
    name: Tool
    input: BashInput


class Message(TypedDict, total=False):
    """An assistant message's content."""

    content: list[Block]


class Entry(TypedDict, total=False):
    """One transcript line, as far as the hook reads it."""

    timestamp: Stamp
    cwd: FilePath
    message: Message


class Terminator(NamedTuple):
    """A heredoc's closing word, and whether its lines may carry leading tabs (`<<-`)."""

    word: Word
    strips_tabs: bool


class Run(NamedTuple):
    """One entry point a command ran: a path run as the program, a path handed to an
    interpreter (a script), or a module with the directory it ran from."""

    kind: RunKind
    target: FilePath
    cwd: FilePath


class Issued(NamedTuple):
    """A run, and the time its command was issued."""

    run: Run
    at: Instant


class Needed(NamedTuple):
    """An entry point the commit changes, and the module names it runs under."""

    rel: RepoPath
    path: Path
    modules: frozenset[ModuleName]


class State(TypedDict):
    """What the hook keeps between calls, in the work tree's git directory."""

    transcript: FilePath
    offset: Offset
    runs: list[tuple[RunKind, FilePath, FilePath, Instant]]
    reported: FindingKey


DRIBBLE = ".commands-to-run.sh"
STATE_NAME = "entry-point-run-guard.json"
PROBES = "probes"
PROBE_RUNNER = "run_all.sh"
GIT_SECONDS = 30
# A dribble line that commits: `git commit`, `git -c k=v commit`, `git commit-tree`.
COMMITS = re.compile(r"^[ \t]*(?:\w+=\S*[ \t]+)*git(?:[ \t]+-[cC][ \t]+\S+)*[ \t]+commit(?:-tree)?\b",
                     re.MULTILINE)
MAIN_GUARD = re.compile(r"""^if __name__ == ['"]__main__['"]\s*:""", re.MULTILINE)
PYTHON = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")
SHELLS = frozenset({"bash", "sh", "dash", "zsh", "fish"})
KEYWORDS = frozenset({"if", "then", "elif", "else", "do", "while", "until", "!", "{", "}"})
PLAIN_WRAPPERS = frozenset({"command", "exec", "nohup", "time"})
HELP = frozenset({"-h", "--help"})
# Python options that take a value, so the value is not read as the script.
PYTHON_VALUED = frozenset({"-W", "-X", "-Q"})
ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.DOTALL)
VARIABLE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")
HEREDOC = re.compile(r"(?<!<)<<(-?)[ \t]*(['\"]?)([A-Za-z_]\w*)\2(?!<)")
PUNCTUATION = ";&|()<>\n"
FINDING = (
    "entry-point-run-guard: the commit dribble ({dribble}) commits changes to entry points\n"
    "this session has not run since their last edit:\n{listing}\n"
    "Run each one and read its whole output before the dribble is handed over: what an\n"
    "entry point prints (a summary, a message, its help) can describe behaviour the change\n"
    "made false, and only running it shows that. A run counts when a command names the\n"
    "entry point itself (`python -m MODULE`, `python PATH`, `PATH`, `bash PATH`; not\n"
    "`--help` alone, not a loop variable such as `\"$p\"`), or runs a probe whose own text\n"
    "runs it. The dribble stands."
)


# Reading commands -----------------------------------------------------------


def without_heredocs(text: CommandLine) -> CommandLine:
    """Drop every heredoc's body and terminator: they are a command's input, not commands."""
    kept: list[CommandLine] = []
    pending: list[Terminator] = []
    for line in text.split("\n"):
        if pending:
            closing = pending[0]
            if (line.lstrip("\t") if closing.strips_tabs else line) == closing.word:
                pending.pop(0)
            continue
        kept.append(CommandLine(line))
        pending += [Terminator(Word(m.group(3)), m.group(1) == "-") for m in HEREDOC.finditer(line)]
    return CommandLine("\n".join(kept))


def words(text: CommandLine) -> list[Word] | None:
    """Split a command line into words and operators, or None where it cannot be lexed."""
    lexer = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return [Word(token) for token in lexer]
    except ValueError:
        return None


def is_operator(word: Word) -> bool:
    return bool(word) and all(ch in PUNCTUATION for ch in word)


def simple_commands(tokens: list[Word]) -> list[list[Word]]:
    """Group words into simple commands, dropping redirections and their targets."""
    commands: list[list[Word]] = [[]]
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if is_operator(token):
            if "<" in token or ">" in token:
                skip = True  # the redirection's target follows
            else:
                commands.append([])
            continue
        commands[-1].append(token)
    return [command for command in commands if command]


def expand(word: Word, known: Variables) -> Word | None:
    """Substitute the variables a command set; None when the word uses one it did not."""
    out: list[Word] = []
    position = 0
    for match in VARIABLE.finditer(word):
        name = VarName(match.group(1) or match.group(2))
        if name not in known:
            return None
        out += [Word(word[position:match.start()]), known[name]]
        position = match.end()
    out.append(Word(word[position:]))
    return Word("".join(out))


def strip_wrappers(command: list[Word]) -> list[Word]:
    """Set aside keywords, assignments and the wrappers that run another program."""
    rest = list(command)
    while rest:
        head = rest[0]
        if head in KEYWORDS or head in PLAIN_WRAPPERS or ASSIGNMENT.match(head):
            rest = rest[1:]
        elif head == "env":
            rest = rest[1:]
            while rest and (rest[0].startswith("-") or ASSIGNMENT.match(rest[0])):
                rest = rest[1:]
        elif head == "taskset":
            rest = rest[3:] if len(rest) > 1 and rest[1].startswith("-") else rest[2:]
        elif head == "nice":
            rest = rest[3:] if len(rest) > 1 and rest[1] == "-n" else rest[1:]
        elif head == "timeout":
            rest = rest[1:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in ("-s", "-k", "--signal", "--kill-after") else rest[1:]
            rest = rest[1:]  # the duration
        else:
            break
    return rest


def resolve(cwd: FilePath, raw: Word) -> FilePath:
    return FilePath(os.path.normpath(os.path.join(cwd, os.path.expanduser(raw))))


def help_only(arguments: list[Word]) -> bool:
    return bool(arguments) and all(argument in HELP for argument in arguments)


def run_of(program: list[Word], cwd: FilePath) -> Run | None:
    """Name the entry point one simple command runs, or None when it runs none."""
    name = Path(program[0]).name
    arguments = program[1:]
    if PYTHON.match(name):
        index = 0
        while index < len(arguments):
            argument = arguments[index]
            if argument == "-m" and index + 1 < len(arguments):
                rest = arguments[index + 2:]
                return None if help_only(rest) else Run("module", FilePath(arguments[index + 1]), cwd)
            if argument == "-c":
                return None
            if argument in PYTHON_VALUED:
                index += 2
                continue
            if argument.startswith("-"):
                index += 1
                continue
            rest = arguments[index + 1:]
            return None if help_only(rest) else Run("script", resolve(cwd, argument), cwd)
        return None
    if name in SHELLS:
        scripts = [index for index, argument in enumerate(arguments) if not argument.startswith("-")]
        if not scripts or "-c" in arguments[: scripts[0]]:
            return None
        rest = arguments[scripts[0] + 1:]
        return None if help_only(rest) else Run("script", resolve(cwd, arguments[scripts[0]]), cwd)
    if "/" in program[0]:
        return None if help_only(arguments) else Run("program", resolve(cwd, program[0]), cwd)
    return None


def runs_in(text: CommandLine, cwd: FilePath, known: Variables | None = None) -> list[Run]:
    """List the entry points a command line runs, from the directory it runs in."""
    tokens = words(without_heredocs(text))
    if tokens is None:
        return []
    variables: Variables = {VarName("HOME"): Word(str(Path.home())), VarName("PWD"): Word(cwd),
                            **(known or {})}
    found: list[Run] = []
    here = cwd
    for command in simple_commands(tokens):
        matches = [ASSIGNMENT.match(word) for word in command]
        if all(matches):
            for match in matches:
                value = expand(Word(match.group(2)), variables) if match is not None else None
                if match is not None and value is not None:
                    variables[VarName(match.group(1))] = value
            continue
        stripped = strip_wrappers(command)
        program = [expand(word, variables) for word in stripped]
        if not program or program[0] is None:
            continue
        words_known = [word if word is not None else Word("") for word in program]
        if words_known[0] == "export":
            for word in words_known[1:]:
                match = ASSIGNMENT.match(word)
                if match is not None:
                    variables[VarName(match.group(1))] = Word(match.group(2))
            continue
        if words_known[0] == "cd":
            target = words_known[1] if len(words_known) > 1 else Word(str(Path.home()))
            if program[1:2] != [None] and target and target != "-":
                here = resolve(here, target)
                variables[VarName("PWD")] = Word(here)
            continue
        run = run_of(words_known, here)
        if run is not None:
            found.append(run)
    return found


# The transcript --------------------------------------------------------------


def instant(stamp: Stamp) -> Instant | None:
    try:
        return Instant(datetime.fromisoformat(stamp).timestamp())
    except ValueError:
        return None


def issued_in(entry: Entry) -> list[Issued]:
    """List the runs of every Bash call one transcript entry issues."""
    stamp, cwd, message = entry.get("timestamp"), entry.get("cwd"), entry.get("message")
    at = instant(stamp) if isinstance(stamp, str) else None
    content = message.get("content") if isinstance(message, dict) else None
    if at is None or not isinstance(cwd, str) or not isinstance(content, list):
        return []
    found: list[Issued] = []
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash"):
            continue
        request = block.get("input")
        command = request.get("command") if isinstance(request, dict) else None
        if isinstance(command, str):
            found += [Issued(run, at) for run in runs_in(command, cwd)]
    return found


def read_transcript(path: Path, offset: Offset) -> tuple[list[Issued], Offset]:
    """Read the Bash commands issued after ``offset``; return their runs and where reading stopped."""
    issued: list[Issued] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        position = offset
        for raw in handle:
            if not raw.endswith(b"\n"):
                break  # a line still being written is read next time
            position = Offset(position + len(raw))
            if b'"tool_use"' not in raw or b'"Bash"' not in raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                issued += issued_in(cast("Entry", entry))
    return issued, position


# The repository --------------------------------------------------------------


def repository_of(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def git(repo: Path, *arguments: GitArgument) -> GitOutput:
    done = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, text=True,
                          check=False, timeout=GIT_SECONDS)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)} failed: {done.stderr.strip()[-300:]}")
    return GitOutput(done.stdout)


def head_time(repo: Path) -> Instant | None:
    """The time of HEAD's commit, or None before the first one."""
    done = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct"], capture_output=True,
                          text=True, check=False, timeout=GIT_SECONDS)
    try:
        return Instant(float(done.stdout.strip())) if done.returncode == 0 else None
    except ValueError:
        return None


def changed_files(repo: Path) -> list[RepoPath]:
    """List the uncommitted paths that still exist: modified, added or untracked."""
    listed = git(repo, GitArgument("status"), GitArgument("--porcelain=v1"), GitArgument("-z"),
                 GitArgument("--untracked-files=all")).split("\0")
    paths: list[RepoPath] = []
    entries = iter(listed)
    for entry in entries:
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            next(entries, None)  # a rename or a copy lists its old path next
        if "D" in entry[:2]:
            continue
        paths.append(RepoPath(entry[3:]))
    return paths


def module_names(repo: Path, path: Path) -> frozenset[ModuleName]:
    """Name the module a .py file runs as: dotted from the top of its package chain."""
    if path.suffix != ".py":
        return frozenset()
    root = path.parent
    while (root / "__init__.py").is_file() and root != repo:
        root = root.parent
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__main__":
        parts = parts[:-1]
    return frozenset({ModuleName(".".join(parts))}) if parts else frozenset()


def entry_point(repo: Path, rel: RepoPath) -> Needed | None:
    path = repo / rel
    if "tests" in Path(rel).parts or not path.is_file():
        return None
    try:
        if path.suffix == ".py":
            if not MAIN_GUARD.search(path.read_text(encoding="utf-8", errors="replace")):
                return None
        else:
            with path.open("rb") as handle:
                if handle.read(2) != b"#!" or not os.access(path, os.X_OK):
                    return None
    except OSError:
        return None
    return Needed(rel, path, module_names(repo, path))


def runs_entry(run: Run, need: Needed, repo: Path) -> bool:
    """Say whether a run ran the entry point. A path run as the program ran only if
    the file is executable: otherwise the shell refused it ("permission denied")."""
    if run.kind != "module":
        same = os.path.realpath(run.target) == os.path.realpath(need.path)
        return same and (run.kind == "script" or os.access(need.path, os.X_OK))
    if ModuleName(run.target) not in need.modules:
        return False
    here = Path(os.path.realpath(run.cwd))
    root = Path(os.path.realpath(repo))
    return here == root or root in here.parents


def probe_runs(repo: Path, probe: Path) -> list[Run]:
    """Read a probe as the commands it runs, from the repository root."""
    try:
        text = CommandLine(probe.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    return runs_in(text, FilePath(str(repo)), {VarName("0"): Word(str(probe))})


def ran(need: Needed, repo: Path, issued: list[Issued], edited: Instant) -> bool:
    """Say whether the entry point, or a probe that runs it, was run after ``edited``."""
    after = [each.run for each in issued if each.at > edited]
    if any(runs_entry(run, need, repo) for run in after):
        return True
    probes = Path(os.path.realpath(repo / PROBES))
    probed = {Path(os.path.realpath(run.target)) for run in after if run.kind != "module"}
    if probes / PROBE_RUNNER in probed:
        candidates = sorted(probes.glob("*.sh"))
    else:
        candidates = sorted(path for path in probed if path.parent == probes)
    return any(runs_entry(run, need, repo) for probe in candidates for run in probe_runs(repo, probe))


# State -----------------------------------------------------------------------


def state_path(repo: Path) -> Path:
    return Path(git(repo, GitArgument("rev-parse"), GitArgument("--absolute-git-dir")).strip()) / STATE_NAME


def load_state(path: Path, transcript: FilePath) -> State:
    fresh = State(transcript=transcript, offset=Offset(0), runs=[], reported=FindingKey(""))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fresh
    if not isinstance(data, dict) or data.get("transcript") != transcript:
        return fresh
    return cast("State", data)


def save_state(path: Path, state: State) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def latest(issued: list[Issued]) -> list[Issued]:
    """Keep each run once, at its latest issue."""
    newest: dict[Run, Instant] = {}
    for each in issued:
        if each.run not in newest or each.at > newest[each.run]:
            newest[each.run] = each.at
    return [Issued(run, at) for run, at in newest.items()]


# The check -------------------------------------------------------------------


def how_to_run(need: Needed) -> Prose:
    return Prose(f"python -m {min(need.modules)}" if need.modules else need.rel)


def after_tool(event: Event) -> ExitStatus:
    raw_cwd = event.get("cwd")
    cwd = Path(raw_cwd) if isinstance(raw_cwd, str) else Path.cwd()
    repo = repository_of(cwd)
    if repo is None or not (repo / DRIBBLE).is_file():
        return ALLOW
    dribble = repo / DRIBBLE
    committed = head_time(repo)
    pending = committed is None or dribble.stat().st_mtime > committed
    if not pending or not COMMITS.search(dribble.read_text(encoding="utf-8", errors="replace")):
        return ALLOW
    needed = [n for n in (entry_point(repo, rel) for rel in changed_files(repo)) if n is not None]
    if not needed:
        return ALLOW
    raw_transcript = event.get("transcript_path")
    transcript = FilePath(raw_transcript) if isinstance(raw_transcript, str) else FilePath("")
    where = state_path(repo)
    state = load_state(where, transcript)
    known = [Issued(Run(kind, target, run_cwd), at) for kind, target, run_cwd, at in state["runs"]]
    if transcript and Path(transcript).is_file():
        new, offset = read_transcript(Path(transcript), state["offset"])
        known = latest(known + new)
        state["offset"] = offset
        state["runs"] = [(each.run.kind, each.run.target, each.run.cwd, each.at) for each in known]
    unrun = sorted((need for need in needed if not ran(need, repo, known, Instant(need.path.stat().st_mtime))),
                   key=lambda need: need.rel)
    if not unrun:
        state["reported"] = FindingKey("")
        save_state(where, state)
        return ALLOW
    names = "\n".join(need.rel for need in unrun)
    key = FindingKey(hashlib.sha256(f"{dribble.stat().st_mtime_ns}\n{names}".encode()).hexdigest())
    repeated = key == state["reported"]
    state["reported"] = key
    save_state(where, state)
    if repeated:
        return ALLOW
    listing = "\n".join(f"  {need.rel}  ({how_to_run(need)})" for need in unrun)
    print(FINDING.format(dribble=DRIBBLE, listing=listing), file=sys.stderr)
    return REFUSE


def main() -> ExitStatus:
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return ALLOW  # a hook that cannot read the event must not block the session
    if not isinstance(event, dict) or event.get("hook_event_name") not in ("PostToolUse", None):
        return ALLOW
    if event.get("tool_name") not in ("Bash", "Edit", "Write", "MultiEdit"):
        return ALLOW
    try:
        return after_tool(cast("Event", event))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"entry-point-run-guard: the call went unchecked: {exc}", file=sys.stderr)
        return REPORTED


if __name__ == "__main__":
    sys.exit(main())
