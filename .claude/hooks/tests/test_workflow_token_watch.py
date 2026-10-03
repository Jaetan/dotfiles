#!/usr/bin/env python3
"""Regression cases for workflow-token-watch, the watcher workflow-watch-guard.py demands.

Most cases write agent transcripts into a fresh directory, as a workflow's agents leave
them, and read them with the watcher's Tally, the way its loop does. The loop's own
cases run main() in this process with a stand-in for the time module whose sleep
records its argument and ends the loop, a Tally that refuses a second read with no sleep
between, and stdout recorded: nothing sleeps, no clock is read, and a loop that never
sleeps ends at its second read. One case runs the watcher by its path, through its own
shebang, as the Monitor runs it; another checks that the shebang is the kit guards'
interpreter line and that the interpreter exists.

Usage: test_workflow_token_watch.py [WATCHER]
  WATCHER  the watcher to test, or a directory holding it (default: ~/dotfiles/.local/bin)
Exit 0 when every case agrees with its expectation.
"""

import contextlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Literal, NamedTuple, NewType, TypedDict

# A token count; a message id; raw transcript bytes; prose a case names itself by or a
# line the watcher prints; a case's name; seconds slept; how many sleeps a loop is let
# take; a command-line argument; the exit status.
Tokens = NewType("Tokens", int)
MessageId = NewType("MessageId", str)
RawText = NewType("RawText", bytes)
Prose = NewType("Prose", str)
CaseId = NewType("CaseId", str)
Seconds = NewType("Seconds", float)
Count = NewType("Count", int)
Argument = NewType("Argument", str)
ExitStatus = NewType("ExitStatus", int)

GIVEN = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "dotfiles" / ".local" / "bin").resolve()
WATCHER = GIVEN / "workflow-token-watch" if GIVEN.is_dir() else GIVEN
BASE = Path(tempfile.mkdtemp(prefix="workflow-token-watch-test-")).resolve()
KIT_GUARD = Path(__file__).resolve().parent.parent / "workflow-watch-guard.py"  # the kit's interpreter line


class Part(TypedDict, total=False):
    type: Literal["message", "advisor_message"]
    input_tokens: Tokens
    cache_creation_input_tokens: Tokens
    cache_read_input_tokens: Tokens
    output_tokens: Tokens


class Usage(TypedDict, total=False):
    input_tokens: Tokens | Prose | None
    cache_creation_input_tokens: Tokens
    cache_read_input_tokens: Tokens
    output_tokens: Tokens | bool
    iterations: list[Part]


class Message(TypedDict, total=False):
    id: MessageId
    role: Literal["assistant", "user"]
    usage: Usage


class Entry(TypedDict):
    type: Literal["assistant", "user"]
    message: Message


class Stopped(Exception):
    """Raised by the stand-in sleep to end main()'s loop."""


class Clock:
    """A stand-in for the time module: sleep records its argument, and ends the loop at the given call."""

    def __init__(self, stop_at: Count = Count(1)) -> None:
        self.slept: list[Seconds] = []
        self.stop_at = stop_at

    def sleep(self, seconds: Seconds) -> None:
        self.slept.append(seconds)
        if len(self.slept) >= self.stop_at:
            raise Stopped


class Recorder:
    """A stand-in for stdout that records each write and each flush, in order."""

    def __init__(self) -> None:
        self.events: list[Prose] = []

    def write(self, text: Prose) -> None:
        self.events.append(Prose("write:" + text))

    def flush(self) -> None:
        self.events.append(Prose("flush"))

    def text(self) -> Prose:
        return Prose("".join(event[6:] for event in self.events if event.startswith("write:")))


class Spun(Exception):
    """Raised by the bounded Tally when the loop reads again without sleeping."""


class Ran(NamedTuple):
    """main() run in process: what it returned (None when the loop reached sleep and was ended), printed and slept."""

    status: ExitStatus | None
    out: Recorder
    clock: Clock


