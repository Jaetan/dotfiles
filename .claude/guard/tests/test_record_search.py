"""The record-search rule, through the dispatcher with every other rule switched off: a recursive search starting
at a declared run-record folder or at a folder holding one is refused, naming the command, the path and the record; the
rest pass. The old cases kept, under their old names, are those whose verdict the blunt rule keeps; Skips holds that
nothing a search skips is read, and each of the spec's other clauses has cases of its own.

The fixture: a home whose machine file declares the records beside the checkout (@SIB@), through a link to the folder
holding it, a checkout (@CK@) whose `.claude/guard.toml` declares tools/ci-output, which a .gitignore of `*` inside it
covers, a scratch folder (@S@) of links to the checkout's tools, and two repositories more declaring `out`, the second
with a .gitignore naming it. Every git run reads no user or system configuration."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NamedTuple, NewType
from unittest import mock

from dispatcher.core import Answer, EventBytes, FolderPath, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_declarations import TomlSource, declaring

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = "record_search"
MARK = Prose("guard record-search:")
BASH = ShellText("Bash")
NO_CONFIG = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
CaseId = NewType("CaseId", str)
Marker = NewType("Marker", str)
Where = Literal["ck", "tools", "rec", "work", "s", "other", "third"]
Status = Literal[0, 2]
Case = tuple[CaseId, ShellText, Where, Status]


class Places(NamedTuple):
    """The fixture's folders."""

    base: Path
    home: Path
    off: Path
    ck: Path
    sib: Path
    work: Path
    s: Path
    other: Path
    third: Path


def git(cwd: Path, *words: ShellText) -> None:
    """Run git in ``cwd`` with no user or system configuration."""
    _ = subprocess.run(["git", "-c", "init.defaultBranch=main", *words], cwd=cwd, env={**os.environ, **NO_CONFIG},
                       check=True, capture_output=True)


def repository(root: Path, records: Prose, ignore: Prose | None) -> None:
    """A git repository at ``root`` declaring ``records`` (TOML array text), with ``ignore`` as its .gitignore."""
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "guard.toml").write_text(f"schema = 1\n[records]\npaths = {records}\n")
    if ignore is not None:
        (root / ".gitignore").write_text(ignore)
    git(root, ShellText("init"), ShellText("-q"))


def places() -> Places:
    """A fresh fixture."""
    base = Path(tempfile.mkdtemp()).resolve()
    work = base / "work"
    at = Places(base, base / "home", base / "off", work / "aletheia", work / "aletheia-ci-output", work,
                base / "scratch", base / "other", base / "third")
    for folder in (at.home / ".config" / "claude-guard", at.off, at.ck / "tools" / "ci-output" / "run1",
                   at.ck / "python" / "aletheia", at.ck / "python" / "tests", at.ck / "docs", at.sib / "run1", at.s,
                   at.other / "out" / "run1", at.third / "out" / "run1"):
        folder.mkdir(parents=True)
    (base / "link").symlink_to(work)
    (at.home / ".config" / "claude-guard" / "machine.toml").write_text(
        declaring(records=TomlSource(f'[records]\npaths = ["{base / "link" / at.sib.name}"]\n')))
    (at.ck / "tools" / "ci-output" / ".gitignore").write_text("*\n")
    repository(at.ck, Prose('["tools/ci-output"]'), None)
    repository(at.other, Prose('["out"]'), None)
    repository(at.third, Prose('["out"]'), Prose("out/\n"))
    for name in ("rec-link", "r(x)", "r#x", "^rx", "r~x", "a b"):
        (at.s / name).symlink_to(at.ck / "tools")
    for entry in os.listdir(KIT / "rules"):
        stem, suffix = os.path.splitext(entry)
        if suffix == ".py" and not stem.startswith("_") and stem != RULE:
            (at.off / stem.replace("_", "-")).touch()
    return at


def folder_of(at: Places, where: Where) -> Path:
    """The folder a case runs in."""
    return {"ck": at.ck, "tools": at.ck / "tools", "rec": at.ck / "tools" / "ci-output", "work": at.work, "s": at.s,
            "other": at.other, "third": at.third}[where]


