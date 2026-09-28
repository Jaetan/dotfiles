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
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=GIT_ENV)


def setup() -> None:
    for d in (T, Q, Q2, S, BASE / "tmp"):
        d.mkdir(parents=True)
    for d in (T, Q, Q2):
        git(d, "init", "-q")
    (T / "tools").mkdir()
    (T / "tools" / "x.py").write_text("x = 1\n")
    (T / "CHANGELOG.md").write_text("# log\n")
    (T / ".gitignore").write_text(".commands-to-run.sh\n")
    (T / ".commands-to-run.sh").write_text("#!/usr/bin/env bash\n")
    git(T, "add", "-A")
    git(T, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    git(T, "worktree", "add", "-q", "--detach", str(W), "HEAD")
    (T / "inner").mkdir()
    git(T / "inner", "init", "-q")
    (BASE / "outside").mkdir()
    os.symlink(T / "tools" / "x.py", BASE / "outside" / "link.py")
    (Q2 / "Cargo.toml").write_text("[package]\n")
    (Q2 / "inner").mkdir()
    git(Q2 / "inner", "init", "-q")
    (S / "writer.py").write_text(f"from pathlib import Path\nPath('{T}/CHANGELOG.md').write_text('x')\n")
    (S / "reader.py").write_text("import json\nprint(json.dumps({'a': 1}))\n")
    src = BASE / "sleeper.c"
    src.write_text("#include <unistd.h>\nint main(void){for(;;)pause();}\n")
    subprocess.run(["cc", "-O0", "-o", str(SLEEPER), str(src)], check=True)


def start(argv: list[str], cwd: Path) -> subprocess.Popen:
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


def hook(path: Path, event: dict | str) -> tuple[int, str]:
    raw = event if isinstance(event, str) else json.dumps(event)
    p = subprocess.run([str(path)], input=raw, capture_output=True, text=True, check=False,  # by its shebang
                       timeout=60, env=HOOK_ENV)
    return p.returncode, p.stderr


def bash(cmd: str, cwd: Path, bg: bool = True, **extra: object) -> dict:
    ti = {"command": cmd, **extra}
    if bg:
        ti["run_in_background"] = True
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": ti, "cwd": str(cwd)}


def monitor(cmd: str, cwd: Path) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Monitor", "tool_input": {"command": cmd},
            "cwd": str(cwd)}


def edit(tool: str, path: str, cwd: Path = T) -> dict:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {key: path}, "cwd": str(cwd)}


X = f"{S}/x.log"

SHAPE = [
    ("A01", bash("cargo test", Q), 2, "bare heavy command"),
    ("A02", bash(BLESSED.format("cargo test"), Q), 0, "the blessed shape"),
    ("A03", bash("(cd rust && cargo test)", Q), 2, "heavy in a subshell"),
    ("A04", bash("timeout 600 cargo test", Q), 2, "behind timeout"),
    ("A05", bash("bash -c 'cargo test'", Q), 2, "inside bash -c"),
    ("A06", bash("for i in 1 2; do cargo test; done", Q), 2, "inside a loop"),
    ("A07", bash("nice -n 10 cargo test", Q), 2, "wrapper option with a value"),
    ("A08", bash(f"taskset -c 0-19 cargo test 2>&1 > {X}", Q), 2, "stderr redirected before stdout"),
    ("A09", bash(f"taskset -c 0-9 cargo test > {X} 2>&1", Q), 0, "a subset of 0-19"),
    ("A10", bash(f"taskset --cpu-list=0-19 cargo test > {X} 2>&1", Q), 0, "long option with ="),
    ("A11", bash(f"cat > {X} <<'EOF'\ncargo test\nEOF", Q), 0, "heredoc data, redirect first"),
    ("A12", bash(f"cat <<'EOF' > {X}\ncargo test\nEOF", Q), 0, "heredoc data, redirect after the tag"),
    ("A13", bash("python/.venv/bin/python -m pytest tests/", Q), 2, "the Python suite"),
    ("A14", bash("cargo mutants --in-place", Q), 2, "cargo mutants"),
    ("A15", bash("agda +RTS -M16G -RTS src/Aletheia/Main.agda", Q), 2, "agda"),
    ("A16", bash("bash benchmarks/run_all.sh --frames 10", Q), 2, "the benchmarks"),
    ("A17", bash("python3 -m tools.coverage_run", Q), 2, "the coverage lane"),
    ("A18", bash("rtk cargo test", Q), 2, "through rtk"),
    ("A19", bash(BLESSED.format("cargo test") + " && git status", Q), 2, "sharing its call"),
    ("A20", bash("git log --oneline | head -5", Q), 0, "head on a light command"),
    ("A21", bash(f"taskset -c 0-19 cargo test > {S}/head.log 2>&1", Q), 0, "'head' only in a file name"),
    ("A22", bash(BLESSED.format("cabal run shake -- build"), Q), 0, "shake, blessed"),
    ("A23", bash("python3 -m tools.check_build_incremental", Q), 2, "the staleness gate, bare"),
    ("A24", bash("echo start\ncargo test", Q), 2, "heavy on the second line"),
    ("A25", bash("cd rust\ncargo test --release", Q), 2, "cd, then heavy on its own line"),
    ("A26", bash(BLESSED.format("cargo test"), Q, bg=False), 0, "blessed shape, foreground, default timeout"),
    ("A26b", bash(BLESSED.format("cargo test"), Q, bg=False, timeout=600000), 0, "foreground with a named timeout"),
    ("A27", bash('"$py" -m tools.mutation_run', Q), 0, "an interpreter this call never names (limit)"),
    ("A27b", bash('py=python/.venv/bin/python; "$py" -m tools.mutation_run', Q), 2, "an interpreter the call names"),
    ("A28", bash("cargo +nightly test", Q), 2, "cargo with a toolchain"),
    ("A29", bash("bash -x probes/run_all.sh", Q), 2, "the store under bash -x"),
    ("A30", bash(f"{{ cargo test; }} > {X} 2>&1", Q), 2, "a group, no taskset"),
    ("A30b", bash(f"{{ taskset -c 0-19 cargo test; }} > {X} 2>&1", Q), 0, "a group, blessed"),
    ("A31", bash(f"taskset -c 0-19 cargo test >& {X}", Q), 0, ">& file"),
    ("A32", bash("taskset -c 0-19 cargo test > /dev/stdout 2>&1", Q), 2, "the terminal is not a log"),
    ("A33", bash(f"taskset -c 0-19 cargo test > {X} 3>&1", Q), 2, "3>&1 does not fold stderr"),
    ("A34", bash(f"taskset 0xffffff cargo test > {X} 2>&1", Q), 2, "a mask with CPUs 20-23"),
    ("A35", bash(f"taskset 0xfffff cargo test > {X} 2>&1", Q), 0, "a mask of 0-19"),
    ("A36", bash(f"taskset -ac 0-19 cargo test > {X} 2>&1", Q), 0, "combined flags"),
    ("A37", bash(f"taskset -c0-19 cargo test > {X} 2>&1", Q), 0, "attached list"),
    ("A38", bash("env -u RUSTFLAGS cargo test", Q), 2, "env with a valued option"),
    ("A39", bash(f"nohup cargo test > {X} 2>&1 &", Q), 2, "nohup and &"),
    ("A40", bash("echo $(cargo test)", Q), 2, "in a command substitution"),
    ("A41", bash("ctest -N --test-dir cpp/build", Q), 0, "ctest -N lists"),
    ("A41b", bash("cabal run shake -- count-modules", Q), 0, "shake count-modules"),
    ("A42", bash(BLESSED.format("cargo test") + f'; echo "$(tail -5 {X})"', Q), 2, "a same-call query via $( )"),
    ("A43", bash(f"taskset -c 0-19 bash -c 'cargo test' > {X} 2>&1", Q), 0, "bash -c carrier, blessed"),
    ("A44", bash(f"> {X} cargo test", Q), 2, "a leading redirection"),
    ("A45", bash("python3 -u tools/run_ci.py", Q), 2, "run_ci as a script"),
    ("A46", bash("go -C go test ./aletheia/", Q), 2, "go -C"),
    ("A47", bash("cabal run -v0 shake -- build", Q), 2, "cabal run with an option"),
    ("A48", bash("if cargo test; then echo ok; fi", Q), 2, "in an if"),
    ("A49", monitor("cargo test | grep x", Q), 2, "heavy inside Monitor"),
    ("A50", bash("n=${#x}; cargo test", Q), 2, "a # inside a word"),
    ("A51", bash("script -qc 'cargo test' /dev/null", Q), 2, "script -c"),
    ("A52", bash(f"cat <<'EOF' > {X}\nwe don't run cargo test here\nEOF", Q), 0, "an apostrophe in heredoc data"),
    ("A53", bash('echo "cargo test', Q), 0, "unparseable, no run live"),
    ("A54", bash("git commit -F - <<'EOF' && git log -1\nci: run go test; ctest now logs\nEOF", Q), 0,
     "a commit message naming heavy words"),
    ("A55", bash("python3 -m pytest tests/test_x.py -q", Q), 0, "a targeted pytest is light"),
    ("A56", bash("f() { cargo test; }; f", Q), 2, "in a function"),
    ("A57", bash("echo cargo test | bash", Q), 2, "a script piped into bash is read"),
    ("A58", bash("bash <<'EOF'\ncargo test\nEOF", Q), 2, "a heredoc fed to bash"),
    ("A59", bash("xargs -0 -r cargo test < /dev/null", Q), 2, "under xargs"),
    ("A60", bash("find rust -maxdepth 0 -exec cargo test \\;", Q), 2, "under find -exec"),
]

WRITES = [
    ("B01", bash("rg -n 'write_text' tools/", T), 0, "a search naming a write-word"),
    ("B02", bash('grep -n "cp " tools/x.py', T), 0, "grep for 'cp '"),
    ("B03", bash('echo "never rm the tree"', T), 0, "a string with 'rm '"),
    ("B04", bash(f"cp tools/x.py {S}/c.py", T), 0, "copy out of the tree"),
    ("B05", bash("cp /tmp/a tools/x.py", T), 2, "copy into the tree"),
    ("B06", bash(f"cp {S}/a {T}/tools/x.py", S), 2, "copy into the tree from elsewhere"),
    ("B07", bash(f"cd {T} && echo x > note.txt", S), 2, "cd, then redirect"),
    ("B08", bash("python3 -c \"open('CHANGELOG.md', 'a').write('x')\"", T), 2, "python open for writing"),
    ("B09", bash("python3 - <<'PY'\nfrom pathlib import Path\nPath('CHANGELOG.md').write_text('x')\nPY", T), 2,
     "python heredoc write_text"),
    ("B10", bash("sed -ni 's/a/b/p' tools/x.py", T), 2, "sed -ni"),
    ("B11", bash("sed -e 's/a/b/' -i tools/x.py", T), 2, "sed -e ... -i"),
    ("B12", bash("sed --in-place 's/a/b/' tools/x.py", T), 2, "sed --in-place"),
    ("B12b", bash("sed -n 's/a/b/p' tools/x.py", T), 0, "sed without -i reads"),
    ("B13", bash("git clean -fdx", T), 2, "git clean"),
    ("B14", bash("git pull --ff-only", T), 2, "git pull"),
    ("B15", bash("git rebase main", T), 2, "git rebase"),
    ("B16", bash("cd rust && cargo fmt", T), 2, "cargo fmt"),
    ("B16b", bash("cd rust && cargo fmt --check", T), 0, "cargo fmt --check reads"),
    ("B17", bash("ruff format tools", T), 2, "ruff format"),
    ("B18", bash(f'R={T}; echo x > "$R/note.txt"', S), 2, "a redirect through a variable set in the call"),
    ("B19", bash("echo x > /tmp/y", T), 0, "a redirect to /tmp"),
    ("B20", bash(f"git -C {T} commit -m x", S), 2, "git -C into the tree"),
    ("B21", bash("git status --short", T), 0, "a read-only git verb"),
    ("B22", bash("ln -sf /tmp/x tools/y", T), 2, "ln"),
    ("B22b", bash("dd if=/dev/zero of=tools/y bs=1 count=1", T), 2, "dd of="),
    ("B22c", bash("install -m 755 /tmp/x tools/y.sh", T), 2, "install"),
    ("B22d", bash("truncate -s 0 CHANGELOG.md", T), 2, "truncate"),
    ("B22e", bash("touch CHANGELOG.md", T), 2, "touch"),
    ("B23", bash(f"python3 {S}/writer.py", S), 2, "a script that writes into the tree"),
    ("B23b", bash(f"python3 {S}/reader.py", T), 0, "a script that writes nothing"),
    ("B24", bash(f"git commit -S -F {S}/msg.txt", T), 2, "a commit"),
    ("B25", bash("cp tools/x.py tools/x.bak", Q), 0, "a write in a tree with no run"),
    ("B26", bash(f"cat > {T}/.commands-to-run.sh <<'EOF'\ngit status\nEOF", S), 0, "the dribble"),
    ("B27", bash("git stash list", T), 0, "git stash list"),
    ("B27b", bash("git stash show -p stash@{0}", T), 0, "git stash show"),
    ("B27c", bash("git stash", T), 2, "git stash"),
    ("B28", bash("echo a#b > tools/f.txt", T), 2, "a # inside a word"),
    ("B29", bash(f"touch /tmp/zz; echo y > {T}/note.txt", Q), 2, "the second target is the live one"),
    ("B30", bash(f"echo a > {Q}/x.txt; echo b > {T}/y.txt", S), 2, "two trees, the second live"),
    ("B31", bash(f"echo x > /tmp/..{T}/note.txt", S), 2, "an unnormalised path"),
    ("B32", bash(f"echo x > {T}/tools/scratchpad-notes.txt", S), 2, "'scratchpad' inside a tree path"),
    ("B33", bash(BLESSED.format("cabal run shake -- build"), T), 2, "a build started mid-run"),
    ("B34", bash(BLESSED.format("python3 -m pytest tests/"), T), 0, "a read-only heavy run mid-run"),
    ("B35", bash("find tools -name '*.bak' -delete", T), 2, "find -delete"),
    ("B36", bash("mkdir tools/new", T), 2, "mkdir"),
    ("B37", bash("patch -p1 < /tmp/x.diff", T), 2, "patch"),
    ("B38", bash("rsync -a /tmp/x/ tools/", T), 2, "rsync"),
    ("B39", bash("tar -xf /tmp/a.tar -C tools", T), 2, "tar -x"),
    ("B40", bash("clang-format-22 -i tools/x.cpp", T), 2, "clang-format -i"),
    ("B40b", bash("xargs -0 -r clang-format-22 -i < /tmp/list", T), 2, "clang-format -i under xargs"),
    ("B41", bash("gofmt -w x.go", T), 2, "gofmt -w"),
    ("B42", bash("sort -o tools/x /tmp/y", T), 2, "sort -o"),
    ("B43", bash("awk -i inplace '{print}' tools/x.py", T), 2, "awk -i inplace"),
    ("B44", bash("git -c commit.gpgsign=false commit -m x", T), 2, "git -c commit"),
    ("B44b", bash("git merge main", T), 2, "git merge"),
    ("B44c", bash("git cherry-pick HEAD~1", T), 2, "git cherry-pick"),
    ("B44d", bash("git worktree add /tmp/wt-x", T), 0, "git worktree add elsewhere (metadata ruled exempt)"),
    ("B44e", bash("git worktree add -b wt sub/wt HEAD", T), 2, "git worktree add inside the live tree"),
    ("B44f", bash("git worktree remove --force /tmp/wt-x && git worktree prune", T), 0, "worktree remove and prune"),
    ("B44g", bash("git init -q /tmp/fresh-repo", T), 0, "git init of a directory elsewhere"),
    ("B44h", bash("git init -q", T), 2, "git init of the live tree"),
    ("B44i", bash("git clone -q . /tmp/clone-x", T), 0, "git clone into a directory elsewhere"),
    ("B44j", bash("git clone -q /tmp/src sub/clone", T), 2, "git clone into the live tree"),
    ("B44k", bash("git log -1 --output=CHANGELOG.md", T), 2, "git log --output into the tree"),
    ("B44l", bash("git reflog expire --expire=now --all", T), 2, "git reflog expire"),
    ("B44m", bash("git log -1 --output=/tmp/log.txt", T), 0, "git log --output elsewhere"),
    ("B45", bash(f"cat > {T}/n.txt <<'END-DATA'\nwe don't\nEND-DATA", S), 2, "a heredoc to a file in the tree"),
    ("B46", bash("echo '*.tmp' >> .git/info/exclude", T), 2, ".git internals"),
    ("B47", bash('echo "unterminated', T), 2, "unparseable while a run is live"),
    ("B48", bash('echo "unterminated', S), 0, "unparseable where no run is"),
    ("B49", bash(f"cp {S}/a {BASE}/outside/link.py", S), 2, "a symlink into the tree"),
    ("B50", bash(f"echo x > {W}/f.txt", S), 0, "a linked worktree outside the live tree"),
    ("B51", bash("tee tools/out.txt < /tmp/in", T), 2, "tee"),
    ("B52", bash("curl -o tools/f https://example.invalid/x", T), 2, "curl -o"),
    ("B53", bash("perl -pi -e 's/a/b/' tools/x.py", T), 2, "perl -pi"),
    ("B54", bash("chmod +x tools/x.py", T), 2, "chmod"),
    ("B55", bash("rm -f tools/x.py", T), 2, "rm"),
    ("B56", bash(f"mv {S}/a tools/", T), 2, "mv into the tree"),
    ("B57", bash("go mod tidy", T), 2, "go mod tidy"),
    ("B58", bash("git apply --check /tmp/x.patch", T), 0, "git apply --check reads"),
    ("B59", bash("git diff HEAD -- tools/x.py", T), 0, "git diff reads"),
    ("B60", bash("cat tools/x.py | wc -l", T), 0, "a read pipeline"),
]

EDITS = [
    ("C01", edit("Edit", f"{T}/tools/x.py"), 2, "tracked file, run live"),
    ("C02", edit("Write", f"{T}/.commands-to-run.sh"), 0, "the dribble"),
    ("C03", edit("Edit", f"{Q}/f.txt"), 0, "a tree with no run"),
    ("C04", edit("Write", f"{S}/draft.txt"), 0, "a plain directory"),
    ("C05", edit("Edit", "tools/x.py", T), 2, "a relative path, resolved against the event's cwd"),
    ("C06", edit("Edit", f"{BASE}/outside/link.py"), 2, "a symlink into the tree"),
    ("C07", edit("NotebookEdit", f"{T}/n.ipynb"), 2, "NotebookEdit"),
    ("C08", edit("Write", f"{T}/new/dir/file.txt"), 2, "a new directory in the tree"),
    ("C09", edit("Edit", f"{T}/inner/f.txt"), 2, "a nested repository inside the live tree"),
    ("C10", edit("Edit", f"{W}/f.txt"), 0, "a linked worktree outside the live tree"),
    ("C11", edit("MultiEdit", f"{T}/tools/x.py"), 2, "MultiEdit"),
    ("C12", edit("Write", f"{T}/.git/info/exclude"), 2, ".git internals"),
]

# Processes started in (or pointing into) Q2, then an Edit on Q2/f.txt: blocked iff the process is a run.
PROCS = [
    ("P01", ["cargo", "test"], Q2, 2),
    ("P02", ["cabal", "run", "shake", "--", "build"], Q2, 2),
    ("P03", ["bash", "probes/run_all.sh"], Q2, 2),
    ("P04", ["pytest", "tests/"], Q2, 2),
    ("P05", ["agda", "+RTS", "-M16G", "-RTS", "src/Main.agda"], Q2, 2),
    ("P06", ["cargo", "mutants", "--in-place"], Q2, 2),
    ("P07", ["gremlins", "unleash", "./aletheia"], Q2, 2),
    ("P08", ["mutmut", "run"], Q2, 2),
    ("P09", ["python3", "-m", "tools.coverage_run"], Q2, 2),
    ("P10", ["python3", "-m", "tools.check_build_incremental"], Q2, 2),
    ("P11", ["python3", "-m", "tools.check_reproducible_build"], Q2, 2),
    ("P12", ["python3", "-m", "tools.iwyu"], Q2, 2),
    ("P13", ["bash", "benchmarks/run_all.sh", "--bench", "throughput"], Q2, 2),
    ("P14", ["python3", "-m", "tools.mutation_run"], Q2, 2),
    ("P15", ["ctest", "--test-dir", "build"], Q2, 2),
    ("P16", ["mull-runner-23", "unit_tests"], Q2, 2),
    ("P17", ["taskset", "-c", "0-19", "cargo", "test"], Q2, 2),
    ("P18", ["nohup", "cargo", "test"], Q2, 2),
    ("P19", ["timeout", "60", "cargo", "test"], Q2, 2),
    ("P20", ["env", "-u", "X", "cargo", "test"], Q2, 2),
    ("P21", ["emacs", "tools/run_ci.py"], Q2, 0),
    ("P22", ["tail", "-f", "probes/run_all.sh"], Q2, 0),
    ("P23", ["/usr/bin/zsh", "-c", "rg -n 'go test' docs"], Q2, 0),
    ("P24", ["/usr/bin/zsh", "-c", "source snap && eval 'taskset -c 0-19 cargo test > log 2>&1'"], Q2, 0),
    ("P25", ["grep", "cargo test", "notes.txt"], Q2, 0),
    ("P26", ["ctest", "-N"], Q2, 0),
    ("P27", ["python3", "-m", "pytest", "tests/test_x.py"], Q2, 0),
    ("P28", ["cargo", "test", "--manifest-path", str(Q2 / "Cargo.toml")], S, 2),
    ("P29", ["cargo", "test"], Q2 / "inner", 0),
    ("P30", ["cargo", "test"], Q2 / "sub", 2),
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
    ("R01", bash(f"cat > {S}/r.sh <<'SH'\ncd {Q} && python3 tools/run_ci.py 2>&1 | tail -5\nSH\nbash {S}/r.sh", Q), 2,
     "a script the call writes, then runs"),
    ("R04", bash("bash -n tools/build_mull.sh && echo syntax ok", Q), 0, "bash -n runs nothing"),
    ("R05", bash("files=(tools/mutation_run.py tools/x.py)\npython3 -m ruff check $files", Q), 0, "an array"),
    ("R05b", bash(f"ARGS=(--release); taskset -c 0-19 cargo test \"${{ARGS[@]}}\" > {X} 2>&1", Q), 0,
     "an array feeding a blessed run"),
    ("R06", bash("cargo install --list", Q), 0, "cargo install --list"),
    ("R06b", bash("cargo mutants --list --json", Q), 0, "cargo mutants --list"),
    ("R06c", bash("python3 -m tools.coverage_run --scope", Q), 0, "coverage_run --scope"),
    ("R06d", bash("python3 -m tools.mutation_scope --binding rust", Q), 0, "mutation_scope is a query"),
    ("R06e", bash("cargo llvm-cov --branch --summary-only --no-run", Q), 0, "llvm-cov --no-run"),
    ("R09", bash(f"taskset -c 2-21 ./cpp/build/benchmark throughput > {S}/b.json 2> {S}/b.err", Q, timeout=600000),
     2, "a benchmark binary on CPUs 20-21"),
    ("R09b", bash("(cd cpp/build && ./unit_tests --order decl 2>&1 | grep FAILED | head -6)", Q), 2,
     "the C++ battery through head"),
    ("R11", bash("go vet ./", Q), 0, "go vet of one package"),
    ("R11b", bash("go test -run '^TestZZ$' -count=1 .", Q), 0, "go test -run"),
    ("R11c", bash("ctest --test-dir cpp/build -R excel_tests -V", Q), 0, "ctest -R"),
    ("R11d", bash("ctest --test-dir cpp/build -V", Q), 2, "ctest -V is verbose, not a version"),
    ("V02", bash("python3 -m pytest tests/test_a.py tests/", Q), 2, "a pytest with a directory"),
    ("V02b", bash("python3 -m pytest tests/*.py", Q), 2, "a pytest over a glob"),
    ("V07", bash("echo \"$(cat <<'EOF'\nwe don't\nEOF\n)\" && cargo test", Q), 2, "heredoc in $( ) beside a run"),
    ("V12", bash("fish -c 'cargo test'", Q), 2, "fish -c"),
    ("V12b", bash("bash --norc -c 'cargo test'", Q), 2, "bash --norc -c"),
    ("V13", bash(f"bash {S}/sweep.sh", Q), 2, "a scratch script with a sweep through tail"),
    ("V13b", bash(f"python3 {S}/driver.py", Q), 2, "a Python driver starting cargo test"),
    ("V13c", bash(f"time {{ cargo test; }} > {X} 2>&1", Q), 2, "time { }"),
    ("V13d", bash("[[ -n \"$(cargo test)\" ]]", Q), 2, "a run inside [[ ]]"),
    ("V13e", bash("cat <<EOF\n$(cargo test 2>&1)\nEOF", Q), 2, "a run inside an unquoted heredoc"),
    ("V13f", bash("coproc cargo test", Q), 2, "coproc"),
    ("V13g", bash("cargo fix --allow-dirty", Q), 2, "cargo fix"),
    ("V13h", bash("bazel build //...", Q), 2, "bazel build"),
    ("V13i", bash("cpp/build/unit_tests", Q), 2, "the Catch2 suite by path"),
    ("V14", bash("bash -n probes/run_all.sh", Q), 0, "bash -n on the store"),
    ("V14b", bash(f"exec > {X} 2>&1; taskset -c 0-19 cargo test", Q), 0, "exec redirects what follows"),
    ("V14c", bash("taskset -c 0-19 cargo test > /dev/shm/x.log 2>&1", Q), 0, "a log in /dev/shm"),
    ("V14d", bash("agda -V", Q), 0, "agda -V"),
    ("V14e", bash("pytest -h", Q), 0, "pytest -h"),
    ("V16", bash(BLESSED.format("cargo test") + f'; echo "$(< {X})"', Q), 2, "$(< file) in the same call"),
    ("V16b", bash(f"taskset -c 0-19 cargo test > {X} 2> {S}/err.log", Q), 0, "both streams in files"),
    ("V16c", bash(BLESSED.format("cargo test"), Q, bg=False, timeout=5000), 0, "a timeout shorter than the run"),
    ("V06", bash("cd \"$(git rev-parse --show-toplevel)\" && " + BLESSED.format("cargo test"), Q), 0,
     "cd to the top level before a run"),
    ("V06b", bash(f"LOG={S}/x-$(date +%s).log; taskset -c 0-19 cargo test > \"$LOG\" 2>&1", Q), 0,
     "a timestamped log name"),
    ("F01c", bash("taskset -c 0-19 cargo test; gh pr comment 1 --body " + HCOMMIT, Q), 2,
     "an unlogged run beside a heredoc body"),
    ("F08", bash("( (cd rust && cargo test) )", Q), 2, "nested subshells"),
    ("F10", bash("(( n = 1 << 2 ))\ncargo test", Q), 2, "<< inside arithmetic"),
    ("F12", bash("echo $(case $x in a) echo a;; esac)", Q), 0, "a case inside $( )"),
    ("F12b", bash("echo $(echo $'it\\'s')", Q), 0, "an ANSI quote inside $( )"),
]

