"""The bytecode-left rule, through the dispatcher with every other rule switched off: after a Bash call that writes a
`.py` file and starts a Python test run, a tracked source of a work tree the call reaches whose __pycache__ entry
CPython would run while its code is not the source's is refused, naming the source and its entry; a fresh entry, no
entry, an entry recording another mtime or size, a hash-based entry and an untracked source pass, and a call
writing no `.py` or starting no test run scans nothing.

Each case has a git work tree of its own in the temporary folder, its name holding a space, tracking pkg/mod.py."""

import json
import os
import py_compile
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NewType

from dispatcher.core import Answer, EventBytes, FolderPath, HookEvent, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.words import VarName
from tests.test_declarations import NONE_DECLARED

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
Source = NewType("Source", str)
Second = NewType("Second", int)
SOURCE = Source("x = 1\n")
PLANT = Source("x = 2\n")
SECOND = Second(1_700_000_000)
CALL = ShellText("sed -i s/a/b/ pkg/mod.py && pytest tests/")
HEADLINE = "this call wrote a Python file and ran Python tests, and left a __pycache__ entry CPython runs in place of"
HASH_CHECKED = 3


def entry(source: Path) -> Path:
    """The __pycache__ entry beside ``source`` this interpreter imports it from, whatever the cache prefix."""
    return source.parent / "__pycache__" / f"{source.stem}.{sys.implementation.cache_tag}.pyc"


class Case(unittest.TestCase):
    """A case with a work tree tracking pkg/mod.py, a home declaring nothing and every rule but this one off."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.base)
        for folder in ("a repo/pkg", "home/.config/claude-guard", "off"):
            (self.base / folder).mkdir(parents=True)
        _ = (self.base / "home/.config/claude-guard/machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
        for module in (KIT / "rules").glob("[!_]*.py"):
            if module.stem != "bytecode_left":
                (self.base / "off" / module.stem.replace("_", "-")).touch()
        self.repo = self.base / "a repo"
        self.source = self.repo / "pkg" / "mod.py"
        _ = self.source.write_text(SOURCE, encoding="utf-8")
        for args in (("init", "-q"), ("add", "pkg/mod.py")):
            _ = subprocess.run(["git", "-C", str(self.repo), *args], check=True, timeout=30)

    def judged(self, call: ShellText = CALL, cwd: Path | None = None, name: HookEvent = "PostToolUse") -> Answer:
        """The dispatcher's answer to a Bash event ``name`` for ``call`` run from ``cwd``, the work tree where None."""
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.base / "off")),
                            ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(self.base / "home"))}),
                            home=self.base / "home")
        fields = {"hook_event_name": name, "tool_name": "Bash", "cwd": str(cwd or self.repo),
                  "tool_input": {"command": call}}
        return answer(EventBytes(json.dumps(fields).encode()), settings)

    def plant(self, source: Path, planted: Source = PLANT, kept: Source = SOURCE, second: Second = SECOND) -> None:
        """Compile ``planted`` as ``source``'s entry at ``second``, then write ``kept`` back with that mtime."""
        for text, compiled in ((planted, True), (kept, False)):
            _ = source.write_text(text, encoding="utf-8")
            os.utime(source, (second, second))
            if compiled:
                _ = py_compile.compile(str(source), cfile=str(entry(source)), doraise=True)

    def assert_refused(self, got: Answer, *sources: Path) -> None:
        """``got`` refuses naming each of ``sources`` with its entry, and the form deleting them."""
        self.assertEqual(got.status, 2, got.stderr)
        self.assertIn(HEADLINE, got.stderr)
        for source in sources:
            self.assertIn(f"  * {source} runs {entry(source)}\n", got.stderr)
        self.assertIn("\n  rm " + shlex.join(str(entry(source)) for source in sources), got.stderr)

    def assert_passed(self, got: Answer) -> None:
        """``got`` lets the call through saying nothing."""
        self.assertEqual((got.status, got.stderr), (0, ""))