def filled(at: Places, text: ShellText) -> ShellText:
    """``text`` with each @NAME@ marker spelled as the fixture's folder."""
    for marker, path in ((Marker("@CK@"), at.ck), (Marker("@SIB@"), at.sib), (Marker("@WORK@"), at.work),
                         (Marker("@S@"), at.s)):
        text = ShellText(text.replace(marker, str(path)))
    return text


def judged(at: Places, call: ShellText, where: Where, tool: ShellText = BASH) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call`` run from ``where``."""
    env = {VarName("HOME"): ShellText(str(at.home))}
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)), ReadSettings(READER, Seconds(5.0), env),
                        home=at.home)
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(folder_of(at, where)),
              "tool_input": {"command": filled(at, call)}}
    return answer(EventBytes(json.dumps(fields).encode()), settings)


class Fixture(unittest.TestCase):
    """A class of cases sharing one fixture, git reading no configuration."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.enterClassContext(mock.patch.dict(os.environ, NO_CONFIG))
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def check(self, cases: list[Case]) -> None:
        """Every case refused by this rule where it wants 2, passed where it wants 0."""
        for case, call, where, want in cases:
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, where)
                self.assertEqual((got.status, MARK in got.stderr), (want, want == 2), got.stderr)

    def told(self, call: ShellText, where: Where, *lines: Prose) -> None:
        """``call`` refused by this rule, its refusal holding each of ``lines``, markers filled."""
        got = judged(self.at, call, where)
        self.assertEqual(got.status, 2)
        for line in lines:
            self.assertIn(filled(self.at, ShellText(line)), got.stderr)


C = CaseId
T = ShellText


