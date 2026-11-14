"""The unit-slice rule, through the dispatcher with every other rule switched off: a call whose systemd-run does not
spell a user unit in the machine's declared slice is refused, each line naming the systemd-run and what keeps it out,
the refusal naming the form that passes; the rest pass. The LIM cases carry the old heavy-run-guard rule 6 cases'
meaning under their names, each keeping its verdict; every other class holds a clause of the rule or an exception."""

import json
import os
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NamedTuple, NewType

from dispatcher.core import Answer, Event, EventBytes, FolderPath, Settings, ToolName, answer, loaded
from dispatcher.model import FOREIGN_FORM, ReadSettings
from dispatcher.tree import Count, ReaderPath, Seconds, ShellText
from dispatcher.verdict import KitFault, Prose, RuleName
from dispatcher.words import Directory, VarName
from tests.test_declarations import NONE_DECLARED, TomlSource, declaring

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = "unit_slice"
MARK = Prose("guard unit-slice:")
CaseId = NewType("CaseId", str)
Machine = Literal["claude", "work", "none", "missing", "broken"]
Status = Literal[0, 2]
C = CaseId
T = ShellText
P = Prose
MACHINES: dict[Machine, TomlSource | None] = {
    "claude": declaring(slice=TomlSource('[slice]\nname = "claude.slice"\n')),
    "work": declaring(slice=TomlSource('[slice]\nname = "work.slice"\n')),
    "none": NONE_DECLARED,
    "missing": None,
    "broken": declaring(slice=TomlSource('[slice]\nname = "claude"\n')),
}
NO_SLICE = P("names no slice, so its unit lands in app.slice")
NO_USER = P("names no --user, or a --system after it")
SR = "systemd-run --user --scope"
SCRIPTS = {"probes/run_all.sh": "true\n", "run.sh": "true\n"}


class Case(NamedTuple):
    """A call, the status it gets, and the lines its refusal holds."""

    call: ShellText
    status: Status
    lines: tuple[Prose, ...] = ()


class Places(NamedTuple):
    """The fixture's folders: one home per machine file under the base, the switch folder, the folder calls run in,
    which holds the scripts the calls run."""

    base: Path
    off: Path
    work: Path


def places() -> Places:
    """A fresh fixture, every rule but this one switched off."""
    base = Path(tempfile.mkdtemp()).resolve()
    at = Places(base, base / "off", base / "work")
    for folder in (at.off, at.work):
        folder.mkdir(parents=True)
    for name, text in SCRIPTS.items():
        script = at.work / name
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text)
    for machine, text in MACHINES.items():
        folder = base / machine / ".config" / "claude-guard"
        folder.mkdir(parents=True)
        if text is not None:
            (folder / "machine.toml").write_text(text)
    for entry in os.listdir(KIT / "rules"):
        stem, suffix = os.path.splitext(entry)
        if suffix == ".py" and not stem.startswith("_") and stem != RULE:
            (at.off / stem.replace("_", "-")).touch()
    return at


def judged(at: Places, call: ShellText, machine: Machine = "claude", tool: ShellText = T("Bash")) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call``, under ``machine``'s file."""
    home = at.base / machine
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)),
                        ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(home))}), home=home)
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(at.work),
              "tool_input": {"command": call}}
    return answer(EventBytes(json.dumps(fields).encode()), settings)


def line(spelled: ShellText, why: Prose) -> Prose:
    """The refusal's line for a systemd-run quoted as ``spelled``, kept out by ``why``."""
    return Prose(f"  * `{spelled}` {why}\n")


def outside(name: ShellText, declared: ShellText = T("claude.slice")) -> Prose:
    """Why a unit placed in a slice other than ``declared`` and those beneath it is kept out."""
    return Prose(f"places its unit in {name}, neither {declared} nor beneath it")


def unknown(text: ShellText) -> Prose:
    """Why a systemd-run holding the word ``text`` the call does not make known is kept out."""
    return Prose(f"holds `{text}`, a word the call does not make known")


