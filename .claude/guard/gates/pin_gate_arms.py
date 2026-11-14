#!/home/nicolas/.local/bin/python3.14
"""The pin gate's arms: a change to anything the corpus gate's verdict rests on turns the kit's suite red until a
passing corpus gate run records the new pin.

`pin_gate_arms.py --work DIR` runs each arm in a copy of the kit of its own beneath DIR/<arm>/: the copy holds
gates/pin_gate.py, gates/corpus_common.py, gates/bash_reference.py, gates/corpus_refusals.toml, testing/runner.py,
tests/test_reader_pin.py and the reader's go.mod, go.sum and .go files, with stand-ins for reader/build.sh and
gates/corpus_gate.py that log their arguments to calls.log and exit as the arm sets, a home of its own whose
.local/state/claude-guard/corpus/pin.json is the record, and a stand-in bash: the copy's bash_reference.py names
DIR/<arm>/bash, a link to a script printing control/bash-version and exiting with control/bash-status. The stand-in
corpus gate imports bash_reference, which imports corpus_common, and takes a name from gates/gate_helper.py, a module
only the arms create. The real kit and its reader are only read. An arm runs the copy's `pin_gate.py --check`, its
whole suite or a pin gate run, and checks their exit statuses, the stand-ins' calls and the record's bytes after.

  pinned                 the record holds the pin: check 0, suite 0, a run 0 starting nothing.
  outside the pin        build.sh changed, a gates file nothing imports, a later bash --version line: check 0.
  inside the pin         each alone, with no run: check 1, suite 1 naming pin_gate.py, nothing started:
                         go.sum, go.mod, a .go file, a new .go file; corpus_gate.py; bash_reference.py, which the gate
                         imports; corpus_common.py, which it imports through bash_reference.py; gate_helper.py, which
                         no list names; corpus_refusals.toml; bash's first --version line; bash's resolved path.
  reader-only record     a record holding a digest of the reader's files alone: check 1, suite 1, a run records the
                         pin, check 0 after.
  build fails            a run exits 1, the corpus gate never started, the record unchanged.
  corpus gate exits 1    a run exits 1, the record still the old pin; exits 2: a run exits 2, the record unchanged.
  changed during the run the reader or the refusal list: a run exits 1, the record unchanged.
  corpus gate passes     a run exits 0, build.sh then corpus_gate.py --reader bin/shell-reader --bash <bash> --work
                         DIR called, the record the new pin; check 0 and suite 0 after.
  record missing         check 1, suite 1, a run without --work starts the corpus gate and records the pin.
  record corrupt         empty, not UTF-8, truncated JSON holding the pin, a list holding the pin, the pin upper-cased,
                         a digest one digit short, arrays nested past the decoder's depth, a directory: check 1,
                         suite 1, a run starts the corpus gate.
  record unwritable      its folder is a file: a passing corpus gate run exits 2.
  cannot pin             go.sum missing, bash missing, bash --version exiting 1 or printing nothing, corpus_gate.py
                         not parsing: check 2, a run 2 starting nothing.

Writes DIR/<arm>/ and DIR/arms.json. Exit 0: every arm as expected. 1: an arm not.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NamedTuple, NewType

from corpus_common import PYTHON
from pin_gate import Digest, pin_of, recorded

ArmName = NewType("ArmName", str)
Prose = NewType("Prose", str)
Status = NewType("Status", int)
Output = NewType("Output", str)
CallLine = NewType("CallLine", str)
RecordText = NewType("RecordText", bytes)
ExitStatus = Literal[0, 1]
Flag = Literal["--check", "--work"]

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
READER = KIT / "reader"
COPIED = [Path("gates/pin_gate.py"), Path("gates/corpus_common.py"), Path("gates/bash_reference.py"),
          Path("gates/corpus_refusals.toml"), Path("testing/runner.py"), Path("tests/__init__.py"),
          Path("tests/test_reader_pin.py")]
RECORD = Path(".local/state/claude-guard/corpus/pin.json")
BASH_LINE = 'BASH = Path("/usr/bin/bash")'
VERSION = Prose("GNU bash, version 9.9.9(1)-release (stand-in)")
LATER = Prose("a later line\n")
WALL = 120

BUILD_STAND_IN = """#!/usr/bin/env bash
printf 'build %s\\n' "$0" >> {calls}
exit "$(cat {control}/build)"
"""
BASH_STAND_IN = """#!/bin/sh
cat {control}/bash-version
exit "$(cat {control}/bash-status)"
"""
GATE_STAND_IN = """import sys
from pathlib import Path

