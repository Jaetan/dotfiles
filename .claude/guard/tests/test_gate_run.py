"""dispatcher/gate_run.py as its docstring states it: a file handed to a gate and its answer read, the paths a gate
names as tests, a path's REL, the files a call changed and the files a rule may hand. Every gate is a stand-in script
written to the case's temporary folder, recording its argv, its cwd and its stdin and answering a chosen exit and
text; no case runs a project's real gate. The case that never ends replaces GATE_TIMEOUT, and the git cases run git
in a work tree of their own."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict
from unittest import mock

from declarations.lookup import Gate, Tree, gate
from declarations.schema import GateEntry, PathText, Protocol, Suffix, Word
from dispatcher import gate_run
from dispatcher.core import Event, EventBytes, ToolName, payload_of
from dispatcher.gate_run import (FILES_PER_RULE, Answer, FindingVerb, Limited, Noun, RelPath, changed, command, handed,
                                 limited, rel, said, unchecked)
from dispatcher.model import ReadSettings
from dispatcher.tree import Count, ReaderPath, Seconds
from dispatcher.verdict import KitFault, Prose

JsonKey = NewType("JsonKey", str)
JsonText = NewType("JsonText", str)
JsonNumber = NewType("JsonNumber", int)
type Json = None | bool | JsonNumber | JsonText | list[Json] | dict[JsonKey, Json]
GateText = NewType("GateText", str)
ExitCode = NewType("ExitCode", int)
GitOutput = NewType("GitOutput", bytes)
SeenRow = TypedDict("SeenRow", {"argv": list[Word], "cwd": PathText, "stdin": GateText})

READING = ReadSettings(ReaderPath("/nonexistent/shell-reader"), Seconds(5.0), {})
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
STAND_IN = """#!{python} -I
import json, os, sys, time
seen = {{"argv": sys.argv, "cwd": os.getcwd(), "stdin": sys.stdin.read()}}
with open({record!r}, "a", encoding="utf-8") as out:
    out.write(json.dumps(seen) + "\\n")
sys.stdout.write({text!r})
sys.stdout.flush()
sys.stderr.write({error!r})
if {endless!r}:
    time.sleep(600)
sys.exit({code!r})
"""


class Seen(NamedTuple):
    """What a stand-in gate recorded of one run: its argv, its cwd and what it read on stdin."""

    argv: list[Word]
    cwd: Path
    stdin: GateText


class Gates(unittest.TestCase):
    """A case with a temporary folder holding a project root, a gate cwd inside it, and a stand-in gate."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(os.path.realpath(scratch.name)) / "project"
        self.cwd = self.root / "tools"
        self.cwd.mkdir(parents=True)
        self.record = self.root / "seen.jsonl"
        self.file = self.root / "pkg" / "mod.py"

    def stand_in(self, code: ExitCode, text: GateText = GateText(""), *, error: GateText = GateText(""),
                 endless: bool = False, protocol: Protocol = "hints-1") -> Gate:
        """A gate whose command is a stand-in at `gates/stand-in` under the cwd, declared with its first word relative
        and resolved as the declarations resolve it, answering ``code`` with ``text`` on stdout and ``error`` on
        stderr, or never ending where ``endless``."""
        script = self.cwd / "gates" / "stand-in"
        script.parent.mkdir(exist_ok=True)
        _ = script.write_text(STAND_IN.format(python=sys.executable, record=str(self.record), text=text, error=error,
                                              endless=endless, code=code), encoding="utf-8")
        script.chmod(0o755)
        entry = GateEntry((Word("gates/stand-in"), Word("--strict")), PathText("tools"), None, protocol,
                          Prose("tools/rule.md"), (Suffix(".py"),))
        return gate(entry, Tree(self.root, self.root), self.root)

    def seen(self) -> list[Seen]:
        """Every run the stand-in recorded, in order."""
        if not self.record.exists():
            return []
        rows: list[SeenRow] = [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines()]
        return [Seen(row["argv"], Path(row["cwd"]), row["stdin"]) for row in rows]


