"""What a call runs, with its wrappers peeled off, and which of it is a heavy run.

Shared by heavy-run-guard.py and no-polling-loops.py, so both read a call the
same way.  `expand()` flattens a call into invocations: every simple command
`_shell` reads, with the text a carrier runs (bash/sh/zsh/fish -c, eval,
script -c, watch, flock -c, tmux, screen, find -exec once per root) read as
more of the call, and a script the call runs (by path, `bash f`, from stdin,
including one the same call wrote just before from a heredoc, echo or
printf) read too, with its words as $0, $1 ...; a sourced file is read in
place by `_shell`.  A background carrier's or script's commands belong to its
process.  Each text is read in the dialect of the shell that runs it: the
call's own is the tool's zsh, `bash -c` and a bash script bash, `sh` and a
script with no shebang sh, eval and source their caller's, flock -c and
script -c the call's $SHELL (the tool shell's zsh unless the call sets one).  A script run the
same way twice is read once, and any text is parsed and charged once however
often it runs; a call past `_shell.MAX_COMMANDS` commands or
`_shell.MAX_LEXED` bytes, its Python-started commands included, is
unreadable.  A copy the call makes carries its source's text and, for a
copy or an edit of every line (cp; cat, tee and tr reading a file or a
`cat FILE` pipe; sed without -n, perl -p; git show REV:PATH; a head, tail or
sed -n that selects every line), its name: a copy of a heavy runner is that
runner, until the call writes something else over it; lines a filter selects
are read as the text they are.  Under xargs the operands come from where its
-a or `<` list, the find or git piped into it, put them, and from nowhere
known otherwise.  Each invocation knows where it came from: the call's own text,
a script outside every git tree or written by the call (held to the call's
rules), or a repository's own script (read for the writes it makes; one that
runs a heavy command makes the invocation running it heavy, so the call is
held to rules 1-3 for it).  An interpreter's code (inline, from stdin, piped
in, its script file, or its `-m` module's source, past the options that take
a value) is attached to its invocation with whose it is, as the origins read,
and a Python driver that starts a heavy run through subprocess or os.system
counts as that run.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import stat
from pathlib import Path
from dataclasses import dataclass, field

import _python
import _shell

SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
INTERPRETERS = {"perl", "ruby", "node"}
SCRIPT_READ_LIMIT = 256 * 1024
MAX_DEPTH = 4
BINARY = "\0binary"  # read_script's answer for a binary

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.S)
_VERSION_SUFFIX = re.compile(r"-\d+(\.\d+)*$")


def prog(word: str) -> str:
    """A command word's program: its basename, less a version suffix (mull-runner-23)."""
    return _VERSION_SUFFIX.sub("", os.path.basename(word))


def is_python(word: str) -> bool:
    return re.fullmatch(r"python[\d.]*", os.path.basename(word)) is not None


def read_script(path: str | None) -> str | None:
    """A regular file's text; BINARY for a binary; None when it is missing, not a file, or too large."""
    if not path:
        return None
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return None
        with open(path, "rb") as fh:
            head = fh.read(4096)
            if b"\0" in head or head.startswith(b"\x7fELF"):
                return BINARY
            if st.st_size > SCRIPT_READ_LIMIT:
                return None
            return (head + fh.read(SCRIPT_READ_LIMIT)).decode("utf-8", errors="replace")
    except (OSError, ValueError, UnicodeError):
        return None


def read_regular(path: str | None) -> str | None:
    text = read_script(path)
    return None if text == BINARY else text


def resolve(path: str | None, cwd: str | None) -> str | None:
    if not path or "\0" in path:
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)
    if cwd is None:
        return None
    return os.path.normpath(os.path.join(cwd, path))


# ─── wrappers ─────────────────────────────────────────────────────────────────


@dataclass
class Core:
    """A command with its wrappers peeled off."""

    argv: list[str]
    cpus: frozenset[int] | None = None
    bad_taskset: str | None = None
    script: str | None = None  # shell text the command runs: bash -c, eval, script -c, watch, tmux ...
    script_lang: str = "sh"  # "fish" for fish -c
    script_file: str | None = None  # a shell script file it runs or sources
    inner: list[list[str]] = field(default_factory=list)  # find -exec commands
    via_xargs: bool = False  # more operands arrive on stdin
    detached: str | None = None  # the wrapper that detaches it from the call: setsid -f, tmux -d ...
    chdir: str | None = None  # env -C DIR
    no_exec: bool = False  # bash -n: reads, runs nothing
    params: list[str] | None = None  # $0, $1 ... of the text or script it runs, where it names them
    cpu_sets: list[tuple[frozenset[int] | None, str]] = field(default_factory=list)  # taskset/numactl it runs under


_CPU_CAP = 64  # CPUs past this are recorded as one: they are outside 0-19 either way


def parse_cpu_list(text: str) -> frozenset[int] | None:
    cpus: set[int] = set()
    for part in text.split(","):
        m = re.fullmatch(r"(\d{1,9})(?:-(\d{1,9})(?::(\d{1,9}))?)?", part.strip())
        if not m:
            return None
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        step = int(m.group(3)) if m.group(3) else 1
        if hi < lo or step < 1:
            return None
        cpus.update(range(lo, min(hi, _CPU_CAP) + 1, step))
        if hi > _CPU_CAP:
            cpus.add(hi)
    return frozenset(cpus) if cpus else None


def parse_cpu_mask(text: str) -> frozenset[int] | None:
    t = text.lower().replace(",", "")
    t = t[2:] if t.startswith("0x") else t
    if not re.fullmatch(r"[0-9a-f]{1,4096}", t):
        return None
    bits = int(t, 16)
    cpus = {i for i, b in enumerate(reversed(bin(bits)[2:][-(_CPU_CAP + 1):])) if b == "1"}
    if bits >> (_CPU_CAP + 1):
        cpus.add(bits.bit_length() - 1)
    return frozenset(cpus) or None


def _taskset(argv: list[str], i: int) -> tuple[frozenset[int] | None, int, str | None]:
    """Parse taskset's options from argv[i]: (cpus, index of the command, what was unreadable)."""
    cpu_list: str | None = None
    while i < len(argv):
        w = argv[i]
        if w == "--":
            i += 1
            break
        if w in ("-p", "--pid", "-h", "--help", "-V", "--version"):
            return None, len(argv), None  # a running pid's affinity, help or the version: no command
        if w in ("-c", "--cpu-list"):
            if i + 1 >= len(argv):
                return None, len(argv), "no CPU list"
            cpu_list, i = argv[i + 1], i + 2
            continue
        if w.startswith("--cpu-list="):
            cpu_list, i = w.split("=", 1)[1], i + 1
            continue
        if re.fullmatch(r"-[a-zA-Z]+", w):
            flags = w[1:]
            if "p" in flags:
                return None, len(argv), None
            if flags.endswith("c"):
                if i + 1 >= len(argv):
                    return None, len(argv), "no CPU list"
                cpu_list, i = argv[i + 1], i + 2
                continue
            i += 1
            continue
        if w.startswith("-c") and len(w) > 2:
            cpu_list, i = w[2:], i + 1
            continue
        if w.startswith("-"):
            i += 1
            continue
        break
    if cpu_list is not None:
        cpus = parse_cpu_list(cpu_list)
        if cpus is None:
            computed = "$" in cpu_list or "(" in cpu_list
            return None, i, (f"the CPU list `{cpu_list[:40]}` is computed when it runs" if computed
                             else f"the CPU list `{cpu_list[:40]}` could not be read")
        return cpus, i, None
    if i >= len(argv):
        return None, i, "no CPU mask"
    cpus = parse_cpu_mask(argv[i])
    return cpus, i + 1, None if cpus is not None else f"the CPU mask `{argv[i][:40]}` could not be read"


def _claim_at(argv: list[str], i: int) -> list[tuple[frozenset[int] | None, str]]:
    """The CPU set the taskset or numactl in command position at argv[i] sets, on a command or a running pid.

    Each is (the CPUs, or None when the list cannot be read, and the text naming them).  Reading a pid's
    affinity, or asking for help or the version (`_taskset` stops there), sets nothing.
    """
    name = prog(argv[i])
    rest = argv[i + 1 :]
    if name == "taskset":
        opts, k = [], 0
        while k < len(rest) and rest[k].startswith("-") and rest[k] != "--":
            opts.append(rest[k])
            k += 1
        flags = "".join(o[1:] for o in opts if not o.startswith("--"))
        longs = {o.split("=", 1)[0] for o in opts if o.startswith("--")}
        if "p" in flags or "--pid" in longs:
            operands = rest[k + (k < len(rest) and rest[k] == "--") :]
            if len(operands) < 2:
                return []  # `taskset -p PID` reads an affinity
            listed = "c" in flags or "--cpu-list" in longs
            text = operands[0]
            return [((parse_cpu_list(text) if listed else parse_cpu_mask(text)), text)]
        cpus, _, bad = _taskset(argv, i + 1)
        return [(cpus, " ".join(rest[:2]))] if bad or cpus is not None else []
    claims: list[tuple[frozenset[int] | None, str]] = []
    for k, a in enumerate(rest):  # numactl
        text = (rest[k + 1] if a in ("-C", "--physcpubind") and k + 1 < len(rest) else
                a.split("=", 1)[1] if a.startswith("--physcpubind=") else
                a[2:] if a.startswith("-C") and len(a) > 2 else None)
        if text is not None:
            claims.append((parse_cpu_list(text), text))
        if not a.startswith("-"):
            break
    return claims