import bash_reference
from gate_helper import HELPER

with open({calls!r}, "a", encoding="utf-8") as calls:
    calls.write("gate " + " ".join(sys.argv[1:]) + "\\n")
touch = Path({control!r}, "touch")
if touch.exists():
    with open(Path({kit!r}, touch.read_text(encoding="utf-8")), "a", encoding="utf-8") as touched:
        touched.write("\\n")
sys.exit(int(Path({control!r}, "gate").read_text()))
"""


class Copy(NamedTuple):
    """One arm's copy of the kit, its home, its record, its stand-ins' call log and controls, its stand-in bash's link
    and the pin it held when made."""

    root: Path
    kit: Path
    home: Path
    record: Path
    calls: Path
    control: Path
    bash: Path
    pin: Digest


class Outcome(NamedTuple):
    status: Status
    out: Output


class Arm(NamedTuple):
    name: ArmName
    ok: bool
    note: Prose


class Change(NamedTuple):
    name: ArmName
    change: Callable[[Copy], None]


class Corrupt(NamedTuple):
    """A corrupt record: its bytes made from the copy's pin, or None for a directory at its place."""

    name: ArmName
    text: Callable[[Digest], RecordText | None]


def written(path: Path, text: Prose) -> None:
    path.write_text(text, encoding="utf-8")


def appended(path: Path, text: Prose) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(text)


def repointed(link: Path, target: Path) -> None:
    link.unlink()
    link.symlink_to(target)


def fresh(work: Path, name: ArmName) -> Copy:
    """A copy of the kit for one arm, its record absent, both stand-ins set to exit 0, its bash giving VERSION."""
    root = work / re.sub(r"\W+", "-", name)
    shutil.rmtree(root, ignore_errors=True)
    kit = root / "kit"
    sources = COPIED + [p.relative_to(KIT) for p in [READER / "go.mod", READER / "go.sum", *READER.rglob("*.go")]]
    for rel in sources:
        (kit / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KIT / rel, kit / rel)
    home, control, calls, bash = root / "home", root / "control", root / "calls.log", root / "bash"
    control.mkdir()
    (home / RECORD).parent.mkdir(parents=True)
    calls.touch()
    for control_file, text in (("build", "0"), ("gate", "0"), ("bash-status", "0"),
                               ("bash-version", VERSION + "\n" + LATER)):
        written(control / control_file, Prose(text))
    for script in ("bash-a", "bash-b"):
        written(root / script, Prose(BASH_STAND_IN.format(control=control)))
        (root / script).chmod(0o755)
    bash.symlink_to(root / "bash-a")
    reference = kit / "gates" / "bash_reference.py"
    source = reference.read_text(encoding="utf-8")
    if source.count(BASH_LINE) != 1:
        raise SystemExit(f"pin_gate_arms: {reference} holds {BASH_LINE!r} {source.count(BASH_LINE)} times, not once")
    written(reference, Prose(source.replace(BASH_LINE, f"BASH = Path({str(bash)!r})")))
    written(kit / "gates" / "gate_helper.py", Prose('HELPER = "a module only the arms create"\n'))
    written(kit / "reader" / "build.sh", Prose(BUILD_STAND_IN.format(calls=calls, control=control)))
    written(kit / "gates" / "corpus_gate.py",
            Prose(GATE_STAND_IN.format(calls=str(calls), control=str(control), kit=str(kit))))
    return Copy(root, kit, home, home / RECORD, calls, control, bash, pin_of(kit, bash))


