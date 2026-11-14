"""The heavy-output rule, through the dispatcher with every other rule switched off: a Bash or Monitor call running a
command heavy() answers a row for is refused where that command's output or errors end outside a file, a device or
proc path among them, where its input is a pipe, and in a Monitor call whatever its streams; and where the call runs
other work beside it or runs head, and wherever Python code starts it; one refusal lists every such command with what
it found, then the other work and each head, and names the form that passes. A command whose name the call does not make known is
judged as a heavy one.

The cases of the old heavy-run-guard SHAPE list and V13c, V14b, V14c and V16b whose verdict rests on where a heavy
command's streams end, its input or Monitor keep their ids and texts, run from the work tree of
declarations/examples/aletheia.toml in place of the old quiet tree, the scratch folder (@S@) a plain one; the cases
whose old verdict rested on taskset or its CPU set, on a name the call does not make known passing (A27), or on a log
under /dev/shm/ (V14c) or an `exec` beside the run (V14b) passing, are in Changed with their new verdict. So do the old
cases whose verdict rests on sharing the call or on head (A19 to A21, A42, F01c, M13, R09b, V06, V06b, V07, V14b,
V16), M13, V07 and V14b in Changed. So do A51 and A57 to A60, whose heavy command runs inside a carrier the model reads, each refused as the
old kit refused it, by this rule or as a call the kit cannot read. Every git run reads no user or system configuration."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, Unpack
from unittest import mock

from dispatcher.core import Answer, EventBytes, FolderPath, HookEvent, Settings, ToolName, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_declarations import declaring
from tests.test_heavy import EXAMPLE, NO_CONFIG, repository
from tests.test_heavy import TomlSource as GuardSource

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = "heavy_output"
MARK = Prose("guard heavy-output: this call runs")
BASH = ToolName("Bash")
MONITOR = ToolName("Monitor")
PASSED = Answer(0, Prose(""), Prose(""))
UNREADABLE = Prose("guard kit: this call cannot be read: ")
Source = Literal["heavy-output", "unreadable"]
SOURCES: dict[Source, Prose] = {"heavy-output": MARK, "unreadable": UNREADABLE}
CaseId = NewType("CaseId", str)
Millis = NewType("Millis", int)
ScriptText = NewType("ScriptText", str)
Status = Literal[0, 2]
type Case = tuple[CaseId, ShellText, ToolName, Status]
type Inside = tuple[CaseId, ShellText, ShellText | None, Source]
C = CaseId
T = ShellText
P = Prose
X = "@S@/x.log"
SCRIPTS = {"benchmarks/run_all.sh": ScriptText("true\n"), "probes/run_all.sh": ScriptText('"$1/t"\n'),
           "tools/run_ci.py": ScriptText("pass\n")}


class Options(TypedDict, total=False):
    """The Bash tool's options a case gives beside its command."""

    run_in_background: bool
    timeout: Millis


class ToolInput(Options):
    """A call's tool input: its command and the Bash tool's options."""

    command: ShellText


def blessed(command: ShellText) -> ShellText:
    """``command`` in the old kit's blessed shape, under taskset, its log in the scratch folder."""
    return T(f'taskset -c 0-19 {command} > "@S@/x.log" 2>&1; echo "EXIT=$?" >> "@S@/x.log"')


def logged(command: ShellText) -> ShellText:
    """``command`` in the form a refusal names, its log "$LOG"."""
    return T(f'{command} > "$LOG" 2>&1; echo "EXIT=$?" >> "$LOG"')


class Places(NamedTuple):
    """The fixture's folders: its base, the home holding the machine file, the switch folder, the aletheia work tree,
    a repository declaring no heavy row, and the scratch folder."""

    base: Path
    home: Path
    off: Path
    work: Path
    other: Path
    scratch: Path


