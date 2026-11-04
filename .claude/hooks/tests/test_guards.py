#!/usr/bin/env python3
"""Regression cases for heavy-run-guard.py and no-polling-loops.py.

Every case pipes a synthetic PreToolUse (or PostToolUse) event into the hook
and compares its exit code with what the user's rules say: 2 blocks, 0 allows.
Runs are made live with a sleeper compiled here and started under any argv, so
the process scan reads a real `cargo test` (or `bash probes/run_all.sh`...)
command line with its working directory in a test tree. Every tree is built
under a fresh temporary directory and removed at the end.

Usage: test_guards.py [HOOK_DIR]   (default: the hooks directory, parent of this tests/ directory)
Exit 0 when every case agrees with its expectation.
"""

from __future__ import annotations

import json
from collections import Counter
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal, NamedTuple, NewType, NotRequired, TypedDict, Unpack

# A case's id; prose a case names itself by, and what a hook printed; what a case compares: a hook's exit status as
# the case reads it (2 a rule 5-6 refusal, 3 any other refusal), or a verdict or a count read the same way; a
# command a Bash or Monitor call runs; a word of a command line; Python source a command feeds python3; a path as
# an event spells it; a worktree's name; a tool name as a settings matcher spells it; an event as a hook's stdin
# carries it, whole; a call's, a session's and an agent's id; a timeout in milliseconds; how many of the suite's
# trees a report names.
CaseId = NewType("CaseId", str)
Prose = NewType("Prose", str)
ExitStatus = NewType("ExitStatus", int)
CommandLine = NewType("CommandLine", str)
Word = NewType("Word", str)
Source = NewType("Source", str)
PathText = NewType("PathText", str)
WorktreeName = NewType("WorktreeName", str)
Matcher = NewType("Matcher", str)
RawEvent = NewType("RawEvent", str)
ToolUseId = NewType("ToolUseId", str)
SessionId = NewType("SessionId", str)
AgentId = NewType("AgentId", str)
Millis = NewType("Millis", int)
TreeCount = NewType("TreeCount", int)

type EventName = Literal["PreToolUse", "PostToolUse", "PostToolUseFailure"]
type ToolName = Literal["Bash", "Monitor", "Edit", "Write", "NotebookEdit", "MultiEdit", "EnterWorktree",
                        "ExitWorktree", "Agent"]
type EditTool = Literal["Edit", "Write", "NotebookEdit", "MultiEdit"]


class ToolInput(TypedDict, total=False):
    """A call's input: every key an event of this suite spells."""

    command: CommandLine
    run_in_background: bool
    timeout: Millis
    file_path: PathText
    notebook_path: PathText
    name: WorktreeName
    path: PathText
    description: Prose
    prompt: Prose
    isolation: Literal["worktree"]
    action: Literal["remove", "keep"]


class BashOptions(TypedDict, total=False):
    """What a Bash call carries beside its command."""

    timeout: Millis


class ToolResponse(TypedDict, total=False):
    """A call's response, which every Post event of this suite sends empty."""


class HookEvent(TypedDict):
    """An event a hook reads: every key an event of this suite spells, the last four on some events only."""

    hook_event_name: EventName
    tool_name: ToolName
    tool_input: ToolInput
    cwd: PathText
    tool_response: NotRequired[ToolResponse]
    tool_use_id: NotRequired[ToolUseId]
    session_id: NotRequired[SessionId]
    agent_id: NotRequired[AgentId]


class HookAnswer(NamedTuple):
    """A hook's exit status and its stderr."""

    status: ExitStatus
    err: Prose


class HookOutput(NamedTuple):
    """A hook's exit status, its stdout and its stderr."""

    status: ExitStatus
    out: Prose
    err: Prose


class CaseRow(NamedTuple):
    """One case's result: its id, the status wanted and given, what it checks, and what the hook printed."""

    id: CaseId
    want: ExitStatus
    got: ExitStatus
    what: Prose
    err: Prose


HOOKS = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
GUARD = HOOKS / "heavy-run-guard.py"
LOOPS = HOOKS / "no-polling-loops.py"

BASE = Path(tempfile.mkdtemp(prefix="guard-test-")).resolve()
T = BASE / "live"  # a tree with a heavy run live in it
Q = BASE / "quiet"  # a tree with none
Q2 = BASE / "probe"  # the tree the process-scan cases make live one run at a time
S = BASE / "scratch"  # a plain directory, as the scratchpad is
W = BASE / "wt"  # a linked worktree of T, outside it
SLEEPER = BASE / "sleeper"
RT = BASE / "runner"  # a tree whose store holds light probes only
BLESSED = f'taskset -c 0-19 {{}} > "{S}/x.log" 2>&1; echo "EXIT=$?" >> "{S}/x.log"'
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(repo: Path, *args: Word) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=GIT_ENV)


def setup() -> None:
    for d in (T, Q, Q2, S, BASE / "tmp"):
        d.mkdir(parents=True)
    for d in (T, Q, Q2):
        git(d, Word("init"), Word("-q"))
    (T / "tools").mkdir()
    (T / "tools" / "x.py").write_text("x = 1\n")
    (T / "CHANGELOG.md").write_text("# log\n")
    (T / ".gitignore").write_text(".commands-to-run.sh\n")
    (T / ".commands-to-run.sh").write_text("#!/usr/bin/env bash\n")
    git(T, Word("add"), Word("-A"))
    git(T, Word("-c"), Word("user.name=t"), Word("-c"), Word("user.email=t@t"), Word("-c"),
        Word("commit.gpgsign=false"), Word("commit"), Word("-qm"), Word("base"))
    git(T, Word("worktree"), Word("add"), Word("-q"), Word("--detach"), Word(str(W)), Word("HEAD"))
    (T / "inner").mkdir()
    git(T / "inner", Word("init"), Word("-q"))
    (BASE / "outside").mkdir()
    os.symlink(T / "tools" / "x.py", BASE / "outside" / "link.py")
    (Q2 / "Cargo.toml").write_text("[package]\n")
    (Q2 / "inner").mkdir()
    git(Q2 / "inner", Word("init"), Word("-q"))
    (S / "writer.py").write_text(f"from pathlib import Path\nPath('{T}/CHANGELOG.md').write_text('x')\n")
    (S / "reader.py").write_text("import json\nprint(json.dumps({'a': 1}))\n")
    src = BASE / "sleeper.c"
    src.write_text("#include <unistd.h>\nint main(void){for(;;)pause();}\n")
    subprocess.run(["cc", "-O0", "-o", str(SLEEPER), str(src)], check=True)


def start(argv: list[Word], cwd: Path) -> subprocess.Popen:
    p = subprocess.Popen(argv, executable=str(SLEEPER), cwd=cwd)
    time.sleep(0.05)
    return p


def stop(p: subprocess.Popen) -> None:
    p.kill()
    p.wait()


# The hooks run in Claude Code's environment, whose SHELL is the user's login shell (measured 2026-09-27), not in
# the tool shell's, which exports SHELL=/usr/bin/zsh.
HOOK_ENV = dict(os.environ, TMPDIR=str(BASE / "tmp"), SHELL="/usr/bin/fish",
                _ZO_DATA_DIR=str(BASE / "zoxide"))  # a zoxide database of the suite's own, never the user's


def hook(path: Path, event: HookEvent | RawEvent) -> HookAnswer:
    raw = event if isinstance(event, str) else json.dumps(event)
    p = subprocess.run([str(path)], input=raw, capture_output=True, text=True, check=False,  # by its shebang
                       timeout=60, env=HOOK_ENV)
    return HookAnswer(ExitStatus(p.returncode), Prose(p.stderr))


def bash(cmd: CommandLine, cwd: Path, bg: bool = True, **extra: Unpack[BashOptions]) -> HookEvent:
    ti: ToolInput = {"command": cmd, **extra}
    if bg:
        ti["run_in_background"] = True
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": ti, "cwd": PathText(str(cwd))}


def monitor(cmd: CommandLine, cwd: Path) -> HookEvent:
    return {"hook_event_name": "PreToolUse", "tool_name": "Monitor", "tool_input": {"command": cmd},
            "cwd": PathText(str(cwd))}


def edit(tool: EditTool, path: PathText, cwd: Path = T) -> HookEvent:
    ti: ToolInput = {"notebook_path": path} if tool == "NotebookEdit" else {"file_path": path}
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti, "cwd": PathText(str(cwd))}


X = f"{S}/x.log"

SHAPE = [
    ("A01", bash(CommandLine("cargo test"), Q), 2, "bare heavy command"),
    ("A02", bash(CommandLine(BLESSED.format("cargo test")), Q), 0, "the blessed shape"),
    ("A03", bash(CommandLine("(cd rust && cargo test)"), Q), 2, "heavy in a subshell"),
    ("A04", bash(CommandLine("timeout 600 cargo test"), Q), 2, "behind timeout"),
    ("A05", bash(CommandLine("bash -c 'cargo test'"), Q), 2, "inside bash -c"),
    ("A06", bash(CommandLine("for i in 1 2; do cargo test; done"), Q), 2, "inside a loop"),
    ("A07", bash(CommandLine("nice -n 10 cargo test"), Q), 2, "wrapper option with a value"),
    ("A08", bash(CommandLine(f"taskset -c 0-19 cargo test 2>&1 > {X}"), Q), 2, "stderr redirected before stdout"),
    ("A09", bash(CommandLine(f"taskset -c 0-9 cargo test > {X} 2>&1"), Q), 0, "a subset of 0-19"),
    ("A10", bash(CommandLine(f"taskset --cpu-list=0-19 cargo test > {X} 2>&1"), Q), 0, "long option with ="),
    ("A11", bash(CommandLine(f"cat > {X} <<'EOF'\ncargo test\nEOF"), Q), 0, "heredoc data, redirect first"),
    ("A12", bash(CommandLine(f"cat <<'EOF' > {X}\ncargo test\nEOF"), Q), 0, "heredoc data, redirect after the tag"),
    ("A13", bash(CommandLine("python/.venv/bin/python -m pytest tests/"), Q), 2, "the Python suite"),
    ("A14", bash(CommandLine("cargo mutants --in-place"), Q), 2, "cargo mutants"),
    ("A15", bash(CommandLine("agda +RTS -M16G -RTS src/Aletheia/Main.agda"), Q), 2, "agda"),
    ("A16", bash(CommandLine("bash benchmarks/run_all.sh --frames 10"), Q), 2, "the benchmarks"),
    ("A17", bash(CommandLine("python3 -m tools.coverage_run"), Q), 2, "the coverage lane"),
    ("A18", bash(CommandLine("rtk cargo test"), Q), 2, "through rtk"),
    ("A19", bash(CommandLine(BLESSED.format("cargo test") + " && git status"), Q), 2, "sharing its call"),
    ("A20", bash(CommandLine("git log --oneline | head -5"), Q), 0, "head on a light command"),
    ("A21", bash(CommandLine(f"taskset -c 0-19 cargo test > {S}/head.log 2>&1"), Q), 0, "'head' only in a file name"),
    ("A22", bash(CommandLine(BLESSED.format("cabal run shake -- build")), Q), 0, "shake, blessed"),
    ("A23", bash(CommandLine("python3 -m tools.check_build_incremental"), Q), 2, "the staleness gate, bare"),
    ("A24", bash(CommandLine("echo start\ncargo test"), Q), 2, "heavy on the second line"),
    ("A25", bash(CommandLine("cd rust\ncargo test --release"), Q), 2, "cd, then heavy on its own line"),
    ("A26", bash(CommandLine(BLESSED.format("cargo test")), Q, bg=False), 0,
     "blessed shape, foreground, default timeout"),
    ("A26b", bash(CommandLine(BLESSED.format("cargo test")), Q, bg=False, timeout=Millis(600000)), 0,
     "foreground with a named timeout"),
    ("A27", bash(CommandLine('"$py" -m tools.mutation_run'), Q), 0, "an interpreter this call never names (limit)"),
    ("A27b", bash(CommandLine('py=python/.venv/bin/python; "$py" -m tools.mutation_run'), Q), 2,
     "an interpreter the call names"),
    ("A28", bash(CommandLine("cargo +nightly test"), Q), 2, "cargo with a toolchain"),
    ("A29", bash(CommandLine("bash -x probes/run_all.sh"), Q), 2, "the store under bash -x"),
    ("A30", bash(CommandLine(f"{{ cargo test; }} > {X} 2>&1"), Q), 2, "a group, no taskset"),
    ("A30b", bash(CommandLine(f"{{ taskset -c 0-19 cargo test; }} > {X} 2>&1"), Q), 0, "a group, blessed"),
    ("A31", bash(CommandLine(f"taskset -c 0-19 cargo test >& {X}"), Q), 0, ">& file"),
    ("A32", bash(CommandLine("taskset -c 0-19 cargo test > /dev/stdout 2>&1"), Q), 2, "the terminal is not a log"),
    ("A33", bash(CommandLine(f"taskset -c 0-19 cargo test > {X} 3>&1"), Q), 2, "3>&1 does not fold stderr"),
    ("A34", bash(CommandLine(f"taskset 0xffffff cargo test > {X} 2>&1"), Q), 2, "a mask with CPUs 20-23"),
    ("A35", bash(CommandLine(f"taskset 0xfffff cargo test > {X} 2>&1"), Q), 0, "a mask of 0-19"),
    ("A36", bash(CommandLine(f"taskset -ac 0-19 cargo test > {X} 2>&1"), Q), 0, "combined flags"),
    ("A37", bash(CommandLine(f"taskset -c0-19 cargo test > {X} 2>&1"), Q), 0, "attached list"),
    ("A38", bash(CommandLine("env -u RUSTFLAGS cargo test"), Q), 2, "env with a valued option"),
    ("A39", bash(CommandLine(f"nohup cargo test > {X} 2>&1 &"), Q), 2, "nohup and &"),
    ("A40", bash(CommandLine("echo $(cargo test)"), Q), 2, "in a command substitution"),
    ("A41", bash(CommandLine("ctest -N --test-dir cpp/build"), Q), 0, "ctest -N lists"),
    ("A41b", bash(CommandLine("cabal run shake -- count-modules"), Q), 0, "shake count-modules"),
    ("A42", bash(CommandLine(BLESSED.format("cargo test") + f'; echo "$(tail -5 {X})"'), Q), 2,
     "a same-call query via $( )"),
    ("A43", bash(CommandLine(f"taskset -c 0-19 bash -c 'cargo test' > {X} 2>&1"), Q), 0, "bash -c carrier, blessed"),
    ("A44", bash(CommandLine(f"> {X} cargo test"), Q), 2, "a leading redirection"),
    ("A45", bash(CommandLine("python3 -u tools/run_ci.py"), Q), 2, "run_ci as a script"),
    ("A46", bash(CommandLine("go -C go test ./aletheia/"), Q), 2, "go -C"),
    ("A47", bash(CommandLine("cabal run -v0 shake -- build"), Q), 2, "cabal run with an option"),
    ("A48", bash(CommandLine("if cargo test; then echo ok; fi"), Q), 2, "in an if"),
    ("A49", monitor(CommandLine("cargo test | grep x"), Q), 2, "heavy inside Monitor"),
    ("A50", bash(CommandLine("n=${#x}; cargo test"), Q), 2, "a # inside a word"),
    ("A51", bash(CommandLine("script -qc 'cargo test' /dev/null"), Q), 2, "script -c"),
    ("A52", bash(CommandLine(f"cat <<'EOF' > {X}\nwe don't run cargo test here\nEOF"), Q), 0,
     "an apostrophe in heredoc data"),
    ("A53", bash(CommandLine('echo "cargo test'), Q), 0, "unparseable, no run live"),
    ("A54", bash(CommandLine("git commit -F - <<'EOF' && git log -1\nci: run go test; ctest now logs\nEOF"), Q), 0,
     "a commit message naming heavy words"),
    ("A55", bash(CommandLine("python3 -m pytest tests/test_x.py -q"), Q), 0, "a targeted pytest is light"),
    ("A56", bash(CommandLine("f() { cargo test; }; f"), Q), 2, "in a function"),
    ("A57", bash(CommandLine("echo cargo test | bash"), Q), 2, "a script piped into bash is read"),
    ("A58", bash(CommandLine("bash <<'EOF'\ncargo test\nEOF"), Q), 2, "a heredoc fed to bash"),
    ("A59", bash(CommandLine("xargs -0 -r cargo test < /dev/null"), Q), 2, "under xargs"),
    ("A60", bash(CommandLine("find rust -maxdepth 0 -exec cargo test \\;"), Q), 2, "under find -exec"),
]

WRITES = [
    ("B01", bash(CommandLine("rg -n 'write_text' tools/"), T), 0, "a search naming a write-word"),
    ("B02", bash(CommandLine('grep -n "cp " tools/x.py'), T), 0, "grep for 'cp '"),
    ("B03", bash(CommandLine('echo "never rm the tree"'), T), 0, "a string with 'rm '"),
    ("B04", bash(CommandLine(f"cp tools/x.py {S}/c.py"), T), 0, "copy out of the tree"),
    ("B05", bash(CommandLine("cp /tmp/a tools/x.py"), T), 2, "copy into the tree"),
    ("B06", bash(CommandLine(f"cp {S}/a {T}/tools/x.py"), S), 2, "copy into the tree from elsewhere"),
    ("B07", bash(CommandLine(f"cd {T} && echo x > note.txt"), S), 2, "cd, then redirect"),
    ("B08", bash(CommandLine("python3 -c \"open('CHANGELOG.md', 'a').write('x')\""), T), 2, "python open for writing"),
    ("B09", bash(CommandLine("python3 - <<'PY'\nfrom pathlib import Path\nPath('CHANGELOG.md').write_text('x')\nPY"),
                 T), 2, "python heredoc write_text"),
    ("B10", bash(CommandLine("sed -ni 's/a/b/p' tools/x.py"), T), 2, "sed -ni"),
    ("B11", bash(CommandLine("sed -e 's/a/b/' -i tools/x.py"), T), 2, "sed -e ... -i"),
    ("B12", bash(CommandLine("sed --in-place 's/a/b/' tools/x.py"), T), 2, "sed --in-place"),
    ("B12b", bash(CommandLine("sed -n 's/a/b/p' tools/x.py"), T), 0, "sed without -i reads"),
    ("B13", bash(CommandLine("git clean -fdx"), T), 2, "git clean"),
    ("B14", bash(CommandLine("git pull --ff-only"), T), 2, "git pull"),
    ("B15", bash(CommandLine("git rebase main"), T), 2, "git rebase"),
    ("B16", bash(CommandLine("cd rust && cargo fmt"), T), 2, "cargo fmt"),
    ("B16b", bash(CommandLine("cd rust && cargo fmt --check"), T), 0, "cargo fmt --check reads"),
    ("B17", bash(CommandLine("ruff format tools"), T), 2, "ruff format"),
    ("B18", bash(CommandLine(f'R={T}; echo x > "$R/note.txt"'), S), 2, "a redirect through a variable set in the call"),
    ("B19", bash(CommandLine("echo x > /tmp/y"), T), 0, "a redirect to /tmp"),
    ("B20", bash(CommandLine(f"git -C {T} commit -m x"), S), 2, "git -C into the tree"),
    ("B21", bash(CommandLine("git status --short"), T), 0, "a read-only git verb"),
    ("B22", bash(CommandLine("ln -sf /tmp/x tools/y"), T), 2, "ln"),
    ("B22b", bash(CommandLine("dd if=/dev/zero of=tools/y bs=1 count=1"), T), 2, "dd of="),
    ("B22c", bash(CommandLine("install -m 755 /tmp/x tools/y.sh"), T), 2, "install"),
    ("B22d", bash(CommandLine("truncate -s 0 CHANGELOG.md"), T), 2, "truncate"),
    ("B22e", bash(CommandLine("touch CHANGELOG.md"), T), 2, "touch"),
    ("B23", bash(CommandLine(f"python3 {S}/writer.py"), S), 2, "a script that writes into the tree"),
    ("B23b", bash(CommandLine(f"python3 {S}/reader.py"), T), 0, "a script that writes nothing"),
    ("B24", bash(CommandLine(f"git commit -S -F {S}/msg.txt"), T), 2, "a commit"),
    ("B25", bash(CommandLine("cp tools/x.py tools/x.bak"), Q), 0, "a write in a tree with no run"),
    ("B26", bash(CommandLine(f"cat > {T}/.commands-to-run.sh <<'EOF'\ngit status\nEOF"), S), 0, "the dribble"),
    ("B27", bash(CommandLine("git stash list"), T), 0, "git stash list"),
    ("B27b", bash(CommandLine("git stash show -p stash@{0}"), T), 0, "git stash show"),
    ("B27c", bash(CommandLine("git stash"), T), 2, "git stash"),
    ("B28", bash(CommandLine("echo a#b > tools/f.txt"), T), 2, "a # inside a word"),
    ("B29", bash(CommandLine(f"touch /tmp/zz; echo y > {T}/note.txt"), Q), 2, "the second target is the live one"),
    ("B30", bash(CommandLine(f"echo a > {Q}/x.txt; echo b > {T}/y.txt"), S), 2, "two trees, the second live"),
    ("B31", bash(CommandLine(f"echo x > /tmp/..{T}/note.txt"), S), 2, "an unnormalised path"),
    ("B32", bash(CommandLine(f"echo x > {T}/tools/scratchpad-notes.txt"), S), 2, "'scratchpad' inside a tree path"),
    ("B33", bash(CommandLine(BLESSED.format("cabal run shake -- build")), T), 2, "a build started mid-run"),
    ("B34", bash(CommandLine(BLESSED.format("python3 -m pytest tests/")), T), 0, "a read-only heavy run mid-run"),
    ("B35", bash(CommandLine("find tools -name '*.bak' -delete"), T), 2, "find -delete"),
    ("B36", bash(CommandLine("mkdir tools/new"), T), 2, "mkdir"),
    ("B37", bash(CommandLine("patch -p1 < /tmp/x.diff"), T), 2, "patch"),
    ("B38", bash(CommandLine("rsync -a /tmp/x/ tools/"), T), 2, "rsync"),
    ("B39", bash(CommandLine("tar -xf /tmp/a.tar -C tools"), T), 2, "tar -x"),
    ("B40", bash(CommandLine("clang-format-22 -i tools/x.cpp"), T), 2, "clang-format -i"),
    ("B40b", bash(CommandLine("xargs -0 -r clang-format-22 -i < /tmp/list"), T), 2, "clang-format -i under xargs"),
    ("B41", bash(CommandLine("gofmt -w x.go"), T), 2, "gofmt -w"),
    ("B42", bash(CommandLine("sort -o tools/x /tmp/y"), T), 2, "sort -o"),
    ("B43", bash(CommandLine("awk -i inplace '{print}' tools/x.py"), T), 2, "awk -i inplace"),
    ("B44", bash(CommandLine("git -c commit.gpgsign=false commit -m x"), T), 2, "git -c commit"),
    ("B44b", bash(CommandLine("git merge main"), T), 2, "git merge"),
    ("B44c", bash(CommandLine("git cherry-pick HEAD~1"), T), 2, "git cherry-pick"),
    ("B44d", bash(CommandLine("git worktree add "
                              "/tmp/wt-x"), T), 0, "git worktree add elsewhere (metadata ruled exempt)"),
    ("B44e", bash(CommandLine("git worktree add -b wt sub/wt HEAD"), T), 2, "git worktree add inside the live tree"),
    ("B44f", bash(CommandLine("git worktree remove --force /tmp/wt-x && git worktree prune"), T), 0,
     "worktree remove and prune"),
    ("B44g", bash(CommandLine("git init -q /tmp/fresh-repo"), T), 0, "git init of a directory elsewhere"),
    ("B44h", bash(CommandLine("git init -q"), T), 2, "git init of the live tree"),
    ("B44i", bash(CommandLine("git clone -q . /tmp/clone-x"), T), 0, "git clone into a directory elsewhere"),
    ("B44j", bash(CommandLine("git clone -q /tmp/src sub/clone"), T), 2, "git clone into the live tree"),
    ("B44k", bash(CommandLine("git log -1 --output=CHANGELOG.md"), T), 2, "git log --output into the tree"),
    ("B44l", bash(CommandLine("git reflog expire --expire=now --all"), T), 2, "git reflog expire"),
    ("B44m", bash(CommandLine("git log -1 --output=/tmp/log.txt"), T), 0, "git log --output elsewhere"),
    ("B45", bash(CommandLine(f"cat > {T}/n.txt <<'END-DATA'\nwe don't\nEND-DATA"), S), 2,
     "a heredoc to a file in the tree"),
    ("B46", bash(CommandLine("echo '*.tmp' >> .git/info/exclude"), T), 2, ".git internals"),
    ("B47", bash(CommandLine('echo "unterminated'), T), 2, "unparseable while a run is live"),
    ("B48", bash(CommandLine('echo "unterminated'), S), 0, "unparseable where no run is"),
    ("B49", bash(CommandLine(f"cp {S}/a {BASE}/outside/link.py"), S), 2, "a symlink into the tree"),
    ("B50", bash(CommandLine(f"echo x > {W}/f.txt"), S), 0, "a linked worktree outside the live tree"),
    ("B51", bash(CommandLine("tee tools/out.txt < /tmp/in"), T), 2, "tee"),
    ("B52", bash(CommandLine("curl -o tools/f https://example.invalid/x"), T), 2, "curl -o"),
    ("B53", bash(CommandLine("perl -pi -e 's/a/b/' tools/x.py"), T), 2, "perl -pi"),
    ("B54", bash(CommandLine("chmod +x tools/x.py"), T), 2, "chmod"),
    ("B55", bash(CommandLine("rm -f tools/x.py"), T), 2, "rm"),
    ("B56", bash(CommandLine(f"mv {S}/a tools/"), T), 2, "mv into the tree"),
    ("B57", bash(CommandLine("go mod tidy"), T), 2, "go mod tidy"),
    ("B58", bash(CommandLine("git apply --check /tmp/x.patch"), T), 0, "git apply --check reads"),
    ("B59", bash(CommandLine("git diff HEAD -- tools/x.py"), T), 0, "git diff reads"),
    ("B60", bash(CommandLine("cat tools/x.py | wc -l"), T), 0, "a read pipeline"),
]

EDITS = [
    ("C01", edit("Edit", PathText(f"{T}/tools/x.py")), 2, "tracked file, run live"),
    ("C02", edit("Write", PathText(f"{T}/.commands-to-run.sh")), 0, "the dribble"),
    ("C03", edit("Edit", PathText(f"{Q}/f.txt")), 0, "a tree with no run"),
    ("C04", edit("Write", PathText(f"{S}/draft.txt")), 0, "a plain directory"),
    ("C05", edit("Edit", PathText("tools/x.py"), T), 2, "a relative path, resolved against the event's cwd"),
    ("C06", edit("Edit", PathText(f"{BASE}/outside/link.py")), 2, "a symlink into the tree"),
    ("C07", edit("NotebookEdit", PathText(f"{T}/n.ipynb")), 2, "NotebookEdit"),
    ("C08", edit("Write", PathText(f"{T}/new/dir/file.txt")), 2, "a new directory in the tree"),
    ("C09", edit("Edit", PathText(f"{T}/inner/f.txt")), 2, "a nested repository inside the live tree"),
    ("C10", edit("Edit", PathText(f"{W}/f.txt")), 0, "a linked worktree outside the live tree"),
    ("C11", edit("MultiEdit", PathText(f"{T}/tools/x.py")), 2, "MultiEdit"),
    ("C12", edit("Write", PathText(f"{T}/.git/info/exclude")), 2, ".git internals"),
]

# Processes started in (or pointing into) Q2, then an Edit on Q2/f.txt: blocked iff the process is a run.
PROCS = [
    ("P01", [Word("cargo"), Word("test")], Q2, 2),
    ("P02", [Word("cabal"), Word("run"), Word("shake"), Word("--"), Word("build")], Q2, 2),
    ("P03", [Word("bash"), Word("probes/run_all.sh")], Q2, 2),
    ("P04", [Word("pytest"), Word("tests/")], Q2, 2),
    ("P05", [Word("agda"), Word("+RTS"), Word("-M16G"), Word("-RTS"), Word("src/Main.agda")], Q2, 2),
    ("P06", [Word("cargo"), Word("mutants"), Word("--in-place")], Q2, 2),
    ("P07", [Word("gremlins"), Word("unleash"), Word("./aletheia")], Q2, 2),
    ("P08", [Word("mutmut"), Word("run")], Q2, 2),
    ("P09", [Word("python3"), Word("-m"), Word("tools.coverage_run")], Q2, 2),
    ("P10", [Word("python3"), Word("-m"), Word("tools.check_build_incremental")], Q2, 2),
    ("P11", [Word("python3"), Word("-m"), Word("tools.check_reproducible_build")], Q2, 2),
    ("P12", [Word("python3"), Word("-m"), Word("tools.iwyu")], Q2, 2),
    ("P13", [Word("bash"), Word("benchmarks/run_all.sh"), Word("--bench"), Word("throughput")], Q2, 2),
    ("P14", [Word("python3"), Word("-m"), Word("tools.mutation_run")], Q2, 2),
    ("P15", [Word("ctest"), Word("--test-dir"), Word("build")], Q2, 2),
    ("P16", [Word("mull-runner-23"), Word("unit_tests")], Q2, 2),
    ("P17", [Word("taskset"), Word("-c"), Word("0-19"), Word("cargo"), Word("test")], Q2, 2),
    ("P18", [Word("nohup"), Word("cargo"), Word("test")], Q2, 2),
    ("P19", [Word("timeout"), Word("60"), Word("cargo"), Word("test")], Q2, 2),
    ("P20", [Word("env"), Word("-u"), Word("X"), Word("cargo"), Word("test")], Q2, 2),
    ("P21", [Word("emacs"), Word("tools/run_ci.py")], Q2, 0),
    ("P22", [Word("tail"), Word("-f"), Word("probes/run_all.sh")], Q2, 0),
    ("P23", [Word("/usr/bin/zsh"), Word("-c"), Word("rg -n 'go test' docs")], Q2, 0),
    ("P24", [Word("/usr/bin/zsh"), Word("-c"), Word("source snap && eval 'taskset -c 0-19 cargo test > log 2>&1'")], Q2,
     0),
    ("P25", [Word("grep"), Word("cargo test"), Word("notes.txt")], Q2, 0),
    ("P26", [Word("ctest"), Word("-N")], Q2, 0),
    ("P27", [Word("python3"), Word("-m"), Word("pytest"), Word("tests/test_x.py")], Q2, 0),
    ("P28", [Word("cargo"), Word("test"), Word("--manifest-path"), Word(str(Q2 / "Cargo.toml"))], S, 2),
    ("P29", [Word("cargo"), Word("test")], Q2 / "inner", 0),
    ("P30", [Word("cargo"), Word("test")], Q2 / "sub", 2),
]


# ─── cases from the adversarial pass (replay, evasion, fuzz) ──────────────────

def breaker_fixtures() -> None:
    (S / "w.sh").write_text(f"#!/usr/bin/env bash\necho x > {T}/CHANGELOG.md\n")
    (S / "w.sh").chmod(0o755)
    (S / "fix.py").write_text(f"#!/usr/bin/env python3\nopen('{T}/CHANGELOG.md', 'w').write('x')\n")
    (S / "fix.py").chmod(0o755)
    (S / "bump.py").write_text("import sys\nopen(sys.argv[1], 'w').write('x')\nprint(open('/tmp/in.txt').read())\n")
    (S / "sweep.sh").write_text("cargo mutants --in-place 2>&1 | tail -5\n")
    (S / "driver.py").write_text("import subprocess\nsubprocess.run(['cargo', 'test'])\n")
    (T / ".commands-to-run.d").mkdir()
    (T / "cpp").mkdir()


DRIB = "#!/usr/bin/env bash\\ncd /x\\ngit add -A\\ngit commit -S -m x\\n"
HCOMMIT = "\"$(cat <<'EOF'\nThe hook's fix landed.\nEOF\n)\""