def _skip_options(argv: list[str], i: int, with_value: set[str]) -> int:
    """Index of the first operand from argv[i], stepping over options (and their values)."""
    while i < len(argv):
        w = argv[i]
        if w == "--":
            return i + 1
        if not w.startswith("-") or w == "-":
            return i
        # A value follows its option, or a cluster whose last letter takes one (`-ds NAME`, `-dmS NAME`).
        cluster_value = re.fullmatch(r"-[a-zA-Z]{2,}", w) is not None and "-" + w[-1] in with_value
        i += 2 if w in with_value or cluster_value else 1
    return i


_WRAPPER_VALUES = {
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nohup": set(),
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "chrt": {"-T", "--sched-runtime", "-P", "--sched-period", "-D", "--sched-deadline"},
    "unbuffer": set(),
    "time": {"-f", "--format", "-o", "--output"},
    "command": set(),
    "builtin": set(),
    "exec": {"-a"},
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-D", "--chdir", "-h", "--host", "-p",
             "--prompt", "-r", "--role", "-t", "--type", "-U", "--other-user", "-T", "--command-timeout"},
    "doas": {"-u", "-C"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "--arg-file", "--delimiter", "--max-args",
              "--max-procs", "--max-chars", "--max-lines", "--replace", "--eof"},
    "flock": {"-w", "--wait", "--timeout", "-E", "--conflict-exit-code"},
    "watch": {"-n", "--interval", "-q", "--equexit"},
    "strace": {"-o", "-e", "-p", "-s", "-u"},
    "valgrind": set(),
    "daemonize": {"-a", "-c", "-e", "-E", "-l", "-o", "-p", "-u"},
    "systemd-run": {"--unit", "-u", "-p", "--property", "--slice", "-E", "--setenv", "--working-directory",
                    "--uid", "--gid", "--nice", "--description", "-M", "--machine", "--on-calendar"},
}
_DETACHERS = {"daemonize"}


def unwrap(argv: list[str], cpus: frozenset[int] | None = None) -> Core:
    """Peel wrappers off a command; note the CPU set a taskset among them sets, and a detaching one."""
    core = Core(list(argv), cpus)
    i = 0
    while i < len(argv):
        w = argv[i]
        name = prog(w)
        if _ASSIGNMENT.fullmatch(w) and i > 0:  # env-style NAME=value
            i += 1
            continue
        if name in ("taskset", "numactl"):
            core.cpu_sets += _claim_at(argv, i)
        if name == "numactl":  # numactl [options] command: its CPU set is recorded above
            i = _skip_options(argv, i + 1, {"-C", "--physcpubind", "-N", "--cpunodebind", "-m", "--membind",
                                             "-i", "--interleave", "-p", "--preferred"})
            continue
        if name == "taskset":
            parsed, i, bad = _taskset(argv, i + 1)
            if bad:
                core.bad_taskset = bad
            elif parsed is not None:
                core.cpus = parsed
            continue
        if name == "rtk":
            i += 2 if i + 1 < len(argv) and argv[i + 1] == "proxy" else 1
            continue
        if name == "uv" and i + 1 < len(argv) and argv[i + 1] == "run":
            i = _skip_options(argv, i + 2, {"--with", "--python", "-p", "--project", "--directory", "--group",
                                             "--extra", "--env-file", "--package"})
            continue
        if name == "command" and any(a in ("-v", "-V") for a in argv[i + 1 : i + 3]):
            core.argv = []
            return core
        if name == "env":
            j = i + 1
            while j < len(argv):
                a = argv[j]
                if a in ("-C", "--chdir") and j + 1 < len(argv):
                    core.chdir = argv[j + 1]
                    j += 2
                elif a.startswith("--chdir="):
                    core.chdir = a.split("=", 1)[1]
                    j += 1
                elif a in ("-S", "--split-string") and j + 1 < len(argv) or a.startswith("--split-string="):
                    text = argv[j + 1] if "=" not in a else a.split("=", 1)[1]
                    try:
                        split = shlex.split(text)
                    except ValueError:
                        split = [text]
                    argv = argv[:j] + split + argv[j + (1 if "=" in a else 2) :]  # the string is the command
                    break
                elif a in ("-u", "--unset") and j + 1 < len(argv):
                    j += 2
                elif a.startswith("-") and a != "-":
                    j += 1
                elif "=" in a:  # NAME=VALUE, whatever NAME is: env takes it
                    j += 1
                else:
                    break
            i = j
            continue
        if name == "timeout":
            i = _skip_options(argv, i + 1, _WRAPPER_VALUES["timeout"]) + 1  # the duration
            continue
        if name == "chrt":
            i = _skip_options(argv, i + 1, _WRAPPER_VALUES["chrt"])
            if i < len(argv) and argv[i].isdigit():
                i += 1
            continue
        if name == "setsid":
            j = _skip_options(argv, i + 1, set())
            if any(a in ("-f", "--fork") for a in argv[i + 1 : j]):
                core.detached = "setsid -f"
            i = j
            continue
        if name in ("at", "batch"):
            core.detached = name  # the job runs later, from stdin, unwatched
            core.argv = argv[i:]
            return core
        if name == "start-stop-daemon":
            rest = argv[i + 1 :]
            if any(a in ("-b", "--background") for a in rest):
                core.detached = "start-stop-daemon -b"
            if "--" in rest:
                i = i + 1 + rest.index("--") + 1
                continue
            for k, a in enumerate(rest):
                if a in ("-x", "--exec", "-a", "--startas") and k + 1 < len(rest):
                    core.argv = [rest[k + 1]]
                    return core
            core.argv = []
            return core
        if name == "systemd-run":
            j = _skip_options(argv, i + 1, _WRAPPER_VALUES["systemd-run"])
            if "--scope" not in argv[i + 1 : j]:
                core.detached = "systemd-run"
            i = j
            continue
        if name == "tmux":
            rest = argv[i + 1 :]
            if rest[:1] and rest[0] in ("new", "new-session", "new-window", "neww", "split-window", "splitw",
                                         "run", "run-shell"):
                values = {"-s", "-n", "-c", "-t", "-x", "-y", "-e", "-F", "-f", "-l"}
                j = _skip_options(rest, 1, values)
                if any(re.fullmatch(r"-[a-zA-Z]*d[a-zA-Z]*", a) for a in rest[1:j]) or rest[0] in ("run", "run-shell"):
                    core.detached = f"tmux {rest[0]} -d"
                core.argv, core.script = argv[i:], " ".join(rest[j:]) or None
                return core
            break
        if name == "screen":
            rest = argv[i + 1 :]
            if any(re.fullmatch(r"-[a-zA-Z]*d[a-zA-Z]*m[a-zA-Z]*|-[a-zA-Z]*m[a-zA-Z]*d[a-zA-Z]*", a) for a in rest):
                core.detached = "screen -dm"
            j = _skip_options(rest, 0, {"-S", "-c", "-e", "-p", "-t", "-L", "-Logfile"})
            core.argv, core.script = argv[i:], " ".join(rest[j:]) or None
            return core
        if name == "git":
            rest = argv[i + 1 :]
            text = shlex.join(rest[2:]) if rest[:2] == ["bisect", "run"] and len(rest) > 2 else (
                _opt(rest, ("-x", "--exec")) if rest[:1] == ["rebase"] else None)
            if text:  # git bisect run CMD ARGS and git rebase -x TEXT: git writes, and runs the text in a shell
                core.argv, core.script = argv[i:], text
                return core
            break
        if name == "flock":
            rest = argv[i + 1 :]
            for k, a in enumerate(rest):
                if a in ("-c", "--command") and k + 1 < len(rest):
                    core.argv, core.script = argv[i:], rest[k + 1]
                    return core
            i = _skip_options(argv, i + 1, _WRAPPER_VALUES["flock"]) + 1  # the lock file
            continue
        if name == "watch":
            j = _skip_options(argv, i + 1, _WRAPPER_VALUES["watch"])
            if "-x" in argv[i + 1 : j] or "--exec" in argv[i + 1 : j]:
                i = j
                continue
            core.argv, core.script = argv[i:], " ".join(argv[j:])
            return core
        if name == "script":
            for k in range(i + 1, len(argv)):
                a = argv[k]
                if (a in ("-c", "--command") or re.fullmatch(r"-[a-zA-Z]*c", a)) and k + 1 < len(argv):
                    core.argv, core.script = argv[i:], argv[k + 1]
                    return core
                if a.startswith("--command="):
                    core.argv, core.script = argv[i:], a.split("=", 1)[1]
                    return core
            break
        if name in _WRAPPER_VALUES:
            core.via_xargs = core.via_xargs or name == "xargs"
            if name in _DETACHERS:
                core.detached = name
            i = _skip_options(argv, i + 1, _WRAPPER_VALUES[name])
            continue
        break
    core.argv = argv[i:]
    if not core.argv:
        return core
    head = prog(core.argv[0])
    if head in SHELLS:
        _shell_args(core)
        return core
    if head == "eval":
        core.script = " ".join(core.argv[1:])
        return core
    if head in ("source", "."):
        if len(core.argv) > 1:
            core.script_file = core.argv[1]
        return core
    if head == "find":
        roots = []
        for a in core.argv[1:]:
            if a.startswith(("-", "(", "!")):
                break
            roots.append(a)
        k = 1
        while k < len(core.argv):
            if core.argv[k] in ("-exec", "-execdir", "-ok", "-okdir"):
                j = k + 1
                inner = []
                while j < len(core.argv) and core.argv[j] not in (";", "+"):
                    inner.append(core.argv[j])
                    j += 1
                for root in (roots or ["."]) if inner else []:  # {} stands for what each root holds
                    core.inner.append([root if a == "{}" else a for a in inner])
                k = j + 1
                continue
            k += 1
    return core