WRITES2 = [
    ("R01b", bash(f"{S}/w.sh", T), 2, "a script run by its path"),
    ("R01d", bash(f"cat > {S}/v.sh <<'SH'\necho x > {T}/CHANGELOG.md\nSH\nbash {S}/v.sh", T), 2,
     "a script written then run in one call"),
    ("R03", bash("perl -0pi -e 's/x = 1/x = 2/' tools/x.py", T), 2, "perl -0pi"),
    ("R04b", bash("bash -n .commands-to-run.sh && echo syntax-ok", T), 0, "bash -n on the dribble"),
    ("R05c", bash("FILES=(); FILES+=(\"a b\"); echo \"${FILES[@]}\"", T), 0, "array append"),
    ("R07", bash(f"S={S}; for f in tools/x.py; do cp \"$f\" \"$S/wt/$f\"; done", T), 0, "copy out through variables"),
    ("R07b", bash("for f in tools/x.py CHANGELOG.md; do printf 'x\\n' > \"$f\"; done", T), 2,
     "a loop variable over literal paths"),
    ("R07c", bash(f"R={T}; w() {{ printf 'x\\n' > \"$R/tools/$1\"; }}; w new.txt", S), 2, "a function's redirect"),
    ("R08", bash(f"python3 - <<'PY'\nimport pathlib\nM = \"{S}/mem/\"\npathlib.Path(M + \"TASKS.md\").write_text(\"x\")\nPY",
                 T), 0, "python writes outside the tree through a name"),
    ("R08b", bash("python3 - <<'PY'\nimport pathlib\n(pathlib.Path.home() / \".claude-test-x.json\").write_text('x')\nPY",
                  T), 0, "python writes under HOME"),
    ("R08c", bash("python3 -c \"x = 'L.6'; open('/tmp/y', 'w').write(x)\"", T), 0, "a task id string is not a path"),
    ("R08d", bash("python3 - <<'PY'\nimport pathlib\nfor p in pathlib.Path('tools').glob('*.py'):\n"
                  "    p.write_text(p.read_text() + '\\n')\npathlib.Path('/tmp/x-log.txt').write_text('done')\nPY", T), 2,
     "a write through a loop variable beside one outside"),
    ("R10", bash("cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release", T), 2, "a cmake configure into the tree"),
    ("R12", bash("git fetch -q origin", T), 2, "git fetch writes .git"),
    ("R12b", bash("git branch -D old-branch", T), 2, "git branch -D"),
    ("R12c", bash("git branch --show-current", T), 0, "git branch --show-current"),
    ("R12d", bash("git config user.name", T), 0, "git config reading a key"),
    ("R14", bash(f"taskset -c 0-19 cmake --build \"{S}/build\" -j20 > {S}/b.log 2>&1", T), 0,
     "a build of a directory outside the tree"),
    ("V06c", bash(f"echo x > \"$(git rev-parse --show-toplevel)/note.txt\"", T), 2, "the top level by substitution"),
    ("V06d", bash("echo x > \"$(pwd)/note.txt\"", T), 2, "$(pwd)"),
    ("V06e", bash("cd \"$(git rev-parse --show-toplevel)\" && sed -i s/a/b/ CHANGELOG.md", T), 2,
     "cd to the top level, then sed -i"),
    ("V06f", bash("cd /tmp && cd - && sed -i s/a/b/ CHANGELOG.md", T), 2, "cd -"),
    ("V06g", bash("tmp=$(mktemp); git show HEAD:CHANGELOG.md > \"$tmp\"; diff \"$tmp\" CHANGELOG.md; rm -f \"$tmp\"", T),
     0, "a mktemp file"),
    ("V07b", bash("gh pr create --title t --body " + HCOMMIT, T), 0, "a PR body heredoc"),
    ("V08", bash("python3 -c \"print('a-b'.replace('-', '_'))\"", T), 0, "str.replace"),
    ("V09", bash("python3 -c \"from pathlib import Path; Path('CHANGELOG.md').open('w').write('x')\"", T), 2,
     "Path.open('w')"),
    ("V09b", bash("python3 -c \"import os; os.system('sed -i s/a/b/ CHANGELOG.md')\"", T), 2, "os.system"),
    ("V09c", bash("python/.venv/bin/pip install -e 'python/.[mutation]'", T), 2, "pip install -e"),
    ("V09d", bash("python3 -m pip install -e python/", T), 2, "python -m pip install"),
    ("V09e", bash("python3 -m ruff format tools", T), 2, "python -m ruff format"),
    ("V09f", bash(f"{S}/fix.py", T), 2, "a shebang Python script by path"),
    ("V09g", bash("echo \"open('CHANGELOG.md','w').write('x')\" | python3", T), 2, "code piped into python"),
    ("V09h", bash("cat <<'PY' | python3 -\nopen('CHANGELOG.md','w').write('x')\nPY", T), 2, "a heredoc piped into python"),
    ("V09i", bash(f"python3 {S}/bump.py CHANGELOG.md", T), 2, "a script writing its argv"),
    ("V10", bash("cd rust && cargo clean", T), 2, "cargo clean"),
    ("V10b", bash("cargo update", T), 2, "cargo update"),
    ("V10c", bash("git push origin HEAD", T), 2, "git push"),
    ("V10d", bash("git commit-tree 'HEAD^{tree}' -p HEAD -m x", T), 2, "git commit-tree"),
    ("V10e", bash("git format-patch -1 HEAD", T), 2, "git format-patch"),
    ("V10f", bash("git archive -o release.tar HEAD", T), 2, "git archive -o"),
    ("V10g", bash("mv CHANGELOG.md /tmp/", T), 2, "mv out of the tree"),
    ("V10h", bash("curl -sSLO https://example.invalid/y", T), 2, "curl -O bundled"),
    ("V10i", bash("tar czf tools/x.tgz docs", T), 2, "tar czf"),
    ("V10j", bash("goimports -w x.go", T), 2, "goimports -w"),
    ("V10k", bash("clang-tidy -fix x.cpp", T), 2, "clang-tidy -fix"),
    ("V10l", bash("ruff check --fix-only tools", T), 2, "ruff --fix-only"),
    ("V11", bash(f"taskset -c 0-19 cmake --build {T}/cpp/build > {X} 2>&1", S), 2, "a build of the live tree from outside"),
    ("V12c", bash("fish -c 'echo x > CHANGELOG.md'", T), 2, "fish -c writing"),
    ("V15", bash("wget -qO- https://example.invalid/x", T), 0, "wget to stdout"),
    ("V15b", bash("wget -O - https://example.invalid/x", T), 0, "wget -O -"),
    ("V15c", bash("unzip -l a.zip", T), 0, "unzip -l"),
    ("V15d", bash("patch --dry-run -p1 < /tmp/x.diff", T), 0, "patch --dry-run"),
    ("V15e", bash("git clean -nd", T), 0, "git clean -n"),
    ("V15f", bash("isort --check-only x.py", T), 0, "isort --check-only"),
    ("V15g", bash("tar -xOf a.tar", T), 0, "tar to stdout"),
    ("V15h", bash("go mod tidy -diff", T), 0, "go mod tidy -diff"),
    ("V15i", bash("git submodule", T), 0, "bare git submodule"),
    ("V18", bash(f"echo x >! {T}/note.txt", S), 2, "zsh >!"),
    ("V21", edit("Write", f"{T}/.commands-to-run.d/msg-next.txt"), 2, "the dribble's directory is not the dribble"),
    ("F01", bash("gh pr comment 1 --body " + HCOMMIT, T), 0, "a comment body heredoc"),
    ("F01b", bash(f"cd {T} && git commit -q -m " + HCOMMIT, S), 2, "a commit with a heredoc body"),
    ("F08b", bash("( (echo x > CHANGELOG.md) )", T), 2, "nested subshells writing"),
    ("F09", bash("echo \"${LOG:-$(git stash)}\"", T), 2, "a substitution inside ${ }"),
    ("F09b", bash("case $(git stash) in *) ;; esac", T), 2, "a case subject"),
    ("F05", bash("bash /dev/zero", T), 2, "a device is not a script: unreadable while a run is live"),
    ("F07", bash("echo x > $'a\\x00b'", T), 2, "a NUL ends the path, as the shell ends it"),
]

LOOPS2 = [
    ("L01", bash("until grep -q done /tmp/x.log; do\n  sleep 5\ndone", T), 2, "a multi-line until"),
    ("L02", bash("for i in {1..30}; do\n echo tick\n sleep 2\ndone", T), 2, "a for loop with sleep"),
    ("L03", bash("while true; do\n pgrep -f cargo || break\n sleep 10\ndone", T), 2, "a multi-line while"),
    ("L04", bash("kill -INT 12345; for i in $(seq 1 40); do kill -0 12345 2>/dev/null || break; sleep 1; done", T), 2,
     "a bounded kill-wait"),
    ("L05", monitor("tail -f /tmp/x.log | grep --line-buffered FAIL &\nwait 12345", T), 2, "a wait on a literal pid"),
    ("L06", bash("sleep 0.1 & sleep 7773 & wait -n", T), 2, "wait -n reaps one"),
    ("L07", bash("bash -c 'sleep 7771 &'", T), 2, "a job inside bash -c"),
    ("L08", bash("eval 'sleep 7778 &'", T), 2, "a job inside eval"),
    ("L09", bash("(sleep 7775 &); wait", T), 2, "a job in a subshell"),
    ("L10", bash("coproc tail -f /tmp/x", T), 2, "coproc"),
    ("L11", bash("setsid -f sleep 7774; echo started", T), 2, "setsid -f"),
    ("L12", bash("tmux new -d 'cargo test'", T), 2, "tmux new -d"),
    ("L13", bash("sleep 999 &!", T), 2, "zsh &!"),
    ("L14", bash("tail -f x | grep y &\nsleep 1\nwait", T), 0, "a pipeline job reaped by wait"),
    ("L15", bash("sleep 1 & p=$!; echo x; wait \"$p\"", T), 0, "wait on a kept pid"),
    ("L16", monitor("cleanup() { kill 0; }\ntrap cleanup EXIT\ntail -f /tmp/x.log | grep --line-buffered FAIL &\n"
                    "while kill -0 123 2>/dev/null; do sleep 15; done", T), 0, "a trap through a function"),
    ("L17", bash("git commit -q -m " + HCOMMIT + " && tail -f /tmp/x.log | grep --line-buffered FAIL &", T), 2,
     "a job beside a heredoc commit body"),
    ("L18", bash(f"cat > {S}/o.sh <<'SH'\nsleep 99 &\nSH\nbash {S}/o.sh", T), 2, "a job in a script the call runs"),
    ("M01", monitor("while pgrep -af cargo >/dev/null; do sleep 5; done", T), 2, "pgrep -af"),
    ("M02", monitor("while pgrep --full cargo >/dev/null; do sleep 5; done", T), 2, "pgrep --full"),
    ("M03", monitor("while pgrep -u \"$USER\" -f cargo >/dev/null; do sleep 5; done", T), 2, "pgrep -u X -f"),
    ("M04", monitor("while pgrep -f '[c]argo test' >/dev/null; do sleep 5; done; echo 'cargo test finished'", T), 2,
     "a bracket whose plain text appears elsewhere"),
    ("M05", monitor("pgrep -f '[x]yz'; pgrep -f cargo", T), 2, "one bracket does not excuse another pattern"),
    ("M06", monitor("while pgrep -f '[c]argo mutants' > /dev/null; do sleep 5; done", T), 0, "a lone bracket pattern"),
    ("N01", bash("cat <<'EOF' > /tmp/doc.md\nuntil grep -q x f; do sleep 1; done\nEOF", T), 0, "a loop in heredoc data"),
    ("N02", bash("rg -n 'until .* do .*sleep' /tmp", T), 0, "a search for the shape"),
    ("N03", bash("git commit -m \"until grep; do sleep 1; done\"", T), 0, "a commit message naming the shape"),
    ("N04", bash("cat <<'END-DOC' > /tmp/doc.md\nwhile true; do sleep 1; done\nEND-DOC", T), 0, "a hyphenated tag"),
    ("N05", bash("python3 -c \"s = 'while x; do sleep 1; done'\"", T), 0, "a Python string naming the shape"),
]

PROCS2 = [
    ("P31", ["python3", str(Q2 / "venv/bin/pytest"), "tests/"], Q2, 2),
    ("P32", ["python3", str(Q2 / "venv/bin/mutmut"), "run"], Q2, 2),
    ("P33", ["python3", str(Q2 / "venv/bin/pylint"), "aletheia/"], Q2, 2),
    ("P34", ["cargo", "test", f"--manifest-path={Q2}/Cargo.toml"], S, 2),
    ("P35", ["cargo", "test", "--manifest-path", "../probe/Cargo.toml"], S, 2),
    ("P36", ["make", f"-C{Q2}"], S, 2),
    ("P37", ["python3", "-m", "tools.mutation_scope"], Q2, 0),
    ("P38", ["ctest", "-V"], Q2, 2),
]


def extra_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, event, want, what in SHAPE2:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    (T / ".commands-to-run.sh").write_text(DRIB)
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in WRITES2:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    for cid, event, want, what in LOOPS2:
        rc, err = hook(LOOPS, event)
        rows.append((cid, want, rc, what, err))
    for cid, argv, cwd, want in PROCS2:
        p = start(argv, cwd)
        try:
            rc, err = hook(GUARD, edit("Edit", f"{Q2}/f.txt", Q2))
        finally:
            stop(p)
        rows.append((cid, want, rc, " ".join(argv) + f" @{cwd.name}", err))
    # A snapshot whose PostToolUse never came is pruned by the next one.
    stale = BASE / "tmp" / "claude-heavy-run-guard" / "stale.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}")
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    live = start(["cargo", "test"], T)
    try:
        hook(GUARD, bash("echo hi", T) | {"tool_use_id": "t-prune"})
        rows.append(("E06", 0, int(stale.exists()), "a stale snapshot is pruned", ""))
        pre = bash("touch /tmp/e07-stamp", T)
        hook(GUARD, pre)
        os.utime(T / "tools" / "x.py", ns=(3, 3))
        post = bash("rtk touch /tmp/e07-stamp", T) | {"hook_event_name": "PostToolUse", "tool_response": {}}
        rc, err = hook(GUARD, post)
        rows.append(("E07", 2, rc, "Pre and Post paired across the rtk rewrite", err))
    finally:
        stop(live)
    # Fuzz findings: crashes, hangs and slow reads, each read within 2 s and exiting 0 or 2.
    slow = "python3 - <<'PY'\n# the hook's regex\ns = open('x').read()\n" + "".join(
        f's = s.replace("old {i}\\n", "new {i}\\n")\n' for i in range(14)) + "p.write_text(s)\nPY"
    for cid, cmd in [("Z01", slow), ("Z02", "for"), ("Z03", "echo x > CHANGELOG.md\nfor"),
                     ("Z04", "{ " * 260 + "true" + "; }" * 260), ("Z05", "echo x > a\x00b"),
                     ("Z06", "sh \ud800"), ("Z07", 'echo "' + "$(" * 20 + "x" + ")" * 20 + '"')]:
        for path in (GUARD, LOOPS):
            t0 = time.monotonic()
            try:
                rc, err = hook(path, bash(cmd, T))
            except UnicodeEncodeError:
                rc, err = 0, ""
            took = time.monotonic() - t0
            rows.append((f"{cid}{'g' if path == GUARD else 'l'}", 1, int(rc in (0, 2) and took < 2),
                         f"read in {took:.2f}s, exit {rc}", err))
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
                                            "if len(sys.argv) > 1:\n    path = Path(sys.argv[1])\npath.write_text('x')\n")
    (S / "argp.py").write_text("import argparse\np = argparse.ArgumentParser()\np.add_argument('f')\n"
                               "a = p.parse_args()\nopen(a.f, 'w').write('x')\n")
    (S / "watch.sh").write_text("while pgrep -f cargo > /dev/null; do sleep 5; done\n")
    (T / "tools" / "check_apply.py").write_text("from pathlib import Path\nimport sys\n"
                                               "for p in Path('.').rglob('*.md'):\n    p.write_text(p.read_text())\n")
    for d in (Q2 / "probes", Q2 / "tools"):
        d.mkdir(exist_ok=True)


SHAPE3 = [
    ("N2a", bash("python3 -m pytest tests/test_x.py -q -p no:randomly", Q), 0, "a pytest option's value"),
    ("N2b", bash("python3 -m pytest tests/test_x.py -k name", Q), 0, "pytest -k"),
    ("N6", bash("env 'cxx_x:/r/cpp/src/client.cpp:1:2:1:3:ab.0=1' timeout 600 cpp/build/unit_tests --order rand 2>&1"
                " | head -5", Q), 2, "env with a Mull switch name"),
    ("N11", bash("cpp/build/unit_tests '[json]' 2>&1 | tail -3", Q), 0, "a Catch2 run of one tag"),
    ("H01", bash("bash -eo pipefail -c 'cargo test 2>&1 | tail -5'", Q), 2, "bash -eo pipefail -c"),
    ("H02", bash(f"bash -euo pipefail {S}/sweep.sh", Q), 2, "bash -euo pipefail script"),
    ("H03", bash("cd probes && bash run_all.sh", Q), 2, "the store from its own directory"),
    ("H04", bash("cd benchmarks && ./run_all.sh --frames 10", Q), 2, "the benchmarks from their directory"),
    ("H06", bash("python3 -c \"import os; os.system('cargo test 2>&1 | tail -5')\"", Q), 2, "os.system"),
    ("H07", bash("python3 -c \"import subprocess; subprocess.run('cargo test | tail -5', shell=True)\"", Q), 2,
     "shell=True"),
    ("H08", bash("python3 -c \"from subprocess import run; run(['cargo', 'test'])\"", Q), 2, "an imported run"),
    ("H09", bash("python3 -c \"import subprocess; subprocess.run(['bash', '-c', 'cargo test | tail -5'])\"", Q), 2,
     "a bash -c list"),
    ("H14", bash("python3 - <<'PY'\nimport subprocess\ncmd = ['cargo', 'test']\nsubprocess.run(cmd)\nPY", Q), 2,
     "a list bound to a name"),
    ("H15", bash("python3 -c \"import subprocess, sys; subprocess.run([sys.executable, '-m', 'pytest', "
                 "'python/tests'])\"", Q), 2, "sys.executable -m pytest"),
    ("H10", bash("valgrind cpp/build/unit_tests", Q), 2, "valgrind"),
    ("H12", bash("source probes/run_all.sh", Q), 2, "source the store"),
    ("H13", bash("cd tools && python3 run_ci.py", Q), 2, "run_ci from tools/"),
    ("H05", bash("cat > build/r.sh <<'EOF'\ncargo test 2>&1 | tail -5\nEOF\nbash build/r.sh", Q), 2,
     "a script the call writes into the tree"),
    ("H16", bash(f"bash < {S}/sweep.sh", Q), 2, "a script on stdin"),
    ("F3", bash("time for p in a b; do cargo test -p $p; done", Q), 2, "time for"),
    ("F4c", bash("n=$(( $(cargo test 2>&1 | grep -c FAILED) ))", Q), 2, "a run inside arithmetic"),
    ("F5b", bash("echo \"$(rg -c case tools)\" && cargo test", Q), 2, "case as an argument in $( )"),
    ("F1q", bash("cargo test; fish -c 'for f in *.py; echo $f; end'", Q), 2, "a run beside fish text"),
    ("F8b", bash("echo $[ 1 << 2 ]\ncargo test", Q), 2, "$[ ] arithmetic"),
]

