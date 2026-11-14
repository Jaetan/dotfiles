"""The test-determinism rule, through the dispatcher with every other rule of the kit switched off: after an Edit, a
Write, a MultiEdit or a Bash call, the files it changed that stand under the gate's as_from are asked of the declared
determinism gate once, and each test among them is handed where it stands; the gate's exit 1 or 2 refuses with its
text and its rule's place, its exit 0 gives the rule as a note naming the tests. A call's tests past FILES_PER_RULE
are refused as unchecked. After a Read, or a Bash call that wrote no test, the paths it names that the gate calls
tests get the note; a non-test, a word that is no path, a deleted file and a project with no gate are silent.

Each case has a git work tree in a temporary folder declaring a stand-in gate in its .claude/guard.toml, as_from its
src folder; the stand-in records its argv, calls a test each asked path whose name starts with `test`, and for a
handed file prints a finding and exits with the code the case's code file holds."""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NewType

from dispatcher.core import Answer, EventBytes, FolderPath, HookEvent, Settings, ToolName, answer
from dispatcher.gate_run import FILES_PER_RULE, ExitCode
from dispatcher.model import ReadSettings
from dispatcher.tree import Count, ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_dispatcher import machine_home

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = "test_determinism.py"
Source = NewType("Source", str)
Word = NewType("Word", str)
Name = NewType("Name", str)
JsonKey = NewType("JsonKey", str)
JsonText = NewType("JsonText", str)
JsonNumber = NewType("JsonNumber", int)
type Json = None | bool | JsonNumber | JsonText | list[Json] | dict[JsonKey, Json]
type Fields = dict[JsonKey, Json]
Verb = Literal["is", "are each"]
Said = Literal["refuses a site", "cannot read it"]

GIT = ("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null")
READ, WRITE, EDIT, MULTI_EDIT, BASH = map(ToolName, ("Read", "Write", "Edit", "MultiEdit", "Bash"))
FINDING = "a sleep the record refuses\n"
RULE_SOURCE = "the determinism page"
STAND_IN = """#!{python} -I
import json, os, sys
asked = sys.argv[2:]
with open({record!r}, "a", encoding="utf-8") as out:
    out.write(json.dumps(asked) + "\\n")
if asked[0] == "--is-test":
    found = [path for path in asked[1:] if os.path.basename(path).startswith("test")]
    sys.stdout.write("".join(path + "\\n" for path in found))
    sys.exit(0 if found else 1)
sys.stdout.write({finding!r})
with open({code!r}, encoding="utf-8") as code:
    sys.exit(int(code.read()))
"""
DECLARED = """schema = 1
[gates.test_determinism]
run = [{gate}, "--strict"]
as_from = "src"
protocol = "determinism-1"
rule_source = "{source}"
"""
RULE = ("guard test-determinism: {paths} {verb} a test. Every test is fully deterministic, because the tests run on "
        "machines of very different power (the determinism page): it reads no clock and waits on no duration (no "
        "sleep, timer, deadline or timeout deciding an outcome) and starts no thread, goroutine or concurrent task. "
        "Inject the clock or the scheduler and drive it step by step. A larger timeout, a retry, a ceiling or a "
        "tolerance argued from machine load is never a fix, in any language or mutation lane.")
REFUSED = ("guard test-determinism: tests this call wrote are refused; the call has run and its writes stand, so "
           "rewrite each now: inject the\nclock or the scheduler and drive it, or drop the process. A row in the "
           "gate's record or its dismissals needs the\nuser's approval.\n\n")
REFUSALS: tuple[tuple[ExitCode, Said], ...] = ((ExitCode(1), "refuses a site"), (ExitCode(2), "cannot read it"))
SILENT = Answer(0, Prose(""), Prose(""))


