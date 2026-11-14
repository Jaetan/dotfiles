"""The shared-shell rule, through the dispatcher with the kit's own rules: a Bash call whose own shell changes
directory anywhere but the project root is refused, naming each change and where it goes; the rest pass. The cases
R and A carry the old bare-cd guard's meaning, its R and A cases, under the same names; N cases are new."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher.core import Answer, EventBytes, FolderPath, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import PARSE_FORM, ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_declarations import NONE_DECLARED

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
CaseId = NewType("CaseId", str)
UNNAMED = Prose("a directory this call does not name")


class Places(NamedTuple):
    """A scratch folder holding the project root, a folder under it, a folder outside it, and the switch folder."""

    base: Path
    root: Path
    sub: Path
    out: Path
    off: Path


def places() -> Places:
    """Fresh folders for one case class, every rule but shared-shell switched off, so a case judges this rule alone."""
    base = Path(tempfile.mkdtemp()).resolve()
    found = Places(base, base / "project", base / "project" / "python", base / "elsewhere", base / "off")
    for folder in (found.sub, found.out / ".config" / "claude-guard", found.off):
        folder.mkdir(parents=True)
    _ = (found.out / ".config" / "claude-guard" / "machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
    for module in (KIT / "rules").glob("*.py"):
        if not module.stem.startswith("_") and module.stem != "shared_shell":
            (found.off / module.stem.replace("_", "-")).touch()
    (base / "link").symlink_to(found.root)
    return found


def judged(at: Places, call: ShellText, tool: ShellText = ShellText("Bash"), root: Path | None = None,
           agent: bool = False) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call`` run from the root, ``root`` the project root."""
    env = {VarName("HOME"): ShellText(str(at.out))}
    if root is not None:
        env[VarName("CLAUDE_PROJECT_DIR")] = ShellText(str(root))
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)),
                        ReadSettings(READER, Seconds(5.0), env), home=at.out)
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(at.root), "tool_input": {"command": call}}
    if agent:
        fields["agent_id"] = "a1"
    return answer(EventBytes(json.dumps(fields).encode()), settings)


def moved(spelled: Prose, where: Path | Prose) -> Prose:
    """The refusal's line for a command ``spelled`` moving the shell to ``where``."""
    return Prose(f"  * `{spelled}` moves the shared shell to {where}\n")


