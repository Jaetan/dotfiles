#!/home/nicolas/.local/bin/python3.14
"""Suite for claude-call and its zshenv line, driven as the Bash tool drives them.

usage: test_claude_call.py [NAME...]   (names select cases)
       run on CPUs 0-19 under a soft address-space cap, `ulimit -Sv 16000000`: the asan case lifts it for one call.

Each case makes a throwaway repository and runs a line of the tool's shape (`source <snapshot> ... && eval '<call>'
< /dev/null && pwd -P >| <cwd file>`) through `zsh -c`, with ZDOTDIR pointing at a .zshenv that sources the
installed claude-call.zshenv, CLAUDECODE=1 and CLAUDE_CALL_ROOT in a scratch directory: the path a real call takes.
Scopes go under the real claude.slice. Exit 0 when every case passes.
"""
import os
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2]
WRAPPER = Path(os.environ.get("CLAUDE_CALL_UNDER_TEST", HOOKS / "claude-call"))
SNIPPET = Path(os.environ.get("CLAUDE_CALL_SNIPPET_UNDER_TEST", HOOKS / "claude-call.zshenv"))
WORK = Path(tempfile.mkdtemp(prefix="claude-call-test-"))
ROOT = WORK / "root"
ZDOT = WORK / "zdot"
ZDOT.mkdir()
(ZDOT / ".zshenv").write_text(f"source {SNIPPET}\n")
SNAPSHOT_RC = sorted(Path.home().glob(".claude/shell-snapshots/snapshot-zsh-*"), key=os.path.getmtime)[-1]
results: list[tuple[str, bool, str]] = []


def env(**extra: str) -> dict[str, str]:
    # Not what an enclosing call set: its preload would mask the one under test, and its CLAUDE_CALL skip the wrap.
    e = {k: v for k, v in os.environ.items()
         if k not in ("CLAUDE_CALL", "ZDOTDIR", "LD_PRELOAD", "ASAN_OPTIONS") and not k.startswith("GIT_")}
    e.update(CLAUDECODE="1", ZDOTDIR=str(ZDOT), CLAUDE_CALL_ROOT=str(ROOT), CLAUDE_CODE_SESSION_ID="testsess0000",
             CLAUDE_CALL_OFF=str(WORK / "off"), CLAUDE_CALL_BIN=str(WRAPPER))
    e.update(extra)
    return e


def line(call: str, cwdfile: Path) -> str:
    return (f"source {SNAPSHOT_RC} 2>/dev/null || true && setopt NO_EXTENDED_GLOB NO_BARE_GLOB_QUAL 2>/dev/null "
            f"|| true && eval {shlex.quote(call)} < /dev/null && pwd -P >| {cwdfile}")


def lift_address_space_cap() -> None:
    """ASan reserves terabytes of shadow address space: raise the harness's soft cap to its hard one."""
    resource.setrlimit(resource.RLIMIT_AS, (resource.getrlimit(resource.RLIMIT_AS)[1],) * 2)


def run(call: str, cwd: Path, timeout: float = 60, lift: bool = False, warns: bool = False,
        **extra: str) -> tuple[subprocess.CompletedProcess, str]:
    """One call of the tool's shape. Unless the case expects one, a warning from the wrapper fails the case: a view
    that cannot be made falls open to the real tree, where most properties hold as well, and would hide the defect."""
    cwdfile = WORK / "cwd"
    cwdfile.unlink(missing_ok=True)
    before = len(events())
    p = subprocess.run(["/usr/bin/zsh", "-c", line(call, cwdfile)], cwd=cwd, env=env(**extra),
                       capture_output=True, text=True, timeout=timeout, check=False,
                       preexec_fn=lift_address_space_cap if lift else None)
    if not warns and "claude-call:" in p.stderr:
        check(f"no wrapper warning for `{call[:50]}`", False, p.stderr.strip()[-300:])
    if not warns and len(events()) != before:
        check(f"no event recorded for `{call[:50]}`", False, events()[-1])
    return p, cwdfile.read_text().strip() if cwdfile.exists() else ""