class Grep(Fixture):
    """A recursive grep: a word before `--` that is a recursive option or a bundle holding r or R."""

    def test_old_refused(self) -> None:
        self.check([
            (C("SCH1"), T('grep -rn "read_issues" python/aletheia python/tests tools'), "ck", 2),
            (C("SCH2"), T('grep -rn "returned null" python/aletheia python/tests tools docs *.md | grep -v ci-output'),
             "ck", 2),
            (C("SCH3"), T("grep -rn x"), "ck", 2),
            (C("SCH4"), T("grep -R x tools"), "ck", 2),
            (C("SCH5"), T("grep --recursive x tools"), "ck", 2),
            (C("SCH6"), T("grep --dereference-recursive x tools"), "ck", 2),
            (C("SCH7"), T("grep -d recurse x tools"), "ck", 2),
            (C("SCH10"), T("grep x tools -r"), "ck", 2),
            (C("SCH11"), T("grep -d read -r x tools"), "ck", 2),
            (C("SCH12"), T("rgrep x tools"), "ck", 2),
            (C("SCH13"), T("egrep -r x tools"), "ck", 2),
            (C("SCH14"), T("fgrep -r x tools"), "ck", 2),
            (C("SCH15"), T("grep -r x @WORK@"), "s", 2),
            (C("SCH16"), T("grep -r --exclude-dir=ci-output x @WORK@"), "s", 2),
            (C("SCH17"), T("grep -r --exclude-dir=ci-output x ."), "rec", 2),
            (C("SCH18"), T("grep -r --exclude-dir=ci-output x tools/ci-output/"), "ck", 2),
            (C("SCH19"), T("grep -r --exclude-dir=output x tools"), "ck", 2),
            (C("SCH20"), T("grep -r --exclude-dir=tools/ci-output x tools"), "ck", 2),
            (C("SCH21"), T("cd @CK@/tools && grep -r x ."), "s", 2),
            (C("SCH22"), T("bash -c 'grep -r x tools'"), "ck", 2),
            (C("SCH23"), T("python3 -c \"import subprocess; subprocess.run(['grep', '-r', 'x', 'tools'])\""), "ck", 2),
            (C("SCH24"), T("echo @CK@/tools | xargs grep -r x"), "s", 2),
            (C("SCH25"), T("find tools -maxdepth 0 -exec grep -r x {} +"), "ck", 2),
            (C("SCH26"), T("grep -r x rec-link"), "s", 2),
            (C("SCH27"), T('grep -r x "$UNKNOWN"'), "ck", 2),
            (C("SCH28"), T("grep -r -e x @CK@/tools"), "s", 2),
            (C("SCH29"), T("grep -rex @CK@/tools"), "s", 2),
            (C("SCH30"), T("grep -r --file=pats @CK@/tools"), "s", 2),
            (C("SCH31"), T("grep -r --regexp=x @CK@/tools"), "s", 2),
            (C("SCH32"), T('cd "$NOWHERE" && grep -r x tools'), "ck", 2),
            (C("SCH33"), T('cat "$LIST" | xargs grep -r x python'), "ck", 2),
            (C("SCH34"), T("grep -rn x tools/*"), "ck", 2),
            (C("SCH35"), T("grep -r -- x @CK@/tools"), "s", 2),
            (C("SCH36"), T("grep -rn --exclude-dir=ci-output$X x tools"), "ck", 2),
            (C("SCH37"), T("grep -rf$X tools python"), "ck", 2),
        ])

    def test_old_passed(self) -> None:
        self.check([
            (C("SCP3"), T("grep -rn x python/aletheia python/tests"), "ck", 0),
            (C("SCP4"), T("grep -n x tools"), "ck", 0),
            (C("SCP7"), T("grep -r x tools/ci-out"), "ck", 0),
            (C("SCP12"), T("grep -r x -"), "ck", 0),
            (C("SCP13"), T("git grep -n x -- tools"), "ck", 0),
            (C("SCP14"), T("grep -r x"), "s", 0),
            (C("SCP16"), T("grep -- -r x"), "ck", 0),
            (C("SCP17"), T("grep -r x tools/ci-output/run1"), "ck", 0),
            (C("SCP19"), T("grep -r x @SIB@/run1"), "s", 0),
            (C("SCP22"), T("grep -r tools python"), "ck", 0),
            (C("SCP23"), T("grep -rn --exclude- x python"), "ck", 0),
            (C("SCP25"), T("grep -rn tools/__init__.p* python"), "ck", 0),
            (C("SCP27"), T("grep -rn x --include=*.py python"), "ck", 0),
            (C("SCP28"), T("grep --rec$X x tools"), "ck", 0),
        ])

    def test_spec_forms(self) -> None:
        self.check([
            (C("N1"), T("grep --directories=recurse x tools"), "ck", 2),
            (C("N2"), T("/usr/bin/grep -r x tools"), "ck", 2),
            (C("N3"), T("grep -n r tools"), "ck", 0),
            (C("N4"), T("grep --color x tools"), "ck", 0),
            (C("N5"), T("echo x | grep -r x"), "ck", 2),
            (C("N6"), T("grep -r -- tools python"), "ck", 0),
            (C("N7"), T("ls -R tools"), "ck", 0),
        ])


class Wildcards(Fixture):
    """A wildcard path is judged by the folder before its first wildcard, with no wildcard semantics."""

    def test_folder_before_wildcard(self) -> None:
        self.check([
            (C("W1"), T("grep -rn x tools/*"), "ck", 2),
            (C("W2"), T("grep -rn x t*"), "ck", 2),
            (C("W3"), T("grep -rn x *.md"), "ck", 2),
            (C("W4"), T("grep -rn x python/*.py"), "ck", 0),
            (C("W5"), T('grep -rn x "tools"/*'), "ck", 2),
            (C("W6"), T("grep -rn x 't*'"), "ck", 0),
            (C("W7"), T("grep -rn --exclude-dir=ci-output x 'tools/ci-output'/*"), "ck", 2),
            (C("W9"), T("grep -rn x {python,tools}"), "ck", 2),
            (C("W11"), T("grep -rn x python/{aletheia,tests}"), "ck", 0),
            (C("W12"), T("grep -rn x ~/x/*"), "ck", 0),
            (C("W13"), T("grep -rn x aletheia-ci-*"), "work", 2),
            (C("W14"), T("grep -rn x @(python|docs)/x"), "ck", 2),
            (C("W15"), T("grep -rn x $UNKNOWN/*.md"), "ck", 2),
            (C("W16"), T("X=tools; grep -rn x $X/*"), "ck", 2),
            (C("W17"), T("X=python; grep -rn x $X/*"), "ck", 0),
            (C("W18"), T("grep -rn x r#x"), "s", 2),
            (C("W19"), T("grep -rn x ^rx"), "s", 2),
            (C("W20"), T("grep -rn x r~x"), "s", 2),
            (C("W21"), T("grep -rn x 'r(x)'/c*"), "s", 2),
            (C("W22"), T("grep -rn x tools/ci-output/run*"), "ck", 2),
            (C("W23"), T("rg -u -n x *.md"), "ck", 2),
            (C("W25"), T("find *.md -exec cat {} +"), "ck", 2),
            (C("W26"), T("find python/* -exec cat {} +"), "ck", 0),
            (C("W27"), T("grep -rn x 'notes (old)'"), "ck", 0),
            (C("W28"), T("grep -rn x */ci-output"), "tools", 2),
        ])


