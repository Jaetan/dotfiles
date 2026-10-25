#!/usr/bin/env python3
"""Regression cases for log-stall-watch, the watcher background-run-watch-guard.py demands.

Most cases hand the watcher's verdict a reading built by hand, the way its loop hands it one read of the
log, with the time given: what it prints, the state it leaves and whether the run ended. The loop's own
cases run main() in this process with a stand-in for the time module, whose time is the case's and
whose sleep records its argument, runs the case's act between reads and ends the loop at the given
call: nothing sleeps and no clock is read. One case runs the watcher by its path, through its own
shebang, as the Monitor runs it; another checks that the shebang is the kit guards' interpreter line.

Usage: test_log_stall_watch.py [WATCHER]
  WATCHER  the watcher to test, or a directory holding it (default: ~/dotfiles/.local/bin)
Exit 0 when every case agrees with its expectation.
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import NamedTuple, NewType

# Prose a case names itself by or a line the watcher prints; a case's name; seconds of a clock or slept;
# a file's size; a count of sleeps; a command-line argument; the exit status.
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
Seconds = NewType("Seconds", float)
Bytes = NewType("Bytes", int)
Count = NewType("Count", int)
Argument = NewType("Argument", str)
ExitStatus = NewType("ExitStatus", int)

GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "dotfiles" / ".local" / "bin").resolve()
WATCHER = GIVEN / "log-stall-watch" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="log-stall-watch-test-")).resolve()
KIT_GUARD = Path(__file__).resolve().parent.parent / "workflow-watch-guard.py"  # the kit's interpreter line
NOW = Seconds(1_000_000.0)


def load() -> ModuleType:
    sys.dont_write_bytecode = True  # no __pycache__ beside the watcher, which stow would link into ~/.local/bin
    loader = importlib.machinery.SourceFileLoader("log_stall_watch", str(WATCHER))
    spec = importlib.util.spec_from_loader("log_stall_watch", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


WATCH = load()
Reading, Watched, Seen = WATCH.Reading, WATCH.Watch, WATCH.Seen  # the watcher's own shapes


class Stopped(Exception):
    """Raised by the stand-in sleep to end main()'s loop."""


type Act = Callable[[], None]


class Clock:
    """A stand-in for the time module: time is the case's; sleep records its argument, runs the act its
    turn names, and ends the loop at the given call."""

    def __init__(self, now: Seconds, stop_at: Count, acts: dict[Count, Act]) -> None:
        self.now = now
        self.slept: list[Seconds] = []
        self.stop_at = stop_at
        self.acts = acts

    def time(self) -> Seconds:
        return self.now

    def sleep(self, seconds: Seconds) -> None:
        self.slept.append(seconds)
        if len(self.slept) >= self.stop_at:
            raise Stopped
        act = self.acts.get(Count(len(self.slept)))
        if act is not None:
            act()


class Ran(NamedTuple):
    """main() run in process: what it returned (None when the loop was ended at a sleep), printed and slept."""

    status: ExitStatus | None
    out: Prose
    err: Prose
    slept: list[Seconds]


def run_main(arguments: list[Argument], stop_at: Count = Count(1), acts: dict[Count, Act] | None = None,
             now: Seconds = NOW) -> Ran:
    clock = Clock(now, stop_at, acts or {})
    out, err = io.StringIO(), io.StringIO()
    saved = sys.argv, WATCH.time
    sys.argv, WATCH.time = [str(WATCHER), *arguments], clock
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = WATCH.main()
    except Stopped:
        status = None
    finally:
        sys.argv, WATCH.time = saved
    return Ran(status, Prose(out.getvalue()), Prose(err.getvalue()), clock.slept)


def log_file(name: CaseId, text: Prose = Prose(""), mtime: Seconds | None = None) -> Path:
    path = BASE / f"{name}.log"
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def reading(size: Bytes = Bytes(10), mtime: Seconds = NOW, last: tuple[Prose, ...] = (Prose("working"),),
            exists: bool = True) -> Reading:
    return Reading(exists, size, mtime, last)


def watch(silence: Seconds = Seconds(900.0)) -> Watched:
    return Watched(Path("/logs/run.log"), silence, Seconds(30.0))


def fresh_seen() -> Seen:
    return Seen(Bytes(-1), Seconds(-1.0), told=False, waited=False)


