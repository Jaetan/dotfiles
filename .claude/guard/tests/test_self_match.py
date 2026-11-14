"""The self-match rule, through the dispatcher with every other rule switched off: a Bash or Monitor call whose pgrep,
pkill or pidwait matches whole command lines without -A, or keeps -A where a forked copy of its shell is no ancestor,
or whose ps a later pipeline element filters with grep, rg or awk, is refused naming the command; the rest pass. Each
clause of the rule's docstring is a test of its own; the cases of the rule it replaced whose verdict it keeps are
kept under their ids."""

import json
import os
import pwd
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher.core import Answer, EventBytes, ExitStatus, FolderPath, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_declarations import NONE_DECLARED

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
CaseId = NewType("CaseId", str)
type Env = dict[VarName, ShellText]
BASH = ShellText("Bash")
MONITOR = ShellText("Monitor")
RULE = Prose("this call looks a process up in a way that can find its own shell")
SLOW = "x{1,1000}{1,1000}yN"
USER = pwd.getpwuid(os.getuid()).pw_name
PS_USER = USER if len(USER) <= 8 else f"{USER[:7]}+"
UID = os.getuid()


class Places(NamedTuple):
    """A scratch folder holding the project root, the home folder and the switch folder."""

    base: Path
    root: Path
    home: Path
    off: Path


class Case(NamedTuple):
    """A call, the tool it goes to, and the status the dispatcher answers it with."""

    tool: ShellText
    call: ShellText
    status: ExitStatus