class Rg(Fixture):
    """An rg searches recursively, but where it names no path and reads what feeds its input."""

    def test_old_refused(self) -> None:
        self.check([
            (C("RG1"), T("rg -u x tools"), "ck", 2),
            (C("RG2"), T("rg -n x @WORK@"), "s", 2),
            (C("RG3"), T("rg -n x"), "work", 2),
            (C("RG4"), T("rg -uu x tools"), "ck", 2),
            (C("RG5"), T("rg --no-ignore x tools"), "ck", 2),
            (C("RG6"), T("rg --no-ignore-vcs x tools"), "ck", 2),
            (C("RG7"), T("rg --unrestricted x tools"), "ck", 2),
            (C("RG8"), T("rg -nu x tools"), "ck", 2),
            (C("RG9"), T("rg x tools/ci-output"), "ck", 2),
            (C("RG10"), T("rg -g '!ci-output' x tools/ci-output"), "ck", 2),
            (C("RG11"), T("rg -u -g '!ci-output' -g ci-output x tools"), "ck", 2),
            (C("RG13"), T("rg -g '!ci-output' x @WORK@"), "s", 2),
            (C("RG14"), T("rg -u -g '!tools/ci-output' x tools"), "ck", 2),
            (C("RG15"), T("rg -e x @WORK@"), "s", 2),
            (C("RG16"), T("rg -f pats @WORK@"), "s", 2),
            (C("RG17"), T("rg --regexp=x @WORK@"), "s", 2),
            (C("RG18"), T("rg --file=pats @WORK@"), "s", 2),
            (C("RG22"), T("rg x < /dev/null"), "work", 2),
            (C("RG23"), T("rg x 3< pats"), "work", 2),
            (C("RG24"), T("cd @CK@ && rg -u x"), "s", 2),
            (C("RG25"), T("bash -c 'rg -u x tools'"), "ck", 2),
            (C("RG26"), T("echo @WORK@ | xargs rg x"), "s", 2),
            (C("RG27"), T("rg --ignore -u x tools"), "ck", 2),
            (C("RG28"), T("echo x | rg -u x tools"), "ck", 2),
            (C("RG29"), T("echo x | rg x < /dev/null"), "work", 2),
            (C("RG30"), T("rg -u -n x tools/*"), "ck", 2),
            (C("RG31"), T("rg --no-ignore-vcs --ignore x tools"), "ck", 2),
            (C("RG32"), T("rg x | head"), "work", 2),
        ])

    def test_old_passed(self) -> None:
        self.check([
            (C("RGP5"), T("echo x | rg x"), "work", 0),
            (C("RGP6"), T("rg x <<< x"), "work", 0),
            (C("RGP7"), T("rg x < pats"), "work", 0),
            (C("RGP8"), T("rg x -"), "work", 0),
            (C("RGP21"), T("rg x tools/ci-output/run1"), "ck", 0),
            (C("RGP28"), T("echo x | rg x | head"), "work", 0),
            (C("RGP30"), T('echo x | rg "$(cat pats)"'), "work", 0),
            (C("RGP31"), T("rg -u x --glob=*.py ../python"), "tools", 0),
        ])

    def test_input(self) -> None:
        self.check([
            (C("R1"), T('rg x < "$F"'), "work", 2),
            (C("R2"), T("rg x < <(echo x)"), "work", 2),
        ])


