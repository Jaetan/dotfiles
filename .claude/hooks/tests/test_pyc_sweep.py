#!/home/nicolas/.local/bin/python3.14
"""Regression cases for pyc-sweep, the hourly sweep of the bytecode caches and ended calls' folders under /tmp.

Each case builds a tree in a temporary directory of its own, every time in it set against one fixed
NOW, and runs the sweeper's main() in this process with --now NOW: no clock is read. A directory the
sweep cannot read and a removal that fails are given by stand-ins for the sweeper's Path and shutil.rmtree,
since the mutant runner runs this suite as root in a user namespace, where chmod refuses nothing; a call's
process is a stat file in a stand-in /proc, so no case rests on a real process. One case runs the sweeper
by its path, through its own shebang; another checks that the shebang is the kit's line.

Usage: test_pyc_sweep.py [SWEEPER]
  SWEEPER  the sweeper to test, or a directory holding it (default: ~/dotfiles/.local/bin)
Exit 0 when every case agrees with its expectation.
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import NamedTuple, NewType

# Prose a case names itself by or a line the sweeper prints; a case's name; seconds of age or of the
# fixed clock; a size in bytes; a file's body; a command-line argument; the exit status; a process id, its start
# time as /proc gives it, and the name of a call's folder.
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
Seconds = NewType("Seconds", float)
Bytes = NewType("Bytes", int)
Content = NewType("Content", bytes)
Argument = NewType("Argument", str)
ExitStatus = NewType("ExitStatus", int)
Pid = NewType("Pid", int)
StartTime = NewType("StartTime", str)
FolderName = NewType("FolderName", str)

GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "dotfiles" / ".local" / "bin").resolve()
SWEEPER = GIVEN / "pyc-sweep" if GIVEN.is_dir() else GIVEN
KIT_LINE = "#!/home/nicolas/.local/bin/python3.14\n"
NOW = Seconds(2_000_000_000.0)
OLD = Seconds(3 * 3600)  # older than the default age of 7200 s
YOUNG = Seconds(600)
BYTECODE = Content(b"bytecode")


def load() -> ModuleType:
    """Load the sweeper as a module."""
    loader = importlib.machinery.SourceFileLoader("pyc_sweep_under_test", str(SWEEPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


SWEEP = load()


class Run(NamedTuple):
    """What one run of main() printed and returned."""

    status: ExitStatus
    out: list[Prose]
    err: list[Prose]


def run(argv: list[Argument]) -> Run:
    """Run the sweeper's main() with ARGV, catching what it prints."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = SWEEP.main(argv)
    return Run(ExitStatus(status), [Prose(x) for x in out.getvalue().splitlines()],
               [Prose(x) for x in err.getvalue().splitlines()])


def at_now(*words: Path | Argument) -> Run:
    """Run the sweeper on WORDS against the fixed clock."""
    return run([Argument("--now"), Argument(str(NOW)), *(Argument(str(word)) for word in words)])


@contextlib.contextmanager
def tree() -> Iterator[Path]:
    """Yield a fresh directory to build a case's tree in, removed afterwards."""
    with tempfile.TemporaryDirectory(prefix="pyc-sweep-case-") as where:
        yield Path(where)


def put(path: Path, age: Seconds, text: Content = BYTECODE) -> Path:
    """Write a file at PATH, written AGE seconds before NOW."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    os.utime(path, (NOW - age, NOW - age))
    return path


def age_dirs(root: Path, age: Seconds) -> None:
    """Give every directory under ROOT, ROOT included, the time AGE seconds before NOW."""
    for path in sorted((p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()), reverse=True):
        os.utime(path, (NOW - age, NOW - age), follow_symlinks=False)
    os.utime(root, (NOW - age, NOW - age))


def removed(result: Run) -> list[Path]:
    """Return the paths a run's lines say it removed."""
    return [Path(line.split()[1]) for line in result.out if line.startswith("removed ")]


def size_of(result: Run, path: Path) -> Bytes:
    """Return the size a run's removal line gives PATH, or -1."""
    for line in result.out:
        parts = line.split()
        if parts[0] in ("removed", "would") and Path(parts[-2]) == path:
            return Bytes(int(parts[-1]))
    return Bytes(-1)