def places() -> Places:
    """Fresh folders for one case class, every rule but self-match switched off, so a case judges this rule alone."""
    base = Path(tempfile.mkdtemp()).resolve()
    found = Places(base, base / "project", base / "home", base / "off")
    for folder in (found.root, found.home / ".config" / "claude-guard", found.off):
        folder.mkdir(parents=True)
    _ = (found.home / ".config" / "claude-guard" / "machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
    for module in (KIT / "rules").glob("*.py"):
        if not module.stem.startswith("_") and module.stem != "self_match":
            (found.off / module.stem.replace("_", "-")).touch()
    _ = (found.root / "watch.sh").write_text("while pgrep -f cargo > /dev/null; do sleep 5; done\n", encoding="utf-8")
    return found


def judged(at: Places, call: ShellText, tool: ShellText = BASH, extra: Env | None = None) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call`` run from the root, ``extra`` added to the hook's
    environment."""
    env: Env = {VarName("HOME"): ShellText(str(at.home)), VarName("CLAUDE_PROJECT_DIR"): ShellText(str(at.root))}
    env.update(extra or {})
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)),
                        ReadSettings(READER, Seconds(5.0), env), home=at.home)
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(at.root), "tool_input": {"command": call}}
    return answer(EventBytes(json.dumps(fields).encode()), settings)


def bash(call: ShellText, status: ExitStatus) -> Case:
    """A Bash case."""
    return Case(BASH, call, status)


def monitor(call: ShellText, status: ExitStatus) -> Case:
    """A Monitor case."""
    return Case(MONITOR, call, status)


class Judged(unittest.TestCase):
    """Each case answered with its status, a refusal of a readable call this rule's."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def check(self, cases: dict[CaseId, Case], extra: Env | None = None) -> None:
        """Every case answered with its status; a refusal of a readable call is this rule's."""
        for case, (tool, call, status) in cases.items():
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, tool, extra)
                self.assertEqual(got.status, status, got.stderr)
                if status == 2 and not case.startswith("U"):
                    self.assertIn(RULE, got.stderr)

    def problems(self, call: ShellText) -> Prose:
        """The stderr of the refusal of a Bash ``call``."""
        got = judged(self.at, call)
        self.assertEqual(got.status, 2, got.stderr)
        return got.stderr

    def test_a_lookup_by_its_base_name_any_version(self) -> None:
        self.check({
            CaseId("B01"): bash(ShellText("pgrep -f cargo"), 2),
            CaseId("B02"): bash(ShellText("pkill -f cargo"), 2),
            CaseId("B03"): bash(ShellText("pidwait -f cargo"), 2),
            CaseId("B04"): bash(ShellText("/usr/bin/pgrep -f cargo"), 2),
            CaseId("B05"): bash(ShellText("pgrep-4.0.4 -f cargo"), 2),
            CaseId("B06"): bash(ShellText("pkill4 -f cargo"), 2),
            CaseId("B07"): monitor(ShellText("while pgrep -f cargo >/dev/null; do sleep 5; done"), 2),
            CaseId("B08"): bash(ShellText("xpgrep -f cargo"), 0),
            CaseId("B09"): bash(ShellText("pgrepx -f cargo"), 0),
            CaseId("B10"): bash(ShellText("pgrep-x -f cargo"), 0),
            CaseId("B11"): bash(ShellText("\"$TOOL\" -f cargo"), 0),
        })

    def test_the_commands_the_model_holds(self) -> None:
        self.check({
            CaseId("H01"): bash(ShellText("timeout 5 pgrep -f cargo"), 2),
            CaseId("H02"): bash(ShellText("bash -c 'pgrep -f cargo'"), 2),
            CaseId("H03"): bash(ShellText(f"bash {self.at.root}/watch.sh"), 2),
            CaseId("H04"): bash(ShellText("python3 -c 'import subprocess; subprocess.run([\"pgrep\", \"-f\", \"cargo\"])'"),
                                2),
            CaseId("H05"): bash(ShellText("echo 'pgrep -f cargo'"), 0),
        })

    def test_whole_lines_matched_before_the_end_of_options(self) -> None:
        self.check({
            CaseId("F01"): bash(ShellText("pgrep --full cargo"), 2),
            CaseId("F02"): bash(ShellText("pgrep -af cargo"), 2),
            CaseId("F03"): bash(ShellText("pgrep -fa cargo"), 2),
            CaseId("F04"): bash(ShellText("pgrep cargo -f"), 2),
            CaseId("F05"): bash(ShellText("pgrep -x \"$NAME\""), 2),
            CaseId("F06"): bash(ShellText("pgrep $OPTS cargo"), 2),
            CaseId("F07"): bash(ShellText("pgrep cargo"), 0),
            CaseId("F08"): bash(ShellText("pgrep -x cargo"), 0),
            CaseId("F09"): bash(ShellText("pgrep -a cargo"), 0),
            CaseId("F10"): bash(ShellText("pgrep -x -- -f"), 0),
            CaseId("F11"): bash(ShellText("pgrep -x -- \"$NAME\""), 0),
            CaseId("F12"): bash(ShellText("pgrep --fuller cargo"), 0),
            CaseId("F13"): bash(ShellText("pgrep --x-f cargo"), 0),
            CaseId("F14"): bash(ShellText("pgrep -u fred cargo"), 0),
        })

    def test_ancestors_left_out_by_a(self) -> None:
        self.check({
            CaseId("A01"): bash(ShellText("pgrep -A -f cargo"), 0),
            CaseId("A02"): bash(ShellText("pgrep --ignore-ancestors -f cargo"), 0),
            CaseId("A03"): bash(ShellText("pgrep -Af cargo"), 0),
            CaseId("A04"): bash(ShellText("pkill -fA cargo"), 0),
            CaseId("A05"): bash(ShellText("pgrep -A \"$OPTS\" cargo"), 0),
            CaseId("A06"): bash(ShellText("pgrep -f -- -A"), 2),
            CaseId("A07"): bash(ShellText("pgrep --A -f cargo"), 2),
            CaseId("A08"): bash(ShellText("pgrep -a -f cargo"), 2),
        })

    def test_a_under_a_pipeline_or_a_background_job(self) -> None:
        self.check({
            CaseId("X01"): bash(ShellText("pgrep -A -f cargo | wc -l"), 2),
            CaseId("X02"): bash(ShellText("echo x | pgrep -A -f cargo"), 2),
            CaseId("X03"): bash(ShellText("sleep 5 & pgrep -A -f cargo"), 2),
            CaseId("X04"): bash(ShellText("pgrep -A -f cargo &"), 2),
            CaseId("X05"): bash(ShellText("coproc sleep 5; pgrep -A -f cargo"), 2),
            CaseId("X06"): bash(ShellText("pgrep -A -f cargo; { sleep 1; echo x; } &"), 2),
            CaseId("X07"): bash(ShellText("(sleep 1); pgrep -A -f cargo"), 0),
            CaseId("X08"): bash(ShellText("n=$(pgrep -A -f cargo)"), 0),
            CaseId("X09"): bash(ShellText("bash -c 'pgrep -A -f cargo'"), 0),
        })

    def test_a_ps_chain_filtered_after_ps(self) -> None:
        self.check({
            CaseId("P01"): bash(ShellText("ps aux | grep cargo"), 2),
            CaseId("P02"): bash(ShellText("ps aux | egrep cargo"), 2),
            CaseId("P03"): bash(ShellText("ps aux | fgrep cargo"), 2),
            CaseId("P04"): bash(ShellText("ps aux | rg cargo"), 2),
            CaseId("P05"): bash(ShellText("ps aux | awk '/cargo/'"), 2),
            CaseId("P06"): bash(ShellText("ps aux | gawk '/cargo/'"), 2),
            CaseId("P07"): bash(ShellText("ps aux | mawk '/cargo/'"), 2),
            CaseId("P08"): bash(ShellText("/usr/bin/ps aux | /usr/bin/grep '[c]argo'"), 2),
            CaseId("P09"): bash(ShellText("ps aux | head -n 50 | grep -v cargo"), 2),
            CaseId("P10"): bash(ShellText("ps aux | { grep cargo; }"), 2),
            CaseId("P11"): monitor(ShellText("while ps -eo args | grep -q cargo; do sleep 5; done"), 2),
            CaseId("P12"): bash(ShellText("ps aux"), 0),
            CaseId("P13"): bash(ShellText("ps aux | head -n 5"), 0),
            CaseId("P14"): bash(ShellText("grep cargo notes.txt | ps -p 1"), 0),
            CaseId("P15"): bash(ShellText("ps aux | head; echo cargo | grep cargo"), 0),
            CaseId("P16"): bash(ShellText("pps aux | grep cargo"), 0),
            CaseId("P17"): bash(ShellText("ps aux | xgrep cargo"), 0),
        })

    def test_the_line_for_a_lookup_without_a(self) -> None:
        self.assertIn("  * `pgrep -f cargo test`: it matches whole command lines (-f, or a word this call does not "
                      "make known) without -A\n", self.problems(ShellText("pgrep -f 'cargo test'")))

    def test_the_line_for_a_lookup_a_does_not_save(self) -> None:
        self.assertIn("  * `pgrep -A -f cargo`: under -A it is a pipeline element or this call starts a background "
                      "job, whose forked shell -A keeps", self.problems(ShellText("pgrep -A -f cargo | wc -l")))

    def test_the_line_for_a_ps_chain(self) -> None:
        self.assertIn("  * `ps aux`: a grep, rg or awk after it in its pipeline reads the line ps prints for this "
                      "call's own shell", self.problems(ShellText("ps aux | grep -q cargo")))

    def test_every_command_refused_is_named(self) -> None:
        stderr = self.problems(ShellText("pgrep -f cargo; pkill -f rustc; ps -ef | grep x"))
        for spelled in ("`pgrep -f cargo`", "`pkill -f rustc`", "`ps -ef`"):
            self.assertIn(spelled, stderr)

    def test_the_forms_that_pass(self) -> None:
        stderr = self.problems(ShellText("pgrep -f cargo"))
        for form in ("`pgrep -A -f PATTERN`", "outside a pipeline in a call that starts no", "background job;",
                     "`pgrep -x NAME`", "`$!`", "run_in_background", "`kill -0 PID`",
                     "ps with no filter after it in its pipeline", "or ps piped into grep, rg or awk,",
                     "as one shell whose command line holds the whole text", "ended reads as running",
                     "waits on itself and pkill kills the call"):
            self.assertIn(form, stderr)
        self.assertNotIn("[c]", stderr)

    def test_kept_lookups(self) -> None:
        """Today's pgrep, pkill and pidwait cases whose verdict the blunt rule keeps."""
        home = str(self.at.home)
        self.check({
            CaseId("N73"): bash(ShellText("pgrep $OPTS cargo"), 2),
            CaseId("N75"): bash(ShellText("pgrep cargo $OPTS"), 2),
            CaseId("N77"): bash(ShellText("pgrep $A $B"), 2),
            CaseId("N78"): bash(ShellText("pgrep -f $X '[c]argo'"), 2),
            CaseId("N79"): bash(ShellText("pgrep -- $X cargo"), 0),
            CaseId("N45"): bash(ShellText("pgrep -A -f cargo"), 0),
            CaseId("N46"): bash(ShellText("pgrep --ignore-ancestors -f cargo"), 0),
            CaseId("N47"): bash(ShellText("pgrep -A -f \"$P\""), 0),
            CaseId("N48"): bash(ShellText("timeout 5 pgrep -A -f car\"\"go"), 0),
            CaseId("N49"): bash(ShellText("{ sleep 9; } & pgrep -A -f cargo"), 2),
            CaseId("N51"): bash(ShellText("coproc { sleep 9; }; pgrep -A -f cargo"), 2),
            CaseId("N52"): bash(ShellText("{ sleep 9; } | pgrep -A -f cargo"), 2),
            CaseId("N53"): bash(ShellText("sleep 1 | cat; pgrep -A -f cargo"), 0),
            CaseId("N54"): bash(ShellText("bash -c '{ sleep 9; } & pgrep -A -f \"^bash -c [{]\"'"), 2),
            CaseId("N56"): bash(ShellText("( { sleep 9; } & pgrep -A -f cargo )"), 2),
            CaseId("N57"): bash(ShellText("echo \"$( { sleep 9; } & pgrep -A -f cargo )\""), 2),
            CaseId("N58"): bash(ShellText("{ { sleep 9; } & pgrep -A -f cargo; } &"), 2),
            CaseId("N60"): bash(ShellText("pkill -A -f cargo"), 0),
            CaseId("N82"): bash(ShellText("bash -c \"bash -c '{ sleep 9; } & pgrep -A -f \\\"^bash -c [{]\\\"'\""), 2),
            CaseId("N22"): bash(ShellText("pgrep-4.0.4 -f cargo"), 2),
            CaseId("N23"): bash(ShellText("/usr/bin/pkill -f cargo"), 2),
            CaseId("N24"): bash(ShellText("command pkill -f cargo"), 2),
            CaseId("N25"): bash(ShellText("timeout 5 pgrep -f cargo"), 2),
            CaseId("N61"): bash(ShellText("pgrep -v -f '[c]argo'"), 2),
            CaseId("N62"): bash(ShellText("pgrep --inverse -f '[c]argo'"), 2),
            CaseId("N65"): bash(ShellText("timeout 5 pgrep -v -f '^/usr/bin/bash'"), 2),
            CaseId("N01"): bash(ShellText("pkill -int -f cargo"), 2),
            CaseId("N06"): bash(ShellText("pgrep cargo -f"), 2),
            CaseId("N08"): bash(ShellText("pgrep --p -f cargo"), 2),
            CaseId("N12"): bash(ShellText("pkill --signal=TERM -f cargo"), 2),
            CaseId("N17"): bash(ShellText("pgrep --ignore-case -f '[C]ARGO test'; echo cargo test"), 2),
            CaseId("N18"): bash(ShellText("pgrep -f -"), 2),
            CaseId("N19"): bash(ShellText("pkill -e -f cargo"), 2),
            CaseId("N20"): bash(ShellText("pgrep -f cargo '[x]yz'"), 2),
            CaseId("N42"): bash(ShellText("pkill --full cargo"), 2),
            CaseId("N43"): bash(ShellText("pidwait --full cargo"), 2),
            CaseId("N33"): bash(ShellText("pgrep -f '^/usr/bin/bash -c source '"), 2),
            CaseId("N34"): bash(ShellText(f"pgrep -f '[/]{home[1:]}/[.]claude/shell-snapshots/snapshot-bash-"
                                          "1794107867155-hgrrgr[.]sh 2>/dev/null'"), 2),
            CaseId("N35"): bash(ShellText("pgrep -f '[s]hopt -u extglob 2>/dev/null [|][|] true && [{] .builtin "
                                          "unalias -- .which.; .builtin unset -f -- .which.; [}] >/dev/null 2>&1'"), 2),
            CaseId("N36"): bash(ShellText("pgrep -f '[u]nalias -- .unsetenv.; .builtin unset -f -- .unsetenv.; [}] "
                                          ">/dev/null 2>&1 [|][|] true && eval .'"), 2),
            CaseId("N37"): bash(ShellText("pgrep -f '[p]wd -P >[|] /tmp/claude-4c5b-cwd$'"), 2),
            CaseId("N66"): bash(ShellText("timeout 5 pgrep -f car\"\"go"), 2),
            CaseId("N67"): bash(ShellText("timeout 5 nice pgrep -f '^nice pgrep'"), 2),
            CaseId("N68"): bash(ShellText("bash -c 'pgrep -f \"^bash -c pgrep\"'"), 2),
            CaseId("N69"): bash(ShellText("timeout 5 bash -c 'pgrep -f \"^timeout 5 bash\"'"), 2),
            CaseId("N70"): bash(ShellText("echo x; bash -c 'pgrep -f \"^bash -c pgrep\"'"), 2),
            CaseId("N71"): bash(ShellText("timeout \"$T\" pgrep -f '[c]argo'"), 2),
            CaseId("N81"): bash(ShellText("bash -c 'pgrep -f \"^bash -c pgrep\"'; echo x"), 2),
            CaseId("L03"): bash(ShellText("while true; do\n pgrep -f cargo || break\n sleep 10\ndone"), 2),
            CaseId("M01"): monitor(ShellText("while pgrep -af cargo >/dev/null; do sleep 5; done"), 2),
            CaseId("M02"): monitor(ShellText("while pgrep --full cargo >/dev/null; do sleep 5; done"), 2),
            CaseId("M03"): monitor(ShellText("while pgrep -u \"$USER\" -f cargo >/dev/null; do sleep 5; done"), 2),
            CaseId("M04"): monitor(ShellText("while pgrep -f '[c]argo test' >/dev/null; do sleep 5; done; "
                                             "echo 'cargo test finished'"), 2),
            CaseId("M05"): monitor(ShellText("pgrep -f '[x]yz'; pgrep -f cargo"), 2),
            CaseId("F4a"): monitor(ShellText("while (( $(pgrep -fc 'cargo test') > 0 )); do sleep 10; done"), 2),
            CaseId("F4b"): monitor(ShellText("pids=($(pgrep -f cargo)); while kill -0 \"${pids[@]}\" 2>/dev/null; "
                                             "do sleep 5; done"), 2),
            CaseId("OS01"): bash(ShellText("pgrep -f 'cargo test'"), 2),
            CaseId("OS02"): bash(ShellText("pgrep -af cargo"), 2),
            CaseId("OS03"): bash(ShellText("pkill -f 'cargo test'"), 2),
            CaseId("OS05"): bash(ShellText("pgrep -f \"$UNSET_PATTERN_ONESHOT\""), 2),
            CaseId("OS06"): bash(ShellText("bash -c \"pgrep -f cargo\""), 2),
            CaseId("OS08"): bash(ShellText("pgrep -x cargo"), 0),
            CaseId("OS09"): bash(ShellText("pgrep cargo"), 0),
            CaseId("OS13"): bash(ShellText("cat <<'EOF'\npgrep -f cargo\nEOF"), 0),
            CaseId("OS14"): bash(ShellText("echo 'pgrep -f cargo'"), 0),
            CaseId("L28"): bash(ShellText("pgrep -f 'cargo test'"), 2),
            CaseId("F10.3"): monitor(ShellText("pgrep -f foo"), 2),
            CaseId("L43"): monitor(ShellText("while pgrep -f \"$(cat /tmp/pat)\" >/dev/null; do sleep 5; done"), 2),
            CaseId("L54"): bash(ShellText("pidwait -f 'cargo test'; echo finished"), 2),
            CaseId("L56"): monitor(ShellText("pidwait -f 'cargo test'; echo finished"), 2),
            CaseId("K10"): monitor(ShellText("while pgrep -f 'x{99999999999}' >/dev/null; do sleep 5; done"), 2),
            CaseId("K11"): bash(ShellText("while pgrep -f 'x{99999999999}' >/dev/null; do sleep 5; done"), 2),
            CaseId("K12"): monitor(ShellText("while pgrep -f '" + "(" * 2000 + "x" + ")" * 2000
                                             + "' >/dev/null; do sleep 5; done"), 2),
            CaseId("K14"): monitor(ShellText("while pgrep -f '\\<cargo\\>' >/dev/null; do sleep 5; done"), 2),
            CaseId("K16"): monitor(ShellText("while pgrep -fx '.*cargo test.*' >/dev/null; do sleep 5; done"), 2),
            CaseId("K24"): monitor(ShellText("echo \"x'y\"; while pgrep -f \"x'\\\"'\\\"'y\" >/dev/null; "
                                             "do sleep 5; done"), 2),
            CaseId("RB3"): monitor(ShellText("while pgrep -f \"$X\"$'\\0' >/dev/null; do sleep 5; done"), 2),
            CaseId("RB8"): monitor(ShellText("P=cargo; while pgrep -f \"$P\" >/dev/null; do sleep 5; done"), 2),
            CaseId("RB9"): monitor(ShellText("while pgrep -f \"$UNSET_PATTERN_X\" >/dev/null; do sleep 5; done"), 2),
            CaseId("GR6"): monitor(ShellText("while pgrep -if '[c]argo test' >/dev/null; do sleep 5; done; "
                                             "echo Cargo test finished"), 2),
            CaseId("GR7"): monitor(ShellText("while pgrep -f -- '-x' >/dev/null; do sleep 5; done"), 2),
            CaseId("GR8"): monitor(ShellText("while pgrep -f '[u]nset -f' >/dev/null; do sleep 5; done"), 2),
            CaseId("N26"): bash(ShellText("pidwait -f \"[e]cho x' && pwd\" < /tmp/in; echo x"), 2),
            CaseId("N27"): bash(ShellText("pgrep -f \"[E]OF' && pwd\"; cat <<'EOF'\nx\nEOF"), 2),
            CaseId("N28"): bash(ShellText("pgrep -f \"[y]))' [<] /dev/null && pwd\"; echo $((x<<y))"), 2),
            CaseId("N29"): bash(ShellText("pgrep -f \"[2] ))' [<] /dev/null && pwd\"; (( z = 1 << 2 ))"), 2),
            CaseId("N30"): bash(ShellText("echo a 2>nul; pgrep -f '[2]>/dev/null;'"), 2),
        })

    def test_kept_under_a_wrapper_shell(self) -> None:
        """Today's cases run with CLAUDE_CODE_SHELL and TMPDIR set, verdict kept."""
        self.check({
            CaseId("N39"): bash(ShellText("pgrep -f '^/opt/wrap/bash -c source '"), 2),
            CaseId("N40"): bash(ShellText("pgrep -f '[/]var/tt/claude-4c5b-cwd$'"), 2),
            CaseId("N162"): bash(ShellText("ps -eo args | grep -q '^/opt/wrap/bash -c source '"), 2),
        }, {VarName("CLAUDE_CODE_SHELL"): ShellText("/opt/wrap/bash"), VarName("TMPDIR"): ShellText("/var/tt/")})

    def test_kept_ps_chains(self) -> None:
        """Today's ps cases whose verdict the blunt rule keeps."""
        self.check({
            CaseId("N104"): bash(ShellText("ps -p 1234 -o args="), 0),
            CaseId("N105"): bash(ShellText("ps --pid 1234"), 0),
            CaseId("N106"): bash(ShellText("ps -C cargo"), 0),
            CaseId("N107"): bash(ShellText("ps 1234 5678"), 0),
            CaseId("N108"): bash(ShellText("ps aux | wc -l"), 0),
            CaseId("N109"): bash(ShellText("ps -p 1234 -o args= | grep -q cargo"), 2),
            CaseId("N131"): bash(ShellText("ps aux | grep -A1 '[c]argo'"), 2),
            CaseId("N132"): bash(ShellText("ps aux | grep -f /tmp/pats"), 2),
            CaseId("N133"): bash(ShellText("ps aux | grep --inv '[c]argo'"), 2),
            CaseId("N135"): bash(ShellText("ps aux | awk '$11 ~ /[c]argo/'"), 2),
            CaseId("N136"): bash(ShellText("ps aux | grep -Eq 'x{99999999999}'"), 2),
            CaseId("N137"): bash(ShellText(f"ps aux | grep -Eq '{SLOW}'"), 2),
            CaseId("N138"): bash(ShellText("ps aux | grep -q \"$P\""), 2),
            CaseId("N139"): bash(ShellText("ps aux | grep -Q '[c]argo'"), 2),
            CaseId("N140"): bash(ShellText("ps aux | rg -A1 '[c]argo'"), 2),
            CaseId("N141"): bash(ShellText("ps aux | awk -f /tmp/prog"), 2),
            CaseId("N142"): bash(ShellText("ps aux | grep -q '[c]argo' | grep -q \"$P\""), 2),
            CaseId("N173"): bash(ShellText("ps aux | awk '$0 ~ \"[c]argo\"'"), 2),
            CaseId("N174"): bash(ShellText("ps aux | awk 'index($0, \"[c]argo\")'"), 2),
            CaseId("N175"): bash(ShellText("ps aux | awk 'match($0, \"[c]argo\")'"), 2),
            CaseId("N176"): bash(ShellText("ps aux | awk '/[c]argo/ && /x/'"), 2),
            CaseId("N191"): bash(ShellText("ps aux | grep -q --regexp"), 2),
            CaseId("N193"): bash(ShellText("ps aux | grep -q -e"), 2),
            CaseId("N194"): bash(ShellText("ps aux | grep -q"), 2),
            CaseId("N202"): bash(ShellText("ps aux | grep -v \"$X\" grep"), 2),
            CaseId("N110"): bash(ShellText("ps aux | grep -qi '[C]ARGO'; echo cargo"), 2),
            CaseId("N113"): bash(ShellText("ps aux | grep -q '[c]arg'; echo cargo"), 2),
            CaseId("N114"): bash(ShellText("ps -eo args | grep -qx 'grep -qx .*'"), 2),
            CaseId("N117"): bash(ShellText("ps aux | fgrep -q '[c]argo'"), 2),
            CaseId("N118"): bash(ShellText("ps aux | rg -qF '[c]argo'"), 2),
            CaseId("N120"): bash(ShellText("ps aux | rg -qi '[C]ARGO'; echo cargo"), 2),
            CaseId("N121"): bash(ShellText("ps aux | grep --regexp='[c]argo' --ignore-case -q; echo CARGO"), 2),
            CaseId("N126"): bash(ShellText("ps aux | grep -v '[c]argo' | grep -q bash"), 2),
            CaseId("N128"): bash(ShellText("ps aux | grep -Pq 'x\\d'; echo x1"), 2),
            CaseId("N130"): bash(ShellText("ps aux | grep -q cargo -"), 2),
            CaseId("N163"): bash(ShellText("ps aux | egrep -q 'xq|cargo'"), 2),
            CaseId("N164"): bash(ShellText("ps aux | gawk '/cargo/'"), 2),
            CaseId("N165"): bash(ShellText("ps aux | mawk '/cargo/'"), 2),
            CaseId("N190"): bash(ShellText("ps aux | grep --regexp cargo -q"), 2),
            CaseId("N195"): bash(ShellText("ps aux | awk -F , '/cargo/'"), 2),
            CaseId("N197"): bash(ShellText("ps aux | awk -- '/cargo/'"), 2),
            CaseId("N199"): bash(ShellText("ps aux | awk '!/[c]argo/' | grep -q bash"), 2),
            CaseId("N200"): bash(ShellText("ps aux | /usr/bin/grep -q cargo"), 2),
            CaseId("N84"): bash(ShellText("ps -eo args | grep -q '^grep'"), 2),
            CaseId("N88"): bash(ShellText("ps -e | grep -q bash"), 2),
            CaseId("N89"): bash(ShellText("ps -eo pid,args | grep -q '^[0-9]* grep'"), 2),
            CaseId("N90"): bash(ShellText("ps -eo comm | grep -qx grep"), 2),
            CaseId("N92"): bash(ShellText("ps auxe | grep -q '[c]argo'"), 2),
            CaseId("N93"): bash(ShellText("ps $OPTS | grep -q '^grep'"), 2),
            CaseId("N94"): bash(ShellText("ps -eO user | grep -q cargo"), 2),
            CaseId("N96"): bash(ShellText("ps -elf | grep -q cargo"), 2),
            CaseId("N97"): bash(ShellText("ps --format args | grep -q '^grep'"), 2),
            CaseId("N100"): bash(ShellText("ps axo pid,command | grep -q '^[0-9]* grep'"), 2),
            CaseId("N101"): bash(ShellText("ps x | grep -q cargo"), 2),
            CaseId("N102"): bash(ShellText("ps -o args=CMD | grep -q '^grep'"), 2),
            CaseId("N177"): bash(ShellText(f"ps aux | grep -q '^{PS_USER} '"), 2),
            CaseId("N179"): bash(ShellText("ps -eo pid,cmd | grep -q '^[0-9]* grep'"), 2),
            CaseId("N180"): bash(ShellText(f"ps aux | grep -qx '{PS_USER} 1234 0 0 0 0 ? S 09:44 00:00:00 ps aux'"), 2),
            CaseId("N181"): bash(ShellText(f"ps -l | grep -qx '0 S {UID} 1234 0 0 0 0 0 0 - ? 00:00:00 ps'"), 2),
            CaseId("N182"): bash(ShellText(f"ps -f | grep -qx '{PS_USER} 1234 0 0 09:44 ? 00:00:00 ps -f'"), 2),
            CaseId("N204"): bash(ShellText("ps -e -f | grep -q cargo"), 2),
            CaseId("N207"): bash(ShellText("ps 1234 | grep -q cargo"), 2),
            CaseId("N208"): bash(ShellText("ps -f | grep -q cargo"), 2),
            CaseId("N209"): bash(ShellText("ps -u root -o args | grep -q '^grep'"), 2),
            CaseId("N210"): bash(ShellText("ps -eoargs | grep -q '^grep'"), 2),
            CaseId("N211"): bash(ShellText("ps -o cmd | grep -q cargo"), 2),
            CaseId("N212"): bash(ShellText(f"ps -O user | grep -qx '1234 {PS_USER} S ? 00:00:00 ps -O user'"), 2),
            CaseId("N213"): bash(ShellText("ps ocmd | grep -q cargo"), 2),
            CaseId("N215"): bash(ShellText(f"ps -F | grep -qx '{PS_USER} 1234 0 0 0 0 0 09:44 ? 00:00:00 ps -F'"), 2),
            CaseId("N216"): bash(ShellText(f"ps -lf | grep -qx '0 S {PS_USER} 1234 0 0 0 0 0 0 - 09:44 ? 00:00:00 ps"
                                           f" -lf'"), 2),
            CaseId("N217"): bash(ShellText("ps -j | grep -qx '1234 0 0 ? 00:00:00 ps'"), 2),
            CaseId("N219"): bash(ShellText("ps -eo comm,args | grep -qx bash"), 2),
            CaseId("N222"): bash(ShellText(f"ps --bogus | grep -q '^{PS_USER} '"), 2),
            CaseId("N227"): bash(ShellText("ps -eo ucomm | grep -qx bash"), 2),
            CaseId("N229"): bash(ShellText("ps --sort cmd -eo args | grep -q cargo"), 2),
            CaseId("N148"): bash(ShellText("ps aux | { grep -q '[c]argo'; true; }"), 2),
            CaseId("N149"): bash(ShellText("echo cargo | grep -q cargo"), 0),
            CaseId("N150"): bash(ShellText("ps aux > /tmp/ps.txt; grep -q cargo /tmp/ps.txt"), 0),
            CaseId("N151"): bash(ShellText("timeout 5 ps -eo args | grep -q '^timeout 5 ps'"), 2),
            CaseId("N152"): bash(ShellText("ps -eo args | grep -q '^ps -eo'"), 2),
            CaseId("N153"): bash(ShellText("ps -eo args | timeout 5 grep -q '^timeout 5 grep'"), 2),
            CaseId("N154"): bash(ShellText("bash -c \"ps -eo args | grep -q '^bash -c ps'\""), 2),
            CaseId("N155"): bash(ShellText("ps -eo args | grep -q '^/usr/bin/bash -c source '"), 2),
            CaseId("N157"): bash(ShellText("ps -eo args | grep '^cat$' | cat"), 2),
            CaseId("N158"): bash(ShellText("echo a\necho b; ps -eo args | grep -q 'a echo [b]'"), 2),
            CaseId("N159"): bash(ShellText("echo 'a\tb'; ps -eo args | grep -q 'a[?]b'"), 2),
            CaseId("N160"): bash(ShellText("ps -eo args | grep -q \"$(echo '^zz')\""), 2),
            CaseId("N161"): bash(ShellText("ps -eo args | grep -q '[c]argo' | head -n \"$N\""), 2),
            CaseId("OS04"): bash(ShellText("ps aux | grep 'cargo test'"), 2),
            CaseId("L57"): monitor(ShellText("while ps aux | rg -q 'cargo test'; do sleep 5; done"), 2),
            CaseId("L58"): monitor(ShellText("ps aux | awk '/cargo test/'"), 2),
            CaseId("L67"): monitor(ShellText("ps aux | grep -v \"$X\" | grep -q cargo"), 2),
            CaseId("L68"): monitor(ShellText("ps aux | rg -q \"$(cat /tmp/p)\" && echo up"), 2),
            CaseId("G17"): monitor(ShellText("while ps aux | grep -q 'cargo test'; do sleep 5; done"), 2),
            CaseId("G17c"): monitor(ShellText("while ps aux | grep -qF 'cargo test'; do sleep 5; done"), 2),
            CaseId("GR3"): monitor(ShellText("while ps aux | grep -q 'x+y'; do sleep 5; done"), 2),
            CaseId("GR4"): monitor(ShellText("while ps aux | grep -q 'run(x)'; do sleep 5; done"), 2),
            CaseId("GR10"): monitor(ShellText("while ps aux | grep -q 'car''go test'; do sleep 5; done"), 2),
            CaseId("MN7"): monitor(ShellText("ps aux | grep -q -e 'cargo test' -e '[r]ustc'"), 2),
        })

    def test_kept_unreadable_calls(self) -> None:
        """Today's unreadable calls, refused by the dispatcher."""
        self.check({
            CaseId("U03"): monitor(ShellText("pgrep -f cargo; echo \"open"), 2),
            CaseId("U04"): monitor(ShellText("pgrep -f '[c]argo'; echo \"open"), 2),
            CaseId("UG18a"): monitor(ShellText("pgrep -af cargo; echo \"open"), 2),
            CaseId("UG13a"): bash(ShellText("fish -c 'while pgrep -f cargo; sleep 5; end'"), 2),
            CaseId("URB1"): monitor(ShellText("while pgrep -f cargo >/dev/null; do sleep 5; done; echo \ud800"), 2),
            CaseId("URB2"): bash(ShellText("while pgrep -f cargo >/dev/null; do sleep 5; done; echo \ud800"), 2),
        })


if __name__ == "__main__":
    unittest.main()