def reader_only(reader: Path) -> Digest:
    """A digest of the reader's go.mod, go.sum and .go files alone, each named relative to reader/: what a record
    taken over the reader's files alone holds."""
    files = sorted([reader / "go.mod", reader / "go.sum", *reader.rglob("*.go")],
                   key=lambda path: path.relative_to(reader).as_posix())
    pin = hashlib.sha256()
    for path in files:
        data = path.read_bytes()
        pin.update(f"{path.relative_to(reader).as_posix()}\0{len(data)}\0".encode())
        pin.update(data)
    return Digest(pin.hexdigest())


def run(copy: Copy, script: Path, *args: Path | Flag) -> Outcome:
    """Run a script of the copy with the copy's home and a bytecode folder made for the run and removed after it."""
    prefix = copy.root / "pycache"
    env = {**os.environ, "HOME": str(copy.home), "PYTHONPYCACHEPREFIX": str(prefix)}
    try:
        done = subprocess.run([str(PYTHON), str(copy.kit / script), *map(str, args)], env=env, capture_output=True,
                              text=True, check=False, timeout=WALL)
    except subprocess.TimeoutExpired:
        return Outcome(Status(-1), Output("timed out"))
    finally:
        shutil.rmtree(prefix, ignore_errors=True)
    return Outcome(Status(done.returncode), Output(done.stdout + done.stderr))


def check(copy: Copy) -> Status:
    return run(copy, Path("gates/pin_gate.py"), "--check").status


def suite(copy: Copy) -> Outcome:
    return run(copy, Path("testing/runner.py"))


def gate(copy: Copy, *args: Path | Flag) -> Status:
    return run(copy, Path("gates/pin_gate.py"), *args).status


def calls(copy: Copy) -> list[CallLine]:
    return [CallLine(line) for line in copy.calls.read_text(encoding="utf-8").splitlines()]


def bytes_of(record: Path) -> RecordText | None:
    return RecordText(record.read_bytes()) if record.is_file() else None


def pinned(pin: Digest) -> RecordText:
    return RecordText((json.dumps({"digest": pin, "passed": "2026-11-07T00:00:00+00:00"}) + "\n").encode())


def build_call(copy: Copy) -> CallLine:
    return CallLine(f"build {copy.kit / 'reader' / 'build.sh'}")


def gate_call(copy: Copy, work: Path | None) -> CallLine:
    tail = f" --work {work}" if work else ""
    return CallLine(f"gate --reader {copy.kit / 'bin' / 'shell-reader'} --bash {copy.bash}{tail}")