def expect(cond: bool, what: Prose) -> list[Prose]:
    return [] if cond else [what]


def old_pyc_dir_removed() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "home" / "x" / "m.cpython-314.pyc", OLD)
        age_dirs(root, OLD)
        result = at_now(root)
        return (expect(removed(result) == [root / "pyc.a"], Prose(f"removed {removed(result)}"))
                + expect(not (root / "pyc.a").exists(), Prose("pyc.a still there"))
                + expect(result.status == 0, Prose(f"exit {result.status}")))


def young_file_kept() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        put(root / "pyc.a" / "n.pyc", YOUNG)
        put(root / "pyc.b" / "m.pyc", OLD)
        put(root / "pyc.b" / "deep" / "n.pyc", YOUNG)
        age_dirs(root, OLD)
        for young in (root / "pyc.a" / "n.pyc", root / "pyc.b" / "deep" / "n.pyc"):
            os.utime(young, (NOW - YOUNG, NOW - YOUNG))
        result = at_now(root)
        return expect(removed(result) == [] and (root / "pyc.b" / "deep" / "n.pyc").exists(),
                      Prose(f"removed {removed(result)}"))


def young_directory_kept() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        age_dirs(root, OLD)
        os.utime(root / "pyc.a", (NOW - YOUNG, NOW - YOUNG))
        result = at_now(root)
        return expect(removed(result) == [], Prose(f"removed {removed(result)}"))


def sources_kept_their_cache_removed() -> list[Prose]:
    with tree() as root:
        put(root / "pkg" / "m.py", OLD, Content(b"x = 1\n"))
        put(root / "pkg" / "__pycache__" / "m.cpython-314.pyc", OLD)
        age_dirs(root, OLD)
        result = at_now(root)
        return (expect(removed(result) == [root / "pkg" / "__pycache__"], Prose(f"removed {removed(result)}"))
                + expect((root / "pkg" / "m.py").exists(), Prose("the source went")))


def symlink_kept() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        (root / "pyc.a" / "link").symlink_to(root / "pyc.a" / "m.pyc")
        age_dirs(root, OLD)
        os.utime(root / "pyc.a" / "link", (NOW - OLD, NOW - OLD), follow_symlinks=False)
        result = at_now(root)
        return expect(removed(result) == [], Prose(f"removed {removed(result)}"))


def pyc_named_symlink_kept() -> list[Prose]:
    with tree() as root:
        put(root / "elsewhere" / "real.pyc", OLD)
        put(root / "elsewhere" / "keep.txt", OLD, Content(b"keep"))
        (root / "pyc.a").mkdir()
        (root / "pyc.a" / "m.pyc").symlink_to(root / "elsewhere" / "real.pyc")
        age_dirs(root, OLD)
        os.utime(root / "pyc.a" / "m.pyc", (NOW - OLD, NOW - OLD), follow_symlinks=False)
        result = at_now(root)
        return expect(removed(result) == [], Prose(f"removed {removed(result)}"))


def fifo_kept() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        os.mkfifo(root / "pyc.a" / "pipe")
        os.utime(root / "pyc.a" / "pipe", (NOW - OLD, NOW - OLD))
        age_dirs(root, OLD)
        result = at_now(root)
        return expect(removed(result) == [], Prose(f"removed {removed(result)}"))


def other_file_kept() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        put(root / "pyc.a" / "notes.txt", OLD, Content(b"notes"))
        age_dirs(root, OLD)
        result = at_now(root)
        return expect(removed(result) == [], Prose(f"removed {removed(result)}"))


def empty_kept() -> list[Prose]:
    with tree() as root:
        (root / "empty").mkdir()
        (root / "shells" / "a" / "b").mkdir(parents=True)
        age_dirs(root, OLD)
        result = at_now(root)
        return expect(removed(result) == [] and (root / "empty").exists(), Prose(f"removed {removed(result)}"))