WRITES3 = [
    ("N1a", bash("python/.venv/bin/python -m ruff check tools/", T), 0, "the venv python binary"),
    ("N1b", bash("python/.venv/bin/python -m pytest tests/test_x.py -q", T), 0, "the venv python, targeted pytest"),
    ("N3a", bash(f"python3 - <<'PY'\nimport pathlib\np=pathlib.Path(\"{S}/notes.md\"); s=p.read_text()\n"
                 "p.write_text(s + 'x')\nPY", T), 0, "statements joined by ;"),
    ("N3b", bash("python3 - <<'PY'\np = \".commands-to-run.sh\"; s = open(p).read()\nopen(p, \"w\").write(s)\nPY", T),
     0, "the dribble edited from Python"),
    ("N4", bash(f"python3 - <<'PY'\nfrom pathlib import Path\np = Path(\"CHANGELOG.md\")\np.write_text(\"x\")\n"
                f"p = Path(\"{S}/n.md\")\np.write_text(\"y\")\nPY", T), 2, "a name bound twice"),
    ("N5a", bash(f"python3 - \"{S}\" <<'PY'\nimport sys\nS = sys.argv[1]\nopen(f\"{{S}}/a.patch\",\"w\")\nPY", T), 0,
     "an f-string over an argument"),
    ("N5b", bash(f"python3 - <<'PY'\nROOT = \"{T}\"\nopen(f\"{{ROOT}}/CHANGELOG.md\",\"w\")\nPY", S), 2,
     "an f-string over a known root"),
    ("N7a", bash("for f in $(git ls-files '*.md'); do sed -i 's/a/b/' \"$f\"; done", T), 2, "a loop over git ls-files"),
    ("N7b", bash("f=$(git ls-files | grep CHANGELOG); sed -i 's/a/b/' \"$f\"", T), 2, "an unknown file name"),
    ("N7c", bash("e() { sed -i 's/a/b/' \"$1\"; }; e CHANGELOG.md", T), 2, "a function's argument"),
    ("N8", bash("for f in /tmp/proof2-x.txt CHANGELOG.md; do printf 'x\\n' > \"$f\"; done", T), 2,
     "the second loop value"),
    ("N9", bash("git hash-object CHANGELOG.md", T), 0, "git hash-object"),
    ("N10", bash("M=~/.x-guard-test; echo x >> \"$M/notes.md\"", T), 0, "a tilde in an assignment"),
    ("N12a", bash("python3 -c \"import os; fd = os.open('/tmp/proof2b-a.lock', os.O_RDONLY)\"", T), 0, "os.open"),
    ("N12b", bash("python3 -c \"import gzip; gzip.open('/tmp/a.gz').read()\"", T), 0, "gzip.open for reading"),
    ("N13", bash(f"head -2 {S}/lib.sh > {S}/m.sh; cat >> {S}/m.sh <<'EOF'\nw\nEOF\nbash {S}/m.sh", T), 2,
     "a script built in parts"),
    ("W01", bash("sed -i 's/a/b/' *.md", T), 2, "a leading glob"),
    ("W02", bash("rm -f *.bak", T), 2, "rm of a glob"),
    ("W03", bash("cd tools && sed -i 's/a/b/' *.py", T), 2, "cd, then a glob"),
    ("W05", bash("echo x > \"notes-$(date +%F).md\"", T), 2, "a dated name"),
    ("W06", bash("cp CHANGELOG.md \"CHANGELOG.$(date +%s).bak\"", T), 2, "a timestamped copy"),
    ("W07", bash("sed -i 's/a/b/' $(git ls-files '*.md')", T), 2, "operands from git ls-files"),
    ("W09", bash("git ls-files '*.md' | while read -r f; do sed -i 's/a/b/' \"$f\"; done", T), 2, "a read loop"),
    ("W27", bash("D=$(cd tools && pwd); sed -i s/a/b/ \"$D/x.py\"", T), 2, "$(cd X && pwd)"),
    ("W37", bash("echo x > \"$(realpath .)/n.txt\"", T), 2, "$(realpath .)"),
    ("W32", bash("FILE=CHANGELOG.md bash -c 'sed -i s/a/b/ \"$FILE\"'", T), 2, "a prefix assignment into bash -c"),
    ("W10", bash("cd \"$(git rev-parse --show-toplevel 2>/dev/null)\" && sed -i s/a/b/ CHANGELOG.md", T / "tools"), 2,
     "a top-level substitution with a redirect"),
    ("W34", bash("cd \"${ROOT:-$PWD}\" && sed -i s/a/b/ CHANGELOG.md", T), 2, "a defaulted directory"),
    ("W11", bash("python3 -m tools.check_apply --apply", T), 2, "a module that rewrites files"),
    ("W12", bash("python3 -c \"import subprocess; subprocess.run(['sed', '-i', 's/a/b/', 'CHANGELOG.md'])\"", T), 2,
     "sed through subprocess"),
    ("W13", bash("python3 -c \"import subprocess; subprocess.run(['git', 'stash'])\"", T), 2, "git through subprocess"),
    ("W36", bash("python3 -c \"import subprocess, sys; subprocess.run([sys.executable, '-m', 'ruff', 'format', "
                 "'tools'])\"", T), 2, "ruff format through subprocess"),
    ("W15", bash("python3 -c \"import os; p='CHANGELOG.md'; os.system(f'sed -i s/a/b/ {p}')\"", T), 2,
     "an f-string command"),
    ("W17", bash(f"python3 {S}/default_then_argv.py CHANGELOG.md", T), 2, "a default replaced by argv"),
    ("W16", bash(f"python3 {S}/argp.py {T}/CHANGELOG.md", S), 2, "an argparse target"),
    ("W18", bash(f"python3 - <<'PY'\nfrom pathlib import Path\nROOT = \"{T}\"\nPath(f'{{ROOT}}/CHANGELOG.md')"
                 ".write_text('x')\nPY", S), 2, "an f-string Path"),
    ("W20", bash(f"f() {{ printf 'x\\n' >> CHANGELOG.md; }}; cd {T} && f", S), 2, "a function called after cd"),
    ("W21", bash(f"w() {{ echo x > \"$OUT\"; }}; OUT={T}/n.txt; w", S), 2, "a function reading a later variable"),
    ("W33", bash(f"python3 - < {S}/fixrel.py", T), 2, "python code from a < file"),
    ("W39", bash(f"cat {S}/fixrel.py | python3 -", T), 2, "python code catted in"),
    ("W38", bash(f"bash < {S}/w_rel.sh", T), 2, "a shell script from a < file"),
    ("W22", bash(f"find {T}/tools -name '*.py' -exec sed -i s/a/b/ {{}} +", S), 2, "find -exec with {}"),
    ("W23", bash("find . -exec sh -c 'sed -i s/a/b/ \"$1\"' _ {} \\;", T), 2, "find -exec sh -c"),
    ("W28", bash("xargs sh -c 'for f; do sed -i s/a/b/ \"$f\"; done' _ < /tmp/list", T), 2, "xargs sh -c"),
    ("W24", bash("gh pr checkout 1", T), 2, "gh pr checkout"),
    ("W25", bash("gh run download 1 -n logs", T), 2, "gh run download"),
    ("W26", bash("tmp=$(mktemp notes.XXXXXX); echo x > \"$tmp\"", T), 2, "mktemp with a bare template"),
    ("R2a", bash("perl -ne 'print if /mkdir/' tools/x.py", T), 0, "perl -ne"),
    ("R2b", bash("perl -pe 's/a/b/' CHANGELOG.md", T), 0, "perl -pe to stdout"),
    ("R2c", bash("perl -lane 'print' tools/x.py", T), 0, "perl -lane"),
    ("R2d", bash("ruby -ne 'puts $_' tools/x.py", T), 0, "ruby -ne"),
    ("R2e", bash("node -pe 'process.version'", T), 0, "node -pe"),
    ("R2f", bash("perl -0777 -ne 'print' tools/x.py", T), 0, "perl -0777 -ne"),
    ("R2g", bash("perl -Mstrict -wne 'print' CHANGELOG.md", T), 0, "perl -Mstrict"),
    ("R2h", bash("perl -i -pe 's/a/b/' tools/x.py", T), 2, "perl -i -pe"),
    ("R2i", bash("perl -e '$x = $a / 2; unlink $f; $y = $b / 3'", T), 2, "unlink between two divisions"),
    ("R2j", bash("perl -e 'print $n/2 if 1; mkdir \"out\"'", T), 2, "a slash that divides, then mkdir"),
    ("R2k", bash("perl -e 'print \"unlink\"'", T), 0, "a call name inside a string"),
    ("R2l", bash("perl -ne 'print $a/2 if 1; print if /mkdir/' tools/x.py", T), 0, "a division, then a pattern"),
    ("F1f", bash("fish -c 'for p in $PATH; echo $p; end'", T), 2, "fish text while a run is live (unreadable)"),
    ("F4d", bash("a=( \"$(git stash)\" )", T), 2, "a substitution in an array value"),
    ("F4e", bash("case x in $(git stash)) ;; esac", T), 2, "a substitution in a case pattern"),
    ("F5a", bash("n=$(grep -c esac tools/x.py); echo \"$n\"", T), 0, "esac as an argument"),
    ("F6a", bash("files=(\n  a.py  # the first\n  b.py  # it's the second\n)\nls \"${files[@]}\"", T), 0,
     "an array with comments"),
    ("F6b", bash("a=($'it\\'s'); echo \"${a[@]}\"", T), 0, "an array with an ANSI quote"),
    ("F10.2", bash("echo x > $'CHANGELOG.md\\0b'", T), 2, "a NUL in a redirect path"),
]

LOOPS3 = [
    ("U01", bash("while true; do sleep 1; done; echo \"open", T), 2, "a loop in text the reader cannot follow"),
    ("U02", bash("while true; do echo x; done; echo \"open", T), 0, "unreadable text, no sleep in the loop"),
    ("U03", monitor("pgrep -f cargo; echo \"open", T), 2, "an unreadable Monitor script with pgrep -f"),
    ("U04", monitor("pgrep -f '[c]argo'; echo \"open", T), 0, "an unreadable Monitor script, bracketed"),
    ("L13.2", bash("sleep 9 & sleep 8 & wait -n", T), 2, "wait -n reaps one job"),
    ("L14.2", bash("sleep 9 & wait -n", T), 2, "zsh: wait -n is an error that reaps nothing"),
    ("L15.2", bash("sleep 9 & sleep 8 & wait -n; wait -n", T), 2, "zsh: two wait -n reap nothing"),
    ("L16.2", bash("sleep 9 & wait 123", T), 2, "a literal pid names no child"),
    ("L17.2", bash("sleep 9 & sleep 8 & wait -p got -n", T), 2, "wait -p VAR -n reaps one"),
    ("L18.2", bash("sleep 9 & a=$!; sleep 8 & b=$!; wait -n $a $b", T), 2, "wait -n over two pids reaps one"),
    ("L19", bash("sleep 9 & a=$!; sleep 8 & wait -p got $a", T), 2, "-p takes its variable, not a pid"),
    ("L20", bash("sleep 9 & sleep 8 & wait -p got", T), 2, "zsh: wait -p is an error"),
    ("L10b", bash("tail -f /tmp/x.log | grep --line-buffered FAIL &\nsleep 999 &\nwait $!", T), 2,
     "wait $! reaps only the last job"),
    ("L12b", bash("trap 'echo skill issue' EXIT\nsleep 999 &", T), 2, "a trap that kills nothing"),
    ("O02", monitor("tail -f /tmp/a.log | grep --line-buffered FAIL &\ntail -f /tmp/b.log | grep --line-buffered FAIL "
                    "&\npid=$!\ntrap 'kill $pid' EXIT\nwhile kill -0 123 2>/dev/null; do sleep 15; done", T), 2,
     "a trap that kills one of two jobs"),
    ("O05", bash("sleep 7799 & wait | cat", T), 2, "a wait in a pipeline"),
    ("O04", bash("sleep 7799 &\nfinish() { wait; }", T), 2, "a wait in a function never called"),
    ("O06", bash("echo 'sleep 999' | at now", T), 2, "at"),
    ("F2a", bash("(cd go && go vet ./aletheia/) & (cd rust && cargo fmt --check) & wait", T), 0,
     "background subshells reaped by wait"),
    ("F2b", bash("( sleep 1 ) & wait", T), 0, "a background subshell reaped by wait"),
    ("F3b", bash("time while ! grep -q done /tmp/x.log; do sleep 5; done", T), 2, "time while"),
    ("F4a", monitor("while (( $(pgrep -fc 'cargo test') > 0 )); do sleep 10; done", T), 2,
     "a self-matching pgrep inside arithmetic"),
    ("F4b", monitor("pids=($(pgrep -f cargo)); while kill -0 \"${pids[@]}\" 2>/dev/null; do sleep 5; done", T), 2,
     "a self-matching pgrep inside an array"),
    ("O09", monitor("while pgrep -f '^cargo test' >/dev/null; do sleep 5; done", T), 0, "an anchored pattern"),
    ("O10", monitor("while pgrep -fx 'cargo test' >/dev/null; do sleep 5; done", T), 0, "pgrep -x"),
    ("O08", monitor("while pgrep -f '[Cc]argo' >/dev/null; do sleep 5; done", T), 0, "a two-letter class"),
    ("O07", monitor(f"bash {S}/watch.sh", T), 0, "pgrep inside a script, not on the Monitor's argv"),
]

PROCS3 = [
    ("P41", ["bash", "run_all.sh"], Q2 / "probes", 2),
    ("P42", ["bash", "-euo", "pipefail", "probes/run_all.sh"], Q2, 2),
    ("P43", ["valgrind", "cpp/build/unit_tests"], Q2, 2),
    ("P44", ["python3", "run_ci.py"], Q2 / "tools", 2),
]


def round2_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, event, want, what in SHAPE3:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in WRITES3:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    for cid, event, want, what in LOOPS3:
        rc, err = hook(LOOPS, event)
        rows.append((cid, want, rc, what, err))
    for cid, argv, cwd, want in PROCS3:
        p = start(argv, cwd)
        try:
            rc, err = hook(GUARD, edit("Edit", f"{Q2}/f.txt", Q2))
        finally:
            stop(p)
        rows.append((cid, want, rc, " ".join(argv) + f" @{cwd.name}", err))
    cmd = f"taskset -c 0-19 cargo test > {X} 2>&\u00b2"
    rc, err = hook(GUARD, bash(cmd, Q))
    rows.append(("F7s", 1, int(rc in (0, 2)), f"a superscript fd, exit {rc}", err))
    return rows



# ─── the fixes that gate the install ──────────────────────────────────────────

AAAA = "a" * 40
PRE_WRITES = [
    ("K01", bash(f"L={S}/logs/$(date +%s).log; echo hi > \"$L\"", T), 0, "the house log shape beside a light command"),
    ("K02", bash(f"export L={S}/x-$(date +%s).log; echo hi > \"$L\"", T), 0, "an exported partial log path"),
    ("K03", bash(f"L={T}/logs/$(date +%s).log; echo hi > \"$L\"", S), 2, "a partial path into the live tree"),
    ("K04", bash(f"L={S}/$(date); read L; echo hi > \"$L\"", T), 2, "a partial path forgotten after read"),
    ("K05", bash(f"L={S}/$(date); unset L; echo hi > \"$L\"", T), 2, "a partial path forgotten after unset"),
    ("K06", bash(f"for L in $(ls); do echo hi > \"$L\"; done", T), 2, "a loop variable with no known prefix"),
]
PRE_LOOPS = [
    ("K10", monitor("while pgrep -f 'x{99999999999}' >/dev/null; do sleep 5; done", T), 2, "a repetition re cannot build"),
    ("K11", bash("while pgrep -f 'x{99999999999}' >/dev/null; do sleep 5; done", T), 2, "the same in Bash"),
    ("K12", monitor("while pgrep -f '" + "(" * 2000 + "x" + ")" * 2000 + "' >/dev/null; do sleep 5; done", T), 2,
     "a pattern nested past re's recursion"),
    ("K13", monitor(f"while pgrep -f '(a+)+$' >/dev/null; do sleep 5; done; echo {AAAA}!", T), 0,
     "a backtracking pattern that matches nothing"),
    ("K14", monitor("while pgrep -f '\\<cargo\\>' >/dev/null; do sleep 5; done", T), 2, "word edges, pgrep's dialect"),
    ("K15", monitor("cargo --version >/dev/null; while pgrep -f '^cargo' >/dev/null; do sleep 5; done", T), 0,
     "an anchor the shell's own line starts before"),
    ("K16", monitor("while pgrep -fx '.*cargo test.*' >/dev/null; do sleep 5; done", T), 2,
     "-x with a pattern spanning the whole line"),
    ("K17", monitor("while pgrep -f 'it'\\''s' >/dev/null; do sleep 5; done", T), 0,
     "a quote the shell's line carries escaped, so the pattern never meets itself"),
    ("K24", monitor("echo \"x'y\"; while pgrep -f \"x'\\\"'\\\"'y\" >/dev/null; do sleep 5; done", T), 2,
     "the tool quotes a single quote as '\"'\"' (measured), and a pattern can meet that spelling"),
    ("K18", monitor("sleep 1\nwhile pgrep -f '^while' >/dev/null; do sleep 5; done", T), 0,
     "an anchor that fits the script's second line, not the shell's whole line"),
]


def pre_install_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in PRE_WRITES:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    for cid, event, want, what in PRE_LOOPS:
        t0 = time.monotonic()
        rc, err = hook(LOOPS, event)
        took = time.monotonic() - t0
        rows.append((cid, want, rc if took < 5 else -1, f"{what} ({took:.1f}s)", err))
    # Every shape an advice text recommends passes both hooks.
    rc1, e1 = hook(LOOPS, bash("pgrep -f '[c]argo test'", Q))
    rc2, e2 = hook(GUARD, bash("pgrep -f '[c]argo test'", Q))
    rows.append(("K20", 0, max(rc1, rc2), "MONITOR_ADVICE's capture step", e1 + e2))
    rc, err = hook(LOOPS, monitor("tail -f /tmp/x.log | grep --line-buffered FAIL &\necho work\nwait", T))
    rows.append(("K21", 0, rc, "ORPHAN_ADVICE's bare wait after a pipeline job", err))
    rc, err = hook(LOOPS, monitor("tail -f /tmp/x.log | grep --line-buffered FAIL &\necho work\nwait $!", T))
    rows.append(("K22", 2, rc, "wait $! after a pipeline job leaves the tail", err))
    rc, err = hook(GUARD, bash("cargo test; echo x " + "9" * 5000 + ">f", Q, bg=False))
    rows.append(("K23", 2, rc, "a 5000-digit number before > is a word, not a descriptor", err))
    # A reader fault, made by the reader raising, is reported with exit 1: the guard as installed, imported.
    fault = subprocess.run([sys.executable, "-c", (
        "import importlib.util, io, json, sys\n"
        f"sys.path.insert(0, {str(GUARD.parent)!r})\n"
        f"spec = importlib.util.spec_from_file_location('g', {str(GUARD)!r})\n"
        "g = importlib.util.module_from_spec(spec); sys.modules['g'] = g; spec.loader.exec_module(g)\n"
        "def boom(*a, **k): raise RuntimeError('reader fault')\n"
        "g.C.expand = boom\n"
        f"sys.stdin = io.StringIO(json.dumps({json.dumps(bash('echo hi', Q))}))\n"
        "sys.exit(g.main())\n")], capture_output=True, text=True, check=False, env=HOOK_ENV, timeout=60)
    rows.append(("K23b", 1, fault.returncode, "a reader fault with no run live is reported, exit 1", fault.stderr))
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
    ("J13", bash("(cd go && git status --short) &\nwait $!", T), 0, "a background subshell, wait $!"),
    ("J12", bash("(cd go && git status --short) & p=$!; wait $p", T), 0, "a background subshell, wait $p"),
    ("J14", bash("{ cd go && git status --short; } & wait $!", T), 0, "a background group"),
    ("J15", bash("(cd go && git status --short) & wait -n", T), 2, "zsh: a background subshell, wait -n"),
    ("J17", bash("cd go && git status --short &\nwait $!", T), 0, "a background and-or list"),
    ("J06b", bash("for d in a b c; do (cd \"$d\" && git status --short) & done; wait -n; wait -n; wait -n", T), 2,
     "zsh: three wait -n reap nothing"),
    ("J06c", bash("for d in a b c; do (cd \"$d\" && git status --short) & done; wait -n; wait -n", T), 2,
     "three jobs, two wait -n"),
    ("J01b", bash("pids=()\nfor d in go rust cpp; do\n  (cd \"$d\" && git status --short) & pids+=($!)\ndone\n"
                  "wait \"${pids[@]}\"", T), 0, "wait over an array of pids"),
    ("J04b", bash("python3 -m http.server 8765 & srv=$!\ncurl -s localhost:8765 > /dev/null\nkill $srv", T), 0,
     "a server killed by its pid"),
    ("J05b", bash("python3 -m http.server 8765 & a=$!\npython3 -m http.server 8766 & b=$!\ntrap 'kill $a $b' EXIT\n"
                  "echo work", T), 0, "a trap killing two pids"),
    ("J05c", bash("python3 -m http.server 8765 & a=$!\npython3 -m http.server 8766 & b=$!\ntrap 'kill $a' EXIT\n"
                  "echo work", T), 2, "a trap killing one of two"),
    ("L08.2", bash("sleep 1 & wait\nsleep 999 &", T), 2, "a wait before the job it would reap"),
    ("L09.2", bash("{ sleep 999 & wait; } &", T), 2, "a background group nothing waits for"),
    ("L21", bash("sleep 999 & wait &", T), 2, "a wait that runs in the background"),
    ("L11.2", bash("tail -f /tmp/a | grep x &\nsleep 999 &\ntrap 'kill %1' EXIT\necho work", T), 2,
     "kill %1 stops the first job only"),
    ("L22", bash("tail -f /tmp/a | grep x & wait %1", T), 2, "zsh: a numbered job spec names nothing sure"),
    ("L23", bash("tail -f /tmp/a | grep x & sleep 9 & wait %2", T), 2, "wait %2 leaves the first job"),
    ("L24", bash("coproc sleep 1; wait", T), 0, "a coprocess reaped by a bare wait"),
    ("L25", bash("coproc W { sleep 5; }; wait", T), 0, "a named coprocess reaped"),
    ("L26", bash("bash -c 'sleep 1' & wait", T), 0, "a background carrier"),
    ("L27", bash(f"bash {S}/bg_job.sh & P=$!; wait $P", T), 0, "a background script, wait on its pid"),
    ("L28", bash("cd /tmp && sleep 1 & P=$!; wait $P", T), 0, "cd && sleep in the background"),
    ("L29", bash("git fetch -q & eval wait", T), 0, "eval runs wait in this shell"),
    ("L30", bash("sleep 99 &\nwait \"$pidd\"", T), 2, "wait on a variable never set"),
    ("L31", bash("sleep 9 & p=$!; kill $p", T), 0, "a job killed by its pid"),
    ("L32", bash("tail -f /tmp/a | grep x & p=$!; kill $p", T), 2, "kill $! of a pipeline leaves the tail"),
    ("L33", bash("sleep 999 & sleep 998 &\necho work\nkill $(jobs -p)", T), 2, "zsh: $(jobs -p) prints nothing"),
]