class Find(Fixture):
    """A find handing what it walks to a program reading contents."""

    def test_old_refused(self) -> None:
        self.check([
            (C("FND1"), T("find tools -type f -exec grep -l x {} +"), "ck", 2),
            (C("FND2"), T("find . -type f -exec cat {} \\;"), "ck", 2),
            (C("FND3"), T("find tools -execdir head -1 {} +"), "ck", 2),
            (C("FND4"), T("find tools -ok tail -1 {} \\;"), "ck", 2),
            (C("FND5"), T("find tools -okdir sed -n 1p {} \\;"), "ck", 2),
            (C("FND6"), T("find tools -type f | xargs grep x"), "ck", 2),
            (C("FND7"), T("find tools -type f -print0 | xargs -0 wc -l"), "ck", 2),
            (C("FND8"), T("find tools -type f | grep -v test | xargs awk 1"), "ck", 2),
            (C("FND9"), T("find @WORK@ -type f -exec cat {} +"), "s", 2),
            (C("FND10"), T("find @WORK@ -path '*/ci-output' -prune -o -type f -exec cat {} +"), "s", 2),
            (C("FND11"), T("find tools/ci-output -type f -exec cat {} +"), "ck", 2),
            (C("FND12"), T("find . -maxdepth 3 -type f -exec cat {} +"), "ck", 2),
            (C("FND13"), T("find . -not -path ./tools/ci-output -type f -exec cat {} +"), "ck", 2),
            (C("FND14"), T("find . -depth -path ./tools/ci-output -prune -o -type f -exec cat {} +"), "ck", 2),
            (C("FND15"), T("find . -d -path ./tools/ci-output -prune -o -type f -exec cat {} +"), "ck", 2),
            (C("FND16"), T("find . -path ./tools/ci-output -prune -o -name '*.tmp' -delete -o -type f -exec cat {} +"),
             "ck", 2),
            (C("FND17"), T("find -L @CK@/tools -exec cat {} +"), "s", 2),
            (C("FND18"), T("find -D stat @CK@/tools -exec cat {} +"), "s", 2),
            (C("FND19"), T("find -O3 @CK@/tools -exec cat {} +"), "s", 2),
            (C("FND20"), T("find tools -path ./tools/ci-output -prune -o -exec cat {} +"), "ck", 2),
            (C("FND21"), T("find tools -name output -prune -o -exec cat {} +"), "ck", 2),
            (C("FND22"), T("find . \\( -path ./x -o -path ./y \\) -prune -o -exec cat {} +"), "ck", 2),
            (C("FND23"), T("find tools -exec env LC_ALL=C grep x {} +"), "ck", 2),
            (C("FND24"), T("bash -c 'find tools -exec cat {} +'"), "ck", 2),
            (C("FND25"), T("python3 -c \"import subprocess; subprocess.run(['find', 'tools', '-exec', 'cat', '{}', "
                           "'+'])\""), "ck", 2),
            (C("FND26"), T("find tools -exec sh -c 'cat \"$1\"' _ {} \\;"), "ck", 2),
            (C("FND27"), T("find . -path ./tools/ci-output -exec cat {} +"), "ck", 2),
            (C("FND28"), T("find . -not -name ci-output -exec cat {} +"), "ck", 2),
            (C("FND29"), T("find tools -not -path 'tools/ci-output/?' -exec cat {} +"), "ck", 2),
            (C("FND30"), T("find tools -not -path 'tools/ci-output/*/*' -exec cat {} +"), "ck", 2),
            (C("FND31"), T("cd @CK@/tools && find . -exec cat {} +"), "s", 2),
            (C("FND32"), T("find . -maxdepth 2 -maxdepth 3 -exec cat {} +"), "ck", 2),
            (C("FND33"), T("find -H @CK@/tools -exec cat {} +"), "s", 2),
            (C("FND34"), T("find -P @CK@/tools -exec cat {} +"), "s", 2),
            (C("FND35"), T("find -D stat -exec cat {} +"), "ck", 2),
            (C("FND36"), T("find -type f -exec cat {} +"), "ck", 2),
            (C("FND37"), T("find . -type f -a -path './tools/ci-output/*' -exec cat {} +"), "ck", 2),
            (C("FND38"), T("find . ! ! -path './tools/ci-output/*' -exec cat {} +"), "ck", 2),
            (C("FND39"), T("find tools -exec ls {} \\; -exec cat {} +"), "ck", 2),
            (C("FND40"), T("find tools -exec ls {} + -exec cat {} \\;"), "ck", 2),
            (C("FND41"), T("find tools -exec cat {} \\; -exec ls {} \\;"), "ck", 2),
            (C("FND42"), T("find tools -exec cat {} + -path"), "ck", 2),
            (C("FND43"), T("find tools -exec cat {} + \\)"), "ck", 2),
            (C("FND44"), T("find . -maxdepth x -exec cat {} +"), "ck", 2),
            (C("FND45"), T("find . ! -type d -path './tools/ci-output/*' -exec cat {} +"), "ck", 2),
            (C("FND46"), T("find tools -exec ls {} + | xargs cat"), "ck", 2),
            (C("FND47"), T("find tools/ci-output -maxdepth 1 -exec cat {} +"), "ck", 2),
            (C("FND48"), T("find . ! -name ci-output -type f -exec grep -n x {} +"), "ck", 2),
            (C("FND49"), T("find tools/* -exec cat {} +"), "ck", 2),
            (C("FND50"), T("find . ! -name '?' -exec cat {} +"), "ck", 2),
            (C("FND51"), T("find . -path ./tools/ci-output -prune -o -exec grep -r x {} +"), "ck", 2),
            (C("FND52"), T('find . -path "$UNKNOWN" -prune -o -exec cat {} +'), "ck", 2),
        ])

    def test_old_passed(self) -> None:
        self.check([
            (C("FNDP1"), T("find tools -type f"), "ck", 0),
            (C("FNDP2"), T("find tools -name '*.py' -print"), "ck", 0),
            (C("FNDP20"), T("find tools -type f | xargs"), "ck", 0),
            (C("FNDP21"), T("find tools -type f | wc -l"), "ck", 0),
            (C("FNDP22"), T("find tools/ci-output/run1 -exec cat {} +"), "ck", 0),
            (C("FNDP23"), T("find python -exec cat {} +"), "ck", 0),
            (C("FNDP24"), T("find tools -type f; ls | xargs cat"), "ck", 0),
            (C("FNDP25"), T("find tools | (cat); ls | xargs cat"), "ck", 0),
            (C("FNDP28"), T("ls | { find tools; xargs cat; }"), "ck", 0),
            (C("FNDP38"), T("find python -exec grep -r x {} +"), "ck", 0),
        ])

    def test_name_only(self) -> None:
        self.check([(C(f"FNDN{k}"), T(f"find @WORK@ -exec {name} {{}} +"), "s", 0) for k, name in enumerate(
            ["ls", "eza", "stat", "echo", "printf", "basename", "dirname", "realpath", "readlink", "du", "test", "'['",
             "true", "false", "rm", "rmdir", "mv", "ln", "touch", "chmod", "chown", "mkdir"], 1)])

    def test_actions(self) -> None:
        self.check([
            (C("A1"), T("find tools -exec ls {} + ; find python -exec cat {} +"), "ck", 0),
            (C("A3"), T("find . -ok cat {} + -maxdepth 1 \\;"), "ck", 2),
            (C("A4"), T("find tools -exec grep -r --exclude-dir=ci-output x {} +"), "ck", 2),
            (C("A5"), T("find tools -exec rg -g '!ci-output' x {} +"), "ck", 2),
            (C("A6"), T("find tools | xargs grep -r --exclude-dir=ci-output --exclude-dir=aletheia-ci-output x"), "ck", 2),
            (C("A7"), T("find tools | xargs rg -g '!ci-output' -g '!aletheia-ci-output' x"), "ck", 2),
            (C("A8"), T("find tools -exec env LC_ALL=C rg -g '!ci-output' x {} +"), "ck", 2),
        ])

    def test_start_paths(self) -> None:
        self.check([
            (C("F1"), T("find \\( -name x \\) -exec cat {} +"), "ck", 2),
            (C("F2"), T("find ! -name x -exec cat {} +"), "ck", 2),
            (C("F3"), T("find , -exec cat {} +"), "ck", 2),
            (C("F4"), T("find python -name tools -exec cat {} +"), "ck", 0),
        ])