def root_never_removed() -> list[Prose]:
    with tree() as base:
        root = base / "root"
        put(root / "m.pyc", OLD)
        age_dirs(base, OLD)
        result = at_now(root)
        return expect(removed(result) == [] and (root / "m.pyc").exists(), Prose(f"removed {removed(result)}"))


def largest_subtree_once() -> list[Prose]:
    with tree() as root:
        files = [put(root / "pyc.a" / "home" / "a" / "m.pyc", OLD, Content(b"m" * 7000)),
                 put(root / "pyc.a" / "home" / "b" / "n.pyc", OLD, Content(b"n" * 300))]
        age_dirs(root, OLD)
        want = blocks(files)
        result = at_now(root)
        return (expect(removed(result) == [root / "pyc.a"], Prose(f"removed {removed(result)}"))
                + expect(size_of(result, root / "pyc.a") == want,
                         Prose(f"size {size_of(result, root / 'pyc.a')}, not {want}")))


def blocks(files: list[Path]) -> Bytes:
    """Return the bytes FILES take on their file system, as the sweeper counts them."""
    return Bytes(sum(path.stat().st_blocks * 512 for path in files))


def deep_other_file_keeps_every_parent() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "c.pyc", OLD)
        put(root / "pyc.a" / "b" / "x.pyc", OLD)
        put(root / "pyc.a" / "b" / "notes.txt", OLD, Content(b"notes"))
        age_dirs(root, OLD)
        result = at_now(root)
        return expect(removed(result) == [] and (root / "pyc.a" / "b" / "notes.txt").exists(),
                      Prose(f"removed {removed(result)}"))


def not_ours_kept() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        age_dirs(root, OLD)
        real = SWEEP.ME
        SWEEP.ME = real + 1
        try:
            result = at_now(root)
        finally:
            SWEEP.ME = real
        return expect(removed(result) == [] and (root / "pyc.a" / "m.pyc").exists(),
                      Prose(f"removed {removed(result)}"))


def nested_under_work_dir() -> list[Prose]:
    with tree() as root:
        put(root / "work" / "plants.txt", OLD, Content(b"plants"))
        put(root / "work" / "pyc-1" / "m.pyc", OLD)
        put(root / "work" / "pyc-2" / "sub" / "n.pyc", OLD)
        age_dirs(root, OLD)
        result = at_now(root)
        got = sorted(removed(result))
        want = sorted([root / "work" / "pyc-1", root / "work" / "pyc-2"])
        return (expect(got == want, Prose(f"removed {got}"))
                + expect((root / "work" / "plants.txt").exists(), Prose("the work file went")))


def unreadable_kept_sibling_swept() -> list[Prose]:
    with tree() as root:
        put(root / "work" / "locked" / "m.pyc", OLD)
        put(root / "work" / "pyc-1" / "n.pyc", OLD)
        put(root / "locked-parent" / "locked" / "m.pyc", OLD)
        age_dirs(root, OLD)
        locked = {root / "work" / "locked", root / "locked-parent" / "locked"}

        class Locked(pathlib.PosixPath):
            """A path whose listing refuses the locked directories, as an unreadable directory refuses it."""

            def iterdir(self) -> Iterator[pathlib.PosixPath]:
                if self in locked:
                    raise PermissionError(13, "Permission denied", str(self))
                return super().iterdir()

        real = SWEEP.Path
        SWEEP.Path = Locked
        try:
            result = at_now(root)
        finally:
            SWEEP.Path = real
        return (expect(removed(result) == [root / "work" / "pyc-1"], Prose(f"removed {removed(result)}"))
                + expect((root / "locked-parent").exists(), Prose("the unreadable directory's parent went")))


def unstatable_file_keeps_its_directory() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        hidden = put(root / "pyc.a" / "n.pyc", OLD)
        put(root / "pyc.b" / "m.pyc", OLD)
        age_dirs(root, OLD)

        class Hidden(pathlib.PosixPath):
            """A path whose lstat refuses one file, as a directory without search permission refuses it."""

            def lstat(self) -> os.stat_result:
                if self == hidden:
                    raise PermissionError(13, "Permission denied", str(self))
                return super().lstat()

        real = SWEEP.Path
        SWEEP.Path = Hidden
        try:
            result = at_now(root)
        finally:
            SWEEP.Path = real
        return expect(removed(result) == [root / "pyc.b"] and hidden.exists(), Prose(f"removed {removed(result)}"))