LOOPS6 = [
    ("L40", bash("f() { tail -f /tmp/a | grep x & }; trap f INT; f", T), 2, "a trap's function called directly"),
    ("L41", bash("tail -f /tmp/a | grep x &\ntrap 'kill -s 0 $$' EXIT\necho work", T), 2, "kill -s 0 stops nothing"),
    ("L42", bash("tail -f /tmp/a | grep x &\ntrap 'trap \"\" TERM; kill -s TERM 0' EXIT\necho work", T), 0,
     "kill -s TERM 0 stops the group"),
    ("L43", monitor("while pgrep -f \"$(cat /tmp/pat)\" >/dev/null; do sleep 5; done", T), 2,
     "a pgrep pattern out of sight"),
    ("L44", bash("until timeout 5 tail -f /tmp/a | grep -q DONE; do :; done", T), 2, "a timed tail in a loop"),
    ("L45", bash("while ! read -t 5 < /tmp/fifo; do echo waiting; done", T), 2, "read -t in a loop"),
    ("L46", bash("until inotifywait -t 5 -e close_write /tmp/out; do echo again; done", T), 2,
     "inotifywait -t in a loop"),
    ("L47", bash("until [ -f /tmp/x.done ]; do :; done", T), 2, "a busy loop on a file"),
    ("L47b", bash("i=0; while [ \"$i\" -lt 3 ]; do i=$((i+1)); done", T), 0, "a counting loop of checks ends"),
    ("L47c", bash("while read -r l; do :; done < /tmp/list", T), 0, "a `:` beside a command that is not a check"),
    ("L44b", bash("until taskset -c 0-19 timeout 5 tail -f /tmp/a | grep -q DONE; do echo again; done", T), 2,
     "timeout behind another wrapper"),
    ("L45b", bash("while ! read -rt 5 < /tmp/fifo; do echo waiting; done", T), 2, "read -rt, a cluster"),
    ("L46b", bash("until inotifywait -qt 5 /tmp/out; do echo again; done", T), 2, "inotifywait -qt, a cluster"),
    ("L46c", bash("while read -r line; do echo \"$line\"; done < /tmp/list", T), 0, "read with no timeout"),
    ("L46d", bash("until inotifywait -e close_write /tmp/out; do echo again; done", T), 0,
     "inotifywait with no timeout blocks until the event"),
    ("L48", bash("while kill -0 $p 2>/dev/null; do :; done", T), 2, "a busy loop on a pid"),
    ("L49", bash("for p in $pids; do for t in $p $(pgrep -P $p); do :; done; done", T), 0,
     "a for loop with an empty body ends"),
    ("L50", bash("python3 -c \"import os, time\nwhile not os.path.exists('/tmp/x'):\n    time.sleep(2)\"", T), 2,
     "a Python while loop that sleeps"),
    ("L51", bash("python3 - <<'PY'\nimport time\nfor _ in range(3):\n    time.sleep(1)\nPY", T), 2,
     "a Python for loop that sleeps polls as its shell twin L02 does (the user's ruling, 2026-09-27)"),
    ("L52", bash("python3 tools/waiter.py", Q), 0, "a repository tool's own wait"),
    ("L53", bash(f"python3 {S}/waiter.py", Q), 2, "a scratch script's wait loop"),
    ("L52b", bash("./tools/waiter.py", Q), 0, "a repository tool run by its path"),
    ("L53b", bash(f"{S}/waiter.py", Q), 2, "a scratch script run by its path"),
    ("L52c", bash("python3 -m tools.waiter", Q), 0, "a repository module's own wait"),
    ("L50b", bash("python3 -c \"import asyncio\nasync def m():\n    while True:\n        await asyncio.sleep(1)\n"
                  "asyncio.run(m())\"", T), 2, "an asyncio while loop that sleeps"),
    ("L46e", bash("until inotifywait --timeout 5 /tmp/out; do echo again; done", T), 2, "inotifywait --timeout"),
    ("L54", bash("pidwait -f 'cargo test'; echo finished", T), 2, "pidwait -f on its own shell"),
    ("L55", bash("pidwait -f '[c]argo test'; echo finished", T), 0, "pidwait -f with a bracketed pattern"),
    ("L56", monitor("pidwait -f 'cargo test'; echo finished", T), 2, "Monitor: pidwait -f on its own shell"),
    ("L57", monitor("while ps aux | rg -q 'cargo test'; do sleep 5; done", T), 2, "Monitor: ps | rg meets itself"),
    ("L58", monitor("ps aux | awk '/cargo test/'", T), 2, "Monitor: ps | awk /re/ meets itself"),
    ("L59", monitor("ps aux | rg -q '[c]argo test' && echo up", T), 0, "Monitor: ps | rg bracketed"),
    ("L60", monitor("ps aux | awk '/[c]argo test/ {print $2}'", T), 0, "Monitor: ps | awk bracketed"),
    ("L61", monitor("ps aux | grep -v ' grep ' | grep -q cargo && echo up", T), 2,
     "Monitor: ps lists the tool's grep as ugrep, which ' grep ' does not filter"),
    ("L62", bash("pidwait -f \"[e]cho x' && pwd\"; echo x", T), 2,
     "the Bash line without `< /dev/null` is tried too"),
    ("L63", monitor("ps aux | awk '!/cargo/' | wc -l", T), 0, "Monitor: an awk !/re/ stage removes its matches"),
    ("L64", monitor("bash -c \"ps aux | grep -v ' grep ' | grep -q cargo\"", T), 0,
     "Monitor: bash's grep is the system grep, which ' grep ' filters"),
    ("L65", monitor("ps aux | grep -v ' grep ' | command grep -q cargo", T), 0,
     "Monitor: `command grep` is the system grep"),
    ("L66", monitor("ps aux | grep -v ' grep ' | grep --null -q cargo", T), 0,
     "Monitor: --null sends the tool's grep to the system grep"),
    ("L68", monitor("ps aux | rg -q \"$(cat /tmp/p)\" && echo up", T), 2,
     "Monitor: a pattern out of sight, in an extended-regex stage, may select the line"),
    ("L67", monitor("ps aux | grep -v \"$X\" | grep -q cargo", T), 2,
     "Monitor: a -v pattern out of sight may leave the line"),
]

CPU4 = [
    ("C01.2", bash("taskset -c 0-22 ls", Q), 2, "a light command named onto CPUs 20-22"),
    ("C02.2", bash("taskset -acp 0-23 123", Q), 2, "re-pinning a pid onto 20-23"),
    ("C03.2", bash("taskset -acp 0-19 123", Q), 0, "re-pinning a pid within 0-19"),
    ("C04.2", bash("taskset -p 123", Q), 0, "reading a pid's affinity"),
    ("C04b", bash("taskset -p 1234567", Q), 0, "reading the affinity of a pid whose digits look like a mask"),
    ("C05.2", bash("numactl -C 20-23 ls", Q), 2, "numactl onto 20-23"),
    ("C06.2", bash("taskset 0xf00000 ls", Q), 2, "a mask of 20-23"),
    ("C07.2", bash("taskset -c $X ls", Q), 2, "a CPU list the call leaves unknown"),
    ("C08.2", bash("bash probes/cpu_bad.sh", Q), 2, "a repository script naming 0-23"),
    ("C09.2", bash("bash probes/cpu_ok.sh", Q), 0, "a repository script computing its list from nproc"),
    ("C10.2", bash(f"bash {S}/cpu_bad.sh", Q), 2, "a scratch script naming 0-22"),
    ("C11.2", bash("taskset -c 0-19 ls", Q), 0, "a light command within 0-19"),
    ("C12.2", bash("taskset -c 0-19 cargo test > /tmp/x.log 2>&1", Q), 0, "a heavy command within 0-19"),
]

WRITES4 = [
    ("X1", bash("printf 'tools/x.py\\0' | xargs -0 -I{} cp --parents {} /tmp/copy/", T), 0,
     "xargs copies out of the live tree"),
    ("X1c", bash("printf 'tools/x.py\\0' | xargs -0 sed -i s/a/b/", T), 2, "xargs rewrites files in place"),
    ("X1d", bash("ls | xargs -I{} cp {} sub/", T), 2, "xargs copies into the live tree"),
    ("P1a", bash("python3 -W ignore -c 'print(1)'", T), 0, "python -W VALUE -c"),
    ("P1b", bash("python3 -X utf8 -m json.tool /dev/null", T), 0, "python -X VALUE -m"),
    ("P1c", bash("perl -I lib -e 'print 1'", T), 0, "perl -I DIR -e"),
    ("P1e", bash("python3 -W ignore -c \"open('CHANGELOG.md', 'w')\"", T), 2, "python -W VALUE -c writing"),
]

SHAPE4 = [
    ("P1d", bash("python3 -W ignore -c \"import subprocess; subprocess.run(['cargo', 'test'])\"", Q), 2,
     "a Python driver behind -W VALUE"),
]


def round4_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, event, want, what in LOOPS4 + LOOPS6:
        rc, err = hook(LOOPS, event)
        rows.append((cid, want, rc, what, err))
    for cid, event, want, what in CPU4 + SHAPE4:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in WRITES4:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    return rows



# ─── the fourth round: Python drivers ─────────────────────────────────────────

def py(code: str) -> str:
    return f"python3 - <<'PY'\n{code}\nPY"


def round4_py_fixtures() -> None:
    (S / "cwd_kw.py").write_text(f"import subprocess\nsubprocess.run(['git', 'stash'], cwd='{T}')\n")
    (S / "argv_from.py").write_text("from sys import argv\nopen(argv[1], 'w').write('x')\n")
    (S / "inplace.py").write_text("import fileinput\nfor line in fileinput.input(inplace=True):\n"
                                  "    print(line, end='')\n")


PYWRITES4 = [
    ("Y06", bash("python3 -c \"import subprocess as sp; sp.run(['git', 'stash'])\"", T), 2, "import subprocess as sp"),
    ("Y07", bash("python3 -c \"from os import system; system('git stash')\"", T), 2, "from os import system"),
    ("Y01", bash(f"python3 {S}/cwd_kw.py", S), 2, "a cwd= keyword into the live tree"),
    ("C1", bash("python3 -c \"import subprocess; subprocess.run(['git', 'stash'], cwd='/tmp/siderepo')\"", T), 0,
     "a cwd= keyword out of the live tree"),
    ("Y02", bash(f"python3 -c \"import os; os.chdir('{T}'); open('CHANGELOG.md', 'w')\"", S), 2,
     "os.chdir into the live tree"),
    ("Y04", bash("python3 -c \"import subprocess, sys; subprocess.run(['sed', '-i', 's/a/b/'] + sys.argv[1:])\"", T), 2,
     "an argv list grown at run time"),
    ("Y05", bash(py("import subprocess\ncmd = ['sed', '-i', 's/a/b/']\ncmd.append('CHANGELOG.md')\nsubprocess.run(cmd)"),
                 T), 2, "an argv list appended to"),
    ("Y08", bash("python3 -c \"import subprocess, sys; subprocess.run([sys.argv[1], 'CHANGELOG.md'])\" rm", T), 2,
     "a driver whose program is out of sight"),
    ("W25.2", bash("python3 -c \"import os; os.open('CHANGELOG.md', os.O_WRONLY | os.O_TRUNC)\"", T), 2,
     "os.open with a writing flag"),
    ("W23.2", bash(py("import subprocess\nfor c in ['sed -i s/a/b/ CHANGELOG.md']:\n    subprocess.run(c, shell=True)"),
                 T), 2, "a shell=True command out of sight"),
    ("TF1", bash("python3 -c \"import pathlib, tempfile; pathlib.Path(tempfile.mkdtemp()).joinpath('x')"
                 ".write_bytes(b'a')\"", T), 0, "a path under a tempfile directory"),
    ("TF2", bash(py("import tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as d:\n"
                    "    (Path(d) / 'x').write_text('a')"), T), 0, "a TemporaryDirectory"),
    ("J1", bash(py(f"from pathlib import Path\nW = Path('{S}/wt')\nfor f in ['tools/x.py']:\n"
                   "    (W / f).write_text('x')"), T), 0, "a known directory joined with an unknown name"),
    ("J1b", bash(py(f"from pathlib import Path\nW = Path('{T}')\nfor f in ['tools/x.py']:\n"
                    "    (W / f).write_text('x')"), S), 2, "the live tree joined with an unknown name"),
    ("J1c", bash(py(f"import os\nfor f in ['x']:\n    open(os.path.join('{T}', f), 'w')"), S), 2,
     "os.path.join of the live tree and an unknown name"),
    ("J1d", bash(py(f"for f in ['x']:\n    open(f'{S}/{{f}}.txt', 'w')"), T), 0,
     "an f-string under a known directory"),
    ("W34.2", bash(f"python3 {S}/argv_from.py {T}/CHANGELOG.md", S), 2, "from sys import argv"),
    ("W35", bash(f"python3 {S}/inplace.py {T}/CHANGELOG.md", S), 2, "fileinput in place over argv"),
]

PYSHAPE4 = [
    ("S04", bash("python3 -c \"import subprocess as sp; sp.run(['cargo', 'test'])\"", Q), 2, "an aliased driver"),
    ("S01", bash("python3 -c \"import subprocess, sys; subprocess.run(['cargo', 'test'] + sys.argv[1:])\"", Q), 2,
     "a driver whose argv grows at run time"),
    ("S02", bash("python3 -c \"import subprocess, shlex; subprocess.run(shlex.split('cargo test --release'))\"", Q),
     2, "shlex.split of a constant"),
    ("S03", bash("python3 -c \"import subprocess; subprocess.run('cargo test --release'.split())\"", Q), 2,
     "str.split of a constant"),
    ("S1", bash(py("import subprocess, sys\nTESTS = ['tests/test_x.py']\n"
                   "subprocess.run([sys.executable, '-m', 'pytest', *TESTS, '-q'])"), Q), 0,
     "a targeted pytest with a starred list"),
]


def round4_py_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, event, want, what in PYSHAPE4:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in PYWRITES4:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
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
    ("P01.2", bash(f"bash {S}/one_probe.sh probes/p_reads.sh {S}/one.log", T), 0, "a helper's $2 is a scratch log"),
    ("P02.2", bash(f"bash {S}/one_probe.sh probes/p_reads.sh {T}/one.log", T), 2, "a helper's $2 in the live tree"),
    ("P03.2", bash(f"bash -c 'source \"$1\"' probes/x.sh {S}/readonly_draft.sh", T), 0, "-c text sourcing its $1"),
    ("P04.2", bash(f"bash {T}/probes/p_writes.sh", S), 2, "a probe that cds to its root through $0"),
    ("P05.2", bash(f"bash {T}/probes/p_writes_bs.sh", S), 2, "a probe that cds through BASH_SOURCE"),
    ("P06.2", bash(f"bash {S}/draft/p.sh", T), 0, "a scratch script writing beside itself"),
    ("P07.2", bash("f() { echo x > \"$2\"; }; f a /tmp/ok.txt", T), 0, "a function's $2 out of the tree"),
    ("P08.2", bash("f() { echo x > \"$2\"; }; f a CHANGELOG.md", T), 2, "a function's $2 in the tree"),
    ("E06a", bash(f"echo 'echo x > {T}/CHANGELOG.md' > {S}/e.sh && bash {S}/e.sh", S), 2, "a script echoed, then run"),
    ("E06b", bash(f"printf '%s\\n' 'echo x > {T}/CHANGELOG.md' > {S}/p.sh && bash {S}/p.sh", S), 2,
     "a script printed, then run"),
    ("E06c", bash(f"echo 'echo x > /tmp/ok.txt' > {S}/e2.sh && bash {S}/e2.sh", T), 0, "an echoed script writing elsewhere"),
    ("P4a", bash(f"cat > {S}/s.sh <<'EOF'\necho safe\nEOF\nsed -i 's|echo safe|echo x > {T}/CHANGELOG.md|' {S}/s.sh\n"
                 f"bash {S}/s.sh", S), 2, "a heredoc script edited before it runs"),
]


def round4_param_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in PARAMS4:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    return rows



SMALL4 = [  # (hook, event, want, what, live)
    ("M01.2", "guard", bash("time ! for p in a; do cargo test; done", Q), 2, "time ! before a loop", False),
    ("M02.2", "guard", bash("export R=\"~/x\"; echo x > \"$R/y\"", T), 2, "a quoted ~ stays literal", True),
    ("M03.2", "guard", bash("R=\\~/x; echo x > \"$R/y\"", T), 2, "an escaped ~ stays literal", True),
    ("M04.2", "guard", bash("export R=~/.x-guard-test; echo x > \"$R/y\"", T), 0, "a bare ~ expands", True),
    ("M05.2", "guard", bash("tar -C tools -xf /tmp/a.tar", T), 2, "tar -C before its mode", True),
    ("M06.2", "guard", bash("tar -C /tmp -cf CHANGELOG.tar x", T), 2, "tar -C, then -cf into the tree", True),
    ("M07", "guard", bash("tar -C tools -tf /tmp/a.tar", T), 0, "tar listing", True),
    ("M08", "guard", bash("cabal run shake -- build -V", Q), 2, "shake -V is verbose", False),
    ("M09", "guard", bash("tmux new -ds build 'cargo test'", Q), 2, "tmux -ds NAME runs its text", False),
    ("M10", "loops", bash("tmux new -ds build 'sleep 999'", T), 2, "tmux -ds detaches", False),
    ("M11", "guard", bash("env -S 'cargo test'", Q), 2, "env -S splits its command", False),
    ("M12", "guard", bash(f"find /tmp {T}/tools -maxdepth 0 -exec touch {{}} +", S), 2, "find -exec over its second root",
     True),
    ("M13", "guard", bash("taskset -c 0-$(($(nproc) - 2)) cargo test > /tmp/x.log 2>&1", Q), 2,
     "a computed CPU list, named once", False),
    ("I02", "guard", bash("perl -ne 'print if m!mkdir!' tools/x.py", T), 0, "perl m!...!", True),
    ("I03", "guard", bash("perl -ne 'print if m#rmdir#' tools/x.py", T), 0, "perl m#...#", True),
    ("I04", "guard", bash("perl -e 'my $x = qq{unlink me}; print $x'", T), 0, "perl qq{...}", True),
    ("I14", "guard", bash("ruby -ne 'puts $_ if $_ =~ %r{mkdir}' tools/x.py", T), 0, "ruby %r{...}", True),
    ("I16", "guard", bash("perl -e 'use File::Path qw(make_path); make_path(\"tools/new\")'", T), 2, "File::Path make_path",
     True),
    ("I17", "guard", bash("perl -e '$q->{x}; unlink \"CHANGELOG.md\"'", T), 2, "a variable named q", True),
    ("I18", "guard", bash("perl -e 'my $s = 1; unlink \"CHANGELOG.md\"'", T), 2, "a variable named s", True),
    ("G17", "loops", monitor("while ps aux | grep -q 'cargo test'; do sleep 5; done", T), 2, "ps | grep meets itself",
     False),
    ("G17b", "loops", monitor("while ps aux | grep -q '[c]argo test'; do sleep 5; done", T), 0, "ps | grep, bracketed",
     False),
    ("G17c", "loops", monitor("while ps aux | grep -qF 'cargo test'; do sleep 5; done", T), 2, "ps | grep -F", False),
    ("G18a", "loops", monitor("pgrep -af cargo; echo \"open", T), 2, "fallback: pgrep -af", False),
    ("G18b", "loops", bash("for i in 1 2 3; do sleep 1; done; echo \"open", T), 2, "fallback: a for loop", False),
    ("G18c", "loops", bash("tail -f /tmp/x.log | grep --line-buffered FAIL &\necho \"open", T), 2,
     "fallback: a background job", False),
    ("G13a", "loops", bash("fish -c 'while pgrep -f cargo; sleep 5; end'", T), 2, "a fish polling loop", False),
    ("G13b", "guard", bash("fish -c 'cd rust; and cargo test'", Q), 2, "a heavy run in fish text", False),
    ("G13c", "guard", bash("fish -c 'for f in *.py; echo $f; end'", Q), 0, "light fish text, no run live", False),
    ("G13d", "loops", bash("fish -c 'while true; echo x; end; sleep 1'", T), 0, "a sleep after fish's end", False),
    ("G13e", "guard", bash("fish -c 'if test -d rust; cargo test; end'", Q), 2, "a heavy run in unreadable fish text",
     False),
    ("T01", "guard", bash("trap 'echo x > CHANGELOG.md' EXIT; true", T), 2, "a trap text writing the tree", True),
    ("T02", "guard", bash("trap 'git checkout -- CHANGELOG.md' EXIT; echo hi", T), 2, "a trap text checking out", True),
    ("T03", "guard", bash("trap 'cargo test' EXIT; echo hi", Q), 2, "a heavy run in a trap text", False),
    ("T04", "guard", bash("trap 'echo done > /tmp/x.txt' EXIT; true", T), 0, "a trap text writing elsewhere", True),
    ("F01p", "guard", bash("{ taskset -c 0-19 cargo test | tail -5; } > /tmp/x.log 2>&1", Q), 2,
     "a pipe inside a group redirected whole", False),
    ("F02p", "guard", bash("taskset -c 0-19 bash {S}/tailpipe.sh > /tmp/x.log 2>&1", Q), 2,
     "a pipe inside a script redirected whole", False),
    ("Z27", "guard", bash("".join(f"for v{k} in 1 2 3 4 5 6 7 8; do " for k in range(6)) + "echo x" + "; done" * 6, T),
     0, "six nested loops stay readable while a run is live", True),
]


def round4_small_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, which, event, want, what, needs_live in SMALL4:
        live = start(["cargo", "test"], T) if needs_live else None
        if isinstance(event.get("tool_input", {}).get("command"), str):
            event["tool_input"]["command"] = event["tool_input"]["command"].replace("{S}", str(S))
        try:
            rc, err = hook(GUARD if which == "guard" else LOOPS, event)
        finally:
            if live:
                stop(live)
        if cid == "M13" and "other work" in err:
            rc = -1  # the wording finding: nproc is not other work
        rows.append((cid, want, rc, what, err))
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
    ("Z35c", "f() { " + "".join(f"for a{k} in 1 2 3 4 5 6 7 8; do " for k in range(2)) + 'echo "$( ' + "[[ x ]]; " * 2000
     + ' )"' + "; done" * 2 + "; }; " + "f; " * 32, "32 calls of 8x8 loops around 2000 tests"),
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
    ("U10", bash("python3 -c 'x = ('", T), 0, "Python that cannot compile runs nothing"),
    ("U11", bash("python3.99 -c 'x = ('", T), 2, "Python a newer interpreter may run"),
    ("U12", bash("; ".join(f"A{i}=1; echo $A{i}" for i in range(20000)), T), 2, "too long to read, a run live"),
]