def start(call: str, cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(["/usr/bin/zsh", "-c", line(call, WORK / "cwd-bg")], cwd=cwd, env=env(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def repo(name: str) -> Path:
    r = WORK / "repos" / name
    (r / "sub").mkdir(parents=True)
    (r / "build").mkdir()
    (r / "build" / "old.o").write_text("old artifact\n")
    (r / ".gitignore").write_text("build/\n.notes\n*.lock\nbig.bin\n")
    (r / "out").mkdir()
    (r / "out" / ".gitignore").write_text("*\n!.gitignore\n")
    for i in range(3):
        (r / "out" / f"run-{i}.log").write_text("run\n")
    (r / "sub" / "a.txt").write_text("one\n")
    (r / "sub" / "b.txt").write_text("bee\n")
    (r / ".notes").write_text("note\n")
    (r / ".tree.lock").write_text("")
    with open(r / "big.bin", "wb") as f:
        f.truncate(2 << 20)
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "-c",
                                                  "commit.gpgsign=false", "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True, env=env())
    return r


def events(kind: str = "") -> list[str]:
    """The fall-through record's lines, those of one kind when given."""
    f = ROOT / "claude-call-events.log"
    lines = f.read_text().splitlines() if f.exists() else []
    return [ln for ln in lines if not kind or ln.split()[2:3] == [kind]]


def logs() -> list[Path]:
    return sorted((ROOT / "claude-calls").glob("*.log")) if (ROOT / "claude-calls").exists() else []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail and not ok else ''}", flush=True)


def git(r: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(r), *args], capture_output=True, text=True, env=env(), check=False).stdout


# --- cases -----------------------------------------------------------------------------------------------------

def case_cd_persists() -> None:
    r = repo("cd")
    p, cwd = run("cd sub && echo hi", r)
    check("cd_persists: output", p.stdout == "hi\n", repr(p.stdout + p.stderr))
    check("cd_persists: the tool's cwd file names the directory", cwd == str(r / "sub"), cwd)


def case_text_verbatim() -> None:
    r = repo("verbatim")
    p, _ = run('X=local; print -r -- "${X} ${NOPE:-dflt} $X [$$]" \'${X}\' "$((1 + 1))"', r)
    words = p.stdout.split()
    check("text_verbatim: the call's text reaches zsh unchanged (braced variables, $$, quotes)",
          words[:3] == ["local", "dflt", "local"] and words[3:4] != ["[$]"] and words[3].strip("[]").isdigit()
          and words[4:] == ["${X}", "2"], repr(p.stdout + p.stderr))
    literal = r"%h %u %% \n \\ ; | & * ? [x] {a,b} ~ ` # !"
    p, _ = run(f"print -r -- {shlex.quote(literal)}", r)
    check("text_verbatim: systemd specifiers and shell metacharacters in quotes arrive as written",
          p.stdout == literal + "\n", repr(p.stdout + p.stderr))


def case_exit_status() -> None:
    r = repo("rc")
    for call, want in (("cd sub && false", 1), ("exit 3", 3), ("true", 0)):
        p, _ = run(call, r)
        check(f"exit_status: `{call}` exits {want}", p.returncode == want, f"{p.returncode} {p.stderr.strip()}")


def case_log() -> None:
    r = repo("log")
    before = set(logs())
    run("echo out-$((40 + 2)); echo err-$((40 + 3)) >&2", r)
    new = sorted(set(logs()) - before)
    text = new[0].read_text() if new else ""
    check("log: one log per call", len(new) == 1, str(new))
    check("log: the call in its header", "echo out-$((40 + 2))" in text, repr(text))
    check("log: both streams of its output", "out-42" in text and "err-43" in text, repr(text))


def case_leftover() -> None:
    r = repo("leftover")
    p, _ = run("tail -f /dev/null & echo started $!", r)
    pid = next((w.split()[1] for w in p.stdout.splitlines() if w.startswith("started ")), None)
    time.sleep(0.3)
    check("leftover: a job the call left is stopped", pid is not None and not Path(f"/proc/{pid}").exists(),
          f"pid {pid}")


def case_isolated_from_edits() -> None:
    r = repo("isolated")
    proc = start("sleep 1.5; cat sub/a.txt", r)
    time.sleep(0.7)
    (r / "sub" / "a.txt").write_text("edited meanwhile\n")
    out, err = proc.communicate(timeout=30)
    check("isolated: the call reads its snapshot, not an edit made meanwhile", out == "one\n", repr(out + err))
    check("isolated: the edit made meanwhile stays", (r / "sub" / "a.txt").read_text() == "edited meanwhile\n")


def case_sync_back() -> None:
    r = repo("sync")
    p, _ = run("echo two >> sub/a.txt && rm sub/b.txt && mkdir -p newdir/x && echo n > newdir/x/f "
               "&& chmod +x sub/a.txt && ln -s a.txt sub/link", r)
    check("sync_back: the call ran", p.returncode == 0, p.stderr.strip())
    check("sync_back: a changed file", (r / "sub" / "a.txt").read_text() == "one\ntwo\n")
    check("sync_back: its mode", os.access(r / "sub" / "a.txt", os.X_OK))
    check("sync_back: a deleted file", not (r / "sub" / "b.txt").exists())
    check("sync_back: a new file in a new directory", (r / "newdir" / "x" / "f").read_text() == "n\n")
    check("sync_back: a new symlink", os.readlink(r / "sub" / "link") == "a.txt" if (r / "sub" / "link").is_symlink()
          else False)
    view_dirs = [d for d in (ROOT / "claude-views").iterdir() if not d.name.startswith(".")]
    check("sync_back: the view is removed", not view_dirs, str(view_dirs))


def case_conflict() -> None:
    r = repo("conflict")
    proc = start("sleep 1.5; echo from-call > sub/a.txt; echo from-call > sub/b.txt", r)
    time.sleep(0.7)
    (r / "sub" / "a.txt").write_text("edited meanwhile\n")
    _, err = proc.communicate(timeout=30)
    check("conflict: the edit made meanwhile wins", (r / "sub" / "a.txt").read_text() == "edited meanwhile\n")
    check("conflict: a file only the call changed is applied", (r / "sub" / "b.txt").read_text() == "from-call\n")
    kept = list((ROOT / "claude-conflicts").glob("*/sub/a.txt"))
    check("conflict: the call's version is kept", len(kept) == 1 and kept[0].read_text() == "from-call\n", str(kept))
    check("conflict: stderr names it", "sub/a.txt" in err and "claude-conflicts" in err, repr(err))
    check("conflict: recorded as an event", any("sub/a.txt" in e for e in events("conflict")), str(events()[-2:]))


def case_live_dir_and_rm() -> None:
    r = repo("livedir")
    p, _ = run("echo art > build/new.o && ls build", r)
    check("live_dir: a build write lands in the real directory", (r / "build" / "new.o").exists(), p.stderr)
    p, _ = run("rm -rf build && mkdir build && echo x > build/y && echo ok", r)
    check("rmdir: `rm -rf build && mkdir build` runs on", p.returncode == 0 and "ok" in p.stdout,
          repr(p.stdout + p.stderr))
    check("rmdir: the real directory holds what the call rebuilt", sorted(os.listdir(r / "build")) == ["y"],
          str(sorted(os.listdir(r / "build"))))
    p, _ = run("python3 -c 'import shutil; shutil.rmtree(\"build\")' && mkdir build && echo ok", r)
    check("rmdir: Python's rmtree of a directory bound live runs on", p.returncode == 0 and "ok" in p.stdout,
          repr(p.stdout + p.stderr))
    p, _ = run("python3 -c 'import os; os.rmdir(\"build\"); fd = os.open(\".\", os.O_RDONLY); "
               "os.mkdir(\"build\", dir_fd=fd); print(\"ok\")'", r)
    check("rmdir: rmdir and mkdirat of an empty directory bound live succeed", p.stdout.strip() == "ok",
          repr(p.stdout + p.stderr[-200:]))
    (r / "build" / "kept.o").write_text("kept\n")
    p, _ = run("python3 -c 'import os; os.rmdir(\"build\")'", r)
    check("rmdir: a directory bound live with content still refuses", p.returncode == 1 and "busy" in p.stderr,
          p.stderr[-200:])
    p, _ = run("rm sub/missing.txt", r)
    check("rmdir: any other failure stays one", p.returncode == 1 and "missing.txt" in p.stderr, p.stderr)
    p, _ = run("mkdir sub", r)
    check("rmdir: mkdir of an existing source directory still fails", p.returncode == 1, p.stderr)


def case_asan() -> None:
    probe = WORK / "asan.c"
    probe.write_text("int main(void){return 0;}\n")
    built = subprocess.run(["gcc", "-fsanitize=address", "-o", str(WORK / "asan-gcc"), str(probe)],
                           capture_output=True, check=False)
    if built.returncode != 0:
        print("     asan: no gcc with ASan, case skipped")
        return
    if resource.getrlimit(resource.RLIMIT_AS)[1] != resource.RLIM_INFINITY:
        check("asan: the suite runs under a soft address-space cap (ulimit -Sv), which ASan needs lifted", False)
        return
    r = repo("asan")
    p, _ = run(f"{WORK / 'asan-gcc'} && echo ran", r, lift=True)
    check("asan: a gcc ASan binary runs behind the preload", p.returncode == 0 and "ran" in p.stdout,
          repr(p.stdout + p.stderr[-200:]))


def case_scattered() -> None:
    r = repo("scattered")
    real_lock, real_big = os.stat(r / ".tree.lock").st_ino, os.stat(r / "big.bin").st_ino
    p, _ = run("stat -c %i .tree.lock big.bin; echo more >> .notes; echo z > .notes.tmp && mv .notes.tmp .notes2", r)
    inodes = p.stdout.split()[:2]
    check("scattered: a lock is bound live", inodes[:1] == [str(real_lock)], str(inodes))
    check("scattered: a file of 1 MiB or more is bound live", inodes[1:2] == [str(real_big)], str(inodes))
    check("scattered: a small ignored file is copied and synced back", (r / ".notes").read_text() == "note\nmore\n")
    check("scattered: an ignored file made by a rename is synced back", (r / ".notes2").read_text() == "z\n")


def case_mtime_and_outputs() -> None:
    r = repo("mtime")
    p, _ = run("stat -c '%y' sub/a.txt; stat -c %i out out/run-0.log", r)
    lines = p.stdout.splitlines()
    real = subprocess.run(["stat", "-c", "%y", str(r / "sub" / "a.txt")], capture_output=True, text=True,
                          check=True).stdout.strip()
    check("mtime: the snapshot keeps a source's mtime to the nanosecond", lines[:1] == [real], f"{lines[:1]} {real}")
    check("outputs: a directory whose only tracked file is its .gitignore is bound live",
          lines[1:3] == [str(os.stat(r / "out").st_ino), str(os.stat(r / "out" / "run-0.log").st_ino)], str(lines))


def case_git_env() -> None:
    r = repo("gitenv")
    proc = subprocess.Popen(["/usr/bin/zsh", "-c", line("sleep 1.5; cat sub/a.txt", WORK / "cwd-ge")], cwd=r,
                            env=env(GIT_INDEX_FILE=str(WORK / "no-such-index"), GIT_DIR=str(WORK / "no-such-git")),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.7)
    (r / "sub" / "a.txt").write_text("edited meanwhile\n")
    out, err = proc.communicate(timeout=30)
    check("git_env: a GIT_ variable in the call's environment does not stop the view", out == "one\n",
          repr(out + err))


def case_git_commit() -> None:
    r = repo("git")
    p, _ = run("echo two >> sub/a.txt && git add sub/a.txt && git -c user.name=t -c user.email=t@t "
               "-c commit.gpgsign=false commit -qm inside && echo committed", r)
    check("git: a commit made in the call lands", git(r, "log", "-1", "--format=%s").strip() == "inside",
          p.stdout + p.stderr)
    check("git: the tree matches it after sync-back", git(r, "status", "--short").strip() == "",
          git(r, "status", "--short"))


def case_new_cwd() -> None:
    r = repo("newcwd")
    p, cwd = run("mkdir -p fresh/deep && cd fresh/deep", r)
    check("new_cwd: a directory the call made and moved into exists after", (r / "fresh" / "deep").is_dir()
          and cwd == str(r / "fresh" / "deep"), cwd + p.stderr)
    (r / "emptydir").mkdir()
    p, cwd = run("pwd", r / "emptydir")
    check("new_cwd: a call from an empty untracked directory runs there", p.stdout.strip() == str(r / "emptydir"),
          repr(p.stdout + p.stderr))


def case_symlink_dir() -> None:
    r = repo("symlinkdir")
    run("ln -s sub linkdir", r)
    check("symlink_dir: a new symlink to a directory is synced back as a symlink",
          (r / "linkdir").is_symlink() and os.readlink(r / "linkdir") == "sub",
          "a directory" if (r / "linkdir").is_dir() and not (r / "linkdir").is_symlink() else "absent")


def case_nested_repo() -> None:
    r = repo("nested")
    inner = r / "vendor" / "inner"
    inner.mkdir(parents=True)
    (inner / "file.txt").write_text("inner\n")
    subprocess.run(["git", "-C", str(inner), "init", "-q"], check=True, env=env())
    p, _ = run("cat vendor/inner/file.txt && echo more >> vendor/inner/file.txt", r)
    check("nested_repo: an untracked repository inside the tree is visible", p.stdout == "inner\n",
          repr(p.stdout + p.stderr))
    check("nested_repo: and live", (inner / "file.txt").read_text() == "inner\nmore\n")


def case_no_repo() -> None:
    d = WORK / "norepo"
    d.mkdir()
    before = set(logs())
    p, _ = run("echo plain > f && echo ok", d)
    check("no_repo: runs, logged, on the real directory", p.stdout == "ok\n" and (d / "f").exists()
          and len(set(logs()) - before) == 1, repr(p.stdout + p.stderr))


def case_off_and_nesting() -> None:
    r = repo("off")
    (WORK / "off").touch()
    before = set(logs())
    p, _ = run("echo unwrapped", r)
    (WORK / "off").unlink()
    check("off: the kill switch leaves the call unwrapped", p.stdout == "unwrapped\n" and set(logs()) == before)
    before = set(logs())
    inner = line("echo inner", WORK / "cwd-inner")
    p, _ = run(f"zsh -c {shlex.quote(inner)}", r)
    check("nesting: a line of the tool's shape inside a call is not wrapped again",
          "inner" in p.stdout and len(set(logs()) - before) == 1, str(len(set(logs()) - before)))


def case_nested_calls() -> None:
    r = repo("nestedcalls")
    inner = line('print -r -- "preload=$LD_PRELOAD asan=$ASAN_OPTIONS"', WORK / "cwd-nested")
    p, _ = run(f"env -u CLAUDE_CALL /usr/bin/zsh -c {shlex.quote(inner)}", r)
    seen = next((w for w in p.stdout.splitlines() if w.startswith("preload=")), "")
    check("nested_calls: a call inside a call carries the preload and the ASan option once",
          seen.count("claude-call-rmdir.so") == 1 and seen.count("verify_asan_link_order=0") == 1, repr(seen))


def case_shape() -> None:
    r = repo("shape")
    before = set(logs())
    p = subprocess.run(["/usr/bin/zsh", "-c", "echo not-the-tool"], cwd=r, env=env(), capture_output=True, text=True,
                       check=False)
    check("shape: a zsh -c not of the tool's shape is not wrapped", p.stdout == "not-the-tool\n"
          and set(logs()) == before, str(len(set(logs()) - before)))


def case_signal() -> None:
    r = repo("signal")
    signals_before = len(events("signal"))
    proc = start("echo changed >> sub/a.txt; tail -f /dev/null", r)
    time.sleep(1.5)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=30)
    check("signal: exits 143", proc.returncode == 143, str(proc.returncode))
    time.sleep(0.3)
    left = subprocess.run(["systemctl", "--user", "list-units", "--all", "--no-legend", "claude-call-*testsess*"],
                          capture_output=True, text=True, check=False).stdout.strip()
    check("signal: its scope is gone", left == "", left)
    views = [d for d in (ROOT / "claude-views").iterdir() if not d.name.startswith(".")]
    check("signal: its view is removed", not views, str(views))
    check("signal: not synced back", (r / "sub" / "a.txt").read_text() == "one\n")
    text = logs()[-1].read_text()
    check("signal: the log names what it had changed", "not synced back" in text and "sub/a.txt" in text, text[-300:])
    check("signal: recorded as an event", len(events("signal")) == signals_before + 1, str(events()[-2:]))