def failed_removal_exit_1() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        put(root / "pyc.b" / "n.pyc", OLD)
        age_dirs(root, OLD)
        real = SWEEP.shutil.rmtree

        def rmtree(path: Path) -> None:
            if Path(path).name == "pyc.a":
                raise OSError(16, "Device or resource busy", str(path))
            real(path)

        SWEEP.shutil.rmtree = rmtree
        try:
            result = at_now(root)
        finally:
            SWEEP.shutil.rmtree = real
        size = size_of(result, root / "pyc.b")
        return (expect(result.status == 1, Prose(f"exit {result.status}"))
                + expect(removed(result) == [root / "pyc.b"], Prose(f"removed {removed(result)}"))
                + expect(any(line.startswith(f"failed {root / 'pyc.a'}") for line in result.err),
                         Prose(f"err {result.err}"))
                + expect(result.out[-1:] == [Prose(f"swept 2 directories, {size} bytes")],
                         Prose(f"summary {result.out[-1:]}")))


def dry_run_removes_nothing() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        age_dirs(root, OLD)
        result = at_now(Argument("--dry-run"), root)
        size = size_of(result, root / "pyc.a")
        return (expect((root / "pyc.a" / "m.pyc").exists(), Prose("a dry run removed it"))
                + expect(result.out == [Prose(f"would remove {root / 'pyc.a'} {size}"),
                                        Prose(f"would sweep 1 directories, {size} bytes")],
                         Prose(f"out {result.out}"))
                + expect(size > 0, Prose(f"size {size}")))


def root_symlink_not_followed() -> list[Prose]:
    with tree() as root:
        put(root / "target" / "pyc.a" / "m.pyc", OLD)
        (root / "link").symlink_to(root / "target")
        age_dirs(root, OLD)
        result = at_now(root / "link")
        missing = at_now(root / "absent")
        return (expect(removed(result) == [] and (root / "target" / "pyc.a").exists(),
                       Prose(f"removed {removed(result)}"))
                + expect(any("ROOT" in line for line in result.err), Prose(f"err {result.err}"))
                + expect(missing.status == 0 and any("ROOT" in line for line in missing.err),
                         Prose(f"absent ROOT: exit {missing.status}, err {missing.err}")))


def age_boundary() -> list[Prose]:
    wrong: list[Prose] = []
    for age, gone in ((Seconds(7200), False), (Seconds(7201), True)):
        with tree() as root:
            put(root / "pyc.a" / "m.pyc", age)
            age_dirs(root, age)
            result = at_now(root)
            wrong += expect(bool(removed(result)) == gone, Prose(f"age {age}: removed {removed(result)}"))
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", Seconds(100))
        age_dirs(root, Seconds(100))
        result = at_now(Argument("--age"), Argument("50"), root)
        wrong += expect(bool(removed(result)), Prose(f"--age 50: removed {removed(result)}"))
    return wrong


def summary_counts() -> list[Prose]:
    with tree() as root:
        first = put(root / "pyc.a" / "m.pyc", OLD, Content(b"x" * 5000))
        second = put(root / "pyc.b" / "n.pyc", OLD, Content(b"y" * 9000))
        age_dirs(root, OLD)
        wanted = (blocks([first]), blocks([second]))
        result = at_now(root)
        a, b = size_of(result, root / "pyc.a"), size_of(result, root / "pyc.b")
        return (expect(result.out[-1:] == [Prose(f"swept 2 directories, {a + b} bytes")], Prose(f"out {result.out}"))
                + expect((a, b) == wanted, Prose(f"sizes {a} {b}, not {wanted}")))


def usage_refusals() -> list[Prose]:
    wrong: list[Prose] = []
    for argv in ([], ["relative"], ["--age"], ["/nonexistent-pyc-sweep-root", "--age"], ["--age", "-1", "/tmp"], ["--age", "nan", "/tmp"],
                 ["--age", "inf", "/tmp"], ["--now", "x", "/tmp"], ["--bogus", "/tmp"]):
        result = run([Argument(word) for word in argv])
        wrong += expect(result.status == 2 and result.err == [SWEEP.USAGE] and not result.out,
                        Prose(f"{argv}: exit {result.status}, out {result.out}, err {result.err}"))
    return wrong