_SHELL_VALUE_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_FISH_VALUE_OPTS = {"-C", "--init-command", "-d", "--debug", "-o", "--debug-output", "--profile"}


def _shell_args(core: Core) -> None:
    """A shell's options: -c text, -n (runs nothing), or the script file it runs."""
    head = prog(core.argv[0])
    valued = _FISH_VALUE_OPTS if head == "fish" else _SHELL_VALUE_OPTS
    k = 1
    while k < len(core.argv):
        a = core.argv[k]
        if a in valued:
            if a == "-o" and k + 1 < len(core.argv) and core.argv[k + 1] == "noexec":
                core.no_exec = True
            k += 2
            continue
        if a in ("-c", "--command") or (re.fullmatch(r"-[a-zA-Z]+", a) and "c" in a[1:]):
            if "n" in a[1:] and not a.startswith("--"):
                core.no_exec = True
            nxt = k + 1
            if re.fullmatch(r"-[a-zA-Z]*[oO][a-zA-Z]*", a) and head != "fish":
                nxt += 1  # `-co pipefail`: the o takes the next word first
            if nxt < len(core.argv):
                core.script = core.argv[nxt]
                core.script_lang = "fish" if head == "fish" else "sh"
                core.params = core.argv[nxt + 1 :] or [core.argv[0]]  # `-c TEXT arg0 arg1`: $0 is arg0
            return
        if a.startswith("--command="):
            core.script = a.split("=", 1)[1]
            core.script_lang = "fish" if head == "fish" else "sh"
            return
        if re.fullmatch(r"[-+][a-zA-Z]+", a):
            if a.startswith("-") and ("n" in a[1:] or a == "--no-execute"):
                core.no_exec = True
            k += 2 if a[-1] in "oO" and head != "fish" else 1  # `-euo pipefail`
            continue
        if a == "--no-execute":
            core.no_exec = True
            k += 1
            continue
        if a.startswith(("-", "+")) and a not in ("-", "--"):
            k += 1
            continue
        if a == "--":
            k += 1
            continue
        if a != "-":
            core.script_file = a
            core.params = core.argv[k:]
        return


# ─── what is heavy ────────────────────────────────────────────────────────────


@dataclass
class Heavy:
    name: str
    writes: bool  # it writes outputs (build trees, reports) where it runs
    out_dir: str | None = None  # the directory it writes, when its command line names one


HEAVY_TOOLS = {"run_ci", "stability_run", "check_reproducible_build", "coverage_run", "check_build_incremental",
               "iwyu", "warm_check_properties", "bundle_validate", "mutation_run", "mutation_cpp",
               "mutation_rust", "mutation_sweep_cache"}
_HEAVY_SCRIPTS = ("probes/run_all.sh", "benchmarks/run_all.sh", "tools/build_mull.sh")
_TEST_BINARIES = {"unit_tests", "excel_tests", "yaml_tests", "integration_tests", "benchmark", "stability_bench"}
_CATCH2_VALUES = {"--order", "--rng-seed", "-r", "--reporter", "-o", "--out", "-c", "--section", "-d",
                  "--durations", "--verbosity", "--warn", "-x", "--abortx", "--colour-mode", "--benchmark-samples",
                  "--benchmark-resamples", "--benchmark-confidence-interval", "--benchmark-warmup-time",
                  "--shard-count", "--shard-index", "--wait-for-keypress", "--min-duration", "--frames", "--runs",
                  "--warmup", "--bench", "--json"}
_CARGO_GLOBAL_VALUES = {"--manifest-path", "--config", "-Z", "--color", "-C"}
_CARGO_HEAVY = {"build": True, "b": True, "test": True, "t": True, "nextest": True, "mutants": True,
                "llvm-cov": True, "clippy": True, "bench": True, "run": True, "r": True, "check": True, "c": True,
                "doc": True, "install": False, "fix": True}
_GO_HEAVY = {"test": False, "build": True, "run": False, "vet": False, "generate": True, "install": False}
_LINTERS = {"pylint", "basedpyright", "mypy", "pyright"}
_LIGHT_FLAGS = {"--version", "--help", "-h", "-V"}
_PY_TOOL_SCRIPTS = {"pytest", "py.test", "mutmut", "pylint", "basedpyright", "mypy", "pyright"}
_PYTEST_VALUES = {"-p", "-k", "-m", "-o", "-c", "-W", "-r", "--tb", "--deselect", "--ignore", "--ignore-glob",
                  "--maxfail", "--rootdir", "--basetemp", "--durations", "--junitxml", "--junit-xml", "-n",
                  "--confcutdir", "--log-level", "--color", "--import-mode", "--junit-prefix", "--cov",
                  "--cov-report", "--capture", "--override-ini", "--pyargs", "--markdown-docs", "--dist"}
_LINTER_VALUES = {"--rcfile", "-j", "--jobs", "--disable", "-d", "--enable", "-e", "--output-format", "-f",
                  "-p", "--project", "--pythonpath", "--pythonversion", "--level", "--outputjson", "--config-file"}


def _operands(args: list[str], valued: set[str] | frozenset[str] = frozenset()) -> list[str]:
    out = []
    k = 0
    while k < len(args):
        a = args[k]
        if a in valued:
            k += 2
            continue
        if not a.startswith("-"):
            out.append(a)
        k += 1
    return out


def _python_heavy(module: str, args: list[str]) -> Heavy | None:
    if module in ("pytest", "py.test"):
        if any(a in ("--collect-only", "--co") for a in args):
            return None
        ops = _operands(args, _PYTEST_VALUES)
        targeted = ops and all((a.endswith(".py") or "::" in a) and not re.search(r"[*?\[]", a) for a in ops)
        return None if targeted else Heavy("pytest", False)
    if module == "mutmut":
        return Heavy("mutmut run", True) if "run" in args else None
    if module in _LINTERS:
        ops = _operands(args, _LINTER_VALUES)
        if ops and all(a.endswith(".py") and not re.search(r"[*?\[]", a) for a in ops):
            return None
        return Heavy(module, False)
    if module.startswith("tools."):
        name = module.split(".", 1)[1]
        if name == "coverage_run" and "--scope" in args:
            return None
        if name in HEAVY_TOOLS:
            return Heavy(module, True)
    return None


def _tool_script(path: str) -> str | None:
    """`tools.<name>` for a heavy repository tool script path, else None."""
    m = re.search(r"(?:^|/)tools/(\w+)\.py$", path)
    if m and m.group(1) in HEAVY_TOOLS:
        return f"tools.{m.group(1)}"
    return None


_ORIGIN: dict[str, str] = {}  # a copy the call made -> what it copied: a runner copied is still the runner


def _heavy_script(path: str, cwd: str | None = None) -> bool:
    """A heavy script by its name, by the one the call copied it from, or by being a byte copy of one in the
    repository the call runs in (copied in an earlier call)."""
    first = path
    for _ in range(8):
        if any(path == s or path.endswith("/" + s) for s in _HEAVY_SCRIPTS):
            return True
        if path not in _ORIGIN:
            break
        path = _ORIGIN[path]
    tree = _shell.tree_of(cwd) if cwd else None
    try:
        size = os.path.getsize(first) if os.path.isfile(first) else -1
        for rel in _HEAVY_SCRIPTS if tree and size > 0 else ():
            original = os.path.join(tree, rel)
            if os.path.isfile(original) and os.path.getsize(original) == size and \
                    Path(original).read_bytes() == Path(first).read_bytes():
                return True
    except OSError:
        return False
    return False