def round4_cost_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, command, what in COST4:
        command = command.replace("{S}", str(S))
        worst = 0.0
        for which in (GUARD, LOOPS):
            t0 = time.monotonic()
            rc, err = hook(which, bash(command, Q))
            worst = max(worst, time.monotonic() - t0)
            if rc not in (0, 2):
                worst = 99.0
        rows.append((cid, 1, int(worst < 2), f"{what} ({worst:.2f}s)", ""))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in UNPARSED4:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
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
    ("Q01", bash("sudo cargo test", Q), 2), ("Q02", bash("stdbuf -oL cargo test", Q), 2),
    ("Q03", bash("setsid cargo test", Q), 2), ("Q04", bash("uv run pytest tests/", Q), 2),
    ("Q05", bash("uv run --with x python -m pytest tests/", Q), 2), ("Q06", bash("ionice -c3 cargo test", Q), 2),
    ("Q07", bash("chrt -i 0 cargo test", Q), 2), ("Q08", bash("doas cargo test", Q), 2),
    ("Q09", bash("unbuffer cargo test", Q), 2), ("Q10", bash("/usr/bin/time -v cargo test", Q), 2),
    ("Q11", bash("command cargo test", Q), 2), ("Q12", bash("exec cargo test", Q), 2),
    ("Q13", bash("strace -f -o /tmp/s.txt cargo test", Q), 2), ("Q14", bash("flock /tmp/l cargo test", Q), 2),
    ("Q15", bash("flock /tmp/l -c 'cargo test'", Q), 2), ("Q16", bash("watch -n 60 cargo test", Q), 2),
    ("Q17", bash("systemd-run --user --scope cargo test", Q), 2), ("Q18", bash("cat <(cargo test)", Q), 2),
    ("Q19", bash("echo `cargo test`", Q), 2),
    ("Q20", bash("python3 -c \"import os; os.popen('cargo test').read()\"", Q), 2),
    ("Q21", bash("python3 -c \"import subprocess; subprocess.getoutput('cargo test')\"", Q), 2),
    ("Q23b", bash("fish -c 'cd rust && cargo test'", Q), 2),
    ("Q25b", bash(f"bash {S}/tailpipe.sh", Q), 2),
    ("Q26", bash(f"cd {Q} && taskset -c 0-19 cargo test > {S}/name.log 2>&1; echo \"EXIT=$?\" >> {S}/name.log", Q), 0),
    ("Q27", bash(f"echo \"start $(date +%T)\"; taskset -c 0-19 cargo test > {XLOG} 2>&1", Q), 2),
    ("Q27b", bash(f"S=$(date +%s); taskset -c 0-19 cargo test > {XLOG} 2>&1", Q), 0),
    ("Q30", bash("screen -dmS w cargo test", Q), 2), ("Q31b", bash("tmux new -d -s build 'cargo test'", Q), 2),
    ("Q33", bash(f"source {S}/heavylib.sh; runit", Q), 2),
    ("Q35", bash(f"{{ exec > {XLOG} 2>&1; }}; taskset -c 0-19 cargo test", Q), 0),
    ("Q35b", bash(f"exec > {XLOG} 2>&1; taskset -c 0-19 cargo test", Q), 0),
    ("Q36", bash("echo \"$(cat <<'EOF'\nx\nEOF)\" && cargo test", Q), 2),
    ("Q37", bash("env -C rust cargo test", Q), 2),
    ("Q38", bash(f"{{ echo x; }} > {XLOG} 2>&1; taskset -c 0-19 cargo test", Q), 2),
    ("Q39", bash(f"source {S}/self_source.sh", Q), 0),
]
CLAIMS_LIVE = [
    ("W01.2", bash("wget https://example.invalid/x", T), 2), ("W02", bash("unzip /tmp/a.zip", T), 2),
    ("W03.2", bash("cargo add serde", T), 2), ("W04", bash("rmdir tools/empty", T), 2),
    ("W05.2", bash("pushd /tmp && popd && echo x > note.txt", T), 2), ("W06", bash(f"pushd {T} && echo x > note.txt", S), 2),
    ("W07.2", bash("python3 - <<< \"open('CHANGELOG.md','w').write('x')\"", T), 2),
    ("W08", bash("python3 - <<'PY'\np: str = 'CHANGELOG.md'\nopen(p, 'w').write('x')\nPY", T), 2),
    ("W09.2", bash(f"python3 - <<'PY'\nimport os\nopen(os.path.join('{T}', 'CHANGELOG.md'), 'w')\nPY", S), 2),
    ("W10.2", bash("python3 - <<'PY'\nfrom pathlib import Path\n(Path.cwd() / 'CHANGELOG.md').write_text('x')\nPY", T), 2),
    ("W11.2", bash(f"python3 - <<'PY'\nfrom pathlib import Path\n(Path('{T}/tools/x.py').parent / 'y.py').write_text('x')\n"
                 "PY", S), 2),
    ("W12.2", bash("python3 - <<'PY'\nfrom pathlib import Path\nPath('CHANGELOG.md').resolve().write_text('x')\nPY", T), 2),
    ("W13.2", bash(f"python3 - <<'PY'\nfrom pathlib import Path\np = Path('{T}/CHANGELOG.md')\np.replace('/tmp/x')\nPY", S),
     2),
    ("W14", bash("python3 - <<'PY'\nimport fileinput\nfor l in fileinput.input('CHANGELOG.md', inplace=True):\n"
                 "    print(l, end='')\nPY", T), 2),
    ("W15.2", bash("python3 -c \"import shutil; shutil.copy('/tmp/a', 'CHANGELOG.md')\"", T), 2),
    ("W16.2", bash("python3 -c \"import os; os.remove('CHANGELOG.md')\"", T), 2),
    ("W17.2", bash("ruby -e \"File.write('CHANGELOG.md', 'x')\"", T), 2),
    ("W18.2", bash("node -e \"require('fs').writeFileSync('CHANGELOG.md', 'x')\"", T), 2),
    ("W19", bash("perl -e 'open(my $f, \">\", \"CHANGELOG.md\")'", T), 2),
    ("W20b", bash("tar -xf /tmp/a.tar -C tools", T), 2),
    ("W21.2", bash(f"source {S}/lib.sh; w", S), 2),
    ("W22.2", bash(f". {S}/env.sh; echo x > \"$OUT/n.txt\"", S), 2),
    ("W26b", bash(f"cd {T} && echo \"open", S), 2),
    ("W27.2", bash(f"cd \"$(dirname {T}/tools/x.py)\" && echo x > n.txt", S), 2),
    ("W28b", bash(f"find {T}/tools /tmp -maxdepth 0 -exec touch {{}} +", S), 2),
    ("W29", bash("echo 'git status' >> .commands-to-run.sh", T), 0), ("W30", bash("chmod +x .commands-to-run.sh", T), 0),
    ("W31", bash(f"grep -c FAIL {XLOG}", T), 0), ("W31b", bash(f"sed -n '1,20p' {XLOG}", T), 0),
    ("W31c", bash(f"grep -n error {XLOG}", T), 0),
    ("W32.2", edit("Write", f"{S}/draft.txt", T), 0), ("W33", bash("pgrep -f 'cargo test'", T, bg=False), 0),
    ("W38.2", bash(f"env -C {T}/tools sed -i s/a/b/ x.py", S), 2),
    ("W39.2", bash(f"{{ L={S}/$(date +%s).log; }}; echo hi > \"$L\"", T), 0),
]
CLAIMS_LOOPS = [
    ("L01.2", bash("sleep 999 &|", T), 2), ("L02", bash("screen -dmS w sleep 999", T), 2),
    ("L03.2", bash("systemd-run --user sleep 999", T), 2), ("L04", bash("daemonize /usr/bin/sleep 999", T), 2),
    ("L05.2", bash("start-stop-daemon -S -b -x /usr/bin/sleep -- 999", T), 2),
    ("L06.2", bash("echo 'sleep 9' | batch", T), 2), ("L07b", bash("tmux new -d -s build 'sleep 999'", T), 2),
    ("L09b", bash("( sleep 999 & wait ) &", T), 2),
    ("L10.2", bash("sleep 998 & a=$!\nsleep 999 & b=$!\ntrap 'kill $a $b' EXIT\necho work", T), 0),
    ("L10c", bash("sleep 998 & a=$!\nsleep 999 & b=$!\ntrap 'kill $a; kill $b' EXIT\necho work", T), 0),
    ("L12.2", bash("tail -f /tmp/x.log | grep --line-buffered FAIL & p=$!\necho work\nwait \"$p\"", T), 2),
    ("L19.2", bash(f"bash {S}/watch.sh", T), 2), ("L20", bash("sleep 999 & (wait)", T), 2),
    ("L23.2", monitor("while ! gh run view 1 --json status | grep -q completed; do sleep 30; done", T), 0),
    ("L24.2", monitor("while kill -0 12345 2>/dev/null; do sleep 5; done", T), 0),
    ("L25.2", monitor("tail -f /tmp/x.log | grep --line-buffered FAIL", T), 0),
    ("L26.2", bash("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep --line-buffered FAIL &\necho work", T), 0),
    ("L27.2", bash("sleep 1 & sleep 2 & wait", T), 0), ("L28", bash("pgrep -f 'cargo test'", T), 0),
    ("L31.2", bash("finish() { wait; }\nsleep 9 &\nfinish", T), 0),
    ("L32.2", bash(f"source {S}/job.sh; wait $!", T), 0),
]


