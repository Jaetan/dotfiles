"""The precise-hints rule, through the dispatcher with every other rule of the kit switched off: before an Edit, a Write
or a MultiEdit of a Python file under the gate's as_from, the text after it is handed to the declared hints gate from a
temporary file, gone after the run; after a Bash call, each file it changed where it stands. The gate's exit 1 or 2
refuses with its text and its rule's place, its exit 0 passes; an edit the tool cannot apply, a file outside as_from
or of another suffix, a deleted file, a call changing nothing and a project with no gate pass unjudged; a Bash call's
files past FILES_PER_RULE are refused as unchecked.

Each case has a git work tree in a temporary folder declaring a stand-in gate in its .claude/guard.toml, as_from its
src folder; the stand-in records its argv and the text of the file it is handed, prints a finding and exits with the
code the case's code file holds."""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher.core import EventBytes, ExitStatus, FolderPath, HookEvent, Settings, ToolName, answer
from dispatcher.gate_run import FILES_PER_RULE, ExitCode
from dispatcher.model import ReadSettings
from dispatcher.tree import Count, ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_dispatcher import machine_home

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = "precise_hints.py"
Source = NewType("Source", str)
Word = NewType("Word", str)
Name = NewType("Name", str)
JsonKey = NewType("JsonKey", str)
JsonText = NewType("JsonText", str)
JsonNumber = NewType("JsonNumber", int)
type Json = None | bool | JsonNumber | JsonText | list[Json] | dict[JsonKey, Json]
type Fields = dict[JsonKey, Json]
type Ask = tuple[ToolName, Fields, Source]

GIT = ("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null")
WRITE, EDIT, MULTI_EDIT, BASH = ToolName("Write"), ToolName("Edit"), ToolName("MultiEdit"), ToolName("Bash")
FINDING = "a hint the record refuses\n"
RULE_SOURCE = "the hints page"
STAND_IN = """#!{python} -I
import json, sys
given = sys.argv[sys.argv.index("--file") + 1]
with open(given, encoding="utf-8") as handed:
    text = handed.read()
with open({record!r}, "a", encoding="utf-8") as out:
    out.write(json.dumps({{"argv": sys.argv[1:], "text": text}}) + "\\n")
sys.stdout.write({finding!r})
with open({code!r}, encoding="utf-8") as code:
    sys.exit(int(code.read()))
"""
DECLARED = """schema = 1
[gates.precise_hints]
run = [{gate}, "--strict"]
as_from = "src"
protocol = "hints-1"
suffixes = [".py"]
rule_source = "{source}"
"""
BEFORE = Source("a = 1\nb = 1\n# é\n")