def places() -> Places:
    """A fresh fixture, every rule but heavy-output switched off and the machine file declaring every fact absent."""
    base = Path(tempfile.mkdtemp()).resolve()
    at = Places(base, base / "home", base / "off", base / "aletheia", base / "other", base / "scratch")
    for folder in (at.home / ".config" / "claude-guard", at.off, at.scratch):
        folder.mkdir(parents=True)
    _ = (at.home / ".config" / "claude-guard" / "machine.toml").write_text(declaring())
    repository(at.work, GuardSource(EXAMPLE.read_text()))
    repository(at.other, GuardSource("schema = 1\n"))
    for name in ("rust", "python", "benchmarks", "probes", "tools"):
        (at.work / name).mkdir()
    for name, text in SCRIPTS.items():
        _ = (at.work / name).write_text(text)
    (at.work / "cpp" / "build").mkdir(parents=True)
    _ = (at.work / "cpp" / "build" / "unit_tests").write_bytes(b"\x7fELF\x02\x01\x01\x00")
    for module in (KIT / "rules").glob("*.py"):
        if not module.stem.startswith("_") and module.stem != RULE:
            (at.off / module.stem.replace("_", "-")).touch()
    return at


def filled(at: Places, text: ShellText) -> ShellText:
    """``text`` with each @NAME@ marker spelled as the fixture's folder."""
    return T(text.replace("@S@", str(at.scratch)).replace("@OTHER@", str(at.other)))


def judged(at: Places, call: ShellText, tool: ToolName = BASH, event: HookEvent = "PreToolUse",
           shell: ShellText | None = None, **options: Unpack[Options]) -> Answer:
    """The dispatcher's answer to ``event`` for ``call`` from the work tree, a Bash call run_in_background unless
    ``options`` say otherwise, as the old cases' Bash calls were; SHELL is ``shell``, unknown where it is None."""
    env = {VarName("HOME"): ShellText(str(at.home))} | ({VarName("SHELL"): shell} if shell else {})
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)), ReadSettings(READER, Seconds(5.0), env),
                        home=at.home)
    given = ToolInput(command=filled(at, call))
    if tool == BASH:
        given |= Options(run_in_background=True) | options
    fields = {"hook_event_name": event, "tool_name": tool, "cwd": str(at.work), "tool_input": given}
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
        for case, call, tool, want in cases:
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, tool)
                self.assertEqual((got.status, MARK in got.stderr), (want, want == 2), got.stderr)

    def told(self, call: ShellText, *lines: Prose, tool: ToolName = BASH) -> Prose:
        """``call`` refused by this rule, its refusal listing exactly ``lines``, markers filled; its stderr."""
        got = judged(self.at, call, tool)
        self.assertEqual((got.status, MARK in got.stderr), (2, True), got.stderr)
        for line in lines:
            self.assertIn(f"\n  * {filled(self.at, T(line))}\n", got.stderr)
        self.assertEqual(got.stderr.count("\n  * "), len(lines), got.stderr)
        return got.stderr

    def passes(self, call: ShellText, tool: ToolName = BASH) -> None:
        """``call`` passed."""
        self.assertEqual(judged(self.at, call, tool), PASSED)