SHAPE2 = [
    ("R01", bash(CommandLine(f"cat > {S}/r.sh <<'SH'\ncd {Q} && python3 tools/run_ci.py 2>&1 | tail -5\nSH\n"
                             f"bash {S}/r.sh"), Q), 2, "a script the call writes, then runs"),
    ("R04", bash(CommandLine("bash -n tools/build_mull.sh && echo syntax ok"), Q), 0, "bash -n runs nothing"),
    ("R05", bash(CommandLine("files=(tools/mutation_run.py tools/x.py)\npython3 -m ruff check $files"), Q), 0,
     "an array"),
    ("R05b", bash(CommandLine(f"ARGS=(--release); taskset -c 0-19 cargo test \"${{ARGS[@]}}\" > {X} 2>&1"), Q), 0,
     "an array feeding a blessed run"),
    ("R06", bash(CommandLine("cargo install --list"), Q), 0, "cargo install --list"),
    ("R06b", bash(CommandLine("cargo mutants --list --json"), Q), 0, "cargo mutants --list"),
    ("R06c", bash(CommandLine("python3 -m tools.coverage_run --scope"), Q), 0, "coverage_run --scope"),
    ("R06d", bash(CommandLine("python3 -m tools.mutation_scope --binding rust"), Q), 0, "mutation_scope is a query"),
    ("R06e", bash(CommandLine("cargo llvm-cov --branch --summary-only --no-run"), Q), 0, "llvm-cov --no-run"),
    ("R09", bash(CommandLine(f"taskset -c 2-21 ./cpp/build/benchmark throughput > {S}/b.json 2> {S}/b.err"), Q,
                 timeout=Millis(600000)), 2, "a benchmark binary on CPUs 20-21"),
    ("R09b", bash(CommandLine("(cd cpp/build && ./unit_tests --order decl 2>&1 | grep FAILED | head -6)"), Q), 2,
     "the C++ battery through head"),
    ("R11", bash(CommandLine("go vet ./"), Q), 0, "go vet of one package"),
    ("R11b", bash(CommandLine("go test -run '^TestZZ$' -count=1 ."), Q), 0, "go test -run"),
    ("R11c", bash(CommandLine("ctest --test-dir cpp/build -R excel_tests -V"), Q), 0, "ctest -R"),
    ("R11d", bash(CommandLine("ctest --test-dir cpp/build -V"), Q), 2, "ctest -V is verbose, not a version"),
    ("V02", bash(CommandLine("python3 -m pytest tests/test_a.py tests/"), Q), 2, "a pytest with a directory"),
    ("V02b", bash(CommandLine("python3 -m pytest tests/*.py"), Q), 2, "a pytest over a glob"),
    ("V07", bash(CommandLine("echo \"$(cat <<'EOF'\nwe don't\nEOF\n)\" && cargo test"), Q), 2,
     "heredoc in $( ) beside a run"),
    ("V12", bash(CommandLine("fish -c 'cargo test'"), Q), 2, "fish -c"),
    ("V12b", bash(CommandLine("bash --norc -c 'cargo test'"), Q), 2, "bash --norc -c"),
    ("V13", bash(CommandLine(f"bash {S}/sweep.sh"), Q), 2, "a scratch script with a sweep through tail"),
    ("V13b", bash(CommandLine(f"python3 {S}/driver.py"), Q), 2, "a Python driver starting cargo test"),
    ("V13c", bash(CommandLine(f"time {{ cargo test; }} > {X} 2>&1"), Q), 2, "time { }"),
    ("V13d", bash(CommandLine("[[ -n \"$(cargo test)\" ]]"), Q), 2, "a run inside [[ ]]"),
    ("V13e", bash(CommandLine("cat <<EOF\n$(cargo test 2>&1)\nEOF"), Q), 2, "a run inside an unquoted heredoc"),
    ("V13f", bash(CommandLine("coproc cargo test"), Q), 2, "coproc"),
    ("V13g", bash(CommandLine("cargo fix --allow-dirty"), Q), 2, "cargo fix"),
    ("V13h", bash(CommandLine("bazel build //..."), Q), 2, "bazel build"),
    ("V13i", bash(CommandLine("cpp/build/unit_tests"), Q), 2, "the Catch2 suite by path"),
    ("V14", bash(CommandLine("bash -n probes/run_all.sh"), Q), 0, "bash -n on the store"),
    ("V14b", bash(CommandLine(f"exec > {X} 2>&1; taskset -c 0-19 cargo test"), Q), 0, "exec redirects what follows"),
    ("V14c", bash(CommandLine("taskset -c 0-19 cargo test > /dev/shm/x.log 2>&1"), Q), 0, "a log in /dev/shm"),
    ("V14d", bash(CommandLine("agda -V"), Q), 0, "agda -V"),
    ("V14e", bash(CommandLine("pytest -h"), Q), 0, "pytest -h"),
    ("V16", bash(CommandLine(BLESSED.format("cargo test") + f'; echo "$(< {X})"'), Q), 2, "$(< file) in the same call"),
    ("V16b", bash(CommandLine(f"taskset -c 0-19 cargo test > {X} 2> {S}/err.log"), Q), 0, "both streams in files"),
    ("V16c", bash(CommandLine(BLESSED.format("cargo test")), Q, bg=False, timeout=Millis(5000)), 0,
     "a timeout shorter than the run"),
    ("V06", bash(CommandLine("cd \"$(git rev-parse --show-toplevel)\" && " + BLESSED.format("cargo test")), Q), 0,
     "cd to the top level before a run"),
    ("V06b", bash(CommandLine(f"LOG={S}/x-$(date +%s).log; taskset -c 0-19 cargo test > \"$LOG\" 2>&1"), Q), 0,
     "a timestamped log name"),
    ("F01c", bash(CommandLine("taskset -c 0-19 cargo test; gh pr comment 1 --body " + HCOMMIT), Q), 2,
     "an unlogged run beside a heredoc body"),
    ("F08", bash(CommandLine("( (cd rust && cargo test) )"), Q), 2, "nested subshells"),
    ("F10", bash(CommandLine("(( n = 1 << 2 ))\ncargo test"), Q), 2, "<< inside arithmetic"),
    ("F12", bash(CommandLine("echo $(case $x in a) echo a;; esac)"), Q), 0, "a case inside $( )"),
    ("F12b", bash(CommandLine("echo $(echo $'it\\'s')"), Q), 0, "an ANSI quote inside $( )"),
]

WRITES2 = [
    ("R01b", bash(CommandLine(f"{S}/w.sh"), T), 2, "a script run by its path"),
    ("R01d", bash(CommandLine(f"cat > {S}/v.sh <<'SH'\necho x > {T}/CHANGELOG.md\nSH\nbash {S}/v.sh"), T), 2,
     "a script written then run in one call"),
    ("R03", bash(CommandLine("perl -0pi -e 's/x = 1/x = 2/' tools/x.py"), T), 2, "perl -0pi"),
    ("R04b", bash(CommandLine("bash -n .commands-to-run.sh && echo syntax-ok"), T), 0, "bash -n on the dribble"),
    ("R05c", bash(CommandLine("FILES=(); FILES+=(\"a b\"); echo \"${FILES[@]}\""), T), 0, "array append"),
    ("R07", bash(CommandLine(f"S={S}; for f in tools/x.py; do cp \"$f\" \"$S/wt/$f\"; done"), T), 0,
     "copy out through variables"),
    ("R07b", bash(CommandLine("for f in tools/x.py CHANGELOG.md; do printf 'x\\n' > \"$f\"; done"), T), 2,
     "a loop variable over literal paths"),
    ("R07c", bash(CommandLine(f"R={T}; w() {{ printf 'x\\n' > \"$R/tools/$1\"; }}; w new.txt"), S), 2,
     "a function's redirect"),
    ("R08", bash(CommandLine(f"python3 - <<'PY'\nimport pathlib\nM = \"{S}/mem/\"\n"
                             "pathlib.Path(M + \"TASKS.md\").write_text(\"x\")\nPY"),
                 T), 0, "python writes outside the tree through a name"),
    ("R08b", bash(CommandLine("python3 - <<'PY'\nimport pathlib\n"
                              "(pathlib.Path.home() / \".claude-test-x.json\").write_text('x')\nPY"),
                  T), 0, "python writes under HOME"),
    ("R08c", bash(CommandLine("python3 -c \"x = 'L.6'; open('/tmp/y', 'w').write(x)\""), T), 0,
     "a task id string is not a path"),
    ("R08d", bash(CommandLine("python3 - <<'PY'\nimport pathlib\nfor p in pathlib.Path('tools').glob('*.py'):\n"
                              "    p.write_text(p.read_text() + '\\n')\n"
                              "pathlib.Path('/tmp/x-log.txt').write_text('done')\nPY"), T), 2,
     "a write through a loop variable beside one outside"),
    ("R10", bash(CommandLine("cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release"), T), 2,
     "a cmake configure into the tree"),
    ("R12", bash(CommandLine("git fetch -q origin"), T), 2, "git fetch writes .git"),
    ("R12b", bash(CommandLine("git branch -D old-branch"), T), 2, "git branch -D"),
    ("R12c", bash(CommandLine("git branch --show-current"), T), 0, "git branch --show-current"),
    ("R12d", bash(CommandLine("git config user.name"), T), 0, "git config reading a key"),
    ("R14", bash(CommandLine(f"taskset -c 0-19 cmake --build \"{S}/build\" -j20 > {S}/b.log 2>&1"), T), 0,
     "a build of a directory outside the tree"),
    ("V06c", bash(CommandLine("echo x > \"$(git rev-parse --show-toplevel)/note.txt\""), T), 2,
     "the top level by substitution"),
    ("V06d", bash(CommandLine("echo x > \"$(pwd)/note.txt\""), T), 2, "$(pwd)"),
    ("V06e", bash(CommandLine("cd \"$(git rev-parse --show-toplevel)\" && sed -i s/a/b/ CHANGELOG.md"), T), 2,
     "cd to the top level, then sed -i"),
    ("V06f", bash(CommandLine("cd /tmp && cd - && sed -i s/a/b/ CHANGELOG.md"), T), 2, "cd -"),
    ("V06g", bash(CommandLine("tmp=$(mktemp); git show HEAD:CHANGELOG.md > \"$tmp\"; diff \"$tmp\" CHANGELOG.md; "
                              "rm -f \"$tmp\""), T), 0, "a mktemp file"),
    ("V07b", bash(CommandLine("gh pr create --title t --body " + HCOMMIT), T), 0, "a PR body heredoc"),
    ("V08", bash(CommandLine("python3 -c \"print('a-b'.replace('-', '_'))\""), T), 0, "str.replace"),
    ("V09", bash(CommandLine("python3 -c \"from pathlib import Path; Path('CHANGELOG.md').open('w').write('x')\""), T),
     2, "Path.open('w')"),
    ("V09b", bash(CommandLine("python3 -c \"import os; os.system('sed -i s/a/b/ CHANGELOG.md')\""), T), 2, "os.system"),
    ("V09c", bash(CommandLine("python/.venv/bin/pip install -e 'python/.[mutation]'"), T), 2, "pip install -e"),
    ("V09d", bash(CommandLine("python3 -m pip install -e python/"), T), 2, "python -m pip install"),
    ("V09e", bash(CommandLine("python3 -m ruff format tools"), T), 2, "python -m ruff format"),
    ("V09f", bash(CommandLine(f"{S}/fix.py"), T), 2, "a shebang Python script by path"),
    ("V09g", bash(CommandLine("echo \"open('CHANGELOG.md','w').write('x')\" | python3"), T), 2,
     "code piped into python"),
    ("V09h", bash(CommandLine("cat <<'PY' | python3 -\nopen('CHANGELOG.md','w').write('x')\nPY"), T), 2,
     "a heredoc piped into python"),
    ("V09i", bash(CommandLine(f"python3 {S}/bump.py CHANGELOG.md"), T), 2, "a script writing its argv"),
    ("V10", bash(CommandLine("cd rust && cargo clean"), T), 2, "cargo clean"),
    ("V10b", bash(CommandLine("cargo update"), T), 2, "cargo update"),
    ("V10c", bash(CommandLine("git push origin HEAD"), T), 2, "git push"),
    ("V10d", bash(CommandLine("git commit-tree 'HEAD^{tree}' -p HEAD -m x"), T), 2, "git commit-tree"),
    ("V10e", bash(CommandLine("git format-patch -1 HEAD"), T), 2, "git format-patch"),
    ("V10f", bash(CommandLine("git archive -o release.tar HEAD"), T), 2, "git archive -o"),
    ("V10g", bash(CommandLine("mv CHANGELOG.md /tmp/"), T), 2, "mv out of the tree"),
    ("V10h", bash(CommandLine("curl -sSLO https://example.invalid/y"), T), 2, "curl -O bundled"),
    ("V10i", bash(CommandLine("tar czf tools/x.tgz docs"), T), 2, "tar czf"),
    ("V10j", bash(CommandLine("goimports -w x.go"), T), 2, "goimports -w"),
    ("V10k", bash(CommandLine("clang-tidy -fix x.cpp"), T), 2, "clang-tidy -fix"),
    ("V10l", bash(CommandLine("ruff check --fix-only tools"), T), 2, "ruff --fix-only"),
    ("V11", bash(CommandLine(f"taskset -c 0-19 cmake --build {T}/cpp/build > {X} 2>&1"), S), 2,
     "a build of the live tree from outside"),
    ("V12c", bash(CommandLine("fish -c 'echo x > CHANGELOG.md'"), T), 2, "fish -c writing"),
    ("V15", bash(CommandLine("wget -qO- https://example.invalid/x"), T), 0, "wget to stdout"),
    ("V15b", bash(CommandLine("wget -O - https://example.invalid/x"), T), 0, "wget -O -"),
    ("V15c", bash(CommandLine("unzip -l a.zip"), T), 0, "unzip -l"),
    ("V15d", bash(CommandLine("patch --dry-run -p1 < /tmp/x.diff"), T), 0, "patch --dry-run"),
    ("V15e", bash(CommandLine("git clean -nd"), T), 0, "git clean -n"),
    ("V15f", bash(CommandLine("isort --check-only x.py"), T), 0, "isort --check-only"),
    ("V15g", bash(CommandLine("tar -xOf a.tar"), T), 0, "tar to stdout"),
    ("V15h", bash(CommandLine("go mod tidy -diff"), T), 0, "go mod tidy -diff"),
    ("V15i", bash(CommandLine("git submodule"), T), 0, "bare git submodule"),
    ("V18", bash(CommandLine(f"echo x >! {T}/note.txt"), S), 2, "zsh >!"),
    ("V21", edit("Write", PathText(f"{T}/.commands-to-run.d/msg-next.txt")), 2,
     "the dribble's directory is not the dribble"),
    ("F01", bash(CommandLine("gh pr comment 1 --body " + HCOMMIT), T), 0, "a comment body heredoc"),
    ("F01b", bash(CommandLine(f"cd {T} && git commit -q -m " + HCOMMIT), S), 2, "a commit with a heredoc body"),
    ("F08b", bash(CommandLine("( (echo x > CHANGELOG.md) )"), T), 2, "nested subshells writing"),
    ("F09", bash(CommandLine("echo \"${LOG:-$(git stash)}\""), T), 2, "a substitution inside ${ }"),
    ("F09b", bash(CommandLine("case $(git stash) in *) ;; esac"), T), 2, "a case subject"),
    ("F05", bash(CommandLine("bash /dev/zero"), T), 2, "a device is not a script: unreadable while a run is live"),
    ("F07", bash(CommandLine("echo x > $'a\\x00b'"), T), 2, "a NUL ends the path, as the shell ends it"),
]

LOOPS2 = [
    ("L01", bash(CommandLine("until grep -q done /tmp/x.log; do\n  sleep 5\ndone"), T), 2, "a multi-line until"),
    ("L02", bash(CommandLine("for i in {1..30}; do\n echo tick\n sleep 2\ndone"), T), 2, "a for loop with sleep"),
    ("L03", bash(CommandLine("while true; do\n pgrep -f cargo || break\n sleep 10\ndone"), T), 2, "a multi-line while"),
    ("L04", bash(CommandLine("kill -INT 12345; for i in $(seq 1 40); do kill -0 12345 2>/dev/null || break; sleep 1; "
                             "done"), T), 2, "a bounded kill-wait"),
    ("L05", monitor(CommandLine("tail -f /tmp/x.log | grep --line-buffered FAIL &\nwait 12345"), T), 2,
     "a wait on a literal pid"),
    ("L06", bash(CommandLine("sleep 0.1 & sleep 7773 & wait -n"), T), 2, "wait -n reaps one"),
    ("L07", bash(CommandLine("bash -c 'sleep 7771 &'"), T), 2, "a job inside bash -c"),
    ("L08", bash(CommandLine("eval 'sleep 7778 &'"), T), 2, "a job inside eval"),
    ("L09", bash(CommandLine("(sleep 7775 &); wait"), T), 2, "a job in a subshell"),
    ("L10", bash(CommandLine("coproc tail -f /tmp/x"), T), 2, "coproc"),
    ("L11", bash(CommandLine("setsid -f sleep 7774; echo started"), T), 2, "setsid -f"),
    ("L12", bash(CommandLine("tmux new -d 'cargo test'"), T), 2, "tmux new -d"),
    ("L13", bash(CommandLine("sleep 999 &!"), T), 2, "zsh &!"),
    ("L14", bash(CommandLine("tail -f x | grep y &\nsleep 1\nwait"), T), 0, "a pipeline job reaped by wait"),
    ("L15", bash(CommandLine("sleep 1 & p=$!; echo x; wait \"$p\""), T), 0, "wait on a kept pid"),
    ("L16", monitor(CommandLine("cleanup() { kill 0; }\ntrap cleanup EXIT\n"
                                "tail -f /tmp/x.log | grep --line-buffered FAIL &\n"
                                "while kill -0 123 2>/dev/null; do sleep 15; done"), T), 0,
     "a trap through a function"),
    ("L17", bash(CommandLine("git commit -q -m " + HCOMMIT + " && tail -f /tmp/x.log | grep --line-buffered FAIL &"),
                 T), 2, "a job beside a heredoc commit body"),
    ("L18", bash(CommandLine(f"cat > {S}/o.sh <<'SH'\nsleep 99 &\nSH\nbash {S}/o.sh"), T), 2,
     "a job in a script the call runs"),
    ("M01", monitor(CommandLine("while pgrep -af cargo >/dev/null; do sleep 5; done"), T), 2, "pgrep -af"),
    ("M02", monitor(CommandLine("while pgrep --full cargo >/dev/null; do sleep 5; done"), T), 2, "pgrep --full"),
    ("M03", monitor(CommandLine("while pgrep -u \"$USER\" -f cargo >/dev/null; do sleep 5; done"), T), 2,
     "pgrep -u X -f"),
    ("M04", monitor(CommandLine("while pgrep -f '[c]argo test' >/dev/null; do sleep 5; done; "
                                "echo 'cargo test finished'"), T), 2, "a bracket whose plain text appears elsewhere"),
    ("M05", monitor(CommandLine("pgrep -f '[x]yz'; pgrep -f cargo"), T), 2,
     "one bracket does not excuse another pattern"),
    ("M06", monitor(CommandLine("while pgrep -f '[c]argo mutants' > /dev/null; do sleep 5; done"), T), 0,
     "a lone bracket pattern"),
    ("N01", bash(CommandLine("cat <<'EOF' > /tmp/doc.md\nuntil grep -q x f; do sleep 1; done\nEOF"), T), 0,
     "a loop in heredoc data"),
    ("N02", bash(CommandLine("rg -n 'until .* do .*sleep' /tmp"), T), 0, "a search for the shape"),
    ("N03", bash(CommandLine("git commit -m \"until grep; do sleep 1; done\""), T), 0,
     "a commit message naming the shape"),
    ("N04", bash(CommandLine("cat <<'END-DOC' > /tmp/doc.md\nwhile true; do sleep 1; done\nEND-DOC"), T), 0,
     "a hyphenated tag"),
    ("N05", bash(CommandLine("python3 -c \"s = 'while x; do sleep 1; done'\""), T), 0,
     "a Python string naming the shape"),
]

PROCS2 = [
    ("P31", [Word("python3"), Word(str(Q2 / "venv/bin/pytest")), Word("tests/")], Q2, 2),
    ("P32", [Word("python3"), Word(str(Q2 / "venv/bin/mutmut")), Word("run")], Q2, 2),
    ("P33", [Word("python3"), Word(str(Q2 / "venv/bin/pylint")), Word("aletheia/")], Q2, 2),
    ("P34", [Word("cargo"), Word("test"), Word(f"--manifest-path={Q2}/Cargo.toml")], S, 2),
    ("P35", [Word("cargo"), Word("test"), Word("--manifest-path"), Word("../probe/Cargo.toml")], S, 2),
    ("P36", [Word("make"), Word(f"-C{Q2}")], S, 2),
    ("P37", [Word("python3"), Word("-m"), Word("tools.mutation_scope")], Q2, 0),
    ("P38", [Word("ctest"), Word("-V")], Q2, 2),
]


def extra_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, event, want, what in SHAPE2:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    (T / ".commands-to-run.sh").write_text(DRIB)
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in WRITES2:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    for cid, event, want, what in LOOPS2:
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    for cid, argv, cwd, want in PROCS2:
        p = start(argv, cwd)
        try:
            rc, err = hook(GUARD, edit("Edit", PathText(f"{Q2}/f.txt"), Q2))
        finally:
            stop(p)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(" ".join(argv) + f" @{cwd.name}"), err))
    # A snapshot whose PostToolUse never came is pruned by the next one.
    stale = BASE / "tmp" / "claude-heavy-run-guard" / "stale.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}")
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    live = start([Word("cargo"), Word("test")], T)
    try:
        hook(GUARD, HookEvent(bash(CommandLine("echo hi"), T), tool_use_id=ToolUseId("t-prune")))
        rows.append(CaseRow(CaseId("E06"), ExitStatus(0), ExitStatus(int(stale.exists())),
                            Prose("a stale snapshot is pruned"), Prose("")))
        pre = bash(CommandLine("touch /tmp/e07-stamp"), T)
        hook(GUARD, pre)
        os.utime(T / "tools" / "x.py", ns=(3, 3))
        post = HookEvent(bash(CommandLine("rtk touch /tmp/e07-stamp"), T), hook_event_name="PostToolUse",
                         tool_response={})
        rc, err = hook(GUARD, post)
        rows.append(CaseRow(CaseId("E07"), ExitStatus(2), rc, Prose("Pre and Post paired across the rtk rewrite"), err))
    finally:
        stop(live)
    # Fuzz findings: crashes, hangs and slow reads, each read within 2 s and exiting 0 or 2.
    slow = CommandLine("python3 - <<'PY'\n# the hook's regex\ns = open('x').read()\n" + "".join(
        f's = s.replace("old {i}\\n", "new {i}\\n")\n' for i in range(14)) + "p.write_text(s)\nPY")
    for cid, cmd in [("Z01", slow), ("Z02", CommandLine("for")), ("Z03", CommandLine("echo x > CHANGELOG.md\nfor")),
                     ("Z04", CommandLine("{ " * 260 + "true" + "; }" * 260)), ("Z05", CommandLine("echo x > a\x00b")),
                     ("Z06", CommandLine("sh \ud800")),
                     ("Z07", CommandLine('echo "' + "$(" * 20 + "x" + ")" * 20 + '"'))]:
        for path in (GUARD, LOOPS):
            t0 = time.monotonic()
            try:
                rc, err = hook(path, bash(cmd, T))
            except UnicodeEncodeError:
                rc, err = ExitStatus(0), Prose("")
            took = time.monotonic() - t0
            rows.append(CaseRow(CaseId(f"{cid}{'g' if path == GUARD else 'l'}"), ExitStatus(1),
                                ExitStatus(int(rc in (0, 2) and took < 2)), Prose(f"read in {took:.2f}s, exit {rc}"),
                                err))
    return rows


# ─── cases from the second adversarial round ──────────────────────────────────

def round2_fixtures() -> None:
    venv = T / "python" / ".venv" / "bin"
    venv.mkdir(parents=True)
    os.symlink(os.path.realpath(sys.executable), venv / "python")
    (S / "lib.sh").write_text(f"#!/usr/bin/env bash\nw() {{ echo x > {T}/CHANGELOG.md; }}\n")
    (S / "fixrel.py").write_text("open('CHANGELOG.md', 'w').write('x')\n")
    (S / "w_rel.sh").write_text("echo x > CHANGELOG.md\n")
    (S / "default_then_argv.py").write_text("import sys\nfrom pathlib import Path\npath = Path('/tmp/x-default')\n"
                                            "if len(sys.argv) > 1:\n    path = Path(sys.argv[1])\n"
                                            "path.write_text('x')\n")
    (S / "argp.py").write_text("import argparse\np = argparse.ArgumentParser()\np.add_argument('f')\n"
                               "a = p.parse_args()\nopen(a.f, 'w').write('x')\n")
    (S / "watch.sh").write_text("while pgrep -f cargo > /dev/null; do sleep 5; done\n")
    (T / "tools" / "check_apply.py").write_text("from pathlib import Path\nimport sys\n"
                                               "for p in Path('.').rglob('*.md'):\n    p.write_text(p.read_text())\n")
    for d in (Q2 / "probes", Q2 / "tools"):
        d.mkdir(exist_ok=True)


SHAPE3 = [
    ("N2a", bash(CommandLine("python3 -m pytest tests/test_x.py -q -p no:randomly"), Q), 0, "a pytest option's value"),
    ("N2b", bash(CommandLine("python3 -m pytest tests/test_x.py -k name"), Q), 0, "pytest -k"),
    ("N6", bash(CommandLine("env 'cxx_x:/r/cpp/src/client.cpp:1:2:1:3:ab.0=1' timeout 600 cpp/build/unit_tests --order "
                            "rand 2>&1 | head -5"), Q), 2, "env with a Mull switch name"),
    ("N11", bash(CommandLine("cpp/build/unit_tests '[json]' 2>&1 | tail -3"), Q), 0, "a Catch2 run of one tag"),
    ("H01", bash(CommandLine("bash -eo pipefail -c 'cargo test 2>&1 | tail -5'"), Q), 2, "bash -eo pipefail -c"),
    ("H02", bash(CommandLine(f"bash -euo pipefail {S}/sweep.sh"), Q), 2, "bash -euo pipefail script"),
    ("H03", bash(CommandLine("cd probes && bash run_all.sh"), Q), 2, "the store from its own directory"),
    ("H04", bash(CommandLine("cd benchmarks && ./run_all.sh --frames 10"), Q), 2,
     "the benchmarks from their directory"),
    ("H06", bash(CommandLine("python3 -c \"import os; os.system('cargo test 2>&1 | tail -5')\""), Q), 2, "os.system"),
    ("H07", bash(CommandLine("python3 -c \"import subprocess; subprocess.run('cargo test | tail -5', shell=True)\""),
                 Q), 2, "shell=True"),
    ("H08", bash(CommandLine("python3 -c \"from subprocess import run; run(['cargo', 'test'])\""), Q), 2,
     "an imported run"),
    ("H09", bash(CommandLine("python3 -c \"import subprocess; subprocess.run(['bash', '-c', 'cargo test | "
                             "tail -5'])\""), Q), 2, "a bash -c list"),
    ("H14", bash(CommandLine("python3 - <<'PY'\nimport subprocess\ncmd = ['cargo', 'test']\nsubprocess.run(cmd)\nPY"),
                 Q), 2, "a list bound to a name"),
    ("H15", bash(CommandLine("python3 -c \"import subprocess, sys; subprocess.run([sys.executable, '-m', 'pytest', "
                             "'python/tests'])\""), Q), 2, "sys.executable -m pytest"),
    ("H10", bash(CommandLine("valgrind cpp/build/unit_tests"), Q), 2, "valgrind"),
    ("H12", bash(CommandLine("source probes/run_all.sh"), Q), 2, "source the store"),
    ("H13", bash(CommandLine("cd tools && python3 run_ci.py"), Q), 2, "run_ci from tools/"),
    ("H05", bash(CommandLine("cat > build/r.sh <<'EOF'\ncargo test 2>&1 | tail -5\nEOF\nbash build/r.sh"), Q), 2,
     "a script the call writes into the tree"),
    ("H16", bash(CommandLine(f"bash < {S}/sweep.sh"), Q), 2, "a script on stdin"),
    ("F3", bash(CommandLine("time for p in a b; do cargo test -p $p; done"), Q), 2, "time for"),
    ("F4c", bash(CommandLine("n=$(( $(cargo test 2>&1 | grep -c FAILED) ))"), Q), 2, "a run inside arithmetic"),
    ("F5b", bash(CommandLine("echo \"$(rg -c case tools)\" && cargo test"), Q), 2, "case as an argument in $( )"),
    ("F1q", bash(CommandLine("cargo test; fish -c 'for f in *.py; echo $f; end'"), Q), 2, "a run beside fish text"),
    ("F8b", bash(CommandLine("echo $[ 1 << 2 ]\ncargo test"), Q), 2, "$[ ] arithmetic"),
]

WRITES3 = [
    ("N1a", bash(CommandLine("python/.venv/bin/python -m ruff check tools/"), T), 0, "the venv python binary"),
    ("N1b", bash(CommandLine("python/.venv/bin/python -m pytest tests/test_x.py -q"), T), 0,
     "the venv python, targeted pytest"),
    ("N3a", bash(CommandLine(f"python3 - <<'PY'\nimport pathlib\np=pathlib.Path(\"{S}/notes.md\"); s=p.read_text()\n"
                             "p.write_text(s + 'x')\nPY"), T), 0, "statements joined by ;"),
    ("N3b", bash(CommandLine("python3 - <<'PY'\np = \".commands-to-run.sh\"; s = open(p).read()\n"
                             "open(p, \"w\").write(s)\nPY"), T), 0, "the dribble edited from Python"),
    ("N4", bash(CommandLine("python3 - <<'PY'\nfrom pathlib import Path\np = Path(\"CHANGELOG.md\")\n"
                            f"p.write_text(\"x\")\np = Path(\"{S}/n.md\")\np.write_text(\"y\")\nPY"), T), 2,
     "a name bound twice"),
    ("N5a", bash(CommandLine(f"python3 - \"{S}\" <<'PY'\nimport sys\nS = sys.argv[1]\nopen(f\"{{S}}/a.patch\",\"w\")\n"
                             "PY"), T), 0, "an f-string over an argument"),
    ("N5b", bash(CommandLine(f"python3 - <<'PY'\nROOT = \"{T}\"\nopen(f\"{{ROOT}}/CHANGELOG.md\",\"w\")\nPY"), S), 2,
     "an f-string over a known root"),
    ("N7a", bash(CommandLine("for f in $(git ls-files '*.md'); do sed -i 's/a/b/' \"$f\"; done"), T), 2,
     "a loop over git ls-files"),
    ("N7b", bash(CommandLine("f=$(git ls-files | grep CHANGELOG); sed -i 's/a/b/' \"$f\""), T), 2,
     "an unknown file name"),
    ("N7c", bash(CommandLine("e() { sed -i 's/a/b/' \"$1\"; }; e CHANGELOG.md"), T), 2, "a function's argument"),
    ("N8", bash(CommandLine("for f in /tmp/proof2-x.txt CHANGELOG.md; do printf 'x\\n' > \"$f\"; done"), T), 2,
     "the second loop value"),
    ("N9", bash(CommandLine("git hash-object CHANGELOG.md"), T), 0, "git hash-object"),
    ("N10", bash(CommandLine("M=~/.x-guard-test; echo x >> \"$M/notes.md\""), T), 0, "a tilde in an assignment"),
    ("N12a", bash(CommandLine("python3 -c \"import os; fd = os.open('/tmp/proof2b-a.lock', os.O_RDONLY)\""), T), 0,
     "os.open"),
    ("N12b", bash(CommandLine("python3 -c \"import gzip; gzip.open('/tmp/a.gz').read()\""), T), 0,
     "gzip.open for reading"),
    ("N13", bash(CommandLine(f"head -2 {S}/lib.sh > {S}/m.sh; cat >> {S}/m.sh <<'EOF'\nw\nEOF\nbash {S}/m.sh"), T), 2,
     "a script built in parts"),
    ("W01", bash(CommandLine("sed -i 's/a/b/' *.md"), T), 2, "a leading glob"),
    ("W02", bash(CommandLine("rm -f *.bak"), T), 2, "rm of a glob"),
    ("W03", bash(CommandLine("cd tools && sed -i 's/a/b/' *.py"), T), 2, "cd, then a glob"),
    ("W05", bash(CommandLine("echo x > \"notes-$(date +%F).md\""), T), 2, "a dated name"),
    ("W06", bash(CommandLine("cp CHANGELOG.md \"CHANGELOG.$(date +%s).bak\""), T), 2, "a timestamped copy"),
    ("W07", bash(CommandLine("sed -i 's/a/b/' $(git ls-files '*.md')"), T), 2, "operands from git ls-files"),
    ("W09", bash(CommandLine("git ls-files '*.md' | while read -r f; do sed -i 's/a/b/' \"$f\"; done"), T), 2,
     "a read loop"),
    ("W27", bash(CommandLine("D=$(cd tools && pwd); sed -i s/a/b/ \"$D/x.py\""), T), 2, "$(cd X && pwd)"),
    ("W37", bash(CommandLine("echo x > \"$(realpath .)/n.txt\""), T), 2, "$(realpath .)"),
    ("W32", bash(CommandLine("FILE=CHANGELOG.md bash -c 'sed -i s/a/b/ \"$FILE\"'"), T), 2,
     "a prefix assignment into bash -c"),
    ("W10", bash(CommandLine("cd \"$(git rev-parse --show-toplevel 2>/dev/null)\" && sed -i s/a/b/ CHANGELOG.md"),
                 T / "tools"), 2, "a top-level substitution with a redirect"),
    ("W34", bash(CommandLine("cd \"${ROOT:-$PWD}\" && sed -i s/a/b/ CHANGELOG.md"), T), 2, "a defaulted directory"),
    ("W11", bash(CommandLine("python3 -m tools.check_apply --apply"), T), 2, "a module that rewrites files"),
    ("W12", bash(CommandLine("python3 -c \"import subprocess; "
                             "subprocess.run(['sed', '-i', 's/a/b/', 'CHANGELOG.md'])\""), T), 2,
     "sed through subprocess"),
    ("W13", bash(CommandLine("python3 -c \"import subprocess; subprocess.run(['git', 'stash'])\""), T), 2,
     "git through subprocess"),
    ("W36", bash(CommandLine("python3 -c \"import subprocess, sys; "
                             "subprocess.run([sys.executable, '-m', 'ruff', 'format', 'tools'])\""), T), 2,
     "ruff format through subprocess"),
    ("W15", bash(CommandLine("python3 -c \"import os; p='CHANGELOG.md'; os.system(f'sed -i s/a/b/ {p}')\""), T), 2,
     "an f-string command"),
    ("W17", bash(CommandLine(f"python3 {S}/default_then_argv.py CHANGELOG.md"), T), 2, "a default replaced by argv"),
    ("W16", bash(CommandLine(f"python3 {S}/argp.py {T}/CHANGELOG.md"), S), 2, "an argparse target"),
    ("W18", bash(CommandLine(f"python3 - <<'PY'\nfrom pathlib import Path\nROOT = \"{T}\"\n"
                             f"Path(f'{{ROOT}}/CHANGELOG.md').write_text('x')\nPY"), S), 2, "an f-string Path"),
    ("W20", bash(CommandLine(f"f() {{ printf 'x\\n' >> CHANGELOG.md; }}; cd {T} && f"), S), 2,
     "a function called after cd"),
    ("W21", bash(CommandLine(f"w() {{ echo x > \"$OUT\"; }}; OUT={T}/n.txt; w"), S), 2,
     "a function reading a later variable"),
    ("W33", bash(CommandLine(f"python3 - < {S}/fixrel.py"), T), 2, "python code from a < file"),
    ("W39", bash(CommandLine(f"cat {S}/fixrel.py | python3 -"), T), 2, "python code catted in"),
    ("W38", bash(CommandLine(f"bash < {S}/w_rel.sh"), T), 2, "a shell script from a < file"),
    ("W22", bash(CommandLine(f"find {T}/tools -name '*.py' -exec sed -i s/a/b/ {{}} +"), S), 2, "find -exec with {}"),
    ("W23", bash(CommandLine("find . -exec sh -c 'sed -i s/a/b/ \"$1\"' _ {} \\;"), T), 2, "find -exec sh -c"),
    ("W28", bash(CommandLine("xargs sh -c 'for f; do sed -i s/a/b/ \"$f\"; done' _ < /tmp/list"), T), 2, "xargs sh -c"),
    ("W24", bash(CommandLine("gh pr checkout 1"), T), 2, "gh pr checkout"),
    ("W25", bash(CommandLine("gh run download 1 -n logs"), T), 2, "gh run download"),
    ("W26", bash(CommandLine("tmp=$(mktemp notes.XXXXXX); echo x > \"$tmp\""), T), 2, "mktemp with a bare template"),
    ("R2a", bash(CommandLine("perl -ne 'print if /mkdir/' tools/x.py"), T), 0, "perl -ne"),
    ("R2b", bash(CommandLine("perl -pe 's/a/b/' CHANGELOG.md"), T), 0, "perl -pe to stdout"),
    ("R2c", bash(CommandLine("perl -lane 'print' tools/x.py"), T), 0, "perl -lane"),
    ("R2d", bash(CommandLine("ruby -ne 'puts $_' tools/x.py"), T), 0, "ruby -ne"),
    ("R2e", bash(CommandLine("node -pe 'process.version'"), T), 0, "node -pe"),
    ("R2f", bash(CommandLine("perl -0777 -ne 'print' tools/x.py"), T), 0, "perl -0777 -ne"),
    ("R2g", bash(CommandLine("perl -Mstrict -wne 'print' CHANGELOG.md"), T), 0, "perl -Mstrict"),
    ("R2h", bash(CommandLine("perl -i -pe 's/a/b/' tools/x.py"), T), 2, "perl -i -pe"),
    ("R2i", bash(CommandLine("perl -e '$x = $a / 2; unlink $f; $y = $b / 3'"), T), 2, "unlink between two divisions"),
    ("R2j", bash(CommandLine("perl -e 'print $n/2 if 1; mkdir \"out\"'"), T), 2, "a slash that divides, then mkdir"),
    ("R2k", bash(CommandLine("perl -e 'print \"unlink\"'"), T), 0, "a call name inside a string"),
    ("R2l", bash(CommandLine("perl -ne 'print $a/2 if 1; print if /mkdir/' tools/x.py"), T), 0,
     "a division, then a pattern"),
    ("F1f", bash(CommandLine("fish -c 'for p in $PATH; echo $p; end'"), T), 2,
     "fish text while a run is live (unreadable)"),
    ("F4d", bash(CommandLine("a=( \"$(git stash)\" )"), T), 2, "a substitution in an array value"),
    ("F4e", bash(CommandLine("case x in $(git stash)) ;; esac"), T), 2, "a substitution in a case pattern"),
    ("F5a", bash(CommandLine("n=$(grep -c esac tools/x.py); echo \"$n\""), T), 0, "esac as an argument"),
    ("F6a", bash(CommandLine("files=(\n  a.py  # the first\n  b.py  # it's the second\n)\nls \"${files[@]}\""), T), 0,
     "an array with comments"),
    ("F6b", bash(CommandLine("a=($'it\\'s'); echo \"${a[@]}\""), T), 0, "an array with an ANSI quote"),
    ("F10.2", bash(CommandLine("echo x > $'CHANGELOG.md\\0b'"), T), 2, "a NUL in a redirect path"),
]