def by_path_through_shebang() -> list[Prose]:
    with tree() as root:
        put(root / "pyc.a" / "m.pyc", OLD)
        age_dirs(root, OLD)
        done = subprocess.run([str(SWEEPER), "--now", str(NOW), str(root)], capture_output=True, text=True,
                              check=False)
        lines = done.stdout.splitlines()
        return (expect(done.returncode == 0, Prose(f"exit {done.returncode}: {done.stderr}"))
                + expect(not (root / "pyc.a").exists(), Prose("not removed by path"))
                + expect(bool(lines) and lines[-1].startswith("swept 1 directories, "), Prose(f"out {lines}")))


def shebang_is_the_kits() -> list[Prose]:
    with SWEEPER.open(encoding="utf-8") as handle:
        first = handle.readline()
    return (expect(first == KIT_LINE, Prose(f"shebang {first!r}"))
            + expect(os.access(SWEEPER, os.X_OK), Prose("not executable")))


GONE = Pid(4242)  # no process of the stand-in /proc has it
REUSED = Pid(4343)  # the stand-in /proc gives it another start time than its record's
LIVE = Pid(4545)
STARTED = StartTime("8131")


@contextlib.contextmanager
def stand_in_proc(base: Path, processes: dict[Pid, Content]) -> Iterator[None]:
    """Point the sweeper's /proc at a stand-in under BASE where each of PROCESSES has the stat file given."""
    proc = base / "proc"
    proc.mkdir()
    for pid, text in processes.items():
        put(proc / str(pid) / "stat", Seconds(0), text)
    real = getattr(SWEEP, "PROC", None)
    SWEEP.PROC = proc
    try:
        yield
    finally:
        SWEEP.PROC = real


def stat_line(pid: Pid, started: StartTime) -> Content:
    """Return a /proc stat line for PID with STARTED as field 22, its command holding a ")" and a space, and
    every other field unlike any start time."""
    fields = " ".join(str(100 + k) for k in range(19))
    return Content(f"{pid} (a) b) {fields} {started} 0 0\n".encode())


def call(parent: Path, pid: Pid, name: FolderName | None = None, record: Content | None = None) -> Path:
    """Make a call's TMPDIR under PARENT as claude-call makes it, its owner record naming PID started at STARTED
    (RECORD replaces the record's text), and a note in it, each file written OLD seconds before NOW."""
    folder = parent / (name or f"cc-{pid}-0123abcd")
    folder.mkdir(mode=0o700, parents=True)
    put(folder / "owner", OLD, record if record is not None else Content(f"{pid} {STARTED} cc-{pid}.scope\n".encode()))
    put(folder / "notes.txt", OLD, Content(b"notes"))
    return folder


def age_all(path: Path, age: Seconds) -> None:
    """Give PATH and everything under it the time AGE seconds before NOW, a link its own time."""
    for entry in [path, *path.rglob("*")]:
        os.utime(entry, (NOW - age, NOW - age), follow_symlinks=False)


def ended_call_removed_whole() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        folder = call(root, GONE)
        put(folder / "big.bin", OLD, Content(b"b" * 9000))
        put(folder / "pkg" / "m.py", OLD, Content(b"x = 1\n"))
        put(folder / "pkg" / "__pycache__" / "m.cpython-314.pyc", OLD)
        put(folder / "deep" / "a" / "b" / "data.bin", OLD, Content(b"d" * 5000))
        (folder / "link").symlink_to(folder / "notes.txt")
        os.mkfifo(folder / "pipe")
        age_dirs(base, OLD)
        age_all(folder, OLD)
        want = Bytes(sum(entry.lstat().st_blocks * 512 for entry in folder.rglob("*")))
        with stand_in_proc(base, {}):
            result = at_now(root)
        return (expect(removed(result) == [folder], Prose(f"removed {removed(result)}"))
                + expect(not folder.exists(), Prose("the folder is still there"))
                + expect(size_of(result, folder) == want, Prose(f"size {size_of(result, folder)}, not {want}"))
                + expect(result.status == 0 and not result.err, Prose(f"exit {result.status}, err {result.err}")))


