#!/home/nicolas/.local/bin/python3.14
"""Block shell polling loops, and jobs nothing will stop, before they run.

WHY A HOOK AND NOT A NOTE. The rule against these loops has been written down
as a memory for a long time and was still broken 390 times across 19 sessions,
63 of them with `pgrep -f`. The reason is structural rather than forgetful: the
Bash tool's own description RECOMMENDS the shape -- "tell me when the build
finishes -> until grep -q ...; do sleep 0.5; done" -- and that text is in
context at the moment the command is written, where the memory is not. A rule
that loses an argument at the call site has to be moved to where it cannot be
argued with.

WHAT IS WRONG WITH THEM, in the order the failures actually happen:

  * `pgrep -f <pattern>` matches the WATCHER's own command line, because the
    shell that runs the loop carries the pattern as an argument. The condition
    is then permanently true and the loop never ends. Two of this session's
    three watchers died this way.
  * A watcher that polls for work already tracked by the harness is pure waste:
    a backgrounded command re-invokes on exit, so the notification is already
    coming.
  * A watcher outlives the task registry: the harness reports the shell as
    completed while its processes run on, so the next thing that looks for them
    finds a lock held by a job nobody is watching.

HOW A CALL IS READ. The text is read as shell by `_shell.py` and flattened by
`_commands.py` (beside this file, shared with heavy-run-guard.py): a polling
loop is a `sleep` in the condition or body of a while, until or for loop, or
a wait with a timeout there (tail, cat, read, sleep, inotifywait or pidwait
under `timeout`, `read -t`, `inotifywait -t`), and a sleep through Python,
perl or ruby counts as `sleep` does. Each loop is judged alone: a while or
until loop whose body only spins (`:`, true, continue) polls whatever its
condition runs, unless the condition only reads input, which ends; one whose
commands are only checks (test or [, kill, pgrep, pidof, ps, grep, stat, ls,
false) and `:` or true spins too; and so does Python's `while` or `for` around
time.sleep or asyncio.sleep, in code the call spells or a script outside every
tree, as its shell twin does.
Wherever the call runs it: a heredoc, a string, a commit message that merely
names the shape is text, not a loop. What a carrier runs (bash -c, eval ...) and a script of the
user's that the call runs are read as part of the call. `pidwait -f` with a
pattern the call's own shell carries waits on itself and is refused, in Bash
and in Monitor.

AND THE MONITOR TOOL IS COVERED TOO. Monitor is SUPPOSED to poll, so its loop
is allowed; what it may not do is look for a process by a pattern its own
script carries: a Monitor script's shell has the script's text as argv, so
`pgrep -f X` (or -af, -fl, --full) matches itself whenever X is written in
the script. The pattern is tried as pgrep tries it: a POSIX extended regular
expression (grep -E, with -i and -x as given) against the shell's whole
command line as measured 2026-09-27 (`zsh -c source <snapshot> ... setopt ...
unalias ... eval '<the script>' ...`), as one record. `'[x]yz'` passes while
`xyz` is written nowhere else, `'^cargo'` passes because that line starts with
the shell, and -x passes unless the pattern spans the whole line. A pattern
the reader cannot know is taken to match. `ps ... | grep P ...` is a filter
chain run over every line ps prints for the call (the shell's, and each grep's
own, which holds its pattern, listed as ps lists it: the tool shell's grep is
the embedded ugrep, `ugrep -G --ignore-files ... PATTERN`, whose basic regex
the hook tries with GNU grep -G; rg and ugrep stages as grep -E, an awk
`/re/` stage by its regex; a `grep -v grep` stage removes what it names. The
hook's greps get 1.5 s in all and 1 s each; past that a pattern is tested as
plain text.

A JOB PUT IN THE BACKGROUND OUTLIVES THE CALL. The harness ends a Bash or
Monitor task when the script's own shell exits and stops nothing else: a `&`
job is reparented to init and runs on. Measured 2026-09-27: nine Monitor
scripts of one session had each run `tail -f "$log" | grep --line-buffered
... &` and then waited on a pid; each was reported "stream ended, completed",
and all nine `tail | grep` pipelines were still running a week later, three
processes each, since `tail -f` never ends and everything behind it waits on
it. So a `&` job is refused unless the same shell reaps it or stops it. A job
is a process `&` forks: one for a command, a subshell, a group or an and-or
list, whatever it runs inside, and one per element of a bare pipeline, since
`$!` names only its last. In order, per shell, among the jobs started before
it: a bare `wait` reaps them all, `wait "${pids[@]}"` too, `wait $a $b` as
many as it names (never a literal pid, which names no child), `wait %%` the
current job, `kill $pid` stops one, `kill 0`, `kill -- -$$` and `pkill -P $$`
stop them all, and a kill whose signal stops nothing (0, CONT, STOP, TSTP ...)
none; a `wait` in another
subshell, or in a function never called, reaps nothing. The call's own text
runs in the tool's zsh 5.9 (measured 2026-09-27), where `wait -n`, `wait -p`,
a numbered job spec (`%1`: the call's first job is not 1) and `$(jobs -p)`
reap or stop nothing and `&!` disowns; there a pipeline's last `wait` runs in
the shell and reaps. Text run by bash (`bash -c`, a bash script) keeps bash's
meaning: `wait -n` reaps one, `%1` its first job, a pipeline's `wait` nothing.
On exit, the EXIT trap in force at the end stops the whole group when it
kills it, and otherwise one job per pid it kills or waits for; `trap - EXIT`
clears it. A job a trap puts in the background, run as the shell exits or as a
signal arrives, is refused unless the same trap waits for it.
`disown` and the wrappers that detach outright (setsid -f, tmux -d, screen
-dm, systemd-run without --scope, daemonize, start-stop-daemon -b) are
refused.

WHAT TO DO INSTEAD is in the message this prints, because a refusal that does
not name the alternative just gets worked around.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _commands as C  # noqa: E402
import _shell  # noqa: E402

# The fallback for text the reader cannot follow: the shapes as text.
SELF_MATCH = re.compile(r"\bp(?:grep|kill)\s+(?:-[a-zA-Z]*f[a-zA-Z]*|--full)\b|\bps\b[^|\n]*\|\s*grep\b")
BRACKETED = re.compile(r"\b(?:p(?:grep|kill)|grep)\s+(?:-\S+\s+)*\S*\[\w\]")
_BACKGROUND_LINE = re.compile(r"(?<![&|>])&\s*(?:\n|$)")
EXIT_SIGNALS = {"EXIT", "SIGEXIT", "0"}
ALL = 1 << 30  # what a bare `wait` reaps: every earlier job
# The command line pgrep -f sees for the Bash or Monitor tool's shell, as measured 2026-09-27: the call's text
# quoted inside an eval, after the snapshot source and the setopt/unalias prefix. Bash adds `< /dev/null` unless
# the call redirects its own stdin; both Bash lines are tried.
_HOME = os.environ.get("HOME", "/home/user")
_SHELL_HEAD = (f"/usr/bin/zsh -c source {_HOME}/.claude/shell-snapshots/snapshot-zsh-0000000000000-000000.sh "
               "2>/dev/null || true && setopt NO_EXTENDED_GLOB NO_BARE_GLOB_QUAL 2>/dev/null || true && "
               "{ \\builtin unalias -- 'unsetenv'; \\builtin unset -f -- 'unsetenv'; } >/dev/null 2>&1 || true && "
               "eval '")
_SHELL_TAILS = {"Bash": ("' < /dev/null && pwd -P >| /tmp/claude-0000-cwd", "' && pwd -P >| /tmp/claude-0000-cwd"),
                "Monitor": ("' && pwd -P >| /tmp/claude-0000-cwd",)}
# The tool shell's grep is a function running the embedded ugrep under this argv; ps lists a grep stage so.
_UGREP_ARGV = ("ugrep -G --ignore-files --hidden -I --exclude-dir=.git --exclude-dir=.svn --exclude-dir=.hg "
               "--exclude-dir=.bzr --exclude-dir=.jj --exclude-dir=.sl")
_PS_COLUMNS = "user        1234  0.0  0.0  12345  6789 ?        S    12:00   0:00 "  # what ps aux prints before it
_PGREP_VALUES = {"-u", "-U", "-g", "-G", "-P", "-s", "-t", "--signal", "-d", "--delimiter", "--ns", "--nslist",
                 "-F", "--pidfile"}

ADVICE = """BLOCKED: a shell polling loop.