def load() -> ModuleType:
    sys.dont_write_bytecode = True  # no __pycache__ beside the watcher, which stow would link into ~/.local/bin
    loader = importlib.machinery.SourceFileLoader("workflow_token_watch", str(WATCHER))
    spec = importlib.util.spec_from_loader("workflow_token_watch", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


WATCH = load()


def call(identity: MessageId, given: Tokens = Tokens(0), written: Tokens = Tokens(0), produced: Tokens = Tokens(0),
         reread: Tokens = Tokens(0)) -> Entry:
    return Entry(type="assistant", message=Message(id=identity, role="assistant", usage=Usage(
        input_tokens=given, cache_creation_input_tokens=written, cache_read_input_tokens=reread,
        output_tokens=produced)))


def write(path: Path, entries: list[Entry], tail: RawText = RawText(b"")) -> None:
    with path.open("ab") as handle:
        for entry in entries:
            handle.write(json.dumps(entry).encode() + b"\n")
        handle.write(tail)


def fresh(name: CaseId) -> Path:
    directory = BASE / name
    directory.mkdir(parents=True)
    return directory


def run_main(arguments: list[Argument], stop_at: Count = Count(1)) -> Ran | None:
    """main() in process; None when its loop read twice with no sleep between."""
    clock, out = Clock(stop_at), Recorder()
    real = WATCH.Tally

    class Bounded(real):
        def read(self, directory: Path) -> Tokens:
            if len(clock.slept) < getattr(self, "reads", 0):
                raise Spun
            self.reads = getattr(self, "reads", 0) + 1
            return super().read(directory)

    saved_argv, saved_time = sys.argv, WATCH.time
    sys.argv, WATCH.time, WATCH.Tally = [str(WATCHER), *arguments], clock, Bounded
    try:
        with contextlib.redirect_stdout(out):
            status = WATCH.main()
    except Stopped:
        status = None
    except Spun:
        return None
    finally:
        sys.argv, WATCH.time, WATCH.Tally = saved_argv, saved_time, real
    return Ran(status, out, clock)


# Counting ------------------------------------------------------------------------

def counts_new_tokens(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), Tokens(10), Tokens(200), Tokens(3000), Tokens(90000))])
    return WATCH.Tally().read(directory) == 3210


def advisor_counted(directory: Path) -> bool:
    entry = call(MessageId("m1"), Tokens(4), Tokens(3310), Tokens(2411), Tokens(191748))
    entry["message"]["usage"]["iterations"] = [
        Part(type="message", input_tokens=Tokens(2), output_tokens=Tokens(679),
             cache_creation_input_tokens=Tokens(1416), cache_read_input_tokens=Tokens(95166)),
        Part(type="advisor_message", input_tokens=Tokens(92451), output_tokens=Tokens(3074),
             cache_creation_input_tokens=Tokens(0), cache_read_input_tokens=Tokens(0)),
        Part(type="message", input_tokens=Tokens(2), output_tokens=Tokens(1732),
             cache_creation_input_tokens=Tokens(1894), cache_read_input_tokens=Tokens(96582))]
    write(directory / "agent-a.jsonl", [entry])
    return WATCH.Tally().read(directory) == 2 + 679 + 1416 + 92451 + 3074 + 2 + 1732 + 1894


def last_usage_once(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(5)),
                                        call(MessageId("m1"), produced=Tokens(50))])
    return WATCH.Tally().read(directory) == 50


def per_transcript(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(7))])
    write(directory / "agent-b.jsonl", [call(MessageId("m1"), produced=Tokens(11))])
    return WATCH.Tally().read(directory) == 18


def only_assistant_calls(directory: Path) -> bool:
    user = Entry(type="user", message=Message(id=MessageId("u1"), role="user", usage=Usage(input_tokens=Tokens(99))))
    nameless = Entry(type="assistant", message=Message(role="assistant", usage=Usage(output_tokens=Tokens(99))))
    write(directory / "agent-a.jsonl", [user, nameless, call(MessageId("m1"), produced=Tokens(1))],
          RawText(b'not json\n[1, 2]\n{"message": "text"}\n'
                  b'{"message": {"role": "assistant", "id": "m9", "usage": 5}}\n\x80not utf-8\n'
                  + b"[" * 100_000 + b"\n"))
    return WATCH.Tally().read(directory) == 1