class Run(Gates):
    def run_gate(self, held: Gate) -> Answer:
        """The answer of ``held`` handed the case's file as `pkg/mod.py`."""
        return gate_run.run(held, self.file, RelPath("pkg/mod.py"))

    def test_exit_0_is_clean(self) -> None:
        self.assertEqual(self.run_gate(self.stand_in(ExitCode(0), GateText("hints: 22, every one recorded\n"))),
                         Answer("clean", Prose("hints: 22, every one recorded\n")))

    def test_exit_1_is_a_finding_with_the_gates_text(self) -> None:
        self.assertEqual(self.run_gate(self.stand_in(ExitCode(1), GateText("pkg/mod.py:3: Any\n"))),
                         Answer("finding", Prose("pkg/mod.py:3: Any\n")))

    def test_exit_2_is_unreadable_with_the_gates_text(self) -> None:
        self.assertEqual(self.run_gate(self.stand_in(ExitCode(2), GateText("cannot read pkg/mod.py\n"))),
                         Answer("unreadable", Prose("cannot read pkg/mod.py\n")))

    def test_the_text_holds_stderr_with_stdout(self) -> None:
        answer = self.run_gate(self.stand_in(ExitCode(1), GateText("out\n"), error=GateText("err\n")))
        self.assertEqual(answer, Answer("finding", Prose("out\nerr\n")))

    def test_any_other_exit_is_a_kit_fault_naming_the_command(self) -> None:
        held = self.stand_in(ExitCode(3), GateText("broken record\n"))
        with self.assertRaises(KitFault) as caught:
            _ = self.run_gate(held)
        self.assertEqual(caught.exception.reason, f"the gate {held.run[0]} --strict exited 3: broken record")

    def test_a_silent_other_exit_is_named_silent(self) -> None:
        with self.assertRaises(KitFault) as caught:
            _ = self.run_gate(self.stand_in(ExitCode(5), GateText("\n")))
        self.assertTrue(caught.exception.reason.endswith("exited 5: silent"), caught.exception.reason)

    def test_a_gate_that_never_ends_is_a_kit_fault(self) -> None:
        held = self.stand_in(ExitCode(0), endless=True)
        with (mock.patch.object(gate_run, "GATE_TIMEOUT", Seconds(0.5)),
              mock.patch("dispatcher.gate_run.subprocess.run", wraps=subprocess.run) as ran,
              self.assertRaises(KitFault) as caught):
            _ = self.run_gate(held)
        self.assertEqual(caught.exception.reason, f"the gate {held.run[0]} --strict outran its 0.5 s limit")
        self.assertEqual(ran.call_args.kwargs["timeout"], 0.5)

    def test_a_gate_that_cannot_start_is_a_kit_fault(self) -> None:
        held = self.stand_in(ExitCode(0))._replace(run=(Word(str(self.cwd / "missing")),))
        with self.assertRaises(KitFault) as caught:
            _ = self.run_gate(held)
        self.assertEqual(caught.exception.reason,
                         f"the gate {self.cwd / 'missing'} cannot start: No such file or directory")

    def test_a_long_output_is_cut_at_64_kib(self) -> None:
        answer = self.run_gate(self.stand_in(ExitCode(1), GateText("x" * (64 << 10) + "past the cut")))
        self.assertEqual(answer.text, "x" * (64 << 10) + "\n(the gate's output is cut here, at 64 KiB)")

    def test_a_cut_through_a_character_is_replaced(self) -> None:
        answer = self.run_gate(self.stand_in(ExitCode(1), GateText("x" * ((64 << 10) - 1) + "é")))
        self.assertEqual(answer.text, "x" * ((64 << 10) - 1) + "�\n(the gate's output is cut here, at 64 KiB)")

    def test_an_output_of_64_kib_is_whole(self) -> None:
        answer = self.run_gate(self.stand_in(ExitCode(1), GateText("x" * (64 << 10))))
        self.assertEqual(answer.text, "x" * (64 << 10))

    def test_the_file_and_its_rel_follow_the_absolute_command_in_its_cwd_with_stdin_empty(self) -> None:
        held = self.stand_in(ExitCode(0))
        given, keeper = os.pipe()
        _ = os.write(keeper, b"the dispatcher's own stdin")
        os.close(keeper)
        saved = os.dup(0)
        os.dup2(given, 0)
        os.close(given)
        try:
            _ = self.run_gate(held)
        finally:
            os.dup2(saved, 0)
            os.close(saved)
        script = Word(str(self.cwd / "gates" / "stand-in"))
        argv = [script, Word("--strict"), Word("--file"), Word(str(self.file)), Word("--as"), Word("pkg/mod.py")]
        self.assertEqual(self.seen(), [Seen(argv, self.cwd, GateText(""))])

    def test_the_limits_are_as_decided(self) -> None:
        self.assertEqual((gate_run.GATE_TIMEOUT, gate_run.OUTPUT_LIMIT, FILES_PER_RULE), (2.0, 64 << 10, 4))