Instead:
  * The work is already tracked -- run it with run_in_background and the harness
    re-invokes you when it exits. No watcher needed.
  * Waiting on a pid you did not start with the tool: read its output file once
    per turn, or `wait` on it if it is a child of this shell.
  * A condition only an external system changes (CI, a deploy): use the Monitor
    tool. Its LOOP is fine there -- but the pattern rule below still applies to
    it, and is enforced.
  * Never `pgrep -f <pattern>` from a shell whose own command line contains that
    pattern -- it matches itself and the loop never ends."""


MONITOR_ADVICE = """BLOCKED: a Monitor script that looks for a process by PATTERN.

Monitor is allowed to loop -- that is what it is for. What it may not do is ask
`pgrep -f`/`pkill -f` (or -af, -fl, --full) about a pattern, because the shell
running this script carries the script's own text as argv: the pattern is in it
by construction, so the process is always "found" and the watch either never
ends or reports the opposite of the truth.

Instead:
  * Capture the pid in an EARLIER call, with a pattern that cannot match
    that call's own shell either (`pgrep -f '[c]argo test'`, the bracket
    keeping the literal off its command line, or `pgrep -x cargo`), and test
    it here with `kill -0 <that literal pid>`.
  * Or watch for something the WORK leaves behind that this script does not
    write: an exit marker, a summary line, a file it creates when it finishes.
  * The pattern is matched as pgrep matches it, a regular expression over
    this shell's command line: `pgrep -f '[x]pattern'` passes while the plain
    `xpattern` is written nowhere else here, and so do an anchored
    `'^pattern'` and `pgrep -fx` with a pattern short of the whole line."""


PIDWAIT_ADVICE = """BLOCKED: `pidwait -f` with a pattern this call's own shell carries.