def arms(work: Path) -> ExitStatus:
    results: list[Arm] = []

    def record(name: ArmName, ok: bool, note: Prose) -> None:
        results.append(Arm(name, ok, note))

    def seeded(name: ArmName) -> Copy:
        copy = fresh(work, name)
        copy.record.write_bytes(pinned(copy.pin))
        return copy

    copy = seeded(ArmName("pinned"))
    k, s, g = check(copy), suite(copy), gate(copy)
    record(ArmName("pinned"), (k, s.status, g) == (0, 0, 0) and not calls(copy) and
           bytes_of(copy.record) == pinned(copy.pin),
           Prose(f"check {k}, suite {s.status}, run {g}, calls {calls(copy)}"))

    outside = [Change(ArmName("build.sh changed"),
                      lambda c: appended(c.kit / "reader" / "build.sh", Prose("# changed\n"))),
               Change(ArmName("a gates file nothing imports"),
                      lambda c: written(c.kit / "gates" / "unimported.py", Prose("VALUE = 1\n"))),
               Change(ArmName("a later bash --version line changed"),
                      lambda c: written(c.control / "bash-version", Prose(VERSION + "\nanother later line\n")))]
    for name, change in outside:
        copy = seeded(name)
        change(copy)
        k = check(copy)
        record(name, k == 0, Prose(f"check {k}"))

    inside = [Change(ArmName("go.sum changed"), lambda c: appended(c.kit / "reader" / "go.sum", Prose("\n"))),
              Change(ArmName("go.mod changed"), lambda c: appended(c.kit / "reader" / "go.mod", Prose("// changed\n"))),
              Change(ArmName("a .go file changed"),
                     lambda c: appended(c.kit / "reader" / "tree.go", Prose("// changed\n"))),
              Change(ArmName("a new .go file"),
                     lambda c: written(c.kit / "reader" / "added.go", Prose("package main\n"))),
              Change(ArmName("corpus_gate.py changed"),
                     lambda c: appended(c.kit / "gates" / "corpus_gate.py", Prose("# changed\n"))),
              Change(ArmName("bash_reference.py changed, imported by the gate"),
                     lambda c: appended(c.kit / "gates" / "bash_reference.py", Prose("# changed\n"))),
              Change(ArmName("corpus_common.py changed, imported through bash_reference.py"),
                     lambda c: appended(c.kit / "gates" / "corpus_common.py", Prose("# changed\n"))),
              Change(ArmName("gate_helper.py changed, named by no list"),
                     lambda c: appended(c.kit / "gates" / "gate_helper.py", Prose("# changed\n"))),
              Change(ArmName("corpus_refusals.toml changed"),
                     lambda c: appended(c.kit / "gates" / "corpus_refusals.toml", Prose("# changed\n"))),
              Change(ArmName("bash's first --version line changed"),
                     lambda c: written(c.control / "bash-version",
                                       Prose("GNU bash, version 9.9.10(1)-release (stand-in)\n" + LATER))),
              Change(ArmName("bash's resolved path changed"), lambda c: repointed(c.bash, c.root / "bash-b"))]
    for name, change in inside:
        copy = seeded(name)
        change(copy)
        k, s = check(copy), suite(copy)
        record(name, k == 1 and s.status == 1 and "pin_gate.py" in s.out and not calls(copy) and
               bytes_of(copy.record) == pinned(copy.pin), Prose(f"check {k}, suite {s.status}"))

    copy = fresh(work, ArmName("reader-only record"))
    copy.record.write_bytes(pinned(reader_only(copy.kit / "reader")))
    k, s = check(copy), suite(copy)
    g = gate(copy)
    record(ArmName("reader-only record"), (k, s.status, g) == (1, 1, 0) and "pin_gate.py" in s.out and
           calls(copy) == [build_call(copy), gate_call(copy, None)] and recorded(copy.record) == copy.pin and
           check(copy) == 0, Prose(f"check {k}, suite {s.status}, run {g}"))

    for name, stand_in, status, want in ((ArmName("build fails"), "build", "1", 1),
                                         (ArmName("corpus gate exits 1"), "gate", "1", 1),
                                         (ArmName("corpus gate exits 2"), "gate", "2", 2)):
        copy = seeded(name)
        appended(copy.kit / "reader" / "go.sum", Prose("\n"))
        written(copy.control / stand_in, Prose(status))
        g = gate(copy)
        expected = [build_call(copy)] + ([gate_call(copy, None)] if stand_in == "gate" else [])
        record(name, g == want and calls(copy) == expected and bytes_of(copy.record) == pinned(copy.pin) and
               recorded(copy.record) == copy.pin and check(copy) == 1, Prose(f"run {g}, calls {len(calls(copy))}"))

    for name, touched in ((ArmName("reader changed during the run"), "reader/go.sum"),
                          (ArmName("refusal list changed during the run"), "gates/corpus_refusals.toml")):
        copy = seeded(name)
        appended(copy.kit / "reader" / "go.sum", Prose("\n"))
        written(copy.control / "touch", Prose(touched))
        g = gate(copy)
        record(name, g == 1 and len(calls(copy)) == 2 and bytes_of(copy.record) == pinned(copy.pin), Prose(f"run {g}"))

    copy = seeded(ArmName("corpus gate passes"))
    appended(copy.kit / "reader" / "go.sum", Prose("\n"))
    new = pin_of(copy.kit, copy.bash)
    gate_work = copy.root / "gate-work"
    g = gate(copy, "--work", gate_work)
    k, s = check(copy), suite(copy)
    held = json.loads(copy.record.read_text(encoding="utf-8")) if copy.record.is_file() else None
    record(ArmName("corpus gate passes"), g == 0 and calls(copy) == [build_call(copy), gate_call(copy, gate_work)] and
           new != copy.pin and recorded(copy.record) == new and isinstance(held, dict) and
           set(held) == {"digest", "passed"} and (k, s.status) == (0, 0),
           Prose(f"run {g}, check {k}, suite {s.status}, record {held}"))

    copy = fresh(work, ArmName("record missing"))
    k, s = check(copy), suite(copy)
    g = gate(copy)
    record(ArmName("record missing"), (k, s.status, g) == (1, 1, 0) and "pin_gate.py" in s.out and
           calls(copy) == [build_call(copy), gate_call(copy, None)] and recorded(copy.record) == copy.pin and
           check(copy) == 0, Prose(f"check {k}, suite {s.status}, run {g}"))

    corrupt = [Corrupt(ArmName("record empty"), lambda pin: RecordText(b"")),
               Corrupt(ArmName("record not UTF-8"), lambda pin: RecordText(b"\xff" + pinned(pin))),
               Corrupt(ArmName("record truncated"), lambda pin: RecordText(pinned(pin)[:-3])),
               Corrupt(ArmName("record a list"), lambda pin: RecordText(json.dumps([pin]).encode())),
               Corrupt(ArmName("record upper-cased"),
                       lambda pin: RecordText(json.dumps({"digest": pin.upper()}).encode())),
               Corrupt(ArmName("record one digit short"),
                       lambda pin: RecordText(json.dumps({"digest": pin[:-1]}).encode())),
               Corrupt(ArmName("record nested too deep"), lambda pin: RecordText(b"[" * 100_000)),
               Corrupt(ArmName("record a directory"), lambda pin: None)]
    for name, text_of in corrupt:
        copy = fresh(work, name)
        text = text_of(copy.pin)
        if text is None:
            copy.record.mkdir()
        else:
            copy.record.write_bytes(text)
        before = bytes_of(copy.record)
        written(copy.control / "gate", Prose("1"))
        k, s = check(copy), suite(copy)
        g = gate(copy)
        record(name, (k, s.status, g) == (1, 1, 1) and "pin_gate.py" in s.out and
               calls(copy) == [build_call(copy), gate_call(copy, None)] and bytes_of(copy.record) == before,
               Prose(f"check {k}, suite {s.status}, run {g}"))

    copy = fresh(work, ArmName("record unwritable"))
    shutil.rmtree(copy.record.parent)
    written(copy.record.parent, Prose(""))
    g = gate(copy)
    record(ArmName("record unwritable"), g == 2 and len(calls(copy)) == 2, Prose(f"run {g}"))

    faults = [Change(ArmName("go.sum missing"), lambda c: (c.kit / "reader" / "go.sum").unlink()),
              Change(ArmName("bash missing"), lambda c: repointed(c.bash, c.root / "no-bash")),
              Change(ArmName("bash --version exits 1"), lambda c: written(c.control / "bash-status", Prose("1"))),
              Change(ArmName("bash --version prints nothing"),
                     lambda c: written(c.control / "bash-version", Prose(""))),
              Change(ArmName("corpus_gate.py does not parse"),
                     lambda c: appended(c.kit / "gates" / "corpus_gate.py", Prose("def (\n")))]
    for name, change in faults:
        copy = seeded(name)
        change(copy)
        k, g = check(copy), gate(copy)
        record(name, (k, g) == (2, 2) and not calls(copy), Prose(f"check {k}, run {g}"))

    (work / "arms.json").write_text(json.dumps([a._asdict() for a in results], indent=1) + "\n", encoding="utf-8")
    for a in results:
        print(f"{'ok ' if a.ok else 'BAD'} {a.name}: {a.note}")
    bad = [a for a in results if not a.ok]
    print(f"{len(results) - len(bad)} of {len(results)} arms as expected; {work / 'arms.json'}")
    return 1 if bad else 0


def main() -> ExitStatus:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--work", type=Path, required=True, help="the folder each arm's copy is made in")
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    return arms(args.work.resolve())


if __name__ == "__main__":
    sys.exit(main())