# Verdicts on readings built by hand ---------------------------------------------

def stall_past_silence() -> list[Prose]:
    _, lines, ended = WATCH.verdict(watch(), fresh_seen(), reading(mtime=Seconds(NOW - 901)), NOW)
    wrong = [] if not ended else [Prose("a silent log read as ended")]
    if lines != ["STALL: /logs/run.log has not grown for 901s, past 900s, and holds no exit line"]:
        wrong.append(Prose(f"901 s of silence past 900 printed {lines!r}"))
    return wrong


def stall_at_silence() -> list[Prose]:
    _, lines, _ = WATCH.verdict(watch(), fresh_seen(), reading(mtime=Seconds(NOW - 900)), NOW)
    return [] if len(lines) == 1 and lines[0].startswith("STALL") else [Prose(f"900 s of 900 printed {lines!r}")]


def quiet_below_silence() -> list[Prose]:
    _, lines, ended = WATCH.verdict(watch(), fresh_seen(), reading(mtime=Seconds(NOW - 899)), NOW)
    return [] if not lines and not ended else [Prose(f"899 s of 900 printed {lines!r}, ended {ended}")]


def stall_told_once() -> list[Prose]:
    silent = reading(mtime=Seconds(NOW - 1000))
    seen, first, _ = WATCH.verdict(watch(), fresh_seen(), silent, NOW)
    _, second, _ = WATCH.verdict(watch(), seen, silent, Seconds(NOW + 30))
    return [] if len(first) == 1 and not second else [Prose(f"two silent reads printed {first!r} then {second!r}")]


def growth_rearms() -> list[Prose]:
    seen, first, _ = WATCH.verdict(watch(), fresh_seen(), reading(mtime=Seconds(NOW - 1000)), NOW)
    seen, grown, _ = WATCH.verdict(watch(), seen, reading(size=Bytes(20), mtime=NOW), NOW)
    later = Seconds(NOW + 900)
    _, again, _ = WATCH.verdict(watch(), seen, reading(size=Bytes(20), mtime=NOW), later)
    if len(first) == 1 and not grown and len(again) == 1 and again[0].startswith("STALL"):
        return []
    return [Prose(f"silent, grown, silent again printed {first!r}, {grown!r}, {again!r}")]


def size_alone_rearms() -> list[Prose]:
    """A size change with the time left old re-arms the STALL, which the old time then tells again."""
    old = Seconds(NOW - 1000)
    seen, _, _ = WATCH.verdict(watch(), fresh_seen(), reading(mtime=old), NOW)
    _, again, _ = WATCH.verdict(watch(), seen, reading(size=Bytes(11), mtime=old), NOW)
    return [] if len(again) == 1 and again[0].startswith("STALL") else [
        Prose(f"a size change with the time left old printed {again!r}")]


def time_alone_is_growth() -> list[Prose]:
    """A log rewritten to the same size, its time new, grew: no STALL until it falls silent again."""
    seen, _, _ = WATCH.verdict(watch(), fresh_seen(), reading(mtime=Seconds(NOW - 1000)), NOW)
    seen, now_lines, _ = WATCH.verdict(watch(), seen, reading(mtime=NOW), NOW)
    return [] if not now_lines and not seen.told else [
        Prose(f"a new time at the same size printed {now_lines!r}, told {seen.told}")]


def ended_on_exit_line() -> list[Prose]:
    last = (Prose("a"), Prose("b"), Prose("EXIT=0"))
    _, lines, ended = WATCH.verdict(watch(), fresh_seen(), reading(last=last, mtime=Seconds(NOW - 5000)), NOW)
    want = ["ENDED: /logs/run.log ends in its exit line; its last lines:", "a", "b", "EXIT=0"]
    return [] if ended and lines == want else [Prose(f"an exit line printed {lines!r}, ended {ended}")]


def exit_line_not_last() -> list[Prose]:
    last = (Prose("EXIT=0"), Prose("more output"))
    _, lines, ended = WATCH.verdict(watch(), fresh_seen(), reading(last=last), NOW)
    return [] if not ended and not lines else [Prose(f"an exit line before more output ended it: {lines!r}")]


def empty_log_not_ended() -> list[Prose]:
    _, lines, ended = WATCH.verdict(watch(), fresh_seen(), reading(size=Bytes(0), last=()), NOW)
    return [] if not ended and not lines else [Prose(f"an empty log printed {lines!r}, ended {ended}")]