def reused_pid_is_an_ended_call() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        reused = call(root, REUSED)
        no_start = call(root, LIVE)
        age_dirs(base, OLD)
        with stand_in_proc(base, {REUSED: stat_line(REUSED, StartTime("8132")), LIVE: Content(b"4545 no command\n")}):
            result = at_now(root)
        got = sorted(removed(result))
        return (expect(got == sorted([reused, no_start]), Prose(f"removed {got}"))
                + expect(result.status == 0, Prose(f"exit {result.status}")))


def ended_young_call_kept() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        young_file = call(root, GONE, FolderName("cc-4242-0000000a"))
        put(young_file / "deep" / "log.txt", OLD, Content(b"log"))
        young_link = call(root, GONE, FolderName("cc-4242-0000000b"))
        (young_link / "link").symlink_to(young_link / "notes.txt")
        young_dir = call(root, GONE, FolderName("cc-4242-0000000c"))
        at_age = call(root, GONE, FolderName("cc-4242-0000000d"))
        past_age = call(root, GONE, FolderName("cc-4242-0000000e"))
        age_dirs(base, OLD)
        for folder in (young_file, young_link, young_dir):
            age_all(folder, OLD)
        os.utime(young_file / "deep" / "log.txt", (NOW - YOUNG, NOW - YOUNG))
        os.utime(young_link / "link", (NOW - YOUNG, NOW - YOUNG), follow_symlinks=False)
        os.utime(young_dir, (NOW - YOUNG, NOW - YOUNG))
        age_all(at_age, Seconds(7200))
        age_all(past_age, Seconds(7201))
        with stand_in_proc(base, {}):
            result = at_now(root)
        return expect(removed(result) == [past_age], Prose(f"removed {removed(result)}"))


def live_call_kept() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        live = call(root, LIVE)
        put(live / "pyc.x" / "m.pyc", OLD)
        ended = call(root, GONE)
        age_dirs(base, OLD)
        with stand_in_proc(base, {LIVE: stat_line(LIVE, STARTED)}):
            result = at_now(root)
        got = sorted(removed(result))
        return (expect(got == sorted([ended, live / "pyc.x"]), Prose(f"removed {got}"))
                + expect((live / "notes.txt").exists(), Prose("the live call's note went")))


def unowned_call_kept() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        kept = [call(root, GONE, FolderName(f"cc-4242-0000000{k}"), Content(text))
                for k, text in enumerate((b"", b"4242 8131\n", b"4242 8131 cc-4242.scope extra\n",
                                          b"pid 8131 cc-4242.scope\n", b"\xff\xfe 8131 cc-4242.scope\n"))]
        missing = call(root, GONE, FolderName("cc-4242-0000000a"))
        (missing / "owner").unlink()
        a_directory = call(root, GONE, FolderName("cc-4242-0000000b"))
        (a_directory / "owner").unlink()
        put(a_directory / "owner" / "x", OLD, Content(b"4242 8131 cc-4242.scope\n"))
        ended = call(root, GONE)
        age_dirs(base, OLD)
        with stand_in_proc(base, {}):
            result = at_now(root)
        return (expect(removed(result) == [ended], Prose(f"removed {removed(result)}"))
                + expect(all(folder.exists() for folder in [*kept, missing, a_directory]), Prose("a kept folder went")))


def nested_call_removed() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        put(root / "work" / "plants.txt", OLD, Content(b"plants"))
        first = call(root / "work", GONE)
        second = call(root / "a" / "b", REUSED)
        age_dirs(base, OLD)
        with stand_in_proc(base, {REUSED: stat_line(REUSED, StartTime("8132"))}):
            result = at_now(root)
        got = sorted(removed(result))
        return (expect(got == sorted([first, second]), Prose(f"removed {got}"))
                + expect((root / "work" / "plants.txt").exists(), Prose("the work file went")))


