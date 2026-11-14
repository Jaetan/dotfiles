"""The polling-loops rule, through the dispatcher with every other rule switched off: a Bash call holding a shell loop
that sleeps, waits with a timeout or spins on checks, or Python code whose loop sleeps, is refused, each line naming
what polls, wherever its code lies, in a git work tree or not, but a script its project declares unread; a Monitor
call polls by design and passes. Cases named by an old id carry the old kit's no-polling-loops case of that id; the
rest are new."""

import contextlib
import json
import os
import shutil
import subprocess
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
MARK = Prose("guard polling-loops: this call polls in a loop.")
WAITER = "#!/usr/bin/env python3\nimport os, time\nwhile not os.path.exists('/tmp/x.done'):\n    time.sleep(0.05)\n"
WATCH = "while pgrep -f cargo > /dev/null; do sleep 5; done\n"
NAP = "import time\ntime.sleep(1)\n"
SPAWNER = "import subprocess\nsubprocess.run(['bash', '-c', 'while true; do sleep 1; done'])\n"
UNREAD = 'schema = 1\n[unread_scripts]\npaths = ["tools/waits.sh", "tools/waits.py"]\n'


class Places(NamedTuple):
    """A scratch folder holding the project root with its tools, a scratch folder outside it, and the switch
    folder."""

    base: Path
    root: Path
    out: Path
    off: Path


def places() -> Places:
    """Fresh folders for one case class, every rule but polling-loops switched off, so a case judges this rule alone.
    The root is a git work tree whose tracked tools hold waiter.py and the package pkg looping around a sleep, watch.sh
    looping around pgrep, nap.py sleeping once, spawner.py starting a shell loop that sleeps, outer.sh running the
    scratch folder's watch.sh, and waits.sh and waits.py, copies of watch.sh and waiter.py its declaration names
    unread; the scratch folder, in no work tree, holds waiter.py, watch.sh and nap.py alike and runs-tool.sh running
    the root's waiter.py."""
    base = Path(tempfile.mkdtemp()).resolve()
    found = Places(base, base / "project", base / "elsewhere", base / "off")
    for folder in (found.root / "tools" / "pkg", found.root / ".claude", found.out / ".config" / "claude-guard",
                   found.off):
        folder.mkdir(parents=True)
    git = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    _ = subprocess.run(["git", "init", "-q", str(found.root)], env=git, check=True, capture_output=True)
    _ = (found.out / ".config" / "claude-guard" / "machine.toml").write_text(NONE_DECLARED, encoding="utf-8")
    for module in (KIT / "rules").glob("*.py"):
        if not module.stem.startswith("_") and module.stem != "polling_loops":
            (found.off / module.stem.replace("_", "-")).touch()
    tools = found.root / "tools"
    scripts = {tools / "waiter.py": WAITER, tools / "pkg" / "__main__.py": WAITER, tools / "watch.sh": WATCH,
               tools / "nap.py": NAP, tools / "spawner.py": SPAWNER, tools / "outer.sh": f"bash {found.out}/watch.sh\n",
               tools / "waits.sh": WATCH, tools / "waits.py": WAITER,
               found.out / "waiter.py": WAITER, found.out / "watch.sh": WATCH, found.out / "nap.py": NAP,
               found.out / "runs-tool.sh": f"python3 {tools}/waiter.py\n"}
    for script, text in scripts.items():
        _ = script.write_text(text, encoding="utf-8")
        script.chmod(0o755)
    _ = (found.root / ".claude" / "guard.toml").write_text(UNREAD, encoding="utf-8")
    _ = subprocess.run(["git", "-C", str(found.root), "add", "-A"], env=git, check=True, capture_output=True)
    return found


def judged(at: Places, call: ShellText, tool: ShellText = ShellText("Bash"),
           event: ShellText = ShellText("PreToolUse")) -> Answer:
    """The dispatcher's answer to an ``event`` of ``tool`` for ``call`` run from the project root."""
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)),
                        ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(at.out))}), home=at.out)
    fields = {"hook_event_name": event, "tool_name": tool, "cwd": str(at.root), "tool_input": {"command": call}}
    return answer(EventBytes(json.dumps(fields).encode()), settings)