CARRIED: list[Case] = [
    (C("A01"), T("cargo test"), BASH, 2),
    (C("A02"), blessed(T("cargo test")), BASH, 0),
    (C("A03"), T("(cd rust && cargo test)"), BASH, 2),
    (C("A04"), T("timeout 600 cargo test"), BASH, 2),
    (C("A05"), T("bash -c 'cargo test'"), BASH, 2),
    (C("A06"), T("for i in 1 2; do cargo test; done"), BASH, 2),
    (C("A07"), T("nice -n 10 cargo test"), BASH, 2),
    (C("A13"), T("python/.venv/bin/python -m pytest tests/"), BASH, 2),
    (C("A14"), T("cargo mutants --in-place"), BASH, 2),
    (C("A15"), T("agda +RTS -M16G -RTS src/Aletheia/Main.agda"), BASH, 2),
    (C("A16"), T("bash benchmarks/run_all.sh --frames 10"), BASH, 2),
    (C("A17"), T("python3 -m tools.coverage_run"), BASH, 2),
    (C("A18"), T("rtk cargo test"), BASH, 2),
    (C("A22"), blessed(T("cabal run shake -- build")), BASH, 0),
    (C("A23"), T("python3 -m tools.check_build_incremental"), BASH, 2),
    (C("A24"), T("echo start\ncargo test"), BASH, 2),
    (C("A25"), T("cd rust\ncargo test --release"), BASH, 2),
    (C("A27b"), T('py=python/.venv/bin/python; "$py" -m tools.mutation_run'), BASH, 2),
    (C("A28"), T("cargo +nightly test"), BASH, 2),
    (C("A29"), T("bash -x probes/run_all.sh"), BASH, 2),
    (C("A30b"), T(f"{{ taskset -c 0-19 cargo test; }} > {X} 2>&1"), BASH, 0),
    (C("A31"), T(f"taskset -c 0-19 cargo test >& {X}"), BASH, 0),
    (C("A32"), T("taskset -c 0-19 cargo test > /dev/stdout 2>&1"), BASH, 2),
    (C("A33"), T(f"taskset -c 0-19 cargo test > {X} 3>&1"), BASH, 2),
    (C("A38"), T("env -u RUSTFLAGS cargo test"), BASH, 2),
    (C("A40"), T("echo $(cargo test)"), BASH, 2),
    (C("A43"), T(f"taskset -c 0-19 bash -c 'cargo test' > {X} 2>&1"), BASH, 0),
    (C("A44"), T(f"> {X} cargo test"), BASH, 2),
    (C("A45"), T("python3 -u tools/run_ci.py"), BASH, 2),
    (C("A46"), T("go -C go test ./aletheia/"), BASH, 2),
    (C("A47"), T("cabal run -v0 shake -- build"), BASH, 2),
    (C("A48"), T("if cargo test; then echo ok; fi"), BASH, 2),
    (C("A49"), T("cargo test | grep x"), MONITOR, 2),
    (C("A50"), T("n=${#x}; cargo test"), BASH, 2),
    (C("A56"), T("f() { cargo test; }; f"), BASH, 2),
    (C("V16b"), T(f"taskset -c 0-19 cargo test > {X} 2> @S@/err.log"), BASH, 0),
    (C("A19"), T(blessed(T("cargo test")) + " && git status"), BASH, 2),
    (C("A20"), T("git log --oneline | head -5"), BASH, 0),
    (C("A21"), T("taskset -c 0-19 cargo test > @S@/head.log 2>&1"), BASH, 0),
    (C("A42"), T(blessed(T("cargo test")) + f'; echo "$(tail -5 {X})"'), BASH, 2),
    (C("F01c"), T("taskset -c 0-19 cargo test; gh pr comment 1 --body \"$(cat <<'EOF'\nThe hook's fix landed.\nEOF\n)\""),
     BASH, 2),
    (C("R09b"), T("(cd cpp/build && ./unit_tests --order decl 2>&1 | grep FAILED | head -6)"), BASH, 2),
    (C("V06"), T('cd "$(git rev-parse --show-toplevel)" && ' + blessed(T("cargo test"))), BASH, 0),
    (C("V06b"), T('LOG=@S@/x-$(date +%s).log; taskset -c 0-19 cargo test > "$LOG" 2>&1'), BASH, 0),
    (C("V16"), T(blessed(T("cargo test")) + f'; echo "$(< {X})"'), BASH, 2),
]
SCRIPT = T("script -qc 'cargo test' /dev/null")
INSIDE: list[Inside] = [  # each refused by the old kit, which matched the heavy command inside its carrier
    (C("A51"), SCRIPT, T("/bin/bash"), "heavy-output"),
    (C("A51"), SCRIPT, None, "unreadable"),  # script -c runs its text in $SHELL, which the call does not make known
    (C("A57"), T("echo cargo test | bash"), None, "unreadable"),  # a shell's script on a pipe is not known
    (C("A58"), T("bash <<'EOF'\ncargo test\nEOF"), None, "heavy-output"),
    (C("A59"), T("xargs -0 -r cargo test < /dev/null"), None, "heavy-output"),
    (C("A60"), T("find rust -maxdepth 0 -exec cargo test \\;"), None, "heavy-output"),
]