def case_stale_view() -> None:
    r = repo("stale")
    stale = ROOT / "claude-views" / "stale-one"
    (stale / "snap").mkdir(parents=True)
    (stale / "owner").write_text("999999999 1 claude-call-stale-one.scope\n")
    run("true", r)
    check("stale_view: a view whose wrapper is gone is removed", not stale.exists())


def case_fail_open() -> None:
    r = repo("failopen")
    count = WORK / "runs"

    def ran() -> int:
        n = len(count.read_text().splitlines()) if count.exists() else 0
        count.unlink(missing_ok=True)
        return n

    p, _ = run(f"echo once >> {count}; echo still-runs", r)
    check("fail_open: a call runs exactly once", p.stdout == "still-runs\n" and ran() == 1, repr(p.stdout + p.stderr))
    p, _ = run(f"echo once >> {count}; echo still-runs", r, warns=True,
               CLAUDE_CALL_ROOT="/proc/claude-call-cannot-exist")
    check("fail_open: without a log or a view the call runs, once", p.stdout == "still-runs\n" and ran() == 1,
          repr(p.stdout + p.stderr))
    started_before = len(events("never-started"))
    p, _ = run(f"echo once >> {count}; echo still-runs", r, warns=True,
               CLAUDE_CALL_SLICE="init.scope")  # not a slice: refused
    check("fail_open: when its scope cannot start the call runs unwrapped, once",
          "still-runs" in p.stdout and ran() == 1 and "runs unwrapped" in p.stderr, repr(p.stdout + p.stderr))
    check("fail_open: a call that never started is recorded as an event",
          len(events("never-started")) == started_before + 1, str(events()[-2:]))
    failed_before = len(events("wrapper-failed"))
    gone = r / "gone"
    gone.mkdir()
    p = subprocess.run(["/bin/sh", "-c", 'cd "$1" && rmdir "$1" && exec /usr/bin/zsh -c "$2"', "sh", str(gone),
                        line(f"echo once >> {count}; echo still-runs", WORK / "cwd-gone")],
                       env=env(), capture_output=True, text=True, timeout=60, check=False)
    check("fail_open: when the wrapper itself fails (its cwd is gone) the call runs unwrapped, once",
          "still-runs" in p.stdout and ran() == 1 and "runs unwrapped" in p.stderr, repr(p.stdout + p.stderr))
    check("fail_open: a wrapper failure is recorded as an event",
          len(events("wrapper-failed")) == failed_before + 1, str(events()[-2:]))


