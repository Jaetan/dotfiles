#!/home/nicolas/.local/bin/python3.14
"""The shell reader's pin gate: the corpus gate has passed for the reader, the gate itself and bash as they are.

Run: `pin_gate.py [--check] [--work DIR]`. The pin is the sha256 over the reader's go.mod, go.sum and every `.go` file
beneath reader/; corpus_gate.py, every module of gates/ it imports, directly or through another, as the import
statements of each name them (an import naming gates/<name>.py, wherever in the file it stands), and
corpus_refusals.toml; then bash, the bash_reference.py BASH the corpus gate is told to run, as its resolved path and
the first line of its `--version`. Each file is written as its path relative to the kit, NUL, its size, NUL and its
bytes, in path order, then each bash fact as its name, NUL, its size, NUL and its bytes. reader/build.sh, this file
and the corpus are outside the pin.

The record, ~/.local/state/claude-guard/corpus/pin.json, is {"digest": the pin, "passed": the UTC time the run ended} of
the last run that passed; a record that is missing, unreadable or not a JSON object holding a string digest is read
as no run at all, and a digest stands for the pin only when it is the pin's own spelling, so a record whose digest
was taken over the reader's files alone is read as no run too.

A pin the record holds is a pass and nothing runs. Otherwise reader/build.sh builds the reader into bin/shell-reader,
corpus_gate.py judges that binary with that bash (--work passed on), and the pin is recorded only when both exit 0 and
the pin taken again after the run is unchanged; their output passes through. --check builds, runs and writes nothing.

Exit 0: the record holds the pin. 1: with --check, it does not; otherwise the build failed, the corpus gate exited 1,
or a pinned input changed during the run, the record left as it was. 2: a pinned file cannot be read or does not
parse, bash is missing or gives no version, the corpus gate exited another non-zero status or could not start, or
the record cannot be written.
"""

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NewType, TypedDict

from bash_reference import BASH
from corpus_common import CORPUS_DIR, PYTHON

Digest = NewType("Digest", str)
Moment = NewType("Moment", str)
Label = NewType("Label", str)
Content = NewType("Content", bytes)
ExitStatus = Literal[0, 1, 2]

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
BUILD = KIT / "reader" / "build.sh"
BINARY = KIT / "bin" / "shell-reader"
CORPUS_GATE = HERE / "corpus_gate.py"
RECORD = CORPUS_DIR / "pin.json"
VERSION_WALL = 30


class PinFault(Exception):
    """A pinned input that is there but cannot be pinned: a module that does not parse, a bash that gives no version."""


class PinRecord(TypedDict):
    """The record: the pin of the last corpus gate run that passed and the UTC time it ended."""

    digest: Digest
    passed: Moment


def imported(gates: Path, path: Path, source: Content) -> list[Path]:
    """The modules of `gates` the source's import statements name, wherever in the file they stand."""
    try:
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError) as error:
        raise PinFault(f"{path} does not parse: {error}") from error
    names = [name for node in ast.walk(tree) for name in
             ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
              [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else [])]
    return [module for name in names if (module := gates / f"{name.split('.')[0]}.py").is_file()]


def version_line(bash: Path) -> Content:
    """The first line of `bash --version`; a bash that exits non-zero, prints no line or hangs raises PinFault."""
    try:
        run = subprocess.run([str(bash), "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                             timeout=VERSION_WALL, check=False)
    except subprocess.TimeoutExpired as error:
        raise PinFault(f"{bash} --version ran past {VERSION_WALL} s") from error
    line = Content(run.stdout.split(b"\n", 1)[0])
    if run.returncode != 0 or not line.strip():
        raise PinFault(f"{bash} --version gave status {run.returncode} and {line[:80]!r}, no version")
    return line


def pin_of(kit: Path, bash: Path) -> Digest:
    """The pin of the kit's reader and corpus gate and of the bash the gate runs. A missing go.mod, go.sum, gate file
    or bash, or a file that cannot be read, raises OSError."""
    reader, gates = kit / "reader", kit / "gates"
    held = {path: Content(path.read_bytes()) for path in
            [reader / "go.mod", reader / "go.sum", *reader.rglob("*.go"), gates / "corpus_refusals.toml"]}
    todo = [gates / "corpus_gate.py"]
    while todo:
        path = todo.pop()
        if path not in held:
            held[path] = Content(path.read_bytes())
            todo += imported(gates, path, held[path])
    facts = [*((Label(path.relative_to(kit).as_posix()), held[path])
               for path in sorted(held, key=lambda path: path.relative_to(kit).as_posix())),
             (Label("bash"), Content(str(bash.resolve(strict=True)).encode())),
             (Label("bash --version"), version_line(bash))]
    pin = hashlib.sha256()
    for label, data in facts:
        pin.update(f"{label}\0{len(data)}\0".encode())
        pin.update(data)
    return Digest(pin.hexdigest())


def recorded(record: Path) -> Digest | None:
    """The pin the record holds, None where it is missing, unreadable or malformed."""
    try:
        held = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    digest = held.get("digest") if isinstance(held, dict) else None
    return Digest(digest) if isinstance(digest, str) else None


def write_record(record: Path, pin: Digest) -> None:
    """Replace the record with the pin and the time now, whole or not at all."""
    record.parent.mkdir(parents=True, exist_ok=True)
    row = PinRecord(digest=pin, passed=Moment(datetime.now(UTC).isoformat(timespec="seconds")))
    part = record.with_name(f"{record.name}.part")
    part.write_text(json.dumps(row) + "\n", encoding="utf-8")
    part.replace(record)


def gate(check: bool, work: Path | None) -> ExitStatus:
    pin = pin_of(KIT, BASH)
    if recorded(RECORD) == pin:
        print(f"pin_gate: the corpus gate has passed for the pin {pin[:12]} of the reader, the gate and {BASH}")
        return 0
    if check:
        print(f"pin_gate: the corpus gate has not passed for the pin {pin[:12]} of the reader, the gate and {BASH} "
              f"(record {RECORD}): run {HERE / 'pin_gate.py'}")
        return 1
    if subprocess.run(["bash", str(BUILD)], check=False).returncode != 0:
        print(f"pin_gate: {BUILD} failed; the record is unchanged", file=sys.stderr)
        return 1
    status = subprocess.run([str(PYTHON), str(CORPUS_GATE), "--reader", str(BINARY), "--bash", str(BASH),
                             *(["--work", str(work)] if work else [])], check=False).returncode
    if status != 0:
        print(f"pin_gate: corpus_gate.py exited {status}; the record is unchanged", file=sys.stderr)
        return 1 if status == 1 else 2
    if pin_of(KIT, BASH) != pin:
        print("pin_gate: a pinned input changed during the run; the record is unchanged", file=sys.stderr)
        return 1
    write_record(RECORD, pin)
    print(f"pin_gate: the corpus gate passed; the pin {pin[:12]} is recorded in {RECORD}")
    return 0


def main() -> ExitStatus:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="compare the pin with the record; build and run nothing")
    parser.add_argument("--work", type=Path, help="the corpus gate's work folder, passed on")
    args = parser.parse_args()
    try:
        return gate(args.check, args.work)
    except (OSError, PinFault) as error:
        print(f"pin_gate: cannot judge: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