class Judged(unittest.TestCase):
    """Fresh folders per class, removed after it."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)


class Refused(Judged):
    """Each polling shape is refused under the rule's own mark."""

    def check(self, cases: dict[CaseId, ShellText]) -> None:
        """Every case refused by this rule."""
        for case, call in cases.items():
            with self.subTest(case=case, call=call):
                got = judged(self.at, call)
                self.assertEqual(got.status, 2)
                self.assertTrue(got.stderr.startswith(MARK), got.stderr)

    def test_the_old_cases(self) -> None:
        out = self.at.out
        self.check({
            CaseId("L01"): ShellText("until grep -q done /tmp/x.log; do\n  sleep 5\ndone"),
            CaseId("L02"): ShellText("for i in {1..30}; do\n echo tick\n sleep 2\ndone"),
            CaseId("L03"): ShellText("while true; do\n pgrep -f cargo || break\n sleep 10\ndone"),
            CaseId("L04"): ShellText("kill -INT 12345; for i in $(seq 1 40); do kill -0 12345 2>/dev/null || break; "
                                     "sleep 1; done"),
            CaseId("F3b"): ShellText("time while ! grep -q done /tmp/x.log; do sleep 5; done"),
            CaseId("K11"): ShellText("while pgrep -f 'x{99999999999}' >/dev/null; do sleep 5; done"),
            CaseId("L44"): ShellText("until timeout 5 tail -f /tmp/a | grep -q DONE; do :; done"),
            CaseId("L45"): ShellText("while ! read -t 5 < /tmp/fifo; do echo waiting; done"),
            CaseId("L46"): ShellText("until inotifywait -t 5 -e close_write /tmp/out; do echo again; done"),
            CaseId("L47"): ShellText("until [ -f /tmp/x.done ]; do :; done"),
            CaseId("L44b"): ShellText("until taskset -c 0-19 timeout 5 tail -f /tmp/a | grep -q DONE; do echo again; "
                                      "done"),
            CaseId("L45b"): ShellText("while ! read -rt 5 < /tmp/fifo; do echo waiting; done"),
            CaseId("L46b"): ShellText("until inotifywait -qt 5 /tmp/out; do echo again; done"),
            CaseId("L46e"): ShellText("until inotifywait --timeout 5 /tmp/out; do echo again; done"),
            CaseId("L48"): ShellText("while kill -0 $p 2>/dev/null; do :; done"),
            CaseId("L50"): ShellText("python3 -c \"import os, time\nwhile not os.path.exists('/tmp/x'):\n"
                                     " time.sleep(2)\""),
            CaseId("L50b"): ShellText("python3 -c \"import asyncio\nasync def m():\n while True:\n"
                                      "  await asyncio.sleep(1)\nasyncio.run(m())\""),
            CaseId("L51"): ShellText("python3 - <<'PY'\nimport time\nfor _ in range(3):\n time.sleep(1)\nPY"),
            CaseId("L53"): ShellText(f"python3 {out}/waiter.py"),
            CaseId("L53b"): ShellText(f"{out}/waiter.py"),
            CaseId("L19.2"): ShellText(f"bash {out}/watch.sh"),
            CaseId("B7a"): ShellText("until curl -sf http://127.0.0.1:8765/ >/dev/null; do :; done"),
            CaseId("B7b"): ShellText("until nc -z 127.0.0.1 8765; do :; done"),
            CaseId("B7c"): ShellText("until [ -f /tmp/b7-flag ]; do continue; done"),
            CaseId("B7d"): ShellText("until gh run view 123 --json status -q .status | grep -q completed; do :; done"),
            CaseId("B7e"): ShellText("while read l; do echo \"$l\"; done < /tmp/b7-in\n"
                                     "until [ -f /tmp/b7-flag ]; do :; done"),
            CaseId("P7a"): ShellText("python3 - <<'PY'\nimport os, time\nfor _ in range(600):\n"
                                     " if os.path.exists('/tmp/p7'):\n  break\n time.sleep(1)\nPY"),
            CaseId("B7f"): ShellText("while ! [ -f /tmp/b7-flag ]; do python3 -c 'import time; time.sleep(1)'; done"),
        })

    def test_code_in_no_work_tree(self) -> None:
        """A script, module or input of a folder in no work tree is judged as the call's own text is."""
        out = self.at.out
        self.check({
            CaseId("a module outside every tree"): ShellText(f"cd {out} && python3 -m waiter"),
            CaseId("a script read on its input"): ShellText(f"bash < {out}/watch.sh"),
            CaseId("Python read on its input"): ShellText(f"python3 < {out}/waiter.py"),
            CaseId("a scratch script's sleep in the call's loop"): ShellText(f"until [ -f /tmp/x ]; do python3 "
                                                                             f"{out}/nap.py; done"),
            CaseId("a one-shot sleep through a function"): ShellText("f() { python3 -c 'import time; time.sleep(1)'; }; "
                                                                     "while true; do f; done"),
        })

    def test_code_in_a_work_tree(self) -> None:
        """The old kit's L52, L52b and L52c, which it and the exemption this rule dropped passed: a tracked script, a
        Python script or a module file in a git work tree, and what it runs, are judged as the call's own text is, a
        script Python runs among them though its project declares it unread."""
        out = self.at.out
        loop = "import time\nwhile True:\n    time.sleep(1)"
        self.check({
            CaseId("L52"): ShellText("python3 tools/waiter.py"),
            CaseId("L52b"): ShellText("./tools/waiter.py"),
            CaseId("L52c"): ShellText("python3 -m tools.waiter"),
            CaseId("a module attached to -m"): ShellText("python3 -mtools.waiter"),
            CaseId("a package"): ShellText("python3 -m tools.pkg"),
            CaseId("a shell script"): ShellText("bash tools/watch.sh"),
            CaseId("a shell named by its path"): ShellText("/bin/bash tools/watch.sh"),
            CaseId("a shell script after a command"): ShellText("echo go; bash tools/watch.sh"),
            CaseId("a shell script run by its path"): ShellText("./tools/watch.sh"),
            CaseId("a script by its name in its folder"): ShellText("cd tools && bash watch.sh"),
            CaseId("a script read on its input"): ShellText("bash < tools/watch.sh"),
            CaseId("Python read on its input"): ShellText("python3 < tools/waiter.py"),
            CaseId("a shell loop a tool's Python starts"): ShellText("python3 tools/spawner.py"),
            CaseId("a tool a scratch script runs"): ShellText(f"bash {out}/runs-tool.sh"),
            CaseId("a scratch script a tool runs"): ShellText("bash tools/outer.sh"),
            CaseId("a tool's one-shot sleep in the call's loop"): ShellText("until [ -f /tmp/x ]; do python3 "
                                                                            "tools/nap.py; done"),
            CaseId("a script the call writes in the tree"): ShellText(f"cat > tools/new.py <<'EOF'\n{loop}\nEOF\n"
                                                                      "python3 tools/new.py"),
            CaseId("-c code fed a tool on its input"): ShellText(f"python3 -c '{loop}' < tools/waiter.py"),
            CaseId("a declared script Python runs"): ShellText("python3 tools/waits.py"),
        })

    def test_a_script_found_on_path_from_an_unknown_folder(self) -> None:
        """bash finds watch.sh on PATH from a folder the call does not make known, so its file is not known and it is
        judged, whatever folder the hook runs in."""
        with contextlib.chdir(self.at.root / "tools"):
            self.check({CaseId("bash on PATH"): ShellText(f"PATH={self.at.root}/tools; cd $U; bash watch.sh")})

    def test_a_sleep_in_a_function_the_loop_calls(self) -> None:
        self.check({
            CaseId("function"): ShellText("f() { sleep 1; }; while true; do f; done"),
            CaseId("function of a function"): ShellText("f() { sleep 1; }; g() { f; }; until [ -f /tmp/x ]; do g; "
                                                        "done"),
            CaseId("recursive function"): ShellText("f() { sleep 1; f; }; while true; do f; done"),
        })

    def test_a_sleep_or_a_timed_wait_in_any_loop(self) -> None:
        self.check({
            CaseId("select"): ShellText("select x in a b; do sleep 1; done"),
            CaseId("a for's words"): ShellText("for x in $(sleep 1; echo a); do echo \"$x\"; done"),
            CaseId("a sleep named by its path"): ShellText("while true; do /usr/bin/sleep 1; done"),
            CaseId("a sleep named in quotes"): ShellText("while true; do 'sleep' 1; done"),
            CaseId("cat under timeout"): ShellText("until timeout 5 cat /tmp/fifo; do echo again; done"),
            CaseId("pidwait under timeout"): ShellText("until timeout 5 pidwait -x cargo; do echo again; done"),
            CaseId("read -t5"): ShellText("while ! read -t5 l < /tmp/fifo; do echo waiting; done"),
            CaseId("--timeout= after the operand"): ShellText("until inotifywait /tmp/out --timeout=5; do echo again; "
                                                              "done"),
            CaseId("read under timeout"): ShellText("until timeout 5 read l < /tmp/fifo; do echo again; done"),
            CaseId("inotifywait under timeout"): ShellText("until timeout 5 inotifywait /tmp/out; do echo again; done"),
        })

    def test_a_loop_that_spins(self) -> None:
        self.check({
            CaseId("a body of true"): ShellText("until nc -z 127.0.0.1 8765; do true; done"),
            CaseId("a condition the model holds no command for"): ShellText("until [[ -f /tmp/x ]]; do :; done"),
            CaseId("a spin under a wrapper"): ShellText("while kill -0 1; do command :; done"),
        })

    def test_a_loop_of_checks(self) -> None:
        bodies = ("test -d /tmp", "[ -d /tmp ]", "kill -0 1", "pgrep -x cargo", "pidof cargo", "ps -p 1",
                  "grep -q x /tmp/x", "stat /tmp/x", "ls /tmp/x", "false", "ls /tmp/x; :", "ls /tmp/x; true")
        self.check({CaseId(body): ShellText(f"until test -f /tmp/x; do {body}; done") for body in bodies})

    def test_each_line_names_what_polls(self) -> None:
        cases = {
            ShellText("while true; do sleep '5'; sleep 5; done"):
                Prose("  * `sleep 5` sleeps or waits with a timeout in the body of the while loop"),
            ShellText("until sleep 1; do echo x; done"):
                Prose("  * `sleep 1` sleeps or waits with a timeout in the condition of the until loop"),
            ShellText("f() { sleep 1; }; while true; do f; done"):
                Prose("  * `sleep 1` sleeps or waits with a timeout in the body of the while loop, through the "
                      "function `f`"),
            ShellText("until [ -f /tmp/x ]; do continue; :; continue; done"):
                Prose("  * the until loop spins: its body runs `continue`, `:` alone"),
            ShellText("while kill -0 1; do ls /tmp; ls /tmp; done"):
                Prose("  * the while loop polls: it runs `kill`, `ls` alone, only checks"),
            ShellText("python3 -c \"import time\nwhile True:\n time.sleep(1)\""):
                Prose("  * `python3 -c import time\nwhile True:\n time.sleep(1)` runs Python code whose loop at line 2 "
                      "sleeps"),
        }
        for call, line in cases.items():
            with self.subTest(call=call):
                got = judged(self.at, call)
                self.assertEqual(got.stderr.count(line), 1, got.stderr)

    def test_the_refusal_names_the_ways_to_wait(self) -> None:
        got = judged(self.at, ShellText("while true; do sleep 1; done"))
        for named in ("run_in_background", "read its output file once per turn", "`wait` on it", "Monitor tool"):
            self.assertIn(named, got.stderr)