def unread(word: ShellText) -> Prose:
    """Why a systemd-run holding the word ``word``, off the rule's list, is kept out."""
    return Prose(f"holds `{word}`, which the rule does not read: an option's value is given as --name=VALUE")


def refused(call: ShellText, why: Prose, quoted: ShellText | None = None) -> Case:
    """A refused case of one line, quoting ``call`` whole unless ``quoted`` says otherwise."""
    return Case(call, 2, (line(call if quoted is None else quoted, why),))


def nested(depth: Count) -> ShellText:
    """A systemd-run within claude.slice running ``depth`` carriers, each running the next."""
    text = "systemd-run --user --slice=claude.slice --scope true"
    for _ in range(depth):
        text = f"systemd-run --user --slice=claude.slice --scope bash -c {shlex.quote(text)}"
    return ShellText(text)


class Judged(unittest.TestCase):
    """A case table judged: each status, the rule's mark on a refusal, and each line its refusal holds."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def check(self, cases: dict[CaseId, Case], machine: Machine = "claude", tool: ShellText = T("Bash")) -> None:
        """Every case judged as it says."""
        for case, (call, status, lines) in cases.items():
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, machine, tool)
                self.assertEqual(got.status, status, got.stderr)
                if status == 2:
                    self.assertIn(MARK, got.stderr)
                else:
                    self.assertEqual(got.stderr, "")
                for each in lines:
                    self.assertIn(each, got.stderr)
                if lines:
                    self.assertEqual(got.stderr.count("\n  * "), len(lines), got.stderr)


class OldCases(Judged):
    """The old rule 6 cases whose verdict the new kit keeps, under claude.slice."""

    def test_refused(self) -> None:
        self.check({
            C("LIM30"): refused(T(f"{SR} -p MemoryMax=1G true"), NO_SLICE),
            C("LIM35"): refused(T("systemd-run --scope --slice=claude.slice true"), NO_USER),
            C("LIM36"): refused(T("systemd-run --user --slice=app.slice true"), outside(T("app.slice"))),
            C("LIM38"): refused(T(f"{SR} --slice=claudette.slice true"), outside(T("claudette.slice"))),
            C("LIM40"): refused(T(f"taskset -c 0-19 {SR} true"), NO_SLICE, T(f"{SR} true")),
            C("LIM42"): refused(T(f"PYTHON_CPU_COUNT=5 {SR} -q -p MemoryMax=24G -p MemorySwapMax=0 taskset -c 0-19 "
                                  "./probes/run_all.sh > store.log 2>&1"), NO_SLICE,
                                T(f"{SR} -q -p MemoryMax=24G -p MemorySwapMax=0 taskset ...")),
            C("LIM44"): refused(T(f"{SR} --slice=claude.slicex true"), outside(T("claude.slicex.slice"))),
        })

    def test_passed(self) -> None:
        self.check({
            C("LIM31"): Case(T(f"{SR} --slice=claude.slice -p MemoryMax=1G true"), 0),
            C("LIM33"): Case(T(f"{SR} -p Slice=claude.slice true"), 0),
            C("LIM34"): Case(T(f"{SR} --slice-inherit true"), 0),
            C("LIM37"): Case(T(f"{SR} --slice=claude-tests.slice true"), 0),
            C("LIM41"): Case(T(f"SL=claude.slice; {SR} --slice=$SL true"), 0),
            C("LIM43"): Case(T(f"PYTHON_CPU_COUNT=5 {SR} --slice=claude.slice -q taskset -c 0-19 "
                               "./probes/run_all.sh > store.log 2>&1"), 0),
            C("LIM45"): Case(T(f"{SR} --property=Slice=claude.slice true"), 0),
            C("LIM47"): Case(T("systemd-run --scope --user --slice=claude.slice true"), 0),
            C("LIM48"): Case(T(f"{SR} --slice-inherit --slice=bar true"), 0),
            C("LIM49"): Case(T(f"{SR} --slice=bar --slice-inherit true"), 0),
        })


class User(Judged):
    """A user unit: --user, with no --system after it."""

    def test_cases(self) -> None:
        self.check({
            C("--system after --user"): refused(T(f"{SR} --system --slice=claude.slice true"), NO_USER),
            C("--user after --system"): Case(T("systemd-run --system --user --slice=claude.slice true"), 0),
            C("neither"): refused(T("systemd-run --slice=claude.slice true"), NO_USER),
        })


class Placement(Judged):
    """The unit placed by --slice-inherit or by slices that are each the declared slice or one beneath it, in any of
    the three forms, `.slice` appended where a name has none."""

    def test_cases(self) -> None:
        self.check({
            C("--slice gets .slice appended"): Case(T(f"{SR} --slice=claude-tests true"), 0),
            C("-p Slice= gets .slice appended"): Case(T(f"{SR} -p Slice=claude-workers true"), 0),
            C("a stem and a dash is no slice"): refused(T(f"{SR} --slice=claude- true"), outside(T("claude-.slice"))),
            C("an empty --slice"): refused(T(f"{SR} --slice= true"), outside(T(".slice"))),
            C("the first slice outside"): refused(T(f"{SR} --slice=app.slice --slice=claude.slice true"),
                                                  outside(T("app.slice"))),
            C("the last slice outside"): refused(T(f"{SR} --slice=claude.slice --slice=app.slice true"),
                                                 outside(T("app.slice"))),
            C("-p Slice= outside beside --slice"): refused(T(f"{SR} -p Slice=app.slice --slice=claude.slice true"),
                                                           outside(T("app.slice"))),
            C("--property=Slice= outside"): refused(T(f"{SR} --property=Slice=app.slice true"),
                                                    outside(T("app.slice"))),
            C("--slice-inherit over a slice outside"): Case(T(f"{SR} --slice-inherit -p Slice=app.slice true"), 0),
            C("-p naming another property"): refused(T(f"{SR} -p MemoryMax=1G -p Slice true"), NO_SLICE),
        })


class List(Judged):
    """Every own word on the list, matched as written: the options taking no value, the others as --name=VALUE, -p
    and -E also with their value as the next word; any other spelling, and the options sending the unit to another
    manager, refuse."""

    def test_every_word(self) -> None:
        self.check({C("every option of systemd-run --help but six"): Case(T(
            "systemd-run --system --user --slice=claude.slice --no-ask-password --scope --slice-inherit --no-block -r "
            "--remain-after-exit --wait --send-sighup -d --same-dir -R --same-root-dir -t --pty -T --pty-late -P "
            "--pipe -q --quiet -v --verbose -G --collect --ignore-failure --no-pager --on-timezone-change "
            "--on-clock-change --unit=u --property=A=1 --description=d --expand-environment=no --service-type=exec "
            "--uid=u --gid=g --nice=1 --working-directory=/ --root-directory=/ --setenv=A=1 --output=cat --json=off "
            "--job-mode=fail --background=red --path-property=A=1 --socket-property=A=1 --on-active=1 --on-boot=1 "
            "--on-startup=1 --on-unit-active=1 --on-unit-inactive=1 --on-calendar=d --timer-property=A=1 -p A=1 "
            "-E A=1 -S --shell"), 0)})

    def test_cases(self) -> None:
        self.check({
            C("--slice NAME"): refused(T(f"{SR} --slice claude.slice true"), unread(T("--slice"))),
            C("--property Slice=NAME"): refused(T(f"{SR} --property Slice=claude.slice true"), unread(T("--property"))),
            C("the slice as --description's value"): refused(T(f"{SR} --description --slice=claude.slice true"),
                                                             unread(T("--description"))),
            C("-u NAME"): refused(T(f"{SR} -u --slice=claude.slice true"), unread(T("-u"))),
            C("-p with no value"): refused(T("systemd-run --user --slice=claude.slice -p"), unread(T("-p"))),
            C("--"): refused(T(f"{SR} --slice=claude.slice -- true"), unread(T("--"))),
            C("--prop is no --property"): refused(T(f"{SR} --prop=Slice=claude.slice true"),
                                                  unread(T("--prop=Slice=claude.slice"))),
            C("--slice-inh is no --slice-inherit"): refused(T(f"{SR} --slice-inh true"), unread(T("--slice-inh"))),
            C("--us is no --user"): refused(T("systemd-run --us --slice=claude.slice true"), unread(T("--us"))),
            C("-p with its value attached"): refused(T(f"{SR} -pSlice=claude.slice true"),
                                                     unread(T("-pSlice=claude.slice"))),
            C("-p ending a bundle"): refused(T(f"{SR} -qp Slice=claude.slice true"), unread(T("-qp"))),
            C("-h in a bundle"): refused(T(f"{SR} -qh"), unread(T("-qh"))),
            C("a flag given a value"): refused(T(f"{SR} --user=yes --slice=claude.slice true"),
                                               unread(T("--user=yes"))),
        })

    def test_elsewhere(self) -> None:
        tail = "--slice=claude.slice true"
        self.check({
            C("-H"): refused(T(f"{SR} -H h {tail}"), unread(T("-H"))),
            C("-H with its value attached"): refused(T(f"{SR} -Hh {tail}"), unread(T("-Hh"))),
            C("--host"): refused(T(f"{SR} --host h {tail}"), unread(T("--host"))),
            C("-M"): refused(T(f"{SR} -M box {tail}"), unread(T("-M"))),
            C("--machine=box"): refused(T(f"{SR} --machine=box {tail}"), unread(T("--machine=box"))),
            C("-C"): refused(T(f"{SR} -C c {tail}"), unread(T("-C"))),
            C("--capsule=c"): refused(T(f"{SR} --capsule=c {tail}"), unread(T("--capsule=c"))),
        })


class Exceptions(Judged):
    """The two exceptions: a word the call does not make known, among the own words or as the command, refuses
    before anything is read, and -h, --help and --version pass where every own word is on the list."""

    def test_unknown(self) -> None:
        self.check({
            C("--slice=$X"): refused(T(f"{SR} --slice=$X true"), unknown(T("--slice=$X"))),
            C("--slice \"$X\""): refused(T(f'{SR} --slice "$X" true'), unknown(T('"$X"'))),
            C("beside a slice that passes"): refused(T(f'{SR} -p "$P" --slice=claude.slice true'),
                                                     unknown(T('"$P"'))),
            C("an unknown long option"): refused(T(f"{SR} --$X --slice=claude.slice true"), unknown(T("--$X"))),
            C("before --help"): refused(T(f"{SR} --slice=$X --help"), unknown(T("--slice=$X"))),
            C("the command"): refused(T("systemd-run --user --slice=claude.slice $OPTS true"), unknown(T("$OPTS")),
                                      T("systemd-run --user --slice=claude.slice $OPTS ...")),
            C("the command, whatever its known prefix"): refused(T("systemd-run --user --slice=claude.slice run_$X"),
                                                                 unknown(T("run_$X"))),
            C("before a word off the list"): refused(T(f"{SR} --$X -M box --slice=claude.slice true"),
                                                     unknown(T("--$X"))),
            C("the command's other words"): Case(T("systemd-run --user --slice=claude.slice true $X"), 0),
        })

    def test_no_unit(self) -> None:
        self.check({
            C("--version"): Case(T("systemd-run --version"), 0),
            C("-h"): Case(T(f"{SR} -h"), 0),
            C("--help"): Case(T("systemd-run --help"), 0),
            C("--help after a word off the list"): refused(T("systemd-run -M box --help"), unread(T("-M"))),
            C("--help as --description's value"): refused(T(f"{SR} --description --help true"),
                                                          unread(T("--description"))),
            C("--help as -p's value"): refused(T(f"{SR} -p --help true"), NO_SLICE),
        })


class OwnWords(Judged):
    """A systemd-run's own words are those before the command the model records it running."""

    def test_cases(self) -> None:
        self.check({
            C("the first operand ends them"): refused(T(f"{SR} true --slice=claude.slice"), NO_SLICE,
                                                      T(f"{SR} true ...")),
            C("--help after the first operand"): refused(T(f"{SR} true --help"), NO_SLICE, T(f"{SR} true ...")),
            C("no command, then another"): Case(T("systemd-run --user --slice=claude.slice && echo done"), 0),
        })