LOOPS3 = [
    ("U01", bash(CommandLine("while true; do sleep 1; done; echo \"open"), T), 2,
     "a loop in text the reader cannot follow"),
    ("U02", bash(CommandLine("while true; do echo x; done; echo \"open"), T), 0,
     "unreadable text, no sleep in the loop"),
    ("U03", monitor(CommandLine("pgrep -f cargo; echo \"open"), T), 2, "an unreadable Monitor script with pgrep -f"),
    ("U04", monitor(CommandLine("pgrep -f '[c]argo'; echo \"open"), T), 0, "an unreadable Monitor script, bracketed"),
    ("L13.2", bash(CommandLine("sleep 9 & sleep 8 & wait -n"), T), 2, "wait -n reaps one job"),
    ("L14.2", bash(CommandLine("sleep 9 & wait -n"), T), 2, "zsh: wait -n is an error that reaps nothing"),
    ("L15.2", bash(CommandLine("sleep 9 & sleep 8 & wait -n; wait -n"), T), 2, "zsh: two wait -n reap nothing"),
    ("L16.2", bash(CommandLine("sleep 9 & wait 123"), T), 2, "a literal pid names no child"),
    ("L17.2", bash(CommandLine("sleep 9 & sleep 8 & wait -p got -n"), T), 2, "wait -p VAR -n reaps one"),
    ("L18.2", bash(CommandLine("sleep 9 & a=$!; sleep 8 & b=$!; wait -n $a $b"), T), 2,
     "wait -n over two pids reaps one"),
    ("L19", bash(CommandLine("sleep 9 & a=$!; sleep 8 & wait -p got $a"), T), 2, "-p takes its variable, not a pid"),
    ("L20", bash(CommandLine("sleep 9 & sleep 8 & wait -p got"), T), 2, "zsh: wait -p is an error"),
    ("L10b", bash(CommandLine("tail -f /tmp/x.log | grep --line-buffered FAIL &\nsleep 999 &\nwait $!"), T), 2,
     "wait $! reaps only the last job"),
    ("L12b", bash(CommandLine("trap 'echo skill issue' EXIT\nsleep 999 &"), T), 2, "a trap that kills nothing"),
    ("O02", monitor(CommandLine("tail -f /tmp/a.log | grep --line-buffered FAIL &\n"
                                "tail -f /tmp/b.log | grep --line-buffered FAIL &\npid=$!\ntrap 'kill $pid' EXIT\n"
                                "while kill -0 123 2>/dev/null; do sleep 15; done"), T), 2,
     "a trap that kills one of two jobs"),
    ("O05", bash(CommandLine("sleep 7799 & wait | cat"), T), 2, "a wait in a pipeline"),
    ("O04", bash(CommandLine("sleep 7799 &\nfinish() { wait; }"), T), 2, "a wait in a function never called"),
    ("O06", bash(CommandLine("echo 'sleep 999' | at now"), T), 2, "at"),
    ("F2a", bash(CommandLine("(cd go && go vet ./aletheia/) & (cd rust && cargo fmt --check) & wait"), T), 0,
     "background subshells reaped by wait"),
    ("F2b", bash(CommandLine("( sleep 1 ) & wait"), T), 0, "a background subshell reaped by wait"),
    ("F3b", bash(CommandLine("time while ! grep -q done /tmp/x.log; do sleep 5; done"), T), 2, "time while"),
    ("F4a", monitor(CommandLine("while (( $(pgrep -fc 'cargo test') > 0 )); do sleep 10; done"), T), 2,
     "a self-matching pgrep inside arithmetic"),
    ("F4b", monitor(CommandLine("pids=($(pgrep -f cargo)); while kill -0 \"${pids[@]}\" 2>/dev/null; do sleep 5; done"),
                    T), 2, "a self-matching pgrep inside an array"),
    ("O09", monitor(CommandLine("while pgrep -f '^cargo test' >/dev/null; do sleep 5; done"), T), 0,
     "an anchored pattern"),
    ("O10", monitor(CommandLine("while pgrep -fx 'cargo test' >/dev/null; do sleep 5; done"), T), 0, "pgrep -x"),
    ("O08", monitor(CommandLine("while pgrep -f '[Cc]argo' >/dev/null; do sleep 5; done"), T), 0, "a two-letter class"),
    ("O07", monitor(CommandLine(f"bash {S}/watch.sh"), T), 0, "pgrep inside a script, not on the Monitor's argv"),
]

PROCS3 = [
    ("P41", [Word("bash"), Word("run_all.sh")], Q2 / "probes", 2),
    ("P42", [Word("bash"), Word("-euo"), Word("pipefail"), Word("probes/run_all.sh")], Q2, 2),
    ("P43", [Word("valgrind"), Word("cpp/build/unit_tests")], Q2, 2),
    ("P44", [Word("python3"), Word("run_ci.py")], Q2 / "tools", 2),
]


def round2_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, event, want, what in SHAPE3:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in WRITES3:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    for cid, event, want, what in LOOPS3:
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    for cid, argv, cwd, want in PROCS3:
        p = start(argv, cwd)
        try:
            rc, err = hook(GUARD, edit("Edit", PathText(f"{Q2}/f.txt"), Q2))
        finally:
            stop(p)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(" ".join(argv) + f" @{cwd.name}"), err))
    cmd = CommandLine(f"taskset -c 0-19 cargo test > {X} 2>&\u00b2")
    rc, err = hook(GUARD, bash(cmd, Q))
    rows.append(CaseRow(CaseId("F7s"), ExitStatus(1), ExitStatus(int(rc in (0, 2))),
                        Prose(f"a superscript fd, exit {rc}"), err))
    return rows



# ─── the fixes that gate the install ──────────────────────────────────────────

AAAA = "a" * 40
PRE_WRITES = [
    ("K01", bash(CommandLine(f"L={S}/logs/$(date +%s).log; echo hi > \"$L\""), T), 0,
     "the house log shape beside a light command"),
    ("K02", bash(CommandLine(f"export L={S}/x-$(date +%s).log; echo hi > \"$L\""), T), 0,
     "an exported partial log path"),
    ("K03", bash(CommandLine(f"L={T}/logs/$(date +%s).log; echo hi > \"$L\""), S), 2,
     "a partial path into the live tree"),
    ("K04", bash(CommandLine(f"L={S}/$(date); read L; echo hi > \"$L\""), T), 2, "a partial path forgotten after read"),
    ("K05", bash(CommandLine(f"L={S}/$(date); unset L; echo hi > \"$L\""), T), 2,
     "a partial path forgotten after unset"),
    ("K06", bash(CommandLine("for L in $(ls); do echo hi > \"$L\"; done"), T), 2,
     "a loop variable with no known prefix"),
]
PRE_LOOPS = [
    ("K10", monitor(CommandLine("while pgrep -f 'x{99999999999}' >/dev/null; do sleep 5; done"), T), 2,
     "a repetition re cannot build"),
    ("K11", bash(CommandLine("while pgrep -f 'x{99999999999}' >/dev/null; do sleep 5; done"), T), 2,
     "the same in Bash"),
    ("K12", monitor(CommandLine("while pgrep -f '" + "(" * 2000 + "x" + ")" * 2000 + "' >/dev/null; do sleep 5; done"),
                    T), 2, "a pattern nested past re's recursion"),
    ("K13", monitor(CommandLine(f"while pgrep -f '(a+)+$' >/dev/null; do sleep 5; done; echo {AAAA}!"), T), 0,
     "a backtracking pattern that matches nothing"),
    ("K14", monitor(CommandLine("while pgrep -f '\\<cargo\\>' >/dev/null; do sleep 5; done"), T), 2,
     "word edges, pgrep's dialect"),
    ("K15", monitor(CommandLine("cargo --version >/dev/null; while pgrep -f '^cargo' >/dev/null; do sleep 5; done"), T),
     0, "an anchor the shell's own line starts before"),
    ("K16", monitor(CommandLine("while pgrep -fx '.*cargo test.*' >/dev/null; do sleep 5; done"), T), 2,
     "-x with a pattern spanning the whole line"),
    ("K17", monitor(CommandLine("while pgrep -f 'it'\\''s' >/dev/null; do sleep 5; done"), T), 0,
     "a quote the shell's line carries escaped, so the pattern never meets itself"),
    ("K24", monitor(CommandLine("echo \"x'y\"; while pgrep -f \"x'\\\"'\\\"'y\" >/dev/null; do sleep 5; done"), T), 2,
     "the tool quotes a single quote as '\"'\"' (measured), and a pattern can meet that spelling"),
    ("K18", monitor(CommandLine("sleep 1\nwhile pgrep -f '^while' >/dev/null; do sleep 5; done"), T), 0,
     "an anchor that fits the script's second line, not the shell's whole line"),
]


def pre_install_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in PRE_WRITES:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    for cid, event, want, what in PRE_LOOPS:
        t0 = time.monotonic()
        rc, err = hook(LOOPS, event)
        took = time.monotonic() - t0
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), ExitStatus(rc if took < 5 else -1),
                            Prose(f"{what} ({took:.1f}s)"), err))
    # Every shape an advice text recommends passes both hooks.
    rc1, e1 = hook(LOOPS, bash(CommandLine("pgrep -f '[c]argo test'"), Q))
    rc2, e2 = hook(GUARD, bash(CommandLine("pgrep -f '[c]argo test'"), Q))
    rows.append(CaseRow(CaseId("K20"), ExitStatus(0), ExitStatus(max(rc1, rc2)), Prose("MONITOR_ADVICE's capture step"),
                        Prose(e1 + e2)))
    rc, err = hook(LOOPS, monitor(CommandLine("tail -f /tmp/x.log | grep --line-buffered FAIL &\necho work\nwait"), T))
    rows.append(CaseRow(CaseId("K21"), ExitStatus(0), rc, Prose("ORPHAN_ADVICE's bare wait after a pipeline job"), err))
    rc, err = hook(LOOPS, monitor(CommandLine("tail -f /tmp/x.log | grep --line-buffered FAIL &\necho work\nwait $!"),
                                  T))
    rows.append(CaseRow(CaseId("K22"), ExitStatus(2), rc, Prose("wait $! after a pipeline job leaves the tail"), err))
    rc, err = hook(GUARD, bash(CommandLine("cargo test; echo x " + "9" * 5000 + ">f"), Q, bg=False))
    rows.append(CaseRow(CaseId("K23"), ExitStatus(2), rc,
                        Prose("a 5000-digit number before > is a word, not a descriptor"), err))
    # A reader fault, made by the reader raising, is reported with exit 1: the guard as installed, imported.
    fault = subprocess.run([sys.executable, "-c", (
        "import importlib.util, io, json, sys\n"
        f"sys.path.insert(0, {str(GUARD.parent)!r})\n"
        f"spec = importlib.util.spec_from_file_location('g', {str(GUARD)!r})\n"
        "g = importlib.util.module_from_spec(spec); sys.modules['g'] = g; spec.loader.exec_module(g)\n"
        "def boom(*a, **k): raise RuntimeError('reader fault')\n"
        "g.C.expand = boom\n"
        f"sys.stdin = io.StringIO(json.dumps({json.dumps(bash(CommandLine('echo hi'), Q))}))\n"
        "sys.exit(g.main())\n")], capture_output=True, text=True, check=False, env=HOOK_ENV, timeout=60)
    rows.append(CaseRow(CaseId("K23b"), ExitStatus(1), ExitStatus(fault.returncode),
                        Prose("a reader fault with no run live is reported, exit 1"), Prose(fault.stderr)))
    return rows



# ─── the fourth round: jobs, CPUs, copies, interpreter options ───────────────

def round4_fixtures() -> None:
    (S / "bg_job.sh").write_text("sleep 1\necho done\n")
    (S / "cpu_bad.sh").write_text("taskset -c 0-22 ls\n")
    (Q / "probes").mkdir(exist_ok=True)
    (Q / "probes" / "cpu_bad.sh").write_text("taskset -c 0-23 ls\n")
    (Q / "probes" / "cpu_ok.sh").write_text('cpus="0-$(($(nproc) - 2))"\ntaskset -c "$cpus" ls\n')
    waiter = "#!/usr/bin/env python3\nimport os, time\nwhile not os.path.exists('/tmp/x.done'):\n    time.sleep(0.05)\n"
    (Q / "tools").mkdir(exist_ok=True)
    (Q / "tools" / "waiter.py").write_text(waiter)
    (S / "waiter.py").write_text(waiter)


LOOPS4 = [
    ("J13", bash(CommandLine("(cd go && git status --short) &\nwait $!"), T), 0, "a background subshell, wait $!"),
    ("J12", bash(CommandLine("(cd go && git status --short) & p=$!; wait $p"), T), 0, "a background subshell, wait $p"),
    ("J14", bash(CommandLine("{ cd go && git status --short; } & wait $!"), T), 0, "a background group"),
    ("J15", bash(CommandLine("(cd go && git status --short) & wait -n"), T), 2, "zsh: a background subshell, wait -n"),
    ("J17", bash(CommandLine("cd go && git status --short &\nwait $!"), T), 0, "a background and-or list"),
    ("J06b", bash(CommandLine("for d in a b c; do (cd \"$d\" && git status --short) & done; wait -n; wait -n; wait -n"),
                  T), 2, "zsh: three wait -n reap nothing"),
    ("J06c", bash(CommandLine("for d in a b c; do (cd \"$d\" && git status --short) & done; wait -n; wait -n"), T), 2,
     "three jobs, two wait -n"),
    ("J01b", bash(CommandLine("pids=()\nfor d in go rust cpp; do\n  (cd \"$d\" && git status --short) & pids+=($!)\n"
                              "done\nwait \"${pids[@]}\""), T), 0, "wait over an array of pids"),
    ("J04b", bash(CommandLine("python3 -m http.server 8765 & srv=$!\ncurl -s localhost:8765 > /dev/null\nkill $srv"),
                  T), 0, "a server killed by its pid"),
    ("J05b", bash(CommandLine("python3 -m http.server 8765 & a=$!\npython3 -m http.server 8766 & b=$!\n"
                              "trap 'kill $a $b' EXIT\necho work"), T), 0, "a trap killing two pids"),
    ("J05c", bash(CommandLine("python3 -m http.server 8765 & a=$!\npython3 -m http.server 8766 & b=$!\n"
                              "trap 'kill $a' EXIT\necho work"), T), 2, "a trap killing one of two"),
    ("L08.2", bash(CommandLine("sleep 1 & wait\nsleep 999 &"), T), 2, "a wait before the job it would reap"),
    ("L09.2", bash(CommandLine("{ sleep 999 & wait; } &"), T), 2, "a background group nothing waits for"),
    ("L21", bash(CommandLine("sleep 999 & wait &"), T), 2, "a wait that runs in the background"),
    ("L11.2", bash(CommandLine("tail -f /tmp/a | grep x &\nsleep 999 &\ntrap 'kill %1' EXIT\necho work"), T), 2,
     "kill %1 stops the first job only"),
    ("L22", bash(CommandLine("tail -f /tmp/a | grep x & wait %1"), T), 2,
     "zsh: a numbered job spec names nothing sure"),
    ("L23", bash(CommandLine("tail -f /tmp/a | grep x & sleep 9 & wait %2"), T), 2, "wait %2 leaves the first job"),
    ("L24", bash(CommandLine("coproc sleep 1; wait"), T), 0, "a coprocess reaped by a bare wait"),
    ("L25", bash(CommandLine("coproc W { sleep 5; }; wait"), T), 0, "a named coprocess reaped"),
    ("L26", bash(CommandLine("bash -c 'sleep 1' & wait"), T), 0, "a background carrier"),
    ("L27", bash(CommandLine(f"bash {S}/bg_job.sh & P=$!; wait $P"), T), 0, "a background script, wait on its pid"),
    ("L28", bash(CommandLine("cd /tmp && sleep 1 & P=$!; wait $P"), T), 0, "cd && sleep in the background"),
    ("L29", bash(CommandLine("git fetch -q & eval wait"), T), 0, "eval runs wait in this shell"),
    ("L30", bash(CommandLine("sleep 99 &\nwait \"$pidd\""), T), 2, "wait on a variable never set"),
    ("L31", bash(CommandLine("sleep 9 & p=$!; kill $p"), T), 0, "a job killed by its pid"),
    ("L32", bash(CommandLine("tail -f /tmp/a | grep x & p=$!; kill $p"), T), 2,
     "kill $! of a pipeline leaves the tail"),
    ("L33", bash(CommandLine("sleep 999 & sleep 998 &\necho work\nkill $(jobs -p)"), T), 2,
     "zsh: $(jobs -p) prints nothing"),
]

# A one-shot lookup by a pattern the call's own shell carries (user, 2026-10-14): refused outside loops too.
ONESHOT = [
    ("OS01", bash(CommandLine("pgrep -f 'cargo test'"), T), 2, "a one-shot pgrep -f meets the call's shell"),
    ("OS02", bash(CommandLine("pgrep -af cargo"), T), 2, "pgrep -af"),
    ("OS03", bash(CommandLine("pkill -f 'cargo test'"), T), 2, "pkill -f would kill the call"),
    ("OS04", bash(CommandLine("ps aux | grep 'cargo test'"), T), 2, "ps | grep meets the call's shell"),
    ("OS05", bash(CommandLine("pgrep -f \"$UNSET_PATTERN_ONESHOT\""), T), 2, "a pattern the reader cannot know"),
    ("OS06", bash(CommandLine("bash -c \"pgrep -f cargo\""), T), 2, "a carrier's pgrep, its text on the call's line"),
    ("OS07", bash(CommandLine("pgrep -f '[c]argo test'"), T), 0, "bracketed, the target spelled nowhere else"),
    ("OS08", bash(CommandLine("pgrep -x cargo"), T), 0, "pgrep -x on the process name"),
    ("OS09", bash(CommandLine("pgrep cargo"), T), 0, "pgrep without -f matches names"),
    ("OS10", bash(CommandLine("ps aux | grep '[c]argo test'"), T), 0, "ps | grep, bracketed"),
    ("OS11", bash(CommandLine("ps aux | grep cargo | grep -v grep"), T), 0,
     "a grep -v grep stage drops the call's lines"),
    ("OS12", bash(CommandLine("pgrep -f '^cargo'"), T), 0, "anchored: the call's line starts with its shell"),
    ("OS13", bash(CommandLine("cat <<'EOF'\npgrep -f cargo\nEOF"), T), 0, "a heredoc's data"),
    ("OS14", bash(CommandLine("echo 'pgrep -f cargo'"), T), 0, "pgrep as an argument"),
]

LOOPS6 = [
    ("L40", bash(CommandLine("f() { tail -f /tmp/a | grep x & }; trap f INT; f"), T), 2,
     "a trap's function called directly"),
    ("L41", bash(CommandLine("tail -f /tmp/a | grep x &\ntrap 'kill -s 0 $$' EXIT\necho work"), T), 2,
     "kill -s 0 stops nothing"),
    ("L42", bash(CommandLine("tail -f /tmp/a | grep x &\ntrap 'trap \"\" TERM; kill -s TERM 0' EXIT\necho work"), T), 0,
     "kill -s TERM 0 stops the group"),
    ("L43", monitor(CommandLine("while pgrep -f \"$(cat /tmp/pat)\" >/dev/null; do sleep 5; done"), T), 2,
     "a pgrep pattern out of sight"),
    ("L44", bash(CommandLine("until timeout 5 tail -f /tmp/a | grep -q DONE; do :; done"), T), 2,
     "a timed tail in a loop"),
    ("L45", bash(CommandLine("while ! read -t 5 < /tmp/fifo; do echo waiting; done"), T), 2, "read -t in a loop"),
    ("L46", bash(CommandLine("until inotifywait -t 5 -e close_write /tmp/out; do echo again; done"), T), 2,
     "inotifywait -t in a loop"),
    ("L47", bash(CommandLine("until [ -f /tmp/x.done ]; do :; done"), T), 2, "a busy loop on a file"),
    ("L47b", bash(CommandLine("i=0; while [ \"$i\" -lt 3 ]; do i=$((i+1)); done"), T), 0,
     "a counting loop of checks ends"),
    ("L47c", bash(CommandLine("while read -r l; do :; done < /tmp/list"), T), 0,
     "a `:` beside a command that is not a check"),
    ("L44b", bash(CommandLine("until taskset -c 0-19 timeout 5 tail -f /tmp/a | grep -q DONE; do echo again; done"), T),
     2, "timeout behind another wrapper"),
    ("L45b", bash(CommandLine("while ! read -rt 5 < /tmp/fifo; do echo waiting; done"), T), 2, "read -rt, a cluster"),
    ("L46b", bash(CommandLine("until inotifywait -qt 5 /tmp/out; do echo again; done"), T), 2,
     "inotifywait -qt, a cluster"),
    ("L46c", bash(CommandLine("while read -r line; do echo \"$line\"; done < /tmp/list"), T), 0,
     "read with no timeout"),
    ("L46d", bash(CommandLine("until inotifywait -e close_write /tmp/out; do echo again; done"), T), 0,
     "inotifywait with no timeout blocks until the event"),
    ("L48", bash(CommandLine("while kill -0 $p 2>/dev/null; do :; done"), T), 2, "a busy loop on a pid"),
    ("L49", bash(CommandLine("for p in $pids; do for t in $p $(pgrep -P $p); do :; done; done"), T), 0,
     "a for loop with an empty body ends"),
    ("L50", bash(CommandLine("python3 -c \"import os, time\nwhile not os.path.exists('/tmp/x'):\n    time.sleep(2)\""),
                 T), 2, "a Python while loop that sleeps"),
    ("L51", bash(CommandLine("python3 - <<'PY'\nimport time\nfor _ in range(3):\n    time.sleep(1)\nPY"), T), 2,
     "a Python for loop that sleeps polls as its shell twin L02 does (the user's ruling, 2026-09-27)"),
    ("L52", bash(CommandLine("python3 tools/waiter.py"), Q), 0, "a repository tool's own wait"),
    ("L53", bash(CommandLine(f"python3 {S}/waiter.py"), Q), 2, "a scratch script's wait loop"),
    ("L52b", bash(CommandLine("./tools/waiter.py"), Q), 0, "a repository tool run by its path"),
    ("L53b", bash(CommandLine(f"{S}/waiter.py"), Q), 2, "a scratch script run by its path"),
    ("L52c", bash(CommandLine("python3 -m tools.waiter"), Q), 0, "a repository module's own wait"),
    ("L50b", bash(CommandLine("python3 -c \"import asyncio\nasync def m():\n    while True:\n"
                              "        await asyncio.sleep(1)\nasyncio.run(m())\""), T), 2,
     "an asyncio while loop that sleeps"),
    ("L46e", bash(CommandLine("until inotifywait --timeout 5 /tmp/out; do echo again; done"), T), 2,
     "inotifywait --timeout"),
    ("L54", bash(CommandLine("pidwait -f 'cargo test'; echo finished"), T), 2, "pidwait -f on its own shell"),
    ("L55", bash(CommandLine("pidwait -f '[c]argo test'; echo finished"), T), 0, "pidwait -f with a bracketed pattern"),
    ("L56", monitor(CommandLine("pidwait -f 'cargo test'; echo finished"), T), 2,
     "Monitor: pidwait -f on its own shell"),
    ("L57", monitor(CommandLine("while ps aux | rg -q 'cargo test'; do sleep 5; done"), T), 2,
     "Monitor: ps | rg meets itself"),
    ("L58", monitor(CommandLine("ps aux | awk '/cargo test/'"), T), 2, "Monitor: ps | awk /re/ meets itself"),
    ("L59", monitor(CommandLine("ps aux | rg -q '[c]argo test' && echo up"), T), 0, "Monitor: ps | rg bracketed"),
    ("L60", monitor(CommandLine("ps aux | awk '/[c]argo test/ {print $2}'"), T), 0, "Monitor: ps | awk bracketed"),
    ("L61", monitor(CommandLine("ps aux | grep -v ' grep ' | grep -q cargo && echo up"), T), 2,
     "Monitor: ps lists the tool's grep as ugrep, which ' grep ' does not filter"),
    ("L62", bash(CommandLine("pidwait -f \"[e]cho x' && pwd\"; echo x"), T), 2,
     "the Bash line without `< /dev/null` is tried too"),
    ("L63", monitor(CommandLine("ps aux | awk '!/cargo/' | wc -l"), T), 0,
     "Monitor: an awk !/re/ stage removes its matches"),
    ("L64", monitor(CommandLine("bash -c \"ps aux | grep -v ' grep ' | grep -q cargo\""), T), 0,
     "Monitor: bash's grep is the system grep, which ' grep ' filters"),
    ("L65", monitor(CommandLine("ps aux | grep -v ' grep ' | command grep -q cargo"), T), 0,
     "Monitor: `command grep` is the system grep"),
    ("L66", monitor(CommandLine("ps aux | grep -v ' grep ' | grep --null -q cargo"), T), 0,
     "Monitor: --null sends the tool's grep to the system grep"),
    ("L68", monitor(CommandLine("ps aux | rg -q \"$(cat /tmp/p)\" && echo up"), T), 2,
     "Monitor: a pattern out of sight, in an extended-regex stage, may select the line"),
    ("L67", monitor(CommandLine("ps aux | grep -v \"$X\" | grep -q cargo"), T), 2,
     "Monitor: a -v pattern out of sight may leave the line"),
]

CPU4 = [
    ("C01.2", bash(CommandLine("taskset -c 0-22 ls"), Q), 2, "a light command named onto CPUs 20-22"),
    ("C02.2", bash(CommandLine("taskset -acp 0-23 123"), Q), 2, "re-pinning a pid onto 20-23"),
    ("C03.2", bash(CommandLine("taskset -acp 0-19 123"), Q), 0, "re-pinning a pid within 0-19"),
    ("C04.2", bash(CommandLine("taskset -p 123"), Q), 0, "reading a pid's affinity"),
    ("C04b", bash(CommandLine("taskset -p "
                              "1234567"), Q), 0, "reading the affinity of a pid whose digits look like a mask"),
    ("C05.2", bash(CommandLine("numactl -C 20-23 ls"), Q), 2, "numactl onto 20-23"),
    ("C06.2", bash(CommandLine("taskset 0xf00000 ls"), Q), 2, "a mask of 20-23"),
    ("C07.2", bash(CommandLine("taskset -c $X ls"), Q), 2, "a CPU list the call leaves unknown"),
    ("C08.2", bash(CommandLine("bash probes/cpu_bad.sh"), Q), 2, "a repository script naming 0-23"),
    ("C09.2", bash(CommandLine("bash probes/cpu_ok.sh"), Q), 0, "a repository script computing its list from nproc"),
    ("C10.2", bash(CommandLine(f"bash {S}/cpu_bad.sh"), Q), 2, "a scratch script naming 0-22"),
    ("C11.2", bash(CommandLine("taskset -c 0-19 ls"), Q), 0, "a light command within 0-19"),
    ("C12.2", bash(CommandLine("taskset -c 0-19 cargo test > /tmp/x.log 2>&1"), Q), 0, "a heavy command within 0-19"),
]

WRITES4 = [
    ("X1", bash(CommandLine("printf 'tools/x.py\\0' | xargs -0 -I{} cp --parents {} /tmp/copy/"), T), 0,
     "xargs copies out of the live tree"),
    ("X1c", bash(CommandLine("printf 'tools/x.py\\0' | xargs -0 sed -i s/a/b/"), T), 2,
     "xargs rewrites files in place"),
    ("X1d", bash(CommandLine("ls | xargs -I{} cp {} sub/"), T), 2, "xargs copies into the live tree"),
    ("P1a", bash(CommandLine("python3 -W ignore -c 'print(1)'"), T), 0, "python -W VALUE -c"),
    ("P1b", bash(CommandLine("python3 -X utf8 -m json.tool /dev/null"), T), 0, "python -X VALUE -m"),
    ("P1c", bash(CommandLine("perl -I lib -e 'print 1'"), T), 0, "perl -I DIR -e"),
    ("P1e", bash(CommandLine("python3 -W ignore -c \"open('CHANGELOG.md', 'w')\""), T), 2,
     "python -W VALUE -c writing"),
]

SHAPE4 = [
    ("P1d", bash(CommandLine("python3 -W ignore -c \"import subprocess; subprocess.run(['cargo', 'test'])\""), Q), 2,
     "a Python driver behind -W VALUE"),
]


def round4_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, event, want, what in LOOPS4 + LOOPS6 + ONESHOT:
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    for cid, event, want, what in CPU4 + SHAPE4:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in WRITES4:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



# ─── the fourth round: Python drivers ─────────────────────────────────────────

def py(code: Source) -> CommandLine:
    return CommandLine(f"python3 - <<'PY'\n{code}\nPY")


def round4_py_fixtures() -> None:
    (S / "cwd_kw.py").write_text(f"import subprocess\nsubprocess.run(['git', 'stash'], cwd='{T}')\n")
    (S / "argv_from.py").write_text("from sys import argv\nopen(argv[1], 'w').write('x')\n")
    (S / "inplace.py").write_text("import fileinput\nfor line in fileinput.input(inplace=True):\n"
                                  "    print(line, end='')\n")