def claims_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, event, want in CLAIMS_QUIET:
        rc, err = hook(GUARD, event)
        rows.append(("c" + cid, want, rc, event["tool_input"].get("command", "")[:50].replace("\n", " "), err))
    for cid, event, want in CLAIMS_LOOPS:
        rc, err = hook(LOOPS, event)
        rows.append(("c" + cid, want, rc, event["tool_input"].get("command", "")[:50].replace("\n", " "), err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want in CLAIMS_LIVE:
            rc, err = hook(GUARD, event)
            rows.append(("c" + cid, want, rc, str(event["tool_input"])[:50].replace("\n", " "), err))
        home = dict(HOOK_ENV, HOME=str(T.parent))  # `cd ~/live` names the live tree
        p = subprocess.run([str(GUARD)], input=json.dumps(bash("cd ~/live && echo \"open", S)), capture_output=True,
                           text=True, check=False, timeout=60, env=home)
        rows.append(("cW26", 2, p.returncode, "cd ~/live, unreadable, a run live", p.stderr))
    finally:
        stop(live)
    return rows



# ─── hotfixes after the fourth round ─────────────────────────────────────────

HOT_QUIET = [  # (id, hook, event, want, what)
    ("H01.2", "guard", bash("grep -n taskset tools/x.py", Q), 0, "a search for the word taskset"),
    ("H02.2", "guard", bash("rg -n taskset docs/", Q), 0, "rg for taskset"),
    ("H03.2", "guard", bash("which taskset", Q), 0, "which taskset"),
    ("H04.2", "guard", bash("taskset --help", Q), 0, "taskset --help"),
    ("H05.2", "guard", bash("git log --oneline --grep taskset", Q), 0, "git log --grep taskset"),
    ("H06.2", "guard", bash("grep -rn numactl -C 20 docs", Q), 0, "grep for numactl with -C 20"),
    ("H07.2", "guard", bash("man taskset", Q), 0, "man taskset"),
    ("H08.2", "guard", bash("sudo taskset -c 20 ls", Q), 2, "taskset under sudo onto CPU 20"),
    ("H09.2", "guard", bash("env X=1 taskset 0xf00000 ls", Q), 2, "taskset under env, a mask of 20-23"),
    ("H10.2", "guard", bash(f"cat > {S}/d.py <<'PY'\nimport subprocess\nsubprocess.run(['cargo', 'test'])\nPY\n"
                          f"python3 {S}/d.py", Q), 2, "a heredoc driver the call runs with python3"),
    ("H11", "guard", bash(f"cat > {S}/t.sh <<'SH'\ncargo test 2>&1 | tail -5\nSH\ntimeout 600 bash {S}/t.sh", Q), 2,
     "a heredoc script run through timeout"),
    ("H12.2", "guard", bash(f"cat > {S}/t2.sh <<'SH'\ncargo test 2>&1 | tail -5\nSH\ntime bash {S}/t2.sh", Q), 2,
     "a heredoc script run through time"),
    ("H13.2", "loops", bash(f"cat > {S}/j.sh <<'SH'\nsleep 99 &\nSH\ntimeout 60 bash {S}/j.sh", Q), 2,
     "a heredoc script with a job, run through timeout"),
    ("H14.2", "guard", bash("numactl -C 0-3 cargo test", Q), 2, "a heavy run under numactl, not taskset"),
]
HOT_LIVE = [
    ("H20", bash(f"cat > {S}/r.py <<'PY'\nprint(1)\nPY\npython3 {S}/r.py", T), 0, "a heredoc script run with python3"),
    ("H21", bash(f"cat > {S}/r.sh <<'SH'\necho hi > /tmp/ok.txt\nSH\ntimeout 60 bash {S}/r.sh", T), 0,
     "a heredoc script run through timeout"),
    ("H22", bash(f"cat > {S}/w2.py <<'PY'\nopen('{S}/ok2.txt', 'w').write('x')\nPY\npython3 {S}/w2.py", T), 0,
     "a heredoc driver writing elsewhere"),
    ("H23", bash(f"cat > {S}/e.sh <<'SH'\necho safe\nSH\nsed -i 's|echo safe|echo x > {T}/CHANGELOG.md|' {S}/e.sh "
                 f"2>/dev/null\nbash {S}/e.sh", S), 2, "a heredoc script edited, with a redirect, before it runs"),
    ("H24", bash(f"cat > {S}/m.sh <<'SH'\necho safe\nSH\ncp /tmp/other.sh {S}/m.sh\nbash {S}/m.sh", T), 2,
     "a heredoc script replaced by cp before it runs"),
    ("H25", bash(f"cat > {S}/s5.sh <<'SH'\necho safe\nSH\ntimeout 5 sed -i 's|echo safe|echo x > CHANGELOG.md|' {S}/s5.sh\n"
                 f"bash {S}/s5.sh", T), 2, "a heredoc script edited through a wrapper"),
    ("H26", bash(f"cat > {S}/a.sh <<'SH'\necho safe\nSH\ngawk -i inplace '{{print}}' {S}/a.sh\nbash {S}/a.sh", T), 2,
     "a heredoc script edited by gawk in place"),
]


def hotfix_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, which, event, want, what in HOT_QUIET:
        rc, err = hook(GUARD if which == "guard" else LOOPS, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in HOT_LIVE:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    return rows



# ─── the tool's shell is zsh: job control by dialect ─────────────────────────

ZSH5 = [
    ("Z30", bash("bash -c 'sleep 9 & wait -n'", T), 0, "bash: wait -n reaps the one job"),
    ("Z31", bash("bash -c 'sleep 9 & sleep 8 & wait -n; wait -n'", T), 0, "bash: two wait -n reap two jobs"),
    ("Z32", bash("bash -c 'sleep 9 & sleep 8 & wait -p got'", T), 0, "bash: wait -p VAR alone reaps every job"),
    ("Z33", bash("bash -c 'for d in a b c; do (cd \"$d\" && git status --short) & done; wait -n; wait -n; wait -n'", T),
     0, "bash: three jobs, three wait -n"),
    ("Z34", bash("bash -c 'tail -f /tmp/a | grep x & wait %1'", T), 0, "bash: wait %1 reaps every process of the job"),
    ("Z35", bash("tail -f /tmp/a | grep x & wait %%", T), 0, "zsh: wait %% reaps the current job"),
    ("Z36", bash("bash -c 'sleep 999 & sleep 998 & kill $(jobs -p)'", T), 0, "bash: kill $(jobs -p)"),
    ("Z37", bash("bash -c 'sleep 1 & sleep 2 & wait %1 %2'", T), 0, "bash: two job specs, numbered by start"),
    ("Z38", bash("bash -c 'sleep 0.2 & sleep 999 & wait %1; wait %1'", T), 2, "bash: %1 twice leaves the second job"),
    ("Z39", bash("sleep 9 &! echo x", T), 2, "zsh &! disowns the job"),
    ("Z40", bash("sleep 9 &| echo x", T), 2, "zsh &| disowns the job"),
    ("Z41", bash("trap 'trap \"\" TERM; kill 0' EXIT\ntail -f /tmp/x.log | grep --line-buffered FAIL &\necho work", T), 0,
     "the advised trap: the group stopped, the call's status kept"),
    ("Z42", bash("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep x &\ntrap 'rm -f /tmp/t' EXIT", T), 2,
     "an EXIT trap replaced by one that kills nothing"),
    ("Z43", bash("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep x &\ntrap - EXIT", T), 2, "an EXIT trap reset"),
    ("Z44", bash("sleep 998 & a=$!\nsleep 999 & b=$!\ntrap 'kill $a; wait $a' EXIT\necho work", T), 2,
     "a trap's kill and wait of one pid count once"),
    ("Z45", bash("pids=()\nsleep 998 & pids+=($!)\nsleep 999 & pids+=($!)\nkill \"${pids[@]}\"", T), 0,
     "kill over an array of pids"),
    ("Z46", bash("( git fetch -q & wait ) & wait", T), 0, "a wait inside a background subshell"),
    ("Z47", bash("sleep 999 & p=$!; kill -0 $p && echo alive", T), 2, "kill -0 stops nothing"),
    ("Z48", bash("sleep 2 & echo x | wait", T), 0, "zsh runs a pipeline's last wait in the shell"),
    ("Z49", bash("bash -c 'sleep 2 & echo x | wait'", T), 2, "bash runs a pipeline's wait in a subshell"),
    ("Z39b", bash("sleep 9 &!\nwait", T), 2, "zsh: a wait does not reap a disowned job"),
    ("Z50", bash("{S}/bashjob.sh", T), 0, "a bash script by its shebang: wait -n reaps"),
    ("Z51", bash("sh {S}/shjob.sh", T), 2, "an sh script: wait -n is an error"),
]


def zsh_cases() -> list[tuple[str, int, int, str, str]]:
    (S / "bashjob.sh").write_text("#!/usr/bin/env bash\nsleep 9 & wait -n\n")
    (S / "shjob.sh").write_text("sleep 9 & wait -n\n")
    rows = []
    for cid, event, want, what in ZSH5:
        event["tool_input"]["command"] = event["tool_input"]["command"].replace("{S}", str(S))
        rc, err = hook(LOOPS, event)
        rows.append((cid, want, rc, what, err))
    return rows



# ─── the fifth round: stdin order, group files, git options ──────────────────

R5_LIVE = [
    ("R5a", bash("bash <<'EOF'\npython3 - <<'PY'\nopen('CHANGELOG.md', 'w').write('x')\nPY\nEOF", T), 2,
     "python3 - inside a heredoc-fed bash reads its own heredoc"),
    ("R5b", bash("bash <<'EOF'\necho \"open('CHANGELOG.md', 'w').write('x')\" | python3 -\nEOF", T), 2,
     "a pipe inside a heredoc-fed bash feeds python3 -"),
    ("R5c", bash("python3 - <<'A' <<'B'\nopen('CHANGELOG.md', 'w')\nA\nprint(1)\nB", T), 2,
     "zsh reads both heredocs, the first writing"),
    ("R5c2", bash("bash -c \"python3 - <<'A' <<'B'\nopen('CHANGELOG.md', 'w')\nA\nprint(1)\nB\"", T), 0,
     "bash reads only the last heredoc"),
    ("R5d", bash(f"{{ echo 'echo x > CHANGELOG.md'; echo 'echo done'; }} > {S}/g.sh; bash {S}/g.sh", T), 2,
     "a script built by a group, its first line writing"),
    ("R5e", bash(f"(echo 'echo x > CHANGELOG.md'; echo 'echo done') > {S}/s.sh; bash {S}/s.sh", T), 2,
     "a script built by a subshell"),
    ("R5f", bash(f"{{ echo 'echo x > /tmp/ok.txt'; echo 'echo done'; }} > {S}/g2.sh; bash {S}/g2.sh", T), 0,
     "a script built by a group, writing elsewhere"),
    ("R5g", bash(f"git clone --depth 1 https://example.invalid/x /tmp/x-clone", T), 0, "clone --depth N into /tmp"),
    ("R5h", bash("git clone -b main . /tmp/x-clone2", T), 0, "clone -b BRANCH . DIR"),
    ("R5i", bash("git init -b main /tmp/x-fresh", T), 0, "init -b BRANCH DIR"),
    ("R5j", bash("git clone --depth 1 /tmp/src sub/clone", T), 2, "clone --depth into the tree"),
    ("R5k", bash("git clone -o upstream /tmp/src sub/clone", T), 2, "clone -o NAME into the tree"),
    ("R5l", bash("git init --template /tmp/tpl", T), 2, "init --template re-initialises the tree"),
    ("V01", bash(f"f() {{ sed -i s/a/b/ \"$@\"; }}; f {T}/CHANGELOG.md", S), 2, "a function's \"$@\""),
    ("V02.2", bash(f"bash {S}/fwd.sh {T}/CHANGELOG.md", S), 2, "a script forwarding \"$@\""),
    ("V03", bash(f"bash {S}/shifted.sh a {T}/CHANGELOG.md", S), 2, "a script that shifts, then writes $1"),
    ("V04", bash(f"f() {{ for g; do echo x > \"$g\"; done; }}; f /tmp/ok.txt {T}/CHANGELOG.md", S), 2,
     "for with no in: every positional parameter"),
    ("V05", bash(f"set -- a {T}/CHANGELOG.md; echo x > \"$2\"", S), 2, "set -- rebinds the parameters"),
    ("V06.2", bash(f"ROOT={T}/; echo x > \"${{ROOT%/}}/note.txt\"", S), 2, "a trimmed variable keeps its value"),
    ("V07.2", bash(f"D={T}; rm -f \"${{D:?}}/note.txt\"", S), 2, "${D:?} keeps its value"),
    ("V08.2", bash(f"cd \"{T}/$(printf tools)\" && echo x > n.txt", S), 2, "cd to a partly known directory"),
    ("V09.2", bash(f"source {S}/lib1.sh {T}/CHANGELOG.md", S), 2, "source with arguments"),
    ("V10.2", bash(f"cd \"$(ls -d {T}/too*)\" && echo x > n.txt", S), 2, "cd to an unknown directory the call names"),
    ("V11.2", bash("f() { shift; echo x > \"$1\"; }; f CHANGELOG.md /tmp/ok.txt", T), 0, "shift moves $2 into $1"),
    ("V12.2", bash(f"X={T}:/tmp/other; echo x > \"${{X%%:*}}/n.txt\"", S), 2, "a %% trim lands in the tree"),
    ("V13.2", bash("D=/tmp; rm -f \"${D:?}/note.txt\"", T), 0, "${D:?} out of the tree"),
    ("V14.2", bash(f"cd \"/tmp/$(printf x)\" && echo x > n.txt; cat {T}/CHANGELOG.md", S), 0,
     "a partly known cd out of the tree, the tree only read"),
    ("J3", bash("trap 'echo \"done' EXIT; echo x", T), 2, "an unreadable trap text while a run is live"),
    ("K1", bash(f"X=/tmp/ok.txt bash {S}/w_env.sh; X=CHANGELOG.md bash {S}/w_env.sh", T), 2,
     "one script run twice with different assignments"),
    ("L1", bash(f"printf 'w() {{ echo x > \"$1\"; }}\\n' > {S}/lib2.sh; source {S}/lib2.sh; w CHANGELOG.md", T), 2,
     "a file the call writes, then sources"),
    ("N1", bash(f"echo \"a|{T}/CHANGELOG.md", S), 2, "an unreadable call naming the tree after a |"),
    ("T1", bash("python3 - <<'PY'\nimport subprocess\ndef run(d):\n    subprocess.run(['git', 'status'], cwd=d)\n"
                "run('/tmp/siderepo')\nPY", T), 0, "a read-only command with a runtime cwd="),
    ("T2", bash("python3 - <<'PY'\nimport subprocess\ndef run(d):\n    subprocess.run(['touch', 'x'], cwd=d)\n"
                "run('/tmp/siderepo')\nPY", T), 2, "a writing command with a runtime cwd="),
]
R5_QUIET = [
    ("R5m", bash("bash <<'EOF'\npython3 - <<'PY'\nimport subprocess\nsubprocess.run(['cargo', 'test'])\nPY\nEOF", Q), 2,
     "a heavy driver inside a heredoc-fed bash"),
    ("J1.2", bash(f"source {S}/fish.fish; cargo test", Q), 2, "an unreadable sourced file hides nothing after it"),
    ("J2", bash("trap 'echo \"done' EXIT; cargo test", Q), 2, "an unreadable trap text hides nothing after it"),
    ("K2", bash(f"taskset -c 0-19 bash {S}/r_heavy.sh > /tmp/x.log 2>&1; bash {S}/r_heavy.sh", Q), 2,
     "one heavy script run blessed, then bare"),
    ("M1", bash("{ R=$(taskset -c 0-19 cargo test); } > /tmp/x.log 2>&1", Q), 2, "a heavy run captured into a variable"),
    ("M2", bash("exec > /tmp/x.log 2>&1; R=$(taskset -c 0-19 cargo test); echo \"EXIT=$?\"", Q), 2,
     "a capture after exec >log"),
]


def round5_cases() -> list[tuple[str, int, int, str, str]]:
    (S / "fwd.sh").write_text('sed -i s/a/b/ "$@"\n')
    (S / "shifted.sh").write_text('shift\necho x > "$1"\n')
    (S / "lib1.sh").write_text('echo x > "$1"\n')
    (S / "fish.fish").write_text("for f in *.py; echo $f; end\n")
    (S / "w_env.sh").write_text('echo x > "$X"\n')
    (S / "r_heavy.sh").write_text("cargo test\n")
    rows = []
    for cid, event, want, what in R5_QUIET:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in R5_LIVE:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    return rows



ROBUST5 = [  # (id, hook, event, want)
    ("RB1", "loops", monitor("while pgrep -f cargo >/dev/null; do sleep 5; done; echo \ud800", T), 2),
    ("RB2", "loops", bash("while pgrep -f cargo >/dev/null; do sleep 5; done; echo \ud800", T), 2),
    ("RB3", "loops", monitor("while pgrep -f \"$X\"$'\\0' >/dev/null; do sleep 5; done", T), 2),
    ("RB4", "guard", bash("\ud800/python <<''\nimport", Q), 0),
    ("RB5", "guard", dict(bash("cargo test", Q), hook_event_name="PostToolUseFailure"), 0),
    ("RB6", "loops", dict(bash("while true; do sleep 1; done", T), hook_event_name="PostToolUse"), 0),
    ("RB7", "loops", dict(bash("while true; do sleep 1; done", T), hook_event_name="PreToolUse"), 2),
    ("RB8", "loops", monitor("P=cargo; while pgrep -f \"$P\" >/dev/null; do sleep 5; done", T), 2),
    ("RB9", "loops", monitor("while pgrep -f \"$UNSET_PATTERN_X\" >/dev/null; do sleep 5; done", T), 2),
    ("GR1", "loops", monitor("while ps aux | grep cargo | grep -v grep > /dev/null; do sleep 5; done", T), 0),
    ("GR2", "loops", monitor("while ps aux | grep -v grep | grep -q 'cargo test'; do sleep 10; done", T), 0),
    ("GR3", "loops", monitor("while ps aux | grep -q 'x+y'; do sleep 5; done", T), 2),
    ("GR4", "loops", monitor("while ps aux | grep -q 'run(x)'; do sleep 5; done", T), 2),
    ("GR5", "loops", monitor("while ps aux | grep -Eq 'x+y'; do sleep 5; done", T), 0),
    ("GR6", "loops", monitor("while pgrep -if '[c]argo test' >/dev/null; do sleep 5; done; echo Cargo test finished", T), 2),
    ("GR7", "loops", monitor("while pgrep -f -- '-x' >/dev/null; do sleep 5; done", T), 2),
    ("GR8", "loops", monitor("while pgrep -f '[u]nset -f' >/dev/null; do sleep 5; done", T), 2),
    ("GR9", "loops", monitor("while pgrep -f '[/]home/user/[.]claude' >/dev/null; do sleep 5; done", T), 0),
    ("GR10", "loops", monitor("while ps aux | grep -q 'car''go test'; do sleep 5; done", T), 2),
    ("GR11", "loops", monitor("while ps aux | grep -q '[c]argo test'; do sleep 5; done", T), 0),
    ("GR12", "loops", monitor("while ps aux | grep -v grep > /dev/null; do sleep 5; done", T), 0),
]


def robust5_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, which, event, want in ROBUST5:
        rc, err = hook(GUARD if which == "guard" else LOOPS, event)
        rows.append((cid, want, rc, ascii(event.get("tool_input", {}).get("command", ""))[:40], ascii(err)))
    return rows



PY5_LIVE = [
    ("PY1", bash("python3 -c \"m = 'w'; open('CHANGELOG.md', m).write('x')\"", T), 2, "a mode held in a name"),
    ("PY2", bash("python3 -c \"open(file='CHANGELOG.md', mode='w')\"", T), 2, "open with keywords"),
    ("PY3", bash("python3 -c \"import tarfile; tarfile.open('CHANGELOG.tar', 'w')\"", T), 2, "tarfile.open for writing"),
    ("PY4", bash("python3 -c \"import tarfile; tarfile.open('/tmp/a.tar').extractall('tools')\"", T), 2,
     "extractall into the tree"),
    ("PY5", bash("python3 -c \"import os; os.utime('CHANGELOG.md')\"", T), 2, "os.utime"),
    ("PY6", bash("python3 -c \"import shutil; shutil.unpack_archive('/tmp/a.zip', 'tools')\"", T), 2, "unpack_archive"),
    ("PY7", bash("python3 - <<EOF\nn = $N\nopen('CHANGELOG.md', 'w').write('x')\nEOF", T), 2,
     "Python with a shell expansion the reader cannot see"),
    ("PY8", bash("python3 -c \"import subprocess; run = subprocess.run; run(['git', 'stash'])\"", T), 2,
     "a subprocess function bound to a name"),
    ("PY9", bash("python3 -c \"open('/tmp/r.txt').read()\"", T), 0, "open to read"),
    ("PY10", bash("python3 -c \"import tarfile; tarfile.open('/tmp/a.tar').extractall('/tmp/out')\"", T), 0,
     "extractall elsewhere"),
]
PY5_QUIET = [
    ("PY11", bash("python3 - --full <<'PY'\nimport subprocess, sys\nif '--full' in sys.argv:\n    cmd = ['cargo', 'test']\n"
                  "else:\n    cmd = ['ls']\nsubprocess.run(cmd)\nPY", Q), 2, "a heavy command behind an if"),
    ("PY12", bash("taskset -c 0-19 python3 -c \"import subprocess; subprocess.run(['taskset', '-c', '0-23', 'ls'])\" "
                  "> /tmp/x.log 2>&1", Q), 2, "Python starting a command on CPUs 20-23"),
    ("PY13", bash("taskset -c 0-19 python3 -c \"import os; os.system('cargo test 2>&1 | tail -5')\" > /tmp/x.log 2>&1",
                  Q), 2, "Python piping a heavy run into tail"),
    ("PY14", bash("taskset -c 0-19 python3 -c \"import subprocess; subprocess.run(['cargo', 'test'])\" > /tmp/x.log 2>&1",
                  Q), 0, "a blessed Python driver"),
]


def py5_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, event, want, what in PY5_QUIET:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in PY5_LIVE:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    return rows



SMALL5_LIVE = [
    ("EW1", {"hook_event_name": "PreToolUse", "tool_name": "EnterWorktree", "tool_input": {"name": "x"}, "cwd": str(T)},
     2, "EnterWorktree makes a worktree inside the live tree"),
    ("EW3", {"hook_event_name": "PreToolUse", "tool_name": "EnterWorktree", "tool_input": {"path": str(W)},
             "cwd": str(T)}, 0, "EnterWorktree into a worktree that exists writes nothing"),
    ("AG1", {"hook_event_name": "PreToolUse", "tool_name": "Agent",
             "tool_input": {"description": "d", "prompt": "p", "isolation": "worktree"}, "cwd": str(T)}, 2,
     "an agent in a worktree makes one inside the live tree"),
    ("AG2", {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {"description": "d", "prompt": "p"},
             "cwd": str(T)}, 0, "an agent sharing the session's tree writes nothing by itself"),
    ("XW1", {"hook_event_name": "PreToolUse", "tool_name": "ExitWorktree", "tool_input": {"action": "remove"},
             "cwd": str(T)}, 2, "ExitWorktree removes a worktree a run is live in"),
    ("XW2", {"hook_event_name": "PreToolUse", "tool_name": "ExitWorktree", "tool_input": {"action": "keep"},
             "cwd": str(T)}, 0, "ExitWorktree keep writes nothing"),
    ("XW3", {"hook_event_name": "PreToolUse", "tool_name": "ExitWorktree", "tool_input": {"action": "remove"},
             "cwd": str(Q)}, 0, "ExitWorktree remove with no run live there"),
    ("CM1", bash("mkdir -p cpp/build && cd cpp/build && cmake ..", T), 2, "a cmake configure without -B"),
    ("CM4", bash("cmake cpp", T), 2, "cmake with a source directory only"),
    ("CM6", bash("cmake --version", T), 0, "cmake --version"),
    ("CM7", bash("cmake -E echo x", T), 0, "cmake -E"),
    ("X28", bash("cargo --color never clean", T), 2, "a cargo global option before clean"),
    ("X29", bash("cargo --config net.offline=true update", T), 2, "cargo --config before update"),
    ("QP1", bash("perl -e 'system(\"git stash\")'", T), 2, "perl system()"),
    ("QP2", bash("ruby -e \"File.open('CHANGELOG.md', 'w')\"", T), 2, "ruby File.open for writing"),
    ("QP3", bash("node -e \"require('fs').createWriteStream('CHANGELOG.md')\"", T), 2, "node createWriteStream"),
    ("QP4", bash("node -e \"require('child_process').execSync('git stash')\"", T), 2, "node execSync"),
    ("QP5", bash("perl -e 'print \"system\"'", T), 0, "a word inside a perl string"),
    ("SF1", bash("git submodule foreach 'git clean -fdx'", T), 2, "git submodule foreach"),
]
SMALL5_QUIET = [
    ("EW2", {"hook_event_name": "PreToolUse", "tool_name": "EnterWorktree", "tool_input": {"name": "x"}, "cwd": str(Q)},
     0, "EnterWorktree with no run live"),
    ("CG1", bash("bash probes/cpu_getconf.sh", Q), 2, "a repository script counting the machine's CPUs"),
]


def small5_cases() -> list[tuple[str, int, int, str, str]]:
    (Q / "probes" / "cpu_getconf.sh").write_text('taskset -c "0-$(($(getconf _NPROCESSORS_ONLN) - 1))" ls\n')
    rows = []
    for cid, event, want, what in SMALL5_QUIET:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in SMALL5_LIVE:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    return rows



R6_LIVE = [  # the sixth round: contexts, dialects, budgets, Python pending code
    ("CX1", bash(f"(cd {T} && sed -i s/a/b/ CHANGELOG.md) &\nwait", S), 2, "cd inside a background subshell"),
    ("CX2", bash(f"(cd {T} && sed -i s/a/b/ CHANGELOG.md) | cat", S), 2, "cd inside a piped subshell"),
    ("CX3", bash(f"x=$(cd {T} && git stash)", S), 2, "cd inside a substitution"),
    ("CX4", bash(f"(cd {S} && echo x > out.txt) &\nwait", T), 0, "cd out of the tree inside a background subshell"),
    ("CX5", bash("tests=$(cd python && .venv/bin/python -m pytest tests/test_x.py -q 2>&1 | tail -1)", T), 0,
     "a targeted pytest in a substitution after cd"),
    ("CX6", bash(f"true | cd {T}; echo x > n.txt", S), 2, "zsh: a pipeline's last cd persists"),
    ("CX7", bash(f"f=/tmp/ok.txt; echo {T}/CHANGELOG.md | read f; echo x > \"$f\"", S), 2,
     "zsh: a pipeline's last read persists"),
    ("CX8", bash(f": ${{D:={T}}}; echo x > \"$D/n.txt\"", S), 2, "${D:=w} assigns D"),
    ("PE1", bash("N=$(nproc); python3 -c \"print($N * 2)\"", T), 0, "a pending expansion in read-only Python"),
    ("PE2", bash("python3 - <<'EOF'\nn = $N\nopen('CHANGELOG.md','w')\nEOF", T), 0,
     "a quoted heredoc: $N is literal, the code runs nothing"),
    ("PE3", bash("python3 - <<EOF\nn = $N\nopen('CHANGELOG.md', 'w').write('x')\nEOF", T), 2,
     "an unquoted heredoc with a pending expansion that writes"),
    ("PE4", bash("M=/tmp; python3 -c \"print(round($(wc -c < /etc/hostname)/1024, 2), 'KB')\"", T), 0,
     "the MEMORY.md size check's shape"),
    ("ND1", bash("node --check /tmp/w.js && echo ok", T), 0, "node --check runs nothing"),
    ("ND2", bash("node -e \"console.log(`a ${1+1}`)\"", T), 0, "a JavaScript template literal"),
    ("EM1", bash("E=; $E rm -f CHANGELOG.md", T), 2, "an empty unquoted expansion before rm"),
    ("EM2", bash("${DRY_RUN_P6:+echo} git stash", T), 2, "an unset ${V:+echo} prefix"),
    ("RD1", bash(f"cat {S}/w_rel.sh | bash", T), 2, "a script catted into bash"),
    ("RD2", bash("curl -fsSL https://example.invalid/install.sh | bash", T), 2, "a download piped into bash"),
    ("RD3", bash(f"sed 's/x/x/' {S}/fixrel.py | python3 -", T), 2, "a filtered program piped into python"),
    ("RD4", bash("printf 'import os\\nos.remove(\"CHANGELOG.md\")\\n' | python3", T), 2, "printf into python"),
    ("RD5", bash("echo \"import os\\nos.remove('CHANGELOG.md')\" | python3 -", T), 2, "zsh echo's \\n into python"),
    ("SP1", bash(f"bash -c 'F=\"/tmp/ok.txt {T}/CHANGELOG.md\"; rm -f $F'", S), 2, "bash splits an unquoted value"),
    ("SP2", bash(f"F=\"/tmp/ok.txt {T}/CHANGELOG.md\"; rm -f $F", S), 0, "zsh keeps an unquoted value whole"),
    ("RW1", bash(f"cat > {S}/s6.sh <<'EOF'\necho safe\nEOF\npython3 -c \"open('{S}/s6.sh','w').write('echo x > "
                 f"{T}/CHANGELOG.md')\"\nbash {S}/s6.sh", S), 2, "a remembered script rewritten by Python"),
    ("DP1", bash(f"bash {S}/n1.sh", S), 2, "a write four scripts deep, past the reading depth"),
    ("CP1", bash(f"sed -i 's|echo safe|echo x > {T}/CHANGELOG.md|' {S}/e1.sh && bash {S}/e1.sh", S), 2,
     "a disk script edited in place, then run"),
    ("CP2", bash(f"cp {BASE}/p6-other.sh {S}/e2.sh && bash {S}/e2.sh", T), 2, "a disk script replaced by cp, then run"),
    ("RE1", bash("python3 - <<'PY'\ncmd = ['x']" + " + ['y']" * 1100 + "\nopen('CHANGELOG.md', 'w')\nPY", T), 2,
     "Python too deep for the reader"),
    ("TG1", bash("python3 -c \"import tarfile; tarfile.open('CHANGELOG.tar.gz', 'w:gz')\"", T), 2, "tarfile 'w:gz'"),
    ("MA1", bash("python3 -c \"import shutil; shutil.make_archive('CHANGELOG', 'zip', '/tmp')\"", T), 2,
     "shutil.make_archive"),
    ("XT1", bash(f"python3 -c \"import tarfile; t = tarfile.open('/tmp/a.tar'); t.extract('m', '{T}/tools')\"", S), 2,
     "extract's path argument"),
    ("BY1", bash("python3 -c \"import zipfile, io; zipfile.ZipFile(io.BytesIO(), 'w').writestr('a', 'b')\"", T), 0,
     "a zip in memory"),
    ("OE1", bash("python3 -c \"import os; os.execvp('git', ['git', 'stash'])\"", T), 2, "os.execvp"),
    ("V01.2", bash("echo " + "$(true) " * 1000 + "; echo ok", T), 0, "a thousand substitutions stay within the budget"),
    ("V02.3", bash("(cd /tmp; echo x > f) & wait", T), 0, "a background subshell's cd holds for its list"),
    ("V03.2", bash("{ export D=/tmp; echo x > $D/f; } & wait", T), 0, "export in a background group"),
    ("V04.2", bash('{ set -- CHANGELOG.md /tmp/ok; shift; echo x > "$1"; } & wait', T), 0,
     "set -- and shift in a background group"),
    ("V05.2", bash("{ D=/tmp; unset D; echo x > ${D:-CHANGELOG.md}; } & wait", T), 2, "unset in a background group"),
    ("V06.3", bash('echo "$C" | bash', T), 2, "a program piped into bash that the reader cannot know"),
    ("V07.3", bash("python3 -c \"x = ${N}; open('CHANGELOG.md', 'w')\"", T), 2,
     "Python holding a shell expansion, read with a placeholder"),
    ("V08.3", bash('python3 -c "${A} ${B}"', T), 2, "Python that parses neither way, holding expansions"),
    ("V09.3", bash(f"printf 'echo x \\x3e CHANGELOG.md\\n' > {S}/px7.sh; bash {S}/px7.sh", T), 2, "printf's hex escape"),
    ("V10.3", bash(f"printf 'echo x \\076 CHANGELOG.md\\n' > {S}/po7.sh; bash {S}/po7.sh", T), 2,
     "printf's octal escape"),
    ("V11.3", bash(f"echo 'echo x \\x3e CHANGELOG.md' > {S}/ez7.sh; bash {S}/ez7.sh", T), 2, "zsh's echo reads escapes"),
    ("V12.3", bash(f"mkdir -p {S}/d7 && cp {S}/a7.sh {S}/b7.sh {S}/d7/ && bash {S}/d7/a7.sh", S), 2,
     "cp of several files into a directory"),
    ("V13.3", bash(f"cp {S}/a7.sh {S}/c7.sh && bash {S}/c7.sh", S), 2, "a copy carries its source's text"),
    ("V14.3", bash(f"sed -i 's/q/r/' {S}/w7.sh && bash {S}/w7.sh", S), 2, "a script edited in place is read nearly"),
    ("V15.2", bash(f"rm -f {S}/w7.sh; bash {S}/w7.sh", S), 0, "a script removed first runs nothing"),
    ("V16.2", bash(f"sed 's/hi/x > CHANGELOG.md/' {S}/hi7.sh > {S}/h7.sh && cd ../live && bash {S}/h7.sh", S), 2,
     "a filter's output is known only nearly"),
    ("V18.2", bash("node -e 'const s = `x`; console.log(s)'", T), 0, "a JavaScript template literal runs nothing"),
    ("V19", bash('echo x > "$U/f"; cd ../live && echo y > /tmp/ok', S), 0, "an unplaced path marks its own command"),
    ("V21.2", bash("cat <<EOF\n" + "${x:-" * 30 + "z" * 20_000 + "}" * 30 + "\n$(echo x > CHANGELOG.md)\nEOF", T), 2,
     "a budget spent inside a scan makes the call unreadable"),
    ("V25", bash("bash -c 'rm -f /tmp/ok\\ CHANGELOG.md'", T), 0, "bash does not split an escaped space"),
    ("V26", bash("flock /tmp/l -c 'F=\"/tmp/ok.txt CHANGELOG.md\"; rm -f $F'", T), 0,
     "flock -c runs in the call's $SHELL, the tool's zsh"),
    ("V26b", bash("SHELL=/bin/bash flock /tmp/l -c 'F=\"/tmp/ok.txt CHANGELOG.md\"; rm -f $F'", T), 2,
     "flock -c under SHELL=/bin/bash"),
    ("V26c", bash("export SHELL=/bin/bash; flock /tmp/l -c 'F=\"/tmp/ok.txt CHANGELOG.md\"; rm -f $F'", T), 2,
     "flock -c after export SHELL=/bin/bash"),
    ("V27", bash("perl -e '" + 'system("true");' * 6000 + "'", T), 2,
     "the commands Perl starts spend the budget: the call is unreadable"),
    ("W1", bash("taskset -c 0-19 git clean -fdx", T), 2, "git behind a wrapper still writes"),
    ("W2", bash("time git hook run pre-commit", T), 2, "git behind time still writes"),
    ("W3", bash("git bisect run make test", T), 2, "git bisect run writes, and runs its command"),
    ("W5", bash("taskset -c 0-19 bash probes/copy_build.sh > /tmp/x.log 2>&1", T), 0,
     "a repository probe building in a scratch copy writes only there"),
    ("X2", bash(f"sed 's/a/b/' {S}/py7.py > {S}/py8.py && python3 {S}/py8.py", T), 2,
     "Python a filter produced: what it writes is out of sight"),
    ("F4", bash(f"grep cargo {S}/src7.sh > {S}/g7.sh && bash {S}/g7.sh", T), 2,
     "lines grep selects are unknown: unreadable while a run is live"),
    ("F5", bash(f"sed 's/a/b/' {S}/hi7.sh > {S}/ap7.sh; printf 'echo done\\n' >> {S}/ap7.sh; cd ../live && bash {S}/ap7.sh",
                S), 2, "an append keeps a near copy near"),
    ("F6", bash(f"head -n 1 {S}/src7.sh > {S}/h8.sh; cd ../live && bash {S}/h8.sh", S), 0,
     "head -n N: the count is a value, not a file"),
    ("SL6", bash(f"head -n 1 {S}/src7.sh {S}/src8.sh > {S}/m7.sh; cd ../live && bash {S}/m7.sh", S), 2,
     "head over two files prints headers: its output is unknown"),
    ("SL7", bash(f"cat -A {S}/hi7.sh > {S}/ca7.sh; cd ../live && bash {S}/ca7.sh", S), 2,
     "cat -A marks the text: its output is unknown"),
    ("H1", bash(f"cat > {S}/rec7.sh <<EOF\ncd {S}\nfor l in a b; do cp \"\\${{l}}.json\" \"\\${{l}}_base.json\"; done\nEOF\n"
                f"bash {S}/rec7.sh", T), 0, "an unquoted heredoc writes \\$ as $: the script's loop names its paths"),
]
R6_QUIET = [
    ("EM3", bash("E=; $E cargo test", Q), 2, "an empty unquoted expansion before a heavy run"),
    ("EM4", bash("${NOPE_UNSET_P6:-} cargo test", Q), 2, "an empty default before a heavy run"),
    ("UX1", bash("printf 'C:\\\\Users\\\\nicolas\\\\n' > /tmp/p6-win.txt; cargo test", Q), 2,
     "a Windows path in printf beside a heavy run"),
    ("UX2", bash("f() { shift \u00b2; echo x; }; f a b", Q), 0, "a superscript shift count"),
    ("CH1", bash("{ R=$(taskset -c 0-19 cargo test); } > /tmp/x.log 2>&1", Q), 2, "a capture inside a logged group"),
    ("BG1", bash("for i in $(seq 10); do A$i=$(date +%s); done; echo " + "x" * 50000 + "; cargo test", Q), 2,
     "substitutions before a long line and a heavy run"),
    ("GC1", bash("git bisect run cargo test", Q), 2, "git bisect run starts a heavy run"),
    ("GC2", bash("git rebase -x 'cargo test' HEAD~2", Q), 2, "git rebase -x starts a heavy run"),
    ("RC1", bash(f"cp probes/run_all.sh {S}/ra.sh && bash {S}/ra.sh", Q), 2, "a copy of the runner, run bare"),
    ("RC2", bash(f"sed 's/x/y/' probes/run_all.sh > {S}/ra2.sh && bash {S}/ra2.sh", Q), 2,
     "a filtered copy of the runner, run bare"),
    ("RC3", bash(f"taskset -c 0-19 bash {S}/ra3.sh > /tmp/x.log 2>&1", RT), 0, "a runner copied earlier, blessed"),
    ("RC3b", bash(f"taskset -c 0-19 bash {S}/ra3.sh > /tmp/x.log 2>&1", Q), 2,
     "the store it runs holds a probe naming CPUs 20-23: the glob is read"),
    ("RC5", bash(f"bash {S}/ra3.sh", Q), 2, "a runner copied in an earlier call, run bare"),
    ("RP1", bash("./probes/heavy_probe.sh 2>&1 | head -5", Q), 2, "a repository probe running a heavy command, bare"),
    ("RP2", bash("taskset -c 0-19 bash probes/heavy_probe.sh > /tmp/x.log 2>&1", Q), 0,
     "a repository probe running a heavy command, blessed"),
    ("RP3", bash("bash probes/light_probe.sh", Q), 0, "a repository probe running nothing heavy"),
    ("PS1", bash("taskset -c 0-19 python3 -c \"import subprocess; subprocess.run(['taskset', '-c', '0-23', 'cargo', "
                 "'test'])\" > /tmp/x.log 2>&1", Q), 2, "Python starting a heavy run on 0-23"),
    ("PS2", bash("taskset -c 0-19 python3 -c \"import os; os.system('cargo test > /tmp/y.log 2>&1 &')\" > /tmp/x.log "
                 "2>&1", Q), 2, "Python starting a heavy run in the background"),
    ("PS3", bash("taskset -c 0-19 python3 -c \"import os; os.system('cargo test > /dev/null 2>&1')\" > /tmp/x.log 2>&1",
                 Q), 2, "Python starting a heavy run into /dev/null"),
    ("EX1", bash("python3 - <<'PY'\nimport subprocess\nsubprocess.run(['cargo', 'test'], executable='/bin/true')\nPY", Q), 0,
     "executable= runs /bin/true, not cargo"),
    ("RC4", bash(f"sed -i '$a cargo test' {S}/e3.sh && bash {S}/e3.sh", Q), 0,
     "a script edited by sed, read from its old text (no heavy run in it)"),
    ("V17", bash("python3 -c \"import os; os.spawnlp(os.P_WAIT, 'cargo', 'cargo', 'test')\"", Q), 2,
     "os.spawnlp starts the argv after its mode and file"),
    ("V20", bash(f"{S}/ra3.sh > /dev/null", Q), 2, "a byte copy of the runner, run by its path"),
    ("V24", bash("cargo test $((", Q), 2, "a heavy command in a call that cannot be read"),
    ("V24b", bash("echo $((", Q), 0, "a light call that cannot be read"),
    ("V28", bash("python3 -c 'import subprocess; subprocess.run(\"cargo test $((\", shell=True)'", Q), 2,
     "a heavy command Python starts in text that does not parse"),
    ("V30b", bash("git commit -m \"$((\"; echo 'the tests pass'", Q), 0, "an unreadable call with nothing heavy"),
    ("W4", bash("echo \"$((\"; printf '%s\\n' 'x\n./probes/run_all.sh | head'", Q), 0,
     "an unreadable call quoting a heavy command as data"),
    ("W3b", bash("git bisect run cargo test", Q), 2, "git bisect run carries a heavy command"),
    ("W3c", bash("git rebase -x 'cargo test' main", Q), 2, "git rebase -x carries a heavy command"),
    ("W3d", bash("git bisect start HEAD HEAD~3", Q), 0, "git bisect without run carries nothing"),
    ("X1.2", bash(f"grep -v bash probes/run_all.sh > {S}/f7.sh; bash {S}/f7.sh", Q), 0,
     "lines a filter selects from the runner are not the runner"),
    ("X1c.2", bash(f"perl -pe 's/x/y/' probes/run_all.sh > {S}/f9.sh && bash {S}/f9.sh", Q), 2,
     "perl -p edits every line: still the runner"),
    ("F1", bash(f"head -1 {S}/src7.sh > {S}/b7.sh; printf 'exit 0\\n' >> {S}/b7.sh; bash {S}/b7.sh", Q), 0,
     "the lines head selects are read, not the whole source"),
    ("F2", bash(f"tail -n 1 {S}/src7.sh > {S}/t7.sh && bash {S}/t7.sh", Q), 2, "the line tail selects runs cargo test"),
    ("F3.2", bash(f"sed -n '2p' {S}/src7.sh > {S}/s7.sh && bash {S}/s7.sh", Q), 2, "the line sed -n selects runs cargo test"),
    ("SL1", bash(f"tail -n 1 {S}/src8.sh > {S}/t8.sh; bash {S}/t8.sh", Q), 0, "the line tail selects is all that runs"),
    ("SL2", bash(f"cat probes/run_all.sh > {S}/rc7.sh && bash {S}/rc7.sh", Q), 2, "cat of the runner is the runner"),
    ("SL3", bash(f"sed -n '1p' {S}/src7.sh > {S}/s8.sh; bash {S}/s8.sh", Q), 0, "the line sed -n selects is all that runs"),
    ("SL4", bash('echo \\" ; cargo test $((', Q), 2, "an escaped quote opens nothing in unreadable text"),
    ("SL5", bash("echo 'x'; cargo test $((", Q), 2, "a closed quote ends its span in unreadable text"),
    ("H2", bash(f"cat > {S}/bn7.sh <<EOF\ncargo \\\ntest\nEOF\nbash {S}/bn7.sh", Q), 2,
     "a backslash-newline in an unquoted heredoc joins the lines"),
    ("NU1", bash("taskset -c 0-19 python3 -c \"import os; os.system('numactl -C 20-23 cargo test')\" > /tmp/x.log 2>&1",
                 Q), 2, "numactl onto 20-23 for a heavy run Python starts"),
    ("NU2", bash("taskset -c 0-19 python3 -c \"import os; os.system('numactl -C 0-19 cargo test')\" > /tmp/x.log 2>&1",
                 Q), 0, "numactl within 0-19 for a heavy run Python starts"),
    ("AG3", bash("/home/nicolas/.cabal/bin/agda --numeric-version", Q), 0, "agda printing its version"),
    ("AG4", bash("agda --print-agda-dir", Q), 0, "agda printing a path"),
    ("AG5", bash("agda src/Aletheia/Main.agda", Q), 2, "agda checking a module"),
    ("MU1", bash("mull-runner-23 ./unit_tests --dry-run --reporters=Elements", Q), 0, "mull-runner --dry-run lists"),
    ("MU2", bash("mull-runner-23 ./unit_tests", Q), 2, "mull-runner runs the mutants"),
    ("CM8", bash("cmake --build cpp/build --target help", Q), 0, "cmake --build --target help lists"),
    ("CM9", bash("cmake --build cpp/build --target help --target unit_tests", Q), 2, "a real target beside help"),
    ("GL1", bash("bash probes/heavy_pro*.sh", Q), 2, "a glob naming a heavy probe is read"),
    ("GL2", bash("for p in probes/heavy*.sh; do bash \"$p\"; done", Q), 2, "a loop over a glob of probes is read"),
    ("GL3", bash("for f in a b c d e f g h i; do echo x > \"$f.log\"; done", S), 0, "nine values are read"),
    ("MEM1", bash("X=a; for i in 1 2 3 4 5 6 7 8; do for j in 1 2 3 4 5 6 7 8; do X=$X$X; done; done; cargo test", Q), 2,
     "a doubling value is capped: the reading goes on to the heavy command"),
]
R6_LOOPS = [
    ("PL1", bash("bash -c 'sleep 0.3 & sleep 0.4 & wait' | cat", T), 0, "a carrier's own wait, piped"),
    ("PL2", bash("bash -c 'sleep 1 & wait %\u00b2'", T), 2, "a superscript job spec, no crash"),
    ("PL3", bash("sleep 999 &\necho work\nkill $(jobs -p)", T), 2, "zsh: $(jobs -p) is not a pid"),
    ("V23", bash("f() { tail -f /tmp/a | grep x & }; trap f EXIT; echo hi", T), 2,
     "a job an EXIT trap's function puts in the background"),
    ("V23b", bash("trap 'sleep 9 &' EXIT; echo hi", T), 2, "a job an EXIT trap's text puts in the background"),
    ("V23c", bash("f() { sleep 1 & wait; }; trap f EXIT; echo hi", T), 0, "a trap that waits for its own job"),
    ("X3", bash(f"cat > {S}/rt.sh <<'EOF'\nf() {{\n  trap \"rm -f /tmp/rt\" RETURN\n  sleep 3\n}}\nf\nEOF\n"
                 f"bash {S}/rt.sh & P=$!\nsleep 1\nkill -INT $P\nwait $P", T), 0,
     "a RETURN trap in a background script runs in that job's own process"),
]


def sw_case() -> tuple[str, int, int, str, str]:
    live = start(["cargo", "test"], T)
    try:
        rc, err = hook(GUARD, bash("python3 -c \"import re; re.compile('\\d'); open('CHANGELOG.md', 'w')\"", T))
    finally:
        stop(live)
    got = int(rc == 2 and err.lstrip().startswith("BLOCKED"))
    return ("SW1", 1, got, "a SyntaxWarning in the code stays off the hook's stderr", err[:80])


def round6_cases() -> list[tuple[str, int, int, str, str]]:
    (S / "e1.sh").write_text("echo safe\n")
    (S / "e2.sh").write_text("echo safe\n")
    (S / "e3.sh").write_text("echo safe\n")
    (BASE / "p6-other.sh").write_text(f"echo x > {T}/CHANGELOG.md\n")
    (Q / "probes" / "run_all.sh").write_text("for p in probes/*.sh; do bash \"$p\"; done\n")
    (S / "ra3.sh").write_text((Q / "probes" / "run_all.sh").read_text())
    (RT / "probes").mkdir(parents=True, exist_ok=True)
    git(RT, "init", "-q")
    (RT / "probes" / "run_all.sh").write_text((Q / "probes" / "run_all.sh").read_text())
    (RT / "probes" / "light_probe.sh").write_text("echo ok\n")
    (Q / "probes" / "heavy_probe.sh").write_text("cargo build\n")
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
    (T / "probes" / "copy_build.sh").write_text('scratch=$(mktemp -d)\ncp -r . "$scratch"\ncd "$scratch" && cargo test\n')
    for k in range(1, 4):
        (S / f"n{k}.sh").write_text(f"bash {S}/n{k + 1}.sh\n")
    (S / "n4.sh").write_text(f"bash -c 'bash -c \"echo x > {T}/CHANGELOG.md\"'\n")
    rows = []
    for cid, event, want, what in R6_QUIET:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, ascii(err)))
    for cid, event, want, what in R6_LOOPS:
        rc, err = hook(LOOPS, event)
        rows.append((cid, want, rc, what, ascii(err)))
    t0 = time.monotonic()  # a trim tries every cut: a long value is left unknown, not tried
    rc, err = hook(GUARD, bash("V=" + "a" * 250_000 + "; echo ${V%%*b}", T))
    rows.append(("V22", 1, int(rc == 0 and time.monotonic() - t0 < 2), f"a trim of 250 KB ({time.monotonic() - t0:.2f}s)",
                 err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in R6_LIVE:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, ascii(err)))
    finally:
        stop(live)
    return rows


def dup_cases() -> list[tuple[str, int, int, str, str]]:
    """A loop read once per value repeats its body's problems: each is listed once."""
    rc, err = hook(GUARD, bash("for f in a b c d e f g h i; do cargo test; done", Q))
    bullets = [ln for ln in err.splitlines() if ln.strip().startswith("*")]
    return [("DUP0", 2, rc, "cargo test in a nine-value loop is refused", err),
            ("DUP1", 0, int(len(bullets) != len(set(bullets))), "a loop's repeated problem is listed once", err)]


def round7_cases() -> list[tuple[str, int, int, str, str]]:
    """What the budget carries, loops too long to unroll, and the node budget."""
    rows = []
    many = "X='" + ":;" * 6000 + "'"
    rc, err = hook(GUARD, bash(f"C=cargo; $C test; {many}; eval \"$X\"", Q))
    rows.append(("BUD1", 2, rc, "a heavy command read before the budget ran out is refused by name", err))
    rc, err = hook(GUARD, bash("[[ a ]]; " * 20001 + "cargo test", Q))
    rows.append(("NODE1", 1, int("nodes to read" in err), "twenty thousand tests spend the node budget", err[-300:]))
    live = start(["cargo", "test"], T)
    try:
        rc, err = hook(GUARD, bash(f"cd {S} && for f in a b c d e f g h i; do echo x > \"$f.log\"; done", T))
        rows.append(("LV1", 0, rc, "nine values are read one by one: the writes land in the scratch directory", err))
        values = " ".join(f"{S}/v{k}.log" for k in range(70))
        rc, err = hook(GUARD, bash(f"for f in {values}; do echo x > \"$f\"; done", T))
        rows.append(("LV2", 0, rc, "seventy values, read once: what they share places the path", err))
    finally:
        stop(live)
    return rows


# ─── round seven: the round-six findings still open ─────────────────────────────
PYSUITE = S / "suite7.py"  # an argv chosen by if/else, started through a starred list
R7_LOOPS = [
    ("J7a", bash("trap 'kill $(jobs -p)' EXIT\nsleep 999 &\necho work", Q), 2,
     "zsh's $(jobs -p) runs in a subshell and names no job: the job runs on"),
    ("J7b", bash("cleanup() { kill $(jobs -p); }; trap cleanup EXIT\nsleep 999 &\necho work", Q), 2,
     "the same through a trap function"),
    ("J7c", bash("sleep 998 &\necho work\npkill -P $$", Q), 0, "pkill -P $$ stops this shell's children"),
    ("J7d", bash("sleep 998 & sleep 999 &\necho work\npkill -P $$", Q), 0, "every child"),
    ("J7e", bash("trap 'trap \"\" TERM; kill -TERM -$$' EXIT\nsleep 998 &\necho work", Q), 0,
     "kill -TERM -$$ in an EXIT trap stops the group"),
    ("J7n", bash("sleep 999 &\necho work\npkill -0 -P $$", Q), 2, "pkill -0 -P $$ tests the children; it stops none"),
    ("J7o", bash("trap 'pkill -P $$' EXIT\nsleep 998 & sleep 999 &\necho work", Q), 0,
     "pkill -P $$ in an EXIT trap stops every child"),
    ("J7f", bash("sleep 998 & sleep 999 &\necho work\nkill %%", Q), 2, "kill %% stops one of two jobs"),
    ("J7g", bash("sleep 998 & sleep 999 &\necho work\nkill %+", Q), 2, "kill %+ stops one of two jobs"),
    ("J7h", bash("bash -c 'sleep 998 & sleep 999 & kill %2'", Q), 2, "kill %2 in bash stops one of two"),
    ("J7i", bash("sleep 997 & sleep 998 & sleep 999 &\necho work\nkill %1 %2", Q), 2, "two of three jobs stopped"),
    ("J7j", bash("sleep 999 & kill -0 0 && echo alive", Q), 2, "kill -0 0 tests the group; it stops nothing"),
    ("J7k", bash("sleep 999 & kill -s 0 0", Q), 2, "kill -s 0 0 stops nothing"),
    ("J7l", bash("sleep 999 & kill -CONT 0", Q), 2, "SIGCONT stops nothing"),
    ("J7m", bash("sleep 999 & kill -0 -- -$$", Q), 2, "kill -0 of the group stops nothing"),
    ("B7a", bash("until curl -sf http://127.0.0.1:8765/ >/dev/null; do :; done", Q), 2, "a busy loop on curl"),
    ("B7b", bash("until nc -z 127.0.0.1 8765; do :; done", Q), 2, "a busy loop on nc -z"),
    ("B7c", bash("until [ -f /tmp/b7-flag ]; do continue; done", Q), 2, "a busy loop on continue"),
    ("B7d", bash("until gh run view 123 --json status -q .status | grep -q completed; do :; done", Q), 2,
     "a busy loop on a pipeline"),
    ("B7e", bash("while read l; do echo \"$l\"; done < /tmp/b7-in\nuntil [ -f /tmp/b7-flag ]; do :; done", Q), 2,
     "a busy loop after a read loop"),
    ("B7f", bash("while ! [ -f /tmp/b7-flag ]; do python3 -c 'import time; time.sleep(1)'; done", Q), 2,
     "a loop whose delay is Python's sleep"),
    ("B7g", bash("while ! [ -f /tmp/b7-flag ]; do perl -e 'sleep 1'; done", Q), 2, "a loop whose delay is perl's sleep"),
    ("B7h", bash("while read l; do echo \"$l\"; done < /tmp/b7-in", Q), 0, "a read loop is not polling"),
    ("TR7", bash("trap -- 'trap \"\" TERM; kill 0' EXIT\nsleep 999 &\necho work", Q), 0,
     "trap -- ACTION EXIT: the -- ends trap's options"),
    ("MN7", monitor("ps aux | grep -q -e 'cargo test' -e '[r]ustc'", T), 2,
     "each -e pattern is tried: the first matches this script's own shell"),
    ("MN7b", monitor("ps aux | grep -q -e '[c]argo test' -e '[r]ustc'", T), 0, "both patterns bracketed"),
    ("P7a", bash("python3 - <<'PY'\nimport os, time\nfor _ in range(600):\n    if os.path.exists('/tmp/p7'):\n"
                 "        break\n    time.sleep(1)\nPY", Q), 2, "a Python for loop polling, as its shell twin"),
]
R7_QUIET = [
    ("X7a", bash("printf 'a\\x\\n' > /tmp/x7-ok.txt", Q), 0, "a lone \\x is read, not a fault"),
    ("X7b", bash("echo 'use \\x here' > /tmp/x7-p.txt; cargo test", Q), 2, "past a lone \\x the cargo test is read"),
    ("X7c", bash("python3 -c \"import subprocess, sys; subprocess.run(['ls', \\\"a'\\\" + sys.argv[1]], "
                 "executable='/bin/ls')\" x", Q), 0, "an unbalanced quote in a started argv is not a fault"),
    ("X7d", bash(f"python3 {PYSUITE} --full", Q), 2, "a starred argv chosen by if/else is read both ways"),
    ("PR7", bash(f"print -r -- 'cargo test 2>&1 | tail -5' > {S}/pr7.sh; bash {S}/pr7.sh", Q), 2,
     "zsh's print writes the script as echo would"),
]
NEST = [  # calls the reader cannot follow: the fallback scan finds the heavy command after any opener
    ("NS1", bash("echo " + "$(" * 9 + "cargo test" + ")" * 9, Q), 2, "cargo test nine substitutions deep"),
    ("NS2", bash("{ " * 260 + "cargo test; " + "} " * 260, Q), 2, "cargo test in 260 nested groups"),
    ("NS3", bash("( " * 260 + "cargo test" + " )" * 260, Q), 2, "cargo test in 260 nested subshells"),
    ("NS4", bash("if true; then " * 210 + "cargo test; " + "fi; " * 210, Q), 2, "cargo test in 210 nested ifs"),
    ("NS5", bash('for p in a b; do cargo test -p $p; done; echo "$(("', Q), 2, "after `do`, in an unreadable call"),
    ("NS6", bash("{ cargo test; }; echo $((", Q), 2, "after `{`, in an unreadable call"),
    ("NS7", bash("R=$(cargo test 2>&1); echo $((", Q), 2, "after `$(`, in an unreadable call"),
    ("NS8", bash("R=`cargo test 2>&1`; echo $((", Q), 2, "after a backquote, in an unreadable call"),
    ("NS9", bash("repeat 2 cargo test; echo $((", Q), 2, "zsh's repeat, in an unreadable call"),
    ("NS10", bash("for x (a b) cargo test; echo $((", Q), 2, "zsh's short for, in an unreadable call"),
    ("NS11", bash("cat > /tmp/ns11-doc.md <<'EOF'\ncargo test --release\nEOF\necho $((", Q), 0,
     "a heredoc's data line is not a statement"),
    ("NS12", bash("if cargo test; then echo ok; fi; echo $((", Q), 2, "an if's condition, in an unreadable call"),
    ("NS13", bash("echo $((1<<x))\ncargo test\necho $((", Q), 2,
     "a shift in arithmetic is no heredoc: the lines after it are still read"),
]
R7_XARGS = [  # a run is live in T; the call runs in the scratch directory S unless it says otherwise
    ("XA1", bash(f"find {T}/tools -name '*.py' -print0 | xargs -0 sed -i s/a/b/", S), 2,
     "find's root places the operands xargs hands to sed -i"),
    ("XA2", bash(f"xargs sed -i s/a/b/ < {S}/xargs-list.txt", S), 2, "a list read on stdin names the live file"),
    ("XA3", bash(f"xargs -a {S}/xargs-list.txt sed -i s/a/b/", S), 2, "a list given by -a names the live file"),
    ("XA4", bash(f"git -C {T} ls-files | xargs sed -i s/a/b/", S), 2,
     "operands from a producer the reader cannot follow land in no known place, in a call naming the live tree"),
    ("XA5", bash(f"find {S} -name '*.log' | xargs rm -f", T), 0, "find's root outside the live tree: the removals land there"),
    ("XA6", bash(f"find {T}/tools -name '*.py' | xargs grep -n x", S), 0, "xargs running a reader writes nothing"),
    ("XA7", bash(f"git -C {S} ls-files | xargs sed -i s/a/b/", T), 0,
     "names git lists lie in its own directory, outside the live tree the call runs in"),
    ("XA8", bash(f"grep -rl x {T}/tools | xargs sed -i s/a/b/", S), 2,
     "operands from a producer the reader cannot follow, in a call naming the live tree"),
    ("H5b", bash(f"bash <<'EOF'\ncat > {S}/p5.py <<'X'\nopen('{T}/CHANGELOG.md', 'w').write('x')\nX\npython3 {S}/p5.py\nEOF", S),
     2, "a heredoc inside a carrier's heredoc is the script it writes, not both bodies joined"),
]
RUNNER = BASE / "runner-repo" / "probes" / "run_all.sh"  # heavy by its name alone: its one line runs nothing heavy
R7_COPIES = [  # a copy of a heavy runner is the runner, however it was made; only provenance can tell here
    ("CO1", bash(f"cat <<'EOF' | tee {S}/co1.sh >/dev/null\ncargo test | tail -5\nEOF\nbash {S}/co1.sh", Q), 2,
     "a heredoc piped into tee writes the script"),
    ("CO2", bash(f"cat {RUNNER} | tee {S}/co2.sh >/dev/null; bash {S}/co2.sh", Q), 2, "cat piped into tee copies the runner"),
    ("CO3", bash(f"git -C {RUNNER.parent.parent} show HEAD:probes/run_all.sh > {S}/co3.sh && bash {S}/co3.sh", Q), 2,
     "git show REV:PATH is a near copy of PATH"),
    ("CO4", bash(f"sed -n '1,$p' {RUNNER} > {S}/co4.sh && bash {S}/co4.sh", Q), 2, "a whole-file sed -n keeps its origin"),
    ("CO5", bash(f"tr a a < {RUNNER} > {S}/co5.sh && bash {S}/co5.sh", Q), 2, "tr from a file is a near copy"),
    ("CO6", bash(f"cp {RUNNER} {S}/co6.sh && echo 'echo hi' > {S}/co6.sh && bash {S}/co6.sh", Q), 0,
     "a copy overwritten with other text is no longer the runner"),
    ("CO7", bash(f"tail -n +0 {RUNNER} > {S}/co7.sh; bash {S}/co7.sh", Q), 2, "tail -n +0 is the whole file"),
    ("CO8", bash(f"cp {RUNNER} {S}/co8.sh && bash {S}/co8.sh", Q), 2, "a cp is the runner (the control)"),
]
R7_ZOXIDE = [  # the tool shell's cd is zoxide's: a name that is no directory where the call stands is looked up
    ("ZO1", bash("cd python && touch zo-marker.txt", S), 2, "zoxide's cd lands in the live tree it knows"),
    ("ZO2", bash("cd zo-nowhere && touch zo-marker.txt", S), 0, "a name zoxide does not know leaves the cd where it was"),
    ("ZO3", bash("mkdir -p python && cd python && touch zo-marker.txt", S), 0, "a directory the call makes is entered"),
    ("ZO4", bash("bash -c 'cd python && touch zo-marker.txt'", S), 0, "bash's cd is the builtin"),
]
R7_ARRAYS = [  # a literal array's elements are its values: $a all of them in zsh, the first in bash
    ("AR1", bash(f"logs=({S}/ar-a.log {S}/ar-b.log); touch $logs", T), 0, "zsh's $logs: every element, all outside the tree"),
    ("AR2", bash(f'logs=({S}/ar-a.log {S}/ar-b.log); rm -f "${{logs[@]}}"', T), 0, '"${logs[@]}": every element'),
    ("AR3", bash(f"files=({T}/CHANGELOG.md); touch $files", S), 2, "an element in the live tree is written"),
    ("AR4", bash(f'files=({T}/CHANGELOG.md {T}/README.md); sed -i s/a/b/ "${{files[@]}}"', S), 2,
     "elements in the live tree edited in place"),
    ("AR5", bash(f"bash -c 'f=({T}/CHANGELOG.md {S}/ar-x); touch $f'", S), 2, "bash's $f is the first element: live"),
    ("AR6", bash(f"bash -c 'f=({S}/ar-x {T}/CHANGELOG.md); touch $f'", S), 0, "bash's $f is the first element: outside"),
    ("AR7", bash(f"logs=({T}/CHANGELOG.md); logs={S}/ar-ok.log; touch $logs", S), 0,
     "a name given a plain value holds no array any more"),
]
R7_LIVE = [
    ("W7a", bash("python3 -c \"import gzip; gzip.open(filename='CHANGELOG.md.gz', mode='wb')\"", T), 2,
     "gzip.open with its file as a keyword writes"),
    ("D7", bash('x=$(echo ${D7:=/tmp/}); echo y > "${D7}CHANGELOG.md"', T), 2,
     "${V:=w} inside $( ) assigns in the subshell only: the parent's V stays unset"),
]


def settings_cases() -> list[tuple[str, int, int, str, str]]:
    """The events and tools the hooks handle reach them: settings.json registers each.  It reads this profile's
    own ~/.claude/settings.json, so it checks the registration in force here, not a copy."""
    settings = Path.home() / ".claude" / "settings.json"
    try:
        hooks = json.loads(settings.read_text()).get("hooks", {})
    except (OSError, ValueError) as exc:
        return [("REG0", 0, 1, f"settings.json unreadable: {exc}", "")]
    def tools(event: str, script: str) -> set[str]:
        return {t for e in hooks.get(event, []) for h in e.get("hooks", []) if script in h.get("command", "")
                for t in e.get("matcher", "").split("|")}
    rows = []
    want = {("PreToolUse", "heavy-run-guard.py"): {"Bash", "Monitor", "Edit", "Write", "NotebookEdit", "MultiEdit",
                                                   "EnterWorktree", "ExitWorktree", "Agent"},
            ("PostToolUse", "heavy-run-guard.py"): {"Bash", "Monitor"},
            ("PostToolUseFailure", "heavy-run-guard.py"): {"Bash", "Monitor"},
            ("PreToolUse", "no-polling-loops.py"): {"Bash", "Monitor"}}
    for k, ((event, script), need) in enumerate(want.items(), 1):
        missing = sorted(need - tools(event, script))
        rows.append((f"REG{k}", 0, len(missing), f"{event} routes {sorted(need)} to {script}", f"missing {missing}"))
    return rows


def budget_cases() -> list[tuple[str, int, int, str, str]]:
    """The byte budget charges each text once per call: a script read again with other words, and a $( ) body
    read as the text around it, cost nothing more; two different large texts still spend it."""
    big = "# " + "x" * 200_000 + "\necho {}\n"  # under the 256 KiB a script is read up to
    for k in (1, 2, 3):
        (S / f"big{k}.sh").write_text(big.format(k))
    spent = "bytes of text to read"
    rc, err = hook(GUARD, bash(f"bash {S}/big1.sh a; bash {S}/big1.sh b; bash {S}/big1.sh c; cargo test", Q))
    rows = [("BU1", 0, int(spent in err), "one 200 KB script read with three sets of words is charged once",
             err[-300:])]
    rc, err = hook(GUARD, bash(f"bash {S}/big1.sh; bash {S}/big2.sh; bash {S}/big3.sh; cargo test", Q))
    rows.append(("BU2", 1, int(spent in err), "three different 200 KB scripts spend the budget", err[-300:]))
    rc, err = hook(GUARD, bash("Y=$(: " + "a " * 150_000 + "); cargo test", Q))
    rows.append(("BU3", 0, int(spent in err), "a 300 KB $( ) body is charged with the text around it", err[-300:]))
    (Q / "probes").mkdir(exist_ok=True)  # a repository probe running a heavy runner with sixteen sets of words
    (Q / "probes" / "runner7.sh").write_text("".join(f'echo "$1" {k}\n' for k in range(400)) + "cargo test\n")
    (Q / "probes" / "p7.sh").write_text("".join(f"bash {Q}/probes/runner7.sh {k}\n" for k in range(16)))
    rc, err = hook(GUARD, bash(BLESSED.format("./probes/p7.sh"), Q))
    rows.append(("BU4", 0, rc, "a repository probe reading its runner sixteen times, run in the blessed shape", err[-300:]))
    t0 = time.monotonic()
    rc, err = hook(GUARD, bash("ls; " * 7990 + "cargo test", Q))
    dt = time.monotonic() - t0
    rows.append(("BU5", 1, int(rc == 2 and "commands to read" not in err and dt < 2),
                 f"a call of 7990 commands is read whole, in {dt:.2f}s", err[-300:]))
    return rows


def r7_open_cases() -> list[tuple[str, int, int, str, str]]:
    PYSUITE.write_text("import subprocess, sys\nTESTS = ['tests/'] if '--full' in sys.argv else ['tests/test_a.py']\n"
                       "subprocess.run([sys.executable, '-m', 'pytest', *TESTS])\n")
    (S / "xargs-list.txt").write_text(f"{T}/CHANGELOG.md\n")
    (T / "python").mkdir(exist_ok=True)
    subprocess.run(["zoxide", "add", str(T / "python")], env=HOOK_ENV, check=False)
    rows = settings_cases()
    for cid, event, want, what in R7_LOOPS:
        rc, err = hook(LOOPS, event)
        rows.append((cid, want, rc, what, ascii(err)))
    RUNNER.parent.mkdir(parents=True, exist_ok=True)
    RUNNER.write_text("echo runner\necho done\n")
    git(RUNNER.parent.parent, "init", "-q")
    for cid, event, want, what in R7_QUIET + NEST + R7_COPIES:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, ascii(err)))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in R7_LIVE + R7_XARGS + R7_ZOXIDE + R7_ARRAYS:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, ascii(err)))
    finally:
        stop(live)
    return rows