V07 = T("echo \"$(cat <<'EOF'\nwe don't\nEOF\n)\" && cargo test")
CHANGED: list[Case] = [  # the old verdict rested on taskset or its CPU set, which the session check now holds, or on an
    # exception the rule no longer keeps
    (C("A27"), T('"$py" -m tools.mutation_run'), BASH, 2),  # old 0: a name the call does not make known is heavy
    (C("A08"), T(f"taskset -c 0-19 cargo test 2>&1 > {X}"), BASH, 2),  # still 2: its errors end in the tool result
    (C("A09"), T(f"taskset -c 0-9 cargo test > {X} 2>&1"), BASH, 0),
    (C("A10"), T(f"taskset --cpu-list=0-19 cargo test > {X} 2>&1"), BASH, 0),
    (C("A30"), T(f"{{ cargo test; }} > {X} 2>&1"), BASH, 0),  # old 2: a group, no taskset
    (C("A34"), T(f"taskset 0xffffff cargo test > {X} 2>&1"), BASH, 0),  # old 2: a mask with CPUs 20-23
    (C("A35"), T(f"taskset 0xfffff cargo test > {X} 2>&1"), BASH, 0),
    (C("A36"), T(f"taskset -ac 0-19 cargo test > {X} 2>&1"), BASH, 0),
    (C("A37"), T(f"taskset -c0-19 cargo test > {X} 2>&1"), BASH, 0),
    (C("V13c"), T(f"time {{ cargo test; }} > {X} 2>&1"), BASH, 0),  # old 2: time { } with no taskset
    (C("A39"), T(f"nohup cargo test > {X} 2>&1 &"), BASH, 0),  # old 2: the & job's lifetime is job_lifetime's
    (C("M13"), T("taskset -c 0-$(($(nproc) - 2)) cargo test > /tmp/x.log 2>&1"), BASH, 0),  # old 2: its CPU list
    (C("V07"), V07, BASH, 2),  # still 2, for its streams alone: a substitution before the run is no other work
    (C("V14c"), T("taskset -c 0-19 cargo test > /dev/shm/x.log 2>&1"), BASH, 2),  # old 0: /dev/shm/ is a device
    (C("V14b"), T(f"exec > {X} 2>&1; taskset -c 0-19 cargo test"), BASH, 2),  # old 0: an exec is other work
]
LOGGED = logged(T("cargo test"))
OTHER = "it runs other work beside them: "
CUT = "it runs head, which reads output cut short: "
STARTED = 'Python code starts it, so run it from the shell, `> "$LOG" 2>&1`, alone in its call'
CALLED = P("`cargo test`: its output ends in the tool result, its errors end in the tool result")


class Carried(Fixture):
    """The old cases whose verdict the new kit keeps."""

    def test_the_old_cases(self) -> None:
        self.check(CARRIED)

    def test_the_old_cases_inside_a_carrier(self) -> None:  # refused, of the two sources exactly the case's
        for case, call, shell, source in INSIDE:
            with self.subTest(case=case, shell=shell):
                got = judged(self.at, call, shell=shell)
                found = [named for named, mark in SOURCES.items() if mark in got.stderr]
                self.assertEqual((got.status, found), (2, [source]), got.stderr)

    def test_the_blessed_shape_in_the_foreground(self) -> None:  # A26, A26b: run_in_background is not read
        for options in (Options(run_in_background=False), Options(run_in_background=False, timeout=Millis(600000))):
            with self.subTest(options=options):
                self.assertEqual(judged(self.at, blessed(T("cargo test")), **options), PASSED)


class Changed(Fixture):
    """The old cases whose ground was taskset, a name the call does not make known, a log under /dev/shm/ or an
    `exec` beside the run, judged by the rule as it reads now."""

    def test_the_cases(self) -> None:
        self.check(CHANGED)

    def test_v07_is_refused_for_its_streams_alone(self) -> None:
        _ = self.told(V07, CALLED)