def _full(path: str, cwd: str | None) -> str:
    """A script path as it resolves from `cwd`, for matching by its repository-relative tail."""
    r = resolve(path, cwd)
    return r if r else path


def _opt(args: list[str], names: tuple[str, ...]) -> str | None:
    for k, a in enumerate(args):
        if a in names and k + 1 < len(args):
            return args[k + 1]
        for n in names:
            if n.startswith("--") and a.startswith(n + "="):
                return a.split("=", 1)[1]
            if not n.startswith("--") and len(n) == 2 and a.startswith(n) and len(a) > 2:
                return a[2:]
    return None


def heavy(argv: list[str], cwd: str | None = None) -> Heavy | None:
    """What heavy run a command (wrappers already peeled) starts, or None."""
    if not argv:
        return None
    name = prog(argv[0])
    if any(a in _LIGHT_FLAGS and not (a == "-V" and (name == "ctest" or "shake" in argv)) for a in argv[1:]):
        return None  # asking for help or a version runs nothing (ctest's and shake's -V are verbose)
    args = argv[1:]
    if is_python(argv[0]):
        k = 0
        while k < len(args):
            a = args[k]
            if a == "-m" and k + 1 < len(args):
                return _python_heavy(args[k + 1], args[k + 2 :])
            if a.startswith("-m") and len(a) > 2:
                return _python_heavy(a[2:], args[k + 1 :])
            if a in ("-W", "-X", "--check-hash-based-pycs"):
                k += 2
                continue
            if a.startswith("-"):
                if a == "-c" or (re.fullmatch(r"-[a-zA-Z]+", a) and "c" in a):
                    return None
                k += 1
                continue
            base = os.path.basename(a)
            if base in _PY_TOOL_SCRIPTS:  # a venv tool's shebang: python .venv/bin/pytest tests/
                return _python_heavy(base, args[k + 1 :])
            module = _tool_script(_full(a, cwd))
            return Heavy(module, True) if module else None
        return None
    if name in ("pytest", "py.test", "mutmut") or name in _LINTERS:
        return _python_heavy(name, args)
    if name == "cargo":
        k = 0
        while k < len(args):
            a = args[k]
            if a.startswith("+"):
                k += 1
                continue
            if a in _CARGO_GLOBAL_VALUES:
                k += 2
                continue
            if a.startswith("-"):
                k += 1
                continue
            rest = args[k + 1 :]
            if a == "install" and "--list" in rest:
                return None
            if a == "mutants" and any(r in ("--list", "--list-files", "--emit-schema") for r in rest):
                return None
            if a == "llvm-cov" and "--no-run" in rest:
                return None
            if a in _CARGO_HEAVY:
                manifest = _opt(args, ("--manifest-path",))
                target = _opt(args, ("--target-dir",))
                out = target or ((os.path.dirname(manifest) or ".") if manifest else None)
                return Heavy(f"cargo {a}", _CARGO_HEAVY[a], out)
            return None
        return None
    if name == "go":
        k = 0
        while k < len(args):
            a = args[k]
            if a == "-C":
                k += 2
                continue
            if a.startswith("-"):
                k += 1
                continue
            rest = args[k + 1 :]
            if a == "test" and ("-run" in rest or any(r.startswith("-run=") for r in rest)):
                return None
            if a == "vet" and not any("..." in r for r in rest):
                return None
            return Heavy(f"go {a}", _GO_HEAVY[a], _opt(args, ("-C",))) if a in _GO_HEAVY else None
        return None
    if name == "cmake":
        if "--build" in args:
            wanted = [args[k + 1] for k, a in enumerate(args[:-1]) if a in ("--target", "-t")] + [
                a.split("=", 1)[1] for a in args if a.startswith("--target=")]
            if wanted and all(w == "help" for w in wanted):
                return None  # --target help lists the targets
            return Heavy("cmake --build", True, _opt(args, ("--build",)))
        if "--workflow" in args:
            return Heavy("cmake --workflow", True)
        return None
    if name == "ctest":
        if any(a in ("-N", "--show-only", "--print-labels", "-R", "-I", "-L", "--tests-regex", "--label-regex",
                     "--tests-information") or a.startswith(("--show-only=", "-R", "-L")) for a in args):
            return None
        return Heavy("ctest", False, _opt(args, ("--test-dir",)))
    if name in ("make", "gmake", "ninja"):
        if any(a in ("-n", "--dry-run", "--just-print", "-q", "--question") for a in args):
            return None
        if name == "ninja" and "-t" in args:
            return None
        return Heavy(name, True, _opt(args, ("-C", "--directory")))
    if name == "cabal":
        ops = [a for a in args if not a.startswith("-")]
        if not ops:
            return None
        sub = ops[0]
        if sub in ("run", "v2-run", "new-run"):
            after = args[args.index(sub) + 1 :]
            target = next((a for a in after if not a.startswith("-")), None)
            if target == "shake":
                targets = args[args.index("--") + 1 :] if "--" in args else []
                if targets and all(t in ("count-modules", "help") for t in targets):
                    return None
                return Heavy("cabal run shake", True)
            return Heavy(f"cabal run {target}", True) if target else None
        if sub in ("build", "v2-build", "new-build", "test", "v2-test", "bench", "install", "v2-install"):
            return Heavy(f"cabal {sub}", True)
        return None
    if name == "agda":  # it checks what it is given: a file, a library, an interaction; a version or a path is light
        if any(re.search(r"\.l?agda(\.\w+)?$", a) or a == "--build-library" or a.startswith("--interaction")
               for a in args):
            return Heavy("agda", True)
        return None
    if name == "gremlins":
        if "unleash" not in args or "--dry-run" in args or "-d" in args:
            return None
        return Heavy("gremlins unleash", True)
    if name == "mull-runner":
        return None if "--dry-run" in args else Heavy("mull-runner", False)  # --dry-run lists the mutants only
    if name == "bazel" and args[:1] and args[0] in ("build", "test", "run"):
        return Heavy(f"bazel {args[0]}", True)
    if name == "act":
        return Heavy("act", True)
    if name == "docker" and args[:1] == ["build"]:
        return Heavy("docker build", False)
    if name in _TEST_BINARIES:
        if any(a.startswith("--list") for a in args):
            return None
        if name not in ("benchmark", "stability_bench") and _operands(args, _CATCH2_VALUES):
            return None  # a Catch2 run of named tests or tags, like ctest -R
        return Heavy(name, False)
    if name in SHELLS:
        core = Core(list(argv))
        _shell_args(core)
        if core.no_exec or core.script is not None:
            return None
        if core.script_file and _heavy_script(_full(core.script_file, cwd), cwd):
            return Heavy(os.path.basename(os.path.dirname(_full(core.script_file, cwd))) + "/"
                         + os.path.basename(core.script_file), True)
        return None
    if name in ("source", ".") and len(argv) > 1 and _heavy_script(_full(argv[1], cwd), cwd):
        return Heavy(os.path.basename(argv[1]), True)
    full = _full(argv[0], cwd) if "/" in argv[0] else argv[0]
    if _heavy_script(full, cwd):
        return Heavy(os.path.basename(os.path.dirname(full)) + "/" + os.path.basename(full), True)
    module = _tool_script(full)
    if module:
        return Heavy(module, True)
    return None


_HEAD_CHARS = 1024  # a statement's program, subcommand and options come first


def _unquoted(text: str) -> str:
    """The text with every quoted span blanked: what stands in quotes is a word's data, not a statement."""
    out, quote, escaped = [], "", False
    for ch in text:
        if escaped:
            escaped = False
            out.append(" " if quote else ch)
        elif ch == "\\" and quote != "'":
            escaped = True
            out.append(" " if quote else ch)
        elif quote:
            quote = "" if ch == quote else quote
            out.append(" ")
        elif ch in "'\"":
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


_HEREDOC_OP = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_]\w*)\2")
# Where a statement can start: after a separator or an opener, a backquote, a reserved word that leads one
# (and `if`'s own condition), zsh's `repeat N`, `!`, and fish's and/or/not/begin/end.
_STATEMENT_BREAK = re.compile(r"[;\n|&(){}`]+|(?<![\w-])(?:do|then|else|elif|if|while|until|time)(?![\w-])"
                              r"|(?<![\w-])repeat\s+\S+|(?:^|(?<=\s))!(?=\s)|\b(?:and|or|not|begin|end)\b")


def _without_heredoc_bodies(text: str) -> str:
    """The text with each heredoc's body left out: its lines are data, not statements.  A `<<` whose
    delimiter line never comes (`$((1 << x))`) is not a heredoc, and hides nothing."""
    lines = text.split("\n")
    out, pending = [], []
    for k, line in enumerate(lines):
        if pending:
            delim, tabs = pending[0]
            if (line.lstrip("\t") if tabs else line) == delim:
                pending.pop(0)
            continue
        out.append(line)
        for m in _HEREDOC_OP.finditer(line):
            tabs, delim = m.group(1) == "-", m.group(3)
            if any((later.lstrip("\t") if tabs else later) == delim for later in lines[k + 1 :]):
                pending.append((delim, tabs))
    return "\n".join(out)