def odd_counts_skipped(directory: Path) -> bool:
    odd = call(MessageId("m1"), produced=Tokens(7))
    odd["message"]["usage"]["input_tokens"] = Prose("12")
    absent = Entry(type="assistant", message=Message(id=MessageId("m2"), role="assistant", usage=Usage(
        input_tokens=None, output_tokens=Tokens(3))))
    flag = Entry(type="assistant", message=Message(id=MessageId("m3"), role="assistant", usage=Usage(
        output_tokens=True)))
    write(directory / "agent-a.jsonl", [odd, absent, flag],
          RawText(b'{"message": {"role": "assistant", "id": "m4", "usage": {"output_tokens": -500}}}\n'
                  b'{"message": {"role": "assistant", "id": "m5", "usage": {"output_tokens": {"a": 1}}}}\n'
                  b'{"message": {"role": "assistant", "id": "m6", "usage": {"iterations": [7, '
                  b'{"output_tokens": 4}]}}}\n'))
    return WATCH.Tally().read(directory) == 7 + 3 + 4


def empty_parts_read_the_top(directory: Path) -> bool:
    none = call(MessageId("m1"), produced=Tokens(5))
    none["message"]["usage"]["iterations"] = []
    odd = Entry(type="assistant", message=Message(id=MessageId("m2"), role="assistant", usage=Usage(
        output_tokens=Tokens(7))))
    write(directory / "agent-a.jsonl", [none, odd],
          RawText(b'{"message": {"role": "assistant", "id": "m3", "usage": {"output_tokens": 11, '
                  b'"iterations": "parts"}}}\n'))
    return WATCH.Tally().read(directory) == 5 + 7 + 11


def agent_files_only(directory: Path) -> bool:
    write(directory / "journal.jsonl", [call(MessageId("m1"), produced=Tokens(100))])
    write(directory / "agent-a.meta.json", [call(MessageId("m1"), produced=Tokens(100))])
    write(directory / "agent-a.jsonl", [call(MessageId("m2"), produced=Tokens(1))])
    return WATCH.Tally().read(directory) == 1


def unopenable_skipped(directory: Path) -> bool:
    (directory / "agent-0gone.jsonl").symlink_to(directory / "later.jsonl")
    (directory / "agent-0dir.jsonl").mkdir()
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(6))])
    tally = WATCH.Tally()
    first = tally.read(directory)
    write(directory / "later.jsonl", [call(MessageId("m2"), produced=Tokens(20))])
    return (first, tally.read(directory)) == (6, 26)


def partial_line_waits(directory: Path) -> bool:
    path = directory / "agent-a.jsonl"
    tally = WATCH.Tally()
    whole = json.dumps(call(MessageId("m1"), produced=Tokens(40))).encode()
    write(path, [call(MessageId("m0"), produced=Tokens(2))], RawText(whole[:20]))
    first = tally.read(directory)
    with path.open("ab") as handle:
        handle.write(whole[20:] + b"\n")
    second = tally.read(directory)
    third = tally.read(directory)
    return (first, second, third) == (2, 42, 42)


def incremental(directory: Path) -> bool:
    path = directory / "agent-a.jsonl"
    tally = WATCH.Tally()
    write(path, [call(MessageId("m1"), produced=Tokens(10))])
    first = tally.read(directory)
    write(path, [call(MessageId("m1"), produced=Tokens(30)), call(MessageId("m2"), produced=Tokens(5))])
    write(directory / "agent-b.jsonl", [call(MessageId("m1"), produced=Tokens(1))])
    return (first, tally.read(directory)) == (10, 36)


def never_rereads(directory: Path) -> bool:
    path = directory / "agent-a.jsonl"
    tally = WATCH.Tally()
    write(path, [call(MessageId("m1"), produced=Tokens(10))])
    first = tally.read(directory)
    idle = tally.read(directory)
    path.write_bytes(path.read_bytes().replace(b'"output_tokens": 10', b'"output_tokens": 90'))
    write(path, [call(MessageId("m2"), produced=Tokens(1))])
    return (first, idle, tally.read(directory)) == (10, 10, 11)