PYWRITES4 = [
    ("Y06", bash(CommandLine("python3 -c \"import subprocess as sp; sp.run(['git', 'stash'])\""), T), 2,
     "import subprocess as sp"),
    ("Y07", bash(CommandLine("python3 -c \"from os import system; system('git stash')\""), T), 2,
     "from os import system"),
    ("Y01", bash(CommandLine(f"python3 {S}/cwd_kw.py"), S), 2, "a cwd= keyword into the live tree"),
    ("C1", bash(CommandLine("python3 -c \"import subprocess; subprocess.run(['git', 'stash'], cwd='/tmp/siderepo')\""),
                T), 0, "a cwd= keyword out of the live tree"),
    ("Y02", bash(CommandLine(f"python3 -c \"import os; os.chdir('{T}'); open('CHANGELOG.md', 'w')\""), S), 2,
     "os.chdir into the live tree"),
    ("Y04", bash(CommandLine("python3 -c \"import subprocess, sys; "
                             "subprocess.run(['sed', '-i', 's/a/b/'] + sys.argv[1:])\""), T), 2,
     "an argv list grown at run time"),
    ("Y05", bash(py(Source("import subprocess\ncmd = ['sed', '-i', 's/a/b/']\ncmd.append('CHANGELOG.md')\n"
                           "subprocess.run(cmd)")), T), 2, "an argv list appended to"),
    ("Y08", bash(CommandLine("python3 -c \"import subprocess, sys; subprocess.run([sys.argv[1], 'CHANGELOG.md'])\" rm"),
                 T), 2, "a driver whose program is out of sight"),
    ("W25.2", bash(CommandLine("python3 -c \"import os; os.open('CHANGELOG.md', os.O_WRONLY | os.O_TRUNC)\""), T), 2,
     "os.open with a writing flag"),
    ("W23.2", bash(py(Source("import subprocess\nfor c in ['sed -i s/a/b/ CHANGELOG.md']:\n"
                             "    subprocess.run(c, shell=True)")), T), 2, "a shell=True command out of sight"),
    ("TF1", bash(CommandLine("python3 -c \"import pathlib, tempfile; pathlib.Path(tempfile.mkdtemp()).joinpath('x')"
                             ".write_bytes(b'a')\""), T), 0, "a path under a tempfile directory"),
    ("TF2", bash(py(Source("import tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as d:\n"
                           "    (Path(d) / 'x').write_text('a')")), T), 0, "a TemporaryDirectory"),
    ("J1", bash(py(Source(f"from pathlib import Path\nW = Path('{S}/wt')\nfor f in ['tools/x.py']:\n"
                          "    (W / f).write_text('x')")), T), 0, "a known directory joined with an unknown name"),
    ("J1b", bash(py(Source(f"from pathlib import Path\nW = Path('{T}')\nfor f in ['tools/x.py']:\n"
                           "    (W / f).write_text('x')")), S), 2, "the live tree joined with an unknown name"),
    ("J1c", bash(py(Source(f"import os\nfor f in ['x']:\n    open(os.path.join('{T}', f), 'w')")), S), 2,
     "os.path.join of the live tree and an unknown name"),
    ("J1d", bash(py(Source(f"for f in ['x']:\n    open(f'{S}/{{f}}.txt', 'w')")), T), 0,
     "an f-string under a known directory"),
    ("W34.2", bash(CommandLine(f"python3 {S}/argv_from.py {T}/CHANGELOG.md"), S), 2, "from sys import argv"),
    ("W35", bash(CommandLine(f"python3 {S}/inplace.py {T}/CHANGELOG.md"), S), 2, "fileinput in place over argv"),
]

PYSHAPE4 = [
    ("S04", bash(CommandLine("python3 -c \"import subprocess as sp; sp.run(['cargo', 'test'])\""), Q), 2,
     "an aliased driver"),
    ("S01", bash(CommandLine("python3 -c \"import subprocess, sys; subprocess.run(['cargo', 'test'] + sys.argv[1:])\""),
                 Q), 2, "a driver whose argv grows at run time"),
    ("S02", bash(CommandLine("python3 -c \"import subprocess, shlex; "
                             "subprocess.run(shlex.split('cargo test --release'))\""), Q),
     2, "shlex.split of a constant"),
    ("S03", bash(CommandLine("python3 -c \"import subprocess; subprocess.run('cargo test --release'.split())\""), Q), 2,
     "str.split of a constant"),
    ("S1", bash(py(Source("import subprocess, sys\nTESTS = ['tests/test_x.py']\n"
                          "subprocess.run([sys.executable, '-m', 'pytest', *TESTS, '-q'])")), Q), 0,
     "a targeted pytest with a starred list"),
]


def round4_py_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, event, want, what in PYSHAPE4:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in PYWRITES4:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



# ─── the fourth round: a script's own words ──────────────────────────────────

def round4_param_fixtures() -> None:
    (T / "probes").mkdir(exist_ok=True)
    (T / "probes" / "p_reads.sh").write_text("cat CHANGELOG.md > /dev/null\n")
    (T / "probes" / "p_writes.sh").write_text('cd "$(dirname "$0")/.." && echo x > CHANGELOG.md\n')
    (T / "probes" / "p_writes_bs.sh").write_text('cd "$(dirname "${BASH_SOURCE[0]}")/.." && echo x > CHANGELOG.md\n')
    (S / "one_probe.sh").write_text('probe=$1\nout=$2\nbash "$probe" > "$out" 2>&1\n')
    (S / "readonly_draft.sh").write_text("cat CHANGELOG.md > /dev/null\n")
    (S / "draft").mkdir(exist_ok=True)
    (S / "draft" / "p.sh").write_text('cd "$(dirname "$0")" && echo x > notes.txt\n')


PARAMS4 = [
    ("P01.2", bash(CommandLine(f"bash {S}/one_probe.sh probes/p_reads.sh {S}/one.log"), T), 0,
     "a helper's $2 is a scratch log"),
    ("P02.2", bash(CommandLine(f"bash {S}/one_probe.sh probes/p_reads.sh {T}/one.log"), T), 2,
     "a helper's $2 in the live tree"),
    ("P03.2", bash(CommandLine(f"bash -c 'source \"$1\"' probes/x.sh {S}/readonly_draft.sh"), T), 0,
     "-c text sourcing its $1"),
    ("P04.2", bash(CommandLine(f"bash {T}/probes/p_writes.sh"), S), 2, "a probe that cds to its root through $0"),
    ("P05.2", bash(CommandLine(f"bash {T}/probes/p_writes_bs.sh"), S), 2, "a probe that cds through BASH_SOURCE"),
    ("P06.2", bash(CommandLine(f"bash {S}/draft/p.sh"), T), 0, "a scratch script writing beside itself"),
    ("P07.2", bash(CommandLine("f() { echo x > \"$2\"; }; f a /tmp/ok.txt"), T), 0, "a function's $2 out of the tree"),
    ("P08.2", bash(CommandLine("f() { echo x > \"$2\"; }; f a CHANGELOG.md"), T), 2, "a function's $2 in the tree"),
    ("E06a", bash(CommandLine(f"echo 'echo x > {T}/CHANGELOG.md' > {S}/e.sh && bash {S}/e.sh"), S), 2,
     "a script echoed, then run"),
    ("E06b", bash(CommandLine(f"printf '%s\\n' 'echo x > {T}/CHANGELOG.md' > {S}/p.sh && bash {S}/p.sh"), S), 2,
     "a script printed, then run"),
    ("E06c", bash(CommandLine(f"echo 'echo x > /tmp/ok.txt' > {S}/e2.sh && bash {S}/e2.sh"), T), 0,
     "an echoed script writing elsewhere"),
    ("P4a", bash(CommandLine(f"cat > {S}/s.sh <<'EOF'\necho safe\nEOF\n"
                             f"sed -i 's|echo safe|echo x > {T}/CHANGELOG.md|' {S}/s.sh\nbash {S}/s.sh"), S), 2,
     "a heredoc script edited before it runs"),
]


def round4_param_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in PARAMS4:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



SMALL4 = [  # (hook, event, want, what, live)
    ("M01.2", "guard", bash(CommandLine("time ! for p in a; do cargo test; done"), Q), 2, "time ! before a loop",
     False),
    ("M02.2", "guard", bash(CommandLine("export R=\"~/x\"; echo x > \"$R/y\""), T), 2, "a quoted ~ stays literal",
     True),
    ("M03.2", "guard", bash(CommandLine("R=\\~/x; echo x > \"$R/y\""), T), 2, "an escaped ~ stays literal", True),
    ("M04.2", "guard", bash(CommandLine("export R=~/.x-guard-test; echo x > \"$R/y\""), T), 0, "a bare ~ expands",
     True),
    ("M05.2", "guard", bash(CommandLine("tar -C tools -xf /tmp/a.tar"), T), 2, "tar -C before its mode", True),
    ("M06.2", "guard", bash(CommandLine("tar -C /tmp -cf CHANGELOG.tar x"), T), 2, "tar -C, then -cf into the tree",
     True),
    ("M07", "guard", bash(CommandLine("tar -C tools -tf /tmp/a.tar"), T), 0, "tar listing", True),
    ("M08", "guard", bash(CommandLine("cabal run shake -- build -V"), Q), 2, "shake -V is verbose", False),
    ("M09", "guard", bash(CommandLine("tmux new -ds build 'cargo test'"), Q), 2, "tmux -ds NAME runs its text", False),
    ("M10", "loops", bash(CommandLine("tmux new -ds build 'sleep 999'"), T), 2, "tmux -ds detaches", False),
    ("M11", "guard", bash(CommandLine("env -S 'cargo test'"), Q), 2, "env -S splits its command", False),
    ("M12", "guard", bash(CommandLine(f"find /tmp {T}/tools -maxdepth 0 -exec touch {{}} +"), S), 2,
     "find -exec over its second root", True),
    ("M13", "guard", bash(CommandLine("taskset -c 0-$(($(nproc) - 2)) cargo test > /tmp/x.log 2>&1"), Q), 2,
     "a computed CPU list, named once", False),
    ("I02", "guard", bash(CommandLine("perl -ne 'print if m!mkdir!' tools/x.py"), T), 0, "perl m!...!", True),
    ("I03", "guard", bash(CommandLine("perl -ne 'print if m#rmdir#' tools/x.py"), T), 0, "perl m#...#", True),
    ("I04", "guard", bash(CommandLine("perl -e 'my $x = qq{unlink me}; print $x'"), T), 0, "perl qq{...}", True),
    ("I14", "guard", bash(CommandLine("ruby -ne 'puts $_ if $_ =~ %r{mkdir}' tools/x.py"), T), 0, "ruby %r{...}", True),
    ("I16", "guard", bash(CommandLine("perl -e 'use File::Path qw(make_path); make_path(\"tools/new\")'"), T), 2,
     "File::Path make_path", True),
    ("I17", "guard", bash(CommandLine("perl -e '$q->{x}; unlink \"CHANGELOG.md\"'"), T), 2, "a variable named q", True),
    ("I18", "guard", bash(CommandLine("perl -e 'my $s = 1; unlink \"CHANGELOG.md\"'"), T), 2, "a variable named s",
     True),
    ("G17", "loops", monitor(CommandLine("while ps aux | grep -q 'cargo test'; do sleep 5; done"), T), 2,
     "ps | grep meets itself", False),
    ("G17b", "loops", monitor(CommandLine("while ps aux | grep -q '[c]argo test'; do sleep 5; done"), T), 0,
     "ps | grep, bracketed", False),
    ("G17c", "loops", monitor(CommandLine("while ps aux | grep -qF 'cargo test'; do sleep 5; done"), T), 2,
     "ps | grep -F", False),
    ("G18a", "loops", monitor(CommandLine("pgrep -af cargo; echo \"open"), T), 2, "fallback: pgrep -af", False),
    ("G18b", "loops", bash(CommandLine("for i in 1 2 3; do sleep 1; done; echo \"open"), T), 2, "fallback: a for loop",
     False),
    ("G18c", "loops", bash(CommandLine("tail -f /tmp/x.log | grep --line-buffered FAIL &\necho \"open"), T), 2,
     "fallback: a background job", False),
    ("G13a", "loops", bash(CommandLine("fish -c 'while pgrep -f cargo; sleep 5; end'"), T), 2, "a fish polling loop",
     False),
    ("G13b", "guard", bash(CommandLine("fish -c 'cd rust; and cargo test'"), Q), 2, "a heavy run in fish text", False),
    ("G13c", "guard", bash(CommandLine("fish -c 'for f in *.py; echo $f; end'"), Q), 0, "light fish text, no run live",
     False),
    ("G13d", "loops", bash(CommandLine("fish -c 'while true; echo x; end; sleep 1'"), T), 0, "a sleep after fish's end",
     False),
    ("G13e", "guard", bash(CommandLine("fish -c 'if test -d rust; cargo test; end'"), Q), 2,
     "a heavy run in unreadable fish text", False),
    ("T01", "guard", bash(CommandLine("trap 'echo x > CHANGELOG.md' EXIT; true"), T), 2, "a trap text writing the tree",
     True),
    ("T02", "guard", bash(CommandLine("trap 'git checkout -- CHANGELOG.md' EXIT; echo hi"), T), 2,
     "a trap text checking out", True),
    ("T03", "guard", bash(CommandLine("trap 'cargo test' EXIT; echo hi"), Q), 2, "a heavy run in a trap text", False),
    ("T04", "guard", bash(CommandLine("trap 'echo done > /tmp/x.txt' EXIT; true"), T), 0,
     "a trap text writing elsewhere", True),
    ("F01p", "guard", bash(CommandLine("{ taskset -c 0-19 cargo test | tail -5; } > /tmp/x.log 2>&1"), Q), 2,
     "a pipe inside a group redirected whole", False),
    ("F02p", "guard", bash(CommandLine("taskset -c 0-19 bash {S}/tailpipe.sh > /tmp/x.log 2>&1"), Q), 2,
     "a pipe inside a script redirected whole", False),
    ("Z27", "guard",
     bash(CommandLine("".join(f"for v{k} in 1 2 3 4 5 6 7 8; do " for k in range(6)) + "echo x" + "; done" * 6), T),
     0, "six nested loops stay readable while a run is live", True),
]


def round4_small_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, which, event, want, what, needs_live in SMALL4:
        live = start([Word("cargo"), Word("test")], T) if needs_live else None
        if isinstance(command := event.get("tool_input", {}).get("command"), str):
            event["tool_input"]["command"] = CommandLine(command.replace("{S}", str(S)))
        try:
            rc, err = hook(GUARD if which == "guard" else LOOPS, event)
        finally:
            if live:
                stop(live)
        if cid == "M13" and "other work" in err:
            rc = ExitStatus(-1)  # the wording finding: nproc is not other work
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    return rows



# ─── the fourth round: cost, and Python this reader cannot parse ─────────────

def cost_fixtures() -> None:
    (S / "selfsrc.sh").write_text(f"source {S}/selfsrc.sh\n" * 4 + "[[ x ]]\n" * 20000)
    (S / "selfpy.py").write_text("import subprocess\n" + "".join(f"subprocess.run(['python3', '{S}/selfpy.py'])\n"
                                                                 for _ in range(10)))
    (S / "bigpy.py").write_text("x = 1\n" * 6000)
    (S / "tailpipe.sh").write_text("cargo test 2>&1 | tail -5\n")
    (S / "self.sh").write_text((f"bash {S}/self.sh; " * 16) + "\n")
    (S / "big.sh").write_text(("echo " + "x" * 70 + "\n") * 3300)


COST4 = [  # (id, command, what): every hook answers inside two seconds
    ("Z20", '"$(( $(wc -l ' * 18, "nested unterminated $(( $("),
    ("Z21", "; ".join(f"A{i}=1; echo $A{i}" for i in range(20000)), "20000 assignments"),
    ("Z22", "python3 - <<'PY'\nx = '" + "\\'" * 50000 + "\nPY", "50000 escaped quotes in Python"),
    ("Z23", "perl - <<'PL'\n" + "open " * 20000 + "\nPL", "20000 perl opens"),
    ("Z24", "".join(f"for v{k} in 1 2 3 4 5 6 7 8; do " for k in range(6)) + "echo x" + "; done" * 6,
     "six nested loops of eight values"),
    ("Z25", "bash {S}/self.sh", "a script running itself 16 times a line"),
    ("Z26", "; ".join(["bash {S}/big.sh"] * 50), "a 242 KB script run 50 times"),
    ("Z28", "perl - <<'PL'\n'" + "\\'" * 50000 + "\nPL", "perl code: a string never closed, 50000 escaped quotes"),
    ("Z35c", "f() { " + "".join(f"for a{k} in 1 2 3 4 5 6 7 8; do " for k in range(2)) + 'echo "$( '
     + "[[ x ]]; " * 2000 + ' )"' + "; done" * 2 + "; }; " + "f; " * 32, "32 calls of 8x8 loops around 2000 tests"),
    ("Z36c", "python3 - <<'PY'\nimport os\n" + "".join("os.system('bash {S}/big.sh')\n" for _ in range(40)) + "PY",
     "40 Python-started runs of a long script"),
    ("Z29", "echo " + "${" * 900 + "x" * 500000 + "}" * 900, "${ nested 900 deep around 500 KB"),
    ("Z30c", "[[ x ]]\n" * 131072, "1 MB of tests"),
    ("Z31c", "source {S}/selfsrc.sh", "a file sourcing itself, 20000 tests a time"),
    ("Z32c", "python3 {S}/selfpy.py", "a Python script starting itself ten times"),
    ("Z33c", "".join(f"for v{k} in 1 2 3 4 5 6 7 8; do " for k in range(2)) + "python3 {S}/bigpy.py" + "; done" * 2,
     "a 6000-line Python script in 8x8 loops"),
    ("Z34c", "; ".join(["while pgrep -f $'x{1,1000}{1,1000}\\x79N' >/dev/null; do sleep 5; done"] * 15),
     "fifteen patterns grep cannot settle"),
]

UNPARSED4 = [
    ("U10", bash(CommandLine("python3 -c 'x = ('"), T), 0, "Python that cannot compile runs nothing"),
    ("U11", bash(CommandLine("python3.99 -c 'x = ('"), T), 2, "Python a newer interpreter may run"),
    ("U12", bash(CommandLine("; ".join(f"A{i}=1; echo $A{i}" for i in range(20000))), T), 2,
     "too long to read, a run live"),
]


def round4_cost_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, command, what in COST4:
        command = CommandLine(command.replace("{S}", str(S)))
        worst = 0.0
        for which in (GUARD, LOOPS):
            t0 = time.monotonic()
            rc, err = hook(which, bash(command, Q))
            worst = max(worst, time.monotonic() - t0)
            if rc not in (0, 2):
                worst = 99.0
        rows.append(CaseRow(CaseId(cid), ExitStatus(1), ExitStatus(int(worst < 2)), Prose(f"{what} ({worst:.2f}s)"),
                            Prose("")))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in UNPARSED4:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



# ─── the claims the hooks' prose makes, each with its case ────────────────────

def claims_fixtures() -> None:
    (S / "heavylib.sh").write_text("runit() { cargo test 2>&1 | tail -5; }\n")
    (S / "job.sh").write_text("sleep 9 &\n")
    (S / "self_source.sh").write_text(f"source {S}/self_source.sh\necho x > /tmp/ok.txt\n")
    (S / "env.sh").write_text(f"OUT={T}\n")
    (T / "tools" / "empty").mkdir(parents=True, exist_ok=True)


XLOG = "/tmp/guard-claims-x.log"
CLAIMS_QUIET = [
    ("Q01", bash(CommandLine("sudo cargo test"), Q), 2), ("Q02", bash(CommandLine("stdbuf -oL cargo test"), Q), 2),
    ("Q03", bash(CommandLine("setsid cargo test"), Q), 2), ("Q04", bash(CommandLine("uv run pytest tests/"), Q), 2),
    ("Q05", bash(CommandLine("uv run --with x python -m pytest tests/"), Q), 2),
    ("Q06", bash(CommandLine("ionice -c3 cargo test"), Q), 2),
    ("Q07", bash(CommandLine("chrt -i 0 cargo test"), Q), 2), ("Q08", bash(CommandLine("doas cargo test"), Q), 2),
    ("Q09", bash(CommandLine("unbuffer cargo test"), Q), 2),
    ("Q10", bash(CommandLine("/usr/bin/time -v cargo test"), Q), 2),
    ("Q11", bash(CommandLine("command cargo test"), Q), 2), ("Q12", bash(CommandLine("exec cargo test"), Q), 2),
    ("Q13", bash(CommandLine("strace -f -o /tmp/s.txt cargo test"), Q), 2),
    ("Q14", bash(CommandLine("flock /tmp/l cargo test"), Q), 2),
    ("Q15", bash(CommandLine("flock /tmp/l -c 'cargo test'"), Q), 2),
    ("Q16", bash(CommandLine("watch -n 60 cargo test"), Q), 2),
    ("Q17", bash(CommandLine("systemd-run --user --scope cargo test"), Q), 2),
    ("Q18", bash(CommandLine("cat <(cargo test)"), Q), 2),
    ("Q19", bash(CommandLine("echo `cargo test`"), Q), 2),
    ("Q20", bash(CommandLine("python3 -c \"import os; os.popen('cargo test').read()\""), Q), 2),
    ("Q21", bash(CommandLine("python3 -c \"import subprocess; subprocess.getoutput('cargo test')\""), Q), 2),
    ("Q23b", bash(CommandLine("fish -c 'cd rust && cargo test'"), Q), 2),
    ("Q25b", bash(CommandLine(f"bash {S}/tailpipe.sh"), Q), 2),
    ("Q26", bash(CommandLine(f"cd {Q} && taskset -c 0-19 cargo test > {S}/name.log 2>&1; "
                             f"echo \"EXIT=$?\" >> {S}/name.log"), Q), 0),
    ("Q27", bash(CommandLine(f"echo \"start $(date +%T)\"; taskset -c 0-19 cargo test > {XLOG} 2>&1"), Q), 2),
    ("Q27b", bash(CommandLine(f"S=$(date +%s); taskset -c 0-19 cargo test > {XLOG} 2>&1"), Q), 0),
    ("Q30", bash(CommandLine("screen -dmS w cargo test"), Q), 2),
    ("Q31b", bash(CommandLine("tmux new -d -s build 'cargo test'"), Q), 2),
    ("Q33", bash(CommandLine(f"source {S}/heavylib.sh; runit"), Q), 2),
    ("Q35", bash(CommandLine(f"{{ exec > {XLOG} 2>&1; }}; taskset -c 0-19 cargo test"), Q), 0),
    ("Q35b", bash(CommandLine(f"exec > {XLOG} 2>&1; taskset -c 0-19 cargo test"), Q), 0),
    ("Q36", bash(CommandLine("echo \"$(cat <<'EOF'\nx\nEOF)\" && cargo test"), Q), 2),
    ("Q37", bash(CommandLine("env -C rust cargo test"), Q), 2),
    ("Q38", bash(CommandLine(f"{{ echo x; }} > {XLOG} 2>&1; taskset -c 0-19 cargo test"), Q), 2),
    ("Q39", bash(CommandLine(f"source {S}/self_source.sh"), Q), 0),
]
CLAIMS_LIVE = [
    ("W01.2", bash(CommandLine("wget https://example.invalid/x"), T), 2),
    ("W02", bash(CommandLine("unzip /tmp/a.zip"), T), 2),
    ("W03.2", bash(CommandLine("cargo add serde"), T), 2), ("W04", bash(CommandLine("rmdir tools/empty"), T), 2),
    ("W05.2", bash(CommandLine("pushd /tmp && popd && echo x > note.txt"), T), 2),
    ("W06", bash(CommandLine(f"pushd {T} && echo x > note.txt"), S), 2),
    ("W07.2", bash(CommandLine("python3 - <<< \"open('CHANGELOG.md','w').write('x')\""), T), 2),
    ("W08", bash(CommandLine("python3 - <<'PY'\np: str = 'CHANGELOG.md'\nopen(p, 'w').write('x')\nPY"), T), 2),
    ("W09.2", bash(CommandLine(f"python3 - <<'PY'\nimport os\nopen(os.path.join('{T}', 'CHANGELOG.md'), 'w')\nPY"), S),
     2),
    ("W10.2", bash(CommandLine("python3 - <<'PY'\nfrom pathlib import Path\n"
                               "(Path.cwd() / 'CHANGELOG.md').write_text('x')\nPY"), T), 2),
    ("W11.2", bash(CommandLine("python3 - <<'PY'\nfrom pathlib import Path\n"
                               f"(Path('{T}/tools/x.py').parent / 'y.py').write_text('x')\nPY"), S), 2),
    ("W12.2", bash(CommandLine("python3 - <<'PY'\nfrom pathlib import Path\n"
                               "Path('CHANGELOG.md').resolve().write_text('x')\nPY"), T), 2),
    ("W13.2", bash(CommandLine(f"python3 - <<'PY'\nfrom pathlib import Path\np = Path('{T}/CHANGELOG.md')\n"
                               "p.replace('/tmp/x')\nPY"), S), 2),
    ("W14", bash(CommandLine("python3 - <<'PY'\nimport fileinput\n"
                             "for l in fileinput.input('CHANGELOG.md', inplace=True):\n    print(l, end='')\nPY"), T),
     2),
    ("W15.2", bash(CommandLine("python3 -c \"import shutil; shutil.copy('/tmp/a', 'CHANGELOG.md')\""), T), 2),
    ("W16.2", bash(CommandLine("python3 -c \"import os; os.remove('CHANGELOG.md')\""), T), 2),
    ("W17.2", bash(CommandLine("ruby -e \"File.write('CHANGELOG.md', 'x')\""), T), 2),
    ("W18.2", bash(CommandLine("node -e \"require('fs').writeFileSync('CHANGELOG.md', 'x')\""), T), 2),
    ("W19", bash(CommandLine("perl -e 'open(my $f, \">\", \"CHANGELOG.md\")'"), T), 2),
    ("W20b", bash(CommandLine("tar -xf /tmp/a.tar -C tools"), T), 2),
    ("W21.2", bash(CommandLine(f"source {S}/lib.sh; w"), S), 2),
    ("W22.2", bash(CommandLine(f". {S}/env.sh; echo x > \"$OUT/n.txt\""), S), 2),
    ("W26b", bash(CommandLine(f"cd {T} && echo \"open"), S), 2),
    ("W27.2", bash(CommandLine(f"cd \"$(dirname {T}/tools/x.py)\" && echo x > n.txt"), S), 2),
    ("W28b", bash(CommandLine(f"find {T}/tools /tmp -maxdepth 0 -exec touch {{}} +"), S), 2),
    ("W29", bash(CommandLine("echo 'git status' >> .commands-to-run.sh"), T), 0),
    ("W30", bash(CommandLine("chmod +x .commands-to-run.sh"), T), 0),
    ("W31", bash(CommandLine(f"grep -c FAIL {XLOG}"), T), 0),
    ("W31b", bash(CommandLine(f"sed -n '1,20p' {XLOG}"), T), 0),
    ("W31c", bash(CommandLine(f"grep -n error {XLOG}"), T), 0),
    ("W32.2", edit("Write", PathText(f"{S}/draft.txt"), T), 0),
    ("W33", bash(CommandLine("pgrep -f 'cargo test'"), T, bg=False), 0),
    ("W38.2", bash(CommandLine(f"env -C {T}/tools sed -i s/a/b/ x.py"), S), 2),
    ("W39.2", bash(CommandLine(f"{{ L={S}/$(date +%s).log; }}; echo hi > \"$L\""), T), 0),
]
CLAIMS_LOOPS = [
    ("L01.2", bash(CommandLine("sleep 999 &|"), T), 2), ("L02", bash(CommandLine("screen -dmS w sleep 999"), T), 2),
    ("L03.2", bash(CommandLine("systemd-run --user sleep 999"), T), 2),
    ("L04", bash(CommandLine("daemonize /usr/bin/sleep 999"), T), 2),
    ("L05.2", bash(CommandLine("start-stop-daemon -S -b -x /usr/bin/sleep -- 999"), T), 2),
    ("L06.2", bash(CommandLine("echo 'sleep 9' | batch"), T), 2),
    ("L07b", bash(CommandLine("tmux new -d -s build 'sleep 999'"), T), 2),
    ("L09b", bash(CommandLine("( sleep 999 & wait ) &"), T), 2),
    ("L10.2", bash(CommandLine("sleep 998 & a=$!\nsleep 999 & b=$!\ntrap 'kill $a $b' EXIT\necho work"), T), 0),
    ("L10c", bash(CommandLine("sleep 998 & a=$!\nsleep 999 & b=$!\ntrap 'kill $a; kill $b' EXIT\necho work"), T), 0),
    ("L12.2", bash(CommandLine("tail -f /tmp/x.log | grep --line-buffered FAIL & p=$!\necho work\nwait \"$p\""), T), 2),
    ("L19.2", bash(CommandLine(f"bash {S}/watch.sh"), T), 2), ("L20", bash(CommandLine("sleep 999 & (wait)"), T), 2),
    ("L23.2", monitor(CommandLine("while ! gh run view 1 --json status | grep -q completed; do sleep 30; done"), T), 0),
    ("L24.2", monitor(CommandLine("while kill -0 12345 2>/dev/null; do sleep 5; done"), T), 0),
    ("L25.2", monitor(CommandLine("tail -f /tmp/x.log | grep --line-buffered FAIL"), T), 0),
    ("L26.2", bash(CommandLine("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep --line-buffered FAIL &\necho work"), T),
     0),
    ("L27.2", bash(CommandLine("sleep 1 & sleep 2 & wait"), T), 0),
    ("L28", bash(CommandLine("pgrep -f 'cargo test'"), T), 2),
    ("L31.2", bash(CommandLine("finish() { wait; }\nsleep 9 &\nfinish"), T), 0),
    ("L32.2", bash(CommandLine(f"source {S}/job.sh; wait $!"), T), 0),
]


def claims_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, event, want in CLAIMS_QUIET:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId("c" + cid), ExitStatus(want), rc,
                            Prose(event["tool_input"].get("command", "")[:50].replace("\n", " ")), err))
    for cid, event, want in CLAIMS_LOOPS:
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId("c" + cid), ExitStatus(want), rc,
                            Prose(event["tool_input"].get("command", "")[:50].replace("\n", " ")), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want in CLAIMS_LIVE:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId("c" + cid), ExitStatus(want), rc,
                                Prose(str(event["tool_input"])[:50].replace("\n", " ")), err))
        home = dict(HOOK_ENV, HOME=str(T.parent))  # `cd ~/live` names the live tree
        p = subprocess.run([str(GUARD)], input=json.dumps(bash(CommandLine("cd ~/live && echo \"open"), S)),
                           capture_output=True, text=True, check=False, timeout=60, env=home)
        rows.append(CaseRow(CaseId("cW26"), ExitStatus(2), ExitStatus(p.returncode),
                            Prose("cd ~/live, unreadable, a run live"), Prose(p.stderr)))
    finally:
        stop(live)
    return rows



# ─── hotfixes after the fourth round ─────────────────────────────────────────

HOT_QUIET = [  # (id, hook, event, want, what)
    ("H01.2", "guard", bash(CommandLine("grep -n taskset tools/x.py"), Q), 0, "a search for the word taskset"),
    ("H02.2", "guard", bash(CommandLine("rg -n taskset docs/"), Q), 0, "rg for taskset"),
    ("H03.2", "guard", bash(CommandLine("which taskset"), Q), 0, "which taskset"),
    ("H04.2", "guard", bash(CommandLine("taskset --help"), Q), 0, "taskset --help"),
    ("H05.2", "guard", bash(CommandLine("git log --oneline --grep taskset"), Q), 0, "git log --grep taskset"),
    ("H06.2", "guard", bash(CommandLine("grep -rn numactl -C 20 docs"), Q), 0, "grep for numactl with -C 20"),
    ("H07.2", "guard", bash(CommandLine("man taskset"), Q), 0, "man taskset"),
    ("H08.2", "guard", bash(CommandLine("sudo taskset -c 20 ls"), Q), 2, "taskset under sudo onto CPU 20"),
    ("H09.2", "guard", bash(CommandLine("env X=1 taskset 0xf00000 ls"), Q), 2, "taskset under env, a mask of 20-23"),
    ("H10.2", "guard", bash(CommandLine(f"cat > {S}/d.py <<'PY'\nimport subprocess\nsubprocess.run(['cargo', 'test'])\n"
                                        f"PY\npython3 {S}/d.py"), Q), 2, "a heredoc driver the call runs with python3"),
    ("H11", "guard", bash(CommandLine(f"cat > {S}/t.sh <<'SH'\ncargo test 2>&1 | tail -5\nSH\n"
                                      f"timeout 600 bash {S}/t.sh"), Q), 2, "a heredoc script run through timeout"),
    ("H12.2", "guard", bash(CommandLine(f"cat > {S}/t2.sh <<'SH'\ncargo test 2>&1 | tail -5\nSH\ntime bash {S}/t2.sh"),
                            Q), 2, "a heredoc script run through time"),
    ("H13.2", "loops", bash(CommandLine(f"cat > {S}/j.sh <<'SH'\nsleep 99 &\nSH\ntimeout 60 bash {S}/j.sh"), Q), 2,
     "a heredoc script with a job, run through timeout"),
    ("H14.2", "guard", bash(CommandLine("numactl -C 0-3 cargo test"), Q), 2, "a heavy run under numactl, not taskset"),
]
HOT_LIVE = [
    ("H20", bash(CommandLine(f"cat > {S}/r.py <<'PY'\nprint(1)\nPY\npython3 {S}/r.py"), T), 0,
     "a heredoc script run with python3"),
    ("H21", bash(CommandLine(f"cat > {S}/r.sh <<'SH'\necho hi > /tmp/ok.txt\nSH\ntimeout 60 bash {S}/r.sh"), T), 0,
     "a heredoc script run through timeout"),
    ("H22", bash(CommandLine(f"cat > {S}/w2.py <<'PY'\nopen('{S}/ok2.txt', 'w').write('x')\nPY\npython3 {S}/w2.py"), T),
     0, "a heredoc driver writing elsewhere"),
    ("H23", bash(CommandLine(f"cat > {S}/e.sh <<'SH'\necho safe\nSH\n"
                             f"sed -i 's|echo safe|echo x > {T}/CHANGELOG.md|' {S}/e.sh 2>/dev/null\nbash {S}/e.sh"),
                 S), 2, "a heredoc script edited, with a redirect, before it runs"),
    ("H24", bash(CommandLine(f"cat > {S}/m.sh <<'SH'\necho safe\nSH\ncp /tmp/other.sh {S}/m.sh\nbash {S}/m.sh"), T), 2,
     "a heredoc script replaced by cp before it runs"),
    ("H25", bash(CommandLine(f"cat > {S}/s5.sh <<'SH'\necho safe\nSH\n"
                             f"timeout 5 sed -i 's|echo safe|echo x > CHANGELOG.md|' {S}/s5.sh\nbash {S}/s5.sh"), T), 2,
     "a heredoc script edited through a wrapper"),
    ("H26", bash(CommandLine(f"cat > {S}/a.sh <<'SH'\necho safe\nSH\ngawk -i inplace '{{print}}' {S}/a.sh\n"
                             f"bash {S}/a.sh"), T), 2, "a heredoc script edited by gawk in place"),
]