class Seen(NamedTuple):
    """One run of the stand-in: its argv after the script, and the text of the file it was handed."""

    argv: list[Word]
    text: Source


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
        self.gate = self.root / "gate" / "hints"
        self.gate.parent.mkdir()
        _ = self.gate.write_text(STAND_IN.format(python=sys.executable, record=str(self.record), finding=FINDING,
                                                 code=str(self.code)), encoding="utf-8")
        self.gate.chmod(0o755)
        self.repo = self.tree(Name("repo"))
        _ = self.write(self.repo / ".claude" / "guard.toml",
                       Source(DECLARED.format(gate=json.dumps(str(self.gate)), source=RULE_SOURCE)))
        self.src = self.repo / "src"

    def tree(self, name: Name) -> Path:
        """A fresh git work tree ``name`` in the case's folder."""
        folder = self.root / name
        folder.mkdir()
        _ = subprocess.run([*GIT, "-C", str(folder), "init", "-q"], check=True, capture_output=True, timeout=30)
        return folder

    def exits(self, code: ExitCode) -> None:
        """Have the stand-in exit ``code`` from now on."""
        _ = self.code.write_text(str(code), encoding="utf-8")

    def write(self, path: Path, text: Source) -> Path:
        """Write ``text`` to ``path``, its folders made; the path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")
        return path

    def seen(self) -> list[Seen]:
        """Every run of the stand-in, in order."""
        if not self.record.exists():
            return []
        rows = [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines()]
        return [Seen(row["argv"], row["text"]) for row in rows]

    def answered(self, tool: ToolName, name: HookEvent, given: Fields,
                 response: Fields | None = None) -> tuple[ExitStatus, Prose]:
        """The dispatcher's exit status and stderr for an event ``name`` of a ``tool`` call asking ``given``, its
        response ``response`` where one is given, run in the work tree."""
        env = {VarName("HOME"): ShellText(str(self.home))}
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), self.home)
        fields: Fields = {JsonKey("hook_event_name"): JsonText(name), JsonKey("session_id"): JsonText("s"),
                          JsonKey("tool_name"): JsonText(tool), JsonKey("tool_input"): given,
                          JsonKey("cwd"): JsonText(str(self.repo))}
        if response is not None:
            fields[JsonKey("tool_response")] = response
        got = answer(EventBytes(json.dumps(fields).encode()), settings)
        return got.status, got.stderr

    def edit(self, tool: ToolName, given: Fields) -> tuple[ExitStatus, Prose]:
        """The answer before a ``tool`` call asking ``given``."""
        return self.answered(tool, "PreToolUse", given)

    def bash(self, paths: list[Path]) -> tuple[ExitStatus, Prose]:
        """The answer after a Bash call that changed ``paths``, its diff listing them all."""
        diff: Fields = {JsonKey("changedFiles"): [JsonText(str(path)) for path in paths],
                        JsonKey("moreFiles"): JsonNumber(0)}
        return self.answered(BASH, "PostToolUse", {JsonKey("command"): JsonText("true")},
                             {JsonKey("bashEditDiff"): diff})


class Edits(Case):
    """Each edit tool hands the gate the text after it, as the file's REL, and takes the gate's answer."""

    def asks(self) -> list[Ask]:
        """Each edit tool's input for src/pkg/mod.py, which holds BEFORE, and the text it leaves there."""
        path = JsonText(str(self.write(self.src / "pkg" / "mod.py", BEFORE)))
        return [
            (WRITE, {"file_path": path, "content": "c = 'é'\n"}, Source("c = 'é'\n")),
            (EDIT, {"file_path": path, "old_string": "b = 1", "new_string": "b: int = 2"},
             Source("a = 1\nb: int = 2\n# é\n")),
            (EDIT, {"file_path": path, "old_string": "1", "new_string": "9", "replace_all": True},
             Source("a = 9\nb = 9\n# é\n")),
            (MULTI_EDIT, {"file_path": path, "edits": [{"old_string": "a = 1", "new_string": "a = 7"},
                                                       {"old_string": "a = 7\nb", "new_string": "c"}]},
             Source("c = 1\n# é\n")),
        ]

    def test_each_edit_is_handed_the_text_after_it_as_its_rel(self) -> None:
        for tool, given, after in self.asks():
            with self.subTest(tool=tool, given=given):
                self.record.unlink(missing_ok=True)
                self.assertEqual(self.edit(tool, given), (0, ""))
                [seen] = self.seen()
                self.assertEqual((seen.argv[:2], seen.argv[3:]), (["--strict", "--file"], ["--as", "pkg/mod.py"]))
                self.assertEqual(seen.text, after)
                self.assertEqual(Path(seen.argv[2]).suffix, ".py")
                self.assertFalse(Path(seen.argv[2]).exists())
                self.assertEqual((self.src / "pkg" / "mod.py").read_text(encoding="utf-8"), BEFORE)

    def test_a_finding_refuses_naming_the_file_the_gate_its_rule_and_its_text(self) -> None:
        self.exits(ExitCode(1))
        for tool, given, _ in self.asks():
            with self.subTest(tool=tool, given=given):
                status, stderr = self.edit(tool, given)
                self.assertEqual(status, 2)
                self.assertIn("guard precise-hints: the hints gate refuses the text this edit would leave, so the edit "
                              f"is not made:\n\n  * {self.src / 'pkg' / 'mod.py'}: the gate "
                              f"{shlex.join([str(self.gate), '--strict'])} refuses a hint, its rule at {RULE_SOURCE}:\n"
                              f"      {FINDING.rstrip()}\n\nType each hint it names precisely, as its rule says, and "
                              "edit again.", stderr)

    def test_an_unreadable_answer_refuses(self) -> None:
        self.exits(ExitCode(2))
        for tool, given, _ in self.asks():
            with self.subTest(tool=tool, given=given):
                status, stderr = self.edit(tool, given)
                self.assertEqual(status, 2)
                self.assertIn(f"--strict cannot read it, its rule at {RULE_SOURCE}:\n      {FINDING.rstrip()}\n", stderr)

    def test_an_edit_the_tool_cannot_apply_passes_unjudged(self) -> None:
        self.exits(ExitCode(1))
        path = JsonText(str(self.write(self.src / "mod.py", BEFORE)))
        absent = JsonText(str(self.src / "absent.py"))
        asks: list[tuple[ToolName, Fields]] = [
            (EDIT, {"file_path": path, "old_string": "z = 1", "new_string": "z = 2"}),
            (EDIT, {"file_path": path, "old_string": "= 1", "new_string": "= 2"}),
            (EDIT, {"file_path": path, "old_string": "", "new_string": "z = 2", "replace_all": True}),
            (EDIT, {"file_path": path, "old_string": 1, "new_string": "z = 2"}),
            (EDIT, {"file_path": path, "old_string": "a = 1", "new_string": None}),
            (EDIT, {"file_path": absent, "old_string": "a = 1", "new_string": "a = 2"}),
            (MULTI_EDIT, {"file_path": path, "edits": [{"old_string": "a = 1", "new_string": "a = 2"},
                                                      {"old_string": "z", "new_string": "y"}]}),
            (MULTI_EDIT, {"file_path": path, "edits": ["a = 1"]}),
            (MULTI_EDIT, {"file_path": path}),
            (WRITE, {"file_path": path}),
            (WRITE, {"file_path": path, "content": 5}),
        ]
        for tool, given in asks:
            with self.subTest(tool=tool, given=given):
                self.assertEqual(self.edit(tool, given), (0, ""))
        self.assertEqual(self.seen(), [])

    def test_a_file_outside_as_from_or_of_another_suffix_is_not_handed(self) -> None:
        self.exits(ExitCode(1))
        for path in (self.repo / "top.py", self.src / "notes.txt"):
            with self.subTest(path=path):
                given: Fields = {JsonKey("file_path"): JsonText(str(path)), JsonKey("content"): JsonText("x = 1\n")}
                self.assertEqual(self.edit(WRITE, given), (0, ""))
        self.assertEqual(self.seen(), [])

    def test_an_edit_is_judged_before_it_runs_alone(self) -> None:
        self.exits(ExitCode(1))
        path = self.write(self.src / "mod.py", BEFORE)
        given: Fields = {JsonKey("file_path"): JsonText(str(path)), JsonKey("content"): JsonText("c = 3\n")}
        self.assertEqual(self.answered(WRITE, "PostToolUse", given), (0, ""))
        self.assertEqual(self.answered(BASH, "PreToolUse", {JsonKey("command"): JsonText("true")}), (0, ""))
        self.assertEqual(self.seen(), [])