def waiting_told_once() -> list[Prose]:
    absent = reading(exists=False)
    seen, first, ended = WATCH.verdict(watch(), fresh_seen(), absent, NOW)
    _, second, _ = WATCH.verdict(watch(), seen, absent, NOW)
    want = ["WAITING: /logs/run.log does not exist yet"]
    return [] if first == want and not second and not ended else [Prose(f"absent twice printed {first!r}, {second!r}")]


# The command line ------------------------------------------------------------------

def refused(arguments: list[Argument], says: Prose) -> list[Prose]:
    ran = run_main(arguments)
    if ran.status == 2 and says in ran.err and "usage: log-stall-watch LOG SILENCE [SECONDS]" in ran.err \
            and not ran.out and not ran.slept:
        return []
    return [Prose(f"{arguments} gave {ran.status}, {ran.err!r}")]


def usage_refusals() -> list[Prose]:
    log = Argument(str(BASE / "u.log"))
    return [*refused([log], Prose("it takes LOG SILENCE [SECONDS]")),
            *refused([log, Argument("1"), Argument("2"), Argument("3")], Prose("it takes LOG SILENCE [SECONDS]")),
            *refused([Argument("u.log"), Argument("900")], Prose("is not an absolute path")),
            *refused([log, Argument("0")], Prose("SILENCE 0 is not a whole number from 1 to 1800")),
            *refused([log, Argument("1801")], Prose("SILENCE 1801")),
            *refused([log, Argument("9.5")], Prose("SILENCE 9.5")),
            *refused([log, Argument("-5")], Prose("SILENCE -5")),
            *refused([log, Argument("900"), Argument("0")], Prose("SECONDS 0 is not above 0 and at most 60")),
            *refused([log, Argument("900"), Argument("61")], Prose("SECONDS 61")),
            *refused([log, Argument("900"), Argument("x")], Prose("SECONDS x is not a number"))]


def bounds_accepted() -> list[Prose]:
    log = log_file(CaseId("bounds"), mtime=NOW)
    wrong = []
    for arguments in ([str(log), "1"], [str(log), "1800"], [str(log), "900", "60"], [str(log), "900", "0.5"]):
        ran = run_main([Argument(a) for a in arguments])
        if ran.status is not None or ran.err:
            wrong.append(Prose(f"{arguments} was refused: {ran.status}, {ran.err!r}"))
    return wrong


# The loop ------------------------------------------------------------------------

def loop_sleeps_the_interval() -> list[Prose]:
    log = log_file(CaseId("interval"), Prose("working\n"), mtime=NOW)
    default = run_main([Argument(str(log)), Argument("900")])
    given = run_main([Argument(str(log)), Argument("900"), Argument("5")])
    if default.slept == [30.0] and given.slept == [5.0] and not default.out and not given.out:
        return []
    return [Prose(f"slept {default.slept} by default and {given.slept} given 5; printed {default.out!r}")]


def loop_stalls_then_ends() -> list[Prose]:
    log = log_file(CaseId("loop"), Prose("working\n"), mtime=Seconds(NOW - 1000))

    def finish() -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write("EXIT=0\n")

    ran = run_main([Argument(str(log)), Argument("900")], stop_at=Count(5), acts={Count(1): finish})
    lines = ran.out.splitlines()
    if ran.status == 0 and ran.slept == [30.0] and lines[0].startswith("STALL") \
            and lines[1].startswith("ENDED") and lines[-1] == "EXIT=0":
        return []
    return [Prose(f"a silent log then its exit line gave {ran.status}, slept {ran.slept}, printed {lines!r}")]


def loop_ends_without_sleeping() -> list[Prose]:
    log = log_file(CaseId("done"), Prose("done\nEXIT=0\n"), mtime=NOW)
    ran = run_main([Argument(str(log)), Argument("900")])
    return [] if ran.status == 0 and not ran.slept else [
        Prose(f"a log already ending in its exit line gave {ran.status}, slept {ran.slept}")]