class Case(unittest.TestCase):
    """A case with a work tree declaring the stand-in gate, a switch folder turning off every rule but this one, and
    a home holding a machine file."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.off = self.root / "off"
        self.off.mkdir()
        for rule in (KIT / "rules").glob("[!_]*.py"):
            if rule.name != OWN:
                (self.off / rule.stem.replace("_", "-")).touch()
        self.home = machine_home(self)
        self.record = self.root / "seen.jsonl"
        self.code = self.root / "code"
        self.exits(ExitCode(0))
        self.gate = self.root / "gate" / "determinism"
        self.gate.parent.mkdir()
        _ = self.gate.write_text(STAND_IN.format(python=sys.executable, record=str(self.record), finding=FINDING,
                                                 code=str(self.code)), encoding="utf-8")
        self.gate.chmod(0o755)
        self.repo = self.tree(Name("repo"))
        _ = self.write(self.repo / ".claude" / "guard.toml",
                       Source(DECLARED.format(gate=json.dumps(str(self.gate)), source=RULE_SOURCE)))
        self.src = self.repo / "src"
        self.test = self.write(self.src / "pkg" / "test_mod.py", Source("x = 1\n"))
        self.plain = self.write(self.src / "pkg" / "mod.py", Source("x = 1\n"))

    def tree(self, name: Name) -> Path:
        """A fresh git work tree ``name`` in the case's folder."""
        folder = self.root / name
        folder.mkdir()
        _ = subprocess.run([*GIT, "-C", str(folder), "init", "-q"], check=True, capture_output=True, timeout=30)
        return folder

    def exits(self, code: ExitCode) -> None:
        """Have the stand-in exit ``code`` for a handed file from now on."""
        _ = self.code.write_text(str(code), encoding="utf-8")

    def write(self, path: Path, text: Source) -> Path:
        """Write ``text`` to ``path``, its folders made; the path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")
        return path

    def seen(self) -> list[list[Word]]:
        """The argv of every run of the stand-in after its run words, in order."""
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines()]

    def answered(self, tool: ToolName, given: Fields, response: Fields | None = None,
                 name: HookEvent = "PostToolUse") -> Answer:
        """The dispatcher's answer to an event ``name`` of a ``tool`` call asking ``given``, its response
        ``response`` where one is given, run in the work tree."""
        env = {VarName("HOME"): ShellText(str(self.home))}
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), self.home)
        fields: Fields = {JsonKey("hook_event_name"): JsonText(name), JsonKey("session_id"): JsonText("s"),
                          JsonKey("tool_name"): JsonText(tool), JsonKey("tool_input"): given,
                          JsonKey("cwd"): JsonText(str(self.repo))}
        if response is not None:
            fields[JsonKey("tool_response")] = response
        return answer(EventBytes(json.dumps(fields).encode()), settings)

    def file(self, tool: ToolName, path: Path) -> Answer:
        """The answer after a ``tool`` call naming the file ``path``."""
        return self.answered(tool, {JsonKey("file_path"): JsonText(str(path))})

    def bash(self, paths: list[Path], command: ShellText = ShellText("true")) -> Answer:
        """The answer after a Bash call running ``command`` that changed ``paths``, its diff listing them all."""
        diff: Fields = {JsonKey("changedFiles"): [JsonText(str(path)) for path in paths],
                        JsonKey("moreFiles"): JsonNumber(0)}
        return self.answered(BASH, {JsonKey("command"): JsonText(command)}, {JsonKey("bashEditDiff"): diff})

    def noted(self, paths: Prose, verb: Verb = "is") -> Answer:
        """The answer giving the rule as a note naming ``paths``."""
        said = {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": RULE.format(paths=paths, verb=verb)}}
        return Answer(0, Prose(json.dumps(said) + "\n"), Prose(""))

    def item(self, path: Path, verb: Said) -> Prose:
        """A refusal's line for the test ``path`` the gate answered ``verb`` for."""
        return Prose(f"  * {path}: the gate {shlex.join([str(self.gate), '--strict'])} {verb}, its rule at "
                     f"{RULE_SOURCE}:\n      {FINDING.rstrip()}")