def case_events_cap() -> None:
    r = repo("eventscap")
    f = ROOT / "claude-call-events.log"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(f"2026-01-01T00:00:00 filler0 filler /x: line {i:07}\n" for i in range(30000)))
    run("true", r, warns=True, CLAUDE_CALL_SLICE="init.scope")
    lines = f.read_text().splitlines()
    check("events_cap: the record stays within 1 MiB, its newest lines kept",
          f.stat().st_size <= 1 << 20 and lines[-1].split()[2] == "never-started" and "line 0029999" in lines[-2],
          f"{f.stat().st_size} bytes, last {lines[-1][:60]!r}")


def case_prune() -> None:
    r = repo("prune")
    d = ROOT / "claude-calls"
    d.mkdir(parents=True, exist_ok=True)
    old = time.time() - 6 * 86400
    for i in range(3):
        f = d / f"00-old-{i}.log"
        f.write_text("old\n")
        os.utime(f, (old, old))
    for i in range(60):
        (d / f"01-many-{i:02}.log").write_text("x\n")
    run("true", r)
    names = [p.name for p in logs()]
    check("prune: nothing older than 5 days", not any(n.startswith("00-old") for n in names))
    check("prune: at most 50 logs", len(names) <= 50, str(len(names)))
    big = d / "02-big.log"
    with open(big, "wb") as f:
        f.truncate(101 << 20)
    run("true", r)
    check("prune: at most 100 MiB", sum(p.stat().st_size for p in logs()) <= 100 << 20)
    for p in logs():
        p.unlink()
    for i in range(2):
        (d / f"03-fresh-{i}.log").write_text("fresh\n")
        f = d / f"03-stale-{i}.log"
        f.write_text("stale\n")
        os.utime(f, (old, old))
    run("true", r)
    names = [p.name for p in logs()]
    check("prune: a log older than 5 days goes though few remain", not any("stale" in n for n in names)
          and sum("fresh" in n for n in names) == 2, str(names))