def heavy_in_text(text: str, cwd: str | None) -> Heavy | None:
    """A heavy command in text no reader could follow (fish, a call past its budget or nested past what the
    reader follows, a command Python starts that does not parse): found statement by statement, from each
    one's leading words, quoted spans and heredoc bodies aside."""
    for piece in _STATEMENT_BREAK.split(_unquoted(_without_heredoc_bodies(text))):
        words = piece[:_HEAD_CHARS].split()  # its quotes are blanked: nothing is left for shlex to read
        core = unwrap(words) if words else None
        h = heavy(core.argv, cwd) if core and core.argv else None
        if h is not None:
            return h
    return None


def heavy_in_code(code: str, cwd: str | None, depth: int = 0) -> Heavy | None:
    """A heavy run a Python driver starts through subprocess or os.system."""
    if depth >= MAX_DEPTH:
        return None
    for text, dirs in python_facts(code, cwd).commands:
        if text is None:
            continue
        for d in dirs or [cwd]:
            try:
                for inv in expand(text, d or cwd, depth=depth + 1, dialect="sh"):
                    if inv.heavy is not None:
                        return Heavy(f"a Python driver of {inv.heavy.name}", inv.heavy.writes)
            except _shell.ParseError:  # a command it starts that does not parse: its text, statement by statement
                h = heavy_in_text(text, d or cwd)
                if h is not None:
                    return Heavy(f"a Python driver of {h.name}", h.writes)
    return None


# ─── a whole call ─────────────────────────────────────────────────────────────


@dataclass
class Inv:
    cmd: _shell.Command
    core: Core
    heavy: Heavy | None
    origin: str  # "call", "script" (the call's own: outside every tree, or written by it) or "repo-script"
    carrier: bool = False
    code: tuple[str, str] | None = None  # (language, source) an interpreter runs
    code_args: list[str] = field(default_factory=list)  # operands after an interpreter's script or code
    code_origin: str | None = None  # whose the code is, as `origin` reads: None while the call spells it inline
    unreadable: str | None = None  # something this call runs that could not be read
    # Under xargs, where the operands it reads come from: the paths a list names, or the roots a find searches;
    # None when the reader cannot tell, and then they land nowhere known.
    xargs_paths: list[str] | None = None


_XARGS_FILE = re.compile(r"-a(.+)|--arg-file=(.+)")


def _listed_paths(text: str | None, cwd: str | None, nul: bool) -> list[str] | None:
    """The paths a list names, one per line (or NUL-separated), each resolved where the list is read."""
    if text is None:
        return None
    names = [n.strip() for n in (text.split("\0") if nul else text.splitlines()) if n.strip()]
    paths = [resolve(n, cwd) for n in names]
    return None if any(p is None for p in paths) else [p for p in paths if p]


def _xargs_paths(cmd: _shell.Command, prev: _shell.Command | None, files: dict[str, str | None]) -> list[str] | None:
    """Where the operands xargs reads come from: an -a list, a `<` list or heredoc, or its producer in the pipe
    (find's roots, echo's or cat's text, git's own directory); None when the reader cannot tell."""
    argv = cmd.argv
    k = next((i for i, a in enumerate(argv) if prog(a) == "xargs"), None)
    if k is None:
        return None
    opts = []
    for a in argv[k + 1 :]:
        if not a.startswith("-"):
            break
        opts.append(a)
    nul = any(o in ("-0", "--null") for o in opts)
    for i, a in enumerate(argv[k + 1 :], k + 1):
        m = _XARGS_FILE.fullmatch(a)
        name = argv[i + 1] if a in ("-a", "--arg-file") and i + 1 < len(argv) else (m.group(1) or m.group(2)) if m \
            else None
        if name is not None:
            return _listed_paths(_file_text(resolve(name, cmd.cwd), files), cmd.cwd, nul)
        if not a.startswith("-"):
            break
    sources = _stdin_sources(cmd)
    if sources and sources[-1].op != "<|":  # a `<` file or a heredoc is the list
        return _listed_paths(_source_text(sources[-1], cmd, prev, files), cmd.cwd, nul)
    if prev is None or not prev.pipe_out or not prev.argv:
        return None
    pname = prog(prev.argv[0])
    if pname == "find":  # the paths it prints lie below its roots
        roots = []
        for a in prev.argv[1:]:
            if a.startswith(("-", "(", "!")):
                break
            roots.append(a)
        placed = [resolve(r, prev.cwd) for r in roots or ["."]]
        return None if any(p is None for p in placed) else [p for p in placed if p]
    if pname == "git" and "ls-files" in prev.argv:  # names relative to where git runs
        c = prev.argv.index("-C") + 1 if "-C" in prev.argv else None
        where = resolve(prev.argv[c], prev.cwd) if c is not None and c < len(prev.argv) else prev.cwd
        return [where] if where else None
    return _listed_paths(_source_text(_shell.Redirect(None, "<|", None), cmd, prev, files), cmd.cwd, nul)


_SHEBANG = re.compile(r"#!\s*(\S+)(?:\s+(\S+))?")


def _lang_of(path: str, text: str) -> str:
    m = _SHEBANG.match(text)
    if m:
        interp = os.path.basename(m.group(1))
        if interp == "env" and m.group(2):
            interp = os.path.basename(m.group(2))
        if is_python(interp):
            return "python"
        if interp in INTERPRETERS:
            return interp
        if interp in SHELLS:
            return "fish" if interp == "fish" else "sh"
    if path.endswith(".py"):
        return "python"
    return "sh"


class Pending(str):
    """Code text that still holds shell expansions the reader could not make: the shell fills them first."""


class Approx(str):
    """A file's text known only nearly: an edit or a filter the reader cannot follow made it from this text.
    Read for what it runs (rules 1-3); what it writes is out of sight (rule 4)."""