class Writes(Case):
    """The tests a call wrote are asked of the gate once and handed one by one; a finding or an unreadable answer
    refuses, a clean answer notes them."""

    def made(self, count: Count) -> list[Path]:
        """``count`` tests under src, each holding its own text."""
        return [self.write(self.src / "pkg" / f"test_{index:02}.py", Source(f"v = {index}\n")) for index in range(count)]

    def test_a_written_test_is_refused_on_a_finding_or_an_unreadable_answer_with_its_text(self) -> None:
        for code, verb in REFUSALS:
            with self.subTest(code=code):
                self.exits(code)
                got = self.file(WRITE, self.test)
                self.assertEqual((got.status, got.stdout), (2, ""))
                self.assertEqual(got.stderr, REFUSED + self.item(self.test, verb) + "\n")

    def test_each_edit_tool_hands_its_test_and_notes_it_on_a_clean_answer(self) -> None:
        for tool in (WRITE, EDIT, MULTI_EDIT):
            with self.subTest(tool=tool):
                self.record.unlink(missing_ok=True)
                self.assertEqual(self.file(tool, self.test), self.noted(Prose("pkg/test_mod.py")))
                self.assertEqual(self.seen(), [["--is-test", "pkg/test_mod.py"],
                                               ["--file", str(self.test), "--as", "pkg/test_mod.py"]])

    def test_a_written_non_test_passes_with_no_note(self) -> None:
        self.exits(ExitCode(1))
        self.assertEqual(self.file(WRITE, self.plain), SILENT)
        self.assertEqual(self.seen(), [["--is-test", "pkg/mod.py"]])

    def test_a_bash_calls_changed_tests_are_asked_once_and_handed_one_by_one(self) -> None:
        first, second = self.made(Count(2))
        got = self.bash([first, self.plain, second], ShellText(f"cat {self.test}"))
        self.assertEqual(got, self.noted(Prose("pkg/test_00.py, pkg/test_01.py"), "are each"))
        self.assertEqual(self.seen(), [["--is-test", "pkg/test_00.py", "pkg/mod.py", "pkg/test_01.py"],
                                       ["--file", str(first), "--as", "pkg/test_00.py"],
                                       ["--file", str(second), "--as", "pkg/test_01.py"]])

    def test_every_finding_and_unreadable_answer_is_refused_together(self) -> None:
        paths = self.made(Count(2))
        for code, verb in REFUSALS:
            with self.subTest(code=code):
                self.exits(code)
                got = self.bash(paths)
                self.assertEqual(got.status, 2)
                self.assertEqual(got.stderr, REFUSED + "\n".join(self.item(path, verb) for path in paths) + "\n")

    def test_five_changed_tests_refuse_the_fifth_as_unchecked(self) -> None:
        paths = self.made(Count(FILES_PER_RULE + 1))
        got = self.bash(paths)
        self.assertEqual(got.status, 2)
        self.assertEqual([seen[1] for seen in self.seen()[1:]], [str(path) for path in paths[:FILES_PER_RULE]])
        fifth = shlex.join([str(self.gate), "--strict", "--file", str(paths[-1]), "--as", f"pkg/{paths[-1].name}"])
        self.assertEqual(got.stderr, REFUSED + f"  * 1 more tests this call wrote stand unchecked, past the "
                         f"{FILES_PER_RULE} a call hands its gate; judge each with\n    its gate, the first 10 here:\n"
                         f"      (cd {self.repo} && {fifth})\n")

    def test_the_first_ten_unchecked_tests_are_named(self) -> None:
        paths = self.made(Count(FILES_PER_RULE + 11))
        got = self.bash(paths)
        self.assertEqual(got.status, 2)
        self.assertIn("  * 11 more tests", got.stderr)
        self.assertIn(f"--file {paths[-2]} ", got.stderr)
        self.assertNotIn(f"--file {paths[-1]} ", got.stderr)

    def test_a_deleted_test_and_one_outside_as_from_are_not_asked(self) -> None:
        self.exits(ExitCode(1))
        folder = self.src / "test_folder.py"
        folder.mkdir()
        outside = self.write(self.repo / "test_top.py", Source("x = 1\n"))
        self.assertEqual(self.bash([self.src / "test_gone.py", folder, outside]), SILENT)
        self.assertEqual(self.seen(), [])