def hotfix_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, which, event, want, what in HOT_QUIET:
        rc, err = hook(GUARD if which == "guard" else LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in HOT_LIVE:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



# ─── the tool's shell is zsh: job control by dialect ─────────────────────────

ZSH5 = [
    ("Z30", bash(CommandLine("bash -c 'sleep 9 & wait -n'"), T), 0, "bash: wait -n reaps the one job"),
    ("Z31", bash(CommandLine("bash -c 'sleep 9 & sleep 8 & wait -n; wait -n'"), T), 0,
     "bash: two wait -n reap two jobs"),
    ("Z32", bash(CommandLine("bash -c 'sleep 9 & sleep 8 & wait -p got'"), T), 0,
     "bash: wait -p VAR alone reaps every job"),
    ("Z33", bash(CommandLine("bash -c 'for d in a b c; do (cd \"$d\" && git status --short) & done; wait -n; wait -n; "
                             "wait -n'"), T), 0, "bash: three jobs, three wait -n"),
    ("Z34", bash(CommandLine("bash -c 'tail -f /tmp/a | grep x & wait %1'"), T), 0,
     "bash: wait %1 reaps every process of the job"),
    ("Z35", bash(CommandLine("tail -f /tmp/a | grep x & wait %%"), T), 0, "zsh: wait %% reaps the current job"),
    ("Z36", bash(CommandLine("bash -c 'sleep 999 & sleep 998 & kill $(jobs -p)'"), T), 0, "bash: kill $(jobs -p)"),
    ("Z37", bash(CommandLine("bash -c 'sleep 1 & sleep 2 & wait %1 %2'"), T), 0,
     "bash: two job specs, numbered by start"),
    ("Z38", bash(CommandLine("bash -c 'sleep 0.2 & sleep 999 & wait %1; wait %1'"), T), 2,
     "bash: %1 twice leaves the second job"),
    ("Z39", bash(CommandLine("sleep 9 &! echo x"), T), 2, "zsh &! disowns the job"),
    ("Z40", bash(CommandLine("sleep 9 &| echo x"), T), 2, "zsh &| disowns the job"),
    ("Z41", bash(CommandLine("trap 'trap \"\" TERM; kill 0' EXIT\ntail -f /tmp/x.log | grep --line-buffered FAIL &\n"
                             "echo work"), T), 0, "the advised trap: the group stopped, the call's status kept"),
    ("Z42", bash(CommandLine("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep x &\ntrap 'rm -f /tmp/t' EXIT"), T), 2,
     "an EXIT trap replaced by one that kills nothing"),
    ("Z43", bash(CommandLine("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep x &\ntrap - EXIT"), T), 2,
     "an EXIT trap reset"),
    ("Z44", bash(CommandLine("sleep 998 & a=$!\nsleep 999 & b=$!\ntrap 'kill $a; wait $a' EXIT\necho work"), T), 2,
     "a trap's kill and wait of one pid count once"),
    ("Z45", bash(CommandLine("pids=()\nsleep 998 & pids+=($!)\nsleep 999 & pids+=($!)\nkill \"${pids[@]}\""), T), 0,
     "kill over an array of pids"),
    ("Z46", bash(CommandLine("( git fetch -q & wait ) & wait"), T), 0, "a wait inside a background subshell"),
    ("Z47", bash(CommandLine("sleep 999 & p=$!; kill -0 $p && echo alive"), T), 2, "kill -0 stops nothing"),
    ("Z48", bash(CommandLine("sleep 2 & echo x | wait"), T), 0, "zsh runs a pipeline's last wait in the shell"),
    ("Z49", bash(CommandLine("bash -c 'sleep 2 & echo x | wait'"), T), 2, "bash runs a pipeline's wait in a subshell"),
    ("Z39b", bash(CommandLine("sleep 9 &!\nwait"), T), 2, "zsh: a wait does not reap a disowned job"),
    ("Z50", bash(CommandLine("{S}/bashjob.sh"), T), 0, "a bash script by its shebang: wait -n reaps"),
    ("Z51", bash(CommandLine("sh {S}/shjob.sh"), T), 2, "an sh script: wait -n is an error"),
]


def zsh_cases() -> list[CaseRow]:
    (S / "bashjob.sh").write_text("#!/usr/bin/env bash\nsleep 9 & wait -n\n")
    (S / "shjob.sh").write_text("sleep 9 & wait -n\n")
    rows: list[CaseRow] = []
    for cid, event, want, what in ZSH5:
        command = event["tool_input"].get("command", CommandLine(""))
        event["tool_input"]["command"] = CommandLine(command.replace("{S}", str(S)))
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    return rows



# ─── the fifth round: stdin order, group files, git options ──────────────────

R5_LIVE = [
    ("R5a", bash(CommandLine("bash <<'EOF'\npython3 - <<'PY'\nopen('CHANGELOG.md', 'w').write('x')\nPY\nEOF"), T), 2,
     "python3 - inside a heredoc-fed bash reads its own heredoc"),
    ("R5b", bash(CommandLine("bash <<'EOF'\necho \"open('CHANGELOG.md', 'w').write('x')\" | python3 -\nEOF"), T), 2,
     "a pipe inside a heredoc-fed bash feeds python3 -"),
    ("R5c", bash(CommandLine("python3 - <<'A' <<'B'\nopen('CHANGELOG.md', 'w')\nA\nprint(1)\nB"), T), 2,
     "zsh reads both heredocs, the first writing"),
    ("R5c2", bash(CommandLine("bash -c \"python3 - <<'A' <<'B'\nopen('CHANGELOG.md', 'w')\nA\nprint(1)\nB\""), T), 0,
     "bash reads only the last heredoc"),
    ("R5d", bash(CommandLine(f"{{ echo 'echo x > CHANGELOG.md'; echo 'echo done'; }} > {S}/g.sh; bash {S}/g.sh"), T), 2,
     "a script built by a group, its first line writing"),
    ("R5e", bash(CommandLine(f"(echo 'echo x > CHANGELOG.md'; echo 'echo done') > {S}/s.sh; bash {S}/s.sh"), T), 2,
     "a script built by a subshell"),
    ("R5f", bash(CommandLine(f"{{ echo 'echo x > /tmp/ok.txt'; echo 'echo done'; }} > {S}/g2.sh; bash {S}/g2.sh"), T),
     0, "a script built by a group, writing elsewhere"),
    ("R5g", bash(CommandLine("git clone --depth 1 https://example.invalid/x /tmp/x-clone"), T), 0,
     "clone --depth N into /tmp"),
    ("R5h", bash(CommandLine("git clone -b main . /tmp/x-clone2"), T), 0, "clone -b BRANCH . DIR"),
    ("R5i", bash(CommandLine("git init -b main /tmp/x-fresh"), T), 0, "init -b BRANCH DIR"),
    ("R5j", bash(CommandLine("git clone --depth 1 /tmp/src sub/clone"), T), 2, "clone --depth into the tree"),
    ("R5k", bash(CommandLine("git clone -o upstream /tmp/src sub/clone"), T), 2, "clone -o NAME into the tree"),
    ("R5l", bash(CommandLine("git init --template /tmp/tpl"), T), 2, "init --template re-initialises the tree"),
    ("V01", bash(CommandLine(f"f() {{ sed -i s/a/b/ \"$@\"; }}; f {T}/CHANGELOG.md"), S), 2, "a function's \"$@\""),
    ("V02.2", bash(CommandLine(f"bash {S}/fwd.sh {T}/CHANGELOG.md"), S), 2, "a script forwarding \"$@\""),
    ("V03", bash(CommandLine(f"bash {S}/shifted.sh a {T}/CHANGELOG.md"), S), 2, "a script that shifts, then writes $1"),
    ("V04", bash(CommandLine(f"f() {{ for g; do echo x > \"$g\"; done; }}; f /tmp/ok.txt {T}/CHANGELOG.md"), S), 2,
     "for with no in: every positional parameter"),
    ("V05", bash(CommandLine(f"set -- a {T}/CHANGELOG.md; echo x > \"$2\""), S), 2, "set -- rebinds the parameters"),
    ("V06.2", bash(CommandLine(f"ROOT={T}/; echo x > \"${{ROOT%/}}/note.txt\""), S), 2,
     "a trimmed variable keeps its value"),
    ("V07.2", bash(CommandLine(f"D={T}; rm -f \"${{D:?}}/note.txt\""), S), 2, "${D:?} keeps its value"),
    ("V08.2", bash(CommandLine(f"cd \"{T}/$(printf tools)\" && echo x > n.txt"), S), 2,
     "cd to a partly known directory"),
    ("V09.2", bash(CommandLine(f"source {S}/lib1.sh {T}/CHANGELOG.md"), S), 2, "source with arguments"),
    ("V10.2", bash(CommandLine(f"cd \"$(ls -d {T}/too*)\" && echo x > n.txt"), S), 2,
     "cd to an unknown directory the call names"),
    ("V11.2", bash(CommandLine("f() { shift; echo x > \"$1\"; }; f CHANGELOG.md /tmp/ok.txt"), T), 0,
     "shift moves $2 into $1"),
    ("V12.2", bash(CommandLine(f"X={T}:/tmp/other; echo x > \"${{X%%:*}}/n.txt\""), S), 2,
     "a %% trim lands in the tree"),
    ("V13.2", bash(CommandLine("D=/tmp; rm -f \"${D:?}/note.txt\""), T), 0, "${D:?} out of the tree"),
    ("V14.2", bash(CommandLine(f"cd \"/tmp/$(printf x)\" && echo x > n.txt; cat {T}/CHANGELOG.md"), S), 0,
     "a partly known cd out of the tree, the tree only read"),
    ("J3", bash(CommandLine("trap 'echo \"done' EXIT; echo x"), T), 2, "an unreadable trap text while a run is live"),
    ("K1", bash(CommandLine(f"X=/tmp/ok.txt bash {S}/w_env.sh; X=CHANGELOG.md bash {S}/w_env.sh"), T), 2,
     "one script run twice with different assignments"),
    ("L1", bash(CommandLine(f"printf 'w() {{ echo x > \"$1\"; }}\\n' > {S}/lib2.sh; source {S}/lib2.sh; "
                            "w CHANGELOG.md"), T), 2, "a file the call writes, then sources"),
    ("N1", bash(CommandLine(f"echo \"a|{T}/CHANGELOG.md"), S), 2, "an unreadable call naming the tree after a |"),
    ("T1", bash(CommandLine("python3 - <<'PY'\nimport subprocess\ndef run(d):\n"
                            "    subprocess.run(['git', 'status'], cwd=d)\nrun('/tmp/siderepo')\nPY"), T), 0,
     "a read-only command with a runtime cwd="),
    ("T2", bash(CommandLine("python3 - <<'PY'\nimport subprocess\ndef run(d):\n"
                            "    subprocess.run(['touch', 'x'], cwd=d)\nrun('/tmp/siderepo')\nPY"), T), 2,
     "a writing command with a runtime cwd="),
]
R5_QUIET = [
    ("R5m", bash(CommandLine("bash <<'EOF'\npython3 - <<'PY'\nimport subprocess\nsubprocess.run(['cargo', 'test'])\n"
                             "PY\nEOF"), Q), 2, "a heavy driver inside a heredoc-fed bash"),
    ("J1.2", bash(CommandLine(f"source {S}/fish.fish; cargo test"), Q), 2,
     "an unreadable sourced file hides nothing after it"),
    ("J2", bash(CommandLine("trap 'echo \"done' EXIT; cargo test"), Q), 2,
     "an unreadable trap text hides nothing after it"),
    ("K2", bash(CommandLine(f"taskset -c 0-19 bash {S}/r_heavy.sh > /tmp/x.log 2>&1; bash {S}/r_heavy.sh"), Q), 2,
     "one heavy script run blessed, then bare"),
    ("M1", bash(CommandLine("{ R=$(taskset -c 0-19 cargo test); } > /tmp/x.log 2>&1"), Q), 2,
     "a heavy run captured into a variable"),
    ("M2", bash(CommandLine("exec > /tmp/x.log 2>&1; R=$(taskset -c 0-19 cargo test); echo \"EXIT=$?\""), Q), 2,
     "a capture after exec >log"),
]


def round5_cases() -> list[CaseRow]:
    (S / "fwd.sh").write_text('sed -i s/a/b/ "$@"\n')
    (S / "shifted.sh").write_text('shift\necho x > "$1"\n')
    (S / "lib1.sh").write_text('echo x > "$1"\n')
    (S / "fish.fish").write_text("for f in *.py; echo $f; end\n")
    (S / "w_env.sh").write_text('echo x > "$X"\n')
    (S / "r_heavy.sh").write_text("cargo test\n")
    rows: list[CaseRow] = []
    for cid, event, want, what in R5_QUIET:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in R5_LIVE:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



ROBUST5 = [  # (id, hook, event, want)
    ("RB1", "loops", monitor(CommandLine("while pgrep -f cargo >/dev/null; do sleep 5; done; echo \ud800"), T), 2),
    ("RB2", "loops", bash(CommandLine("while pgrep -f cargo >/dev/null; do sleep 5; done; echo \ud800"), T), 2),
    ("RB3", "loops", monitor(CommandLine("while pgrep -f \"$X\"$'\\0' >/dev/null; do sleep 5; done"), T), 2),
    ("RB4", "guard", bash(CommandLine("\ud800/python <<''\nimport"), Q), 0),
    ("RB5", "guard", HookEvent(bash(CommandLine("cargo test"), Q), hook_event_name="PostToolUseFailure"), 0),
    ("RB6", "loops", HookEvent(bash(CommandLine("while true; do sleep 1; done"), T), hook_event_name="PostToolUse"), 0),
    ("RB7", "loops", HookEvent(bash(CommandLine("while true; do sleep 1; done"), T), hook_event_name="PreToolUse"), 2),
    ("RB8", "loops", monitor(CommandLine("P=cargo; while pgrep -f \"$P\" >/dev/null; do sleep 5; done"), T), 2),
    ("RB9", "loops", monitor(CommandLine("while pgrep -f \"$UNSET_PATTERN_X\" >/dev/null; do sleep 5; done"), T), 2),
    ("GR1", "loops", monitor(CommandLine("while ps aux | grep cargo | grep -v grep > /dev/null; do sleep 5; done"), T),
     0),
    ("GR2", "loops", monitor(CommandLine("while ps aux | grep -v grep | grep -q 'cargo test'; do sleep 10; done"), T),
     0),
    ("GR3", "loops", monitor(CommandLine("while ps aux | grep -q 'x+y'; do sleep 5; done"), T), 2),
    ("GR4", "loops", monitor(CommandLine("while ps aux | grep -q 'run(x)'; do sleep 5; done"), T), 2),
    ("GR5", "loops", monitor(CommandLine("while ps aux | grep -Eq 'x+y'; do sleep 5; done"), T), 0),
    ("GR6", "loops", monitor(CommandLine("while pgrep -if '[c]argo test' >/dev/null; do sleep 5; done; "
                                         "echo Cargo test finished"), T), 2),
    ("GR7", "loops", monitor(CommandLine("while pgrep -f -- '-x' >/dev/null; do sleep 5; done"), T), 2),
    ("GR8", "loops", monitor(CommandLine("while pgrep -f '[u]nset -f' >/dev/null; do sleep 5; done"), T), 2),
    ("GR9", "loops", monitor(CommandLine("while pgrep -f '[/]home/user/[.]claude' >/dev/null; do sleep 5; done"), T),
     0),
    ("GR10", "loops", monitor(CommandLine("while ps aux | grep -q 'car''go test'; do sleep 5; done"), T), 2),
    ("GR11", "loops", monitor(CommandLine("while ps aux | grep -q '[c]argo test'; do sleep 5; done"), T), 0),
    ("GR12", "loops", monitor(CommandLine("while ps aux | grep -v grep > /dev/null; do sleep 5; done"), T), 0),
]


def robust5_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, which, event, want in ROBUST5:
        rc, err = hook(GUARD if which == "guard" else LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc,
                            Prose(ascii(event.get("tool_input", {}).get("command", ""))[:40]), Prose(ascii(err))))
    return rows



PY5_LIVE = [
    ("PY1", bash(CommandLine("python3 -c \"m = 'w'; open('CHANGELOG.md', m).write('x')\""), T), 2,
     "a mode held in a name"),
    ("PY2", bash(CommandLine("python3 -c \"open(file='CHANGELOG.md', mode='w')\""), T), 2, "open with keywords"),
    ("PY3", bash(CommandLine("python3 -c \"import tarfile; tarfile.open('CHANGELOG.tar', 'w')\""), T), 2,
     "tarfile.open for writing"),
    ("PY4", bash(CommandLine("python3 -c \"import tarfile; tarfile.open('/tmp/a.tar').extractall('tools')\""), T), 2,
     "extractall into the tree"),
    ("PY5", bash(CommandLine("python3 -c \"import os; os.utime('CHANGELOG.md')\""), T), 2, "os.utime"),
    ("PY6", bash(CommandLine("python3 -c \"import shutil; shutil.unpack_archive('/tmp/a.zip', 'tools')\""), T), 2,
     "unpack_archive"),
    ("PY7", bash(CommandLine("python3 - <<EOF\nn = $N\nopen('CHANGELOG.md', 'w').write('x')\nEOF"), T), 2,
     "Python with a shell expansion the reader cannot see"),
    ("PY8", bash(CommandLine("python3 -c \"import subprocess; run = subprocess.run; run(['git', 'stash'])\""), T), 2,
     "a subprocess function bound to a name"),
    ("PY9", bash(CommandLine("python3 -c \"open('/tmp/r.txt').read()\""), T), 0, "open to read"),
    ("PY10", bash(CommandLine("python3 -c \"import tarfile; tarfile.open('/tmp/a.tar').extractall('/tmp/out')\""), T),
     0, "extractall elsewhere"),
]
PY5_QUIET = [
    ("PY11", bash(CommandLine("python3 - --full <<'PY'\nimport subprocess, sys\nif '--full' in sys.argv:\n"
                              "    cmd = ['cargo', 'test']\nelse:\n    cmd = ['ls']\nsubprocess.run(cmd)\nPY"), Q), 2,
     "a heavy command behind an if"),
    ("PY12", bash(CommandLine("taskset -c 0-19 python3 -c \"import subprocess; "
                              "subprocess.run(['taskset', '-c', '0-23', 'ls'])\" > /tmp/x.log 2>&1"), Q), 2,
     "Python starting a command on CPUs 20-23"),
    ("PY13", bash(CommandLine("taskset -c 0-19 python3 -c \"import os; os.system('cargo test 2>&1 | "
                              "tail -5')\" > /tmp/x.log 2>&1"), Q), 2, "Python piping a heavy run into tail"),
    ("PY14", bash(CommandLine("taskset -c 0-19 python3 -c \"import subprocess; "
                              "subprocess.run(['cargo', 'test'])\" > /tmp/x.log 2>&1"),
                  Q), 0, "a blessed Python driver"),
]


def py5_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, event, want, what in PY5_QUIET:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in PY5_LIVE:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



SMALL5_LIVE = [
    ("EW1", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "EnterWorktree",
                       "tool_input": {"name": WorktreeName("x")}, "cwd": PathText(str(T))}),
     2, "EnterWorktree makes a worktree inside the live tree"),
    ("EW3", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "EnterWorktree",
                       "tool_input": {"path": PathText(str(W))},
                       "cwd": PathText(str(T))}), 0, "EnterWorktree into a worktree that exists writes nothing"),
    ("AG1", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "Agent",
                       "tool_input": {"description": Prose("d"), "prompt": Prose("p"), "isolation": "worktree"},
                       "cwd": PathText(str(T))}), 2, "an agent in a worktree makes one inside the live tree"),
    ("AG2", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "Agent",
                       "tool_input": {"description": Prose("d"), "prompt": Prose("p")},
                       "cwd": PathText(str(T))}), 0, "an agent sharing the session's tree writes nothing by itself"),
    ("XW1", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "ExitWorktree", "tool_input": {"action": "remove"},
                       "cwd": PathText(str(T))}), 2, "ExitWorktree removes a worktree a run is live in"),
    ("XW2", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "ExitWorktree", "tool_input": {"action": "keep"},
                       "cwd": PathText(str(T))}), 0, "ExitWorktree keep writes nothing"),
    ("XW3", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "ExitWorktree", "tool_input": {"action": "remove"},
                       "cwd": PathText(str(Q))}), 0, "ExitWorktree remove with no run live there"),
    ("CM1", bash(CommandLine("mkdir -p cpp/build && cd cpp/build && cmake .."), T), 2, "a cmake configure without -B"),
    ("CM4", bash(CommandLine("cmake cpp"), T), 2, "cmake with a source directory only"),
    ("CM6", bash(CommandLine("cmake --version"), T), 0, "cmake --version"),
    ("CM7", bash(CommandLine("cmake -E echo x"), T), 0, "cmake -E"),
    ("X28", bash(CommandLine("cargo --color never clean"), T), 2, "a cargo global option before clean"),
    ("X29", bash(CommandLine("cargo --config net.offline=true update"), T), 2, "cargo --config before update"),
    ("QP1", bash(CommandLine("perl -e 'system(\"git stash\")'"), T), 2, "perl system()"),
    ("QP2", bash(CommandLine("ruby -e \"File.open('CHANGELOG.md', 'w')\""), T), 2, "ruby File.open for writing"),
    ("QP3", bash(CommandLine("node -e \"require('fs').createWriteStream('CHANGELOG.md')\""), T), 2,
     "node createWriteStream"),
    ("QP4", bash(CommandLine("node -e \"require('child_process').execSync('git stash')\""), T), 2, "node execSync"),
    ("QP5", bash(CommandLine("perl -e 'print \"system\"'"), T), 0, "a word inside a perl string"),
    ("SF1", bash(CommandLine("git submodule foreach 'git clean -fdx'"), T), 2, "git submodule foreach"),
]
SMALL5_QUIET = [
    ("EW2", HookEvent({"hook_event_name": "PreToolUse", "tool_name": "EnterWorktree",
                       "tool_input": {"name": WorktreeName("x")}, "cwd": PathText(str(Q))}),
     0, "EnterWorktree with no run live"),
    ("CG1", bash(CommandLine("bash probes/cpu_getconf.sh"), Q), 2, "a repository script counting the machine's CPUs"),
]


def small5_cases() -> list[CaseRow]:
    (Q / "probes" / "cpu_getconf.sh").write_text('taskset -c "0-$(($(getconf _NPROCESSORS_ONLN) - 1))" ls\n')
    rows: list[CaseRow] = []
    for cid, event, want, what in SMALL5_QUIET:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in SMALL5_LIVE:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows



R6_LIVE = [  # the sixth round: contexts, dialects, budgets, Python pending code
    ("CX1", bash(CommandLine(f"(cd {T} && sed -i s/a/b/ CHANGELOG.md) &\nwait"), S), 2,
     "cd inside a background subshell"),
    ("CX2", bash(CommandLine(f"(cd {T} && sed -i s/a/b/ CHANGELOG.md) | cat"), S), 2, "cd inside a piped subshell"),
    ("CX3", bash(CommandLine(f"x=$(cd {T} && git stash)"), S), 2, "cd inside a substitution"),
    ("CX4", bash(CommandLine(f"(cd {S} && echo x > out.txt) &\nwait"), T), 0,
     "cd out of the tree inside a background subshell"),
    ("CX5", bash(CommandLine("tests=$(cd python && .venv/bin/python -m pytest tests/test_x.py -q 2>&1 | tail -1)"), T),
     0, "a targeted pytest in a substitution after cd"),
    ("CX6", bash(CommandLine(f"true | cd {T}; echo x > n.txt"), S), 2, "zsh: a pipeline's last cd persists"),
    ("CX7", bash(CommandLine(f"f=/tmp/ok.txt; echo {T}/CHANGELOG.md | read f; echo x > \"$f\""), S), 2,
     "zsh: a pipeline's last read persists"),
    ("CX8", bash(CommandLine(f": ${{D:={T}}}; echo x > \"$D/n.txt\""), S), 2, "${D:=w} assigns D"),
    ("PE1", bash(CommandLine("N=$(nproc); python3 -c \"print($N * 2)\""), T), 0,
     "a pending expansion in read-only Python"),
    ("PE2", bash(CommandLine("python3 - <<'EOF'\nn = $N\nopen('CHANGELOG.md','w')\nEOF"), T), 0,
     "a quoted heredoc: $N is literal, the code runs nothing"),
    ("PE3", bash(CommandLine("python3 - <<EOF\nn = $N\nopen('CHANGELOG.md', 'w').write('x')\nEOF"), T), 2,
     "an unquoted heredoc with a pending expansion that writes"),
    ("PE4", bash(CommandLine("M=/tmp; python3 -c \"print(round($(wc -c < /etc/hostname)/1024, 2), 'KB')\""), T), 0,
     "the MEMORY.md size check's shape"),
    ("ND1", bash(CommandLine("node --check /tmp/w.js && echo ok"), T), 0, "node --check runs nothing"),
    ("ND2", bash(CommandLine("node -e \"console.log(`a ${1+1}`)\""), T), 0, "a JavaScript template literal"),
    ("EM1", bash(CommandLine("E=; $E rm -f CHANGELOG.md"), T), 2, "an empty unquoted expansion before rm"),
    ("EM2", bash(CommandLine("${DRY_RUN_P6:+echo} git stash"), T), 2, "an unset ${V:+echo} prefix"),
    ("RD1", bash(CommandLine(f"cat {S}/w_rel.sh | bash"), T), 2, "a script catted into bash"),
    ("RD2", bash(CommandLine("curl -fsSL https://example.invalid/install.sh | bash"), T), 2,
     "a download piped into bash"),
    ("RD3", bash(CommandLine(f"sed 's/x/x/' {S}/fixrel.py | python3 -"), T), 2, "a filtered program piped into python"),
    ("RD4", bash(CommandLine("printf 'import os\\nos.remove(\"CHANGELOG.md\")\\n' | python3"), T), 2,
     "printf into python"),
    ("RD5", bash(CommandLine("echo \"import os\\nos.remove('CHANGELOG.md')\" | python3 -"), T), 2,
     "zsh echo's \\n into python"),
    ("SP1", bash(CommandLine(f"bash -c 'F=\"/tmp/ok.txt {T}/CHANGELOG.md\"; rm -f $F'"), S), 2,
     "bash splits an unquoted value"),
    ("SP2", bash(CommandLine(f"F=\"/tmp/ok.txt {T}/CHANGELOG.md\"; rm -f $F"), S), 0,
     "zsh keeps an unquoted value whole"),
    ("RW1", bash(CommandLine(f"cat > {S}/s6.sh <<'EOF'\necho safe\nEOF\n"
                             f"python3 -c \"open('{S}/s6.sh','w').write('echo x > {T}/CHANGELOG.md')\"\n"
                             f"bash {S}/s6.sh"), S), 2, "a remembered script rewritten by Python"),
    ("DP1", bash(CommandLine(f"bash {S}/n1.sh"), S), 2, "a write four scripts deep, past the reading depth"),
    ("CP1", bash(CommandLine(f"sed -i 's|echo safe|echo x > {T}/CHANGELOG.md|' {S}/e1.sh && bash {S}/e1.sh"), S), 2,
     "a disk script edited in place, then run"),
    ("CP2", bash(CommandLine(f"cp {BASE}/p6-other.sh {S}/e2.sh && bash {S}/e2.sh"), T), 2,
     "a disk script replaced by cp, then run"),
    ("RE1", bash(CommandLine("python3 - <<'PY'\ncmd = ['x']" + " + ['y']" * 1100 + "\nopen('CHANGELOG.md', 'w')\nPY"),
                 T), 2, "Python too deep for the reader"),
    ("TG1", bash(CommandLine("python3 -c \"import tarfile; tarfile.open('CHANGELOG.tar.gz', 'w:gz')\""), T), 2,
     "tarfile 'w:gz'"),
    ("MA1", bash(CommandLine("python3 -c \"import shutil; shutil.make_archive('CHANGELOG', 'zip', '/tmp')\""), T), 2,
     "shutil.make_archive"),
    ("XT1", bash(CommandLine("python3 -c \"import tarfile; t = tarfile.open('/tmp/a.tar'); "
                             f"t.extract('m', '{T}/tools')\""), S), 2, "extract's path argument"),
    ("BY1", bash(CommandLine("python3 -c \"import zipfile, io; "
                             "zipfile.ZipFile(io.BytesIO(), 'w').writestr('a', 'b')\""), T), 0, "a zip in memory"),
    ("OE1", bash(CommandLine("python3 -c \"import os; os.execvp('git', ['git', 'stash'])\""), T), 2, "os.execvp"),
    ("V01.2", bash(CommandLine("echo " + "$(true) " * 1000 + "; echo ok"), T), 0,
     "a thousand substitutions stay within the budget"),
    ("V02.3", bash(CommandLine("(cd /tmp; echo x > f) & wait"), T), 0, "a background subshell's cd holds for its list"),
    ("V03.2", bash(CommandLine("{ export D=/tmp; echo x > $D/f; } & wait"), T), 0, "export in a background group"),
    ("V04.2", bash(CommandLine('{ set -- CHANGELOG.md /tmp/ok; shift; echo x > "$1"; } & wait'), T), 0,
     "set -- and shift in a background group"),
    ("V05.2", bash(CommandLine("{ D=/tmp; unset D; echo x > ${D:-CHANGELOG.md}; } & wait"), T), 2,
     "unset in a background group"),
    ("V06.3", bash(CommandLine('echo "$C" | bash'), T), 2, "a program piped into bash that the reader cannot know"),
    ("V07.3", bash(CommandLine("python3 -c \"x = ${N}; open('CHANGELOG.md', 'w')\""), T), 2,
     "Python holding a shell expansion, read with a placeholder"),
    ("V08.3", bash(CommandLine('python3 -c "${A} ${B}"'), T), 2, "Python that parses neither way, holding expansions"),
    ("V09.3", bash(CommandLine(f"printf 'echo x \\x3e CHANGELOG.md\\n' > {S}/px7.sh; bash {S}/px7.sh"), T), 2,
     "printf's hex escape"),
    ("V10.3", bash(CommandLine(f"printf 'echo x \\076 CHANGELOG.md\\n' > {S}/po7.sh; bash {S}/po7.sh"), T), 2,
     "printf's octal escape"),
    ("V11.3", bash(CommandLine(f"echo 'echo x \\x3e CHANGELOG.md' > {S}/ez7.sh; bash {S}/ez7.sh"), T), 2,
     "zsh's echo reads escapes"),
    ("V12.3", bash(CommandLine(f"mkdir -p {S}/d7 && cp {S}/a7.sh {S}/b7.sh {S}/d7/ && bash {S}/d7/a7.sh"), S), 2,
     "cp of several files into a directory"),
    ("V13.3", bash(CommandLine(f"cp {S}/a7.sh {S}/c7.sh && bash {S}/c7.sh"), S), 2, "a copy carries its source's text"),
    ("V14.3", bash(CommandLine(f"sed -i 's/q/r/' {S}/w7.sh && bash {S}/w7.sh"), S), 2,
     "a script edited in place is read nearly"),
    ("V15.2", bash(CommandLine(f"rm -f {S}/w7.sh; bash {S}/w7.sh"), S), 0, "a script removed first runs nothing"),
    ("V16.2", bash(CommandLine(f"sed 's/hi/x > CHANGELOG.md/' {S}/hi7.sh > {S}/h7.sh && cd ../live && bash {S}/h7.sh"),
                   S), 2, "a filter's output is known only nearly"),
    ("V18.2", bash(CommandLine("node -e 'const s = `x`; console.log(s)'"), T), 0,
     "a JavaScript template literal runs nothing"),
    ("V19", bash(CommandLine('echo x > "$U/f"; cd ../live && echo y > /tmp/ok'), S), 0,
     "an unplaced path marks its own command"),
    ("V21.2", bash(CommandLine("cat "
                               "<<EOF\n" + "${x:-" * 30 + "z" * 20_000 + "}" * 30 + "\n$(echo x > CHANGELOG.md)\nEOF"),
                   T), 2, "a budget spent inside a scan makes the call unreadable"),
    ("V25", bash(CommandLine("bash -c 'rm -f /tmp/ok\\ CHANGELOG.md'"), T), 0, "bash does not split an escaped space"),
    ("V26", bash(CommandLine("flock /tmp/l -c 'F=\"/tmp/ok.txt CHANGELOG.md\"; rm -f $F'"), T), 0,
     "flock -c runs in the call's $SHELL, the tool's zsh"),
    ("V26b", bash(CommandLine("SHELL=/bin/bash flock /tmp/l -c 'F=\"/tmp/ok.txt CHANGELOG.md\"; rm -f $F'"), T), 2,
     "flock -c under SHELL=/bin/bash"),
    ("V26c", bash(CommandLine("export SHELL=/bin/bash; flock /tmp/l -c 'F=\"/tmp/ok.txt CHANGELOG.md\"; rm -f $F'"), T),
     2, "flock -c after export SHELL=/bin/bash"),
    ("V27", bash(CommandLine("perl -e '" + 'system("true");' * 6000 + "'"), T), 2,
     "the commands Perl starts spend the budget: the call is unreadable"),
    ("W1", bash(CommandLine("taskset -c 0-19 git clean -fdx"), T), 2, "git behind a wrapper still writes"),
    ("W2", bash(CommandLine("time git hook run pre-commit"), T), 2, "git behind time still writes"),
    ("W3", bash(CommandLine("git bisect run make test"), T), 2, "git bisect run writes, and runs its command"),
    ("W5", bash(CommandLine("taskset -c 0-19 bash probes/copy_build.sh > /tmp/x.log 2>&1"), T), 0,
     "a repository probe building in a scratch copy writes only there"),
    ("X2", bash(CommandLine(f"sed 's/a/b/' {S}/py7.py > {S}/py8.py && python3 {S}/py8.py"), T), 2,
     "Python a filter produced: what it writes is out of sight"),
    ("F4", bash(CommandLine(f"grep cargo {S}/src7.sh > {S}/g7.sh && bash {S}/g7.sh"), T), 2,
     "lines grep selects are unknown: unreadable while a run is live"),
    ("F5", bash(CommandLine(f"sed 's/a/b/' {S}/hi7.sh > {S}/ap7.sh; printf 'echo done\\n' >> {S}/ap7.sh; cd ../live && "
                            f"bash {S}/ap7.sh"), S), 2, "an append keeps a near copy near"),
    ("F6", bash(CommandLine(f"head -n 1 {S}/src7.sh > {S}/h8.sh; cd ../live && bash {S}/h8.sh"), S), 0,
     "head -n N: the count is a value, not a file"),
    ("SL6", bash(CommandLine(f"head -n 1 {S}/src7.sh {S}/src8.sh > {S}/m7.sh; cd ../live && bash {S}/m7.sh"), S), 2,
     "head over two files prints headers: its output is unknown"),
    ("SL7", bash(CommandLine(f"cat -A {S}/hi7.sh > {S}/ca7.sh; cd ../live && bash {S}/ca7.sh"), S), 2,
     "cat -A marks the text: its output is unknown"),
    ("H1", bash(CommandLine(f"cat > {S}/rec7.sh <<EOF\ncd {S}\n"
                            f"for l in a b; do cp \"\\${{l}}.json\" \"\\${{l}}_base.json\"; done\nEOF\n"
                            f"bash {S}/rec7.sh"), T), 0,
     "an unquoted heredoc writes \\$ as $: the script's loop names its paths"),
]
R6_QUIET = [
    ("EM3", bash(CommandLine("E=; $E cargo test"), Q), 2, "an empty unquoted expansion before a heavy run"),
    ("EM4", bash(CommandLine("${NOPE_UNSET_P6:-} cargo test"), Q), 2, "an empty default before a heavy run"),
    ("UX1", bash(CommandLine("printf 'C:\\\\Users\\\\nicolas\\\\n' > /tmp/p6-win.txt; cargo test"), Q), 2,
     "a Windows path in printf beside a heavy run"),
    ("UX2", bash(CommandLine("f() { shift \u00b2; echo x; }; f a b"), Q), 0, "a superscript shift count"),
    ("CH1", bash(CommandLine("{ R=$(taskset -c 0-19 cargo test); } > /tmp/x.log 2>&1"), Q), 2,
     "a capture inside a logged group"),
    ("BG1", bash(CommandLine("for i in $(seq 10); do A$i=$(date +%s); done; echo " + "x" * 50000 + "; cargo test"), Q),
     2, "substitutions before a long line and a heavy run"),
    ("GC1", bash(CommandLine("git bisect run cargo test"), Q), 2, "git bisect run starts a heavy run"),
    ("GC2", bash(CommandLine("git rebase -x 'cargo test' HEAD~2"), Q), 2, "git rebase -x starts a heavy run"),
    ("RC1", bash(CommandLine(f"cp probes/run_all.sh {S}/ra.sh && bash {S}/ra.sh"), Q), 2,
     "a copy of the runner, run bare"),
    ("RC2", bash(CommandLine(f"sed 's/x/y/' probes/run_all.sh > {S}/ra2.sh && bash {S}/ra2.sh"), Q), 2,
     "a filtered copy of the runner, run bare"),
    ("RC3", bash(CommandLine(f"taskset -c 0-19 bash {S}/ra3.sh > /tmp/x.log 2>&1"), RT), 0,
     "a runner copied earlier, blessed"),
    ("RC3b", bash(CommandLine(f"taskset -c 0-19 bash {S}/ra3.sh > /tmp/x.log 2>&1"), Q), 2,
     "the store it runs holds a probe naming CPUs 20-23: the glob is read"),
    ("RC5", bash(CommandLine(f"bash {S}/ra3.sh"), Q), 2, "a runner copied in an earlier call, run bare"),
    ("RP1", bash(CommandLine("./probes/heavy_probe.sh 2>&1 | head -5"), Q), 2,
     "a repository probe running a heavy command, bare"),
    ("RP2", bash(CommandLine("taskset -c 0-19 bash probes/heavy_probe.sh > /tmp/x.log 2>&1"), Q), 0,
     "a repository probe running a heavy command, blessed"),
    ("RP3", bash(CommandLine("bash probes/light_probe.sh"), Q), 0, "a repository probe running nothing heavy"),
    ("RP4", bash(CommandLine("taskset -c 0-19 bash probes/capturing_probe.sh > /tmp/x.log 2>&1"), Q), 0,
     "a repository probe capturing a heavy command's output, blessed"),
    ("RP5", bash(CommandLine(f"taskset -c 0-19 bash {S}/wrap5.sh > /tmp/x.log 2>&1"), Q), 0,
     "a script copying that probe and running the copy: the copy runs what the repository's probe runs"),
    ("RP6", bash(CommandLine(f"taskset -c 0-19 bash {S}/wrap6.sh > /tmp/x.log 2>&1"), Q), 2,
     "a script running a filtered copy: the copy is its own text, its capture held to rule 1"),
    ("RP7", bash(CommandLine(f"taskset -c 0-19 bash {S}/wrap7.sh > /tmp/x.log 2>&1"), Q), 2,
     "a script running a copy it appended to: its own text"),
    ("RP8", bash(CommandLine(f"taskset -c 0-19 bash {S}/wrap8.sh > /tmp/x.log 2>&1"), Q), 2,
     "a script running a copy of a script outside every tree: no repository's text"),
    ("RP9", bash(CommandLine(f"taskset -c 0-19 bash {S}/wrap9.sh > /tmp/x.log 2>&1"), Q), 2,
     "a script running a copy of a probe it edited first: its own text"),
    ("PS1", bash(CommandLine("taskset -c 0-19 python3 -c \"import subprocess; "
                             "subprocess.run(['taskset', '-c', '0-23', 'cargo', 'test'])\" > /tmp/x.log 2>&1"), Q), 2,
     "Python starting a heavy run on 0-23"),
    ("PS2", bash(CommandLine("taskset -c 0-19 python3 -c \"import os; "
                             "os.system('cargo test > /tmp/y.log 2>&1 &')\" > /tmp/x.log 2>&1"), Q), 2,
     "Python starting a heavy run in the background"),
    ("PS3", bash(CommandLine("taskset -c 0-19 python3 -c \"import os; "
                             "os.system('cargo test > /dev/null 2>&1')\" > /tmp/x.log 2>&1"),
                 Q), 2, "Python starting a heavy run into /dev/null"),
    ("EX1", bash(CommandLine("python3 - <<'PY'\nimport subprocess\n"
                             "subprocess.run(['cargo', 'test'], executable='/bin/true')\nPY"), Q), 0,
     "executable= runs /bin/true, not cargo"),
    ("RC4", bash(CommandLine(f"sed -i '$a cargo test' {S}/e3.sh && bash {S}/e3.sh"), Q), 0,
     "a script edited by sed, read from its old text (no heavy run in it)"),
    ("V17", bash(CommandLine("python3 -c \"import os; os.spawnlp(os.P_WAIT, 'cargo', 'cargo', 'test')\""), Q), 2,
     "os.spawnlp starts the argv after its mode and file"),
    ("V20", bash(CommandLine(f"{S}/ra3.sh > /dev/null"), Q), 2, "a byte copy of the runner, run by its path"),
    ("V24", bash(CommandLine("cargo test $(("), Q), 2, "a heavy command in a call that cannot be read"),
    ("V24b", bash(CommandLine("echo $(("), Q), 0, "a light call that cannot be read"),
    ("V28", bash(CommandLine("python3 -c 'import subprocess; subprocess.run(\"cargo test $((\", shell=True)'"), Q), 2,
     "a heavy command Python starts in text that does not parse"),
    ("V30b", bash(CommandLine("git commit -m \"$((\"; echo 'the tests pass'"), Q), 0,
     "an unreadable call with nothing heavy"),
    ("W4", bash(CommandLine("echo \"$((\"; printf '%s\\n' 'x\n./probes/run_all.sh | head'"), Q), 0,
     "an unreadable call quoting a heavy command as data"),
    ("W3b", bash(CommandLine("git bisect run cargo test"), Q), 2, "git bisect run carries a heavy command"),
    ("W3c", bash(CommandLine("git rebase -x 'cargo test' main"), Q), 2, "git rebase -x carries a heavy command"),
    ("W3d", bash(CommandLine("git bisect start HEAD HEAD~3"), Q), 0, "git bisect without run carries nothing"),
    ("X1.2", bash(CommandLine(f"grep -v bash probes/run_all.sh > {S}/f7.sh; bash {S}/f7.sh"), Q), 0,
     "lines a filter selects from the runner are not the runner"),
    ("X1c.2", bash(CommandLine(f"perl -pe 's/x/y/' probes/run_all.sh > {S}/f9.sh && bash {S}/f9.sh"), Q), 2,
     "perl -p edits every line: still the runner"),
    ("F1", bash(CommandLine(f"head -1 {S}/src7.sh > {S}/b7.sh; printf 'exit 0\\n' >> {S}/b7.sh; bash {S}/b7.sh"), Q), 0,
     "the lines head selects are read, not the whole source"),
    ("F2", bash(CommandLine(f"tail -n 1 {S}/src7.sh > {S}/t7.sh && bash {S}/t7.sh"), Q), 2,
     "the line tail selects runs cargo test"),
    ("F3.2", bash(CommandLine(f"sed -n '2p' {S}/src7.sh > {S}/s7.sh && bash {S}/s7.sh"), Q), 2,
     "the line sed -n selects runs cargo test"),
    ("SL1", bash(CommandLine(f"tail -n 1 {S}/src8.sh > {S}/t8.sh; bash {S}/t8.sh"), Q), 0,
     "the line tail selects is all that runs"),
    ("SL2", bash(CommandLine(f"cat probes/run_all.sh > {S}/rc7.sh && bash {S}/rc7.sh"), Q), 2,
     "cat of the runner is the runner"),
    ("SL3", bash(CommandLine(f"sed -n '1p' {S}/src7.sh > {S}/s8.sh; bash {S}/s8.sh"), Q), 0,
     "the line sed -n selects is all that runs"),
    ("SL4", bash(CommandLine('echo \\" ; cargo test $(('), Q), 2, "an escaped quote opens nothing in unreadable text"),
    ("SL5", bash(CommandLine("echo 'x'; cargo test $(("), Q), 2, "a closed quote ends its span in unreadable text"),
    ("H2", bash(CommandLine(f"cat > {S}/bn7.sh <<EOF\ncargo \\\ntest\nEOF\nbash {S}/bn7.sh"), Q), 2,
     "a backslash-newline in an unquoted heredoc joins the lines"),
    ("NU1", bash(CommandLine("taskset -c 0-19 python3 -c \"import os; "
                             "os.system('numactl -C 20-23 cargo test')\" > /tmp/x.log 2>&1"),
                 Q), 2, "numactl onto 20-23 for a heavy run Python starts"),
    ("NU2", bash(CommandLine("taskset -c 0-19 python3 -c \"import os; "
                             "os.system('numactl -C 0-19 cargo test')\" > /tmp/x.log 2>&1"),
                 Q), 0, "numactl within 0-19 for a heavy run Python starts"),
    ("AG3", bash(CommandLine("/home/nicolas/.cabal/bin/agda --numeric-version"), Q), 0, "agda printing its version"),
    ("AG4", bash(CommandLine("agda --print-agda-dir"), Q), 0, "agda printing a path"),
    ("AG5", bash(CommandLine("agda src/Aletheia/Main.agda"), Q), 2, "agda checking a module"),
    ("MU1", bash(CommandLine("mull-runner-23 ./unit_tests --dry-run --reporters=Elements"), Q), 0,
     "mull-runner --dry-run lists"),
    ("MU2", bash(CommandLine("mull-runner-23 ./unit_tests"), Q), 2, "mull-runner runs the mutants"),
    ("CM8", bash(CommandLine("cmake --build cpp/build --target help"), Q), 0, "cmake --build --target help lists"),
    ("CM9", bash(CommandLine("cmake --build cpp/build --target help --target unit_tests"), Q), 2,
     "a real target beside help"),
    ("GL1", bash(CommandLine("bash probes/heavy_pro*.sh"), Q), 2, "a glob naming a heavy probe is read"),
    ("GL2", bash(CommandLine("for p in probes/heavy*.sh; do bash \"$p\"; done"), Q), 2,
     "a loop over a glob of probes is read"),
    ("GL3", bash(CommandLine("for f in a b c d e f g h i; do echo x > \"$f.log\"; done"), S), 0,
     "nine values are read"),
    ("MEM1", bash(CommandLine("X=a; for i in 1 2 3 4 5 6 7 8; do for j in 1 2 3 4 5 6 7 8; do X=$X$X; done; done; "
                              "cargo test"), Q), 2,
     "a doubling value is capped: the reading goes on to the heavy command"),
]
R6_LOOPS = [
    ("PL1", bash(CommandLine("bash -c 'sleep 0.3 & sleep 0.4 & wait' | cat"), T), 0, "a carrier's own wait, piped"),
    ("PL2", bash(CommandLine("bash -c 'sleep 1 & wait %\u00b2'"), T), 2, "a superscript job spec, no crash"),
    ("PL3", bash(CommandLine("sleep 999 &\necho work\nkill $(jobs -p)"), T), 2, "zsh: $(jobs -p) is not a pid"),
    ("V23", bash(CommandLine("f() { tail -f /tmp/a | grep x & }; trap f EXIT; echo hi"), T), 2,
     "a job an EXIT trap's function puts in the background"),
    ("V23b", bash(CommandLine("trap 'sleep 9 &' EXIT; echo "
                              "hi"), T), 2, "a job an EXIT trap's text puts in the background"),
    ("V23c", bash(CommandLine("f() { sleep 1 & wait; }; trap f EXIT; echo hi"), T), 0,
     "a trap that waits for its own job"),
    ("X3", bash(CommandLine(f"cat > {S}/rt.sh <<'EOF'\nf() {{\n  trap \"rm -f /tmp/rt\" RETURN\n  sleep 3\n}}\nf\nEOF\n"
                 f"bash {S}/rt.sh & P=$!\nsleep 1\nkill -INT $P\nwait $P"), T), 0,
     "a RETURN trap in a background script runs in that job's own process"),
]


LIMITS = [
    ("LIM1", CommandLine("run-clang-tidy-23 -quiet -p build cpp/src/"), 2, "run-clang-tidy with no job count"),
    ("LIM2", CommandLine("run-clang-tidy-23 -j 5 -quiet -p build cpp/src/"), 0, "run-clang-tidy with -j 5"),
    ("LIM3", CommandLine("run-clang-tidy-23 -j5 -p build src"), 0, "run-clang-tidy with -j5"),
    ("LIM4", CommandLine("run-clang-tidy-23 -j=5 -p build src"), 0, "run-clang-tidy with -j=5"),
    ("LIM5", CommandLine("run-clang-tidy-23 -j 0 -p build src"), 2, "run-clang-tidy with -j 0, which is every CPU"),
    ("LIM6", CommandLine("run-clang-tidy-23 -j 24 -p build src"), 2, "run-clang-tidy with -j 24, past 20"),
    ("LIM7", CommandLine("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p build src"), 0,
     "run-clang-tidy under PYTHON_CPU_COUNT=5"),
    ("LIM8", CommandLine("env PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p build src"), 0, "PYTHON_CPU_COUNT=5 through env"),
    ("LIM9", CommandLine("export PYTHON_CPU_COUNT=5; run-clang-tidy-23 -p build src"), 0,
     "PYTHON_CPU_COUNT=5 exported first"),
    ("LIM10", CommandLine("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -j 0 -p build src"), 0,
     "-j 0 reads cpu_count, which PYTHON_CPU_COUNT=5 bounds"),
    ("LIM11", CommandLine("PYTHON_CPU_COUNT=0 run-clang-tidy-23 -p build src"), 2, "PYTHON_CPU_COUNT=0 bounds nothing"),
    ("LIM12", CommandLine("python3 /usr/lib/llvm-23/bin/run-clang-tidy -p build src"), 2,
     "run-clang-tidy through python3"),
    ("LIM13", CommandLine("out=$(cd cpp && run-clang-tidy-23 -quiet -p build src 2>&1); echo \"$out\" | tail -1"), 2,
     "run-clang-tidy in a command substitution"),
    ("LIM14", CommandLine("bash -c 'run-clang-tidy-23 -p build src'"), 2, "run-clang-tidy in bash -c text"),
    ("LIM15", CommandLine(f"bash {S}/tidy.sh"), 2, "run-clang-tidy in a script the call runs"),
    ("LIM16", CommandLine(f"bash {S}/tidy_bound.sh"), 0, "run-clang-tidy -j 4 in a script the call runs"),
    ("LIM17", CommandLine("N=$(nproc); run-clang-tidy-23 -j $N -p build src"), 2, "a job count read only when it runs"),
    ("LIM18", CommandLine("clang-tidy-23 -p build src/a.cpp"), 0, "clang-tidy on one file sizes nothing"),
    ("LIM19", CommandLine("git grep -n run-clang-tidy -- tools"), 0, "a search naming run-clang-tidy runs nothing"),
    ("LIM20", CommandLine("python3 -c \"import subprocess; "
                          "subprocess.run(['run-clang-tidy-23', '-p', 'build', 'src'])\""), 2,
     "run-clang-tidy started from Python"),
    ("LIM21", CommandLine("python3 /opt/llvm/share/clang/run-clang-tidy.py -p build src"), 2,
     "run-clang-tidy.py through python3"),
    ("LIM22", CommandLine("run-clang-tidy-23 -j 20 -p build src"), 0, "-j 20, the session's whole share"),
    ("LIM23", CommandLine("run-clang-tidy-23 -j 21 -p build src"), 2, "-j 21, one past it"),
    ("LIM24", CommandLine("run-clang-tidy-23 -j 15 -p build src"), 0, "-j 15"),
    ("LIM25", CommandLine("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -jx -p build src"), 2,
     "-jx names no count, whatever the bound"),
    ("LIM27", CommandLine("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -j 24 -p build src"), 2,
     "-j 24 wins over PYTHON_CPU_COUNT=5"),
    ("LIM28", CommandLine("python3 -c 'print(1)'"), 0, "a Python call that is not run-clang-tidy"),
    ("LIM30", CommandLine("systemd-run --user --scope -p MemoryMax=1G true"), 2,
     "a user scope with no slice: app.slice"),
    ("LIM31", CommandLine("systemd-run --user --scope --slice=claude.slice -p MemoryMax=1G true"), 0,
     "--slice=claude.slice"),
    ("LIM32", CommandLine("systemd-run --user --scope --slice claude.slice true"), 0, "--slice claude.slice"),
    ("LIM33", CommandLine("systemd-run --user --scope -p Slice=claude.slice true"), 0, "-p Slice=claude.slice"),
    ("LIM34", CommandLine("systemd-run --user --scope --slice-inherit true"), 0, "--slice-inherit from a call's scope"),
    ("LIM35", CommandLine("systemd-run --scope --slice=claude.slice true"), 2,
     "the system manager has no claude.slice"),
    ("LIM36", CommandLine("systemd-run --user --slice=app.slice true"), 2, "a service placed in app.slice"),
    ("LIM37", CommandLine("systemd-run --user --scope --slice=claude-tests.slice true"), 0,
     "a slice beneath claude.slice"),
    ("LIM38", CommandLine("systemd-run --user --scope --slice=claudette.slice true"), 2,
     "a slice merely named like it"),
    ("LIM39", CommandLine("systemd-run --user --scope --description --slice=claude.slice true"), 2,
     "--slice=claude.slice as --description's value places nothing"),
    ("LIM40", CommandLine("taskset -c 0-19 systemd-run --user --scope true"), 2, "systemd-run under another wrapper"),
    ("LIM41", CommandLine("SL=claude.slice; systemd-run --user --scope --slice=$SL true"), 0,
     "a slice the call assigns first"),
    ("LIM42", CommandLine("PYTHON_CPU_COUNT=5 systemd-run --user --scope -q -p MemoryMax=24G -p MemorySwapMax=0 "
                          f"taskset -c 0-19 ./probes/run_all.sh > {S}/store.log 2>&1"), 2,
     "the store in a scope of its own, outside claude.slice"),
    ("LIM43", CommandLine(f"PYTHON_CPU_COUNT=5 systemd-run --user --scope --slice=claude.slice -q taskset -c 0-19 "
                          f"./probes/run_all.sh > {S}/store.log 2>&1"), 0, "the store in a scope under claude.slice"),
    ("LIM44", CommandLine("systemd-run --user --scope --slice=claude.slicex true"), 2,
     "a name that only begins as claude.slice"),
    ("LIM45", CommandLine("systemd-run --user --scope --property=Slice=claude.slice true"), 0,
     "--property=Slice=claude.slice"),
    ("LIM46", CommandLine("systemd-run --user --scope -pSlice=claude.slice true"), 0, "-pSlice=claude.slice"),
    ("LIM47", CommandLine("systemd-run --scope --user --slice=claude.slice true"), 0,
     "a flag before --user takes no value"),
    ("LIM48", CommandLine("systemd-run --user --scope --slice-inherit --slice=bar true"), 0,
     "--slice-inherit places --slice=bar beneath the caller's slice (systemd-run(1))"),
    ("LIM49", CommandLine("systemd-run --user --scope --slice=bar --slice-inherit true"), 0,
     "the same, the options the other way"),
]