_SHELL_PART = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)|\$[A-Za-z_]\w*|`[^`]*`")


def _pending(text: str) -> str:
    return Pending(text) if _SHELL_PART.search(text) else text


def python_facts(code: str, cwd: str | None) -> _python.Facts:
    """What Python code writes and starts.  Code still holding shell expansions is read with a placeholder for
    each; code that parses neither way runs nothing, unless it held such an expansion."""
    facts = _python.read_python(code, cwd)
    if facts.parsed or not isinstance(code, Pending):
        return facts
    return _python.read_python(_SHELL_PART.sub("__SHELL__", code), cwd)


def _expand_body(body: str, env: dict[str, str | None]) -> str:
    """An unquoted heredoc as the shell writes it (bash and zsh alike, measured 2026-09-28): `\\$`, `\\``, `\\\\`
    and a backslash-newline unescaped, any other backslash kept, and each variable replaced where its value is
    known; an escaped `\\$x` stays `$x`, for the script the heredoc writes to expand when it runs."""
    def sub(m: re.Match[str]) -> str:
        if m.group(1) is not None:
            return "" if m.group(1) == "\n" else m.group(1)
        name = m.group(2) or m.group(3)
        v = env.get(name, os.environ.get(name))
        return v if v is not None else m.group(0)

    return re.sub(r"\\([$`\\\n])|\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)", sub, body)


def _file_text(path: str | None, files: dict[str, str | None]) -> str | None:
    if not path:
        return None
    if path in files:
        return files[path]
    return read_regular(path)


def _stdin_sources(cmd: _shell.Command) -> list[_shell.Redirect]:
    return [r for r in cmd.redirects if r.op == "<|" or r.body is not None or (r.op == "<" and r.target is not None)]


def _stdin_text(cmd: _shell.Command, prev: _shell.Command | None, files: dict[str, str | None]) -> str | None:
    """The text a command reads on stdin: bash gives it the last source (a heredoc, a `<` file, its pipe),
    zsh every source in order."""
    sources = _stdin_sources(cmd)  # inherited first, then the pipe, then its own
    if not sources:
        return None
    texts = [_source_text(r, cmd, prev, files) for r in (sources if cmd.dialect == "zsh" else sources[-1:])]
    if any(t is None for t in texts):
        return None
    text = "\n".join(t for t in texts if t is not None)
    return _pending(text) if any(isinstance(t, Pending) for t in texts) else text


def _source_text(source: _shell.Redirect, cmd: _shell.Command, prev: _shell.Command | None,
                 files: dict[str, str | None]) -> str | None:
    if source.body is not None:
        return source.body if source.body_literal else _pending(_expand_body(source.body, cmd.env))
    if source.op == "<":
        return _file_text(resolve(source.target.value, cmd.cwd), files) if source.target.value else None
    if prev is not None and prev.pipe_out and prev.words:  # the pipe: a literal producer before it
        pname = prog(prev.argv[0])
        if pname in ("echo", "printf", "print") and all(w.value is not None for w in prev.words):
            return _printed(pname, prev.argv[1:], prev.dialect)
        if pname == "cat":
            if prev.bodies():
                return "\n".join(prev.bodies())
            texts = [_file_text(resolve(a, prev.cwd), files) for a in prev.argv[1:] if not a.startswith("-")]
            if texts and all(texts):
                return "\n".join(t for t in texts if t)
    return None


_CODE_FLAG = {"python": re.compile(r"-[a-zA-Z]*c"), "perl": re.compile(r"-[a-zA-Z0-9]*[eE]"),
              "ruby": re.compile(r"-[a-zA-Z]*e"), "node": re.compile(r"-[a-z]*[ep]|--eval|--print")}
_INTERP_VALUES = {"python": ("-W", "-X"), "perl": ("-I", "-M", "-m", "-x"), "ruby": ("-I", "-r"),
                  "node": ("-r", "--require")}


def _module_file(module: str, cwd: str | None) -> str | None:
    base = resolve(module.replace(".", "/"), cwd)
    if not base:
        return None
    for cand in (base + ".py", os.path.join(base, "__main__.py")):
        if os.path.isfile(cand):
            return cand
    return None


def _interpreter_code(core: Core, cmd: _shell.Command, prev: _shell.Command | None,
                      files: dict[str, str | None]) -> tuple[tuple[str, str] | None, list[str], str | None, str | None]:
    """(language, code) an interpreter call runs, the operands after it, a script it could not read, and the file
    the code was read from (None for code the call spells: -c, a heredoc, a pipe)."""
    argv = core.argv
    name = prog(argv[0])
    lang = "python" if is_python(argv[0]) else name if name in INTERPRETERS else None
    if lang is None:
        return None, [], None, None
    args = argv[1:]
    k = 0
    while k < len(args):  # the options before the code, a module or the script; a value is never one
        a = args[k]
        if a in _INTERP_VALUES[lang]:
            k += 2
            continue
        if lang == "node" and a in ("--check", "-c"):
            return None, [], None, None  # node --check reads the syntax and runs nothing
        if _CODE_FLAG[lang].fullmatch(a) and k + 1 < len(args):
            code = args[k + 1]  # an unknown word keeps its expansions spelled as written
            return ((lang, _pending(code) if "${" in code or "$(" in code else code), _operands(args[k + 2 :]), None,
                    None)
        module = None
        if lang == "python":
            module = args[k + 1] if a == "-m" and k + 1 < len(args) else (a[2:] if a.startswith("-m") and
                                                                           len(a) > 2 else None)
        if module is not None:
            path = _module_file(module, cmd.cwd)
            text = read_regular(path) if path else None
            return ((lang, text), _operands(args[k + 2 :]), None, path) if text else (None, [], None, None)
        if not a.startswith("-") or a == "-":
            break
        k += 1
    k = 0
    while k < len(args) and args[k].startswith("-") and args[k] != "-":
        k += 2 if args[k] in _INTERP_VALUES[lang] else 1
    script = args[k] if k < len(args) else None
    if script is None or script == "-":
        text = _stdin_text(cmd, prev, files)
        if text:
            return (lang, text), _operands(args[k + 1 :]), None, None
        return None, [], (f"the code piped into `{name}`" if _stdin_sources(cmd) else None), None
    path = resolve(script, cmd.cwd)
    text = _file_text(path, files)
    if text is None:
        return None, [], script, None
    return (lang, text), _operands(args[k + 1 :]), None, path


def expand(text: str, cwd: str | None, **kw: object) -> list[Inv]:
    """Every invocation a call runs, carriers and scripts read.  Raises _shell.ParseError for text it cannot
    follow, and _shell.BudgetExceeded past the budget, carrying in `found` what was read before it ran out; a
    fault of the reader itself raises something else, which the hooks report."""
    out: list[Inv] = []
    try:
        _expand(out, text, cwd, **kw)  # type: ignore[arg-type]
    except _shell.BudgetExceeded as exc:
        exc.found = [*getattr(exc, "found", []), *out]
        raise
    return out


def _expand(out: list[Inv], text: str, cwd: str | None, *, cpus: frozenset[int] | None = None, depth: int = 0,
            origin: str = "call", outer: _shell.Command | None = None,
            files: dict[str, str | None] | None = None, params: list[str] | None = None,
            script_path: str | None = None, budget: _Budget | None = None, dialect: str = "zsh") -> list[Inv]:
    """expand()'s reading, into `out`."""
    files = {} if files is None else files
    if budget is None:
        if depth == 0 and outer is None:  # a new call: every reading of it shares one budget from here
            _shell.reset_budget()
            _CALL[0] = _Budget()
        budget = _CALL[0]
    env = None
    if outer is not None:
        env = dict(outer.env)
        env.update({k: v for k, v in outer.assigns.items()})
    same_shell = outer is not None and bool(outer.words) and prog(outer.argv[0]) in ("eval", "source", ".")
    if params is not None:
        env = dict(env or {})
        for k in range(10):  # a script's or -c text's own words; one never given is empty
            v = params[k] if k < len(params) else ""
            env[str(k)] = None if re.search(r"[$`]", v) else v
        env["#"] = str(len(params) - 1) if 0 < len(params) <= 10 else None
        if script_path:
            env["BASH_SOURCE"] = script_path
    reader = _sourced_text
    if re.search(r"(^|[\s;&|(])(source|\.)\s", text) and (files or re.search(r">|\btee\b", text)):
        # A file this text writes before it sources it is read as written: a first reading finds the writes.
        written = dict(files)
        for c0 in _shell.commands(text, cwd, env=env, redirects=list(outer.redirects) if outer else None,
                                  scope=outer.job_scope if same_shell else None, dialect=dialect):
            _remember_written(c0, written, set())
        reader = functools.partial(_sourced_text, written=written)
    cmds = _shell.commands(text, cwd, env=env, redirects=list(outer.redirects) if outer else None,
                           scope=outer.job_scope if same_shell else None, source_reader=reader,
                           dialect=dialect)
    budget.commands += len(cmds)
    if budget.commands > _shell.MAX_COMMANDS:
        raise _shell.BudgetExceeded(f"more than {_shell.MAX_COMMANDS} commands to read")
    prev: _shell.Command | None = None
    for cmd in cmds:
        if outer is not None:
            if outer.background and not cmd.background:  # the carrier's own process runs it
                cmd.background, cmd.proc, cmd.job, cmd.bg_scope = True, outer.proc, outer.job, outer.bg_scope
        _remember_written(cmd, files, budget.opened, prev)
        if not cmd.words:
            out.append(Inv(cmd, Core([]), None, origin))
            prev = cmd
            continue
        core = unwrap(cmd.argv, cpus)
        if core.chdir:
            cmd.cwd = resolve(core.chdir, cmd.cwd)
        if core.no_exec:
            out.append(Inv(cmd, core, None, origin))
            prev = cmd
            continue
        inv = Inv(cmd, core, heavy(core.argv, cmd.cwd), origin)
        if core.via_xargs:
            inv.xargs_paths = _xargs_paths(cmd, prev, files)
        out.append(inv)
        if cmd.unparsed:
            inv.unreadable = cmd.unparsed
        piped_from = prev
        if core.argv:
            inv.code, inv.code_args, unread, source = _interpreter_code(core, cmd, prev, files)
            if unread:
                inv.unreadable = unread
            if source is not None:
                inv.code_origin = "script" if source in files or not _shell.tree_of(source) else "repo-script"
                if isinstance(inv.code[1], Approx):  # read for what it starts; what it writes is out of sight
                    inv.unreadable = inv.unreadable or f"{source}, which the call rewrote first"
            if inv.code and inv.code[0] == "python" and inv.heavy is None:
                inv.heavy = heavy_in_code(inv.code[1], cmd.cwd, depth)
            if inv.code:  # code that names a script this call wrote may rewrite it: its text is no longer sure
                for path in [p for p in files if files[p] is not None and p in inv.code[1]]:
                    files[path] = None
        prev = cmd
        if depth >= MAX_DEPTH:
            if core.inner or core.script or core.script_file or (core.argv and prog(core.argv[0]) in SHELLS):
                inv.unreadable = inv.unreadable or f"`{' '.join(core.argv[:2])[:40]}`, past the reading depth"
            continue
        for inner in core.inner:  # find -exec: read as a command of the call, {} standing for a root
            try:
                out += expand(shlex.join(inner), cmd.cwd, cpus=core.cpus, depth=depth + 1, origin=origin,
                              outer=cmd, files=files, budget=budget, dialect="sh")
            except _shell.ParseError:
                inv.unreadable = " ".join(inner)
        script = core.script
        script_file = core.script_file
        if script is None and core.argv and prog(core.argv[0]) in SHELLS and script_file is None:
            script = _stdin_text(cmd, piped_from, files)
            if script is None and _stdin_sources(cmd):  # a program on stdin the reader cannot see
                inv.unreadable = inv.unreadable or f"the program piped into `{prog(core.argv[0])}`"
        if script is None and script_file is None and core.argv and "/" in core.argv[0] and inv.code is None \
                and inv.heavy is None:
            script_file = core.argv[0]  # a script run by its path
        if script is not None:
            inv.carrier = True
            if core.script_lang == "fish":  # fish's `; and cmd`: the combinator, not a program named and
                script = re.sub(r"(^|[;\n|&(]\s*)(?:and|or|not)\s+", r"\1", script)
            try:
                out += expand(script, cmd.cwd, cpus=core.cpus, depth=depth + 1, origin=origin, outer=cmd,
                              files=files, params=core.params, budget=budget,
                              dialect=_shell_dialect(core.argv, cmd.dialect, env={**cmd.env, **cmd.assigns}))
            except _shell.ParseError:
                inv.unreadable = f"the {core.script_lang} text of `{' '.join(core.argv[:2])[:40]}`"
            continue
        if script_file is None or cmd.inlined:
            continue  # a sourced file the reader already read in place
        path = resolve(script_file, cmd.cwd)
        written = path is not None and path in files
        body = files[path] if written else read_script(path)
        if body == BINARY:
            continue  # a binary run by its path, not a script
        if isinstance(body, Approx):
            inv.unreadable = inv.unreadable or f"{script_file}, which the call rewrote first"
        budget.commands += len(body or "") // 200  # a long script spends the call's budget as it is read
        if body is None:
            if not os.path.isabs(script_file) and "/" not in script_file and prog(core.argv[0]) not in SHELLS \
                    and prog(core.argv[0]) not in ("source", "."):
                continue  # a program found on PATH, not a script path
            inv.unreadable = script_file
            continue
        file_lang = _lang_of(path or script_file, body)
        if file_lang not in ("sh", "fish"):
            if inv.code is None:
                inv.code, inv.code_args = (file_lang, body), _operands(core.argv[1:])
                inv.code_origin = "script" if written or not _shell.tree_of(path or script_file) else "repo-script"
                if file_lang == "python" and inv.heavy is None:
                    inv.heavy = heavy_in_code(body, cmd.cwd, depth)
            continue
        inv.carrier = True
        child_origin = "script" if written or not _shell.tree_of(path or script_file) else "repo-script"
        if origin == "repo-script":
            child_origin = "repo-script"
        sourced = prog(core.argv[0]) in ("source", ".")
        params = None if sourced and len(core.argv) < 3 else (
            [script_file, *core.argv[core.argv.index(script_file) + 1 :]] if script_file in core.argv else None)
        start = len(out)
        key = (path, body, cmd.cwd, tuple(params or ()), cmd.background, child_origin, core.cpus,
               frozenset(cmd.env.items()), frozenset(cmd.assigns.items()), tuple(id(r) for r in cmd.redirects),
               cmd.pipe_in, cmd.pipe_out)  # run the same way: every input that can change what it does
        if key in budget.read:
            continue  # read once already: the same script, run the same way, runs the same commands
        budget.read.add(key)
        try:
            out += expand(body, cmd.cwd, cpus=core.cpus, depth=depth + 1, origin=child_origin, outer=cmd,
                          files=files, params=params, script_path=path, budget=budget,
                          dialect=_shell_dialect(core.argv, cmd.dialect, body))
        except _shell.ParseError:
            inv.unreadable = script_file
        # A repository script that runs a heavy command makes the call running it that heavy run (the user's
        # ruling, 2026-09-27); inside, it is still read for its writes only.
        inner = next((x.heavy for x in out[start:] if x.heavy is not None), None)
        if child_origin == "repo-script" and inv.heavy is None and inner is not None:
            inv.heavy = Heavy(f"{os.path.basename(script_file)} (a repository script running {inner.name})",
                              False)  # rules 1-3 only: what it writes is read from the script itself
    return out