class Passes(Judged):
    """A loop that neither sleeps, waits with a timeout nor spins, and text naming one, pass."""

    def check(self, cases: dict[CaseId, ShellText], tool: ShellText = ShellText("Bash")) -> None:
        """Every case passed."""
        for case, call in cases.items():
            with self.subTest(case=case, call=call):
                self.assertEqual(judged(self.at, call, tool), Answer(0, Prose(""), Prose("")))

    def test_the_old_cases(self) -> None:
        self.check({
            CaseId("N01"): ShellText("cat <<'EOF' > /tmp/doc.md\nuntil grep -q x f; do sleep 1; done\nEOF"),
            CaseId("N02"): ShellText("rg -n 'until .* do .*sleep' /tmp"),
            CaseId("N03"): ShellText("git commit -m \"until grep; do sleep 1; done\""),
            CaseId("N04"): ShellText("cat <<'END-DOC' > /tmp/doc.md\nwhile true; do sleep 1; done\nEND-DOC"),
            CaseId("N05"): ShellText("python3 -c \"s = 'while x; do sleep 1; done'\""),
            CaseId("L47b"): ShellText("i=0; while [ \"$i\" -lt 3 ]; do i=$((i+1)); done"),
            CaseId("L47c"): ShellText("while read -r l; do :; done < /tmp/list"),
            CaseId("L46c"): ShellText("while read -r line; do echo \"$line\"; done < /tmp/list"),
            CaseId("L46d"): ShellText("until inotifywait -e close_write /tmp/out; do echo again; done"),
            CaseId("L49"): ShellText("for p in $pids; do for t in $p $(pgrep -P $p); do :; done; done"),
            CaseId("B7h"): ShellText("while read l; do echo \"$l\"; done < /tmp/b7-in"),
        })

    def test_a_sleep_the_model_does_not_see(self) -> None:
        """The old kit refused this; the model reads no perl."""
        self.check({
            CaseId("B7g"): ShellText("while ! [ -f /tmp/b7-flag ]; do perl -e 'sleep 1'; done"),
        })

    def test_a_script_its_project_declares_unread(self) -> None:
        """A shell script the root's declaration names under `[unread_scripts]` is a program the model does not read,
        so the waiting it does is its project's: the same text undeclared, watch.sh, is refused."""
        self.check({
            CaseId("run through bash"): ShellText("bash tools/waits.sh"),
            CaseId("run by its path"): ShellText("./tools/waits.sh"),
            CaseId("sourced"): ShellText(". tools/waits.sh"),
        })

    def test_a_monitor_polls_by_design(self) -> None:
        self.check({
            CaseId("L23.2"): ShellText("while ! gh run view 1 --json status | grep -q completed; do sleep 30; done"),
            CaseId("L24.2"): ShellText("while kill -0 12345 2>/dev/null; do sleep 5; done"),
            CaseId("a Python loop"): ShellText("python3 -c \"import time\nwhile True:\n time.sleep(1)\""),
        }, ShellText("Monitor"))

    def test_the_new_cases(self) -> None:
        self.check({
            CaseId("a sleep outside any loop"): ShellText("sleep 5; echo done"),
            CaseId("a one-shot Python sleep outside any loop"): ShellText("python3 -c 'import time; time.sleep(1)'"),
            CaseId("a function no loop calls"): ShellText("f() { sleep 1; }; for x in a; do echo \"$x\"; done; f"),
            CaseId("a step the model holds no command for"): ShellText("i=0; while [ $i -lt 3 ]; do ((i++)); done"),
            CaseId("a condition the model holds no command for"): ShellText("while [[ -f /tmp/x ]]; do ls /tmp/x; "
                                                                            "done"),
            CaseId("a timeout on a command that does not wait"): ShellText("for f in a b; do timeout 5 ls \"$f\"; "
                                                                           "done"),
            CaseId("a `:` beside a command that is no check"): ShellText("while kill -0 1; do :; echo waiting; done"),
        })

    def test_options_that_set_no_timeout(self) -> None:
        self.check({
            CaseId("read's -d takes the rest of its cluster"): ShellText("while read -dt l; do echo \"$l\"; done "
                                                                         "< /tmp/list"),
            CaseId("read's -d takes the next word"): ShellText("while read -d -t l; do echo \"$l\"; done < /tmp/list"),
            CaseId("read's options end at its first name"): ShellText("while read l -t; do echo \"$l\"; done "
                                                                      "< /tmp/list"),
            CaseId("inotifywait's -e takes its value"): ShellText("until inotifywait -eattrib /tmp/out; do echo again; "
                                                                  "done"),
            CaseId("inotifywait's options end at --"): ShellText("until inotifywait -e modify -- -t; do echo again; "
                                                                 "done"),
            CaseId("a long option is no cluster"): ShellText("until inotifywait --timefmt %T /tmp/out; do echo again; "
                                                             "done"),
        })


class Reach(Judged):
    """The rule judges a Bash call before it runs, and the kit refuses a call it cannot read before any rule."""

    def test_another_event(self) -> None:
        got = judged(self.at, ShellText("while true; do sleep 1; done"), event=ShellText("PostToolUse"))
        self.assertEqual(got, Answer(0, Prose(""), Prose("")))

    def test_a_call_the_kit_cannot_read(self) -> None:
        """The old cases U01 and U02: the old kit passed U02, whose loop does not sleep."""
        for case, call in ((CaseId("U01"), ShellText("while true; do sleep 1; done; echo \"open")),
                           (CaseId("U02"), ShellText("while true; do echo x; done; echo \"open"))):
            with self.subTest(case=case):
                got = judged(self.at, call)
                self.assertEqual(got.status, 2)
                self.assertIn(PARSE_FORM, got.stderr)


if __name__ == "__main__":
    unittest.main()