def not_a_call_folder_kept() -> list[Prose]:
    names = ("cc-4242", "cc-4242-0123abc", "cc-4242-0123abcd0", "cc-4242-0123ABCD", "cc-4242-0123abcg",
             "cc-x-0123abcd", "cc--0123abcd", "xcc-4242-0123abcd")
    with tree() as base:
        root = base / "tmp"
        kept = [call(root, GONE, FolderName(name)) for name in names]
        ended = call(root, GONE)
        age_dirs(base, OLD)
        with stand_in_proc(base, {}):
            result = at_now(root)
        wrong = (expect(removed(result) == [ended], Prose(f"removed {removed(result)}"))
                 + expect(all(folder.exists() for folder in kept), Prose("a misnamed folder went")))
    with tree() as base:
        theirs = call(base / "tmp", GONE)
        age_dirs(base, OLD)
        real = SWEEP.ME
        SWEEP.ME = real + 1
        try:
            with stand_in_proc(base, {}):
                not_ours = at_now(base / "tmp")
        finally:
            SWEEP.ME = real
        return wrong + expect(removed(not_ours) == [] and theirs.exists(),
                              Prose(f"not ours: removed {removed(not_ours)}"))


def unreadable_call_kept() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        inner = call(root, GONE, FolderName("cc-4242-0000000a"))
        put(inner / "locked" / "m.txt", OLD, Content(b"m"))
        itself = call(root, GONE, FolderName("cc-4242-0000000b"))
        unstatable = call(root, GONE, FolderName("cc-4242-0000000c"))
        hidden = put(unstatable / "hidden.txt", OLD, Content(b"h"))
        ended = call(root, GONE)
        age_dirs(base, OLD)
        locked = {inner / "locked", itself}

        class Unreadable(pathlib.PosixPath):
            """A path whose listing refuses the locked directories and whose lstat refuses the hidden file."""

            def iterdir(self) -> Iterator[pathlib.PosixPath]:
                if self in locked:
                    raise PermissionError(13, "Permission denied", str(self))
                return super().iterdir()

            def lstat(self) -> os.stat_result:
                if self == hidden:
                    raise PermissionError(13, "Permission denied", str(self))
                return super().lstat()

        real = SWEEP.Path
        SWEEP.Path = Unreadable
        try:
            with stand_in_proc(base, {}):
                result = at_now(root)
        finally:
            SWEEP.Path = real
        return (expect(removed(result) == [ended], Prose(f"removed {removed(result)}"))
                + expect(inner.exists() and itself.exists() and unstatable.exists(), Prose("an unread folder went")))


def unwritable_call_removed() -> list[Prose]:
    with tree() as base:
        root = base / "tmp"
        folder = call(root, GONE)
        put(folder / "mod" / "pkg" / "go.mod", OLD, Content(b"module m\n"))
        age_dirs(base, OLD)
        for directory in (folder / "mod" / "pkg", folder / "mod"):
            directory.chmod(0o500)
        real = SWEEP.shutil.rmtree

        def rmtree(path: Path) -> None:
            """Refuse while a directory under PATH lacks its owner's write bit, as its owner's rmtree is refused."""
            for entry in [Path(path), *Path(path).rglob("*")]:
                if entry.is_dir() and not entry.is_symlink() and not entry.stat().st_mode & stat.S_IWUSR:
                    raise PermissionError(13, "Permission denied", str(entry))
            real(path)

        SWEEP.shutil.rmtree = rmtree
        try:
            with stand_in_proc(base, {}):
                result = at_now(root)
        finally:
            SWEEP.shutil.rmtree = real
        return (expect(removed(result) == [folder] and not folder.exists(), Prose(f"removed {removed(result)}"))
                + expect(result.status == 0 and not result.err, Prose(f"exit {result.status}, err {result.err}")))


class Case(NamedTuple):
    id: CaseId
    what: Prose
    check: Callable[[], list[Prose]]