class Tests(Gates):
    def asked(self) -> list[Path]:
        """Three paths a determinism gate is asked about."""
        return [self.root / "tests" / "test_a.py", self.root / "pkg" / "b.py", self.root / "tests" / "test_c.py"]

    def test_exit_0_answers_the_asked_paths_it_printed_in_the_order_asked(self) -> None:
        a, _, c = self.asked()
        held = self.stand_in(ExitCode(0), GateText("tests/test_c.py\nunasked.py\ntests/test_a.py\n"),
                             protocol="determinism-1")
        self.assertEqual(gate_run.tests(held, self.asked()), (a, c))

    def test_a_path_printed_whole_names_no_test(self) -> None:
        """The gate reads a path relative to its project, as aletheia's does: an absolute line it prints is no answer."""
        a, _, c = self.asked()
        held = self.stand_in(ExitCode(0), GateText(f"{c}\n{a}\n"), protocol="determinism-1")
        self.assertEqual(gate_run.tests(held, self.asked()), ())

    def test_a_path_outside_as_from_is_not_asked(self) -> None:
        held = self.stand_in(ExitCode(0), GateText("tests/test_a.py\n"), protocol="determinism-1")
        outside = self.root.parent / "elsewhere" / "test_x.py"
        self.assertEqual(gate_run.tests(held, [outside, self.asked()[0]]), (self.asked()[0],))
        script = Word(str(self.cwd / "gates" / "stand-in"))
        self.assertEqual(self.seen(), [Seen([script, Word("--strict"), Word("--is-test"), Word("tests/test_a.py")],
                                            self.cwd, GateText(""))])

    def test_no_path_to_ask_runs_no_gate(self) -> None:
        held = self.stand_in(ExitCode(2), protocol="determinism-1")
        self.assertEqual(gate_run.tests(held, [self.root.parent / "elsewhere" / "test_x.py"]), ())
        self.assertEqual(self.seen(), [])

    def test_the_gate_runs_once_with_every_path(self) -> None:
        held = self.stand_in(ExitCode(1), protocol="determinism-1")
        _ = gate_run.tests(held, self.asked())
        script = Word(str(self.cwd / "gates" / "stand-in"))
        argv = [script, Word("--strict"), Word("--is-test"), Word("tests/test_a.py"), Word("pkg/b.py"),
                Word("tests/test_c.py")]
        self.assertEqual(self.seen(), [Seen(argv, self.cwd, GateText(""))])

    def test_exit_1_answers_none_whatever_it_printed(self) -> None:
        held = self.stand_in(ExitCode(1), GateText("tests/test_a.py\n"), protocol="determinism-1")
        self.assertEqual(gate_run.tests(held, self.asked()), ())

    def test_any_other_exit_is_a_kit_fault(self) -> None:
        held = self.stand_in(ExitCode(2), GateText("cannot read\n"), protocol="determinism-1")
        with self.assertRaises(KitFault) as caught:
            _ = gate_run.tests(held, self.asked())
        self.assertEqual(caught.exception.reason, f"the gate {held.run[0]} --strict exited 2: cannot read")


class Rel(Gates):
    def test_a_path_inside_as_from_is_relative_to_it(self) -> None:
        self.assertEqual(rel(self.stand_in(ExitCode(0)), self.file), "pkg/mod.py")

    def test_a_path_outside_as_from_is_none(self) -> None:
        self.assertIsNone(rel(self.stand_in(ExitCode(0)), self.root.parent / "elsewhere" / "mod.py"))

    def test_a_hints_gate_takes_only_its_suffixes(self) -> None:
        self.assertIsNone(rel(self.stand_in(ExitCode(0)), self.root / "pkg" / "notes.md"))

    def test_a_determinism_gate_takes_every_suffix(self) -> None:
        held = self.stand_in(ExitCode(0), protocol="determinism-1")
        self.assertEqual(rel(held, self.root / "tests" / "cases.toml"), "tests/cases.toml")