def case_cost() -> None:
    top = Path("/home/nicolas/dev/agda/aletheia")
    runs: dict[str, list[float]] = {"bare": [], "wrapped": []}
    for _ in range(5):
        for label, extra in (("bare", {"CLAUDE_CALL_OFF": "/"}), ("wrapped", {})):
            t = time.monotonic()
            p, _ = run("true", top, **extra)
            runs[label].append(1000 * (time.monotonic() - t))
    med = {k: sorted(v)[2] for k, v in runs.items()}
    print(f"     cost on aletheia, `true`, median of 5: bare {med['bare']:.0f} ms, wrapped {med['wrapped']:.0f} ms")
    check("cost: a wrapped call on aletheia runs", p.returncode == 0, p.stderr)


CASES = {n[5:]: f for n, f in sorted(globals().items()) if n.startswith("case_")}

try:
    for name, fn in CASES.items():
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        try:
            fn()
        except Exception as e:  # a case that raises is a failure of that case
            check(f"{name}: raised", False, f"{type(e).__name__}: {e}")
finally:
    subprocess.run("systemctl --user stop 'claude-call-*testsess*'", shell=True, capture_output=True, check=False)
    subprocess.run(["chmod", "-R", "u+rwx", str(WORK)], capture_output=True, check=False)
    shutil.rmtree(WORK, ignore_errors=True)
failed = [n for n, ok, _ in results if not ok]
print(f"{len(results) - len(failed)} passed, {len(failed)} failed" + (": " + ", ".join(failed) if failed else ""))
sys.exit(1 if failed else 0)