CASES = [
    Case(CaseId("P01"), Prose("an old bytecode-only directory is removed"), old_pyc_dir_removed),
    Case(CaseId("P02"), Prose("a file written within the age keeps its directory"), young_file_kept),
    Case(CaseId("P03"), Prose("a directory changed within the age is kept"), young_directory_kept),
    Case(CaseId("P04"), Prose("sources stay, their bytecode cache goes"), sources_kept_their_cache_removed),
    Case(CaseId("P05"), Prose("a symbolic link keeps its directory"), symlink_kept),
    Case(CaseId("P06"), Prose("a link named .pyc is a link, not bytecode"), pyc_named_symlink_kept),
    Case(CaseId("P07"), Prose("a pipe keeps its directory"), fifo_kept),
    Case(CaseId("P08"), Prose("any other file keeps its directory"), other_file_kept),
    Case(CaseId("P09"), Prose("an empty directory and one of empty directories are kept"), empty_kept),
    Case(CaseId("P10"), Prose("a ROOT is never removed"), root_never_removed),
    Case(CaseId("P11"), Prose("a removable tree goes whole, one line"), largest_subtree_once),
    Case(CaseId("P12"), Prose("caches nested in a work directory go, the work stays"), nested_under_work_dir),
    Case(CaseId("P13"), Prose("an unreadable directory is kept with its parent, a sibling still goes"),
         unreadable_kept_sibling_swept),
    Case(CaseId("P14"), Prose("a failed removal is told and exits 1, the others still go"), failed_removal_exit_1),
    Case(CaseId("P15"), Prose("a dry run removes nothing and says what it would"), dry_run_removes_nothing),
    Case(CaseId("P16"), Prose("a ROOT that is a link is not followed, an absent one is told"),
         root_symlink_not_followed),
    Case(CaseId("P17"), Prose("the age is strict and --age sets it"), age_boundary),
    Case(CaseId("P18"), Prose("the summary counts the directories and their bytes"), summary_counts),
    Case(CaseId("P19"), Prose("every command line it does not take exits 2 with the usage"), usage_refusals),
    Case(CaseId("P20"), Prose("run by its path through its shebang"), by_path_through_shebang),
    Case(CaseId("P21"), Prose("its shebang is the kit's interpreter line"), shebang_is_the_kits),
    Case(CaseId("P22"), Prose("bytecode not ours is kept"), not_ours_kept),
    Case(CaseId("P23"), Prose("another file deep inside keeps every directory above it"),
         deep_other_file_keeps_every_parent),
    Case(CaseId("P24"), Prose("a file the sweep cannot stat keeps its directory"),
         unstatable_file_keeps_its_directory),
    Case(CaseId("P25"), Prose("an ended call's old folder goes whole, whatever it holds"), ended_call_removed_whole),
    Case(CaseId("P26"), Prose("a pid another process took, or whose stat names no start time, is an ended call"),
         reused_pid_is_an_ended_call),
    Case(CaseId("P27"), Prose("an ended call's folder changed within the age is kept"), ended_young_call_kept),
    Case(CaseId("P28"), Prose("a live call's old folder is kept, the bytecode in it still goes"), live_call_kept),
    Case(CaseId("P29"), Prose("a folder with no owner record, or a garbled one, is kept"), unowned_call_kept),
    Case(CaseId("P30"), Prose("an ended call's folder nested in a directory goes, the directory stays"),
         nested_call_removed),
    Case(CaseId("P31"), Prose("a folder not ours, or not named as claude-call names one, is kept"),
         not_a_call_folder_kept),
    Case(CaseId("P32"), Prose("a folder part of which the sweep cannot read is kept"), unreadable_call_kept),
    Case(CaseId("P33"), Prose("a directory the call left unwritable goes with its folder"),
         unwritable_call_removed),
]


def main() -> ExitStatus:
    bad = 0
    for case in CASES:
        try:
            wrong = case.check()
        except Exception as exc:  # noqa: BLE001 - a case that raises disagrees; the others still run
            wrong = [Prose(f"raised {type(exc).__name__}: {exc}")]
        print(f"{'MISS' if wrong else 'ok  '} {case.id:<4} {case.what}")
        for line in wrong:
            print(f"     {line}")
        bad += bool(wrong)
    print(f"{len(CASES)} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
