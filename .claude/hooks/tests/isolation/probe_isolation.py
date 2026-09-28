#!/home/nicolas/.local/bin/python3.14
"""Probes for the isolation design: bind cost, git inside a snapshot, and the per-call wrapper's three properties.

usage: probe_isolation.py [REPO]   (REPO: a git tree read, never written, for the bind measurement; default aletheia)

1. binds: bubblewrap startup with the snapshot of REPO's sources at REPO's own path and each collapsed ignored
   directory bound live from the real tree; reports the number of binds and the time, and checks that a build
   directory is the real one and a source write stays in the snapshot.
2. git: in a throwaway repository, `git add` and `git commit` inside the snapshot with .git writable; reports what
   the real tree shows before and after the source files are synced back.
3. wrapper: a simulated tool line (`zsh -c 'eval CALL; pwd -P >| CWDFILE'`) around the wrapper, on a throwaway
   repository: a cd persists to the next call, the log holds the whole output, a job left running is stopped;
   and the wrapper's cost on a plain `ls` against the bare call.
Exit 0 when every property holds.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/nicolas/dev/agda/aletheia")
WORK = Path(tempfile.mkdtemp(prefix="isolation-probe-", dir=os.environ.get("TMPDIR")))
SNAPSHOT_RC = sorted(Path.home().glob(".claude/shell-snapshots/snapshot-zsh-*"), key=os.path.getmtime)[-1]
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{': ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def remove(tree: Path) -> None:
    """Remove a probe's tree, overlay work directories (mode 000) included, and say so if anything is left."""
    subprocess.run(["chmod", "-R", "u+rwx", str(tree)], capture_output=True)
    shutil.rmtree(tree, ignore_errors=True)
    check(f"{tree} removed", not tree.exists())


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def snapshot(repo: Path, dest: Path) -> float:
    """Copy the sources (tracked, and untracked not ignored, as the working tree has them) to dest."""
    t = time.monotonic()
    listed = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    present = "\0".join(p for p in dict.fromkeys(listed) if p and os.path.lexists(repo / p))  # a deleted tracked file
    # posix format: gnu truncates mtimes to the second, and a build tool comparing a source's mtime with its output's
    # then misses an edit made in the second after the last build.
    subprocess.run(f"tar --format=posix --null -T - -cf - | tar -xf - -C '{dest}'",
                   shell=True, check=True, cwd=repo, input=present.encode())
    return time.monotonic() - t


def classify_ignored(repo: Path) -> None:
    """The ignored entries a snapshot of the sources misses, by how the design treats them: a directory ignored whole,
    a directory whose only tracked file is its .gitignore (an output directory), and the rest, scattered files."""
    entries = [p for p in git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
               .split("\0") if p]
    whole = [p for p in entries if p.endswith("/")]
    outputs: dict[str, int] = {}
    scattered: list[tuple[str, int]] = []
    for f in (p for p in entries if not p.endswith("/")):
        parent = os.path.dirname(f)
        tracked = git(repo, "ls-files", "--", parent).split() if parent else ["(root)"]
        size = os.lstat(repo / f).st_size
        if tracked == [f"{parent}/.gitignore"]:
            outputs[parent] = outputs.get(parent, 0) + 1
        else:
            scattered.append((f, size))
    print(f"     ignored whole: {len(whole)} directories; output directories: {outputs}")
    print(f"     scattered ignored files: {len(scattered)}, {sum(s for _, s in scattered) / 2**20:.1f} MiB: "
          + ", ".join(f"{f} {s // 1024}K" for f, s in sorted(scattered, key=lambda x: -x[1])[:8]))


def ignored_dirs(repo: Path) -> list[str]:
    """Ignored directories, collapsed: the build outputs and caches bound live from the real tree."""
    out = git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
    return [p.rstrip("/") for p in out.split("\0") if p.endswith("/")]