class Skips(Fixture):
    """Nothing a search skips is read: --exclude-dir, -g, an ignore file, -prune and -maxdepth change no verdict."""

    def test_nothing_skipped(self) -> None:
        self.check([
            (C("FNDP16"), T("find . -maxdepth 2 -type f -exec cat {} +"), "ck", 2),
            (C("FNDP31"), T("find tools/ci-output -maxdepth 0 -exec cat {} +"), "ck", 2),
            (C("K1"), T('grep -rn --exclude-dir=ci-output "read_issues" python/aletheia python/tests tools'), "ck", 2),
            (C("K2"), T("rg -u -g '!ci-output' x tools"), "ck", 2),
            (C("K3"), T("rg -n x"), "ck", 2),
            (C("K4"), T("rg -n x"), "third", 2),
            (C("K5"), T("find . -path ./tools/ci-output -prune -o -type f -exec grep x {} +"), "ck", 2),
            (C("K6"), T("find . -maxdepth 1 -type f -exec cat {} +"), "ck", 2),
        ])


class Pipelines(Fixture):
    """What an xargs runs, and what reaches it from a find earlier in its pipeline; bash runs every pipeline element
    in a process of its own, an rg in a later element reading the pipe."""

    def test_xargs(self) -> None:
        self.check([
            (C("X1"), T("ls | xargs -n1 grep -rn x"), "s", 2),
            (C("X3"), T("ls | xargs -I{} rg x {}"), "s", 2),
            (C("X4"), T("find tools -type f | sort | xargs cat"), "ck", 2),
            (C("X5"), T("find tools -type f | xargs -0 -n 1 ls"), "ck", 0),
            (C("X6"), T("bash -c 'find tools -type f | xargs cat'"), "ck", 2),
            (C("X7"), T("ls | xargs grep -n x"), "s", 0),
            (C("X8"), T("find tools -type f | xargs -P 4 -- env LC_ALL=C cat"), "ck", 2),
            (C("X9"), T("find tools -type f | xargs -i cat {}"), "ck", 2),
            (C("X10"), T("find tools -type f | xargs --max-args 1 cat"), "ck", 2),
            (C("X11"), T("find tools -type f | xargs --max-args 1 ls"), "ck", 0),
            (C("X12"), T("find tools -type f | grep -f <(xargs cat < pats) x | head"), "ck", 0),
            (C("X13"), T("find tools -type f | xargs -eoffn ls cat"), "ck", 0),
        ])

    def test_elements(self) -> None:
        self.check([
            (C("RG33"), T("bash -c 'echo x | rg x'"), "work", 0),
            (C("P1"), T("echo x | { true; rg x; }"), "work", 0),
            (C("P2"), T("echo x | (rg x | head)"), "work", 0),
            (C("P3"), T("(rg x | head) | head"), "work", 2),
            (C("P4"), T("bash -c 'find tools -type f | sort; xargs cat < list'"), "ck", 0),
            (C("P5"), T("find tools -type f | (xargs cat) | head"), "ck", 2),
            (C("P6"), T("(find tools -type f | head) | xargs cat"), "ck", 2),
            (C("P7"), T("(ls | find tools -type f) | xargs cat"), "ck", 2),
            (C("P8"), T("find tools -type f | head; ls | xargs cat"), "ck", 0),
            (C("P9"), T("{ find tools -type f; xargs cat < list; } | head"), "ck", 0),
            (C("P10"), T("(find tools -type f | xargs cat) | head"), "ck", 2),
        ])