class Refused(unittest.TestCase):
    """Each change of the call's own shell off the root is refused, its line naming it and where it goes."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def check(self, cases: dict[CaseId, tuple[ShellText, Prose, Path | Prose]]) -> None:
        """Every case refused, its stderr holding its line."""
        for case, (call, spelled, where) in cases.items():
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, root=self.at.root)
                self.assertEqual(got.status, 2)
                self.assertIn(moved(spelled, where), got.stderr)

    def test_the_old_cases(self) -> None:
        root, sub, out = self.at.root, self.at.sub, self.at.out
        cd_out = Prose(f"cd {out}")
        self.check({
            CaseId("R01"): (ShellText(f"cd {out} && ls"), cd_out, out),
            CaseId("R02"): (ShellText("cd python && ls"), Prose("cd python"), sub),
            CaseId("R03"): (ShellText(f"cd {sub}; ls"), Prose(f"cd {sub}"), sub),
            CaseId("R04"): (ShellText("cd"), Prose("cd"), UNNAMED),
            CaseId("R05"): (ShellText("cd -"), Prose("cd -"), UNNAMED),
            CaseId("R06"): (ShellText(f"pushd {out}"), Prose(f"pushd {out}"), out),
            CaseId("R07"): (ShellText("popd"), Prose("popd"), UNNAMED),
            CaseId("R08"): (ShellText(f"builtin cd {out}"), cd_out, out),
            CaseId("R09"): (ShellText(f"command cd {out}"), cd_out, out),
            CaseId("R10"): (ShellText('cd "$BARE_CD_GUARD_UNSET_VARIABLE"'),
                            Prose('cd "$BARE_CD_GUARD_UNSET_VARIABLE"'), UNNAMED),
            CaseId("R11"): (ShellText(f"f() {{ cd {out}; }}; f"), cd_out, out),
            CaseId("R12"): (ShellText(f"eval 'cd {out}'"), cd_out, out),
            CaseId("R13"): (ShellText(f"{{ cd {out}; ls; }}"), cd_out, out),
            CaseId("R14"): (ShellText(f"if true; then cd {out}; fi"), cd_out, out),
            CaseId("R15"): (ShellText(f"cd -P {out}"), Prose(f"cd -P {out}"), out),
            CaseId("R17"): (ShellText(f"cd {root} && cd python"), Prose("cd python"), sub),
            CaseId("R18"): (ShellText("popd +1"), Prose("popd +1"), UNNAMED),
            CaseId("R19"): (ShellText("cd python/../python"), Prose("cd python/../python"), sub),
            CaseId("R20"): (ShellText("cd -- -dir"), Prose("cd -- -dir"), root / "-dir"),
        })

    def test_the_new_cases(self) -> None:
        out = self.at.out
        cd_out = Prose(f"cd {out}")
        self.check({
            CaseId("N01"): (ShellText(f"f() {{ cd {out}; }}"), cd_out, out),
            CaseId("N02"): (ShellText(f"echo ${{ cd {out};}}"), cd_out, out),
            CaseId("N03"): (ShellText(f"exec cd {out}"), cd_out, out),
            CaseId("N04"): (ShellText(f"rtk proxy cd {out}"), cd_out, out),
            CaseId("N05"): (ShellText("cd ~"), cd_out, out),
            CaseId("N06"): (ShellText(f"D={out}; cd $D"), cd_out, out),
            CaseId("N07"): (ShellText(f"for x in a; do cd {out}; done"), cd_out, out),
            CaseId("N08"): (ShellText(f"case a in a) cd {out};; esac"), cd_out, out),
            CaseId("N09"): (ShellText(f"cd {out} || true"), cd_out, out),
        })

    def test_every_change_is_named(self) -> None:
        got = judged(self.at, ShellText(f"cd {self.at.out}; ls; cd python"), root=self.at.root)
        self.assertEqual(got.stderr.count("moves the shared shell"), 2)

    def test_an_agents_call_is_held_alike(self) -> None:
        self.assertEqual(judged(self.at, ShellText(f"cd {self.at.out}"), root=self.at.root, agent=True).status, 2)

    def test_with_no_project_root_every_change_is_refused(self) -> None:
        got = judged(self.at, ShellText(f"cd {self.at.root}"))
        self.assertEqual(got.status, 2)
        self.assertIn(moved(Prose(f"cd {self.at.root}"), self.at.root), got.stderr)
        self.assertIn("project root (unset)", got.stderr)

    def test_the_refusal_names_the_subshell_form_and_the_root(self) -> None:
        got = judged(self.at, ShellText(f"cd {self.at.out}"), root=self.at.root)
        self.assertTrue(got.stderr.startswith("guard shared-shell: this call changes the directory"))
        self.assertIn("(cd /abs/dir && ", got.stderr)
        self.assertIn(f"({self.at.root})", got.stderr)

    def test_a_call_the_kit_cannot_read(self) -> None:
        got = judged(self.at, ShellText("cd $(("), root=self.at.root)
        self.assertEqual(got.status, 2)
        self.assertIn(PARSE_FORM, got.stderr)


class Allowed(unittest.TestCase):
    """A change in a shell of its own, a change to the root and a call with no change pass."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def test_the_old_cases(self) -> None:
        root, sub, out = self.at.root, self.at.sub, self.at.out
        cases = {
            CaseId("A01"): f"(cd {out} && ls)", CaseId("A02"): f"cd {root} && ls", CaseId("A03"): f"cd {sub}/.. && ls",
            CaseId("A04"): f"ls -la {out}", CaseId("A05"): f"echo cd {out}", CaseId("A06"): f"x=$(cd {out} && pwd)",
            CaseId("A07"): f"cd {out} &", CaseId("A08"): f"bash -c 'cd {out}'",
            CaseId("A09"): f"cat <<'EOF'\ncd {out}\nEOF", CaseId("A10"): f"cd {out} | cat",
            CaseId("A11"): f"(ls | cd {out})", CaseId("A12"): f"ls | cd {out} | cat",
            CaseId("A13"): f"ls | cd {out} &", CaseId("R16"): f"ls | cd {out}",
            CaseId("N10"): 'cd "$CLAUDE_PROJECT_DIR" && ls', CaseId("N11"): f"sh -c 'cd {out}'",
            CaseId("N12"): f"cd {out} 2>/dev/null &", CaseId("N13"): f"command -v cd {out}",
            CaseId("N14"): f"cat <(cd {out})", CaseId("N15"): f"coproc cd {out}",
            CaseId("N16"): f"cd {self.at.base / 'link'} && ls",
        }
        for case, call in cases.items():
            with self.subTest(case=case, call=call):
                self.assertEqual(judged(self.at, ShellText(call), root=root), Answer(0, Prose(""), Prose("")))

    def test_a_tool_other_than_bash(self) -> None:
        got = judged(self.at, ShellText(f"cd {self.at.out}"), tool=ShellText("Monitor"), root=self.at.root)
        self.assertEqual(got.status, 0)

    def test_the_rule_switched_off(self) -> None:
        (self.at.off / "shared-shell").touch()
        try:
            self.assertEqual(judged(self.at, ShellText(f"cd {self.at.out}"), root=self.at.root).status, 0)
        finally:
            (self.at.off / "shared-shell").unlink()


if __name__ == "__main__":
    unittest.main()