class Refused(Case):
    """A planted entry left in a tree the call reaches is refused, naming the source and its entry."""

    def test_a_planted_entry_left_is_refused_naming_the_file(self) -> None:
        self.plant(self.source)
        self.assert_refused(self.judged(), self.source)

    def test_the_refusal_names_the_entry_to_delete(self) -> None:
        self.plant(self.source)
        form = f"so the next import compiles the source:\n  rm {shlex.quote(str(entry(self.source)))}"
        self.assertIn(form, self.judged().stderr)

    def test_a_tree_a_shell_write_names_is_scanned(self) -> None:
        self.plant(self.source)
        call = ShellText(f"sed -i s/a/b/ {shlex.quote(str(self.source))} && pytest tests/")
        self.assert_refused(self.judged(call, self.base), self.source)

    def test_a_tree_a_command_runs_in_is_scanned(self) -> None:
        self.plant(self.source)
        call = ShellText(f"cd {shlex.quote(str(self.repo))} && sed -i s/a/b/ {self.base}/x.py && pytest tests/")
        self.assert_refused(self.judged(call, self.base), self.source)

    def test_a_python_write_and_an_unspelled_test_run_are_read_as_bytecode_plants_reads_them(self) -> None:
        self.plant(self.source)
        call = ShellText("python3 - <<'EOF'\nimport subprocess, sys\nopen(\"pkg/mod.py\", \"w\")\n"
                         "subprocess.run([sys.executable, \"-m\", \"pytest\"])\nEOF")
        self.assert_refused(self.judged(call), self.source)

    def test_an_mtime_past_2106_is_compared_as_cpython_records_it(self) -> None:
        self.plant(self.source, second=Second(SECOND + 2**32))
        self.assert_refused(self.judged(), self.source)

    def test_an_entry_or_source_that_cannot_be_compiled_is_refused(self) -> None:
        for case, body, kept in (("truncated", b"", SOURCE), ("corrupt", b"\xff", SOURCE),
                                 ("unparsable", None, Source("x = (\n")), ("unreadable", None, SOURCE)):
            with self.subTest(case=case):
                self.plant(self.source, kept=kept)
                if body is not None:
                    _ = entry(self.source).write_bytes(entry(self.source).read_bytes()[:16] + body)
                if case == "unreadable":
                    self.source.chmod(0)
                    self.addCleanup(self.source.chmod, 0o644)
                self.assert_refused(self.judged(), self.source)
                self.source.chmod(0o644)

    def test_a_tree_git_cannot_list_is_refused(self) -> None:
        broken = self.base / "broken"
        (broken / ".git").mkdir(parents=True)
        got = self.judged(ShellText(f"sed -i s/a/b/ {broken}/x.py && pytest tests/"), self.base)
        self.assertEqual(got.status, 2, got.stderr)
        self.assertIn(f"the rule bytecode-left failed: CalledProcessError: Command '['git', '-C', '{broken}'",
                      got.stderr)


class Passed(Case):
    """What CPython would not run in place of a source, or a call the rule does not scan, passes."""

    def test_a_fresh_entry_passes(self) -> None:
        self.plant(self.source, planted=SOURCE)
        self.assert_passed(self.judged())

    def test_no_entry_passes(self) -> None:
        self.assert_passed(self.judged())

    def test_an_entry_recording_another_mtime_passes(self) -> None:
        self.plant(self.source)
        os.utime(self.source, (SECOND + 1, SECOND + 1))
        self.assert_passed(self.judged())

    def test_an_entry_recording_another_size_passes(self) -> None:
        self.plant(self.source, planted=Source("x = 22\n"))
        self.assert_passed(self.judged())

    def test_a_hash_based_entry_passes_though_it_reads_as_the_sources_stamp(self) -> None:
        self.plant(self.source)
        data = entry(self.source).read_bytes()
        _ = entry(self.source).write_bytes(data[:4] + HASH_CHECKED.to_bytes(4, "little") + data[8:])
        self.assert_passed(self.judged())

    def test_an_untracked_file_is_not_scanned(self) -> None:
        other = self.repo / "pkg" / "other.py"
        self.plant(other)
        self.assert_passed(self.judged(ShellText("sed -i s/a/b/ pkg/other.py && pytest tests/")))

    def test_a_call_writing_no_python_file_scans_nothing(self) -> None:
        self.plant(self.source)
        self.assert_passed(self.judged(ShellText("sed -i s/a/b/ pkg/mod.txt && pytest tests/")))

    def test_a_call_starting_no_test_run_scans_nothing(self) -> None:
        self.plant(self.source)
        self.assert_passed(self.judged(ShellText("sed -i s/a/b/ pkg/mod.py && git diff")))

    def test_another_event_is_not_judged(self) -> None:
        self.plant(self.source)
        self.assert_passed(self.judged(ShellText("echo x > pkg/mod.py && pytest tests/"), name="PostToolUseFailure"))


if __name__ == "__main__":
    _ = unittest.main()