class Handing(Gates):
    def test_a_file_is_handed_to_the_gate_as_its_rel(self) -> None:
        held = self.stand_in(ExitCode(0))
        self.assertEqual(handed(held, self.file), (held, RelPath("pkg/mod.py")))

    def test_a_file_of_a_project_with_no_gate_is_not_handed(self) -> None:
        self.assertIsNone(handed(None, self.file))

    def test_a_file_outside_as_from_is_not_handed(self) -> None:
        self.assertIsNone(handed(self.stand_in(ExitCode(0)), self.root.parent / "elsewhere" / "mod.py"))


class Refusals(Gates):
    """The pieces of a gate rule's refusal, the stand-in declared and never run."""

    def test_a_finding_is_said_in_the_rules_words_with_the_gate_its_rule_and_its_text(self) -> None:
        held = self.stand_in(ExitCode(1))
        answer = Answer("finding", Prose("pkg/mod.py:3: Any\npkg/mod.py:4: object\n"))
        self.assertEqual(said(self.file, (held, RelPath("pkg/mod.py")), answer, FindingVerb("refuses a word")),
                         f"  * {self.file}: the gate {held.run[0]} --strict refuses a word, its rule at tools/rule.md:\n"
                         "      pkg/mod.py:3: Any\n      pkg/mod.py:4: object")

    def test_an_unreadable_answer_is_said_as_the_gate_that_cannot_read_it(self) -> None:
        held = self.stand_in(ExitCode(2))
        answer = Answer("unreadable", Prose("cannot read pkg/mod.py\n"))
        self.assertEqual(said(self.file, (held, RelPath("pkg/mod.py")), answer, FindingVerb("refuses a word")),
                         f"  * {self.file}: the gate {held.run[0]} --strict cannot read it, its rule at tools/rule.md:\n"
                         "      cannot read pkg/mod.py")

    def test_a_command_quotes_a_cwd_and_a_path_holding_a_space(self) -> None:
        held = self.stand_in(ExitCode(0))._replace(cwd=self.root / "the tools")
        path = self.root / "my pkg" / "mod.py"
        self.assertEqual(command(path, (held, RelPath("my pkg/mod.py"))),
                         f"(cd '{self.root}/the tools' && {held.run[0]} --strict --file '{path}' --as 'my pkg/mod.py')")

    def test_the_unchecked_are_counted_against_the_bound_and_the_first_ten_named(self) -> None:
        held = self.stand_in(ExitCode(0))
        paths = [self.root / "tests" / f"test_{index}.py" for index in range(11)]
        handed_of = {path: (held, RelPath(f"tests/{path.name}")) for path in paths}
        for count in (1, 10, 11):
            with self.subTest(count=count):
                head = (f"  * {count} more tests this call wrote stand unchecked, past the 4 a call hands its gate; "
                        "judge each with\n    its gate, the first 10 here:")
                lines = [f"      (cd {self.cwd} && {held.run[0]} --strict --file {path} --as tests/{path.name})"
                         for path in paths[:min(count, 10)]]
                self.assertEqual(unchecked(paths[:count], handed_of, Noun("tests")), "\n".join([head, *lines]))


def git(cwd: Path, *words: Word) -> None:
    """Run git in ``cwd``, with no user or system configuration but an identity."""
    _ = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *words],
                       cwd=cwd, env=GIT_ENV, check=True, capture_output=True)