def run_cases() -> list[tuple[str, int, int, str, str]]:
    rows = dup_cases() + round7_cases() + r7_open_cases() + budget_cases()
    for cid, event, want, what in SHAPE:
        rc, err = hook(GUARD, event)
        rows.append((cid, want, rc, what, err))
    live = start(["cargo", "test"], T)
    try:
        for cid, event, want, what in WRITES + EDITS:
            rc, err = hook(GUARD, event)
            rows.append((cid, want, rc, what, err))
    finally:
        stop(live)
    (Q2 / "sub").mkdir(exist_ok=True)
    for cid, argv, cwd, want in PROCS:
        p = start(argv, cwd)
        try:
            rc, err = hook(GUARD, edit("Edit", f"{Q2}/f.txt", Q2))
        finally:
            stop(p)
        rows.append((cid, want, rc, " ".join(argv) + f" @{cwd.name}", err))
    rows += after_cases()
    rows += after6_cases()
    rows += loop_cases()
    rows += robustness_cases()
    rows += extra_cases()
    rows += round2_cases()
    rows += pre_install_cases()
    rows += round4_cases()
    rows += round4_py_cases()
    rows += round4_param_cases()
    rows += round4_small_cases()
    rows += round4_cost_cases()
    rows += claims_cases()
    rows += hotfix_cases()
    rows += zsh_cases()
    rows += round5_cases()
    rows += robust5_cases()
    rows += py5_cases()
    rows += small5_cases()
    rows += round6_cases()
    rows.append(sw_case())
    return rows


