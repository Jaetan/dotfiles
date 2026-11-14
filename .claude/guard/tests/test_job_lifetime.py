"""The job-lifetime rule, through the dispatcher with every other rule switched off: a background job no later plain
`wait` of its shell reaps, and each detaching form the rule names, are refused, each line naming the form, why and the
form that passes; the rest pass. A class per clause of the module docstring, its exception included; a case named
after a measured form keeps the name it was measured under."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NamedTuple, NewType

from dispatcher.core import Answer, EventBytes, FolderPath, Settings, answer
from dispatcher.model import Command, ProcessId, ReadSettings, build
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose, RuleName
from dispatcher.words import VarName
from rules.job_lifetime import starts_shell, wraps
from tests.test_declarations import NONE_DECLARED, TomlSource, declaring

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = RuleName("job_lifetime")
SLICE_RULE = RuleName("unit_slice")
MARK = Prose("guard job-lifetime:")
SLICE_MARK = Prose("guard unit-slice:")
CaseId = NewType("CaseId", str)
Status = Literal[0, 2]
REFUSED: Status = 2
Case = tuple[CaseId, ShellText, Status]
C = CaseId
T = ShellText
BASH = T("Bash")
MONITOR = T("Monitor")
DIES = Prose("which the call's end kills before anything reads it")
ESCAPES = Prose("which escapes the call's scope: it runs on after the call, unwatched")
REAP_FORM = Prose("End the shell that started it with `wait`, or run a long run with run_in_background.")
SLICED = declaring(slice=TomlSource('[slice]\nname = "claude.slice"\n'))


class Places(NamedTuple):
    """The fixture's folders: the home holding the machine file, the switch folder, the folder calls run in."""

    base: Path
    home: Path
    off: Path
    work: Path


def places(on: frozenset[RuleName] = frozenset({RULE}), machine: TomlSource = NONE_DECLARED) -> Places:
    """A fresh fixture, every rule but those ``on`` switched off, and the cases' scripts in the work folder."""
    base = Path(tempfile.mkdtemp()).resolve()
    at = Places(base, base / "home", base / "off", base / "work")
    for folder in (at.home / ".config" / "claude-guard", at.off, at.work):
        folder.mkdir(parents=True)
    _ = (at.home / ".config" / "claude-guard" / "machine.toml").write_text(machine, encoding="utf-8")
    _ = (at.work / "bg_job.sh").write_text("sleep 1\necho done\n", encoding="utf-8")
    _ = (at.work / "shjob.sh").write_text("sleep 9 & wait -n\n", encoding="utf-8")
    for entry in os.listdir(KIT / "rules"):
        stem, suffix = os.path.splitext(entry)
        if suffix == ".py" and not stem.startswith("_") and stem not in on:
            (at.off / stem.replace("_", "-")).touch()
    return at


def judged(at: Places, call: ShellText, tool: ShellText = BASH) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call`` run from the work folder."""
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)),
                        ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(at.home))}), home=at.home)
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(at.work),
              "tool_input": {"command": call}}
    return answer(EventBytes(json.dumps(fields).encode()), settings)


class Fixture(unittest.TestCase):
    """A class of cases sharing one fixture."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def check(self, cases: list[Case], tool: ShellText = BASH) -> None:
        """Every case answered with its status, a refusal this rule's alone."""
        for case, call, status in cases:
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, tool)
                self.assertEqual(got.status, status, got.stderr)
                if status == REFUSED:
                    self.assertIn(MARK, got.stderr)
                    self.assertNotIn("guard kit:", got.stderr)
                else:
                    self.assertEqual(got.stderr, "")

    def says(self, call: ShellText, *lines: Prose) -> Prose:
        """``call`` refused, its refusal holding each of ``lines``; the refusal's text."""
        got = judged(self.at, call)
        self.assertEqual(got.status, 2, got.stderr)
        for expected in lines:
            self.assertIn(expected, got.stderr)
        return got.stderr