CK = Path("/home/nicolas/dev/agda/aletheia")  # the checkout whose run records rule 7 keeps a search out of
SIB = CK.parent / "aletheia-ci-output"  # the records' directory beside it
SEARCHES = [
    ("SCH1", CommandLine('grep -rn "read_issues" python/aletheia python/tests tools'), CK, 2,
     "the incident: tools holds ci-output"),
    ("SCH2", CommandLine('grep -rn "returned null" python/aletheia python/tests tools docs *.md | grep -v ci-output'),
     CK, 2, "the incident: a filter after grep reads nothing less"),
    ("SCH3", CommandLine("grep -rn x"), CK, 2, "no path: grep reads where it runs"),
    ("SCH4", CommandLine("grep -R x tools"), CK, 2, "-R"),
    ("SCH5", CommandLine("grep --recursive x tools"), CK, 2, "--recursive"),
    ("SCH6", CommandLine("grep --dereference-recursive x tools"), CK, 2, "--dereference-recursive"),
    ("SCH7", CommandLine("grep -d recurse x tools"), CK, 2, "-d recurse"),
    ("SCH8", CommandLine("grep --directories=rec x tools"), CK, 2, "--directories=rec, an abbreviated action"),
    ("SCH9", CommandLine("grep --rec x tools"), CK, 2, "--rec, an abbreviated --recursive"),
    ("SCH10", CommandLine("grep x tools -r"), CK, 2, "-r after the operands, as getopt permutes them"),
    ("SCH11", CommandLine("grep -d read -r x tools"), CK, 2, "the last of -d and -r decides: -r"),
    ("SCH12", CommandLine("rgrep x tools"), CK, 2, "rgrep recurses without a flag"),
    ("SCH13", CommandLine("egrep -r x tools"), CK, 2, "egrep -r"),
    ("SCH14", CommandLine("fgrep -r x tools"), CK, 2, "fgrep -r"),
    ("SCH15", CommandLine(f"grep -r x {CK.parent}"), S, 2, "a directory holding both record directories"),
    ("SCH16", CommandLine(f"grep -r --exclude-dir=ci-output x {CK.parent}"), S, 2,
     "one record directory skipped, the other read"),
    ("SCH17", CommandLine("grep -r --exclude-dir=ci-output x ."), CK / "tools" / "ci-output", 2,
     "the record directory itself, which an --exclude-dir does not skip"),
    ("SCH18", CommandLine("grep -r --exclude-dir=ci-output x tools/ci-output/"), CK, 2,
     "the record directory itself, a slash after it"),
    ("SCH19", CommandLine("grep -r --exclude-dir=output x tools"), CK, 2,
     "a glob that matches no record directory's name"),
    ("SCH20", CommandLine("grep -r --exclude-dir=tools/ci-output x tools"), CK, 2,
     "a glob holding a slash matches no name"),
    ("SCH21", CommandLine(f"cd {CK}/tools && grep -r x ."), S, 2, "a path placed where a cd leaves the command"),
    ("SCH22", CommandLine("bash -c 'grep -r x tools'"), CK, 2, "a grep in bash -c text"),
    ("SCH23", CommandLine("python3 -c \"import subprocess; subprocess.run(['grep', '-r', 'x', 'tools'])\""), CK, 2,
     "a grep Python starts"),
    ("SCH24", CommandLine(f"echo {CK}/tools | xargs grep -r x"), S, 2, "the operands xargs hands it"),
    ("SCH25", CommandLine("find tools -maxdepth 0 -exec grep -r x {} +"), CK, 2, "a grep find -exec runs on its roots"),
    ("SCH26", CommandLine("grep -r x rec-link"), S, 2, "a symlink to the checkout's tools"),
    ("SCH27", CommandLine('grep -r x "$UNKNOWN"'), CK, 2, "a path wholly unknown stands for where the command runs"),
    ("SCH28", CommandLine(f"grep -r -e x {CK}/tools"), S, 2, "-e gives the pattern: the first operand is a path"),
    ("SCH29", CommandLine("grep -rex "
                          f"{CK}/tools"), S, 2, "-e's value is the rest of its word: the first operand is a path"),
    ("SCH30", CommandLine("grep -r --file=pats "
                          f"{CK}/tools"), S, 2, "--file gives the pattern: the first operand is a path"),
    ("SCH31", CommandLine("grep -r --regexp=x "
                          f"{CK}/tools"), S, 2, "--regexp gives the pattern: the first operand is a path"),
    ("SCH32", CommandLine('cd "$NOWHERE" && grep -r x '
                          'tools'), CK, 2, "a cd the reader cannot follow: placed where the call starts"),
    ("SCH33", CommandLine('cat "$LIST" | xargs grep -r x python'), CK, 2,
     "operands xargs reads from a list nowhere known: placed where it runs"),
    ("SCP1", CommandLine('grep -rn --exclude-dir=ci-output "read_issues" python/aletheia python/tests tools'), CK, 0,
     "the incident with --exclude-dir=ci-output"),
    ("SCP2", CommandLine('grep -rn --exclude-dir=ci-output "returned null" python/aletheia python/tests tools docs *.md'
                         " | grep -v ci-output"), CK, 0, "the second incident with --exclude-dir=ci-output"),
    ("SCP3", CommandLine("grep -rn x python/aletheia python/tests"), CK, 0, "every path elsewhere"),
    ("SCP4", CommandLine("grep -n x tools"), CK, 0, "not recursive"),
    ("SCP5", CommandLine("grep -r -d read x tools"), CK, 0, "the last of -r and -d decides: -d read"),
    ("SCP6", CommandLine("grep -r -d re x tools"), CK, 0, "-d re is ambiguous, which grep refuses"),
    ("SCP7", CommandLine("grep -r x tools/ci-out"), CK, 0, "a name the record directory's only begins with"),
    ("SCP8", CommandLine("grep -r --exclude-dir='ci-out*' x tools"), CK, 0, "a glob matching ci-output"),
    ("SCP9", CommandLine("grep -r --exclude-dir ci-output x tools"), CK, 0,
     "--exclude-dir with its glob in the next word"),
    ("SCP10", CommandLine("grep -r --exclude-d=ci-output x tools"), CK, 0, "--exclude-d, an abbreviated --exclude-dir"),
    ("SCP11", CommandLine(f"grep -rn --exclude-dir=ci-output --exclude-dir=aletheia-ci-output x {CK.parent}"), S, 0,
     "both record directories skipped"),
    ("SCP12", CommandLine("grep -r x -"), CK, 0, "- reads standard input"),
    ("SCP13", CommandLine("git grep -n x -- tools"), CK, 0, "git grep reads tracked files only"),
    ("SCP14", CommandLine("grep -r x"), S, 0, "no path, run elsewhere"),
    ("SCP15", CommandLine("grep -re tools python"), CK, 0, "-e takes the next word as the pattern"),
    ("SCP16", CommandLine("grep -- -r x"), CK, 0, "-r after -- is the pattern"),
    ("SCP17", CommandLine("grep -r x "
                          "tools/ci-output/run1"), CK, 0, "a path inside a record directory neither is nor holds one"),
    ("SCP18", CommandLine(f"grep -r --exclude-dir='*output' x {CK.parent}"), S, 0, "one glob matching both names"),
    ("SCP19", CommandLine(f"grep -r x {SIB}/run1"), S, 0, "a path inside the records beside the checkout"),
    ("SCP20", CommandLine("grep -dread x tools"), CK, 0, "-d's value is the rest of its word, read as no letters"),
    ("SCP21", CommandLine("grep -r --regexp tools python"), CK, 0, "--regexp takes the next word as the pattern"),
    ("SCP22", CommandLine("grep -r tools python"), CK, 0, "the first operand is the pattern"),
    ("SCP23", CommandLine("grep -rn --exclude- x python"), CK, 0, "an ambiguous long option, which grep refuses"),
    ("RG1", CommandLine("rg -u x tools"), CK, 2, "-u turns off the .gitignore that skips tools/ci-output"),
    ("RG2", CommandLine(f"rg -n x {CK.parent}"), S, 2, "the records beside the checkout, which no ignore file covers"),
    ("RG3", CommandLine("rg -n x"), CK.parent, 2, "no path and nothing on stdin: rg searches where it runs"),
    ("RG4", CommandLine("rg -uu x tools"), CK, 2, "-uu"),
    ("RG5", CommandLine("rg --no-ignore x tools"), CK, 2, "--no-ignore"),
    ("RG6", CommandLine("rg --no-ignore-vcs x tools"), CK, 2, "--no-ignore-vcs"),
    ("RG7", CommandLine("rg --unrestricted x tools"), CK, 2, "--unrestricted, -u's long name"),
    ("RG8", CommandLine("rg -nu x tools"), CK, 2, "-u in a cluster"),
    ("RG9", CommandLine("rg x tools/ci-output"), CK, 2, "the record directory itself"),
    ("RG10", CommandLine("rg -g '!ci-output' x tools/ci-output"), CK, 2,
     "the record directory itself, which no -g skips"),
    ("RG11", CommandLine("rg -u -g '!ci-output' -g ci-output x tools"), CK, 2,
     "a later -g matching the name takes it back"),
    ("RG12", CommandLine(f"rg -u -g '!aletheia-ci-output' x {CK.parent}"), S, 2,
     "one record directory skipped, the other read"),
    ("RG13", CommandLine(f"rg -g '!ci-output' x {CK.parent}"), S, 2, "a glob naming the other directory"),
    ("RG14", CommandLine("rg -u -g '!tools/ci-output' x "
                         "tools"), CK, 2, "a glob holding a slash, which no root anchors alike"),
    ("RG15", CommandLine(f"rg -e x {CK.parent}"), S, 2, "-e gives the pattern: the first operand is a path"),
    ("RG16", CommandLine(f"rg -f pats {CK.parent}"), S, 2, "-f gives the pattern: the first operand is a path"),
    ("RG17", CommandLine("rg --regexp=x "
                         f"{CK.parent}"), S, 2, "--regexp gives the pattern: the first operand is a path"),
    ("RG18", CommandLine(f"rg --file=pats {CK.parent}"), S, 2, "--file gives the pattern: the first operand is a path"),
    ("RG19", CommandLine(f"rg --files {CK.parent}"), S, 2, "--files takes every operand for a path"),
    ("RG20", CommandLine("echo x | rg --files"), CK.parent, 2, "--files lists where it runs, whatever its stdin"),
    ("RG21", CommandLine("echo x | rg -f "
                         "-"), CK.parent, 2, "-f - takes stdin for the patterns: rg searches where it runs"),
    ("RG22", CommandLine("rg x < /dev/null"), CK.parent, 2, "/dev/null on stdin: rg searches where it runs"),
    ("RG23", CommandLine("rg x 3< pats"), CK.parent, 2, "a file on another descriptor leaves stdin as it was"),
    ("RG24", CommandLine(f"cd {CK} && rg -u x"), S, 2, "no path, placed where a cd leaves the command"),
    ("RG25", CommandLine("bash -c 'rg -u x tools'"), CK, 2, "an rg in bash -c text"),
    ("RG26", CommandLine(f"echo {CK.parent} | xargs rg x"), S, 2, "the operands xargs hands it"),
    ("RG27", CommandLine("rg --ignore -u x tools"), CK, 2, "the last of --ignore and -u decides: -u"),
    ("RG28", CommandLine("echo x | rg -u x tools"), CK, 2, "a path given: rg searches it, not its stdin"),
    ("RG29", CommandLine("echo x | rg x < /dev/null"), CK.parent, 2, "the last source on stdin decides: /dev/null"),
    ("RGP1", CommandLine("rg -n x"), CK, 0, "the checkout, whose tools/ci-output a .gitignore inside it skips"),
    ("RGP2", CommandLine("rg -n x . tools"), CK, 0, "the checkout's root and its tools"),
    ("RGP3", CommandLine(f"rg -n x {CK}"), S, 0, "the checkout by its absolute path"),
    ("RGP4", CommandLine(f"rg -g '!aletheia-ci-output' x {CK.parent}"), S, 0,
     "the records beside the checkout skipped by name"),
    ("RGP5", CommandLine("echo x | rg x"), CK.parent, 0, "rg searches stdin, a pipe"),
    ("RGP6", CommandLine("rg x <<< x"), CK.parent, 0, "rg searches stdin, a here-string"),
    ("RGP7", CommandLine("rg x < pats"), CK.parent, 0, "rg searches stdin, a file"),
    ("RGP8", CommandLine("rg x -"), CK.parent, 0, "- names stdin"),
    ("RGP9", CommandLine("rg -u -g '!ci-output' x tools"), CK, 0, "-u with the record directory skipped by name"),
    ("RGP10", CommandLine("rg -u --glob '!ci-output' x tools"), CK, 0, "--glob with its value in the next word"),
    ("RGP11", CommandLine("rg -u --glob=!ci-output x tools"), CK, 0, "--glob="),
    ("RGP12", CommandLine("rg -u -g=!ci-output x tools"), CK, 0, "-g=, the = dropped as rg drops it"),
    ("RGP13", CommandLine("rg -ug!ci-output x tools"), CK, 0, "-g's value is the rest of its word"),
    ("RGP14", CommandLine("rg -u -g ci-output -g '!ci-output' x tools"), CK, 0,
     "the last -g matching the name decides"),
    ("RGP15", CommandLine(f"rg -u -g '!*ci-output' x {CK.parent}"), S, 0, "one glob matching both names"),
    ("RGP16", CommandLine("rg -u --ignore x tools"), CK, 0, "--ignore turns ignore files back on"),
    ("RGP17", CommandLine("rg --no-ignore-vcs --ignore-vcs x tools"), CK, 0,
     "--ignore-vcs turns VCS ignore files back on"),
    ("RGP18", CommandLine("rg -u --ignore -u x tools"), CK, 0, "a second -u turns on hidden files only"),
    ("RGP19", CommandLine("rg -- -u tools"), CK, 0, "-u after -- is the pattern"),
    ("RGP20", CommandLine("rg -u --regexp tools x"), CK, 0, "--regexp takes the next word for the pattern"),
    ("RGP21", CommandLine("rg x "
                          "tools/ci-output/run1"), CK, 0, "a path inside a record directory neither is nor holds one"),
    ("RGP22", CommandLine("rg -u -e tools x"), CK, 0, "-e takes the next word for the pattern"),
    ("RGP23", CommandLine("rg -u -f tools x"), CK, 0, "-f takes the next word for its pattern file"),
    ("RGP24", CommandLine("rg -eunused tools"), CK, 0, "-e's value is the rest of its word, read as no letters"),
    ("RGP25", CommandLine("rg -u --file tools x"), CK, 0, "--file takes the next word for its pattern file"),
    ("FND1", CommandLine("find tools -type f -exec grep -l x {} +"), CK, 2,
     "an -exec running grep on what tools holds"),
    ("FND2", CommandLine("find . -type f -exec cat {} \\;"), CK, 2, "an -exec ending at `;`, from the checkout's root"),
    ("FND3", CommandLine("find tools -execdir head -1 {} +"), CK, 2, "-execdir"),
    ("FND4", CommandLine("find tools -ok tail -1 {} \\;"), CK, 2, "-ok"),
    ("FND5", CommandLine("find tools -okdir sed -n 1p {} \\;"), CK, 2, "-okdir"),
    ("FND6", CommandLine("find tools -type f | xargs grep "
                         "x"), CK, 2, "the paths a find prints, piped to xargs running grep"),
    ("FND7", CommandLine("find tools -type f -print0 | xargs -0 wc -l"), CK, 2, "-print0 into xargs -0 running wc"),
    ("FND8", CommandLine("find tools -type f | grep -v test | xargs awk 1"), CK, 2,
     "a filter between the find and the xargs"),
    ("FND9", CommandLine(f"find {CK.parent} -type f -exec cat {{}} +"), S, 2,
     "a start point holding both record directories"),
    ("FND10", CommandLine(f"find {CK.parent} -path '*/ci-output' -prune -o -type f -exec cat {{}} +"), S, 2,
     "one record directory pruned, the other read"),
    ("FND11", CommandLine("find tools/ci-output -type f -exec cat {} +"), CK, 2,
     "the record directory is the start point"),
    ("FND12", CommandLine("find . -maxdepth 3 -type f -exec cat {} +"), CK, 2,
     "-maxdepth 3 reaches the files tools/ci-output holds"),
    ("FND13", CommandLine("find . -not -path ./tools/ci-output -type f -exec cat {} +"), CK, 2,
     "-not -path naming the directory alone leaves what it holds"),
    ("FND14", CommandLine("find . -depth -path ./tools/ci-output -prune -o -type f -exec cat {} +"), CK, 2,
     "-depth turns -prune off"),
    ("FND15", CommandLine("find . -d -path ./tools/ci-output -prune -o -type f -exec cat {} +"), CK, 2,
     "-d, -depth's synonym"),
    ("FND16", CommandLine("find . -path ./tools/ci-output -prune -o -name '*.tmp' -delete -o -type f -exec cat {} +"),
     CK, 2, "-delete turns -prune off"),
    ("FND17", CommandLine(f"find -L {CK}/tools -exec cat {{}} +"), S, 2, "an option before the start point"),
    ("FND18", CommandLine(f"find -D stat {CK}/tools -exec cat {{}} +"), S, 2, "-D takes the next word"),
    ("FND19", CommandLine(f"find -O3 {CK}/tools -exec cat {{}} +"), S, 2, "-O3 is one word"),
    ("FND20", CommandLine("find tools -path ./tools/ci-output -prune -o -exec cat {} +"), CK, 2,
     "a -path spelled from another start point than the one find prints"),
    ("FND21", CommandLine("find tools -name output -prune -o -exec cat {} +"), CK, 2,
     "a -name matching no record directory's name"),
    ("FND22", CommandLine("find . \\( -path ./x -o -path ./y \\) -prune -o -exec cat {} +"), CK, 2,
     "a pruned group naming neither"),
    ("FND23", CommandLine("find tools -exec env LC_ALL=C grep x {} +"), CK, 2, "a reader behind a wrapper"),
    ("FND24", CommandLine("bash -c 'find tools -exec cat {} +'"), CK, 2, "a find in bash -c text"),
    ("FND25", CommandLine("python3 -c \"import subprocess; "
                          "subprocess.run(['find', 'tools', '-exec', 'cat', '{}', '+'])\""), CK, 2,
     "a find Python starts"),
    ("FND26", CommandLine("find tools -exec sh -c 'cat \"$1\"' _ {} \\;"), CK, 2,
     "a shell's -c text, no name-only program"),
    ("FND27", CommandLine("find . -path ./tools/ci-output -exec cat {} +"), CK, 2,
     "a -path with no -prune selects, never skips"),
    ("FND28", CommandLine("find . -not -name ci-output -exec cat {} +"), CK, 2,
     "-not -name leaves what the directory holds"),
    ("FND29", CommandLine("find tools -not -path 'tools/ci-output/?' -exec cat {} +"), CK, 2,
     "a negated -path matching names one level down only"),
    ("FND30", CommandLine("find tools -not -path 'tools/ci-output/*/*' -exec cat {} +"), CK, 2,
     "a negated -path matching names two levels down only"),
    ("FND31", CommandLine(f"cd {CK}/tools && find . -exec cat {{}} +"), S, 2,
     "a start point placed where a cd leaves the command"),
    ("FND32", CommandLine("find . -maxdepth 2 -maxdepth 3 -exec cat {} +"), CK, 2, "the last -maxdepth decides"),
    ("FND33", CommandLine(f"find -H {CK}/tools -exec cat {{}} +"), S, 2, "-H before the start point"),
    ("FND34", CommandLine(f"find -P {CK}/tools -exec cat {{}} +"), S, 2, "-P before the start point"),
    ("FND35", CommandLine("find -D stat -exec cat {} +"), CK, 2, "-D's value is no start point"),
    ("FND36", CommandLine("find -type f -exec cat {} +"), CK, 2, "no start point: find searches where it runs"),
    ("FND37", CommandLine("find . -type f -a -path './tools/ci-output/*' -exec cat {} +"), CK, 2, "-a negates nothing"),
    ("FND38", CommandLine("find . ! ! -path './tools/ci-output/*' -exec cat {} +"), CK, 2,
     "a second ! takes the first back"),
    ("FND39", CommandLine("find tools -exec ls {} \\; -exec cat {} +"), CK, 2,
     "`;` ends a name-only -exec, a reader's after it"),
    ("FND40", CommandLine("find tools -exec ls {} + -exec cat {} \\;"), CK, 2,
     "`+` ends a name-only -exec, a reader's after it"),
    ("FND41", CommandLine("find tools -exec cat {} \\; -exec ls {} \\;"), CK, 2,
     "a reader's -exec before a name-only one"),
    ("FND42", CommandLine("find tools -exec cat {} + -path"), CK, 2, "a -path missing its pattern, which find refuses"),
    ("FND43", CommandLine("find tools -exec cat {} + \\)"), CK, 2, "a `)` closing no group, which find refuses"),
    ("FND44", CommandLine("find . -maxdepth x -exec cat {} "
                          "+"), CK, 2, "a -maxdepth that is no number, which find refuses"),
    ("FND45", CommandLine("find . ! -type d -path './tools/ci-output/*' -exec cat {} +"), CK, 2,
     "a ! negates the operand after it only"),
    ("FND46", CommandLine("find tools -exec ls {} + | xargs cat"), CK, 2,
     "the -exec's own command read between find and xargs"),
    ("FND47", CommandLine("find tools/ci-output -maxdepth 1 -exec cat {} +"), CK, 2,
     "-maxdepth 1 opens the start point"),
    ("FND48", CommandLine("find . ! -name ci-output -type f -exec grep -n x {} +"), CK, 2,
     "a negated -name skips the directory's own entry, not the files find still reads beneath it"),
    ("FND49", CommandLine("find tools/* -exec cat {} "
                          "+"), CK, 2, "a start point holding a glob is read from its known part, tools"),
    ("FND50", CommandLine("find . ! -name '?' -exec cat {} +"), CK, 2,
     "a negated -name is no exclusion: files whose names it does not match are still read"),
    ("FNDP1", CommandLine("find tools -type f"), CK, 0, "a find listing names reads no contents"),
    ("FNDP2", CommandLine("find tools -name '*.py' -print"), CK, 0, "-name and -print read no contents"),
    ("FNDP3", CommandLine("find . -path ./tools/ci-output -prune -o -type f -exec grep x {} +"), CK, 0,
     "-path naming it with -prune"),
    ("FNDP4", CommandLine("find . -wholename ./tools/ci-output -prune -o -type f -exec cat {} +"), CK, 0, "-wholename"),
    ("FNDP5", CommandLine("find . -ipath ./TOOLS/CI-OUTPUT -prune -o -type f -exec cat {} +"), CK, 0,
     "-ipath, its case ignored"),
    ("FNDP6", CommandLine("find . -iwholename ./Tools/Ci-Output -prune -o -type f -exec cat {} +"), CK, 0,
     "-iwholename"),
    ("FNDP7", CommandLine("find . -name ci-output -prune -o -type f -exec cat {} +"), CK, 0, "-name with -prune"),
    ("FNDP8", CommandLine("find . -iname CI-OUTPUT -prune -o -type f -exec cat {} +"), CK, 0,
     "-iname, its case ignored"),
    ("FNDP9", CommandLine("find . -path ./tools -prune -o -type f -exec cat {} +"), CK, 0,
     "a directory above it pruned"),
    ("FNDP10", CommandLine("find . \\( -path ./x -o -path ./tools/ci-output \\) -prune -o -type f -exec cat {} +"), CK,
     0, "a pruned group of alternatives"),
    ("FNDP11", CommandLine("find . -path ./tools/ci-output -a -prune -o -type f -exec cat {} +"), CK, 0,
     "-a before -prune"),
    ("FNDP12", CommandLine("find . -path ./tools/ci-output -and -prune -o -type f -exec cat {} +"), CK, 0,
     "-and before -prune"),
    ("FNDP13", CommandLine("find . -not -path './tools/ci-output/*' -type f -exec cat {} +"), CK, 0,
     "-not -path naming what it holds"),
    ("FNDP14", CommandLine("find . ! -path '*/ci-output/*' -type f -exec cat {} +"), CK, 0, "! -path"),
    ("FNDP15", CommandLine("find . -not \\( -path '*/ci-output/*' -o -path '*/.git/*' \\) -type f -exec cat {} +"), CK,
     0, "-not over a group"),
    ("FNDP16", CommandLine("find . -maxdepth 2 -type f -exec cat {} +"), CK, 0,
     "-maxdepth 2 stops at tools/ci-output itself"),
    ("FNDP17", CommandLine("find tools/ -path tools/ci-output -prune -o -type f -exec cat {} +"), CK, 0,
     "a start point's closing slash, which find does not print twice"),
    ("FNDP18", CommandLine("find tools/ci-output -path tools/ci-output -prune -o -exec cat {} +"), CK, 0,
     "the start point pruned"),
    ("FNDP19", CommandLine(f"find {CK.parent} \\( -name ci-output -o -name aletheia-ci-output \\) -prune -o -exec cat "
                           f"{{}} +"), S, 0, "both record directories pruned, as the refusal shows"),
    ("FNDP20", CommandLine("find tools -type f | xargs"), CK, 0, "xargs with no command runs echo"),
    ("FNDP21", CommandLine("find tools -type f | wc -l"), CK, 0, "wc reads the names, not the files"),
    ("FNDP22", CommandLine("find tools/ci-output/run1 -exec cat {} +"), CK, 0,
     "a start point inside a record directory"),
    ("FNDP23", CommandLine("find python -exec cat {} +"), CK, 0, "a start point elsewhere"),
    ("FNDP24", CommandLine("find tools -type f; ls | xargs cat"), CK, 0, "an xargs in another pipeline"),
    ("FNDP25", CommandLine("find tools | (cat); ls | xargs cat"), CK, 0, "an xargs in a pipeline after a subshell"),
    ("FNDP26", CommandLine("find . -maxdepth 3 -maxdepth 2 -exec cat {} +"), CK, 0, "the last -maxdepth decides"),
    ("FNDP27", CommandLine("find . -path ./tools/ci-output -prune -o -type f -exec grep -d skip x {} +"), CK, 0,
     "an -exec's own words are not the expression's"),
    ("FNDP28", CommandLine("ls | { find tools; xargs cat; }"), CK, 0, "a find whose output reaches no pipe"),
    ("FNDP29", CommandLine("find tools/* -path tools/ci-output -prune -o -exec cat {} +"), CK, 0,
     "a start point holding a glob"),
    ("FNDP30", CommandLine("find rec-link -path rec-link/ci-output -prune -o -exec cat {} +"), S, 0,
     "a symlinked start point, printed as it is spelled"),
    ("FNDP31", CommandLine("find tools/ci-output -maxdepth 0 -exec cat {} +"), CK, 0,
     "-maxdepth 0 stops at the start point"),
    ("FNDP32", CommandLine("find tools -exec env LC_ALL=C ls {} +"), CK, 0, "a name-only program behind a wrapper"),
    ("FNDP33", CommandLine("find tools -path tools -prune -o -exec cat {} +"), CK, 0,
     "the start point holding it pruned"),
    ("FNDP34", CommandLine("find tools/* -path tools/ci-output -prune -o -exec cat {} +"), CK, 0,
     "a glob start point's records directory pruned by the path find prints for it"),
    ("FNDP35", CommandLine("find tools/$X -path tools/ci-output -prune -o -exec cat {} +"), CK, 0,
     "a start point read only as far as its known part, tools, prunes by the path find prints from there"),
    *[(f"FNDN{k}", f"find {CK.parent} -exec {shlex.quote(name)} {{}} +", S, 0, f"`{name}` reads no file's contents")
      for k, name in enumerate(["ls", "eza", "stat", "echo", "printf", "basename", "dirname", "realpath", "readlink",
                                "du", "test", "[", "true", "false", "rm", "rmdir", "mv", "ln", "touch", "chmod",
                                "chown", "mkdir"], 1)],
]


def limits_cases() -> list[CaseRow]:
    """Rules 5-6: the job count a self-sizing tool runs with, and the slice a systemd-run unit lands in.

    A refusal counts as 2 only when it is rule 5-6's; any other refusal reads as 3, so a case refused for
    its shape cannot pass for the limit's.
    """
    (S / "tidy.sh").write_text("run-clang-tidy-23 -quiet -p build src\n")
    (S / "tidy_bound.sh").write_text("run-clang-tidy-23 -j 4 -quiet -p build src\n")
    rows: list[CaseRow] = []
    for cid, cmd, want, what in LIMITS:
        rc, err = hook(GUARD, bash(cmd, Q))
        got = 2 if rc == 2 and "rules 5-6" in err else 3 if rc == 2 else rc
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), ExitStatus(got), Prose(what), err))
    HOOK_ENV["PYTHON_CPU_COUNT"] = "5"  # the session's own environment, which every call inherits
    try:
        rc, err = hook(GUARD, bash(CommandLine("run-clang-tidy-23 -p build src"), Q))
    finally:
        del HOOK_ENV["PYTHON_CPU_COUNT"]
    rows.append(CaseRow(CaseId("LIM26"), ExitStatus(0), rc,
                        Prose("PYTHON_CPU_COUNT=5 in the session's environment"), err))
    return rows


def search_cases() -> list[CaseRow]:
    """Rule 7: a recursive grep or an rg reads no run-record directory it does not skip, and a find hands no file
    beneath one it does not skip to a program that reads it.

    A refusal counts as 2 only when it is rule 7's; any other refusal reads as 3.
    """
    link = S / "rec-link"
    if not link.is_symlink():
        link.symlink_to(CK / "tools")
    rows: list[CaseRow] = []
    for cid, cmd, cwd, want, what in SEARCHES:
        rc, err = hook(GUARD, bash(cmd, cwd))
        got = 2 if rc == 2 and "rule 7" in err else 3 if rc == 2 else rc
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), ExitStatus(got), Prose(what), err))
    rc, err = hook(GUARD, bash(CommandLine(f"rg -n x {CK.parent}"), S))
    told = all(s in err for s in (f"reads {SIB},", "which no -g '!aletheia-ci-output' skips", "git grep -n"))
    rows.append(CaseRow(CaseId("RGM1"), ExitStatus(2), ExitStatus(2 if rc == 2 and told else 3 if rc == 2 else rc),
                        Prose("the refusal names the directory, the -g that skips it, and git grep"), err))
    rc, err = hook(GUARD, bash(CommandLine("find . -type f -exec grep -n x {} +"), CK))
    told = all(s in err for s in (f"reads {CK}/tools/ci-output,", "which no `-path ./tools/ci-output -prune` skips",
                                  "\\( -name aletheia-ci-output -o -name ci-output \\) -prune -o", "git grep -n"))
    rows.append(CaseRow(CaseId("FNDM1"), ExitStatus(2), ExitStatus(2 if rc == 2 and told else 3 if rc == 2 else rc),
                        Prose("the refusal names the directory, the -prune that skips it, and git grep"), err))
    rc, err = hook(GUARD, bash(CommandLine("find tools/ci-output -exec cat {} +"), CK))
    told = (f"reads {CK}/tools/ci-output, the directory `tools/ci-output` names, which no "
            "`-path tools/ci-output -prune` skips") in err
    rows.append(CaseRow(CaseId("FNDM2"), ExitStatus(2), ExitStatus(2 if rc == 2 and told else 3 if rc == 2 else rc),
                        Prose("the refusal names a start point that is the record directory"), err))
    return rows


def sw_case() -> CaseRow:
    live = start([Word("cargo"), Word("test")], T)
    try:
        rc, err = hook(GUARD, bash(CommandLine("python3 -c \"import re; re.compile('\\d'); "
                                               "open('CHANGELOG.md', 'w')\""), T))
    finally:
        stop(live)
    got = int(rc == 2 and err.lstrip().startswith("BLOCKED"))
    return CaseRow(CaseId("SW1"), ExitStatus(1), ExitStatus(got),
                   Prose("a SyntaxWarning in the code stays off the hook's stderr"), Prose(err[:80]))


def round6_cases() -> list[CaseRow]:
    (S / "e1.sh").write_text("echo safe\n")
    (S / "e2.sh").write_text("echo safe\n")
    (S / "e3.sh").write_text("echo safe\n")
    (BASE / "p6-other.sh").write_text(f"echo x > {T}/CHANGELOG.md\n")
    (Q / "probes" / "run_all.sh").write_text("for p in probes/*.sh; do bash \"$p\"; done\n")
    (S / "ra3.sh").write_text((Q / "probes" / "run_all.sh").read_text())
    (RT / "probes").mkdir(parents=True, exist_ok=True)
    git(RT, Word("init"), Word("-q"))
    (RT / "probes" / "run_all.sh").write_text((Q / "probes" / "run_all.sh").read_text())
    (RT / "probes" / "light_probe.sh").write_text("echo ok\n")
    (Q / "probes" / "heavy_probe.sh").write_text("cargo build\n")
    (Q / "probes" / "capturing_probe.sh").write_text('out=$(cargo build)\necho "$out" | tail -1\n')
    (S / "capturing.sh").write_text((Q / "probes" / "capturing_probe.sh").read_text())
    for n, make in ((5, 'cp probes/capturing_probe.sh "$d/p.sh"'),
                    (6, "sed 's/-1/-2/' probes/capturing_probe.sh > \"$d/p.sh\""),
                    (7, 'cp probes/capturing_probe.sh "$d/p.sh"; echo "echo more" >> "$d/p.sh"'),
                    (8, f'cp {S}/capturing.sh "$d/p.sh"'),
                    (9, "sed -i 's/-1/-2/' probes/capturing_probe.sh; cp probes/capturing_probe.sh \"$d/p.sh\"")):
        (S / f"wrap{n}.sh").write_text(f'd=$(mktemp -d)\n{make}\nbash "$d/p.sh" > "$d/out.log" 2>&1\nrm -rf "$d"\n')
    (Q / "probes" / "light_probe.sh").write_text("echo ok\n")
    (S / "a7.sh").write_text(f"echo x > {T}/CHANGELOG.md\n")
    (S / "b7.sh").write_text("echo b\n")
    (S / "c7.sh").write_text("echo harmless\n")
    (S / "w7.sh").write_text(f"echo x > {T}/CHANGELOG.md\n")
    (S / "hi7.sh").write_text("echo hi\n")
    (S / "py7.py").write_text("print(1)\n")
    (S / "src7.sh").write_text("echo banner\ncargo test\n")
    (S / "src8.sh").write_text("cargo test\necho end\n")
    (T / "probes").mkdir(exist_ok=True)
    (T / "probes" / "copy_build.sh").write_text('scratch=$(mktemp -d)\ncp -r . "$scratch"\n'
                                                'cd "$scratch" && cargo test\n')
    for k in range(1, 4):
        (S / f"n{k}.sh").write_text(f"bash {S}/n{k + 1}.sh\n")
    (S / "n4.sh").write_text(f"bash -c 'bash -c \"echo x > {T}/CHANGELOG.md\"'\n")
    rows: list[CaseRow] = []
    for cid, event, want, what in R6_QUIET:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), Prose(ascii(err))))
    for cid, event, want, what in R6_LOOPS:
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), Prose(ascii(err))))
    t0 = time.monotonic()  # a trim tries every cut: a long value is left unknown, not tried
    rc, err = hook(GUARD, bash(CommandLine("V=" + "a" * 250_000 + "; echo ${V%%*b}"), T))
    rows.append(CaseRow(CaseId("V22"), ExitStatus(1), ExitStatus(int(rc == 0 and time.monotonic() - t0 < 2)),
                        Prose(f"a trim of 250 KB ({time.monotonic() - t0:.2f}s)"), err))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in R6_LIVE:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), Prose(ascii(err))))
    finally:
        stop(live)
    return rows


def dup_cases() -> list[CaseRow]:
    """A loop read once per value repeats its body's problems: each is listed once."""
    rc, err = hook(GUARD, bash(CommandLine("for f in a b c d e f g h i; do cargo test; done"), Q))
    bullets = [ln for ln in err.splitlines() if ln.strip().startswith("*")]
    return [CaseRow(CaseId("DUP0"), ExitStatus(2), rc, Prose("cargo test in a nine-value loop is refused"), err),
            CaseRow(CaseId("DUP1"), ExitStatus(0), ExitStatus(int(len(bullets) != len(set(bullets)))),
                    Prose("a loop's repeated problem is listed once"), err)]


def round7_cases() -> list[CaseRow]:
    """What the budget carries, loops too long to unroll, and the node budget."""
    rows: list[CaseRow] = []
    many = "X='" + ":;" * 6000 + "'"
    rc, err = hook(GUARD, bash(CommandLine(f"C=cargo; $C test; {many}; eval \"$X\""), Q))
    rows.append(CaseRow(CaseId("BUD1"), ExitStatus(2), rc,
                        Prose("a heavy command read before the budget ran out is refused by name"), err))
    rc, err = hook(GUARD, bash(CommandLine("[[ a ]]; " * 20001 + "cargo test"), Q))
    rows.append(CaseRow(CaseId("NODE1"), ExitStatus(1), ExitStatus(int("nodes to read" in err)),
                        Prose("twenty thousand tests spend the node budget"), Prose(err[-300:])))
    live = start([Word("cargo"), Word("test")], T)
    try:
        rc, err = hook(GUARD, bash(CommandLine(f"cd {S} && for f in a b c d e f g h i; do echo x > \"$f.log\"; done"),
                                   T))
        rows.append(CaseRow(CaseId("LV1"), ExitStatus(0), rc,
                            Prose("nine values are read one by one: the writes land in the scratch directory"), err))
        values = " ".join(f"{S}/v{k}.log" for k in range(70))
        rc, err = hook(GUARD, bash(CommandLine(f"for f in {values}; do echo x > \"$f\"; done"), T))
        rows.append(CaseRow(CaseId("LV2"), ExitStatus(0), rc,
                            Prose("seventy values, read once: what they share places the path"), err))
    finally:
        stop(live)
    return rows


# ─── round seven: the round-six findings still open ─────────────────────────────
PYSUITE = S / "suite7.py"  # an argv chosen by if/else, started through a starred list
R7_LOOPS = [
    ("J7a", bash(CommandLine("trap 'kill $(jobs -p)' EXIT\nsleep 999 &\necho work"), Q), 2,
     "zsh's $(jobs -p) runs in a subshell and names no job: the job runs on"),
    ("J7b", bash(CommandLine("cleanup() { kill $(jobs -p); }; trap cleanup EXIT\nsleep 999 &\necho work"), Q), 2,
     "the same through a trap function"),
    ("J7c", bash(CommandLine("sleep 998 &\necho work\npkill -P $$"), Q), 0, "pkill -P $$ stops this shell's children"),
    ("J7d", bash(CommandLine("sleep 998 & sleep 999 &\necho work\npkill -P $$"), Q), 0, "every child"),
    ("J7e", bash(CommandLine("trap 'trap \"\" TERM; kill -TERM -$$' EXIT\nsleep 998 &\necho work"), Q), 0,
     "kill -TERM -$$ in an EXIT trap stops the group"),
    ("J7n", bash(CommandLine("sleep 999 &\necho work\npkill -0 -P $$"), Q), 2,
     "pkill -0 -P $$ tests the children; it stops none"),
    ("J7o", bash(CommandLine("trap 'pkill -P $$' EXIT\nsleep 998 & sleep 999 &\necho work"), Q), 0,
     "pkill -P $$ in an EXIT trap stops every child"),
    ("J7f", bash(CommandLine("sleep 998 & sleep 999 &\necho work\nkill %%"), Q), 2, "kill %% stops one of two jobs"),
    ("J7g", bash(CommandLine("sleep 998 & sleep 999 &\necho work\nkill %+"), Q), 2, "kill %+ stops one of two jobs"),
    ("J7h", bash(CommandLine("bash -c 'sleep 998 & sleep 999 & kill %2'"), Q), 2, "kill %2 in bash stops one of two"),
    ("J7i", bash(CommandLine("sleep 997 & sleep 998 & sleep 999 &\necho work\nkill %1 %2"), Q), 2,
     "two of three jobs stopped"),
    ("J7j", bash(CommandLine("sleep 999 & kill -0 0 && echo alive"), Q), 2,
     "kill -0 0 tests the group; it stops nothing"),
    ("J7k", bash(CommandLine("sleep 999 & kill -s 0 0"), Q), 2, "kill -s 0 0 stops nothing"),
    ("J7l", bash(CommandLine("sleep 999 & kill -CONT 0"), Q), 2, "SIGCONT stops nothing"),
    ("J7m", bash(CommandLine("sleep 999 & kill -0 -- -$$"), Q), 2, "kill -0 of the group stops nothing"),
    ("B7a", bash(CommandLine("until curl -sf http://127.0.0.1:8765/ >/dev/null; do :; done"), Q), 2,
     "a busy loop on curl"),
    ("B7b", bash(CommandLine("until nc -z 127.0.0.1 8765; do :; done"), Q), 2, "a busy loop on nc -z"),
    ("B7c", bash(CommandLine("until [ -f /tmp/b7-flag ]; do continue; done"), Q), 2, "a busy loop on continue"),
    ("B7d", bash(CommandLine("until gh run view 123 --json status -q .status | grep -q completed; do :; done"), Q), 2,
     "a busy loop on a pipeline"),
    ("B7e", bash(CommandLine("while read l; do echo \"$l\"; done < /tmp/b7-in\nuntil [ -f /tmp/b7-flag ]; do :; done"),
                 Q), 2, "a busy loop after a read loop"),
    ("B7f", bash(CommandLine("while ! [ -f /tmp/b7-flag ]; do python3 -c 'import time; time.sleep(1)'; done"), Q), 2,
     "a loop whose delay is Python's sleep"),
    ("B7g", bash(CommandLine("while ! [ -f /tmp/b7-flag ]; do perl -e 'sleep 1'; done"), Q), 2,
     "a loop whose delay is perl's sleep"),
    ("B7h", bash(CommandLine("while read l; do echo \"$l\"; done < /tmp/b7-in"), Q), 0, "a read loop is not polling"),
    ("TR7", bash(CommandLine("trap -- 'trap \"\" TERM; kill 0' EXIT\nsleep 999 &\necho work"), Q), 0,
     "trap -- ACTION EXIT: the -- ends trap's options"),
    ("MN7", monitor(CommandLine("ps aux | grep -q -e 'cargo test' -e '[r]ustc'"), T), 2,
     "each -e pattern is tried: the first matches this script's own shell"),
    ("MN7b", monitor(CommandLine("ps aux | grep -q -e '[c]argo test' -e '[r]ustc'"), T), 0, "both patterns bracketed"),
    ("P7a", bash(CommandLine("python3 - <<'PY'\nimport os, time\nfor _ in range(600):\n"
                             "    if os.path.exists('/tmp/p7'):\n        break\n    time.sleep(1)\nPY"), Q), 2,
     "a Python for loop polling, as its shell twin"),
]
R7_QUIET = [
    ("X7a", bash(CommandLine("printf 'a\\x\\n' > /tmp/x7-ok.txt"), Q), 0, "a lone \\x is read, not a fault"),
    ("X7b", bash(CommandLine("echo 'use \\x here' > /tmp/x7-p.txt; cargo test"), Q), 2,
     "past a lone \\x the cargo test is read"),
    ("X7c", bash(CommandLine("python3 -c \"import subprocess, sys; subprocess.run(['ls', \\\"a'\\\" + sys.argv[1]], "
                             "executable='/bin/ls')\" x"), Q), 0,
     "an unbalanced quote in a started argv is not a fault"),
    ("X7d", bash(CommandLine(f"python3 {PYSUITE} --full"), Q), 2, "a starred argv chosen by if/else is read both ways"),
    ("PR7", bash(CommandLine(f"print -r -- 'cargo test 2>&1 | tail -5' > {S}/pr7.sh; bash {S}/pr7.sh"), Q), 2,
     "zsh's print writes the script as echo would"),
]
NEST = [  # calls the reader cannot follow: the fallback scan finds the heavy command after any opener
    ("NS1", bash(CommandLine("echo " + "$(" * 9 + "cargo test" + ")" * 9), Q), 2, "cargo test nine substitutions deep"),
    ("NS2", bash(CommandLine("{ " * 260 + "cargo test; " + "} " * 260), Q), 2, "cargo test in 260 nested groups"),
    ("NS3", bash(CommandLine("( " * 260 + "cargo test" + " )" * 260), Q), 2, "cargo test in 260 nested subshells"),
    ("NS4", bash(CommandLine("if true; then " * 210 + "cargo test; " + "fi; " * 210), Q), 2,
     "cargo test in 210 nested ifs"),
    ("NS5", bash(CommandLine('for p in a b; do cargo test -p $p; done; echo "$(("'), Q), 2,
     "after `do`, in an unreadable call"),
    ("NS6", bash(CommandLine("{ cargo test; }; echo $(("), Q), 2, "after `{`, in an unreadable call"),
    ("NS7", bash(CommandLine("R=$(cargo test 2>&1); echo $(("), Q), 2, "after `$(`, in an unreadable call"),
    ("NS8", bash(CommandLine("R=`cargo test 2>&1`; echo $(("), Q), 2, "after a backquote, in an unreadable call"),
    ("NS9", bash(CommandLine("repeat 2 cargo test; echo $(("), Q), 2, "zsh's repeat, in an unreadable call"),
    ("NS10", bash(CommandLine("for x (a b) cargo test; echo $(("), Q), 2, "zsh's short for, in an unreadable call"),
    ("NS11", bash(CommandLine("cat > /tmp/ns11-doc.md <<'EOF'\ncargo test --release\nEOF\necho $(("), Q), 0,
     "a heredoc's data line is not a statement"),
    ("NS12", bash(CommandLine("if cargo test; then echo ok; fi; echo $(("), Q), 2,
     "an if's condition, in an unreadable call"),
    ("NS13", bash(CommandLine("echo $((1<<x))\ncargo test\necho $(("), Q), 2,
     "a shift in arithmetic is no heredoc: the lines after it are still read"),
]
R7_XARGS = [  # a run is live in T; the call runs in the scratch directory S unless it says otherwise
    ("XA1", bash(CommandLine(f"find {T}/tools -name '*.py' -print0 | xargs -0 sed -i s/a/b/"), S), 2,
     "find's root places the operands xargs hands to sed -i"),
    ("XA2", bash(CommandLine(f"xargs sed -i s/a/b/ < {S}/xargs-list.txt"), S), 2,
     "a list read on stdin names the live file"),
    ("XA3", bash(CommandLine(f"xargs -a {S}/xargs-list.txt sed -i s/a/b/"), S), 2,
     "a list given by -a names the live file"),
    ("XA4", bash(CommandLine(f"git -C {T} ls-files | xargs sed -i s/a/b/"), S), 2,
     "operands from a producer the reader cannot follow land in no known place, in a call naming the live tree"),
    ("XA5", bash(CommandLine(f"find {S} -name '*.log' | xargs rm -f"), T), 0,
     "find's root outside the live tree: the removals land there"),
    ("XA6", bash(CommandLine(f"find {T}/tools -name '*.py' | xargs grep -n x"), S), 0,
     "xargs running a reader writes nothing"),
    ("XA7", bash(CommandLine(f"git -C {S} ls-files | xargs sed -i s/a/b/"), T), 0,
     "names git lists lie in its own directory, outside the live tree the call runs in"),
    ("XA8", bash(CommandLine(f"grep -rl x {T}/tools | xargs sed -i s/a/b/"), S), 2,
     "operands from a producer the reader cannot follow, in a call naming the live tree"),
    ("H5b", bash(CommandLine(f"bash <<'EOF'\ncat > {S}/p5.py <<'X'\nopen('{T}/CHANGELOG.md', 'w').write('x')\nX\n"
                             f"python3 {S}/p5.py\nEOF"), S),
     2, "a heredoc inside a carrier's heredoc is the script it writes, not both bodies joined"),
]
RUNNER = BASE / "runner-repo" / "probes" / "run_all.sh"  # heavy by its name alone: its one line runs nothing heavy
R7_COPIES = [  # a copy of a heavy runner is the runner, however it was made; only provenance can tell here
    ("CO1", bash(CommandLine(f"cat <<'EOF' | tee {S}/co1.sh >/dev/null\ncargo test | tail -5\nEOF\nbash {S}/co1.sh"),
                 Q), 2, "a heredoc piped into tee writes the script"),
    ("CO2", bash(CommandLine(f"cat {RUNNER} | tee {S}/co2.sh >/dev/null; bash {S}/co2.sh"), Q), 2,
     "cat piped into tee copies the runner"),
    ("CO3", bash(CommandLine(f"git -C {RUNNER.parent.parent} show HEAD:probes/run_all.sh > {S}/co3.sh && "
                             f"bash {S}/co3.sh"), Q), 2, "git show REV:PATH is a near copy of PATH"),
    ("CO4", bash(CommandLine(f"sed -n '1,$p' {RUNNER} > {S}/co4.sh && bash {S}/co4.sh"), Q), 2,
     "a whole-file sed -n keeps its origin"),
    ("CO5", bash(CommandLine(f"tr a a < {RUNNER} > {S}/co5.sh && bash {S}/co5.sh"), Q), 2,
     "tr from a file is a near copy"),
    ("CO6", bash(CommandLine(f"cp {RUNNER} {S}/co6.sh && echo 'echo hi' > {S}/co6.sh && bash {S}/co6.sh"), Q), 0,
     "a copy overwritten with other text is no longer the runner"),
    ("CO7", bash(CommandLine(f"tail -n +0 {RUNNER} > {S}/co7.sh; bash {S}/co7.sh"), Q), 2,
     "tail -n +0 is the whole file"),
    ("CO8", bash(CommandLine(f"cp {RUNNER} {S}/co8.sh && bash {S}/co8.sh"), Q), 2, "a cp is the runner (the control)"),
]
R7_ZOXIDE = [  # the tool shell's cd is zoxide's: a name that is no directory where the call stands is looked up
    ("ZO1", bash(CommandLine("cd python && touch zo-marker.txt"), S), 2, "zoxide's cd lands in the live tree it knows"),
    ("ZO2", bash(CommandLine("cd zo-nowhere && touch zo-marker.txt"), S), 0,
     "a name zoxide does not know leaves the cd where it was"),
    ("ZO3", bash(CommandLine("mkdir -p python && cd python && touch zo-marker.txt"), S), 0,
     "a directory the call makes is entered"),
    ("ZO4", bash(CommandLine("bash -c 'cd python && touch zo-marker.txt'"), S), 0, "bash's cd is the builtin"),
]
R7_ARRAYS = [  # a literal array's elements are its values: $a all of them in zsh, the first in bash
    ("AR1", bash(CommandLine(f"logs=({S}/ar-a.log {S}/ar-b.log); touch $logs"), T), 0,
     "zsh's $logs: every element, all outside the tree"),
    ("AR2", bash(CommandLine(f'logs=({S}/ar-a.log {S}/ar-b.log); rm -f "${{logs[@]}}"'), T), 0,
     '"${logs[@]}": every element'),
    ("AR3", bash(CommandLine(f"files=({T}/CHANGELOG.md); touch $files"), S), 2,
     "an element in the live tree is written"),
    ("AR4", bash(CommandLine(f'files=({T}/CHANGELOG.md {T}/README.md); sed -i s/a/b/ "${{files[@]}}"'), S), 2,
     "elements in the live tree edited in place"),
    ("AR5", bash(CommandLine(f"bash -c 'f=({T}/CHANGELOG.md {S}/ar-x); touch $f'"), S), 2,
     "bash's $f is the first element: live"),
    ("AR6", bash(CommandLine(f"bash -c 'f=({S}/ar-x {T}/CHANGELOG.md); touch $f'"), S), 0,
     "bash's $f is the first element: outside"),
    ("AR7", bash(CommandLine(f"logs=({T}/CHANGELOG.md); logs={S}/ar-ok.log; touch $logs"), S), 0,
     "a name given a plain value holds no array any more"),
]
R7_LIVE = [
    ("W7a", bash(CommandLine("python3 -c \"import gzip; gzip.open(filename='CHANGELOG.md.gz', mode='wb')\""), T), 2,
     "gzip.open with its file as a keyword writes"),
    ("D7", bash(CommandLine('x=$(echo ${D7:=/tmp/}); echo y > "${D7}CHANGELOG.md"'), T), 2,
     "${V:=w} inside $( ) assigns in the subshell only: the parent's V stays unset"),
]