class Declared(Fixture):
    """The records are the ones the declarations hold for the path, whatever names them, on either shell tool."""

    def test_repository_records(self) -> None:
        self.check([
            (C("D1"), T("rg -n x"), "other", 2),
            (C("D2"), T("rg -n -g '!out' x"), "other", 0),
            (C("D5"), T("rg -u -n x"), "third", 2),
            (C("D6"), T("grep -rn x ."), "third", 2),
            (C("D7"), T("grep -rn x out/run1"), "other", 0),
        ])

    def test_monitor(self) -> None:
        got = judged(self.at, T("grep -rn x tools"), "ck", T("Monitor"))
        self.assertEqual((got.status, MARK in got.stderr), (2, True), got.stderr)
        got = judged(self.at, T("grep -rn x python"), "ck", T("Monitor"))
        self.assertEqual(got.status, 0, got.stderr)


class Unknown(Fixture):
    """A path whose folder the call does not make known is refused wherever a record is declared for the command's
    directory."""

    def test_unknown_paths(self) -> None:
        self.check([
            (C("U2"), T('grep -rn --exclude-dir=ci-output x "$UNKNOWN"'), "ck", 2),
            (C("U3"), T('rg -u -g "!ci-output" x "$UNKNOWN"'), "ck", 2),
            (C("U4"), T('rg x "$UNKNOWN"'), "s", 2),
            (C("U6"), T('find "$UNKNOWN" -exec cat {} +'), "s", 2),
            (C("U9"), T('find "$UNKNOWN" -maxdepth 1 -exec cat {} +'), "s", 2),
            (C("U10"), T('grep -rn x "$UNKNOWN"'), "other", 2),
            (C("U11"), T('grep -rn --exclude-dir=aletheia-ci-output x "$UNKNOWN"'), "ck", 2),
            (C("U12"), T('cd "$NOWHERE" && grep -r --exclude-dir=aletheia-ci-output x tools'), "ck", 2),
        ])