class Ends(Fixture):
    """Each end a stream is lost in is named: the reach, or the device or proc path."""

    def test_the_tool_result(self) -> None:
        _ = self.told(T("cargo test"), CALLED)

    def test_closed_and_null(self) -> None:
        _ = self.told(T("cargo test >&- 2>/dev/null"),
                      P("`cargo test`: its output ends in a closed descriptor, its errors end in /dev/null"))

    def test_a_pipe(self) -> None:
        _ = self.told(T("cargo test 2>&1 | tail -5"), P("`cargo test`: its output ends in a pipe, its errors end in a pipe"),
                      P(OTHER + "`tail -5`"))

    def test_a_substitution(self) -> None:
        _ = self.told(T("x=$(cargo test 2>&1)"), P("`cargo test`: its output ends in a command or process substitution, "
                                                   "its errors end in a command or process substitution"))

    def test_a_here_string(self) -> None:
        _ = self.told(T("cargo test 1<<<x 2>&1"), P("`cargo test`: its output ends in a heredoc or here-string, its "
                                                    "errors end in a heredoc or here-string"))

    def test_an_end_the_call_does_not_make_known(self) -> None:
        _ = self.told(T('cargo test >&"$fd" 2>&1'), P("`cargo test`: its output ends in an end the call does not make "
                                                      "known, its errors end in an end the call does not make known"))

    def test_a_device_and_a_proc_file(self) -> None:
        _ = self.told(T("cargo test > /dev/tty 2> /proc/self/fd/2"),
                      P("`cargo test`: its output ends in /dev/tty, its errors end in /proc/self/fd/2"))

    def test_errors_alone(self) -> None:
        _ = self.told(T(f"cargo test > {X}"), P("`cargo test`: its errors end in the tool result"))


class Paths(Fixture):
    """A file's path is placed against the command's directory and normalised before it is judged."""

    def test_a_log_passes(self) -> None:
        for call in (f"cargo test > {X} 2>&1", "cargo test > x.log 2>&1", "cargo test > /dev/../tmp/x.log 2>&1"):
            with self.subTest(call=call):
                self.passes(T(call))

    def test_a_log_under_dev_shm(self) -> None:  # V14c: a log kept in memory is a device like any other
        _ = self.told(T("cargo test > /dev/shm/x.log 2>&1"),
                      P("`cargo test`: its output ends in /dev/shm/x.log, its errors end in /dev/shm/x.log"))

    def test_the_form_that_passes(self) -> None:
        self.passes(logged(T("cargo test")))

    def test_relative_to_the_commands_directory(self) -> None:
        _ = self.told(T("cd /dev && cargo test > tty 2> shm/x.log"),
                      P("`cargo test`: its output ends in /dev/tty, its errors end in /dev/shm/x.log"))

    def test_a_doubled_slash(self) -> None:
        _ = self.told(T("cargo test > //dev/tty 2>&1"),
                      P("`cargo test`: its output ends in /dev/tty, its errors end in /dev/tty"))

    def test_out_of_shm_by_dot_dot(self) -> None:
        _ = self.told(T("cargo test > /dev/shm/../tty 2>&1"),
                      P("`cargo test`: its output ends in /dev/tty, its errors end in /dev/tty"))

    def test_relative_from_a_directory_the_call_does_not_make_known(self) -> None:
        self.passes(T('cd "$x" && cargo test > dev/tty 2>&1'))


class Input(Fixture):
    """A heavy command's input is no pipe; any other input passes."""

    def test_a_pipe(self) -> None:
        _ = self.told(T(f"echo y | cargo test > {X} 2>&1"), P("`cargo test`: its input is a pipe"))

    def test_a_coprocess(self) -> None:
        _ = self.told(T("coproc cargo test"), P("`cargo test`: its output ends in a pipe, its errors end in the tool "
                                                "result, its input is a pipe"))

    def test_other_inputs(self) -> None:
        for call in (f"cargo test < <(echo y) > {X} 2>&1", f"cargo test <<< y > {X} 2>&1",
                     f"cargo test < /dev/tty > {X} 2>&1"):
            with self.subTest(call=call):
                self.passes(T(call))