def settings_cases() -> list[CaseRow]:
    """The events and tools the hooks handle reach them: settings.json registers each.  It reads this profile's
    own ~/.claude/settings.json, so it checks the registration in force here, not a copy."""
    settings = Path.home() / ".claude" / "settings.json"
    try:
        hooks = json.loads(settings.read_text()).get("hooks", {})
    except (OSError, ValueError) as exc:
        return [CaseRow(CaseId("REG0"), ExitStatus(0), ExitStatus(1), Prose(f"settings.json unreadable: {exc}"),
                        Prose(""))]
    def tools(event: EventName, script: PathText) -> set[Matcher]:
        return {Matcher(t) for e in hooks.get(event, []) for h in e.get("hooks", []) if script in h.get("command", "")
                for t in e.get("matcher", "").split("|")}
    rows: list[CaseRow] = []
    want: dict[tuple[EventName, PathText], set[Matcher]] = {
        ("PreToolUse", PathText("heavy-run-guard.py")): {Matcher("Bash"), Matcher("Monitor"), Matcher("Edit"),
                                                         Matcher("Write"), Matcher("NotebookEdit"),
                                                         Matcher("MultiEdit"), Matcher("EnterWorktree"),
                                                         Matcher("ExitWorktree"), Matcher("Agent")},
        ("PostToolUse", PathText("heavy-run-guard.py")): {Matcher("Bash"), Matcher("Monitor")},
        ("PostToolUseFailure", PathText("heavy-run-guard.py")): {Matcher("Bash"), Matcher("Monitor")},
        ("PreToolUse", PathText("no-polling-loops.py")): {Matcher("Bash"), Matcher("Monitor")}}
    for k, ((event, script), need) in enumerate(want.items(), 1):
        missing = sorted(need - tools(event, script))
        rows.append(CaseRow(CaseId(f"REG{k}"), ExitStatus(0), ExitStatus(len(missing)),
                            Prose(f"{event} routes {sorted(need)} to {script}"), Prose(f"missing {missing}")))
    return rows


def budget_cases() -> list[CaseRow]:
    """The byte budget charges each text once per call: a script read again with other words, and a $( ) body
    read as the text around it, cost nothing more; two different large texts still spend it."""
    big = "# " + "x" * 200_000 + "\necho {}\n"  # under the 256 KiB a script is read up to
    for k in (1, 2, 3):
        (S / f"big{k}.sh").write_text(big.format(k))
    spent = "bytes of text to read"
    rc, err = hook(GUARD, bash(CommandLine(f"bash {S}/big1.sh a; bash {S}/big1.sh b; bash {S}/big1.sh c; cargo test"),
                               Q))
    rows = [CaseRow(CaseId("BU1"), ExitStatus(0), ExitStatus(int(spent in err)),
                    Prose("one 200 KB script read with three sets of words is charged once"), Prose(err[-300:]))]
    rc, err = hook(GUARD, bash(CommandLine(f"bash {S}/big1.sh; bash {S}/big2.sh; bash {S}/big3.sh; cargo test"), Q))
    rows.append(CaseRow(CaseId("BU2"), ExitStatus(1), ExitStatus(int(spent in err)),
                        Prose("three different 200 KB scripts spend the budget"), Prose(err[-300:])))
    rc, err = hook(GUARD, bash(CommandLine("Y=$(: " + "a " * 150_000 + "); cargo test"), Q))
    rows.append(CaseRow(CaseId("BU3"), ExitStatus(0), ExitStatus(int(spent in err)),
                        Prose("a 300 KB $( ) body is charged with the text around it"), Prose(err[-300:])))
    (Q / "probes").mkdir(exist_ok=True)  # a repository probe running a heavy runner with sixteen sets of words
    (Q / "probes" / "runner7.sh").write_text("".join(f'echo "$1" {k}\n' for k in range(400)) + "cargo test\n")
    (Q / "probes" / "p7.sh").write_text("".join(f"bash {Q}/probes/runner7.sh {k}\n" for k in range(16)))
    rc, err = hook(GUARD, bash(CommandLine(BLESSED.format("./probes/p7.sh")), Q))
    rows.append(CaseRow(CaseId("BU4"), ExitStatus(0), rc,
                        Prose("a repository probe reading its runner sixteen times, run in the blessed shape"),
                        Prose(err[-300:])))
    t0 = time.monotonic()
    rc, err = hook(GUARD, bash(CommandLine("ls; " * 7990 + "cargo test"), Q))
    dt = time.monotonic() - t0
    rows.append(CaseRow(CaseId("BU5"), ExitStatus(1),
                        ExitStatus(int(rc == 2 and "commands to read" not in err and dt < 2)),
                        Prose(f"a call of 7990 commands is read whole, in {dt:.2f}s"), Prose(err[-300:])))
    return rows


def r7_open_cases() -> list[CaseRow]:
    PYSUITE.write_text("import subprocess, sys\nTESTS = ['tests/'] if '--full' in sys.argv else ['tests/test_a.py']\n"
                       "subprocess.run([sys.executable, '-m', 'pytest', *TESTS])\n")
    (S / "xargs-list.txt").write_text(f"{T}/CHANGELOG.md\n")
    (T / "python").mkdir(exist_ok=True)
    subprocess.run(["zoxide", "add", str(T / "python")], env=HOOK_ENV, check=False)
    rows = settings_cases()
    for cid, event, want, what in R7_LOOPS:
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), Prose(ascii(err))))
    RUNNER.parent.mkdir(parents=True, exist_ok=True)
    RUNNER.write_text("echo runner\necho done\n")
    git(RUNNER.parent.parent, Word("init"), Word("-q"))
    for cid, event, want, what in R7_QUIET + NEST + R7_COPIES:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), Prose(ascii(err))))
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in R7_LIVE + R7_XARGS + R7_ZOXIDE + R7_ARRAYS:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), Prose(ascii(err))))
    finally:
        stop(live)
    return rows


def shape_cases() -> list[CaseRow]:
    """The events whose shape alone decides the answer."""
    rows: list[CaseRow] = []
    for cid, event, want, what in SHAPE:
        rc, err = hook(GUARD, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    return rows


def writes_cases() -> list[CaseRow]:
    """Writes and edits made while a `cargo test` runs in the tree."""
    rows: list[CaseRow] = []
    live = start([Word("cargo"), Word("test")], T)
    try:
        for cid, event, want, what in WRITES + EDITS:
            rc, err = hook(GUARD, event)
            rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    finally:
        stop(live)
    return rows


def procs_cases() -> list[CaseRow]:
    """One edit while each command line in PROCS runs."""
    rows: list[CaseRow] = []
    (Q2 / "sub").mkdir(exist_ok=True)
    for cid, argv, cwd, want in PROCS:
        p = start(argv, cwd)
        try:
            rc, err = hook(GUARD, edit("Edit", PathText(f"{Q2}/f.txt"), Q2))
        finally:
            stop(p)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(" ".join(argv) + f" @{cwd.name}"), err))
    return rows




def hook_out(path: Path, event: HookEvent) -> HookOutput:
    p = subprocess.run([str(path)], input=json.dumps(event), capture_output=True, text=True, check=False,
                       timeout=60, env=HOOK_ENV)
    return HookOutput(ExitStatus(p.returncode), Prose(p.stdout), Prose(p.stderr))


def context_of(out: Prose) -> Prose:
    """The additionalContext a hook printed, or ""."""
    try:
        return Prose(json.loads(out)["hookSpecificOutput"]["additionalContext"])
    except (ValueError, KeyError, TypeError):
        return Prose("")


def ours(text: Prose) -> TreeCount:
    """How many of this suite's own trees a report names: a suite run beside another sees the other's runs."""
    return TreeCount(sum(str(tree) + "/" in text for tree in (T, Q, Q2, S, W)))


def after6_cases() -> list[CaseRow]:
    """The after-call check as redesigned: failures, blame by tree, and the session's baseline."""
    rows: list[CaseRow] = []
    x = T / "tools" / "x.py"
    live = start([Word("cargo"), Word("test")], T)
    try:
        def post(pre: HookEvent, name: EventName = "PostToolUse") -> HookOutput:
            return hook_out(GUARD, HookEvent(pre, hook_event_name=name, tool_response={}))

        pre = HookEvent(bash(CommandLine("python3 -c 'raise SystemExit(1)'"), T), tool_use_id=ToolUseId("a6-fail"),
                        session_id=SessionId("s-fail"))
        hook(GUARD, pre)
        os.utime(x, ns=(11, 11))
        rc, out, err = post(pre, "PostToolUseFailure")
        rows.append(CaseRow(CaseId("AF1"), ExitStatus(2), rc, Prose("a failed call's Post still checks what moved"),
                            err))
        pre = HookEvent(bash(CommandLine("echo hi"), S), tool_use_id=ToolUseId("a6-else"),
                        session_id=SessionId("s-else"))
        hook(GUARD, pre)
        os.utime(x, ns=(12, 12))
        rc, out, err = post(pre)
        rows.append(CaseRow(CaseId("AF3"), ExitStatus(0), rc,
                            Prose("a move in a tree the call neither runs in nor names is not blamed"), err))
        rows.append(CaseRow(CaseId("AF3c"), ExitStatus(1),
                            ExitStatus(int(str(x) in context_of(out) and "not necessarily" in context_of(out))),
                            Prose("it is reported beside the call as not necessarily its doing"), out))
        pre = HookEvent(bash(CommandLine(f"ls {Q}"), Q), tool_use_id=ToolUseId("a6-q"), session_id=SessionId("s-q"))
        hook(GUARD, pre)
        os.utime(x, ns=(13, 13))
        rc, out, err = post(pre)
        rows.append(CaseRow(CaseId("AF4"), ExitStatus(0), rc,
                            Prose("a call in another tree is not blamed for the run's tree"), err))
        pre = HookEvent(bash(CommandLine(f"cp {x} /tmp/af5-copy"), S), tool_use_id=ToolUseId("a6-named"),
                        session_id=SessionId("s-named"))
        hook(GUARD, pre)
        os.utime(x, ns=(14, 14))
        rc, out, err = post(pre)
        rows.append(CaseRow(CaseId("AF5"), ExitStatus(2), rc, Prose("a call naming the tree is blamed for its moves"),
                            err))
        # A background call's Post comes at launch: what it writes later is caught at the session's next call.
        pre = HookEvent(bash(CommandLine("sleep 5; git status --short"), T), tool_use_id=ToolUseId("a6-bg"),
                        session_id=SessionId("s-bg"))
        hook(GUARD, pre)
        rc, out, err = post(pre)
        rows.append(CaseRow(CaseId("AF6"), ExitStatus(0), rc, Prose("a background call's launch moved nothing"), err))
        os.utime(x, ns=(15, 15))
        nxt = HookEvent(bash(CommandLine("echo next"), S), tool_use_id=ToolUseId("a6-next"),
                        session_id=SessionId("s-bg"))
        rc, out, err = hook_out(GUARD, nxt)
        rows.append(CaseRow(CaseId("AF7"), ExitStatus(0), rc, Prose("the session's next call goes ahead"), err))
        rows.append(CaseRow(CaseId("AF7c"), ExitStatus(1),
                            ExitStatus(int(str(x) in context_of(out) and "last call ended" in context_of(out))),
                            Prose("and is told what moved since the previous call"), out))
        rc, out, err = post(nxt)
        rows.append(CaseRow(CaseId("AF8"), ExitStatus(0), ExitStatus(rc + ours(out)),
                            Prose("a move reported once is not reported again"), Prose(err + out)))
        other = HookEvent(bash(CommandLine("echo other"), S), tool_use_id=ToolUseId("a6-other"),
                          session_id=SessionId("s-other"))
        rc, out, err = hook_out(GUARD, other)
        rows.append(CaseRow(CaseId("AF9"), ExitStatus(0), ExitStatus(ours(out)),
                            Prose("a session's first call has no baseline to compare"), out))
        os.utime(x, ns=(16, 16))
        rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo later"), S), tool_use_id=ToolUseId("a6-later"),
                                                 session_id=SessionId("s-bg")))
        rows.append(CaseRow(CaseId("AF10"), ExitStatus(1), ExitStatus(int(str(x) in context_of(out))),
                            Prose("baselines are per session"), out))
        rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo quiet"), S), tool_use_id=ToolUseId("a6-quiet"),
                                                 session_id=SessionId("s-bg")))
        rows.append(CaseRow(CaseId("AF10b"), ExitStatus(1), ExitStatus(int(str(x) in context_of(out))),
                            Prose("a Pre whose Post never came leaves the baseline: "
                                  "its note may never have reached the model"), out))
    finally:
        stop(live)
    os.utime(x, ns=(17, 17))  # after the run ended, a move since the session's last call
    rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo after"), S), tool_use_id=ToolUseId("a6-ended"),
                                             session_id=SessionId("s-bg")))
    rows.append(CaseRow(CaseId("AF11"), ExitStatus(1), ExitStatus(int(str(x) in context_of(out))),
                        Prose("a run that ended is compared once more"), out))
    rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo again"), S), tool_use_id=ToolUseId("a6-ended2"),
                                             session_id=SessionId("s-bg")))
    rows.append(CaseRow(CaseId("AF12"), ExitStatus(0), ExitStatus(ours(out)), Prose("and then nothing is held"), out))
    live = start([Word("cargo"), Word("test")], T)
    try:
        pre = HookEvent(bash(CommandLine("touch /tmp/af13-stamp"), T), tool_use_id=ToolUseId("a6-twice"),
                        session_id=SessionId("s-twice"))
        hook(GUARD, pre)
        os.utime(x, ns=(18, 18))
        rc, out, err = hook_out(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
        rows.append(CaseRow(CaseId("AF13"), ExitStatus(2), rc, Prose("a move during the call"), err))
        rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo on"), S), tool_use_id=ToolUseId("a6-twice2"),
                                                 session_id=SessionId("s-twice")))
        rows.append(CaseRow(CaseId("AF13b"), ExitStatus(0), ExitStatus(ours(out)),
                            Prose("reported at its Post, not again at the next call"), out))
        pre = HookEvent(bash(CommandLine("cd ../live && touch /tmp/af14-stamp"), S), tool_use_id=ToolUseId("a6-cd"),
                        session_id=SessionId("s-cd"))
        hook(GUARD, pre)
        os.utime(x, ns=(19, 19))
        rc, out, err = hook_out(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
        rows.append(CaseRow(CaseId("AF14"), ExitStatus(2), rc, Prose("a call that cds into the tree is blamed"), err))
        state = BASE / "tmp" / "claude-heavy-run-guard"
        kept = state / "session-kept.json"
        kept.write_text(json.dumps({"trees": {}, "runs": []}))
        os.utime(kept, (time.time() - 3600, time.time() - 3600))
        gone = state / "session-s-gone.json"
        gone.write_text(json.dumps({"trees": {str(BASE / "no-such-tree"): {"f.py": [1, 1, 1]}}, "runs": []}))
        rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo gone"), S), tool_use_id=ToolUseId("a6-gone"),
                                                 session_id=SessionId("s-gone")))
        rows.append(CaseRow(CaseId("AF15"), ExitStatus(1), ExitStatus(int(kept.exists())),
                            Prose("a session's baseline outlives a call's snapshot"), Prose("")))
        rows.append(CaseRow(CaseId("AF16"), ExitStatus(0), ExitStatus(int("no-such-tree" in out)),
                            Prose("a tree removed since the baseline is not reported as moved"), out))
        pre = HookEvent(bash(CommandLine(f"cat {x}"), S), tool_use_id=ToolUseId("a7-read"),
                        session_id=SessionId("s-read"))
        hook(GUARD, pre)
        os.utime(x, ns=(20, 20))
        rc, out, err = hook_out(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
        rows.append(CaseRow(CaseId("AF17"), ExitStatus(0), rc,
                            Prose("a call that only reads is not blamed (the user's ruling, 2026-09-28)"), err))
        rows.append(CaseRow(CaseId("AF17c"), ExitStatus(1),
                            ExitStatus(int(str(x) in context_of(out) and "only reads" in context_of(out))),
                            Prose("it is told of the move beside the call"), out))
        a_pre = HookEvent(bash(CommandLine("touch /tmp/af18-a"), T), tool_use_id=ToolUseId("a7-A"),
                          session_id=SessionId("s-agents"), agent_id=AgentId("agentA"))
        hook(GUARD, a_pre)
        hook_out(GUARD, HookEvent(a_pre, hook_event_name="PostToolUse", tool_response={}))
        b_pre = HookEvent(bash(CommandLine("echo b"), S), tool_use_id=ToolUseId("a7-B"),
                          session_id=SessionId("s-agents"), agent_id=AgentId("agentB"))
        hook(GUARD, b_pre)
        hook_out(GUARD, HookEvent(b_pre, hook_event_name="PostToolUse", tool_response={}))
        os.utime(x, ns=(21, 21))
        rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo b2"), S), tool_use_id=ToolUseId("a7-B2"),
                                                 session_id=SessionId("s-agents"), agent_id=AgentId("agentB")))
        rows.append(CaseRow(CaseId("AF18"), ExitStatus(0), ExitStatus(ours(out)),
                            Prose("another agent of the session is not told of a tree it never touched"), out))
        rc, out, err = hook_out(GUARD, HookEvent(bash(CommandLine("echo a2"), S), tool_use_id=ToolUseId("a7-A2"),
                                                 session_id=SessionId("s-agents"), agent_id=AgentId("agentA")))
        rows.append(CaseRow(CaseId("AF18b"), ExitStatus(1), ExitStatus(int(str(x) in context_of(out))),
                            Prose("the agent whose calls ran in the tree is told"), out))
        other = BASE / "other7"
        other.mkdir(exist_ok=True)
        git(other, Word("init"), Word("-q"))
        (other / "f.txt").write_text("f\n")
        git(other, Word("add"), Word("f.txt"))
        run2 = start([Word("cargo"), Word("test")], other)
        try:
            pre = HookEvent(bash(CommandLine("touch /tmp/af19-stamp"), T), tool_use_id=ToolUseId("a7-rm"),
                            session_id=SessionId("s-rm"))
            hook(GUARD, pre)
            stop(run2)
            shutil.rmtree(other)
            os.utime(x, ns=(22, 22))
            rc, out, err = hook_out(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
            rows.append(CaseRow(CaseId("AF19"), ExitStatus(0), ExitStatus(int("other7" in err + out)),
                                Prose("a tree removed during the call is not listed"), err))
            rows.append(CaseRow(CaseId("AF20"), ExitStatus(0), ExitStatus(int(str(run2.pid) in err + out)),
                                Prose("a run whose tree moved nothing is not listed"), err))
        finally:
            if run2.poll() is None:
                stop(run2)
    finally:
        stop(live)
    return rows


def after_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    live = start([Word("cargo"), Word("test")], T)
    try:
        pre = HookEvent(bash(CommandLine("touch /tmp/e01-stamp"), T), tool_use_id=ToolUseId("t-moved"))
        hook(GUARD, pre)
        os.utime(T / "tools" / "x.py", ns=(1, 1))
        rc, err = hook(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
        rows.append(CaseRow(CaseId("E01"), ExitStatus(2), rc, Prose("a tracked file moved during the call"), err))
        rows.append(CaseRow(CaseId("E01m"), ExitStatus(1), ExitStatus(int("tools/x.py" in err)),
                            Prose("the report names it"), err))
        pre = HookEvent(bash(CommandLine("echo hi"), T), tool_use_id=ToolUseId("t-still"))
        hook(GUARD, pre)
        rc, err = hook(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
        rows.append(CaseRow(CaseId("E02"), ExitStatus(0), rc, Prose("nothing moved"), err))
        pre = HookEvent(bash(CommandLine("echo hi"), T), tool_use_id=ToolUseId("t-dribble"))
        hook(GUARD, pre)
        (T / ".commands-to-run.sh").write_text("#!/usr/bin/env bash\necho x\n")
        rc, err = hook(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
        rows.append(CaseRow(CaseId("E03"), ExitStatus(0), rc, Prose("only the dribble moved"), err))
        pre = HookEvent(bash(CommandLine("touch /tmp/e04-stamp"), T), tool_use_id=ToolUseId("t-new"))
        hook(GUARD, pre)
        (T / "tools" / "new_untracked.py").write_text("y = 2\n")
        rc, err = hook(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
        rows.append(CaseRow(CaseId("E04"), ExitStatus(2), rc, Prose("a new untracked file"), err))
    finally:
        stop(live)
    pre = HookEvent(bash(CommandLine("echo hi"), T), tool_use_id=ToolUseId("t-norun"))
    hook(GUARD, pre)
    os.utime(T / "tools" / "x.py", ns=(2, 2))
    rc, err = hook(GUARD, HookEvent(pre, hook_event_name="PostToolUse", tool_response={}))
    rows.append(CaseRow(CaseId("E05"), ExitStatus(0), rc, Prose("no run live: no snapshot, no report"), err))
    return rows


def loop_cases() -> list[CaseRow]:
    cases = [
        ("F01.2", monitor(CommandLine('log=/tmp/p.log\ntail -f -n +1 "$log" | grep -E --line-buffered "^FAIL " &\n'
                        "while kill -0 123 2> /dev/null; do sleep 15; done"), T), 2, "the orphaning Monitor"),
        ("F02", bash(CommandLine("sleep 1 & sleep 2 & wait"), T), 0, "jobs reaped by wait"),
        ("F03", bash(CommandLine("trap 'kill 0' EXIT\ntail -f x | grep y &\necho work"), T), 0, "a trap stops the job"),
        ("F04", bash(CommandLine("sleep 9 & disown"), T), 2, "disown"),
        ("F05.2", bash(CommandLine("echo a && echo b"), T), 0, "no job"),
        ("F06", bash(CommandLine("cat > /tmp/f <<'EOF'\nx &\nEOF"), T), 0, "& in heredoc data"),
        ("F07.2", monitor(CommandLine("tail -f log | grep --line-buffered X"), T), 0, "a foreground watch"),
        ("F08.2", bash(CommandLine("nohup sleep 9 > /tmp/x 2>&1 &"), T), 2, "nohup &"),
        ("F09.2", bash(CommandLine("until grep -q x f; do sleep 1; done"), T), 2, "a polling loop"),
        ("F10.3", monitor(CommandLine("pgrep -f foo"), T), 2, "self-matching pgrep in Monitor"),
        ("F11", monitor(CommandLine("while kill -0 123; do sleep 5; done"), T), 0, "Monitor's own loop"),
        ("F12.2", bash(CommandLine("echo 'a & b'"), T), 0, "& in a string"),
        ("F13", bash(CommandLine("wait; sleep 1 &"), T), 2, "a wait before the job does not reap it"),
    ]
    rows: list[CaseRow] = []
    for cid, event, want, what in cases:
        rc, err = hook(LOOPS, event)
        rows.append(CaseRow(CaseId(cid), ExitStatus(want), rc, Prose(what), err))
    return rows


def robustness_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for cid, raw, what in [
        ("G01", RawEvent("[]"), "an event that is a list"),
        ("G02", RawEvent(json.dumps({"tool_name": "Bash", "tool_input": "x"})), "tool_input a string"),
        ("G03", RawEvent(json.dumps({"tool_name": "Bash", "tool_input": {"command": None}})), "command null"),
        ("G04", RawEvent(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": 123}})), "file_path a number"),
        ("G05", RawEvent(json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": 5})),
         "cwd a number"),
        ("G06", RawEvent("\udcff"), "undecodable input"),
    ]:
        for path in (GUARD, LOOPS):
            try:
                rc, err = hook(path, raw)
            except UnicodeEncodeError:
                p = subprocess.run([sys.executable, str(path)], input=b"\xff\xfe", capture_output=True, check=False)
                rc, err = ExitStatus(p.returncode), Prose(p.stderr.decode(errors="replace"))
            rows.append(CaseRow(CaseId(f"{cid}{'g' if path == GUARD else 'l'}"), ExitStatus(0), rc, Prose(what), err))
    big = CommandLine("echo " + "a" * 1_000_000)
    t0 = time.monotonic()
    rc, err = hook(GUARD, bash(big, T))
    rows.append(CaseRow(CaseId("G07"), ExitStatus(0), rc, Prose(f"a 1 MB command ({time.monotonic() - t0:.2f}s)"), err))
    rows.append(CaseRow(CaseId("G07t"), ExitStatus(1), ExitStatus(int(time.monotonic() - t0 < 5)),
                        Prose("read within 5 s"), Prose("")))
    t0 = time.monotonic()
    for _ in range(10):
        hook(GUARD, bash(CommandLine("git status --short && ls tools"), T))
    per = (time.monotonic() - t0) / 10
    rows.append(CaseRow(CaseId("G08"), ExitStatus(1), ExitStatus(int(per < 0.5)),
                        Prose(f"a typical call costs {per * 1000:.0f} ms"), Prose("")))
    return rows


# A group of cases by name. A mutant list made for one rule names the groups that hold that rule's cases in
# GUARD_GROUPS (comma-separated), so each mutant runs those alone: the whole suite takes about 100 s, its search
# group a few. Unset, every group runs, in this order.
GroupName = NewType("GroupName", str)
GROUPS_ENV = "GUARD_GROUPS"
GROUPS = {
    GroupName("dup"): dup_cases, GroupName("round7"): round7_cases, GroupName("r7_open"): r7_open_cases,
    GroupName("budget"): budget_cases, GroupName("shape"): shape_cases, GroupName("writes"): writes_cases,
    GroupName("procs"): procs_cases, GroupName("after"): after_cases, GroupName("after6"): after6_cases,
    GroupName("loop"): loop_cases, GroupName("robustness"): robustness_cases, GroupName("extra"): extra_cases,
    GroupName("round2"): round2_cases, GroupName("pre_install"): pre_install_cases,
    GroupName("round4"): round4_cases, GroupName("round4_py"): round4_py_cases,
    GroupName("round4_param"): round4_param_cases, GroupName("round4_small"): round4_small_cases,
    GroupName("round4_cost"): round4_cost_cases, GroupName("claims"): claims_cases,
    GroupName("hotfix"): hotfix_cases, GroupName("zsh"): zsh_cases, GroupName("round5"): round5_cases,
    GroupName("robust5"): robust5_cases, GroupName("py5"): py5_cases, GroupName("small5"): small5_cases,
    GroupName("round6"): round6_cases, GroupName("limits"): limits_cases, GroupName("search"): search_cases,
    GroupName("sw"): lambda: [sw_case()],
}


def selected_groups() -> list[GroupName]:
    """Return the groups GUARD_GROUPS names, in its order, or every group when it is unset or empty.

    A name no group carries ends the run with exit 2, so a mistyped list never passes by running nothing.
    """
    named = [GroupName(word.strip()) for word in os.environ.get(GROUPS_ENV, "").split(",") if word.strip()]
    unknown = [name for name in named if name not in GROUPS]
    if unknown:
        print(f"{GROUPS_ENV} names no group: {', '.join(unknown)}; the groups are {', '.join(GROUPS)}")
        sys.exit(2)
    return named or list(GROUPS)


def run_cases() -> list[CaseRow]:
    rows: list[CaseRow] = []
    for name in selected_groups():
        rows += GROUPS[name]()
    return rows


def main() -> ExitStatus:
    try:
        setup()
        breaker_fixtures()
        round2_fixtures()
        round4_fixtures()
        round4_py_fixtures()
        round4_param_fixtures()
        cost_fixtures()
        claims_fixtures()
        rows = run_cases()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)  # the linked worktree's records live inside T, gone with it
    ids = Counter(cid for cid, *_ in rows)
    twice = sorted(cid for cid, n in ids.items() if n > 1)
    if twice:  # a report names its cases by id: one id, one case
        print(f"DUPLICATE case ids: {twice}")
        return ExitStatus(1)
    bad = 0
    for cid, want, got, what, err in rows:
        ok = want == got
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {cid:<6} want={want} got={got} {what}")
        if not ok and err.strip():
            print("        " + err.strip().splitlines()[0][:150])
            extra = [text for text in err.strip().splitlines()[1:] if text.strip().startswith("*")]
            for line in extra[:3]:
                print("        " + line.strip()[:150])
    print(f"{len(rows)} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