_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}


def _unescape(text: str) -> str:
    """printf's and `echo -e`'s backslash escapes; one it does not know stays as written."""
    def one(m: re.Match[str]) -> str:
        e = m.group(1)
        if e in _ESCAPES:
            return _ESCAPES[e]
        if e.startswith("x") and len(e) > 1:
            return chr(int(e[1:], 16))  # a lone \\x, no hex digit after it, stays as written, as bash prints it
        if e.startswith("0"):
            return chr(int(e[1:] or "0", 8))
        return m.group(0)
    return re.sub(r"\\(x[0-9A-Fa-f]{1,2}|0[0-7]{0,3}|.)", one, text, flags=re.S)


def _printed(name: str, args: list[str], dialect: str = "bash") -> str | None:
    """What echo, printf or zsh's print writes, where the reader can tell (zsh's echo and print read escapes
    unless told not to: -E, or print's -r and -R)."""
    if name == "print":
        newline, escapes = True, True
        while args and re.fullmatch(r"-[nrRE]+", args[0]):
            newline = newline and "n" not in args[0]
            escapes = escapes and not set("rRE") & set(args[0])
            args = args[1:]
        if args[:1] == ["--"]:
            args = args[1:]
        text = " ".join(args)
        return (_unescape(text) if escapes else text) + ("\n" if newline else "")
    if name == "echo":
        newline, escapes = True, dialect == "zsh"
        while args and re.fullmatch(r"-[neE]+", args[0]):
            newline = newline and "n" not in args[0]
            escapes = "e" in args[0] or (escapes and "E" not in args[0])
            args = args[1:]
        text = " ".join(args)
        return (_unescape(text) if escapes else text) + ("\n" if newline else "")
    if not args:
        return None
    fmt, rest = _unescape(args[0]), args[1:]
    if not rest:
        return None if "%" in fmt.replace("%%", "") else fmt.replace("%%", "%")
    if fmt in ("%s\n", "%s"):
        return "".join(a + ("\n" if fmt.endswith("\n") else "") for a in rest)
    return None


def _edited(argv: list[str], cwd: str | None) -> list[str]:
    """The files a command edits in place or replaces, by its own words: what a remembered script loses."""
    if not argv:
        return []
    name = prog(argv[0])
    args = argv[1:]
    operands = [a for a in args if not a.startswith("-")]
    paths: list[str] = []
    if name in ("sed", "perl") and any(re.fullmatch(r"-[a-zA-Z]*i\S*", a) or a.startswith("--in-place")
                                         for a in args):
        paths = operands[1:] if name == "sed" and not any(a in ("-e", "-f") or a.startswith(("-e", "-f"))
                                                           for a in args) else operands
    elif name in ("awk", "gawk") and "inplace" in args:
        paths = operands
    elif name in ("cp", "mv", "install", "rsync", "ln", "scp"):
        paths = list(_copied(argv, cwd)) + (operands[:-1] if name == "mv" else [])
        return [p for p in (resolve(x, cwd) if not os.path.isabs(x) else x for x in paths) if p]
    elif name in ("rm", "truncate", "shred", "sponge", "patch", "ed", "ex", "vi", "vim", "nvim", "nano", "emacs"):
        paths = operands
    elif name == "dd":
        paths = [a[3:] for a in args if a.startswith("of=")]
    return [p for p in (resolve(x, cwd) for x in paths) if p]


def _forget_edited(cmd: _shell.Command, files: dict[str, str | None]) -> None:
    """What a call's command does to a script it later runs: a copy carries its source's text and name, an
    edit in place leaves the old text as a near guess, a move or a removal leaves nothing to read."""
    if not cmd.words:
        return
    argv = unwrap(cmd.argv).argv
    name = prog(argv[0]) if argv else ""
    copied = _copied(argv, cmd.cwd) if name in ("cp", "install", "rsync", "scp", "ln", "mv") else None
    for full in _edited(argv, cmd.cwd):
        if copied is not None and full in copied:
            src = copied[full]
            files[full] = _file_text(src, files) if src else None
            if src:
                _ORIGIN[full] = src
            continue
        before = files[full] if full in files else read_regular(full)
        gone = name in ("rm", "mv", "shred", "truncate")
        files[full] = None if gone or before is None else Approx(before)


def _copied(argv: list[str], cwd: str | None) -> dict[str, str | None]:
    """Each destination file of a cp, install, rsync, ln or mv, and the source it gets (None: several)."""
    args = argv[1:]
    target = _opt(args, ("-t", "--target-directory"))
    operands = [a for a in args if not a.startswith("-")]
    sources, dest = (operands, target) if target else (operands[:-1], operands[-1] if operands else None)
    out: dict[str, str | None] = {}
    dfull = resolve(dest, cwd) if dest else None
    if not dfull:
        return out
    srcs = [resolve(x, cwd) for x in sources]
    if len(srcs) == 1 and srcs[0] and not os.path.isdir(dfull) and not dest.endswith("/"):
        out[dfull] = srcs[0]
    else:
        for x in srcs:
            if x:
                out[os.path.join(dfull, os.path.basename(x))] = x
    return out


TOOL_SHELL = "/usr/bin/zsh"  # the $SHELL the Bash tool's shell exports, measured 2026-09-27