class Monitor(Fixture):
    """A Monitor call starts no heavy command, whatever its streams, nor one whose name the call does not make
    known; a light one passes."""

    def test_a_logged_run(self) -> None:
        _ = self.told(logged(T("cargo test")), P("`cargo test`: a Monitor call starts it"), tool=MONITOR)

    def test_light(self) -> None:
        self.passes(T("tail -f @S@/x.log"), MONITOR)

    def test_unknown(self) -> None:
        _ = self.told(T('"$py" -m tools.mutation_run'),
                      P('`"$py" -m tools.mutation_run`: its output ends in the tool result, its errors end in the tool '
                        'result, a Monitor call starts it'), tool=MONITOR)


class Unknown(Fixture):
    """A command whose name the call does not make known is judged as a heavy one."""

    def test_piped_is_refused(self) -> None:
        _ = self.told(T('"$X" -m pytest | tail -3'),
                      P('`"$X" -m pytest`: its output ends in a pipe, its errors end in the tool result'),
                      P(OTHER + "`tail -3`"))

    def test_logged_alone_passes(self) -> None:
        self.passes(T('"${cmd[@]}" > "$LOG" 2>&1'))

    def test_a_bracket_test_is_known(self) -> None:
        self.passes(T("[ -f x ] && echo y"))


class Refusal(Fixture):
    """One refusal lists every heavy command refused and no other, then the form that passes."""

    def test_every_heavy_command_and_no_other(self) -> None:
        _ = self.told(T(f"cargo test; agda src/Aletheia/Main.agda > {X} 2>&1; git status; ctest --test-dir cpp/build"),
                      CALLED, P("`ctest --test-dir cpp/build`: its output ends in the tool result, its errors end in "
                                "the tool result"), P(OTHER + "`git status`"))

    def test_the_whole_text(self) -> None:  # the form that passes, run_in_background, and no taskset
        self.assertEqual(judged(self.at, T("cargo test")), Answer(2, P(""), P(
            "guard heavy-output: this call runs heavy commands without keeping their output whole in a log read in later "
            f"calls.\n\n  * {CALLED}\n\nA heavy run's output and errors go whole to a log, so a long run never has to "
            "run twice to be read. Give the run a\ncall of its own, run with run_in_background, in the form\n\n  "
            '<heavy command> > "$LOG" 2>&1; echo "EXIT=$?" >> "$LOG"\n\nits input no pipe, its log a file outside '
            "/dev/ and /proc/, no command beside it but\n`cd`, `pushd`, `popd`, `echo`, `printf`, "
            "`mkdir`, `export`, `set`, `true`, `:`, a substitution only before it and no head in the call,\nand query "
            "the log in later calls with grep -c, grep -n or sed -n of a range.\nA Monitor call watches work and "
            "starts none.\n")))