class Changed(unittest.TestCase):
    """A case with a temporary folder holding a git work tree, whose events name it as their cwd."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(os.path.realpath(scratch.name))
        self.root = self.home / "work"
        self.root.mkdir()

    def made(self, tool: ToolName, fields: dict[JsonKey, Json], cwd: Path | None = None) -> Event:
        """A PostToolUse event of a ``tool`` call carrying ``fields``, its cwd the work tree unless ``cwd`` names one."""
        payload: dict[JsonKey, Json] = {JsonKey("hook_event_name"): JsonText("PostToolUse"),
                                        JsonKey("tool_name"): JsonText(tool),
                                        JsonKey("cwd"): JsonText(str(cwd or self.root)), **fields}
        return Event(payload_of(EventBytes(json.dumps(payload).encode())), READING, self.home)

    def bash(self, diff: Json, cwd: Path | None = None) -> Event:
        """A Bash PostToolUse event whose tool_response carries ``diff`` as its bashEditDiff."""
        return self.made(ToolName("Bash"), {"tool_response": {"stdout": "", "bashEditDiff": diff}}, cwd)

    def worked(self) -> None:
        """Make the work tree a repository with a modified, an untracked, a renamed and a deleted file."""
        git(self.root, Word("init"), Word("-q"))
        for name in ("a.py", "b.py", "c.py"):
            _ = (self.root / name).write_text(f"{name}\n", encoding="utf-8")
        git(self.root, Word("add"), Word("."))
        git(self.root, Word("commit"), Word("-q"), Word("-m"), Word("base"))
        _ = (self.root / "a.py").write_text("changed\n", encoding="utf-8")
        _ = (self.root / "new.py").write_text("new\n", encoding="utf-8")
        git(self.root, Word("mv"), Word("b.py"), Word("moved.py"))
        (self.root / "c.py").unlink()

    def test_an_edit_write_or_multiedit_gives_its_file_path(self) -> None:
        for tool in ("Edit", "Write", "MultiEdit"):
            with self.subTest(tool=tool):
                event = self.made(ToolName(tool), {"tool_input": {"file_path": str(self.root / "x.py")}})
                self.assertEqual(changed(event), (self.root / "x.py",))

    def test_an_edit_without_an_absolute_file_path_gives_none(self) -> None:
        for given in ({"file_path": "x.py"}, {"file_path": 3}, {}, "x.py"):
            with self.subTest(given=given):
                self.assertEqual(changed(self.made(ToolName("Edit"), {"tool_input": given})), ())

    def test_another_tool_gives_none(self) -> None:
        event = self.made(ToolName("Read"), {
            "tool_input": {"file_path": str(self.root / "x.py")},
            "tool_response": {"bashEditDiff": {"changedFiles": [str(self.root / "x.py")]}}})
        self.assertEqual(changed(event), ())

    def test_a_bash_call_gives_its_changed_files_each_once_strings_and_absolute_only(self) -> None:
        a, b = str(self.root / "a.py"), str(self.root / "b.py")
        event = self.bash({"files": [], "moreFiles": 0, "changedFiles": [a, "rel/x.py", 3, None, b, a]})
        self.assertEqual(changed(event), (Path(a), Path(b)))

    def test_a_bash_call_that_changed_nothing_gives_none(self) -> None:
        self.worked()
        self.assertEqual(changed(self.bash({"files": [], "moreFiles": 0, "shared": True})), ())
        self.assertEqual(changed(self.made(ToolName("Bash"), {"tool_response": {"stdout": "done"}})), ())
        self.assertEqual(changed(self.made(ToolName("Bash"), {"tool_response": "done"})), ())

    def test_fields_of_the_wrong_type_read_as_none(self) -> None:
        self.worked()
        for diff in ([str(self.root / "a.py")], {"changedFiles": str(self.root / "a.py"), "moreFiles": "2"},
                     {"changedFiles": None, "moreFiles": True}):
            with self.subTest(diff=diff):
                self.assertEqual(changed(self.bash(diff)), ())

    def test_more_files_lists_the_work_tree_changes_but_the_deleted_a_rename_by_its_new_path(self) -> None:
        self.worked()
        event = self.bash({"files": [], "moreFiles": 3, "changedFiles": [str(self.root / "a.py")]})
        self.assertEqual(changed(event), (self.root / "a.py", self.root / "moved.py", self.root / "new.py"))

    def test_more_files_reads_the_work_tree_of_a_cwd_below_its_root(self) -> None:
        self.worked()
        (self.root / "sub").mkdir()
        event = self.bash({"files": [], "moreFiles": 1, "changedFiles": []}, cwd=self.root / "sub")
        self.assertEqual(changed(event), (self.root / "a.py", self.root / "moved.py", self.root / "new.py"))

    def test_more_files_reads_no_git_dir_from_the_environment(self) -> None:
        self.worked()
        other = self.home / "other"
        other.mkdir()
        git(other, Word("init"), Word("-q"))
        _ = (other / "a.py").write_text("changed\n", encoding="utf-8")
        git(other, Word("add"), Word("a.py"))
        git(other, Word("commit"), Word("-q"), Word("-m"), Word("the work tree's a.py, unchanged"))
        event = self.bash({"files": [], "moreFiles": 1, "changedFiles": []})
        _ = event.declaration(self.root)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            self.assertEqual(changed(event), (self.root / "a.py", self.root / "moved.py", self.root / "new.py"))

    def test_more_files_lists_a_copy_by_its_new_path(self) -> None:
        self.worked()
        git(self.root, Word("commit"), Word("-q"), Word("-am"), Word("moved"))
        _ = (self.root / "copy.py").write_text("changed\n", encoding="utf-8")
        _ = (self.root / "a.py").write_text("changed again\n", encoding="utf-8")
        git(self.root, Word("add"), Word("copy.py"), Word("a.py"))
        copies = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "status.renames", "GIT_CONFIG_VALUE_0": "copies"}
        event = self.bash({"files": [], "moreFiles": 1, "changedFiles": []})
        with mock.patch.dict(os.environ, copies):
            self.assertEqual(changed(event), (self.root / "a.py", self.root / "copy.py", self.root / "new.py"))

    def test_more_files_outside_every_repository_lists_nothing_more(self) -> None:
        event = self.bash({"files": [], "moreFiles": 2, "changedFiles": [str(self.root / "a.py")]})
        self.assertEqual(changed(event), (self.root / "a.py",))

    def test_more_files_without_a_cwd_lists_nothing_more(self) -> None:
        self.worked()
        event = self.made(ToolName("Bash"), {"cwd": None, "tool_response": {
            "bashEditDiff": {"moreFiles": 2, "changedFiles": [str(self.root / "a.py")]}}})
        self.assertEqual(changed(event), (self.root / "a.py",))

    def git_fault(self, *, side_effect: OSError | subprocess.TimeoutExpired | None = None,
                  return_value: subprocess.CompletedProcess[GitOutput] | None = None) -> KitFault:
        """The fault of a moreFiles listing whose git run raises ``side_effect`` or answers ``return_value``,
        asserting its time limit."""
        self.worked()
        event = self.bash({"files": [], "moreFiles": 1, "changedFiles": []})
        _ = event.declaration(self.root)
        with (mock.patch("dispatcher.gate_run.subprocess.run", side_effect=side_effect, return_value=return_value)
              as ran, self.assertRaises(KitFault) as caught):
            _ = changed(event)
        self.assertEqual(ran.call_args.kwargs["timeout"], gate_run.GATE_TIMEOUT)
        return caught.exception

    def test_git_past_its_limit_is_a_kit_fault(self) -> None:
        fault = self.git_fault(side_effect=subprocess.TimeoutExpired("git", gate_run.GATE_TIMEOUT))
        self.assertEqual(fault.reason, f"git outran its 2 s limit listing the changes of {self.root}")

    def test_git_that_cannot_run_is_a_kit_fault(self) -> None:
        fault = self.git_fault(side_effect=FileNotFoundError(2, "No such file or directory"))
        self.assertEqual(fault.reason, "git cannot run: No such file or directory")

    def test_git_failing_is_a_kit_fault(self) -> None:
        said = GitOutput(b"fatal: index broken\nmore\n")
        fault = self.git_fault(return_value=subprocess.CompletedProcess([], 128, GitOutput(b""), said))
        self.assertEqual(fault.reason, f"git cannot list the changes of {self.root}: fatal: index broken")


class Limits(unittest.TestCase):
    def test_a_rule_hands_the_first_four_and_gives_back_the_rest(self) -> None:
        paths = [Path(f"/w/{index}.py") for index in range(5)]
        for count, rest in ((3, ()), (4, ()), (5, (paths[4],))):
            with self.subTest(count=count):
                self.assertEqual(limited(paths[:count]), Limited(tuple(paths[:min(count, 4)]), rest))
        self.assertEqual(FILES_PER_RULE, Count(4))


if __name__ == "__main__":
    _ = unittest.main()
