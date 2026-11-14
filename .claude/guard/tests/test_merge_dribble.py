"""The merge-dribble rule, through the dispatcher with every other rule of the kit switched off: a work tree's dribble
merging otherwise than by pr-all-green's merge after `set -e` in the merge's own shell is refused, naming each command;
the merge under `set -euo pipefail`, a dribble holding no merge, a call outside any work tree and a version of the
dribble read before pass; tools and events the rule does not judge read nothing.

Each case has a work tree (a folder holding `.git`), a sessions folder and a proc tree of its own in the temporary
folder, one claude process standing in it."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import NewType

from dispatcher.core import AgentId, EventBytes, ExitStatus, FolderPath, HookEvent, Settings, ToolName, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import Environment, VarName
from state.session import Pid, Places, StartTicks
from tests.test_session_state import proc_tree, stat_line

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
OWN = "merge_dribble.py"
Script = NewType("Script", str)
Problem = NewType("Problem", str)

PID = Pid(10)
START = StartTicks(100)
BOOT = Prose("bbbb-0001")
MERGE = ShellText("pr-all-green 5 --head 0123456789abcdef0123456789abcdef01234567 --squash-with-body body.md")
GH_MERGE = Prose("merges with `gh pr merge`, past pr-all-green's verdict")
REST = Prose("merges through a `pulls/N/merge` path, past pr-all-green's verdict")
GRAPHQL = Prose("names a GraphQL merge mutation, past pr-all-green's verdict")
UNREAD = Prose("takes its GraphQL query from a file, its input or a word the dribble does not make known, where a "
               "merge mutation hides unread")
ERREXIT = Prose("runs where errexit is off: no `set -e` before it in its own shell leaves it on (a subshell, a "
                "pipeline element, a substitution and each script run are shells of their own), so the script runs "
                "on past a refusal")


def problem(command: ShellText, why: Prose) -> Problem:
    """The refusal's line naming ``command`` and ``why``."""
    return Problem(f"  * `{command}` {why}")


class Case(unittest.TestCase):
    """A case with a work tree, a switch folder turning off every rule but this one, and a proc tree holding PID."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.off = self.root / "off"
        self.off.mkdir()
        for rule in (KIT / "rules").glob("[!_]*.py"):
            if rule.name != OWN:
                (self.off / rule.stem.replace("_", "-")).touch()
        self.places = Places(self.root / "sessions", proc_tree(self.root, BOOT, {PID: stat_line(PID, START)}))
        self.repo = self.root / "repo"
        (self.repo / ".git").mkdir(parents=True)

    def dribble(self, text: Script, folder: Path | None = None) -> Path:
        """Write ``text`` as the dribble of ``folder``, the case's work tree where None."""
        path = (folder or self.repo) / ".commands-to-run.sh"
        _ = path.write_text(text, encoding="utf-8")
        return path

    def answered(self, cwd: Path | None, tool: ToolName, name: HookEvent,
                 agent: AgentId | None) -> tuple[ExitStatus, Prose]:
        """The dispatcher's exit status and stderr for an event ``name`` of a ``tool`` call in ``cwd``."""
        env: Environment = {VarName("HOME"): ShellText(str(self.root)), VarName("CLAUDE_PID"): ShellText(str(PID))}
        settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(self.off)),
                            ReadSettings(READER, Seconds(5.0), env), self.root, self.places)
        fields = {"hook_event_name": name, "session_id": "s", "tool_name": tool, "tool_input": {"command": "true"}}
        if cwd is not None:
            fields["cwd"] = str(cwd)
        if agent is not None:
            fields["agent_id"] = agent
        got = answer(EventBytes(json.dumps(fields).encode()), settings)
        return got.status, got.stderr

    def heard(self, cwd: Path | None = None, tool: ToolName = ToolName("Bash"), name: HookEvent = "PostToolUse",
              agent: AgentId | None = None) -> list[Problem] | None:
        """The problems the refusal of the event names, None where the call passes with nothing."""
        status, stderr = self.answered(self.repo if cwd is None else cwd, tool, name, agent)
        if status == 0:
            self.assertEqual(stderr, "")
            return None
        self.assertEqual(status, 2, stderr)
        return [Problem(line) for line in stderr.splitlines() if line.startswith("  * ")]

    def refused(self, text: Script) -> list[Problem] | None:
        """The problems a dribble holding ``text`` is refused for."""
        _ = self.dribble(text)
        return self.heard()