pidwait blocks until no process matches, and the shell running this call holds the pattern in its own
command line: it waits on itself and never returns. Bracket the pattern (`pidwait -f '[c]argo test'`);
`wait $p` waits only for a job this same call started, so for any other pid watch `kill -0 <pid>` from
Monitor instead."""


ORPHAN_ADVICE = """BLOCKED: {what}, and nothing in this call stops it.

The harness ends this task when the script's own shell exits and stops nothing
else, so the job is reparented to init and runs on unwatched -- `tail -f` never
ends, and a pipeline behind it waits on it forever (nine such watchers of one
session were found still running a week later).

Instead:
  * Run the work itself with the Bash tool's run_in_background, or watch a log
    with Monitor running `tail -f LOG | grep --line-buffered PATTERN` in the
    FOREGROUND, as the script's last command.
  * If the script must run jobs side by side, end it with a bare `wait`, in
    the same shell that started them. `wait $!` names one process: for a
    pipeline job (`tail -f LOG | grep X &`) that is the grep, and the tail
    runs on.
  * If a helper job must run beside the script's own work, stop it on exit:
    `trap 'trap "" TERM; kill 0' EXIT` before starting it. The tool's shell
    leads its own process group, so a plain `kill 0` kills the shell too and
    the call reports exit 144; ignoring TERM first keeps the call's status.
  * The tool's shell is zsh: `wait -n`, `wait %1` and `kill $(jobs -p)` do
    nothing there, and `&!` disowns."""


def _loop_in_text(text: str) -> bool:
    """The fallback: `while`, `until` or `for`, then `sleep` before its `done` (or fish's `end`), a word scan."""
    inside = False
    for word in re.findall(r"[A-Za-z_]+", text):
        if word in ("while", "until", "for"):
            inside = True
        elif inside and word == "sleep":
            return True
        elif word in ("done", "end"):
            inside = False
    return False


_BUSY = {":", "true", "false", "test", "[", "kill", "pgrep", "ps", "grep", "stat", "ls", "pidof"}


def _waits(inv: "C.Inv") -> bool:
    """A wait with a timeout in a loop: `timeout N tail -f`, `read -t`, `inotifywait -t`: polling spelled otherwise."""
    core = C.prog(inv.core.argv[0]) if inv.core.argv else ""
    wrappers = inv.cmd.argv[: max(0, len(inv.cmd.argv) - len(inv.core.argv))]
    if any(C.prog(w) == "timeout" for w in wrappers) and core in ("tail", "inotifywait", "pidwait", "read", "sleep",
                                                                  "cat"):
        return True
    timed = any(re.fullmatch(r"-[a-zA-Z]*t\w*", a) or re.fullmatch(r"--timeout(=.*)?", a) for a in inv.core.argv[1:])
    return core in ("read", "inotifywait") and timed


_SPIN = {":", "true", "continue"}


def _busy(ours: list["C.Inv"]) -> bool:
    """A busy loop, each loop judged alone: one whose body only spins (`until curl ...; do :; done`) polls
    whatever its condition runs, unless the condition only reads input, which ends; and one made of checks
    and a `:` with nothing between (`while :; do kill -0 $p || break; done`)."""
    loops: dict[int, list["C.Inv"]] = {}
    for i in ours:
        if i.cmd.loop in ("while", "until") and i.core.argv:
            loops.setdefault(i.cmd.loop_id, []).append(i)
    for invs in loops.values():
        names = {C.prog(i.core.argv[0]) for i in invs}
        body = {C.prog(i.core.argv[0]) for i in invs if i.cmd.loop_body}
        cond = {C.prog(i.core.argv[0]) for i in invs if not i.cmd.loop_body}
        if body and body <= _SPIN and cond != {"read"}:
            return True
        if {":", "true"} & names and names <= _BUSY:
            return True
    return False


def _sleeps(inv: "C.Inv") -> bool:
    """An interpreter's sleep is a loop's delay as `sleep` is: `python3 -c 'import time; time.sleep(1)'`."""
    if not inv.code:
        return False
    lang, text = inv.code
    if lang == "python":
        return C.python_facts(text, inv.cmd.cwd).sleeps
    return lang in ("perl", "ruby") and re.search(r"\bsleep\b", text) is not None


def _text_problem(text: str, tool: str) -> str | None:
    """The shapes as text, for what the reader cannot follow: a whole call, or a carrier's text."""
    if tool == "Monitor":
        return MONITOR_ADVICE if SELF_MATCH.search(text) and not BRACKETED.search(text) else None
    if _loop_in_text(text):
        return ADVICE
    if _BACKGROUND_LINE.search(text) and not re.search(r"\bwait\b|\bkill\s+0\b", text):
        return ORPHAN_ADVICE.format(what="a job put in the background")
    return None


def _grep_stage(inv: "C.Inv") -> tuple[str, str] | None:
    """A grep stage's pattern and grep flags (its dialect, -v, -i, -w, -x), or None for a stage it cannot read."""
    name = C.prog(inv.core.argv[0])
    args = inv.core.argv[1:]
    if name in ("awk", "gawk"):  # awk '/re/' selects the lines its pattern matches
        program = next((a for a in args if not a.startswith("-")), "")
        m = re.fullmatch(r"\s*(!?)\s*/((?:\\.|[^/\\])*)/\s*(?:\{.*\})?\s*", program, re.S)
        return (m.group(2), "E" + ("v" if m.group(1) else "")) if m else None
    dialect = {"egrep": "E", "fgrep": "F", "rg": "E", "ugrep": "E"}.get(name, "G")
    flags, pattern, given, k = "", None, [], 0
    while k < len(args):
        a = args[k]
        if a == "--":
            pattern = args[k + 1] if k + 1 < len(args) else None
            break
        if a in ("-e", "--regexp") and k + 1 < len(args):
            given.append(args[k + 1])  # each -e is a pattern of its own: grep takes them one per line
            k += 2
            continue
        if a in ("-f", "--file", "-m", "--max-count", "-A", "-B", "-C"):
            k += 2
            continue
        if a.startswith("--"):
            flags += {"--invert-match": "v", "--ignore-case": "i", "--word-regexp": "w", "--line-regexp": "x",
                      "--fixed-strings": "F", "--extended-regexp": "E", "--basic-regexp": "G"}.get(a, "")
        elif a.startswith("-"):
            flags += a[1:]
        elif pattern is None:
            pattern = a
        k += 1
    if given:  # with -e given, a bare operand is a file: the patterns are the -e ones, any of them matching
        pattern = "\n".join(given)
    if pattern is None:
        return None
    for d in "EFGP":
        if d in flags:
            dialect = d
    return pattern, dialect + "".join(f for f in "viwx" if f in flags)


def _ps_grep_self_match(invs: list["C.Inv"], raw: str, tool: str = "Monitor") -> bool:
    """`ps ... | grep P ...`: ps lists this call's own processes, which are seen unless a stage filters them out.

    Those are the shell's line, which holds the whole script, and each stage's own command line, which holds its
    pattern as grep received it: `grep "car""go"` is `grep cargo` there, and meets itself.
    """
    for start, inv in enumerate(invs):
        if not (inv.core.argv and C.prog(inv.core.argv[0]) == "ps" and inv.cmd.pipe_out):
            continue
        stages = []
        for stage in invs[start + 1 :]:
            if stage.cmd.feeds and not inv.cmd.feeds:
                continue  # a substitution inside a stage (`grep "$(cat f)"`): its commands are not stages
            if not (stage.core.argv and C.prog(stage.core.argv[0]) in ("grep", "egrep", "fgrep", "rg", "ugrep", "awk",
                                                                       "gawk") and stage.cmd.pipe_in):
                break
            read = _grep_stage(stage)
            if read is None:
                break
            stages.append((stage, *read))
            if not stage.cmd.pipe_out:
                break
        if not any("v" not in flags for _, _, flags in stages):
            continue
        lines = [*_shell_lines(raw, tool), " ".join(inv.core.argv)] + [_stage_line(s2) for s2, _, _ in stages]
        for line in (_PS_COLUMNS + ln for ln in lines):
            survives = True
            for _, pattern, flags in stages:
                if any(mark in pattern for mark in ("${", "$(", "`")):
                    continue  # a pattern out of sight may select the line, and a -v one may leave it
                hit = _grep(pattern, line, flags.replace("v", ""))
                hit = (pattern in line) if hit is None else hit
                survives = survives and (hit != ("v" in flags))
            if survives:
                return True
    return False


def _pgrep_self_match(inv: "C.Inv", raw: str, tool: str = "Bash") -> bool:
    """Whether a pgrep -f pattern matches the script's own text, as pgrep would match its shell."""
    argv = inv.core.argv
    if not argv or C.prog(argv[0]) not in ("pgrep", "pkill", "pidwait"):
        return False
    args = argv[1:]
    full = "--full" in args or any(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", a) for a in args)
    if not full:
        return False
    k = 0
    pattern = None
    while k < len(args):
        if args[k] == "--":
            pattern = args[k + 1] if k + 1 < len(args) else None
            break
        if args[k] in _PGREP_VALUES:
            k += 2
            continue
        if not args[k].startswith("-"):
            pattern = args[k]
            break
        k += 1
    if pattern is None:
        return False
    if any(mark in pattern for mark in ("${", "$(", "`")):
        return True  # a pattern the reader cannot know may be anything, the empty one included: it meets itself
    opts = [a for a in args[:k] if a.startswith("-") and not a.startswith("--")]
    exact = "--exact" in args or any("x" in o for o in opts)
    ignore_case = "--ignore-case" in args or any("i" in o for o in opts)
    return _pgrep_matches(pattern, raw, exact, tool, ignore_case)


def _shell_lines(raw: str, tool: str) -> list[str]:
    quoted = _SHELL_HEAD + raw.replace("'", "'\"'\"'")
    return [quoted + tail for tail in _SHELL_TAILS.get(tool, _SHELL_TAILS["Bash"])]


def _stage_line(inv: "C.Inv") -> str:
    """A filter stage's own line in ps: the tool zsh's `grep` is ugrep behind the snapshot's fixed options, unless
    an option sends it to the system grep (-z, --null, a filter or pager option)."""
    argv = inv.core.argv
    if C.prog(argv[0]) == "grep" and inv.cmd.dialect == "zsh" and inv.cmd.argv[:1] == ["grep"] and not any(
            re.match(r"-[^-]*[Zz]|--null|-.*-(?:filter|pager|view|format-open|config)|---|-@", a) for a in argv[1:]):
        return " ".join([_UGREP_ARGV, *argv[1:]])
    return " ".join(argv)


_GREP_SECONDS = [1.5]  # what every grep of one hook run may spend together


def _grep(pattern: str, line: str, flags: str) -> bool | None:
    """Whether grep with `flags` selects `line` taken whole as one record; None when grep could not say."""
    if _GREP_SECONDS[0] <= 0:
        return None
    t0 = time.monotonic()
    try:
        p = subprocess.run(["grep", "-zq" + flags, "--", pattern], input=line.encode(errors="surrogateescape") + b"\0",
                           capture_output=True, timeout=min(1.0, _GREP_SECONDS[0]), check=False)
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        return None  # a NUL, an unencodable character or a pattern grep cannot settle
    finally:
        _GREP_SECONDS[0] -= time.monotonic() - t0
    return p.returncode == 0 if p.returncode in (0, 1) else None


def _pgrep_matches(pattern: str, raw: str, exact: bool, tool: str = "Bash", ignore_case: bool = False) -> bool:
    """Whether pgrep's pattern, a POSIX extended regex, matches the shell running `raw` as pgrep tests it.

    grep -E reads the pattern in pgrep's own dialect (`\\<` is a word edge), -z takes the whole command
    line as one record as pgrep does, and grep's matcher does not backtrack: a pattern it cannot settle
    within its share of the grep budget, or refuses, is tested as plain text. Either Bash line matching is a match.
    """
    flags = "E" + ("x" if exact else "") + ("i" if ignore_case else "")
    for line in _shell_lines(raw, tool):
        hit = _grep(pattern, line, flags)
        if (pattern in raw) if hit is None else hit:
            return True
    return False


def _reaped(inv: "C.Inv") -> int:
    """How many of its shell's jobs a `wait` reaps: all when bare or given an array, one with -n, else one per pid.

    In the tool's zsh (and in sh) `wait -n` and `wait -p` are errors that reap nothing, and zsh runs the last
    element of a pipeline in the shell itself, so a `wait` there reaps.
    """
    argv = inv.core.argv
    zsh = inv.cmd.dialect == "zsh"
    if not argv or argv[0] != "wait" or inv.cmd.pipe_out or (inv.cmd.pipe_in and not zsh):
        return 0  # a wait in a pipeline runs in a subshell: it reaps nothing
    words = inv.cmd.words[1:]
    one, operands, named, k = False, 0, 0, 0
    while k < len(words):
        text = words[k].text
        if text in ("-p", "-n") or re.fullmatch(r"-[a-z]*[np][a-z]*", text):
            if inv.cmd.dialect != "bash":
                return 0  # zsh: "job not found: -n"
            if text == "-p":
                k += 2  # -p VAR: where the reaped pid is stored
                continue
        if text.startswith("-"):
            one = one or "n" in text
        elif re.search(r"\[[@*]\]|^\"?\$[@*]\"?$", text):
            return 1 if one else ALL  # "${pids[@]}": every pid the array holds
        else:
            operands += 1
            # A literal number names no child of this shell, nor a variable the call never set; a variable
            # it set ($!, "$pid") can.  A job spec is counted by orphan_jobs, which knows the jobs.
            var = re.fullmatch(r'"?\$\{?(\w+)\}?"?', text)
            unset = var is not None and var.group(1) not in inv.cmd.env and not var.group(1).isdigit() \
                and var.group(1) not in os.environ
            named += not (re.fullmatch(r"\d+", text) or unset or text.startswith("%"))
        k += 1
    if not operands:
        return 1 if one else ALL
    return min(1, named) if one else named


_HARMLESS_SIGNALS = {"0", "CONT", "SIGCONT", "STOP", "SIGSTOP", "TSTP", "SIGTSTP", "TTIN", "TTOU", "WINCH", "CHLD",
                     "SIGCHLD", "URG", "18", "19", "20", "28"}


def _kill_operands(words: list[str], dialect: str, specs: bool = True) -> int:
    """How many jobs one kill's words stop: none for a signal that stops nothing, ALL for this shell's group
    (`0`, `-$$`) or every process (`-1`), one per pid it kept.  Only the first option is the signal; a word
    after it is an operand, so `-$$` there is a group.  Job specs count only when `specs`: a kill the reader
    sees has them taken by orphan_jobs, which knows the jobs."""
    text = " ".join(words)
    if dialect == "zsh" and re.search(_JOBS_P, text):
        words = re.sub(_JOBS_P, " ", text).split()  # zsh prints nothing for it inside $( ): no pid at all
    signal, k = None, 1
    if k < len(words) and words[k] in ("-s", "-n") and k + 1 < len(words):
        signal, k = words[k + 1], k + 2
    elif k < len(words) and words[k].startswith("-") and words[k] not in ("-", "--"):
        signal, k = words[k][1:], k + 1
    if k < len(words) and words[k] == "--":
        k += 1
    if (signal or "").upper() in _HARMLESS_SIGNALS:
        return 0
    ops = words[k:]
    if re.search(_JOBS_P, " ".join(ops)):
        return ALL  # bash's $(jobs -p): every job
    count = 0
    for w in ops:
        if w.strip("\"'") in ("0", "-$$", "-${$}", "-1") or re.search(r"\[[@*]\]", w):
            return ALL  # the group, every process, or "${pids[@]}"
        if w.startswith("%"):
            count += specs and (w in ("%%", "%+", "%") or dialect != "zsh")
        elif re.match(r'"?\$', w):
            count += 1  # a pid it kept
    return count


def _pkill_stops(words: list[str]) -> int:
    """`pkill -P $$` stops every child of this shell; any other pkill stops no job of it for sure."""
    for k, w in enumerate(words[1:], 1):
        sig = words[k + 1] if w == "--signal" and k + 1 < len(words) else w.split("=", 1)[1] \
            if w.startswith("--signal=") else w[1:] if re.fullmatch(r"-(?:\d+|(?:SIG)?[A-Z]{2,}\d*)", w) else None
        if sig is not None and sig.upper() in _HARMLESS_SIGNALS:
            return 0  # -0, -CONT, --signal 0: the children go on
    for k, w in enumerate(words):
        value = words[k + 1] if w in ("-P", "--parent") and k + 1 < len(words) else \
            w[2:] if w.startswith("-P") and len(w) > 2 else w.split("=", 1)[1] if w.startswith("--parent=") else None
        if value is not None and value.strip("\"'") in ("$$", "${$}"):
            return ALL
    return 0


def _killed(inv: "C.Inv") -> int:
    """How many of its shell's jobs a `kill` or `pkill -P $$` stops."""
    argv = inv.core.argv
    words = [w.text for w in inv.cmd.words]
    if argv[:1] == ["kill"]:
        return _kill_operands(words, inv.cmd.dialect, specs=False)
    if argv[:1] == ["pkill"]:
        return _pkill_stops(words)
    return 0


_JOBS_P = r"\$\(\s*jobs\s+-p\s*\)|`jobs -p`"  # zsh prints nothing for it inside $( ): it stops nothing there


def _job_specs(inv: "C.Inv") -> list[str]:
    argv = inv.core.argv
    if not argv or argv[0] not in ("wait", "kill") or inv.cmd.pipe_out or (inv.cmd.pipe_in and
                                                                            inv.cmd.dialect != "zsh"):
        return []
    return [w.text for w in inv.cmd.words[1:] if w.text.startswith("%")]


def _trap_stops(text: str, dialect: str) -> int:
    """How many jobs an EXIT trap's text stops on exit: its kills, or its waits, whichever is more."""
    kills = sum(_pkill_stops(m.group(0).split()) if m.group(1) == "pkill" else _kill_operands(m.group(0).split(), dialect)
                for m in re.finditer(r"\b(p?kill)\b[^;|&]*", text))
    waits = [m.group(1).split() for m in re.finditer(r"\bwait\b([^;|&]*)", text)]
    reaped = sum(ALL if not w else len([x for x in w if x.startswith("$")]) for w in waits)
    return max(kills, reaped)


def orphan_jobs(invs: list["C.Inv"]) -> list[str]:
    """The jobs a call leaves running past its end, one per process `&` forked."""
    ours = [i for i in invs if i.origin in ("call", "script")]
    if any(i.core.argv[:1] == ["disown"] for i in ours):
        return ["a job is disowned"]
    disowned = [f"`{' '.join(i.cmd.argv)[:60]}` is disowned with zsh's &!" for i in ours if i.cmd.disowned]
    if disowned:
        return disowned[:1]
    detached = [f"`{' '.join(i.cmd.argv)[:60]}` is detached with {i.core.detached}" for i in ours
                if i.core.detached]
    if detached:
        return detached
    # The EXIT trap that holds at the end: the last one set; `trap - EXIT` or `trap '' EXIT` clears it.
    exit_trap: tuple[str, str] | None = None
    for i in ours:
        if i.core.argv[:1] != ["trap"] or len(i.cmd.words) < 2:
            continue
        k = 2 if i.cmd.words[1].text == "--" and len(i.cmd.words) > 2 else 1  # `trap -- ACTION SIG`
        action_word = i.cmd.words[k]
        action = action_word.value if action_word.value is not None else action_word.text
        if {w.text.upper() for w in i.cmd.words[k + 1 :]} & EXIT_SIGNALS:
            exit_trap = None if action.strip() in ("", "-") else (action, i.cmd.dialect)
    # A trap runs as the shell exits or when a signal arrives: a job it puts in the background outlives the
    # call, unless the same trap waits for it.
    outer = {c.cmd.proc for c in ours if not (c.cmd.function or "").startswith("trap:")}
    for label in sorted({c.cmd.function for c in ours if (c.cmd.function or "").startswith("trap:")}):
        run = [c for c in ours if c.cmd.function == label]
        started = [c for c in run if c.cmd.background and c.core.argv and c.cmd.proc not in outer]  # its own `&`
        if started and not any(c.core.argv == ["wait"] and not c.cmd.background for c in run):
            return [f"`{' '.join(started[0].cmd.argv)[:60]}` runs in the background from a trap"]
    stopped = 0
    if exit_trap is not None:
        action, dialect = exit_trap
        texts = [action] + [" ".join(c.cmd.argv) for c in ours if c.cmd.function == f"trap:{action.strip()}"]
        stopped = min(ALL, sum(_trap_stops(t, dialect) for t in texts))
    # In order, per shell: a process is pending from its start until a wait or a kill in that shell takes it.
    pending: dict[int, list[tuple[int, int]]] = {}  # shell -> (process, job), in start order
    started: dict[int, list[int]] = {}  # shell -> its jobs in start order: what %1, %2 ... name
    label: dict[int, str] = {}
    for i in ours:
        cmd = i.cmd
        if (cmd.function or "").startswith("trap:"):
            continue  # a trap's commands: counted with its trap above; a direct call of that function is not
        if cmd.background and cmd.proc and i.core.argv and i.core.argv[0] != "trap" and cmd.proc not in label:
            label[cmd.proc] = f"`{' '.join(i.cmd.argv)[:60]}` runs in the background"
            pending.setdefault(cmd.bg_scope, []).append((cmd.proc, cmd.job))
            if cmd.job not in started.setdefault(cmd.bg_scope, []):
                started[cmd.bg_scope].append(cmd.job)
        # A wait or kill, in the background or not, takes jobs of the shell it runs in.
        procs = pending.get(cmd.job_scope, [])
        jobs = started.get(cmd.job_scope, [])
        for spec in _job_specs(i):  # `wait %1`, `kill %1`: every process of that job
            job = None
            if spec in ("%", "%%", "%+"):
                job = jobs[-1] if jobs else None
            elif cmd.dialect == "zsh":
                job = None  # the tool's zsh numbers the call's first job 2: a numbered spec names nothing sure
            elif spec == "%-":
                job = jobs[-2] if len(jobs) > 1 else None
            elif spec[1:].isascii() and spec[1:].isdigit() and 0 < int(spec[1:]) <= len(jobs):
                job = jobs[int(spec[1:]) - 1]
            procs[:] = [pj for pj in procs if pj[1] != job]
        taken = _reaped(i) or _killed(i)
        if taken:
            del procs[max(0, len(procs) - taken) :]  # `wait $!` takes the latest
    left = [label[proc] for procs in pending.values() for proc, _ in procs]
    return left[stopped:]


def main() -> int:
    _shell.bound_memory()
    try:
        return decide()
    except Exception as e:  # noqa: BLE001 - a reader fault is reported, never a silent pass
        print(f"no-polling-loops: internal error, the call goes ahead unchecked: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1


def decide() -> int:
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return 0  # a hook that cannot read the event must not block the session
    if not isinstance(event, dict):
        return 0
    tool = event.get("tool_name")
    if tool not in ("Bash", "Monitor") or event.get("hook_event_name") not in ("PreToolUse", None):
        return 0
    ti = event.get("tool_input")
    raw = ti.get("command", "") if isinstance(ti, dict) else ""
    if not isinstance(raw, str) or not raw.strip():
        return 0
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else "/"
    try:
        invs = C.expand(raw, cwd)
    except Exception:  # noqa: BLE001 - text the reader cannot follow falls back to the text shapes
        invs = None
    if invs is None:
        advice = _text_problem(raw, tool)
        if advice:
            print(advice, file=sys.stderr)
            return 2
        return 0
    for inv in invs:  # a carrier's text the reader could not follow (fish -c ...): the text shapes
        if inv.unreadable and inv.core.script:
            advice = _text_problem(inv.core.script, tool)
            if advice:
                print(advice, file=sys.stderr)
                return 2
    jobs = orphan_jobs(invs)
    if jobs:
        print(ORPHAN_ADVICE.format(what="; ".join(jobs[:4])), file=sys.stderr)
        return 2
    ours = [i for i in invs if i.origin in ("call", "script")]
    if tool == "Monitor":
        if any(_pgrep_self_match(i, raw, "Monitor") for i in invs if i.origin == "call") or \
                _ps_grep_self_match([i for i in invs if i.origin == "call"], raw, "Monitor"):
            print(MONITOR_ADVICE, file=sys.stderr)
            return 2
        return 0
    if any(_pgrep_self_match(i, raw, "Bash") for i in ours if i.core.argv and C.prog(i.core.argv[0]) == "pidwait"):
        print(PIDWAIT_ADVICE, file=sys.stderr)  # pidwait blocks until the pattern is gone: its own shell never is
        return 2
    loops = [i for i in ours if i.cmd.loop and i.core.argv and (C.prog(i.core.argv[0]) == "sleep" or _waits(i)
                                                                or _sleeps(i))]
    if not loops and _busy(ours):
        loops = [i for i in ours if i.cmd.loop]
    if not loops:  # Python's own `while ...: time.sleep()` or for loop, in code the call writes, as the shell's
        loops = [i for i in ours if i.code and i.code[0] == "python" and i.code_origin != "repo-script"
                 and C.python_facts(i.code[1], i.cmd.cwd).polls]
    if not loops:
        return 0
    try:  # the verdict is fixed before anything prints: a fault here costs the note, never the refusal
        self_match = any(_pgrep_self_match(i, raw) for i in ours)
    except Exception:  # noqa: BLE001
        self_match = False
    print(ADVICE, file=sys.stderr)
    if self_match:
        print(
            "\nAnd this one polls with `pgrep -f`, so it would also have matched its own\n"
            "command line and never exited.",
            file=sys.stderr,
        )
    return 2  # PreToolUse: block the call and hand stderr back to the model


if __name__ == "__main__":
    sys.exit(main())