def bwrap_argv(repo: Path, snap: Path, binds: list[str], git_writable: bool) -> list[str]:
    argv = ["bwrap", "--dev-bind", "/", "/", "--bind", str(snap), str(repo)]
    for rel in binds:
        (snap / rel).mkdir(parents=True, exist_ok=True)
        argv += ["--bind", str(repo / rel), str(repo / rel)]
    argv += ["--bind" if git_writable else "--ro-bind", str(repo / ".git"), str(repo / ".git")]
    return argv


def probe_binds() -> None:
    snap = WORK / "snap-binds"
    snap.mkdir()
    t_snap = snapshot(REPO, snap)
    dirs = ignored_dirs(REPO)
    argv = bwrap_argv(REPO, snap, dirs, git_writable=False)
    t = time.monotonic()
    live = dirs[0]
    p = subprocess.run(argv + ["--chdir", str(REPO), "sh", "-c", f"echo x > .isolation-probe; stat -c %d:%i '{live}'"],
                       capture_output=True, text=True)
    t_run = time.monotonic() - t
    check("snapshot of the sources", t_snap < 1, f"{t_snap:.2f}s")
    main = "src/Aletheia/Main.agda"
    check("the snapshot keeps each mtime to the nanosecond",
          (REPO / main).stat().st_mtime_ns == (snap / main).stat().st_mtime_ns)
    check("bwrap with the ignored directories bound live", p.returncode == 0 and t_run < 0.2,
          f"{len(dirs)} binds, {t_run * 1000:.0f} ms {p.stderr.strip()[:80]}")
    real = subprocess.run(["stat", "-c", "%d:%i", str(REPO / live)], capture_output=True, text=True).stdout.strip()
    check(f"the ignored directory {live} in the view is the real one", p.stdout.strip() == real, p.stdout.strip())
    check("a source write lands in the snapshot only", (snap / ".isolation-probe").exists()
          and not (REPO / ".isolation-probe").exists())
    files = [p for p in git(REPO, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
             .split("\0") if p and not p.endswith("/")]
    print(f"     ignored files, not visible in the view unless bound one by one: {len(files)} {files[:6]}")
    classify_ignored(REPO)
    target = WORK / "bound-file"
    target.write_text("real\n")
    (snap / ".bound-file").write_text("")
    q = subprocess.run(argv + ["--bind", str(target), str(REPO / ".bound-file"), "--chdir", str(REPO), "sh", "-c",
                               "echo new > .bound-file.tmp && mv .bound-file.tmp .bound-file"],
                       capture_output=True, text=True)
    check("a file bound live cannot be replaced by rename (why scattered ignored files are copied, not bound)",
          q.returncode != 0 and "busy" in q.stderr.lower(), q.stderr.strip()[:100])
    for label, view in (("real tree", []), ("snapshot view", argv + ["--chdir", str(REPO)])):
        t = time.monotonic()
        q = subprocess.run(view + ["git", "-C", str(REPO), "status", "--short"], capture_output=True, text=True)
        print(f"     git status in the {label}: {1000 * (time.monotonic() - t):.0f} ms, rc {q.returncode}, "
              f"{len(q.stdout.splitlines())} lines")


def throwaway(name: str) -> Path:
    repo = WORK / name
    (repo / "src").mkdir(parents=True)
    (repo / "build").mkdir()
    (repo / ".gitignore").write_text("build/\n")
    (repo / "src" / "a.txt").write_text("one\n")
    (repo / "src" / "gone.txt").write_text("tracked, then deleted from the working tree\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    (repo / "src" / "gone.txt").unlink()
    return repo


def probe_git() -> None:
    repo = throwaway("gitrepo")
    snap = WORK / "snap-git"
    snap.mkdir()
    snapshot(repo, snap)
    check("a tracked file deleted from the working tree is absent from the snapshot, not an error",
          not (snap / "src" / "gone.txt").exists() and (snap / "src" / "a.txt").exists())
    argv = bwrap_argv(repo, snap, ignored_dirs(repo), git_writable=True)
    inner = ("echo two >> src/a.txt && git add src/a.txt && git -c user.name=t -c user.email=t@t -c commit.gpgsign=false "
             "commit -qm inside && git status --short && echo committed")
    p = subprocess.run(argv + ["--chdir", str(repo), "sh", "-c", inner], capture_output=True, text=True)
    check("git add and commit inside the snapshot", p.returncode == 0 and "committed" in p.stdout,
          (p.stdout + p.stderr).strip().replace("\n", " | ")[:120])
    head = git(repo, "log", "-1", "--format=%s").strip()
    before = sorted(git(repo, "status", "--short").splitlines())
    check("the commit is in the real repository", head == "inside", head)
    check("before sync-back the real tree lags its own HEAD", before == [" D src/gone.txt", " M src/a.txt"], str(before))
    shutil.copy2(snap / "src" / "a.txt", repo / "src" / "a.txt")  # sync-back of the one changed source file
    after = sorted(git(repo, "status", "--short").splitlines())
    check("after sync-back the real tree matches HEAD but for its own deletion", after == [" D src/gone.txt"], str(after))


def wrapped(call: str, repo: Path, snap: Path, log: Path, cwdfile: Path, unit: str) -> str:
    """The per-call wrapper as the tool's zsh would eval it: the call in a scope and a snapshot view, its output
    to the log and the terminal, its leftovers stopped, its last directory carried back to the tool's shell."""
    # $PWD and not pwd -P: zsh's pwd -P walks up matching inodes, and the snapshot directory and the path it is
    # bound at share one, so it can name the snapshot's own path; the tool's shell resolves the logical one.
    inner = f"source {SNAPSHOT_RC} 2>/dev/null; eval \"$__CALL\"; __rc=$?; print -r -- \"$PWD\" >| {cwdfile}; exit $__rc"
    bw = shlex.join(bwrap_argv(repo, snap, ignored_dirs(repo), git_writable=True))
    # The stop is inside the group: zsh waits for a >(...) attached to a { } group, and tee sees EOF only once the
    # call's last process holding the pipe is gone, so a stop after the group would wait on the job it stops.
    return (f"export __CALL={shlex.quote(call)}; "
            f"{{ systemd-run --user --scope --quiet --collect --unit={unit} -p TimeoutStopSec=2s -- {bw} --chdir \"$PWD\" "
            f"zsh -c {shlex.quote(inner)}; __rc=$?; systemctl --user stop {unit}.scope 2>/dev/null; }} "
            f"> >(tee -a {log}) 2> >(tee -a {log} >&2); "
            f"[ -s {cwdfile} ] && cd \"$(cat {cwdfile})\"; (exit $__rc)")


def tool_line(text: str, cwd: Path, harness_cwd: Path) -> subprocess.CompletedProcess:
    """The shape of the tool's own command line, as measured: the snapshot, then eval, then pwd to its cwd file."""
    line = f"source {SNAPSHOT_RC} 2>/dev/null || true && eval {shlex.quote(text)} < /dev/null && pwd -P >| {harness_cwd}"
    try:
        return subprocess.run(["/usr/bin/zsh", "-c", line], cwd=cwd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(e.cmd, 124, str(e.stdout or ""), "timed out after 20 s")


def alive(stdout: str, tag: str) -> bool:
    """Whether the process whose pid the call printed after `tag` still runs: by its pid, never by a pattern over the
    process table, which also matches the shell whose command line holds the pattern."""
    pid = next((w.split()[1] for w in stdout.splitlines() if w.startswith(tag + " ")), None)
    if pid is None:
        check(f"the call printed its {tag} pid", False, repr(stdout[:80]))
        return True
    return Path(f"/proc/{pid}").exists()


def probe_wrapper() -> None:
    repo = throwaway("wraprepo")
    snap = WORK / "snap-wrap"
    snap.mkdir()
    snapshot(repo, snap)
    log, cwdfile, harness_cwd = WORK / "call.log", WORK / "call.cwd", WORK / "harness.cwd"
    call = "cd src && echo in-src; sleep 299.25 & echo started $!"
    p = tool_line(wrapped(call, repo, snap, log, cwdfile, "isolation-probe-1"), repo, harness_cwd)
    check("the call ran", p.returncode == 0 and "in-src" in p.stdout and "started" in p.stdout,
          (p.stdout + p.stderr).strip().replace("\n", " | ")[:120])
    got = harness_cwd.read_text().strip() if harness_cwd.exists() else ""
    check("a cd inside the call persists to the tool's shell", got == str(repo / "src"), got)
    text = log.read_text() if log.exists() else ""
    check("the log holds the call's whole output", "in-src" in text and "started" in text, repr(text[:80]))
    time.sleep(0.5)
    check("a job the call left running is stopped", not alive(p.stdout, "started"))
    for n, call in enumerate(("cd src && false", "cd src; exit 3", "cd src; true")):
        (WORK / "bare.cwd").unlink(missing_ok=True)
        (WORK / "wrapped.cwd").unlink(missing_ok=True)
        cwdfile.unlink(missing_ok=True)
        b = tool_line(call, repo, WORK / "bare.cwd")
        w = tool_line(wrapped(call, repo, snap, log, cwdfile, f"isolation-probe-f{n}"), repo, WORK / "wrapped.cwd")
        cwd = [(WORK / f).read_text().strip() if (WORK / f).exists() else "(none)" for f in ("bare.cwd", "wrapped.cwd")]
        check(f"`{call}`: the wrapper keeps the bare call's exit status and cwd", (b.returncode, cwd[0]) == (w.returncode, cwd[1]),
              f"bare {b.returncode} {cwd[0]}, wrapped {w.returncode} {cwd[1]}")
    t = time.monotonic()
    # zsh does not hand an ignored TERM to a background job, so the sleeper ignores it through sh.
    term_call = ("sh -c \"trap '' TERM; exec sleep 299.5\" & sleep 0.2; echo leftover $!; "
                 "awk '/^SigIgn/{print \"sigign\", $2}' /proc/$!/status")
    q = tool_line(wrapped(term_call, repo, snap, log, cwdfile, "isolation-probe-t"), repo, harness_cwd)
    took = time.monotonic() - t
    mask = next((int(w.split()[1], 16) for w in q.stdout.splitlines() if w.startswith("sigign")), 0)
    check("the leftover really ignores SIGTERM", bool(mask & (1 << 14)), f"SigIgn {mask:#x} {q.stderr.strip()[:80]}")
    check("a leftover ignoring SIGTERM is killed at the scope's stop timeout (2 s)",
          not alive(q.stdout, "leftover") and 1.5 < took < 5, f"{took:.2f} s")
    for label, text in (("bare", "ls >/dev/null"), ("wrapped", wrapped("ls >/dev/null", repo, snap, log, cwdfile,
                                                                          "isolation-probe-2"))):
        runs = []
        for _ in range(5):
            t = time.monotonic()
            tool_line(text, repo, harness_cwd)
            runs.append(1000 * (time.monotonic() - t))
        print(f"     cost: {label} ls, median of 5: {sorted(runs)[2]:.0f} ms")


def probe_overlay() -> None:
    """Each ignored directory as an overlay on the real one, its upper layer on the repository's own filesystem (under
    the ignored .claude/), so a build reads its incremental state and writes only its upper layer."""
    root = Path(tempfile.mkdtemp(prefix="isolation-probe-", dir=REPO / ".claude"))
    try:
        snap = root / "snap"
        snap.mkdir()
        snapshot(REPO, snap)
        dirs = ignored_dirs(REPO)
        argv = ["bwrap", "--dev-bind", "/", "/", "--bind", str(snap), str(REPO)]
        for i, rel in enumerate(dirs):
            (snap / rel).mkdir(parents=True, exist_ok=True)
            (root / f"u{i}").mkdir()
            (root / f"w{i}").mkdir()
            argv += ["--overlay-src", str(REPO / rel), "--overlay", str(root / f"u{i}"), str(root / f"w{i}"), str(REPO / rel)]
        live = dirs[0]
        t = time.monotonic()
        p = subprocess.run(argv + ["--chdir", str(REPO), "sh", "-c", f"ls '{live}' >/dev/null && echo x > '{live}/.isolation-probe'"],
                           capture_output=True, text=True)
        t_run = time.monotonic() - t
        check("bwrap with each ignored directory an overlay, upper on the repository's filesystem",
              p.returncode == 0 and t_run < 0.2, f"{len(dirs)} overlays, {t_run * 1000:.0f} ms {p.stderr.strip()[:100]}")
        check("a build write lands in the upper layer, not the real directory",
              (root / "u0" / ".isolation-probe").exists() and not (REPO / live / ".isolation-probe").exists())
    finally:
        remove(root)


def probe_remove_mounted_dir() -> None:
    """A call removing a whole ignored directory: the directory is a mount point in the view, under either mechanism."""
    for kind in ("bind", "overlay"):
        repo = throwaway(f"rmrepo-{kind}")
        (repo / "build" / "out.o").write_text("artifact\n")
        snap = WORK / f"snap-rm-{kind}"
        snap.mkdir()
        snapshot(repo, snap)
        (snap / "build").mkdir()
        argv = ["bwrap", "--dev-bind", "/", "/", "--bind", str(snap), str(repo)]
        if kind == "bind":
            argv += ["--bind", str(repo / "build"), str(repo / "build")]
        else:
            (WORK / f"u-{kind}").mkdir()
            (WORK / f"w-{kind}").mkdir()
            argv += ["--overlay-src", str(repo / "build"), "--overlay", str(WORK / f"u-{kind}"), str(WORK / f"w-{kind}"),
                     str(repo / "build")]
        q = subprocess.run(argv + ["--chdir", str(repo), "sh", "-c", "rm -rf build; echo rc=$?; mkdir -p build"],
                           capture_output=True, text=True)
        print(f"     rm -rf of a {kind}-mounted ignored directory: {q.stdout.split()[0] if q.stdout else '?'}, "
              f"{q.stderr.strip()[:70]!r}; real artifact left: {(repo / 'build' / 'out.o').exists()}")


def stacked_argv(repo: Path, snap: Path, upper: Path, work: Path) -> list[str]:
    """One overlay over the whole repository: the snapshot of its sources above the real tree, every write to upper;
    .git bound writable on top. No mount point inside the tree but .git."""
    for d in (upper, work):
        d.mkdir(parents=True, exist_ok=True)
    return ["bwrap", "--dev-bind", "/", "/", "--overlay-src", str(repo), "--overlay-src", str(snap),
            "--overlay", str(upper), str(work), str(repo), "--bind", str(repo / ".git"), str(repo / ".git")]


def probe_stacked() -> None:
    repo = throwaway("stackrepo")
    (repo / "build" / "out.o").write_text("artifact\n")
    (repo / ".gitignore").write_text("build/\nnotes.txt\n")
    (repo / "notes.txt").write_text("scattered ignored\n")
    snap = WORK / "snap-stack"
    snap.mkdir()
    snapshot(repo, snap)
    (repo / "src" / "a.txt").write_text("edited in the real tree after the snapshot\n")
    (repo / "src" / "new.txt").write_text("created in the real tree after the snapshot\n")
    upper = WORK / "stack-upper"
    argv = stacked_argv(repo, snap, upper, WORK / "stack-work")
    script = ("cat src/a.txt; test -e src/new.txt && echo new-visible; cat build/out.o; "
              "rm -rf build && mkdir build && echo rebuilt > build/out.o && echo rm-ok; "
              "echo replaced > notes.tmp && mv notes.tmp notes.txt && echo mv-ok; git status --short | wc -l")
    q = subprocess.run(argv + ["--chdir", str(repo), "sh", "-c", script], capture_output=True, text=True)
    out = q.stdout
    check("stacked overlay mounts", q.returncode == 0, q.stderr.strip()[:120])
    check("stacked: a source edited in the real tree after the snapshot is not seen", "one" in out.splitlines()[:1])
    print(f"     stacked: a source created in the real tree after the snapshot is seen: {'new-visible' in out}")
    check("stacked: the real tree's ignored artifact is read", "artifact" in out)
    check("stacked: rm -rf of an ignored directory, then its rebuild, succeed", "rm-ok" in out)
    check("stacked: a scattered ignored file is replaced by rename", "mv-ok" in out)
    check("stacked: the real tree is untouched", (repo / "build" / "out.o").read_text() == "artifact\n"
          and (repo / "notes.txt").read_text() == "scattered ignored\n")
    kinds = {p.relative_to(upper).as_posix(): ("whiteout" if p.is_char_device() else "dir" if p.is_dir() else "file")
             for p in upper.rglob("*")}
    print(f"     stacked: upper layer after the call: {kinds}")
    opaque = subprocess.run(["getfattr", "--absolute-names", "-n", "user.overlay.opaque", str(upper / "build")],
                            capture_output=True, text=True).stdout.strip().replace("\n", " ")
    print(f"     stacked: the rebuilt directory's opaque mark: {opaque or '(none)'}")
    inside = repo / ".claude" / "upper"
    r = subprocess.run(stacked_argv(repo, snap, inside, repo / ".claude" / "work") + ["true"], capture_output=True, text=True)
    print(f"     stacked: an upper inside the repository mounts: {r.returncode == 0} {r.stderr.strip()[:90]!r}")
    # aletheia: one overlay, git status and a whole-tree walk through it
    root = Path(tempfile.mkdtemp(prefix="isolation-probe-", dir=os.environ.get("TMPDIR")))
    try:
        s2 = root / "snap"
        s2.mkdir()
        snapshot(REPO, s2)
        argv = stacked_argv(REPO, s2, root / "u", root / "w") + ["--chdir", str(REPO)]
        for label, cmd in (("mount and true", ["true"]), ("git status", ["git", "status", "--short"]),
                           ("walk of every file", ["sh", "-c", "find . -path ./.git -prune -o -type f -print | wc -l"])):
            t = time.monotonic()
            v = subprocess.run(argv + cmd, capture_output=True, text=True)
            tv = 1000 * (time.monotonic() - t)
            t = time.monotonic()
            b = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True) if label != "mount and true" else v
            tb = 1000 * (time.monotonic() - t)
            print(f"     stacked on aletheia, {label}: view {tv:.0f} ms (rc {v.returncode}), real tree {tb:.0f} ms"
                  + (f", {v.stdout.strip()} vs {b.stdout.strip()} files" if label.startswith("walk") else ""))
    finally:
        remove(root)


try:
    probe_stacked()
    probe_remove_mounted_dir()
    probe_binds()
    probe_overlay()
    probe_git()
    probe_wrapper()
finally:
    subprocess.run(["systemctl", "--user", "stop", "isolation-probe-1.scope", "isolation-probe-2.scope",
                    "isolation-probe-t.scope", "isolation-probe-f0.scope", "isolation-probe-f1.scope",
                    "isolation-probe-f2.scope"], capture_output=True)
    remove(WORK)
print(f"{len(failures)} failing" + (": " + ", ".join(failures) if failures else ""))
sys.exit(1 if failures else 0)