def loop_waits_for_the_log() -> list[Prose]:
    log = BASE / "later.log"

    def appear() -> None:
        log.write_text("EXIT=0\n", encoding="utf-8")

    ran = run_main([Argument(str(log)), Argument("900")], stop_at=Count(5), acts={Count(2): appear})
    lines = ran.out.splitlines()
    if ran.status == 0 and len(ran.slept) == 2 and lines[0] == f"WAITING: {log} does not exist yet" \
            and lines.count(lines[0]) == 1 and lines[1].startswith("ENDED"):
        return []
    return [Prose(f"a log appearing later gave {ran.status}, slept {ran.slept}, printed {lines!r}")]


def last_lines_read_from_the_end() -> list[Prose]:
    log = log_file(CaseId("long"), Prose("".join(f"line {n}\n" for n in range(20000)) + "EXIT=0\n"))
    last = WATCH.last_lines(log, Bytes(log.stat().st_size))
    return [] if last == ("line 19998", "line 19999", "EXIT=0") else [Prose(f"a long log's last lines read {last!r}")]


# By its path ---------------------------------------------------------------------

def by_path_through_shebang() -> list[Prose]:
    """A relative log is refused before any read: one that ends in its exit line would end the watch at once,
    so a watcher that let it through answers 0 here rather than looping."""
    (BASE / "relative.log").write_text("EXIT=0\n", encoding="utf-8")
    out = subprocess.run([str(WATCHER), "relative.log", "900"], capture_output=True, text=True, check=False, cwd=BASE)
    if out.returncode == 2 and "LOG relative.log is not an absolute path" in out.stderr and not out.stdout:
        return []
    return [Prose(f"run by its path it gave {out.returncode}, {out.stderr!r}")]


def shebang_is_the_kits() -> list[Prose]:
    mine = WATCHER.read_text(encoding="utf-8").splitlines()[0]
    kits = KIT_GUARD.read_text(encoding="utf-8").splitlines()[0]
    interpreter = Path(mine.removeprefix("#!"))
    if mine == kits and os.access(interpreter, os.X_OK):
        return []
    return [Prose(f"the shebang {mine!r} is not the kit's {kits!r} or names no executable")]


class Case(NamedTuple):
    id: CaseId
    what: Prose
    check: Callable[[], list[Prose]]


CASES = [
    Case(CaseId("L01"), Prose("a log silent past SILENCE prints STALL, saying how long"), stall_past_silence),
    Case(CaseId("L02"), Prose("silent exactly SILENCE prints STALL"), stall_at_silence),
    Case(CaseId("L03"), Prose("silent below SILENCE prints nothing"), quiet_below_silence),
    Case(CaseId("L04"), Prose("one silence is told once"), stall_told_once),
    Case(CaseId("L05"), Prose("the log growing re-arms the STALL"), growth_rearms),
    Case(CaseId("L06"), Prose("a size change alone re-arms the STALL"), size_alone_rearms),
    Case(CaseId("L06b"), Prose("a time change alone is growth"), time_alone_is_growth),
    Case(CaseId("L07"), Prose("a last line starting EXIT= ends it, printing the last lines"), ended_on_exit_line),
    Case(CaseId("L08"), Prose("an exit line followed by more output does not end it"), exit_line_not_last),
    Case(CaseId("L09"), Prose("an empty log is not ended"), empty_log_not_ended),
    Case(CaseId("L10"), Prose("an absent log is told once"), waiting_told_once),
    Case(CaseId("L11"), Prose("every out-of-bounds argument is refused at exit 2 with the usage"), usage_refusals),
    Case(CaseId("L12"), Prose("the bounds themselves are accepted"), bounds_accepted),
    Case(CaseId("L13"), Prose("the loop sleeps the interval, 30 s unless given"), loop_sleeps_the_interval),
    Case(CaseId("L14"), Prose("the loop says STALL, then ENDED once the exit line lands"), loop_stalls_then_ends),
    Case(CaseId("L15"), Prose("a log already ending in its exit line ends the loop before any sleep"),
         loop_ends_without_sleeping),
    Case(CaseId("L16"), Prose("the loop waits for a log that does not exist yet"), loop_waits_for_the_log),
    Case(CaseId("L17"), Prose("the last lines come from the log's end"), last_lines_read_from_the_end),
    Case(CaseId("L19"), Prose("run by its path through its shebang"), by_path_through_shebang),
    Case(CaseId("L20"), Prose("its shebang is the kit's interpreter line"), shebang_is_the_kits),
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