class Nesting(Judged):
    """The commands a unit runs, as the call model reads them, each systemd-run judged alone."""

    def test_cases(self) -> None:
        ok = "systemd-run --user --slice=claude.slice --scope"
        app = "systemd-run --user --slice=app.slice --scope"
        self.check({
            C("a bare systemd-run inside"): refused(T(f"{ok} {SR} true"), NO_SLICE, T(f"{SR} true")),
            C("inherit inside app.slice"): refused(T(f"{app} {SR} --slice-inherit true"), outside(T("app.slice")),
                                                   T(f"{app} systemd-run ...")),
            C("inherit inside the system manager"): refused(
                T(f"systemd-run --scope --slice=claude.slice {SR} --slice-inherit true"), NO_USER,
                T("systemd-run --scope --slice=claude.slice systemd-run ...")),
            C("two refused"): Case(T(f"systemd-run --bogus {SR} true"), 2, (
                line(T("systemd-run --bogus systemd-run ..."), unread(T("--bogus"))), line(T(f"{SR} true"), NO_SLICE))),
            C("a carrier inside"): refused(T(f"{ok} bash -c '{SR} true'"), NO_SLICE, T(f"{SR} true")),
            C("a carrier's other commands"): Case(T(f"{ok} bash -c 'echo hi; {ok} true'"), 0),
            C("a wrapper inside"): refused(T(f"{ok} env A=1 {SR} true"), NO_SLICE, T(f"{SR} true")),
            C("--version inside a carrier"): Case(T(f"{ok} bash -c 'systemd-run --version'"), 0),
            C("--version runs no command"): refused(T(f"systemd-run --version bash -c '{SR} true'"), unread(T("bash")),
                                                    T(f"systemd-run --version bash -c {SR} true")),
            C("four carriers deep"): Case(nested(Count(4)), 0),
            C("a carrier running a script"): Case(T(f"{ok} bash run.sh"), 0),
            C("no command"): Case(T("systemd-run --user --slice=claude.slice"), 0),
            C("a scope's carrier runs in the call's directory"): Case(
                T(f"{ok} bash -c '{SR} --slice=claude-$PWD true'"), 0),
        })

    def test_unreadable(self) -> None:
        cases = {C("an unknown carrier text"): (T('systemd-run --user --slice=claude.slice --scope bash -c "$CMD"'),
                                                P("is not known from the call")),
                 C("a gap in a carrier's text"): (
                     T("""systemd-run --user --slice=claude.slice --scope bash -c 'bash -c "$CMD"'"""),
                     P("is not known from the call")),
                 C("five carriers deep"): (nested(Count(5)), P("more than 4 deep")),
                 C("a zsh command"): (T("systemd-run --user --slice=claude.slice --scope zsh -c 'systemd-run true'"),
                                      P("it runs zsh, which the kit does not read: `systemd-run --user "
                                        "--slice=claude.slice --scope zsh -c 'systemd-run true'`.\n" + FOREIGN_FORM)),
                 C("zsh by its name alone"): (T("systemd-run --user --slice=claude.slice --scope zsh --version"),
                                              P("it runs zsh, which the kit does not read: `systemd-run --user "
                                                "--slice=claude.slice --scope zsh --version`")),
                 C("a fish command past a wrapper"): (
                     T('systemd-run --user --slice=claude.slice --scope env A=1 fish -c "ls"'),
                     P('it runs fish, which the kit does not read: `systemd-run --user --slice=claude.slice --scope '
                       'env A=1 fish -c "ls"`')),
                 C("a fish command inside a carrier"): (
                     T("""systemd-run --user --slice=claude.slice --scope bash -c 'env fish -c "ls"'"""),
                     P('it runs fish, which the kit does not read: `env fish -c "ls"`'))}
        for case, (call, said) in cases.items():
            with self.subTest(case=case):
                got = judged(self.at, call)
                self.assertEqual(got.status, 2, got.stderr)
                self.assertIn(said, got.stderr)