class Writes(Case):
    """After a Bash call, each file it changed is handed where it stands, the files past the bound refused."""

    def files(self, count: Count) -> list[Path]:
        """``count`` Python files under src, each holding its own text."""
        return [self.write(self.src / "pkg" / f"m{index:02}.py", Source(f"v{index} = {index}\n")) for index in range(count)]

    def test_a_calls_changed_files_are_handed_where_they_stand(self) -> None:
        paths = self.files(Count(2))
        self.assertEqual(self.bash(paths), (0, ""))
        self.assertEqual(self.seen(), [Seen([Word("--strict"), Word("--file"), Word(str(path)), Word("--as"),
                                             Word(str(path.relative_to(self.src)))], Source(path.read_text(encoding="utf-8")))
                                       for path in paths])

    def test_every_finding_and_unreadable_answer_is_refused_together(self) -> None:
        paths = self.files(Count(2))
        gate = shlex.join([str(self.gate), "--strict"])
        for code, verb in ((ExitCode(1), "refuses a hint"), (ExitCode(2), "cannot read it")):
            with self.subTest(code=code):
                self.exits(code)
                status, stderr = self.bash(paths)
                self.assertEqual(status, 2)
                self.assertIn("guard precise-hints: files this call wrote are refused; the call has run and its writes "
                              "stand, so fix each before building on it:\n\n"
                              + "\n".join(f"  * {path}: the gate {gate} {verb}, its rule at {RULE_SOURCE}:\n"
                                          f"      {FINDING.rstrip()}" for path in paths), stderr)

    def test_five_changed_files_refuse_the_fifth_as_unchecked(self) -> None:
        paths = self.files(Count(FILES_PER_RULE + 1))
        status, stderr = self.bash(paths)
        self.assertEqual(status, 2)
        self.assertEqual([seen.argv[2] for seen in self.seen()], [str(path) for path in paths[:FILES_PER_RULE]])
        fifth = shlex.join([str(self.gate), "--strict", "--file", str(paths[-1]), "--as", f"pkg/{paths[-1].name}"])
        self.assertIn(f"  * 1 more files this call wrote stand unchecked, past the {FILES_PER_RULE} a call hands its "
                      "gate; judge each with\n    its gate, the first 10 here:\n"
                      f"      (cd {self.repo} && {fifth})", stderr)

    def test_the_first_ten_unchecked_files_are_named(self) -> None:
        paths = self.files(Count(FILES_PER_RULE + 11))
        status, stderr = self.bash(paths)
        self.assertEqual(status, 2)
        self.assertIn("  * 11 more files", stderr)
        self.assertIn(f"--file {paths[-2]} ", stderr)
        self.assertNotIn(f"--file {paths[-1]} ", stderr)

    def test_files_left_out_are_not_counted_against_the_bound(self) -> None:
        paths = self.files(FILES_PER_RULE)
        outside = [self.write(self.repo / "top.py", BEFORE), self.write(self.src / "notes.txt", BEFORE)]
        self.assertEqual(self.bash([*outside, *paths]), (0, ""))
        self.assertEqual(len(self.seen()), FILES_PER_RULE)

    def test_a_deleted_file_and_a_call_changing_nothing_pass(self) -> None:
        self.exits(ExitCode(1))
        folder = self.src / "pkg.py"
        folder.mkdir(parents=True)
        for paths in ([self.src / "gone.py"], [folder], []):
            with self.subTest(paths=paths):
                self.assertEqual(self.bash(paths), (0, ""))
        self.assertEqual(self.answered(BASH, "PostToolUse", {JsonKey("command"): JsonText("true")}), (0, ""))
        self.assertEqual(self.seen(), [])

    def test_a_project_with_no_gate_passes(self) -> None:
        self.exits(ExitCode(1))
        other = self.tree(Name("other"))
        path = self.write(other / "src" / "mod.py", BEFORE)
        given: Fields = {JsonKey("file_path"): JsonText(str(path)), JsonKey("content"): JsonText("x = 1\n")}
        self.assertEqual(self.edit(WRITE, given), (0, ""))
        self.assertEqual(self.bash([path]), (0, ""))
        self.assertEqual(self.seen(), [])


if __name__ == "__main__":
    unittest.main()