def _shell_dialect(argv: list[str], caller: str, body: str | None = None,
                   env: dict[str, str | None] | None = None) -> str:
    """The shell that runs a carrier's text or a script: the one named, a script's shebang, else sh."""
    name = prog(argv[0]) if argv else ""
    if name in ("eval", "source", "."):
        return caller
    if name in ("flock", "script"):  # they run their text in the call's $SHELL, not the hook's
        name = os.path.basename((env or {}).get("SHELL") or TOOL_SHELL)
    for shell, dialect in (("bash", "bash"), ("zsh", "zsh"), ("fish", "fish"), ("dash", "sh"), ("sh", "sh")):
        if name == shell:
            return dialect
    first = body.split("\n", 1)[0] if body and body.startswith("#!") else ""
    for shell in ("bash", "zsh", "fish"):
        if re.search(rf"[/ ]{shell}\b", first):
            return shell
    return "sh"  # watch and a script with no shebang run under sh


def _sourced_text(path: str, written: dict[str, str | None] | None = None) -> str | None:
    """A sourced file's text, for the reader to run in place; None for anything it should not read."""
    if written is not None and path in written:
        return written[path]  # the call wrote it first: its text, or None when it cannot be told
    body = read_script(path)
    return None if body is None or body == BINARY else body


@dataclass
class _Budget:
    """What one call's reading has spent: its commands, the scripts already read, the files already opened."""
    commands: int = 0
    read: set[tuple[object, ...]] = field(default_factory=set)
    opened: set[int] = field(default_factory=set)  # redirections already opened: a group's, written again


_CALL: list[_Budget] = [_Budget()]  # the budget of the call being read, shared by what its Python starts


_FILTERS = ("cat", "sed", "awk", "gawk", "perl", "grep", "head", "tail", "cut", "sort")
_COUNTED = re.compile(r"(\d+)(?:,(\d+|\$))?p")  # sed -n '5p', '1,72p', '3,$p'


def _count(value: str | None) -> tuple[str, int] | None:
    """A head or tail count: its sign (`+` from a line, `-` all but) and its number."""
    m = re.fullmatch(r"([+-]?)(\d{1,9})", value or "")
    return (m.group(1), int(m.group(2))) if m else None


def _filtered(name: str, args: list[str], cwd: str | None,
              files: dict[str, str | None]) -> tuple[str | None, str | None]:
    """A filter's output where the reader can tell it, and the file it is still a copy of.

    cat copies; sed without -n and perl -p edit every line, so their output is their source, nearly; head, tail
    and sed -n with line numbers select lines the reader counts; any other filter's output is unknown.
    """
    count, quiet, script, sources, k = None, False, None, [], 0
    while k < len(args):
        a = args[k]
        value = args[k + 1] if k + 1 < len(args) else None
        if name in ("head", "tail") and a in ("-n", "--lines") or name == "sed" and a in ("-e", "--expression"):
            count, script, k = (_count(value), script, k + 2) if name != "sed" else (count, value, k + 2)
            continue
        if name in ("head", "tail") and re.fullmatch(r"-n?[+-]?\d+|--lines=[+-]?\d+|\+\d+", a):
            count = _count(re.sub(r"^(?:-n?|--lines=)(?=[+-]?\d)", "", a) if not a.startswith("+") else a)
        elif name == "sed" and (a in ("--quiet", "--silent") or re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a)):
            quiet = True
        elif name == "perl" and re.fullmatch(r"-[a-zA-Z]*p[a-zA-Z]*", a):
            quiet = True  # perl -p: every line printed, as sed without -n
        elif a.startswith("-") and a != "-":
            if name in ("head", "tail", "cat") or a.startswith(("-f", "--file", "-i", "--in-place")):
                return None, None  # bytes, follow, a program file, an edit in place: out of count
        elif name in ("sed", "perl", "awk", "gawk", "grep") and script is None:
            script = a
        else:
            sources.append(a)
        k += 1
    texts = [_file_text(resolve(p, cwd), files) for p in sources]
    if not sources or any(x is None for x in texts) or (name in ("head", "tail") and len(sources) > 1):
        return None, None
    text = "".join(x for x in texts if x is not None)
    single = resolve(sources[0], cwd) if len(sources) == 1 else None
    if name == "cat":
        return text, single
    if (name == "sed" and not quiet) or (name == "perl" and quiet):
        return Approx(text), single
    lines = text.splitlines(keepends=True)
    picked = None
    if name in ("head", "tail"):
        sign, n = count or ("", 10)
        if name == "head":
            picked = lines[:-n] if sign == "-" else lines[:n]
        else:
            picked = lines[max(n, 1) - 1 :] if sign == "+" else lines[-n:] if n else []  # +0 reads as +1
    m = _COUNTED.fullmatch(script or "") if name == "sed" else None
    if m:
        end = len(lines) if m.group(2) == "$" else int(m.group(2) or m.group(1))
        picked = lines[int(m.group(1)) - 1 : end]
    if picked is None:
        return None, None
    selected = "".join(picked)
    return selected, single if selected == text else None  # every line selected: still a copy of the file


def _stdin_origin(cmd: _shell.Command, prev: _shell.Command | None) -> str | None:
    """The one file a command's stdin is a copy of: a `<` file, or a `cat FILE` piped in."""
    sources = _stdin_sources(cmd)
    last = sources[-1] if sources else None
    if last is not None and last.op == "<" and last.target is not None and last.target.value:
        return resolve(last.target.value, cmd.cwd)
    if last is not None and last.op == "<|" and prev is not None and prev.words and prog(prev.argv[0]) == "cat" \
            and not prev.bodies():
        named = [a for a in prev.argv[1:] if not a.startswith("-")]
        return resolve(named[0], prev.cwd) if len(named) == 1 else None
    return None


def _git_shown(cmd: _shell.Command) -> str | None:
    """The file `git show REV:PATH` prints: PATH from the top of the repository git runs in."""
    argv = cmd.argv
    if "show" not in argv:
        return None
    k = argv.index("show")
    where = resolve(argv[argv.index("-C") + 1], cmd.cwd) if "-C" in argv[:k] and argv.index("-C") + 1 < k \
        else cmd.cwd
    top = _shell.tree_of(where) if where else None
    spec = next((a for a in argv[k + 1 :] if ":" in a and not a.startswith("-")), None)
    if not top or spec is None:
        return None
    rel = spec.split(":", 1)[1]
    return os.path.normpath(os.path.join(where if rel.startswith("./") else top, rel))


def _remember_written(cmd: _shell.Command, files: dict[str, str | None], opened: set[int],
                      prev: _shell.Command | None = None) -> None:
    """What a call writes into a file before it runs it, and the file each write is a copy of: a heredoc's
    text, a copy's, a near copy's (an edit of every line); unknown for anything else."""
    _forget_edited(cmd, files)
    targets: list[tuple[str, bool]] = []
    for r in cmd.redirects:
        if r.op in (">", ">|", ">>", "&>", "&>>") and r.target is not None and r.target.value:
            full = resolve(r.target.value, cmd.cwd)
            if full:  # a group's file, opened once for all its commands, is appended to after the first
                targets.append((full, r.op in (">>", "&>>") or id(r) in opened))
                opened.add(id(r))
    name = prog(cmd.argv[0]) if cmd.words else ""
    if name == "tee":
        append = "-a" in cmd.argv[1:]
        targets += [(p, append) for p in (resolve(a, cmd.cwd) for a in cmd.argv[1:] if not a.startswith("-")) if p]
    if not targets:
        return
    body, origin = None, None
    if name in ("cat", "tee") and not [a for a in cmd.argv[1:] if not a.startswith("-") and name == "cat"]:
        body = _stdin_text(cmd, prev, files)  # what the shell gives it: bash its last source, zsh every one
        origin = _stdin_origin(cmd, prev)
    elif name == "tr":  # every character mapped: a near copy of what it reads
        text = _stdin_text(cmd, prev, files)
        body, origin = (Approx(text), _stdin_origin(cmd, prev)) if text is not None else (None, None)
    elif name == "git" and _git_shown(cmd):
        origin = _git_shown(cmd)
        text = read_regular(origin) if origin else None
        body = Approx(text) if text is not None else None  # the committed file: near the one on disk
    elif name in ("echo", "printf", "print") and all(w.value is not None for w in cmd.words):
        body = _printed(name, cmd.argv[1:], cmd.dialect)
    elif name in _FILTERS and all(w.value is not None for w in cmd.words):
        body, origin = _filtered(name, cmd.argv[1:], cmd.cwd, files)
    for full, append in targets:
        if origin:
            _ORIGIN[full] = origin  # a copy, or an edit of every line, is still the script it came from
        elif not append:
            _ORIGIN.pop(full, None)  # written over with something else: no longer that script
        if body is None:
            files[full] = None  # written by something the reader cannot follow
        elif append:
            before = files[full] if full in files else read_regular(full)
            joined = (before or "") + body
            files[full] = None if before is None and full in files else (
                Approx(joined) if isinstance(before, Approx) or isinstance(body, Approx) else joined)
        else:
            files[full] = body