class Jobs(Fixture):
    """A background job passes only where a later command of the very shell that started it, in the same function
    body, is `wait` alone with no wrapper."""

    def test_refused(self) -> None:
        w = self.at.work
        self.check([
            (C("D-JOB"), T("sleep 30 &"), 2),
            (C("D-NOHUP"), T("nohup sleep 30 &"), 2),
            (C("F08.2"), T("nohup sleep 9 > /tmp/x 2>&1 &"), 2),
            (C("L10"), T("coproc tail -f /tmp/x"), 2),
            (C("L07"), T("bash -c 'sleep 7771 &'"), 2),
            (C("L08"), T("eval 'sleep 7778 &'"), 2),
            (C("L17"), T("git commit -q -m \"$(cat <<'EOF'\nfix: a & b\nEOF\n)\" && tail -f /tmp/x.log | "
                         "grep --line-buffered FAIL &"), 2),
            (C("L18"), T(f"cat > {w}/o.sh <<'SH'\nsleep 99 &\nSH\nbash {w}/o.sh"), 2),
            (C("D-SUBSHELL"), T("( sleep 30 & )"), 2),
            (C("D-CARRIER"), T("bash -c 'sleep 30 &'"), 2),
            (C("D-SUBSTITUTION"), T("x=$(sleep 30 &)"), 2),
            (C("D-PYTHON"), T("python3 -c \"import os; os.system('sleep 30 &')\""), 2),
            (C("D-SETSID-JOB"), T("setsid sleep 30 &"), 2),
            (C("Z51"), T(f"sh {w}/shjob.sh"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("F02"), T("sleep 1 & sleep 2 & wait"), 0),
            (C("L27.2"), T("sleep 1 & sleep 2 & wait"), 0),
            (C("F05.2"), T("echo a && echo b"), 0),
            (C("F06"), T("cat > /tmp/f <<'EOF'\nx &\nEOF"), 0),
            (C("F12.2"), T("echo 'a & b'"), 0),
            (C("L14"), T("tail -f x | grep y &\nsleep 1\nwait"), 0),
            (C("F2a"), T("(cd go && go vet ./aletheia/) & (cd rust && cargo fmt --check) & wait"), 0),
            (C("F2b"), T("( sleep 1 ) & wait"), 0),
            (C("L24"), T("coproc sleep 1; wait"), 0),
            (C("L25"), T("coproc W { sleep 5; }; wait"), 0),
            (C("L26"), T("bash -c 'sleep 1' & wait"), 0),
            (C("L29"), T("git fetch -q & eval wait"), 0),
            (C("P-NOHUP"), T("nohup sleep 30 > /dev/null 2>&1 & wait"), 0),
            (C("P-SETSID-JOB"), T("setsid sleep 30 & wait"), 0),
            (C("PL1"), T("bash -c 'sleep 0.3 & sleep 0.4 & wait' | cat"), 0),
            (C("SH-BARE"), T("sh -c 'sleep 9 & wait'"), 0),
            (C("N-ONE-THEN-ALL"), T("sleep 30 & a=$!; sleep 31 & wait -n; wait"), 0),
        ])

    def test_later(self) -> None:
        self.check([
            (C("F13"), T("wait; sleep 1 &"), 2),
            (C("L08.2"), T("sleep 1 & wait\nsleep 999 &"), 2),
        ])

    def test_very_shell(self) -> None:
        self.check([
            (C("L09"), T("(sleep 7775 &); wait"), 2),
            (C("O05"), T("sleep 7799 & wait | cat"), 2),
            (C("Z48"), T("sleep 2 & echo x | wait"), 2),
            (C("Z49"), T("bash -c 'sleep 2 & echo x | wait'"), 2),
            (C("L09.2"), T("{ sleep 999 & wait; } &"), 2),
            (C("L09b"), T("( sleep 999 & wait ) &"), 2),
            (C("L21"), T("sleep 999 & wait &"), 2),
            (C("L20c"), T("sleep 999 & (wait)"), 2),
            (C("FN-SUBSHELL-BODY"), T("w() ( wait ); sleep 30 & w"), 2),
            (C("FN-CALL-ELSEWHERE"), T("w() { wait; }; sleep 30 & ( w )"), 2),
            (C("Z46"), T("( git fetch -q & wait ) & wait"), 0),
            (C("B-SUBSHELL-WAIT"), T("( sleep 30 & wait )"), 0),
            (C("B-CARRIER-WAIT"), T("bash -c 'sleep 9 & wait'"), 0),
        ])

    def test_plain(self) -> None:
        self.check([
            (C("L06"), T("sleep 0.1 & sleep 7773 & wait -n"), 2),
            (C("L13.2"), T("sleep 9 & sleep 8 & wait -n"), 2),
            (C("L16.2"), T("sleep 9 & wait 123"), 2),
            (C("L17.2"), T("sleep 9 & sleep 8 & wait -p got -n"), 2),
            (C("L18.2"), T("sleep 9 & a=$!; sleep 8 & b=$!; wait -n $a $b"), 2),
            (C("L19"), T("sleep 9 & a=$!; sleep 8 & wait -p got $a"), 2),
            (C("L10b"), T("tail -f /tmp/x.log | grep --line-buffered FAIL &\nsleep 999 &\nwait $!"), 2),
            (C("L23"), T("tail -f /tmp/a | grep x & sleep 9 & wait %2"), 2),
            (C("L30"), T("sleep 99 &\nwait \"$pidd\""), 2),
            (C("Z38"), T("bash -c 'sleep 0.2 & sleep 999 & wait %1; wait %1'"), 2),
            (C("PL2"), T("bash -c 'sleep 1 & wait %²'"), 2),
            (C("SH-N"), T("sh -c 'sleep 9 & wait -n'"), 2),
            (C("D-WRAPPED-WAIT"), T("sleep 30 & timeout 5 wait"), 2),
            (C("B-WAIT-N"), T("sleep 9 & wait -n"), 2),
            (C("B-WAIT-PID"), T("sleep 9 & wait $!"), 2),
            (C("B-BUILTIN"), T("sleep 30 & builtin wait"), 2),
            (C("B-REDIRECTED"), T("sleep 9 & wait 2> /dev/null"), 0),
        ])

    def test_nothing_else_reaps(self) -> None:
        """No `$!`, job spec, kill, pkill or trap reaps or stops a job."""
        self.check([
            (C("F04"), T("sleep 9 & disown"), 2),
            (C("J05c"), T("python3 -m http.server 8765 & a=$!\npython3 -m http.server 8766 & b=$!\n"
                          "trap 'kill $a' EXIT\necho work"), 2),
            (C("L11.2"), T("tail -f /tmp/a | grep x &\nsleep 999 &\ntrap 'kill %1' EXIT\necho work"), 2),
            (C("L12b"), T("trap 'echo skill issue' EXIT\nsleep 999 &"), 2),
            (C("L32"), T("tail -f /tmp/a | grep x & p=$!; kill $p"), 2),
            (C("L41"), T("tail -f /tmp/a | grep x &\ntrap 'kill -s 0 $$' EXIT\necho work"), 2),
            (C("Z42"), T("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep x &\ntrap 'rm -f /tmp/t' EXIT"), 2),
            (C("Z43"), T("trap 'kill 0' EXIT\ntail -f /tmp/x.log | grep x &\ntrap - EXIT"), 2),
            (C("Z44"), T("sleep 998 & a=$!\nsleep 999 & b=$!\ntrap 'kill $a; wait $a' EXIT\necho work"), 2),
            (C("Z47"), T("sleep 999 & p=$!; kill -0 $p && echo alive"), 2),
            (C("J7f"), T("sleep 998 & sleep 999 &\necho work\nkill %%"), 2),
            (C("J7g"), T("sleep 998 & sleep 999 &\necho work\nkill %+"), 2),
            (C("J7h"), T("bash -c 'sleep 998 & sleep 999 & kill %2'"), 2),
            (C("J7i"), T("sleep 997 & sleep 998 & sleep 999 &\necho work\nkill %1 %2"), 2),
            (C("J7j"), T("sleep 999 & kill -0 0 && echo alive"), 2),
            (C("J7k"), T("sleep 999 & kill -s 0 0"), 2),
            (C("J7l"), T("sleep 999 & kill -CONT 0"), 2),
            (C("J7m"), T("sleep 999 & kill -0 -- -$$"), 2),
            (C("J7n"), T("sleep 999 &\necho work\npkill -0 -P $$"), 2),
            (C("K-SUBSHELL-LIST"), T("( sleep 30; : ) & kill $!"), 2),
            (C("K-SUBSHELL-SPEC"), T("( sleep 30; : ) & kill %1"), 2),
            (C("K-ANDOR"), T("cd /tmp && sleep 30 & kill $!"), 2),
            (C("K-CARRIER-LIST"), T("bash -c 'sleep 30; :' & kill $!"), 2),
            (C("K-PIPE-PID"), T("tail -f x | grep y & kill $!"), 2),
            (C("K-SCRIPT"), T(f"bash {self.at.work}/bg_job.sh & kill $!"), 2),
            (C("K-GROUP-CARRIER"), T("bash -c 'sleep 30 & kill -- -$$'"), 2),
            (C("K-PKILL-PATTERN"), T("sleep 30 & pkill -P $$ sleep"), 2),
            (C("K-PKILL-SUBSHELL"), T("( sleep 30 & pkill -P $$ )"), 2),
            (C("K-NESTED"), T("( sleep 30 & wait ) & kill $!"), 2),
            (C("N-OTHER-VAR"), T("sleep 30 & p=$!; q=1; wait $q"), 2),
            (C("N-OVERWRITTEN"), T("sleep 30 & p=$!; p=1; wait $p"), 2),
            (C("N-CLEARED"), T("sleep 30 & pids+=($!); pids=(); wait \"${pids[@]}\""), 2),
            (C("N-APPENDED"), T("sleep 30 & pids=($!); sleep 31 & wait \"${pids[@]}\""), 2),
            (C("N-NAME"), T("sleep 30 & wait %sleep"), 2),
            (C("N-PREVIOUS"), T("sleep 30 & sleep 31 & wait %-"), 2),
            (C("N-BEFORE"), T("p=$!; sleep 30 & wait $p"), 2),
            (C("N-SUBSHELL-VAR"), T("sleep 30 & (p=$!); wait $p"), 2),
            (C("N-LITERAL-INDEX"), T("pids=(); sleep 30 & pids+=($!); sleep 31 & pids+=($!); wait ${pids[0]}"), 2),
            (C("N-ONE-MANY"), T("sleep 30 & a=$!; sleep 31 & b=$!; wait -n $a $b; wait $b"), 2),
            (C("T-SIGEXIT"), T("trap 'kill 0' SIGEXIT\nsleep 30 &"), 2),
            (C("T-INT"), T("trap 'kill 0' INT\nsleep 30 &"), 2),
            (C("T-EARLY"), T("trap \"kill $p\" EXIT; sleep 30 & p=$!"), 2),
            (C("T-IGNORED"), T("trap 'kill 0' EXIT\ntrap '' EXIT\nsleep 30 &"), 2),
            (C("T-SUBSHELL"), T("( trap 'kill 0' EXIT ); sleep 30 &"), 2),
            (C("T-RESET-NUMBER"), T("trap 'kill 0' EXIT\ntrap 0 INT\nsleep 30 &"), 2),
            (C("T-RESET-NAME"), T("trap 'kill 0' EXIT\ntrap EXIT\nsleep 30 &"), 2),
            (C("T-FUNCTION-UNCALLED"), T("f() { trap 'kill 0' EXIT; }; sleep 30 &"), 2),
            (C("B-KILL"), T("sleep 30 & kill $!"), 2),
            (C("B-TRAP-KILL"), T("trap 'kill 0' EXIT\nsleep 30 &"), 2),
            (C("B-TRAP-WAIT"), T("trap wait EXIT\nsleep 30 &"), 2),
        ])

    def test_same_body(self) -> None:
        self.check([
            (C("O04"), T("sleep 7799 &\nfinish() { wait; }"), 2),
            (C("FN-WAIT-FIRST"), T("f() { sleep 30 & }; wait; f"), 2),
            (C("FN-UNCALLED-JOB"), T("f() { sleep 30 & }"), 2),
            (C("FN-WAIT-BEFORE-JOB"), T("f() { wait; sleep 30 & }"), 2),
            (C("L40"), T("f() { tail -f /tmp/a | grep x & }; trap f INT; f"), 2),
            (C("V23"), T("f() { tail -f /tmp/a | grep x & }; trap f EXIT; echo hi"), 2),
            (C("B-CALL-THEN-WAIT"), T("f() { sleep 30 & }; f; wait"), 2),
            (C("FN-OWN-BODY"), T("f() { sleep 30 & wait; }"), 0),
        ])


class Disown(Fixture):
    """disown is refused, whatever its options."""

    def test_refused(self) -> None:
        self.check([
            (C("D-DISOWN"), T("sleep 30 & disown"), 2),
            (C("D-DISOWN-A"), T("sleep 30 & disown -a; wait"), 2),
            (C("B-DISOWN-H"), T("sleep 30 & disown -h; wait"), 2),
        ])


class Setsid(Fixture):
    """A setsid given -f or --fork, alone or in a single-dash bundle, before its first word not led by `-`."""

    def test_refused(self) -> None:
        self.check([
            (C("L11"), T("setsid -f sleep 7774; echo started"), 2),
            (C("D-SETSID-F"), T("setsid -f sleep 30"), 2),
            (C("D-SETSID-FORK"), T("setsid --fork sleep 30"), 2),
            (C("B-SETSID-W"), T("setsid -w -f sleep 30"), 2),
            (C("B-SETSID-FW"), T("setsid -wf sleep 30"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("P-SETSID"), T("setsid sleep 30"), 0),
            (C("B-SETSID-LONG"), T("setsid --fo sleep 30"), 0),
            (C("B-SETSID-LATE"), T("setsid sleep -f 30"), 0),
        ])


class StartStopDaemon(Fixture):
    """A start-stop-daemon given -b or --background before `--`."""

    def test_refused(self) -> None:
        self.check([
            (C("L05.2"), T("start-stop-daemon -S -b -x /usr/bin/sleep -- 999"), 2),
            (C("D-SSD"), T("start-stop-daemon --start --background --exec /usr/bin/sleep -- 30"), 2),
            (C("B-SSD-STOP"), T("start-stop-daemon --stop -b -x /usr/bin/sleep"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("P-SSD-FG"), T("start-stop-daemon -S -x /usr/bin/sleep -- 30"), 0),
            (C("P-SSD-VALUE"), T("start-stop-daemon -Sxb -- 30"), 0),
            (C("B-SSD-AFTER"), T("start-stop-daemon -S -x /usr/bin/sleep -- -b"), 0),
        ])


class Tmux(Fixture):
    """A tmux whose words name new-session or new with -d, or a command that starts a window or pane."""

    def test_refused(self) -> None:
        self.check([
            (C("L12"), T("tmux new -d 'cargo test'"), 2),
            (C("L07b"), T("tmux new -d -s build 'sleep 999'"), 2),
            (C("E-TMUX-D"), T("tmux new-session -d sleep 30"), 2),
            (C("E-TMUX-SD"), T("tmux new -s build -d sleep 30"), 2),
            (C("E-TMUX-SOCKET"), T("tmux -L probe new -d"), 2),
            (C("E-TMUX-WINDOW"), T("tmux new-window 'sleep 30'"), 2),
            (C("E-TMUX-NEWW"), T("tmux neww"), 2),
            (C("B-TMUX-SPLIT-WINDOW"), T("tmux split-window -h"), 2),
            (C("E-TMUX-SPLITW"), T("tmux splitw"), 2),
            (C("E-TMUX-RESPAWN"), T("tmux respawn-pane -k"), 2),
            (C("B-TMUX-RESPAWNP"), T("tmux respawnp -k"), 2),
            (C("B-TMUX-RESPAWN-WINDOW"), T("tmux respawn-window -k"), 2),
            (C("E-TMUX-RESPAWNW"), T("tmux respawnw -k"), 2),
            (C("E-TMUX-CHAIN"), T("tmux new \\; split-window"), 2),
            (C("E-TMUX-SEMI"), T("tmux new 'sleep 1'\\; neww"), 2),
            (C("B-TMUX-SHELL-WORDS"), T("tmux -c 'echo hi' new -d"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("P-TMUX-ATTACHED"), T("tmux new-session sleep 1"), 0),
            (C("P-TMUX-PTY"), T("SHELL=/usr/bin/bash script -qec 'tmux new-session sleep 1' /dev/null"), 0),
            (C("P-TMUX-LS"), T("tmux ls"), 0),
            (C("P-TMUX-KILL"), T("tmux kill-server"), 0),
            (C("P-TMUX-SHELL"), T("tmux -c 'new -d'"), 0),
            (C("P-TMUX-AMBIGUOUS"), T("tmux new- -d"), 0),
            (C("B-TMUX-ATTACH-D"), T("tmux attach -d"), 0),
            (C("B-TMUX-PREFIX"), T("tmux split -h"), 0),
        ])


class Systemctl(Fixture):
    """A systemctl whose words name a verb that starts a unit, or enable or reenable with --now."""

    def test_refused(self) -> None:
        self.check([
            (C("N-START"), T("systemctl --user start probe.service"), 2),
            (C("E-RESTART"), T("systemctl --user restart probe.service"), 2),
            (C("E-TRY"), T("systemctl --user try-restart probe.service"), 2),
            (C("B-RELOAD-OR-RESTART"), T("systemctl --user reload-or-restart probe.service"), 2),
            (C("B-TRY-RELOAD-OR-RESTART"), T("systemctl --user try-reload-or-restart probe.service"), 2),
            (C("E-ISOLATE"), T("systemctl --user isolate probe.target"), 2),
            (C("E-ISOLATE-WAIT"), T("systemctl --user isolate --wait probe.target"), 2),
            (C("E-ENABLE-NOW"), T("systemctl --user enable --now probe.service"), 2),
            (C("B-REENABLE-NOW"), T("systemctl --user reenable --now probe.service"), 2),
            (C("E-AFTER"), T("systemctl start probe.service --user"), 2),
            (C("E-VALUED"), T("systemctl --user -p MainPID start probe.service"), 2),
            (C("E-PREFIX"), T("systemctl --us start probe.service"), 2),
            (C("B-START-WAIT"), T("systemctl --user start --wait probe.service"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("P-ENABLE"), T("systemctl --user enable probe.service"), 0),
            (C("B-REENABLE"), T("systemctl --user reenable probe.service"), 0),
            (C("B-STOP-NOW"), T("systemctl --user stop --now probe.service"), 0),
            (C("P-STOP"), T("systemctl --user stop probe.service"), 0),
            (C("P-STATUS"), T("systemctl --user status probe.service"), 0),
        ])


class SystemdRun(Fixture):
    """A systemd-run with none of the waiting options before its first word not led by `-`."""

    def test_refused(self) -> None:
        self.check([
            (C("E-RUN"), T("systemd-run --user sleep 30"), 2),
            (C("L03.2"), T("systemd-run --user sleep 999"), 2),
            (C("E-INHERIT"), T("systemd-run --user --slice-inherit sleep 30"), 2),
            (C("E-REMAIN"), T("systemd-run --user -r sleep 30"), 2),
            (C("E-INNER-WAIT"), T("systemd-run --user grep --wait x"), 2),
            (C("E-SCOPE-BG"), T("systemd-run --user --scope sleep 30 &"), 2),
            (C("E-WAIT-BG"), T("systemd-run --user --wait sleep 30 &"), 2),
            (C("B-BUNDLE"), T("systemd-run --user -qt sleep 1"), 2),
            (C("B-PATH"), T("/usr/bin/systemd-run --user sleep 30"), 2),
            (C("B-UNKNOWN-WORD"), T("systemd-run --user \"$(echo -q)\" --wait sleep 30"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("P-WAIT"), T("systemd-run --user --wait sleep 30"), 0),
            (C("N-WAIT"), T("systemd-run --user --slice=claude.slice --wait sleep 30"), 0),
            (C("P-PIPE"), T("systemd-run --user --pipe cat"), 0),
            (C("P-P"), T("systemd-run --user -P cat"), 0),
            (C("P-PTY"), T("systemd-run --user --pty sleep 1"), 0),
            (C("B-T"), T("systemd-run --user -t sleep 1"), 0),
            (C("P-PTY-LATE"), T("systemd-run --user --pty-late sleep 1"), 0),
            (C("P-SHELL"), T("systemd-run --user --shell"), 0),
            (C("B-S"), T("systemd-run --user -S"), 0),
            (C("P-SCOPE"), T("systemd-run --user --scope sleep 30"), 0),
            (C("P-SCOPE-REAPED"), T("systemd-run --user --scope sleep 30 & wait"), 0),
            (C("B-VALUE-UNSKIPPED"), T("systemd-run --user --description --wait sleep 30"), 0),
        ])


class Traps(Fixture):
    """The exception: a trap's text is judged as more of the call, its own jobs reaped within it alone."""

    def test_refused(self) -> None:
        self.check([
            (C("V23b"), T("trap 'sleep 9 &' EXIT; echo hi"), 2),
            (C("T-SIGNAL-JOB"), T("trap 'sleep 9 &' INT; echo hi"), 2),
            (C("T-UNIT"), T("trap 'systemd-run --user sleep 9' EXIT; echo hi"), 2),
            (C("TR-DASHES"), T("trap -- 'setsid -f sleep 9' EXIT"), 2),
            (C("TR-QUOTED"), T("trap \"sleep $(date +%s) &\" EXIT"), 2),
            (C("TR-NESTED"), T("trap 'trap \"sleep 9 &\" INT' EXIT"), 2),
            (C("TR-OWN-JOBS"), T("trap 'sleep 9 &' EXIT; wait"), 2),
            (C("TR-ESCAPED-JOB"), T("trap \"echo $(date) \\\"x\\\" & sleep 9\" EXIT"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("T-UNRELATED"), T("trap 'rm -f /tmp/x' EXIT; echo hi"), 0),
            (C("V23c"), T("f() { sleep 1 & wait; }; trap f EXIT; echo hi"), 0),
            (C("TR-REAPED"), T("trap 'sleep 9 & wait' EXIT"), 0),
            (C("TR-ONE"), T("trap 'sleep 9 &'"), 0),
            (C("TR-RESET"), T("trap - EXIT"), 0),
            (C("TR-LIST"), T("trap -p"), 0),
            (C("TR-ESCAPED"), T("trap \"echo $(date) \\\"&\\\" sleep 9\" EXIT"), 0),
        ])

    def test_unknown_text(self) -> None:
        got = judged(self.at, T("trap $cleanup EXIT; sleep 30 &"))
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: this call cannot be read: the text a trap runs is not made known by the call: "
                      "`$cleanup`", got.stderr)

    def test_quotes_inside(self) -> None:
        got = judged(self.at, T("trap \"echo \"$x\" hi\" EXIT; echo hi"))
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: this call cannot be read: the text a trap runs is not made known by the call: "
                      "`\"echo \"$x\" hi\"`", got.stderr)

    def test_gap_in_text(self) -> None:
        got = judged(self.at, T("trap 'eval \"$x\"' EXIT; echo hi"))
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: this call cannot be read: the text the trap `trap eval \"$x\" EXIT` runs: "
                      "the text eval runs is not known from the call",
                      got.stderr)

    def test_unparsed_text(self) -> None:
        got = judged(self.at, T("trap 'echo \"done' EXIT; sleep 30 & wait"))
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: this call cannot be read: the text the trap `trap echo \"done EXIT` runs:",
                      got.stderr)


class Unnamed(Fixture):
    """screen, daemonize, at and batch are not installed and the rule names none of them, so they pass."""

    def test_passed(self) -> None:
        self.check([
            (C("L02"), T("screen -dmS w sleep 999"), 0),
            (C("L04"), T("daemonize /usr/bin/sleep 999"), 0),
            (C("O06"), T("echo 'sleep 999' | at now"), 0),
            (C("L06.2"), T("echo 'sleep 9' | batch"), 0),
        ])


class Monitor(Fixture):
    """The rule judges a Monitor call as it judges a Bash call."""

    def test_refused(self) -> None:
        self.check([
            (C("R-MONITOR"), T("sleep 30 &"), 2),
            (C("F01.2"), T("log=/tmp/p.log\ntail -f -n +1 \"$log\" | grep -E --line-buffered \"^FAIL \" &\n"
                           "while kill -0 123 2> /dev/null; do sleep 15; done"), 2),
            (C("L05"), T("tail -f /tmp/x.log | grep --line-buffered FAIL &\nwait 12345"), 2),
            (C("O02"), T("tail -f /tmp/a.log | grep --line-buffered FAIL &\ntail -f /tmp/b.log | grep --line-buffered "
                         "FAIL &\npid=$!\ntrap 'kill $pid' EXIT\nwhile kill -0 123 2>/dev/null; do sleep 15; done"), 2),
        ], MONITOR)

    def test_passed(self) -> None:
        self.check([(C("F07.2"), T("tail -f log | grep --line-buffered X"), 0)], MONITOR)


class Refusal(Fixture):
    """Each line names the form, why and the form that passes, under a lead and the scope's facts, each line once."""

    def test_job_line(self) -> None:
        _ = self.says(T("tail -f x | grep y &"),
                      Prose("guard job-lifetime: this call starts what outlives it unseen, or what its end kills "
                            "unseen.\n"),
                      Prose("  * the background job starting `tail -f x` is reaped by no plain `wait` after it in the "
                            f"shell that started it, {DIES}. {REAP_FORM}\n"),
                      Prose("Every call runs in a scope stopped when its shell exits: what is left in it gets SIGTERM, "
                            "then SIGKILL 2 s later,\nnohup and setsid notwithstanding"),
                      Prose("with a plain `wait`; start a unit with systemd-run --wait (or --pipe), which the call "
                            "waits for."))

    def test_long_forms_cut(self) -> None:
        long = "x" * 120
        _ = self.says(T(f"echo {long} &"), Prose(f"  * the background job starting `echo {long[:95]}...` is"))
        _ = self.says(T(f"sleep 1 & disown {long}"), Prose(f"  * `disown {long[:93]}...` takes a job"))

    def test_each_line_once(self) -> None:
        said = self.says(T("nohup sleep 1 & disown; nohup sleep 1 & disown; wait"))
        self.assertEqual(said.count("takes a job out"), 1, said)
        self.assertEqual(said.count("the background job starting"), 0, said)

    def test_form_lines(self) -> None:
        _ = self.says(T("sleep 30 & disown"),
                      Prose(f"  * `disown` takes a job out of its shell's table, so no `wait` reaps it, {DIES}. "
                            f"{REAP_FORM}\n"))
        _ = self.says(T("setsid -f sleep 30"),
                      Prose(f"  * `setsid -f sleep 30` forks a process no shell waits for, {DIES}. Run it without -f, "
                            "or a long run with run_in_background.\n"))
        _ = self.says(T("start-stop-daemon -S -b -x /usr/bin/sleep"),
                      Prose(f"  * `start-stop-daemon -S -b -x /usr/bin/sleep` forks a daemon that stays in the call's "
                            f"scope, {DIES}. Run it in the foreground, or a long run with run_in_background.\n"))
        _ = self.says(T("tmux new -d -s build"),
                      Prose(f"  * `tmux new -d -s build` starts a tmux session, window or pane, a scope of its own, "
                            f"{ESCAPES}. Run the command itself, or a long run with run_in_background.\n"))
        _ = self.says(T("systemctl --user start probe.service"),
                      Prose(f"  * `systemctl --user start probe.service` starts a unit, {ESCAPES}. Run the work as "
                            "systemd-run --wait.\n"))
        _ = self.says(T("systemd-run --user sleep 30"),
                      Prose(f"  * `systemd-run --user sleep 30` starts a unit it does not wait for, {ESCAPES}. Give it "
                            "--wait (or --pipe) before its command, and the call waits for the unit.\n"))


def commands(call: ShellText) -> tuple[Command, ...]:
    """The commands of the call model of ``call``."""
    return build(call, None, ReadSettings(READER, Seconds(5.0), {})).commands


class Kept(unittest.TestCase):
    """The helpers other rules import: a peeled wrapper, and the start of a carrier's, script's or Python's shell."""

    def test_wraps(self) -> None:
        wrapper, run = commands(T("timeout 5 sleep 1"))
        self.assertTrue(wraps(wrapper, run))
        self.assertFalse(wraps(wrapper, run._replace(processes=(ProcessId(9),))))
        self.assertFalse(wraps(wrapper, None))

    def test_starts_shell(self) -> None:
        carrier, run = commands(T("bash -c 'sleep 1'"))
        self.assertTrue(starts_shell(carrier, run))
        first, sub = commands(T("echo a; ( sleep 1 )"))
        self.assertFalse(starts_shell(first, sub))
        self.assertFalse(starts_shell(carrier, None))


class SliceRule(unittest.TestCase):
    """With the slice rule on, each systemd-run is refused once per reason: this rule for how long its unit lives,
    the slice rule for where it lands."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places(frozenset({RULE, SLICE_RULE}), SLICED)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def heads(self, call: ShellText) -> tuple[bool, bool]:
        """Whether this rule, and the slice rule, refuse ``call``."""
        got = judged(self.at, call)
        return MARK in got.stderr, SLICE_MARK in got.stderr

    def test_each_reason_once(self) -> None:
        self.assertEqual(self.heads(T("systemd-run --user --slice=claude.slice sleep 30")), (True, False))
        self.assertEqual(self.heads(T("systemd-run --user --wait sleep 30")), (False, True))
        self.assertEqual(self.heads(T("systemd-run --user sleep 30")), (True, True))


if __name__ == "__main__":
    unittest.main()
