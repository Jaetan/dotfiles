#!/home/nicolas/.local/bin/python3.14 -IS
"""The guard kit's one hook command: registered under every hook event, it reads the event on stdin and answers it.

Before reading, the process lowers its address space to CAP bytes, which the shell reader it runs inherits, and arms
a wall-time limit of WALL seconds; the event is read whole, at most MAX_EVENT bytes. core.answer judges it with the
rules under the kit's rules/ folder, the switches under ~/.config/claude-guard/off/ and the reader bin/shell-reader.
Exit 2 with the refusals on stderr, exit 0 with a JSON note on stdout (core.noted) or with nothing; a longer event,
a limit reached, an answer that cannot be written or any fault of the dispatcher is a refusal naming it, never a
pass. The options --rules=DIR, --wall=SECONDS and --cap=BYTES replace those defaults for the kit's tests; any other
argument is refused.

EXCEPTIONS, each with the failure it holds:
  * a fault of the dispatcher's own (a limit reached or any other fault while judging) is a note and no refusal where
    the bytes read are a JSON object naming Stop or SubagentStop with stop_hook_active true, read without the rules,
    the declarations or the state: exit 2 keeps a turn going and Claude Code fires that Stop again with
    stop_hook_active true, so a broken kit refusing each time loops the turn's end for ever.
"""

import os
import resource
import signal
import sys
from types import FrameType
from typing import NewType

KIT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, KIT)

try:
    from dispatcher.core import (Answer, EventBytes, ExitStatus, FolderPath, Settings, answer, environment, fault_text,
                                 kept_going, noted, payload_of)
    from dispatcher.model import ReadSettings
    from dispatcher.tree import ReaderPath, Seconds
    from dispatcher.verdict import KitFault, Prose
except BaseException as broken:  # noqa: BLE001 - a kit that cannot load refuses, never passes
    _ = sys.stderr.write(f"guard kit: the dispatcher cannot load: {type(broken).__name__}: {broken}. The call is "
                         "refused, since a kit that cannot judge a call passes nothing.\n")
    sys.exit(2)

Bytes = NewType("Bytes", int)
Option = NewType("Option", str)
SignalNumber = NewType("SignalNumber", int)

MAX_EVENT = Bytes(8 << 20)
CAP = Bytes(2 << 30)
WALL = Seconds(10.0)
READER_TIMEOUT = Seconds(5.0)
OPTIONS = frozenset({Option("--rules"), Option("--wall"), Option("--cap")})


class Overrun(BaseException):
    """The wall-time limit, raised past every handler a rule holds."""


def overran(_signal: SignalNumber, _frame: FrameType | None) -> None:
    """SIGALRM's handler: end the judging wherever it is."""
    raise Overrun


def given(argv: list[Option]) -> dict[Option, Prose]:
    """The test options named, each `--name=value`; a ValueError naming any other argument."""
    found: dict[Option, Prose] = {}
    for argument in argv:
        name, sign, value = argument.partition("=")
        if Option(name) not in OPTIONS or not sign:
            raise ValueError(f"the argument {argument!r} is not one of --rules=, --wall=, --cap=")
        found[Option(name)] = Prose(value)
    return found


def capped(cap: Bytes) -> Bytes:
    """Lower the address-space limit to ``cap``, never raising it; return the limit that holds."""
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    limit = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
    if soft == resource.RLIM_INFINITY or soft > limit:
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
        return limit
    return Bytes(soft)


def refusal(reason: Prose) -> Answer:
    """Exit 2 naming a fault of the dispatcher's own."""
    return Answer(2, Prose(""), Prose(fault_text(reason) + "\n"))


def faulted(reason: Prose, raw: EventBytes) -> Answer:
    """A fault of the dispatcher's own: a note where ``raw`` is a Stop or SubagentStop a Stop hook has kept going, a
    refusal otherwise."""
    try:
        payload = payload_of(raw)
    except KitFault:
        return refusal(reason)
    return noted(payload["hook_event_name"], [fault_text(reason)]) if kept_going(payload) else refusal(reason)


def judged(argv: list[Option]) -> Answer:
    """The answer to the event on stdin, under the limits."""
    try:
        options = given(argv)
        cap = Bytes(int(options.get(Option("--cap"), str(CAP))))
        wall = Seconds(float(options.get(Option("--wall"), str(WALL))))
    except ValueError as fault:
        return refusal(Prose(f"the dispatcher's arguments are wrong: {fault}"))
    cap = capped(cap)
    _ = signal.signal(signal.SIGALRM, overran)
    raw = EventBytes(b"")
    _ = signal.setitimer(signal.ITIMER_REAL, wall)
    try:
        raw = EventBytes(sys.stdin.buffer.read(MAX_EVENT + 1))
        if len(raw) > MAX_EVENT:
            return refusal(Prose(f"the hook event is longer than {MAX_EVENT >> 20} MiB"))
        home = os.environ.get("HOME", "/")
        settings = Settings(
            FolderPath(options.get(Option("--rules"), os.path.join(KIT, "rules"))),
            FolderPath(os.path.join(home, ".config", "claude-guard", "off")),
            ReadSettings(ReaderPath(os.path.join(KIT, "bin", "shell-reader")), READER_TIMEOUT, environment()))
        return answer(raw, settings)
    except Overrun:
        reason = Prose(f"the dispatcher outran its {wall:g} s limit")
    except MemoryError:
        reason = Prose(f"the dispatcher ran out of its {cap >> 20} MiB address space")
    except Exception as fault:  # noqa: BLE001 - any fault of the dispatcher refuses, naming itself
        reason = Prose(f"the dispatcher failed: {type(fault).__name__}: {fault}")
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, 0)
    return faulted(reason, raw)


def main() -> ExitStatus:
    """Answer the event: write stdout and stderr, return the exit status; a write that fails exits 2."""
    try:
        result = judged([Option(argument) for argument in sys.argv[1:]])
    except BaseException as fault:  # noqa: BLE001 - the limit landing in a handler still refuses
        result = refusal(Prose(f"the dispatcher failed: {type(fault).__name__}: {fault}"))
    try:
        if result.stdout:
            _ = sys.stdout.write(result.stdout)
        if result.stderr:
            _ = sys.stderr.write(result.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
    except OSError:  # the harness reads no answer, so none passes; what is left unwritten goes nowhere
        for stream in (sys.stdout, sys.stderr):
            os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())
        return 2
    return result.status


if __name__ == "__main__":
    sys.exit(main())