class Beside(Fixture):
    """A heavy run has its call to itself: each other command of the call's own text that runs a word or reads a file
    whole as `$(< FILE)` does is named, once and in the order it runs, but for a name of BESIDE, a wrapper or a start
    that only runs what follows it, and a substitution run before the first heavy command."""

    def test_other_work_is_named(self) -> None:  # A19
        _ = self.told(T(blessed(T("cargo test")) + " && git status"), P(OTHER + "`git status`"))

    def test_the_names_that_pass(self) -> None:
        self.passes(T(f"cd @S@; pushd @S@; popd; echo x; printf x; mkdir -p @S@/d; export A=1; set -e; true; :; {LOGGED}"))

    def test_a_name_by_its_path(self) -> None:
        self.passes(T(f"/usr/bin/echo x; {LOGGED}"))

    def test_a_command_of_no_words(self) -> None:
        self.passes(T(f"LOG=@S@/x.log; {LOGGED}"))
        self.passes(T(f'{LOGGED}; echo "$(<> {X})"'))  # one redirection that reads no file into the substitution

    def test_an_exec_that_only_redirects(self) -> None:  # V14b: an exec is other work, its words or none
        _ = self.told(T(f"exec 3>&1; {LOGGED}"), P(OTHER + "`exec`"))  # a command's text holds no redirection
        _ = self.told(T(f"{LOGGED}; exec -a x"), P(OTHER + "`exec -a x`"))

    def test_a_file_read_whole(self) -> None:  # V16: bash 5.3 reads a file into $( ) for a lone `<` onto fd 0
        for call in (blessed(T("cargo test")) + f'; echo "$(< {X})"', f'{LOGGED}; echo "$(0< {X})"'):
            with self.subTest(call=call):
                _ = self.told(T(call), P(OTHER + f"`$(< {X})`"))
        _ = self.told(T(f'{LOGGED}; echo "$(cat < {X})"'), P(OTHER + "`cat`"))

    def test_what_reads_no_file_whole(self) -> None:
        self.passes(T(f'{LOGGED}; echo "$(< {X} 2>/dev/null)" "$(<> {X})" "$( (< {X}) )"; true >(< {X})'))

    def test_a_name_the_call_does_not_make_known(self) -> None:
        _ = self.told(T(f'{LOGGED}; "$tool" x'),
                      P('`"$tool" x`: its output ends in the tool result, its errors end in the tool result'))

    def test_a_command_before_the_run(self) -> None:
        _ = self.told(T(f"git status && {LOGGED}"), P(OTHER + "`git status`"))

    def test_a_substitution_before_the_run(self) -> None:  # V06, and V07 before a logged run
        for call in ('cd "$(git rev-parse --show-toplevel)" && ' + LOGGED,
                     "echo \"$(cat <<'EOF'\nwe don't\nEOF\n)\" && " + LOGGED):
            with self.subTest(call=call):
                self.passes(T(call))

    def test_a_substitution_after_the_run(self) -> None:  # A42
        _ = self.told(T(blessed(T("cargo test")) + f'; echo "$(tail -5 {X})"'), P(OTHER + f"`tail -5 {X}`"))

    def test_a_substitution_between_two_runs(self) -> None:
        _ = self.told(T(f"{LOGGED}; x=$(git status); agda src/Aletheia/Main.agda > {X} 2>&1"), P(OTHER + "`git status`"))

    def test_each_named_once_in_the_order_it_runs(self) -> None:
        _ = self.told(T(f"{LOGGED}; git status; ls; git status"), P(OTHER + "`git status`, `ls`"))

    def test_a_wrapper(self) -> None:
        self.passes(T(f"{LOGGED}; nice -n 5 echo x"))

    def test_a_carrier(self) -> None:  # its start passes, its text is the call's own
        _ = self.told(T(f"{LOGGED}; bash -c 'git status'"), P(OTHER + "`git status`"))

    def test_a_find_action(self) -> None:  # a find only starts an action a heavy run is; one that is not is other work
        self.passes(T(r'find rust -maxdepth 0 -exec cargo test \; > "$LOG" 2>&1'))
        _ = self.told(T(LOGGED + r"; find . -exec grep y {} \;"), P(OTHER + r"`find . -exec grep y {} \;`"))

    def test_a_script(self) -> None:  # its commands are not the call's own; its start passes where a heavy one runs in it
        self.passes(T("printf 'git status\\ncargo test\\n' > @S@/h.sh; bash @S@/h.sh > \"$LOG\" 2>&1"))
        _ = self.told(T(f"printf 'git status\\n' > @S@/l.sh; {LOGGED}; bash @S@/l.sh"), P(OTHER + "`bash @S@/l.sh`"))

    def test_python(self) -> None:  # a light Python run beside a heavy one is other work
        light = "python3 -c \"import subprocess; subprocess.run(['git', 'status'])\""
        _ = self.told(T(f"{LOGGED}; {light}"), P(OTHER + f"`{light}`"))

    def test_no_heavy_run(self) -> None:
        self.passes(T("git status; ls"))