class Reads(Case):
    """The tests a Read or a Bash call that wrote no test names get the rule as a note, and are never handed."""

    def test_a_read_of_a_test_is_noted_and_of_a_non_test_silent(self) -> None:
        self.exits(ExitCode(1))
        self.assertEqual(self.file(READ, self.test), self.noted(Prose("pkg/test_mod.py")))
        self.assertEqual(self.file(READ, self.plain), SILENT)
        self.assertEqual(self.file(READ, Path("src/pkg/test_mod.py")), SILENT)
        self.assertEqual(self.seen(), [["--is-test", "pkg/test_mod.py"], ["--is-test", "pkg/mod.py"]])

    def test_a_bash_command_naming_a_test_by_a_path_is_noted(self) -> None:
        self.exits(ExitCode(1))
        asks = [("cat src/pkg/test_mod.py", ["pkg/test_mod.py"]),
                ("cd src/pkg && cat test_mod.py", ["pkg", "pkg/test_mod.py"]),
                ("cd src/pkg && cat ../pkg/test_mod.py", ["pkg", "pkg/test_mod.py"]),
                (f"cd src && cat {self.test}", ["pkg/test_mod.py"])]
        for command, asked in asks:
            with self.subTest(command=command):
                self.record.unlink(missing_ok=True)
                self.assertEqual(self.bash([self.plain], ShellText(command)), self.noted(Prose("pkg/test_mod.py")))
                self.assertEqual(self.seen(), [["--is-test", "pkg/mod.py"], ["--is-test", *asked]])

    def test_a_word_that_is_no_path_or_names_nothing_is_silent(self) -> None:
        self.exits(ExitCode(1))
        _ = self.write(self.src / "tests" / "test_a.py", Source("x = 1\n"))
        for command in ("cd src && ls tests", "cat src/pkg/test_gone.py", 'cd "$(pwd)/src/pkg" && cat test_mod.py'):
            with self.subTest(command=command):
                self.assertEqual(self.bash([], ShellText(command)), SILENT)
        self.assertEqual(self.seen(), [])

    def test_a_project_with_no_gate_is_silent(self) -> None:
        self.exits(ExitCode(1))
        other = self.tree(Name("other"))
        path = self.write(other / "src" / "test_mod.py", Source("x = 1\n"))
        self.assertEqual(self.file(WRITE, path), SILENT)
        self.assertEqual(self.file(READ, path), SILENT)
        self.assertEqual(self.bash([path], ShellText(f"cat {path}")), SILENT)
        self.assertEqual(self.seen(), [])

    def test_no_other_event_or_tool_is_judged(self) -> None:
        self.exits(ExitCode(1))
        given: Fields = {JsonKey("file_path"): JsonText(str(self.test))}
        self.assertEqual(self.answered(WRITE, given, name="PreToolUse"), SILENT)
        self.assertEqual(self.answered(ToolName("Glob"), given), SILENT)
        self.assertEqual(self.answered(READ, {}), SILENT)
        self.assertEqual(self.answered(READ, {JsonKey("file_path"): None}), SILENT)
        self.assertEqual(self.seen(), [])

    def test_a_read_whose_input_is_no_object_is_silent(self) -> None:
        env = {VarName("HOME"): ShellText(str(self.home))}
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), self.home)
        raw = json.dumps({"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": READ,
                          "tool_input": str(self.test), "cwd": str(self.repo)})
        self.assertEqual(answer(EventBytes(raw.encode()), settings), SILENT)
        self.assertEqual(self.seen(), [])

    def test_tests_of_two_gates_get_a_sentence_each_citing_its_rule(self) -> None:
        other = self.tree(Name("other"))
        _ = self.write(other / ".claude" / "guard.toml",
                       Source(DECLARED.format(gate=json.dumps(str(self.gate)), source="the other page")))
        theirs = self.write(other / "src" / "test_b.py", Source("x = 1\n"))
        second = RULE.removeprefix("guard test-determinism: ").format(paths="test_b.py", verb="is")
        said = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": RULE.format(
            paths="pkg/test_mod.py", verb="is") + "\n" + second.replace(RULE_SOURCE, "the other page")}}
        self.assertEqual(self.bash([self.test, theirs]), Answer(0, Prose(json.dumps(said) + "\n"), Prose("")))


if __name__ == "__main__":
    unittest.main()