def replaced_shorter(directory: Path) -> bool:
    path = directory / "agent-a.jsonl"
    tally = WATCH.Tally()
    write(path, [call(MessageId(f"m{n}"), produced=Tokens(10)) for n in range(3)])
    first = tally.read(directory)
    path.write_bytes(b"")
    write(path, [call(MessageId("r1"), produced=Tokens(1000))])
    second = tally.read(directory)
    write(path, [call(MessageId(f"r{n}"), produced=Tokens(5)) for n in range(2, 5)])
    return (first, second, tally.read(directory)) == (30, 1030, 1045)


def quarters(directory: Path) -> bool:
    del directory
    tally = WATCH.Tally()
    limit = Tokens(100)
    lines = [tally.report(Tokens(total), limit) for total in (10, 24, 25, 30, 50, 74, 75, 100)]
    return lines == [None, None, "25 new tokens of 100", None, "50 new tokens of 100", None, "75 new tokens of 100",
                     "100 new tokens of 100"]


def over_limit(directory: Path) -> bool:
    del directory
    tally = WATCH.Tally()
    return (tally.report(Tokens(2_000_000), Tokens(2_000_000)) == "2,000,000 new tokens of 2,000,000"
            and tally.report(Tokens(2_000_001), Tokens(2_000_000))
            == "OVER LIMIT: 2,000,001 new tokens, ceiling 2,000,000; stop the workflow")


# The loop ------------------------------------------------------------------------

def at_the_ceiling_keeps_watching(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(100))])
    ran = run_main([Argument(str(directory)), Argument("100"), Argument("3")])
    return ran is not None and ran.status is None and ran.out.text() == "100 new tokens of 100\n" \
        and ran.clock.slept == [3.0]


def interval_defaults_to_5(directory: Path) -> bool:
    ran = run_main([Argument(str(directory)), Argument("100")])
    return ran is not None and ran.status is None and ran.clock.slept == [5.0]


def each_line_is_flushed(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(30))])
    ran = run_main([Argument(str(directory)), Argument("100"), Argument("1")])
    return ran is not None and ran.out.events == ["write:30 new tokens of 100", "write:\n", "flush"]


def missing_directory_said_once(directory: Path) -> bool:
    absent = directory / "not-yet"
    ran = run_main([Argument(str(absent)), Argument("100"), Argument("1")], stop_at=Count(2))
    return ran is not None and ran.status is None \
        and ran.out.text() == f"{absent} does not exist yet; reading it until it does\n" \
        and ran.clock.slept == [1.0, 1.0]


def over_limit_returns(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(101))])
    ran = run_main([Argument(str(directory)), Argument("100"), Argument("1")])
    return ran is not None and ran.status == 0 \
        and ran.out.text() == "OVER LIMIT: 101 new tokens, ceiling 100; stop the workflow\n" and ran.clock.slept == []


def limit_is_read(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(1_000_000)),
                                        call(MessageId("m2"), written=Tokens(1_000_001))])
    ran = run_main([Argument(str(directory)), Argument("2000000")])
    small = run_main([Argument(str(directory)), Argument("2000002")])
    return ran is not None and small is not None and ran.status == 0 and small.status is None \
        and ran.out.text() == "OVER LIMIT: 2,000,001 new tokens, ceiling 2,000,000; stop the workflow\n"


def default_ceiling(directory: Path) -> bool:
    write(directory / "agent-a.jsonl", [call(MessageId("m1"), produced=Tokens(2_000_001))])
    ran = run_main([Argument(str(directory))])
    return ran is not None and ran.status == 0 and "ceiling 2,000,000" in ran.out.text()


# The command line ----------------------------------------------------------------

def usage_without_arguments(directory: Path) -> bool:
    del directory
    done = subprocess.run([str(WATCHER)], capture_output=True, text=True, check=False)
    return done.returncode == 2 and "Usage: workflow-token-watch" in done.stderr