class Python(Fixture):
    """A heavy command Python code starts is refused whatever its streams, named with the form that passes; a heavy
    Python interpreter run from the shell with its log passes."""

    def test_a_start_writing_a_log(self) -> None:
        start = 'subprocess.run(["cargo", "test"], stdout=open(log, "w"), stderr=subprocess.STDOUT)'
        _ = self.told(T(f'python3 - <<EOF\nimport subprocess\nlog = "x.log"\n{start}\nEOF'), P(f"`{start}`: {STARTED}"))

    def test_a_start_whose_interpreter_has_a_log(self) -> None:
        start = "subprocess.run(['cargo', 'test'])"
        _ = self.told(T(f"python3 -c \"import subprocess; subprocess.run(['git', 'status']); {start}\" > \"$LOG\" 2>&1"),
                      P(f"`{start}`: {STARTED}"))

    def test_a_carrier_python_starts(self) -> None:
        _ = self.told(T("python3 -c \"import subprocess; subprocess.run(['bash', '-c', 'cargo test > x.log 2>&1'])\""),
                      P(f"`cargo test`: {STARTED}"))

    def test_in_a_monitor_call(self) -> None:
        start = "subprocess.run(['cargo', 'test'])"
        _ = self.told(T(f"python3 -c \"import subprocess; {start}\" > \"$LOG\" 2>&1"),
                      P(f"`{start}`: a Monitor call starts it, {STARTED}"), tool=MONITOR)

    def test_a_heavy_interpreter_with_its_log(self) -> None:
        self.passes(T('python3 -u tools/run_ci.py > "$LOG" 2>&1'))


class Head(Fixture):
    """No head runs in a call that runs a heavy command, in its own text, a script or Python code alike."""

    def test_in_the_calls_text(self) -> None:
        _ = self.told(T(f'{LOGGED}; head -3 "$LOG"'), P(OTHER + '`head -3 "$LOG"`'), P(CUT + '`head -3 "$LOG"`'))

    def test_beside_a_bare_run(self) -> None:  # R09b
        _ = self.told(T("(cd cpp/build && ./unit_tests --order decl 2>&1 | grep FAILED | head -6)"),
                      P("`./unit_tests --order decl`: its output ends in a pipe, its errors end in a pipe"),
                      P(OTHER + "`grep FAILED`, `head -6`"), P(CUT + "`head -6`"))

    def test_in_a_script_by_a_wrapper_and_a_path(self) -> None:
        _ = self.told(T("printf 'cargo test\\ntimeout 5 /usr/bin/head -3 x\\n' > @S@/hw.sh; bash @S@/hw.sh > \"$LOG\" 2>&1"),
                      P(CUT + "`timeout 5 /usr/bin/head -3 x`"))

    def test_in_python(self) -> None:
        _ = self.told(T("python3 -c \"import subprocess; subprocess.run(['cargo', 'test']); subprocess.run(['head', '-3', "
                        "'x'])\" > \"$LOG\" 2>&1"), P(f"`subprocess.run(['cargo', 'test'])`: {STARTED}"),
                      P(CUT + "`subprocess.run(['head', '-3', 'x'])`"))

    def test_only_in_a_file_name(self) -> None:  # A21
        self.passes(T("cargo test > @S@/head.log 2>&1"))

    def test_without_a_heavy_run(self) -> None:  # A20
        self.passes(T("git log --oneline | head -5"))


class Reach(Fixture):
    """The rule judges PreToolUse of a Bash or Monitor call alone, and reads each command's own declaration."""

    def test_another_event(self) -> None:
        self.assertEqual(judged(self.at, T("cargo test"), event="PostToolUse"), PASSED)

    def test_another_tool(self) -> None:
        self.assertEqual(judged(self.at, T("cargo test"), ToolName("Read")), PASSED)

    def test_the_commands_own_declaration(self) -> None:
        self.passes(T("(cd @OTHER@ && unit_tests)"))

    def test_the_sessions_declaration_where_the_directory_is_unknown(self) -> None:
        _ = self.told(T('cd "$x" && unit_tests'),
                      P("`unit_tests`: its output ends in the tool result, its errors end in the tool result"))


class Unread(Fixture):
    """A script aletheia's example declares unread, its text running a path it computes, is judged as the heavy command
    it is, never refused as a call the kit cannot read."""

    def test_a_declared_script_is_judged_heavy(self) -> None:
        self.check([(C("unread-logged"), T('LOG=@S@/x.log; bash probes/run_all.sh > "$LOG" 2>&1'), BASH, 0),
                    (C("unread-bare"), T("bash probes/run_all.sh"), BASH, 2)])


if __name__ == "__main__":
    unittest.main()