class Messages(Fixture):
    """A refusal names the command, its start path and the record, and the forms that pass."""

    def test_known(self) -> None:
        self.told(T("grep -rn x 'tools'"), "ck",
                  Prose("guard record-search: a recursive search starts at a run-record folder or at a folder holding "
                        "one."),
                  Prose("  * `grep -rn x tools` starts at `tools`, which is or holds the run record "
                        "@CK@/tools/ci-output"),
                  Prose("(--exclude-dir, -g, ignore files, -prune, -maxdepth)"),
                  Prose("  * a narrower path: one run's folder beneath the record, or a folder beside it;"),
                  Prose("  * git grep -n PATTERN -- PATHS;"),
                  Prose("  * rg -n PATTERN PATHS over named trees that hold no record."))

    def test_unknown(self) -> None:
        self.told(T('cd "$NOWHERE" && grep -r x tools'), "ck",
                  Prose("  * `grep -r x tools` starts at `tools`, a folder this call does not make known, while the run "
                        "record @CK@/tools/ci-output is declared for @CK@"))
        self.told(T('cd @CK@ && grep -rn x "$U"'), "s",
                  Prose('  * `grep -rn x "$U"` starts at `"$U"`, a folder this call does not make known, while the run '
                        "record @CK@/tools/ci-output is declared for @CK@"))

    def test_named_once(self) -> None:
        got = judged(self.at, T("grep -r x tools tools"), "ck")
        named = [said for said in got.stderr.splitlines() if said.startswith("  * `")]
        self.assertEqual(named, [filled(self.at, T("  * `grep -r x tools tools` starts at `tools`, which is or holds "
                                                    "the run record @CK@/tools/ci-output"))], got.stderr)

    def test_long_command_cut(self) -> None:
        long = "a" * 130
        got = judged(self.at, T(f"grep -rn {long} tools"), "ck")
        self.assertIn(f"  * `grep -rn {long[:SPELLED - len('grep -rn ')]}...` starts at", got.stderr)


SPELLED = 117


if __name__ == "__main__":
    unittest.main()