class Reach(Judged):
    """What the rule judges: Bash and Monitor calls, every command of the call model by its name alone."""

    def test_monitor(self) -> None:
        self.check({C("Monitor"): refused(T(f"{SR} true"), NO_SLICE)}, tool=T("Monitor"))

    def test_cases(self) -> None:
        self.check({
            C("a carrier of the call"): refused(T(f"bash -c '{SR} true'"), NO_SLICE, T(f"{SR} true")),
            C("by path"): refused(T("/usr/bin/systemd-run --user --scope true"), NO_SLICE),
            C("an operand naming it"): Case(T(f"echo {SR} true"), 0),
            C("a search naming it"): Case(T("git grep -n systemd-run -- tools"), 0),
        })


class Machines(Judged):
    """The slice is the machine's declaration; a machine declaring none holds nothing, and a machine without its file
    refuses the call."""

    def test_another_slice(self) -> None:
        self.check({
            C("its slice"): Case(T(f"{SR} --slice=work.slice true"), 0),
            C("beneath it"): Case(T(f"{SR} --slice=work-x.slice true"), 0),
            C("inherited"): Case(T(f"{SR} --slice-inherit true"), 0),
            C("claude.slice"): refused(T(f"{SR} --slice=claude.slice true"),
                                       outside(T("claude.slice"), T("work.slice"))),
        }, machine="work")

    def test_no_slice(self) -> None:
        self.check({C("bare"): Case(T("systemd-run --scope true"), 0)}, machine="none")
        got = judged(self.at, T("systemd-run --scope true"), "missing")
        file = self.at.base / "missing" / ".config" / "claude-guard" / "machine.toml"
        self.assertEqual(got.status, 2, got.stderr)
        self.assertTrue(got.stderr.startswith(f"guard kit: the machine file {file} is missing: "), got.stderr)

    def test_no_systemd_run_reads_nothing(self) -> None:
        home = self.at.base / "broken"
        rule = loaded(FolderPath(str(KIT / "rules" / f"{RULE}.py")), RuleName("unit-slice"))
        reading = ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(home))})

        def asked(call: ShellText) -> Event:
            """A PreToolUse event of a Bash call of ``call``, its declarations those of the broken machine's home."""
            return Event({"hook_event_name": "PreToolUse", "tool_name": ToolName("Bash"),
                          "cwd": Directory(str(self.at.work)), "tool_input": {"command": call}}, reading, home)

        self.assertIsNone(rule.judge(asked(T("echo hi"))))
        with self.assertRaises(KitFault) as caught:
            _ = rule.judge(asked(T(f"{SR} true")))
        self.assertIn("slice.name", caught.exception.reason)


class Refusal(Judged):
    """The refusal's whole text: the lead naming the slice, each line, and the form that passes."""

    def test_text(self) -> None:
        got = judged(self.at, T(f"{SR} true; systemd-run --user --slice=app.slice true"), "work")
        self.assertEqual(got.stderr, """guard unit-slice: this call starts a systemd unit outside work.slice, the \
machine's slice, and its memory and CPU caps.

  * `systemd-run --user --scope true` names no slice, so its unit lands in app.slice
  * `systemd-run --user --slice=app.slice true` places its unit in app.slice, neither work.slice nor beneath it

A user unit naming no slice lands in app.slice, outside every cap. Name the user manager and the slice:

  systemd-run --user --slice=work.slice ...

or a slice beneath it, --slice=work-NAME.slice, or --slice-inherit, which keeps the unit in the slice the call
runs in.
""")


if __name__ == "__main__":
    unittest.main()