def hook_out(path: Path, event: dict) -> tuple[int, str, str]:
    p = subprocess.run([str(path)], input=json.dumps(event), capture_output=True, text=True, check=False,
                       timeout=60, env=HOOK_ENV)
    return p.returncode, p.stdout, p.stderr


def context_of(out: str) -> str:
    """The additionalContext a hook printed, or ""."""
    try:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError):
        return ""


def ours(text: str) -> int:
    """How many of this suite's own trees a report names: a suite run beside another sees the other's runs."""
    return sum(str(tree) + "/" in text for tree in (T, Q, Q2, S, W))


def after6_cases() -> list[tuple[str, int, int, str, str]]:
    """The after-call check as redesigned: failures, blame by tree, and the session's baseline."""
    rows = []
    x = T / "tools" / "x.py"
    live = start(["cargo", "test"], T)
    try:
        def post(pre: dict, name: str = "PostToolUse") -> tuple[int, str, str]:
            return hook_out(GUARD, pre | {"hook_event_name": name, "tool_response": {}})

        pre = bash("python3 -c 'raise SystemExit(1)'", T) | {"tool_use_id": "a6-fail", "session_id": "s-fail"}
        hook(GUARD, pre)
        os.utime(x, ns=(11, 11))
        rc, out, err = post(pre, "PostToolUseFailure")
        rows.append(("AF1", 2, rc, "a failed call's Post still checks what moved", err))
        pre = bash("echo hi", S) | {"tool_use_id": "a6-else", "session_id": "s-else"}
        hook(GUARD, pre)
        os.utime(x, ns=(12, 12))
        rc, out, err = post(pre)
        rows.append(("AF3", 0, rc, "a move in a tree the call neither runs in nor names is not blamed", err))
        rows.append(("AF3c", 1, int(str(x) in context_of(out) and "not necessarily" in context_of(out)),
                     "it is reported beside the call as not necessarily its doing", out))
        pre = bash(f"ls {Q}", Q) | {"tool_use_id": "a6-q", "session_id": "s-q"}
        hook(GUARD, pre)
        os.utime(x, ns=(13, 13))
        rc, out, err = post(pre)
        rows.append(("AF4", 0, rc, "a call in another tree is not blamed for the run's tree", err))
        pre = bash(f"cp {x} /tmp/af5-copy", S) | {"tool_use_id": "a6-named", "session_id": "s-named"}
        hook(GUARD, pre)
        os.utime(x, ns=(14, 14))
        rc, out, err = post(pre)
        rows.append(("AF5", 2, rc, "a call naming the tree is blamed for its moves", err))
        # A background call's Post comes at launch: what it writes later is caught at the session's next call.
        pre = bash("sleep 5; git status --short", T) | {"tool_use_id": "a6-bg", "session_id": "s-bg"}
        hook(GUARD, pre)
        rc, out, err = post(pre)
        rows.append(("AF6", 0, rc, "a background call's launch moved nothing", err))
        os.utime(x, ns=(15, 15))
        nxt = bash("echo next", S) | {"tool_use_id": "a6-next", "session_id": "s-bg"}
        rc, out, err = hook_out(GUARD, nxt)
        rows.append(("AF7", 0, rc, "the session's next call goes ahead", err))
        rows.append(("AF7c", 1, int(str(x) in context_of(out) and "last call ended" in context_of(out)),
                     "and is told what moved since the previous call", out))
        rc, out, err = post(nxt)
        rows.append(("AF8", 0, rc + ours(out), "a move reported once is not reported again", err + out))
        other = bash("echo other", S) | {"tool_use_id": "a6-other", "session_id": "s-other"}
        rc, out, err = hook_out(GUARD, other)
        rows.append(("AF9", 0, ours(out), "a session's first call has no baseline to compare", out))
        os.utime(x, ns=(16, 16))
        rc, out, err = hook_out(GUARD, bash("echo later", S) | {"tool_use_id": "a6-later", "session_id": "s-bg"})
        rows.append(("AF10", 1, int(str(x) in context_of(out)), "baselines are per session", out))
        rc, out, err = hook_out(GUARD, bash("echo quiet", S) | {"tool_use_id": "a6-quiet", "session_id": "s-bg"})
        rows.append(("AF10b", 1, int(str(x) in context_of(out)), "a Pre whose Post never came leaves the baseline: "
                     "its note may never have reached the model", out))
    finally:
        stop(live)
    os.utime(x, ns=(17, 17))  # after the run ended, a move since the session's last call
    rc, out, err = hook_out(GUARD, bash("echo after", S) | {"tool_use_id": "a6-ended", "session_id": "s-bg"})
    rows.append(("AF11", 1, int(str(x) in context_of(out)), "a run that ended is compared once more", out))
    rc, out, err = hook_out(GUARD, bash("echo again", S) | {"tool_use_id": "a6-ended2", "session_id": "s-bg"})
    rows.append(("AF12", 0, ours(out), "and then nothing is held", out))
    live = start(["cargo", "test"], T)
    try:
        pre = bash("touch /tmp/af13-stamp", T) | {"tool_use_id": "a6-twice", "session_id": "s-twice"}
        hook(GUARD, pre)
        os.utime(x, ns=(18, 18))
        rc, out, err = hook_out(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        rows.append(("AF13", 2, rc, "a move during the call", err))
        rc, out, err = hook_out(GUARD, bash("echo on", S) | {"tool_use_id": "a6-twice2", "session_id": "s-twice"})
        rows.append(("AF13b", 0, ours(out), "reported at its Post, not again at the next call", out))
        pre = bash("cd ../live && touch /tmp/af14-stamp", S) | {"tool_use_id": "a6-cd", "session_id": "s-cd"}
        hook(GUARD, pre)
        os.utime(x, ns=(19, 19))
        rc, out, err = hook_out(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        rows.append(("AF14", 2, rc, "a call that cds into the tree is blamed", err))
        state = BASE / "tmp" / "claude-heavy-run-guard"
        kept = state / "session-kept.json"
        kept.write_text(json.dumps({"trees": {}, "runs": []}))
        os.utime(kept, (time.time() - 3600, time.time() - 3600))
        gone = state / "session-s-gone.json"
        gone.write_text(json.dumps({"trees": {str(BASE / "no-such-tree"): {"f.py": [1, 1, 1]}}, "runs": []}))
        rc, out, err = hook_out(GUARD, bash("echo gone", S) | {"tool_use_id": "a6-gone", "session_id": "s-gone"})
        rows.append(("AF15", 1, int(kept.exists()), "a session's baseline outlives a call's snapshot", ""))
        rows.append(("AF16", 0, int("no-such-tree" in out), "a tree removed since the baseline is not reported as moved",
                     out))
        pre = bash(f"cat {x}", S) | {"tool_use_id": "a7-read", "session_id": "s-read"}
        hook(GUARD, pre)
        os.utime(x, ns=(20, 20))
        rc, out, err = hook_out(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        rows.append(("AF17", 0, rc, "a call that only reads is not blamed (the user's ruling, 2026-09-28)", err))
        rows.append(("AF17c", 1, int(str(x) in context_of(out) and "only reads" in context_of(out)),
                     "it is told of the move beside the call", out))
        a_pre = bash("touch /tmp/af18-a", T) | {"tool_use_id": "a7-A", "session_id": "s-agents", "agent_id": "agentA"}
        hook(GUARD, a_pre)
        hook_out(GUARD, a_pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        b_pre = bash("echo b", S) | {"tool_use_id": "a7-B", "session_id": "s-agents", "agent_id": "agentB"}
        hook(GUARD, b_pre)
        hook_out(GUARD, b_pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        os.utime(x, ns=(21, 21))
        rc, out, err = hook_out(GUARD, bash("echo b2", S) | {"tool_use_id": "a7-B2", "session_id": "s-agents",
                                                              "agent_id": "agentB"})
        rows.append(("AF18", 0, ours(out), "another agent of the session is not told of a tree it never touched", out))
        rc, out, err = hook_out(GUARD, bash("echo a2", S) | {"tool_use_id": "a7-A2", "session_id": "s-agents",
                                                              "agent_id": "agentA"})
        rows.append(("AF18b", 1, int(str(x) in context_of(out)), "the agent whose calls ran in the tree is told", out))
        other = BASE / "other7"
        other.mkdir(exist_ok=True)
        git(other, "init", "-q")
        (other / "f.txt").write_text("f\n")
        git(other, "add", "f.txt")
        run2 = start(["cargo", "test"], other)
        try:
            pre = bash("touch /tmp/af19-stamp", T) | {"tool_use_id": "a7-rm", "session_id": "s-rm"}
            hook(GUARD, pre)
            stop(run2)
            shutil.rmtree(other)
            os.utime(x, ns=(22, 22))
            rc, out, err = hook_out(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
            rows.append(("AF19", 0, int("other7" in err + out), "a tree removed during the call is not listed", err))
            rows.append(("AF20", 0, int(str(run2.pid) in err + out), "a run whose tree moved nothing is not listed",
                         err))
        finally:
            if run2.poll() is None:
                stop(run2)
    finally:
        stop(live)
    return rows


def after_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    live = start(["cargo", "test"], T)
    try:
        pre = bash("touch /tmp/e01-stamp", T) | {"tool_use_id": "t-moved"}
        hook(GUARD, pre)
        os.utime(T / "tools" / "x.py", ns=(1, 1))
        rc, err = hook(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        rows.append(("E01", 2, rc, "a tracked file moved during the call", err))
        rows.append(("E01m", 1, int("tools/x.py" in err), "the report names it", err))
        pre = bash("echo hi", T) | {"tool_use_id": "t-still"}
        hook(GUARD, pre)
        rc, err = hook(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        rows.append(("E02", 0, rc, "nothing moved", err))
        pre = bash("echo hi", T) | {"tool_use_id": "t-dribble"}
        hook(GUARD, pre)
        (T / ".commands-to-run.sh").write_text("#!/usr/bin/env bash\necho x\n")
        rc, err = hook(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        rows.append(("E03", 0, rc, "only the dribble moved", err))
        pre = bash("touch /tmp/e04-stamp", T) | {"tool_use_id": "t-new"}
        hook(GUARD, pre)
        (T / "tools" / "new_untracked.py").write_text("y = 2\n")
        rc, err = hook(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
        rows.append(("E04", 2, rc, "a new untracked file", err))
    finally:
        stop(live)
    pre = bash("echo hi", T) | {"tool_use_id": "t-norun"}
    hook(GUARD, pre)
    os.utime(T / "tools" / "x.py", ns=(2, 2))
    rc, err = hook(GUARD, pre | {"hook_event_name": "PostToolUse", "tool_response": {}})
    rows.append(("E05", 0, rc, "no run live: no snapshot, no report", err))
    return rows


def loop_cases() -> list[tuple[str, int, int, str, str]]:
    cases = [
        ("F01.2", monitor('log=/tmp/p.log\ntail -f -n +1 "$log" | grep -E --line-buffered "^FAIL " &\n'
                        "while kill -0 123 2> /dev/null; do sleep 15; done", T), 2, "the orphaning Monitor"),
        ("F02", bash("sleep 1 & sleep 2 & wait", T), 0, "jobs reaped by wait"),
        ("F03", bash("trap 'kill 0' EXIT\ntail -f x | grep y &\necho work", T), 0, "a trap stops the job"),
        ("F04", bash("sleep 9 & disown", T), 2, "disown"),
        ("F05.2", bash("echo a && echo b", T), 0, "no job"),
        ("F06", bash("cat > /tmp/f <<'EOF'\nx &\nEOF", T), 0, "& in heredoc data"),
        ("F07.2", monitor("tail -f log | grep --line-buffered X", T), 0, "a foreground watch"),
        ("F08.2", bash("nohup sleep 9 > /tmp/x 2>&1 &", T), 2, "nohup &"),
        ("F09.2", bash("until grep -q x f; do sleep 1; done", T), 2, "a polling loop"),
        ("F10.3", monitor("pgrep -f foo", T), 2, "self-matching pgrep in Monitor"),
        ("F11", monitor("while kill -0 123; do sleep 5; done", T), 0, "Monitor's own loop"),
        ("F12.2", bash("echo 'a & b'", T), 0, "& in a string"),
        ("F13", bash("wait; sleep 1 &", T), 2, "a wait before the job does not reap it"),
    ]
    rows = []
    for cid, event, want, what in cases:
        rc, err = hook(LOOPS, event)
        rows.append((cid, want, rc, what, err))
    return rows


def robustness_cases() -> list[tuple[str, int, int, str, str]]:
    rows = []
    for cid, raw, what in [
        ("G01", "[]", "an event that is a list"),
        ("G02", json.dumps({"tool_name": "Bash", "tool_input": "x"}), "tool_input a string"),
        ("G03", json.dumps({"tool_name": "Bash", "tool_input": {"command": None}}), "command null"),
        ("G04", json.dumps({"tool_name": "Edit", "tool_input": {"file_path": 123}}), "file_path a number"),
        ("G05", json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": 5}), "cwd a number"),
        ("G06", "\udcff", "undecodable input"),
    ]:
        for path in (GUARD, LOOPS):
            try:
                rc, err = hook(path, raw)
            except UnicodeEncodeError:
                p = subprocess.run([sys.executable, str(path)], input=b"\xff\xfe", capture_output=True, check=False)
                rc, err = p.returncode, p.stderr.decode(errors="replace")
            rows.append((f"{cid}{'g' if path == GUARD else 'l'}", 0, rc, what, err))
    big = "echo " + "a" * 1_000_000
    t0 = time.monotonic()
    rc, err = hook(GUARD, bash(big, T))
    rows.append(("G07", 0, rc, f"a 1 MB command ({time.monotonic() - t0:.2f}s)", err))
    rows.append(("G07t", 1, int(time.monotonic() - t0 < 5), "read within 5 s", ""))
    t0 = time.monotonic()
    for _ in range(10):
        hook(GUARD, bash("git status --short && ls tools", T))
    per = (time.monotonic() - t0) / 10
    rows.append(("G08", 1, int(per < 0.5), f"a typical call costs {per * 1000:.0f} ms", ""))
    return rows


def main() -> int:
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
        return 1
    bad = 0
    for cid, want, got, what, err in rows:
        ok = want == got
        bad += not ok
        print(f"{'ok  ' if ok else 'MISS'} {cid:<6} want={want} got={got} {what}")
        if not ok and err.strip():
            print("        " + err.strip().splitlines()[0][:150])
            extra = [l for l in err.strip().splitlines()[1:] if l.strip().startswith("*")]
            for line in extra[:3]:
                print("        " + line.strip()[:150])
    print(f"{len(rows)} cases, {bad} disagree")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