def runs_under_the_kits_python(directory: Path) -> bool:
    del directory
    first = WATCHER.read_text().splitlines()[0]
    kits = KIT_GUARD.read_text().splitlines()[0]
    interpreter = Path(first.removeprefix("#!"))
    return first == kits and interpreter.is_file() and os.access(interpreter, os.X_OK)


class Case(NamedTuple):
    id: CaseId
    what: Prose
    check: Callable[[Path], bool]


CASES = [
    Case(CaseId("T01"), Prose("input, cache writes and output count; cache reads do not"), counts_new_tokens),
    Case(CaseId("T02"), Prose("a call recorded twice counts once, at its last usage"), last_usage_once),
    Case(CaseId("T03"), Prose("one message id in two agents' transcripts is two calls"), per_transcript),
    Case(CaseId("T04"), Prose("only an assistant message with an id and a usage counts; a line that cannot be read, "
                              "however deep, is skipped"), only_assistant_calls),
    Case(CaseId("T05"), Prose("only agent-*.jsonl is read"), agent_files_only),
    Case(CaseId("T06"), Prose("a line still being written counts once it is whole, and once"), partial_line_waits),
    Case(CaseId("T07"), Prose("each read takes what the transcripts gained"), incremental),
    Case(CaseId("T08"), Prose("the total is printed at each new quarter of the ceiling, once"), quarters),
    Case(CaseId("T09"), Prose("OVER LIMIT past the ceiling, not at it"), over_limit),
    Case(CaseId("T11"), Prose("the ceiling defaults to 2,000,000"), default_ceiling),
    Case(CaseId("T12"), Prose("run by its path with no directory, it prints its usage and exits 2"),
         usage_without_arguments),
    Case(CaseId("T13"), Prose("a read, idle or not, does not go back over the lines it has taken"), never_rereads),
    Case(CaseId("T14"), Prose("a call that consulted the advisor counts every part, the advisor's included"),
         advisor_counted),
    Case(CaseId("T15"), Prose("a count that is not a whole number of 0 or more counts nothing"), odd_counts_skipped),
    Case(CaseId("T16"), Prose("an agent transcript that cannot be opened is skipped, the others read, and it is "
                              "read once it can be"), unopenable_skipped),
    Case(CaseId("T25"), Prose("a call whose parts are listed as none, or not as a list, counts its top level"),
         empty_parts_read_the_top),
    Case(CaseId("T17"), Prose("a transcript replaced by a shorter one is read again from its start"),
         replaced_shorter),
    Case(CaseId("T18"), Prose("the ceiling given is the ceiling applied, and past it the loop returns 0"),
         limit_is_read),
    Case(CaseId("T19"), Prose("at exactly the ceiling it keeps watching, sleeping the interval given"),
         at_the_ceiling_keeps_watching),
    Case(CaseId("T20"), Prose("the interval defaults to 5 seconds"), interval_defaults_to_5),
    Case(CaseId("T21"), Prose("each line is flushed as it is printed"), each_line_is_flushed),
    Case(CaseId("T22"), Prose("a directory that does not exist yet is said once, and read again"),
         missing_directory_said_once),
    Case(CaseId("T23"), Prose("past the ceiling the loop returns 0 without sleeping"), over_limit_returns),
    Case(CaseId("T24"), Prose("its shebang is the kit's guards' interpreter line, and that interpreter exists"),
         runs_under_the_kits_python),
]


def main() -> ExitStatus:
    bad = 0
    try:
        for case in CASES:
            try:
                ok = case.check(fresh(case.id))
            except Exception as exc:  # noqa: BLE001 - a case that raises disagrees; the others still run
                print(f"        {case.id}: {type(exc).__name__}: {exc}")
                ok = False
            bad += not ok
            print(f"{'ok  ' if ok else 'MISS'} {case.id:<4} {case.what}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"{len(CASES)} cases, {bad} disagree")
    return ExitStatus(1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