class Refused(Case):
    """Every merge but pr-all-green's under errexit is refused, each command named with why."""

    def test_the_refusal_names_the_dribble_each_command_and_the_form(self) -> None:
        path = self.dribble(Script(f"gh pr merge 5 --squash\n{MERGE}\n"))
        status, stderr = self.answered(self.repo, ToolName("Bash"), "PostToolUse", None)
        self.assertEqual(status, 2)
        self.assertIn(f"guard merge-dribble: the dribble {path} merges a pull request in a form that skips "
                      "pr-all-green's verdict:\n\n"
                      f"{problem(ShellText('gh pr merge 5 --squash'), GH_MERGE)}\n{problem(MERGE, ERREXIT)}\n\n"
                      "A dribble merges only by `pr-all-green N --head SHA --squash-with-body FILE` after "
                      "`set -euo pipefail` in the same\nscript: it merges once every check of SHA is green past its "
                      "conclusion and the merge state is CLEAN, and its refusal\nstops what follows.", stderr)

    def test_gh_pr_merge_is_refused_whatever_its_flags(self) -> None:
        held = ShellText("gh pr merge 5 --squash --auto")
        self.assertEqual(self.refused(Script(f"set -euo pipefail\n{held}\n")), [problem(held, GH_MERGE)])

    def test_a_rest_merge_path_is_refused(self) -> None:
        held = ShellText("gh api -X PUT repos/o/r/pulls/5/merge")
        self.assertEqual(self.refused(Script(f"set -e\n{held}\n")), [problem(held, REST)])

    def test_a_graphql_merge_mutation_is_refused(self) -> None:
        merge = ShellText("gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'")
        auto = ShellText("gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) { "
                         "clientMutationId } }'")
        self.assertEqual(self.refused(Script(f"set -e\n{merge}\n{auto}\n")),
                         [problem(merge, GRAPHQL), problem(auto, GRAPHQL)])

    def test_a_graphql_query_read_from_elsewhere_is_refused(self) -> None:
        held = [ShellText(text) for text in ("gh api graphql -F query=@q.graphql", "gh api graphql --input q.json",
                                             "gh api graphql --input=q.json",
                                             'gh api graphql -f query="$(cat q.graphql)"')]
        self.assertEqual(self.refused(Script("set -e\n" + "\n".join(held) + "\n")),
                         [problem(command, UNREAD) for command in held])

    def test_the_merge_without_errexit_is_refused(self) -> None:
        held = [MERGE, ShellText("pr-all-green 5 --head X --squash=body.md"),
                ShellText("python3 /home/u/.local/bin/pr-all-green 5 --head X --squash-with-body=body.md")]
        self.assertEqual(self.refused(Script("\n".join(held) + "\n")), [problem(command, ERREXIT) for command in held])

    def test_a_set_not_turning_errexit_on_leaves_it_off(self) -> None:
        for turned in ("set -u", "set -o pipefail", "set -- -e", "set - -e", "set xy -e", 'set "$(printf %s -e)"'):
            with self.subTest(turned=turned):
                self.assertEqual(self.refused(Script(f"{turned}\n{MERGE}\n")), [problem(MERGE, ERREXIT)])

    def test_set_plus_e_turns_errexit_off_again(self) -> None:
        for turned in ("set -e\nset +e", "set -euo pipefail\nset +o errexit", "set -o errexit\nset +eu"):
            with self.subTest(turned=turned):
                self.assertEqual(self.refused(Script(f"{turned}\n{MERGE}\n")), [problem(MERGE, ERREXIT)])

    def test_errexit_counts_in_the_merge_own_shell(self) -> None:
        self.assertEqual(self.refused(Script(f"set -e\n( {MERGE} )\n")), [problem(MERGE, ERREXIT)])
        inner = self.repo / "inner.sh"
        _ = inner.write_text(f"#!/bin/bash\n{MERGE}\n", encoding="utf-8")
        inner.chmod(0o755)
        self.assertEqual(self.refused(Script("set -e\nexec ./inner.sh\n")), [problem(MERGE, ERREXIT)])


class Passed(Case):
    """The one merge form, a dribble holding no merge and a dribble the rule does not reach pass with nothing."""

    def test_the_merge_under_errexit_passes(self) -> None:
        for turned in ("set -euo pipefail", "set -o errexit", "set -e", "set +u -e", "set +e\nset -e"):
            with self.subTest(turned=turned):
                self.assertIsNone(self.refused(Script(f"{turned}\n{MERGE}\n")))

    def test_a_script_the_dribble_runs_under_its_own_errexit_passes(self) -> None:
        inner = self.repo / "inner.sh"
        _ = inner.write_text(f"#!/bin/bash\nset -euo pipefail\n{MERGE}\n", encoding="utf-8")
        inner.chmod(0o755)
        self.assertIsNone(self.refused(Script("exec ./inner.sh\n")))

    def test_a_dribble_holding_no_merge_passes(self) -> None:
        self.assertIsNone(self.refused(Script(
            "git status\ngh pr view 5 --json state\ngh api repos/o/r/pulls/5\npr-all-green 5 --head X --stops s\n"
            "gh api graphql -f query='{ viewer { login } }'\ngh api -X PATCH repos/o/r/pulls/5 --input body.json\n"
            "gh issue comment 5 --body 'not pulls/5/merge, nor graphql mergePullRequest'\n")))

    def test_a_call_outside_any_work_tree_passes(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        _ = self.dribble(Script("gh pr merge 5\n"), plain)
        self.assertIsNone(self.heard(plain))

    def test_a_work_tree_with_no_dribble_passes(self) -> None:
        self.assertIsNone(self.heard())

    def test_an_unreadable_dribble_is_a_fault_of_the_kit(self) -> None:
        path = self.dribble(Script("gh pr merge 5\n"))
        path.chmod(0)
        status, stderr = self.answered(self.repo, ToolName("Bash"), "PostToolUse", None)
        self.assertEqual(status, 2)
        self.assertIn(f"the dribble {path} cannot be read", stderr)

    def test_an_event_naming_no_folder_passes(self) -> None:
        _ = self.dribble(Script("gh pr merge 5\n"))
        self.assertEqual(self.answered(None, ToolName("Bash"), "PostToolUse", None), (0, ""))


class Reading(Case):
    """The dribble is read after each call of a writing tool, once per version, from anywhere in its work tree."""

    HELD = ShellText("gh pr merge 5")

    def test_each_tool_that_writes_reads_it(self) -> None:
        for tool in ("Bash", "Edit", "Write", "MultiEdit"):
            with self.subTest(tool=tool):
                _ = self.dribble(Script(f"# {tool}\n{self.HELD}\n"))
                self.assertEqual(self.heard(tool=ToolName(tool)), [problem(self.HELD, GH_MERGE)])

    def test_other_tools_and_events_read_nothing(self) -> None:
        _ = self.dribble(Script(f"{self.HELD}\n"))
        self.assertIsNone(self.heard(tool=ToolName("Read")))
        self.assertIsNone(self.heard(tool=ToolName("Edit"), name="PreToolUse"))
        self.assertEqual(self.heard(), [problem(self.HELD, GH_MERGE)])

    def test_a_call_from_a_subfolder_reads_the_root_dribble(self) -> None:
        (self.repo / "src").mkdir()
        _ = self.dribble(Script(f"{self.HELD}\n"))
        self.assertEqual(self.heard(self.repo / "src"), [problem(self.HELD, GH_MERGE)])

    def test_an_unchanged_dribble_is_not_read_again(self) -> None:
        other = ShellText("gh pr merge 6")
        self.assertEqual(self.refused(Script(f"{self.HELD}\n")), [problem(self.HELD, GH_MERGE)])
        self.assertIsNone(self.heard())
        self.assertEqual(self.refused(Script(f"{other}\n")), [problem(other, GH_MERGE)])

    def test_a_version_read_before_another_agent_read_the_next_is_read_again(self) -> None:
        other = ShellText("gh pr merge 6")
        _ = self.dribble(Script(f"{self.HELD}\n"))
        self.assertEqual(self.heard(), [problem(self.HELD, GH_MERGE)])
        _ = self.dribble(Script(f"{other}\n"))
        self.assertEqual(self.heard(agent=AgentId("a1")), [problem(other, GH_MERGE)])
        _ = self.dribble(Script(f"{self.HELD}\n"))
        self.assertEqual(self.heard(), [problem(self.HELD, GH_MERGE)])


if __name__ == "__main__":
    _ = unittest.main()
