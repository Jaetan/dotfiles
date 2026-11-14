"""The call model: wrappers peeled, each command placed in its shell, carriers and scripts re-read, directories
followed, and the calls it cannot read refused; every case reads its text with the kit's own shell reader,
bin/shell-reader."""

import os
import shutil
import socket
import stat
import tempfile
import unittest
import warnings
from collections.abc import Callable
from unittest import mock
from pathlib import Path
from typing import NamedTuple, NewType

from dispatcher import model, python, scripts
from dispatcher.model import (INPUT_WORDS, WRAPPED_SHELL_FORM, Assignment, Call, Command, End, Environ, LoopKind,
                              LoopPart, ProcessId, ReadSettings, Shell, build)
from dispatcher.python import LineNumber, PythonText
from dispatcher.tree import PARSE_FORM, Count, ReaderPath, Seconds, ShellText
from dispatcher.verdict import KitFault, Unreadable
from dispatcher.words import Directory, Environment, VarName
from dispatcher.wrappers import Dialect

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
ENV: Environment = {VarName("HOME"): ShellText("/home/u"), VarName("SPACED"): ShellText("a b")}
SETTINGS = ReadSettings(READER, Seconds(5.0), ENV)
type Placement = tuple[ShellText | None, tuple[Shell, ...]]
type Gaps = list[tuple[ShellText, ShellText]]
type Values = tuple[ShellText | None, ...]
ScriptName = NewType("ScriptName", str)
ScriptBody = NewType("ScriptBody", bytes)


def read(text: ShellText, cwd: Directory = Directory("/r"), settings: ReadSettings = SETTINGS) -> Call:
    """The call model of ``text`` run from ``cwd``."""
    return build(text, cwd, settings)


def places(text: ShellText) -> list[Placement]:
    """Each command's name and shells, in order."""
    return [(command.name, command.shells) for command in read(text).commands]


def stand_in(folder: Path, body: ShellText) -> ReaderPath:
    """A reader that runs the shell ``body`` in place of bin/shell-reader."""
    path = folder / "reader"
    _ = path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return ReaderPath(str(path))


OLD_CARRIED = {"A51": "script -qc 'cargo test' /dev/null", "A59": "xargs -0 -r cargo test < /dev/null"}
"""A51 and A59 of the old kit: the text script -c runs and the command xargs runs are read as more of the call."""


class Wrappers(unittest.TestCase):
    """A wrapper that only runs the command after it is peeled; one that runs none stays the command."""

    def test_each_wrapper_is_peeled(self) -> None:
        cases = {
            "env -u A -C /d B=1 cd /x": ("env",), "env --unset=A --chdir /d cd /x": ("env",),
            "taskset -c 0-19 cd /x": ("taskset",), "timeout -k 5 10 cd /x": ("timeout",),
            "timeout --signal=KILL 10 cd /x": ("timeout",), "timeout -sKILL 10 cd /x": ("timeout",),
            "nice -n 3 cd /x": ("nice",), "nice -10 cd /x": ("nice",), "nohup cd /x": ("nohup",),
            "setsid -f cd /x": ("setsid",), "stdbuf -oL -e 0 cd /x": ("stdbuf",), "command cd /x": ("command",),
            "builtin cd /x": ("builtin",), "exec cd /x": ("exec",), "exec -a n cd /x": ("exec",),
            "rtk cd /x": ("rtk",), "rtk proxy cd /x": ("rtk",), "/usr/bin/env cd /x": ("env",),
            "rtk proxy taskset -c 0-19 timeout 9 nice env A=1 cd /x": ("rtk", "taskset", "timeout", "nice", "env"),
            "env -- cd /x": ("env",),
        }
        for text, wrappers in cases.items():
            with self.subTest(text=text):
                command = read(ShellText(text)).commands[-1]
                self.assertEqual([w.spelled for w in command.words], ["cd", "/x"])
                self.assertEqual(command.wrappers, wrappers)

    def test_what_script_and_xargs_run(self) -> None:
        with_shell = ReadSettings(READER, Seconds(5.0), {**ENV, VarName("SHELL"): ShellText("/bin/bash")})
        cargo = [ShellText("cargo"), ShellText("test")]
        expected = {"A51": (cargo, ("carrier",), ()), "A59": ([*cargo, INPUT_WORDS.spelled], (), ("xargs",))}
        for old, text in OLD_CARRIED.items():
            with self.subTest(old=old):
                call = read(ShellText(text), settings=with_shell)
                command = call.commands[-1]
                self.assertEqual(([w.spelled for w in command.words], command.shells, command.wrappers, call.gaps),
                                 (*expected[old], ()))
        unknown = ShellText(OLD_CARRIED["A51"])
        self.assertEqual([(gap.text, gap.form) for gap in read(unknown).gaps], [(unknown, WRAPPED_SHELL_FORM)])

    def test_a_wrapper_that_runs_no_followed_command_stays(self) -> None:
        for text in ("command -v cd", "command -V cd", "taskset -p 99", "taskset -acp 0-19 99", "env -S 'cd /x'",
                     "env --split-string='cd /x'", "env", "timeout 5"):
            with self.subTest(text=text):
                command = read(ShellText(text)).commands[-1]
                self.assertEqual(command.wrappers, ())
                self.assertEqual(command.name, text.split()[0])

    def test_an_unknown_word_ends_the_options(self) -> None:
        cases = {"timeout $T cd /x": ("cd", ("timeout",)), "env $UNSET cd /x": (None, ("env",)),
                 "nice $UNSET cd /x": (None, ("nice",)), "taskset -c $C cd /x": ("cd", ("taskset",))}
        for text, (name, wrappers) in cases.items():
            with self.subTest(text=text):
                command = read(ShellText(text)).commands[-1]
                self.assertEqual((command.name, command.wrappers), (name, wrappers))


OLD_FOUND = {"A60": "find rust -maxdepth 0 -exec cargo test \\;"}
"""A60 of the old kit: the command a find's -exec runs is read as more of the call."""
type Found = tuple[tuple[ShellText | None, ...], Directory | None, End]
CARGO = (ShellText("cargo"), ShellText("test"))
HERE, CALLED, NULL = Directory("/r"), End("call"), End("null")


def found(text: ShellText) -> list[Found]:
    """Each command a find process of ``text`` runs: its words' values, its directory and its input."""
    return [(tuple(word.value for word in command.words), command.cwd, command.streams.input)
            for command in read(text).commands if "find" in command.shells]


class Finds(unittest.TestCase):
    """The command each action of a find runs, read as GNU find 4.10 reads its words, in a find process of its
    own."""

    def test_what_find_runs(self) -> None:
        call = read(ShellText(OLD_FOUND["A60"]))
        self.assertEqual([(command.name, command.shells, command.cwd) for command in call.commands],
                         [("find", (), HERE), ("cargo", ("find",), HERE)])
        self.assertEqual(call.gaps, ())

    def test_each_action_and_where_it_runs(self) -> None:
        cases: dict[ShellText, list[Found]] = {
            "cd /x && find . -exec cargo test \\;": [(CARGO, Directory("/x"), CALLED)],
            "find . -execdir cargo test \\;": [(CARGO, None, CALLED)],
            "find . -ok cargo test \\;": [(CARGO, HERE, NULL)],
            "find . -okdir cargo test \\;": [(CARGO, None, NULL)],
            "find . -exec cargo test \\; < /dev/null": [(CARGO, HERE, NULL)],
            "/usr/bin/find . -exec cargo test {} +": [((*CARGO, None), HERE, CALLED)],
            "find . -execdir cargo test {} +": [((*CARGO, None), None, CALLED)],
            "find . -ok cargo test {} + \\;": [((*CARGO, None, ShellText("+")), HERE, NULL)],
            "find . -exec echo -exec \\; -exec cargo test \\;": [((ShellText("echo"), ShellText("-exec")), HERE, CALLED),
                                                               (CARGO, HERE, CALLED)],
            "find . -name -exec -exec cargo test \\;": [(CARGO, HERE, CALLED)],
            'find . -amin "$A" -iname "$B" -name "$C" -xtype "$T" -fprintf "$F" "$G" -newermt "$M" -exec cargo test \\;':
                [(CARGO, HERE, CALLED)],
            "find -exec cargo test \\;": [(CARGO, HERE, CALLED)],
            "find -L -exec cargo test \\;": [(CARGO, HERE, CALLED)],
            'find -H -L -P -O3 -D tree -- - "$D" -exec cargo test \\;': [(CARGO, HERE, CALLED)],
            "find -D -exec cargo test \\;": [],
            "find . -exec \\;": [],
            "find . -exec exec cargo test \\;": [((ShellText("exec"), *CARGO), HERE, CALLED)],
            "find . -exec /bin/true \\;": [((ShellText("/bin/true"),), HERE, CALLED)],
            "find . -exec cargo test ./{} \\;": [((*CARGO, None), HERE, CALLED)],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(found(text), expected)
                self.assertEqual(read(ShellText(text)).gaps, ())

    def test_a_placeholder_is_a_part_not_known(self) -> None:
        cases = {
            "find python -exec cp {} x{}y \\;": ["python/", "xpython/"],
            "find python/a/ python/b -ok cp {} \\;": ["python/"],
            "find / -exec cp {} \\;": ["/"],
            "find python/* -exec cp {} \\;": ["python/"],
            "find -exec cp {} \\;": ["./"],
            "find python -execdir cp {} x{} \\;": ["./", "x./"],
            'find "$U" -exec cp {} x{} \\;': [None, "x"],
        }
        for text, prefixes in cases.items():
            with self.subTest(text=text):
                command = read(ShellText(text)).commands[-1]
                self.assertEqual([(word.value, word.prefix) for word in command.words],
                                 [("cp", "cp"), *((None, prefix) for prefix in prefixes)])

    def test_each_action_runs_in_a_process_of_its_own(self) -> None:
        text = ShellText("A=1 find . -exec timeout 5 bash -c 'cargo test' \\; -exec cargo test \\;")
        call = read(text)
        given = (Assignment(VarName("A"), ShellText("1")),)
        self.assertEqual([(command.name, command.shells) for command in call.commands],
                         [("find", ()), ("timeout", ("find",)), ("bash", ("find",)), ("cargo", ("find", "carrier")),
                          ("cargo", ("find",))])
        self.assertEqual([(got.name, got.environ.assigns) for got in peels(lambda: read(text))],
                         [("find", given), ("bash", given), ("cargo", given), ("cargo", given)])
        processes = [command.processes for command in call.commands[1:]]
        first, carrier, second = processes[0][0], processes[2][1], processes[3][0]
        self.assertEqual(processes, [(first,), (first,), (first, carrier), (second,)])
        self.assertEqual(len({first, carrier, second}), 3)

    def test_what_the_call_does_not_make_known_is_a_gap(self) -> None:
        unknown = "a word of the find expression is not known from the call"
        cases = {
            "find . -exec cargo test": "the find -exec action has no end",
            "find . -exec cargo test {} x +": "the find -exec action has no end",
            "find . -ok cargo test {} +": "the find -ok action has no end",
            'find . -exec cargo "$T" \\;': "the find -exec action holds a word not known from the call",
            'find . -type f "$X" -exec cargo test \\;': unknown,
            'find rust \\( "$X" \\)': unknown,
            'find rust ! "$X"': unknown,
        }
        for text, what in cases.items():
            with self.subTest(text=text):
                call = read(ShellText(text))
                self.assertEqual([(gap.what, gap.text, gap.form) for gap in call.gaps],
                                 [(what, text, model.FIND_FORM)])
                self.assertEqual([command for command in call.commands if "find" in command.shells], [])
        text = ShellText("find . -exec ./{} \\;")
        self.assertEqual([(gap.text, gap.form) for gap in read(text).gaps], [(text, model.SCRIPT_PATH_FORM)])


class Peeled(NamedTuple):
    """A command as the model's peeler gives it: its name past its wrappers, the wrappers, what the call does to the
    environment it starts with, and the dialect of the shell it is peeled in, None for a Python start or a find
    action."""

    name: ShellText | None
    wrappers: tuple[ShellText, ...]
    environ: Environ
    dialect: Dialect | None


def peels(build_call: Callable[[], Call]) -> list[Peeled]:
    """Each command the peeler gives while ``build_call`` builds a call, in the order it peels them: the environment
    and the dialect stay inside the model, which records neither on a command."""
    seen: list[Peeled] = []
    real = model.peel

    def spy(words: tuple[model.Word, ...], environ: Environ, dialect: Dialect | None) -> model.Peel:
        got = real(words, environ, dialect)
        seen.append(Peeled(got.words[0].value if got.words else None, got.wrappers, got.environ, dialect))
        return got

    with mock.patch.object(model, "peel", spy):
        _ = build_call()
    return seen


def dialects(build_call: Callable[[], Call]) -> dict[ShellText | None, Dialect | None]:
    """The dialect each command name is last peeled in while ``build_call`` builds a call."""
    return {got.name: got.dialect for got in peels(build_call)}


class Start(NamedTuple):
    """A command's name and wrappers, and what the call does to the environment it starts with."""

    name: ShellText | None
    wrappers: tuple[ShellText, ...]
    assigns: tuple[Assignment, ...]
    unsets: tuple[VarName | None, ...]
    cleared: bool


def start(text: ShellText) -> Start:
    """The last command of ``text`` as it starts."""
    got = peels(lambda: read(text))[-1]
    return Start(got.name, got.wrappers, *got.environ)


class Environments(unittest.TestCase):
    """An env wrapper's NAME=VALUE words are the command's assignments, after its own prefixes; an env -i or -
    empties the environment the command's shell exports, and an env -u removes a name from it."""

    def test_an_assignment_whatever_its_value(self) -> None:
        cases = {
            "env A=1 cmd": ("cmd", ("env",), (("A", "1"),), (), False),
            "env A=$X cmd": ("cmd", ("env",), (("A", None),), (), False),
            "env A=1 B=$(x) cmd": ("cmd", ("env",), (("A", "1"), ("B", None)), (), False),
            "env =x cmd": ("cmd", ("env",), (("", "x"),), (), False),
            "env 1A=x cmd": ("cmd", ("env",), (("1A", "x"),), (), False),
            "env A=1 -x=1 cmd": ("cmd", ("env",), (("A", "1"), ("-x", "1")), (), False),
            "env -- -x=1 cmd": ("cmd", ("env",), (("-x", "1"),), (), False),
            "env -a n A=1 cmd": ("cmd", ("env",), (("A", "1"),), (), False),
            "env --argv0 n cmd": ("cmd", ("env",), (), (), False),
            "env $X=1 cmd": (None, ("env",), (), (), False),
            "nohup A=1 cmd": ("A=1", ("nohup",), (), (), False),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(start(ShellText(text)), expected)

    def test_prefixes_come_first(self) -> None:
        cases = {
            "A=1 cmd": ("cmd", (), (("A", "1"),), (), False),
            "A=1 env B=2 cmd": ("cmd", ("env",), (("A", "1"), ("B", "2")), (), False),
            "A=1 env A=2 cmd": ("cmd", ("env",), (("A", "1"), ("A", "2")), (), False),
            "taskset -c 0-19 timeout 9 env A=$X cmd": ("cmd", ("taskset", "timeout", "env"), (("A", None),), (),
                                                       False),
            "A=1 env B=2 taskset -c 0 env C=3 cmd": ("cmd", ("env", "taskset", "env"),
                                                     (("A", "1"), ("B", "2"), ("C", "3")), (), False),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(start(ShellText(text)), expected)

    def test_emptying_and_unsetting(self) -> None:
        cases = {
            "A=1 env -i B=2 cmd": ("cmd", ("env",), (("B", "2"),), (), True),
            "A=1 env - B=2 cmd": ("cmd", ("env",), (("B", "2"),), (), True),
            "A=1 env -- - B=2 cmd": ("cmd", ("env",), (("B", "2"),), (), True),
            "A=1 env --ignore-environment cmd": ("cmd", ("env",), (), (), True),
            "A=1 env -iu C cmd": ("cmd", ("env",), (), (), True),
            "env -u A env -i cmd": ("cmd", ("env", "env"), (), (), True),
            "env -i A=1 env -u A cmd": ("cmd", ("env", "env"), (), (), True),
            "A=1 C=3 env -u A B=2 cmd": ("cmd", ("env",), (("C", "3"), ("B", "2")), ("A",), False),
            "A=1 env -uA cmd": ("cmd", ("env",), (), ("A",), False),
            "A=1 env --unset=A cmd": ("cmd", ("env",), (), ("A",), False),
            "A=1 env --unset A cmd": ("cmd", ("env",), (), ("A",), False),
            "A=1 env -u A A=2 cmd": ("cmd", ("env",), (("A", "2"),), ("A",), False),
            "A=1 env -u $X B=2 cmd": ("cmd", ("env",), (("A", None), ("B", "2")), (None,), False),
            "A=1 env --unset=$X B=2 cmd": ("cmd", ("env",), (("A", None), ("B", "2")), (None,), False),
            "A=1 env -u$X B=2 cmd": ("cmd", ("env",), (("A", None), ("B", "2")), (None,), False),
            "env -- -i cmd": ("-i", ("env",), (), (), False),
            "nohup - cmd": ("-", ("nohup",), (), (), False),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(start(ShellText(text)), expected)


class Placements(unittest.TestCase):
    """Each command is placed in the shell it runs in."""

    def test_the_calls_own_shell(self) -> None:
        for text in ("cd /x", "{ cd /x; }", "if true; then cd /x; fi", "while false; do cd /x; done",
                     "for a in b; do cd /x; done", "case a in a) cd /x;; esac", "f() { cd /x; }", "eval 'cd /x'",
                     "echo ${ cd /x;}", "echo ${| cd /x;}", "! cd /x", "time cd /x",
                     "cd /x && ls", "false || cd /x", "until true; do cd /x; done", "select a in b; do cd /x; done",
                     "if cd /x; then ls; fi", "case a in b) ls;; a) cd /x;; esac"):
            with self.subTest(text=text):
                self.assertIn((ShellText("cd"), ()), places(ShellText(text)))

    def test_other_shells(self) -> None:
        cases = {
            "(cd /x)": ("subshell",), "cd /x &": ("background",), "coproc cd /x": ("background",),
            "coproc w { cd /x; }": ("background",), "echo $(cd /x)": ("substitution",),
            "echo `cd /x`": ("substitution",), "cat <(cd /x)": ("substitution",), "x=$(cd /x)": ("substitution",),
            "cd /x | cat": ("pipeline",), "ls | cd /x | cat": ("pipeline",), "ls | cd /x": ("pipeline",),
            "bash -c 'cd /x'": ("carrier",), "sh -c 'cd /x'": ("carrier",), "bash -lc 'cd /x'": ("carrier",),
            "/bin/bash -e -o pipefail -c 'cd /x'": ("carrier",), "env bash -c 'cd /x'": ("carrier",),
            "(ls | cd /x) &": ("background", "subshell", "pipeline"),
            "(cd /x | cat) &": ("background", "subshell", "pipeline"), "ls | cd /x &": ("background", "pipeline"),
            "bash -c 'ls | cd /x'": ("carrier", "pipeline"), "[[ $(cd /x) ]]": ("substitution",),
            "cat > $(cd /x)": ("substitution",), "cat <<E\n$(cd /x)\nE": ("substitution",),
            "(( $(cd /x) ))": ("substitution",), "bash -c \"eval 'cd /x'\"": ("carrier",),
            "{ ls; } > $(cd /x)": ("substitution",),
        }
        for text, shells in cases.items():
            with self.subTest(text=text):
                self.assertIn((ShellText("cd"), shells), places(ShellText(text)))

    def test_a_carriers_dialect(self) -> None:
        got = dialects(lambda: read(ShellText("sh -c 'cd /b; bash -c pwd'")))
        self.assertEqual((got["sh"], got["cd"], got["bash"], got["pwd"]),
                         ("bash", "sh", "sh", "bash"))

    def test_each_substitution_is_walked_once(self) -> None:
        self.assertEqual(places(ShellText("echo $(echo $(cd /x))")),
                         [("cd", ("substitution", "substitution")), ("echo", ("substitution",)), ("echo", ())])

    def test_heredoc_data_runs_nothing(self) -> None:
        self.assertEqual(places(ShellText("cat <<'E'\ncd /x\nE")), [("cat", ())])

    def test_a_function_body_is_judged_where_it_is_defined(self) -> None:
        commands = read(ShellText("f() { cd /x; } && b")).commands
        self.assertEqual([(c.name, c.function) for c in commands], [("cd", "f"), ("b", "f")])

    def test_shells_wrap_in_order_inside_a_carrier(self) -> None:
        self.assertIn((ShellText("cd"), ("subshell", "carrier", "substitution")),
                      places(ShellText("(bash -c 'echo $(cd /x)')")))


def commands(text: ShellText) -> tuple[Command, ...]:
    """The commands of ``text``, in the order they run."""
    return read(text).commands


def seats(command: Command) -> list[Count]:
    """The position of ``command`` in each pipeline it is an element of, outermost first."""
    return [seat.position for seat in command.pipelines]


IDENTITY_CASES = (
    "a & b &", "{ a; b; } & c &", "a && b & c &", "a | b & c", "(a & wait; (wait))", "echo $(a; b) $(c)",
    "coproc w { a; b; }; coproc c", "(a); (b); bash -c 'c; d'; bash -c 'e'", "a | b | c", "bash -c 'a | b'",
    "(a | b) | c", "{ a; b; } | c", "a $(b | d) | c", "for d in a; do (b) & cd /x; done; (f)",
)


class Processes(unittest.TestCase):
    """Each command names the instance of every boundary it runs inside, two commands sharing one sharing that
    process."""

    def test_a_job_of_one_command(self) -> None:
        a, b = commands(ShellText("a & b"))
        self.assertEqual((a.shells, len(a.processes), b.shells, b.processes), (("background",), 1, (), ()))

    def test_a_job_of_a_group(self) -> None:
        a, b, c = commands(ShellText("{ a; b; } & c &"))
        self.assertEqual((a.shells, b.shells, c.shells), (("background",),) * 3)
        self.assertEqual(a.processes, b.processes)
        self.assertNotEqual(a.processes, c.processes)

    def test_a_job_of_an_and_or_list(self) -> None:
        a, b, c = commands(ShellText("a && b & c &"))
        self.assertEqual((a.shells, b.shells, c.shells), (("background",),) * 3)
        self.assertEqual(a.processes, b.processes)
        self.assertNotEqual(a.processes, c.processes)

    def test_a_job_of_a_pipeline_is_one_job(self) -> None:
        a, b, c = commands(ShellText("a | b & c"))
        self.assertEqual((a.shells, b.shells, c.processes), (("background", "pipeline"),) * 2 + ((),))
        self.assertEqual(a.processes[0], b.processes[0])
        self.assertNotEqual(a.processes[1], b.processes[1])

    def test_two_jobs_in_one_shell(self) -> None:
        a, b = commands(ShellText("a & b &"))
        self.assertEqual((a.shells, b.shells), (("background",), ("background",)))
        self.assertNotEqual(a.processes, b.processes)

    def test_a_wait_in_the_jobs_shell_and_in_a_subshell(self) -> None:
        a, here, inner = commands(ShellText("(a & wait; (wait))"))
        self.assertEqual((a.shells, here.shells, inner.shells),
                         (("subshell", "background"), ("subshell",), ("subshell", "subshell")))
        self.assertEqual(here.processes, a.processes[:-1])
        self.assertEqual(inner.processes[:-1], a.processes[:-1])
        self.assertNotEqual(inner.processes, a.processes[:-1])
        job, wait = commands(ShellText("a & wait"))
        self.assertEqual((job.processes[:-1], wait.processes), ((), ()))

    def test_a_substitution(self) -> None:
        a, b, c, echo = commands(ShellText("echo $(a; b) $(c)"))
        self.assertEqual((a.shells, c.shells, echo.processes), (("substitution",), ("substitution",), ()))
        self.assertEqual(a.processes, b.processes)
        self.assertNotEqual(a.processes, c.processes)

    def test_a_coprocess(self) -> None:
        a, b, c = commands(ShellText("coproc w { a; b; }; coproc c"))
        self.assertEqual((a.shells, c.shells), (("background",), ("background",)))
        self.assertEqual(a.processes, b.processes)
        self.assertNotEqual(a.processes, c.processes)

    def test_each_subshell_and_each_carrier(self) -> None:
        a, b, carrier, c, d, other, e = commands(ShellText("(a); (b); bash -c 'c; d'; bash -c 'e'"))
        self.assertNotEqual(a.processes, b.processes)
        self.assertEqual((carrier.processes, other.processes, c.processes), ((), (), d.processes))
        self.assertEqual((c.shells, e.shells), (("carrier",), ("carrier",)))
        self.assertNotEqual(c.processes, e.processes)

    def test_a_loop_walked_twice_names_what_one_pass_names(self) -> None:
        twice = commands(ShellText("for d in a; do (b); c | e; cd /x; done; (f)"))
        once = commands(ShellText("for d in a; do (b); c | e; done; (f)"))
        self.assertEqual([(c.processes, c.pipelines) for c in twice if c.name != "cd"],
                         [(c.processes, c.pipelines) for c in once])

    def test_every_identifier_names_one_instance(self) -> None:
        for text in IDENTITY_CASES:
            with self.subTest(text=text):
                first: dict[ProcessId, Command] = {}
                for command in commands(ShellText(text)):
                    self.assertEqual(len(command.processes), len(command.shells))
                    for depth, process in enumerate(command.processes):
                        holder = first.setdefault(process, command)
                        self.assertEqual(holder.processes.index(process), depth)
                        self.assertEqual((holder.shells[:depth + 1], holder.processes[:depth]),
                                         (command.shells[:depth + 1], command.processes[:depth]))


class Pipelines(unittest.TestCase):
    """Each command names every pipeline it is an element of, with its position."""

    def test_every_element_in_a_process_of_its_own(self) -> None:
        a, b, c = commands(ShellText("a | b | c"))
        self.assertEqual((a.shells, b.shells, c.shells), (("pipeline",),) * 3)
        self.assertEqual(len({a.processes, b.processes, c.processes}), 3)
        self.assertEqual((seats(a), seats(b), seats(c)), ([0], [1], [2]))
        self.assertEqual(len({a.pipelines[0].pipeline, b.pipelines[0].pipeline, c.pipelines[0].pipeline}), 1)

    def test_a_pipeline_in_a_bash_carrier(self) -> None:
        _, a, b = commands(ShellText("bash -c 'a | b'"))
        self.assertEqual((a.shells, b.shells), (("carrier", "pipeline"), ("carrier", "pipeline")))
        self.assertEqual(a.processes[0], b.processes[0])
        self.assertNotEqual(a.processes[1], b.processes[1])
        self.assertEqual((seats(a), seats(b)), ([0], [1]))
        self.assertEqual(a.pipelines[0].pipeline, b.pipelines[0].pipeline)

    def test_each_pipeline_is_its_own(self) -> None:
        a, b, c, d = commands(ShellText("a | b; c | d"))
        self.assertEqual(a.pipelines[0].pipeline, b.pipelines[0].pipeline)
        self.assertNotEqual(a.pipelines[0].pipeline, c.pipelines[0].pipeline)
        self.assertEqual(c.pipelines[0].pipeline, d.pipelines[0].pipeline)

    def test_nested_pipelines_outermost_first(self) -> None:
        a, b, c = commands(ShellText("(a | b) | c"))
        self.assertEqual((seats(a), seats(b), seats(c)), ([0, 0], [0, 1], [1]))
        self.assertEqual([seat.pipeline for seat in a.pipelines], [seat.pipeline for seat in b.pipelines])
        self.assertEqual(a.pipelines[0].pipeline, c.pipelines[0].pipeline)
        self.assertNotEqual(a.pipelines[0].pipeline, a.pipelines[1].pipeline)

    def test_a_group_element(self) -> None:
        a, b, c = commands(ShellText("{ a; b; } | c"))
        self.assertEqual((seats(a), a.pipelines, seats(c)), ([0], b.pipelines, [1]))

    def test_a_substitution_is_in_no_pipeline_outside_it(self) -> None:
        b, d, a, c = commands(ShellText("a $(b | d) | c"))
        self.assertEqual((seats(b), seats(d), seats(a), seats(c)), ([0], [1], [0], [1]))
        self.assertNotEqual(b.pipelines[0].pipeline, a.pipelines[0].pipeline)
        self.assertEqual(commands(ShellText("a < <(b) | c"))[0].pipelines, ())
        self.assertEqual(commands(ShellText("a ${ b;} | c"))[0].pipelines, ())

    def test_a_command_in_no_pipeline(self) -> None:
        self.assertEqual([c.pipelines for c in commands(ShellText("a && b; (c) &"))], [(), (), ()])


type Looped = list[tuple[LoopKind, LoopPart]]


def looped(command: Command) -> Looped:
    """The kind of each loop ``command`` runs inside and the part holding it, outermost first."""
    return [(loop.kind, loop.part) for loop in command.loops]


INSIDE_A_LOOP: dict[ShellText, tuple[Shell, ...]] = {
    ShellText("while a; do bash -c 'b'; done"): ("carrier",),
    ShellText("cat > w.sh <<'EOF'\nb\nEOF\nwhile a; do bash w.sh; done"): ("script",),
    ShellText("while a; do python3 -c \"import subprocess; subprocess.run(['b'])\"; done"): ("python",),
    ShellText("while a; do find . -exec b ';'; done"): ("find",),
}
"""A carrier, a script, a Python start and a find action run in a loop's body, each with the shells of b."""


class Loops(unittest.TestCase):
    """Each command names every while, until, for and select loop it runs inside, outermost first, and whether it is
    in the loop's condition or its body."""

    def test_a_while(self) -> None:
        a, b, c = commands(ShellText("while a; do b; done; c"))
        self.assertEqual((looped(a), looped(b), c.loops), ([("while", "condition")], [("while", "body")], ()))
        self.assertEqual(a.loops[0].loop, b.loops[0].loop)

    def test_an_until(self) -> None:
        a, b = commands(ShellText("until a; do b; done"))
        self.assertEqual((looped(a), looped(b)), ([("until", "condition")], [("until", "body")]))

    def test_a_for_and_its_words(self) -> None:
        for text in ("for x in $(a) y; do b; done", "for ((i = 0; i < $(a); i++)); do b; done"):
            with self.subTest(text=text):
                a, b = commands(ShellText(text))
                self.assertEqual((looped(a), looped(b)), ([("for", "condition")], [("for", "body")]))
                self.assertEqual(a.loops[0].loop, b.loops[0].loop)

    def test_a_for_leaves_its_variable_unknown(self) -> None:
        echo = commands(ShellText("d=/a; for d in /b; do echo $d; done"))[-1]
        self.assertEqual((echo.words[1].value, looped(echo)), (None, [("for", "body")]))

    def test_a_select(self) -> None:
        a, b = commands(ShellText("select x in $(a); do b; done"))
        self.assertEqual((looped(a), looped(b)), ([("select", "condition")], [("select", "body")]))

    def test_nested_loops_outermost_first(self) -> None:
        a, b, c = commands(ShellText("while a; do for x in $(b); do c; done; done"))
        self.assertEqual((looped(a), looped(b), looped(c)), ([("while", "condition")],
                                                             [("while", "body"), ("for", "condition")],
                                                             [("while", "body"), ("for", "body")]))
        self.assertEqual(len({a.loops[0].loop, b.loops[0].loop, c.loops[0].loop}), 1)
        self.assertEqual(b.loops[1].loop, c.loops[1].loop)
        self.assertNotEqual(c.loops[0].loop, c.loops[1].loop)

    def test_a_carrier_a_script_a_python_start_and_a_find_action_inside_a_loop(self) -> None:
        for text, shells in INSIDE_A_LOOP.items():
            with self.subTest(text=text):
                found = commands(text)
                a = next(command for command in found if command.name == "a")
                b = next(command for command in found if command.name == "b")
                self.assertEqual((b.shells, looped(b)), (shells, [("while", "body")]))
                self.assertEqual(b.loops[0].loop, a.loops[0].loop)

    def test_a_function_called_inside_a_loop(self) -> None:
        a, b, f = commands(ShellText("f() { a; }; while b; do f; done"))
        self.assertEqual((a.loops, looped(b), looped(f)), ((), [("while", "condition")], [("while", "body")]))
        b, c, g = commands(ShellText("while b; do g() { c; }; g; done"))
        self.assertEqual((c.function, looped(c), looped(g)), ("g", [("while", "body")], [("while", "body")]))
        self.assertEqual(c.loops, g.loops)

    def test_a_loop_inside_a_substitution(self) -> None:
        a, b, echo = commands(ShellText("echo $(while a; do b; done)"))
        self.assertEqual((looped(a), looped(b), echo.loops), ([("while", "condition")], [("while", "body")], ()))
        self.assertEqual(b.shells, ("substitution",))
        a, b, echo = commands(ShellText("while a; do echo $(b); done"))
        self.assertEqual((b.shells, looped(b), b.loops), (("substitution",), [("while", "body")], echo.loops))

    def test_a_command_after_a_loop_is_in_none(self) -> None:
        _, b, c, d, e = commands(ShellText("while a; do b; done; c; for x in y; do d; done; e"))
        self.assertEqual((c.loops, e.loops, looped(d)), ((), (), [("for", "body")]))
        self.assertNotEqual(b.loops[0].loop, d.loops[0].loop)

    def test_a_body_walked_twice_names_its_loop_once(self) -> None:
        twice = commands(ShellText("while a; do cd /x; while e; do b; done; done; until c; do d; done"))
        once = commands(ShellText("while a; do while e; do b; done; done; until c; do d; done"))
        self.assertEqual([c.loops for c in twice if c.name != "cd"], [c.loops for c in once])
        self.assertEqual([len(c.loops) for c in once], [1, 2, 2, 1, 1])


def nested(depth: Count) -> ShellText:
    """`cd /x` inside ``depth`` evals."""
    text = "cd /x"
    for _ in range(depth):
        text = "eval '" + text.replace("'", "'\\''") + "'"
    return ShellText(text)


class Carriers(unittest.TestCase):
    """A carrier's text is read as more of the call, to a depth limit; one the call does not make known is a gap."""

    def test_unknown_texts_are_gaps(self) -> None:
        for text in ('eval "$X"', 'bash -c "$X"', "eval $(cat f)"):
            with self.subTest(text=text):
                call = read(ShellText(text))
                self.assertEqual([gap.text for gap in call.gaps], [text])

    def test_a_script_is_no_carrier(self) -> None:
        call = read(ShellText("bash script.sh; bash -x s.sh; bash $S -c"))
        self.assertEqual(([(c.name, c.shells) for c in call.commands], [gap.form for gap in call.gaps]),
                         ([("bash", ())] * 3, [model.SCRIPT_FIND_FORM, model.SCRIPT_FIND_FORM, model.SCRIPT_PATH_FORM]))

    def test_the_depth_limit(self) -> None:
        self.assertIn(ShellText("cd"), [c.name for c in read(nested(Count(model.MAX_DEPTH))).commands])
        with self.assertRaises(Unreadable) as raised:
            _ = read(nested(Count(model.MAX_DEPTH + 1)))
        self.assertIn(f"more than {model.MAX_DEPTH} deep", raised.exception.reason)

    def test_eval_joins_its_words(self) -> None:
        self.assertIn((ShellText("cd"), ()), places(ShellText("eval cd /x")))

    def assert_refused(self, *texts: ShellText) -> None:
        """Each text is one gap, the whole command, whose form is to run it as bash, its words never read as bash."""
        for text in texts:
            with self.subTest(text=text):
                call = read(ShellText(text))
                self.assertEqual(([gap.text for gap in call.gaps], [gap.form for gap in call.gaps]),
                                 ([text], [model.FOREIGN_FORM]))
                self.assertNotIn(ShellText("cd"), [c.name for c in call.commands])

    def test_zsh_and_fish_are_gaps_bare(self) -> None:
        self.assert_refused("zsh", "fish")

    def test_zsh_and_fish_are_gaps_whatever_their_words(self) -> None:
        self.assert_refused("zsh --version", "fish --version", "zsh -n -c 'cd /x'", "fish -n -c 'cd /x'",
                            "zsh -c 'cd /x'", "fish -c 'cd /x'", "zsh s.zsh", "fish s.fish -c 'cd /x'",
                            "zsh -- -c 'cd /x'", "zsh $X -c 'cd /x'", 'fish -c "$X"', "fish --bogus", "zsh -c")

    def test_zsh_and_fish_are_gaps_by_path(self) -> None:
        self.assert_refused("/usr/bin/zsh --version", "/usr/bin/fish -c 'cd /x'", "bin/zsh")

    def test_zsh_and_fish_are_gaps_through_env(self) -> None:
        self.assert_refused("env zsh --version", "env A=1 fish", "env -i /usr/bin/zsh -n -c 'cd /x'",
                            "env -u A fish -c 'cd /x'")

    def test_zsh_and_fish_are_gaps_through_timeout(self) -> None:
        self.assert_refused("timeout 5 zsh --version", "timeout -k 1 5 fish -c 'cd /x'")

    def test_zsh_and_fish_are_gaps_through_rtk(self) -> None:
        self.assert_refused("rtk zsh --version", "rtk proxy fish -c 'cd /x'")

    def test_a_command_neither_shell_runs_is_no_gap(self) -> None:
        for text in ("zshx -c 'cd /x'", "$F -c 'cd /x'", "command -v zsh", "which fish", "echo zsh fish",
                     "bash -c 'echo zsh'"):
            with self.subTest(text=text):
                self.assertEqual(read(ShellText(text)).gaps, ())

    def test_a_nested_zsh_or_fish_is_its_own_gap(self) -> None:
        for nested, inner in (("bash -c 'zsh --version'", "zsh --version"), ("eval fish", "fish"),
                              ("sh -c 'env fish -n'", "env fish -n")):
            with self.subTest(text=nested):
                self.assertEqual([gap.text for gap in read(ShellText(nested)).gaps], [inner])

    def test_dash_runs_its_text_as_sh(self) -> None:
        start = "import subprocess; subprocess.run(['dash', '-c', 'cd /x'])"
        for text in ("dash -c 'cd /x'", "/bin/dash -ec 'cd /x'", "env A=1 dash -c 'cd /x'", "timeout 5 dash -c 'cd /x'",
                     "nohup dash -c 'cd /x'", "rtk dash -c 'cd /x'", f'python3 -c "{start}"'):
            with self.subTest(text=text):
                [cd] = [command for command in read(ShellText(text)).commands if command.name == "cd"]
                self.assertEqual((cd.shells[-1], dialects(lambda: read(ShellText(text)))["cd"]), ("carrier", "sh"))
        self.assertEqual([gap.text for gap in read(ShellText('dash -c "$X"')).gaps], ['dash -c "$X"'])

    def test_an_option_bundle_reads_as_the_shells_getopt(self) -> None:
        for text in ("bash -eo pipefail -c 'cd /x'", "bash -oe pipefail -c 'cd /x'", "sh -eo errexit -c 'cd /x'",
                     "dash -eo errexit -c 'cd /x'", "bash +eo errexit -c 'cd /x'", "bash -eO extglob -c 'cd /x'",
                     "bash -oc errexit 'cd /x'", "bash -ooc errexit nounset 'cd /x'", "bash -c - 'cd /x'",
                     "dash -c -- 'cd /x'", "bash +c 'cd /x'", "bash --rcfile x -c 'cd /x'",
                     "bash --init-file x -c 'cd /x'", "bash + -c 'cd /x'", "sh -e + -c 'cd /x'"):
            with self.subTest(text=text):
                self.assertIn((ShellText("cd"), ("carrier",)), places(ShellText(text)))
        call = read(ShellText("bash -co errexit"))
        self.assertEqual(([command.name for command in call.commands], call.gaps), (["bash"], ()))


class Dash(unittest.TestCase):
    """An sh text is read as dash runs it: no pushd, popd or builtin, and `time` a command keeping its changes."""

    def test_what_moves_the_directory(self) -> None:
        cases = {"pushd /y; ls": ("/r", "/y"), "cd /y; popd; ls": ("/y", None), "builtin cd /y; ls": ("/r", "/y"),
                 "time cd /y; ls": ("/r", "/y"), "cd /y; ls": ("/y", "/y"), "command cd /y; ls": ("/y", "/y")}
        for text, (dash, bash) in cases.items():
            with self.subTest(text=text):
                got = [read(ShellText(f"{shell} -c '{text}'")).commands[-1].cwd for shell in ("sh", "bash")]
                self.assertEqual(got, [dash, bash])
        self.assertEqual(read(ShellText("pushd /y; ls")).commands[-1].cwd, "/y")

    def test_builtin_is_a_command(self) -> None:
        self.assertEqual([c.name for c in read(ShellText("sh -c 'builtin cd /y'")).commands], ["sh", "builtin"])


class Unreadables(unittest.TestCase):
    """A call the reader cannot parse, or too long to read, is Unreadable; a reader that fails is a KitFault."""

    def test_a_parse_error_names_the_place_and_the_form(self) -> None:
        for text in ("echo ${=x}", "bash -c 'echo ${=x}'", "echo 'x"):
            with self.subTest(text=text), self.assertRaises(Unreadable) as raised:
                _ = read(ShellText(text))
            self.assertRegex(raised.exception.reason, r"^bash cannot parse it: .+ at line \d+, column \d+$")
            self.assertEqual(raised.exception.form, PARSE_FORM)

    def test_a_text_too_long(self) -> None:
        with self.assertRaises(Unreadable):
            _ = read(ShellText("#" * (model.MAX_CALL_TEXT + 1)))
        text = "#" * (model.MAX_CALL_TEXT * 3 // 5) + "\n"
        with self.assertRaises(Unreadable):
            _ = read(ShellText(f"eval '{text}'"))

    def test_a_text_read_twice_counts_once(self) -> None:
        text = "#" * (model.MAX_CALL_TEXT * 2 // 5) + "\n"
        self.assertEqual(read(ShellText(f"for a in b; do cd /x; eval '{text}'; done")).commands[0].name, "cd")

    def test_too_many_commands(self) -> None:
        kept = model.MAX_COMMANDS
        model.MAX_COMMANDS = 3
        try:
            _ = read(ShellText("a; b; c"))
            with self.assertRaises(Unreadable):
                _ = read(ShellText("a; b; c; d"))
        finally:
            model.MAX_COMMANDS = kept

    def test_reader_faults(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            cases = {
                "exit 3": "exited 3", "echo nope": "printed no JSON tree", "echo '[1]'": "not a tree",
                "echo '{\"ok\": true, \"stmts\": [{\"cmd\": {\"type\": \"weird\"}}]}'": "does not hold a weird node",
                "exec sleep 5": "outran its 0.5 s limit",
                "echo '{\"ok\": true, \"stmts\": [{\"cmd\": {\"type\": \"while\", \"cond\": {}}}]}'":
                    "a condition is a node",
                "echo '{\"ok\": true, \"stmts\": [{\"cmd\": {\"type\": \"function\", \"body\": \"x\"}}]}'":
                    "body is text",
            }
            for body, said in cases.items():
                with self.subTest(body=body), self.assertRaises(KitFault) as raised:
                    _ = read(ShellText("true"), settings=ReadSettings(stand_in(folder, ShellText(body)),
                                                                      Seconds(0.5), ENV))
                self.assertIn(said, raised.exception.reason)
            with self.assertRaises(KitFault) as raised:
                _ = read(ShellText("true"), settings=ReadSettings(ReaderPath(str(folder / "none")), Seconds(1), ENV))
            self.assertIn("cannot run", raised.exception.reason)

    def test_text_that_is_not_utf8(self) -> None:
        with self.assertRaises(Unreadable):
            _ = read(ShellText("echo \udc80"))


class Directories(unittest.TestCase):
    """The directory each command runs in, followed through the call's own changes."""

    def test_followed(self) -> None:
        cases = {
            "cd /a; cd b; pwd": ["/r", "/a", "/a/b"], "cd ..; pwd": ["/r", "/"], "pushd /p; pwd": ["/r", "/p"],
            "popd; pwd": ["/r", None], "cd; pwd": ["/r", None], "cd -; pwd": ["/r", None],
            "D=/d; cd $D/e; pwd": ["/r", "/r", "/d/e"], "cd ~; pwd": ["/r", "/home/u"],
            "cd -P /a; pwd": ["/r", "/a"], "(cd /s); pwd": ["/r", "/r"], "x=$(cd /s); pwd": ["/r", "/r", "/r"],
            "cd /s & pwd": ["/r", "/r"], "cd /s | cat; pwd": ["/r", "/r", "/r"], "ls | cd /s; pwd": ["/r", "/r", "/r"],
            "if a; then cd /i; fi; pwd": ["/r", "/r", None],
            "if a; then cd /i; else cd /i; fi; pwd": ["/r", "/r", "/r", "/i"],
            "case a in a) cd /c;; esac; pwd": ["/r", None], "for x in a; do cd ..; done; pwd": [None, None],
            "while a; do pwd; done; pwd": ["/r", "/r", "/r"], "f() { cd /f; }; pwd": ["/r", None],
            "eval 'cd /e'; pwd": ["/r", "/r", "/e"], "echo ${ cd /b;}; pwd": ["/r", "/b", "/b"],
            "bash -c 'cd /b; pwd'; pwd": ["/r", "/r", "/b", "/r"], "cd a b; pwd": ["/r", None],
            "export D=/x; cd $D; pwd": ["/r", "/r", "/x"], "cd $PWD/q; pwd": ["/r", "/r/q"],
            "if a; then D=/x; fi; cd $D; pwd": ["/r", "/r", "/r", None], "(D=/x); cd $D; pwd": ["/r", "/r", None],
            "d=/q; for d in a; do pwd; done; cd $d; pwd": ["/r", "/r", "/r", None],
            "D=/x true; cd $D; pwd": ["/r", "/r", None],
        }
        for text, cwds in cases.items():
            with self.subTest(text=text):
                self.assertEqual([command.cwd for command in read(ShellText(text)).commands], cwds)

    def test_a_loop_body_is_judged_from_its_second_pass(self) -> None:
        commands = read(ShellText("for d in a b; do pwd; cd /x; done")).commands
        self.assertEqual([(c.name, c.cwd) for c in commands], [("pwd", None), ("cd", None)])

    def test_an_unknown_start(self) -> None:
        self.assertEqual([c.cwd for c in build(ShellText("cd a; pwd; cd /b; pwd"), None, SETTINGS).commands],
                         [None, None, None, "/b"])


class Words(unittest.TestCase):
    """A word's value as its command receives it, where the call makes it known."""

    def test_values(self) -> None:
        cases = {
            "a\\ b": "a b", "'a b'": "a b", '"a $HOME"': "a /home/u", '"\\$x\\q"': "$x\\q", "~": "/home/u",
            "~/a": "/home/u/a", "~u/a": None, "~+": None, "a*": None, "a\\*": "a*", "'a*'": "a*", "{a,b}": None,
            "{a..c}": None, "{}": "{}", "a{b}": "a{b}", "$SPACED": None, '"$SPACED"': "a b", "$UNSET": None,
            "$(x)": None, "$((1))": None, "$'a'": "a", "$'\\n'": None, "${HOME}": "/home/u", "${HOME:-x}": None,
            "${#HOME}": None, "$1": None, "@(a)": None, "a\\\nb": "ab", '"a\\\nb"': "ab", "x~": "x~",
            "~$HOME": "~/home/u", '~"/a"': "~/a", "~'x'": "~x", '~""': "~", "~\\/a": "~/a", '~/"a"': "/home/u/a",
            "~/a\\b": "/home/u/ab", "~u$HOME": "~u/home/u", '~u"x"': "~ux", "a[b": "a[b", "a[b]": None, "[ab]": None,
            "[": "[", "]a[": "]a[", "a?b": None, "a\\[b]": "a[b]", 'a["]"': "a[]", 'a["b"]': None,
        }
        for text, value in cases.items():
            with self.subTest(text=text):
                self.assertEqual(read(ShellText(f"echo {text}")).commands[-1].words[1].value, value)

    def test_a_bracket_no_later_bracket_closes_is_literal(self) -> None:
        cases = {
            "[ -f x ] && echo y": [["[", "-f", "x", "]"], ["echo", "y"]],
            "V='x['; echo $V ${V}a]": [["echo", "x[", None]], "V='[x]'; echo $V": [["echo", None]],
            "V='b]'; echo a[$V": [["echo", None]], "V=']x['; echo $V": [["echo", "]x["]],
        }
        for text, values in cases.items():
            with self.subTest(text=text):
                self.assertEqual([[w.value for w in c.words] for c in read(ShellText(text)).commands if c.words], values)

    def test_assignments_and_redirections(self) -> None:
        text = ShellText("A=1 B=$(x) C= D+=2 cmd > out 2>&1 <<'E'\nbody\nE")
        command = read(text).commands[-1]
        self.assertEqual([(a.name, a.value) for a in peels(lambda: read(text))[-1].environ.assigns],
                         [("A", "1"), ("B", None), ("C", ""), ("D", None)])
        self.assertEqual([(r.op, r.fd, r.target.spelled, r.heredoc) for r in command.redirs],
                         [(">", None, "out", None), (">&", "2", "1", None), ("<<", None, "E", "body\n")])

    def test_a_declaration_is_a_command(self) -> None:
        command = read(ShellText("export A=1 B -x")).commands[0]
        self.assertEqual([w.spelled for w in command.words], ["export", "A=1", "B", "-x"])


class Prefixes(unittest.TestCase):
    """A word's prefix, its value up to the first part or character the shell would expand, and the folder that
    prefix reaches from the command's directory."""

    def test_prefix_and_folder(self) -> None:
        cases = {
            "echo a/b": ("a/b", "/r/a/b"), "echo /x/y": ("/x/y", "/x/y"), "echo plain": ("plain", "/r/plain"),
            "echo a{b}": ("a{b}", "/r/a{b}"), "echo tools/*.py": ("tools/", "/r/tools"),
            'echo "a b"/c*': ("a b/c", "/r/a b"), "echo 'x*'/y": ("x*/y", "/r/x*/y"),
            "echo 'x*'/y*": ("x*/y", "/r/x*"), "echo {a,b}/c": (None, None), "K=/k; echo $K/x*": ("/k/x", "/k"),
            "echo $UNKNOWN/x": (None, None), "echo ~/x/*": ("/home/u/x/", "/home/u/x"),
            "echo @(a|b)/c": (None, None), "echo *": ("", "/r"), "echo \\*a*": ("*a", "/r"), "echo /a*": ("/a", "/"),
            "echo a{b,c}": ("a", "/r"), "echo x/$UNKNOWN": ("x/", "/r/x"), "echo ''$UNKNOWN/x": (None, None),
            'echo "a$(x)"b': ("a", "/r"), "echo ~u/*": (None, None), 'echo "$SPACED"/x*': ("a b/x", "/r/a b"),
            "echo x$'\\n'y": ("x", "/r"), "echo ~/{a,b}": ("/home/u/", "/home/u"), "echo a\\\nb*": ("ab", "/r"),
            'echo "x*"/y*': ("x*/y", "/r/x*"), 'echo ~"/a"*': ("~/a", "/r/~"), "echo ~'x'*": ("~x", "/r"),
            "K=/k; echo ~$K/x*": ("~/k/x", "/r/~/k"), "echo ~$UNKNOWN/x": ("~", "/r"), "echo x[y]*": ("x", "/r"),
            "echo x[y*": ("x[y", "/r"), 'echo x["y"]*': ("x", "/r"), "echo ]x[*": ("]x[", "/r"),
            "echo a[b/c*": ("a[b/c", "/r/a[b"), "V='x['; echo $V$(y)": (None, None),
            "V='x['; echo $V*": ("x[", "/r"),
        }
        for text, (prefix, folder) in cases.items():
            with self.subTest(text=text):
                command = read(ShellText(text)).commands[-1]
                word = command.words[1]
                self.assertEqual((word.prefix, word.folder(command.cwd)), (prefix, folder))

    def test_an_unknown_directory(self) -> None:
        for text, folder in {"echo a*": None, "echo a": None, "echo /a/b*": "/a", "echo /a": "/a"}.items():
            with self.subTest(text=text):
                self.assertEqual(build(ShellText(text), None, SETTINGS).commands[-1].words[1].folder(None), folder)

    def test_redirections_and_declarations(self) -> None:
        command = read(ShellText("cat > logs/x*")).commands[-1]
        target = command.redirs[0].target
        self.assertEqual((target.prefix, target.folder(command.cwd)), ("logs/x", "/r/logs"))
        words = read(ShellText("export A=x* B C=$(y) -x D=1")).commands[-1].words
        self.assertEqual([(w.value, w.prefix) for w in words], [("export", "export"), (None, "A=x"), ("B", "B"),
                                                                (None, "C="), ("-x", "-x"), ("D=1", "D=1")])


SCRIPTS = {ScriptName(name): ScriptBody(body) for name, body in {
    "bash.sh": b"#!/bin/bash\ncd /d\n", "sh.sh": b"#!/bin/sh\ncd /d\n", "dash.sh": b"#!/usr/bin/env dash\ncd /d\n",
    "none.sh": b"cd /d\n", "lib.sh": b"V=/from-lib\ncd /lib\n", "params.sh": b'make -j"$1" "$@" $# "$0"\n',
    "shift.sh": b'shift\nmake -j"$1" "$0"\n', "func.sh": b'f() { make -j"$1"; }\nmake -j"$1"\n',
    "carrier.sh": b"bash -c 'make -j\"$1\"' _ 9\n", "split.sh": b"make -j$1 $1\n", "env.sh": b'make -j"$N"\n',
    "z.sh": b"#!/usr/bin/zsh\ncd /d\n", "f.fish": b"#!/usr/bin/env fish\ncd /d\n",
    "py.py": b"#!/usr/bin/env python3\nimport os\n", "node.js": b"#!/usr/bin/env node\ncd /d\n",
    "prog": b"\x7fELF\x02\x01\x01cd /d\n", "nul.dat": b"cd /d\0\n",
    "bad.sh": b"if then fi (\n", "latin.sh": b"echo \xe9\n", "locked.sh": b"cd /d\n", "self.sh": b"./self.sh\n",
    "long.sh": b"#" * (model.MAX_CALL_TEXT + 1) + b"\n", "run.sh": b"cd /d\n", "sub/run.sh": b"cd /sub\n",
    "-c": b"cd /minus-c\n",
    "huge.sh": "\u00e9".encode() * (2 * model.MAX_CALL_TEXT + 1), "selfsrc.sh": b". ./selfsrc.sh\n",
    "envs.sh": b"#!/usr/bin/env -S A=1 bash -e\ncd /d\n", "envu.sh": b"#!/usr/bin/env -u X sh\ncd /d\n",
    "bshift.sh": b'builtin shift\nmake -j"$1"\n', "seto.sh": b'set -euo pipefail\nmake -j"$1"\n',
    "setx.sh": b'set -- x\nmake -j"$1"\n', "fshift.sh": b'f() { shift; }\nmake -j"$1"\n',
    "ifshift.sh": b'if true; then shift; fi\nmake -j"$1"\n', "atpart.sh": b'make -j"$@"\n', "star.sh": b'make "$*"\n',
    "atun.sh": b"make $@\n", "starun.sh": b"make $*\n", "atx.sh": b'make "$@x"\n', "ops.sh": b'make "${1:-x}" "${#1}"\n',
    "empty.sh": b"make $1 x\n", "subst.sh": b'echo "$(make $1)"\n', "gen.sh": b"cat > w2.sh <<EOF\ncd /g$1\nEOF\nbash w2.sh\n",
    "evals.sh": b"eval 'make -j\"$1\"'\n", "feval.sh": b"f() { eval 'make -j\"$1\"'; }\nf 9\n",
    "outer.sh": b". ./inner.sh\n", "outer2.sh": b". ./inner.sh 5\n", "inner.sh": b'make -j"$1" "$0"\n',
    "computes.sh": b'cd /d\n"$1/t"\n',
}.items()}


LINKS = {"run.lnk": "run.sh", "stdin.lnk": "/dev/stdin", "chain.lnk": "stdin.lnk", "fd3.lnk": "/dev/fd/3", "dev.lnk": "/dev",
         "self.lnk": "/proc/self", "null.lnk": "/dev/null", "loop.lnk": "loop2.lnk", "loop2.lnk": "loop.lnk"}
"""The symbolic links of the scripts' folder, by name and target."""

OLD_INPUT_READ = {"bash <<'EOF'\ncd /a58\nEOF": ("/a58", "bash")}
"""A58 of the old kit: a heredoc fed to bash is read as its script."""
OLD_INPUT_UNKNOWN = {"A57": "echo cargo test | bash"}
"""A57 of the old kit: a script piped into bash is a gap, the pipe's text unknown to the call."""


class Scripts(unittest.TestCase):
    """A shell script the call runs is read as more of the call: run by its path or through bash, sh or dash in a
    script shell of its own, sourced in the current shell, its positional parameters bound, its text the last the
    call writes into its path or else its file; one the kit cannot find, read or parse is a gap naming a form."""

    folder: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp()).resolve()
        for name, body in SCRIPTS.items():
            (cls.folder / name).parent.mkdir(exist_ok=True)
            _ = (cls.folder / name).write_bytes(body)
        (cls.folder / "locked.sh").chmod(0)
        for name, target in LINKS.items():
            (cls.folder / name).symlink_to(target)
        os.mkfifo(cls.folder / "fifo")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(cls.folder / "sock"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder)

    def at(self, text: ShellText) -> Call:
        """The call model of ``text`` run from the scripts' folder."""
        return read(text, Directory(str(self.folder)))

    def last(self, text: ShellText, name: ShellText) -> Command:
        """The last command named ``name`` that ``text`` runs."""
        return [command for command in self.at(text).commands if command.name == name][-1]

    def values(self, text: ShellText) -> list[Values]:
        """The word values of each make ``text`` runs."""
        return [tuple(word.value for word in c.words) for c in self.at(text).commands if c.name == "make"]

    def gaps(self, text: ShellText) -> Gaps:
        """Each gap of ``text`` as its prose and its form."""
        return [(ShellText(gap.what), ShellText(gap.form)) for gap in self.at(text).gaps]

    def test_a_script_run_by_its_path(self) -> None:
        for text in ("./run.sh", f"{self.folder}/run.sh", "exec ./run.sh", "nohup ./run.sh a", "timeout 9 ./run.sh"):
            with self.subTest(text=text):
                call = self.at(ShellText(text + "; pwd"))
                cd = [command for command in call.commands if command.name == "cd"]
                self.assertEqual([(c.words[1].value, c.shells, c.cwd) for c in cd],
                                 [("/d", ("script",), str(self.folder))])
                self.assertEqual((call.commands[-1].cwd, call.gaps), (str(self.folder), ()))
        first, second = [c for c in self.at(ShellText("./run.sh; ./run.sh")).commands if c.name == "cd"]
        self.assertNotEqual(first.processes, second.processes)

    def test_a_script_run_under_noexec(self) -> None:
        """A shell under -n or -o noexec reads its script and runs nothing, the last of each and its + form deciding."""
        for text, runs in (("bash -n run.sh", False), ("sh -n run.sh", False), ("bash -o noexec run.sh", False),
                           ("bash -eo noexec run.sh", False), ("bash -n -- run.sh", False), ("bash -xn run.sh", False),
                           ("bash -n +n run.sh", True), ("bash +n run.sh", True), ("bash -o errexit run.sh", True),
                           ("bash -o noexec +o noexec run.sh", True), ("bash -n +o noexec run.sh", True),
                           ("bash -- run.sh", True)):
            with self.subTest(text=text):
                cd = [c for c in self.at(ShellText(text)).commands if c.name == "cd"]
                self.assertEqual(bool(cd), runs)
        self.assertEqual(self.gaps(ShellText('bash -n "$S"')), [])

    def test_a_script_its_project_declares_unread(self) -> None:
        declared = SETTINGS._replace(unread=frozenset({scripts.ScriptPath(str(self.folder / "computes.sh"))}))
        for text in ("./computes.sh a", f"{self.folder}/computes.sh", "bash computes.sh", "sh -e ./computes.sh",
                     "dash computes.sh", ". ./computes.sh", "source computes.sh a"):
            with self.subTest(text=text):
                unread = read(ShellText(text), Directory(str(self.folder)), declared)
                self.assertEqual(([c.text for c in unread.commands], unread.gaps), ([text], ()))
                kept = self.at(ShellText(text))
                self.assertEqual(("cd" in [c.name for c in kept.commands], bool(kept.gaps)), (True, True))
        written = read(ShellText("echo 'cd /w' > computes.sh; ./computes.sh"), Directory(str(self.folder)), declared)
        self.assertEqual([c.words[1].value for c in written.commands if c.name == "cd"], ["/w"])

    def test_the_dialect_a_script_is_read_in(self) -> None:
        cases = {"./bash.sh": "bash", "./sh.sh": "sh", "./dash.sh": "sh", "./none.sh": "bash", "sh -c ./none.sh": "sh",
                 "bash ./sh.sh": "bash", "sh ./bash.sh": "sh", "dash bash.sh": "sh", "/bin/bash sh.sh": "bash",
                 "./envs.sh": "bash", "./envu.sh": "sh", "find . -exec ./none.sh \\;": "sh",
                 "python3 -c \"import subprocess; subprocess.run(['./none.sh'])\"": "sh"}
        for text, dialect in cases.items():
            with self.subTest(text=text):
                command = self.last(ShellText(text), ShellText("cd"))
                self.assertEqual((dialects(lambda: self.at(ShellText(text)))["cd"], command.shells[-1]),
                                 (dialect, "script"))

    def test_an_interpreters_operand(self) -> None:
        for text in ("bash -x -o pipefail run.sh", "bash -- run.sh", "sh -e run.sh", "bash -eo pipefail run.sh",
                     "bash - run.sh", "bash --norc run.sh a", "bash +O extglob run.sh", "bash --rcfile x run.sh",
                     "bash + run.sh", "sh -e + run.sh", "bash + -x run.sh"):
            with self.subTest(text=text):
                self.assertEqual(self.last(ShellText(text), ShellText("cd")).shells, ("script",))
        missing = (ShellText(f"the script {self.folder}/-x {scripts.MISSING}"), ShellText(model.SCRIPT_FIND_FORM))
        self.assertEqual(self.gaps(ShellText("bash - -x")), [missing])
        for text in ("bash -c 'echo' run.sh", "sh -ec 'echo' run.sh", "source", "."):
            with self.subTest(text=text):
                call = self.at(ShellText(text))
                self.assertEqual(([c.name for c in call.commands if c.name == "cd"], call.gaps), ([], ()))
        self.assertIsNone(scripts.operand([ShellText(word) for word in ("bash", "-c", "cd /x", "run.sh")]))

    def test_a_script_on_an_interpreters_input(self) -> None:
        cases = {
            "bash <<'EOF'\ncd /h\nEOF": ("/h", "bash"), "bash <<< 'cd /w'": ("/w", "bash"), "sh < run.sh": ("/d", "sh"),
            "bash < bash.sh": ("/d", "bash"), "sh < bash.sh": ("/d", "sh"),
            "dash -s <<-EOF\n\tcd /t\n\tEOF": ("/t", "sh"),
            "bash - <<< 'cd /m'": ("/m", "bash"), "bash -x -o pipefail <<< 'cd /o'": ("/o", "bash"),
            "bash 0< run.sh": ("/d", "bash"), "bash <> run.sh": ("/d", "bash"), "bash < sub/run.sh": ("/sub", "bash"),
            "cd sub; bash < run.sh": ("/sub", "bash"), "echo 'cd /e' > w.sh; bash < w.sh": ("/e", "bash"),
            "bash -n +n <<< 'cd /p'": ("/p", "bash"), "bash -o noexec +o noexec <<< 'cd /q'": ("/q", "bash"),
            "bash <<< x <<< 'cd /l'": ("/l", "bash"), "bash 3< x.sh <<< 'cd /k'": ("/k", "bash"),
            "bash --norc <<< 'cd /r'": ("/r", "bash"),
        }
        for text, (folder, dialect) in {**cases, **OLD_INPUT_READ}.items():
            with self.subTest(text=text):
                cd = self.last(ShellText(text), ShellText("cd"))
                self.assertEqual((cd.words[1].value, dialects(lambda: self.at(ShellText(text)))["cd"], cd.shells),
                                 (folder, dialect, ("script",)))
                self.assertEqual(self.at(ShellText(text)).gaps, ())
        self.assertEqual(self.last(ShellText("bash <<< 'cd /x' | cat"), ShellText("cd")).shells, ("pipeline", "script"))
        params = {
            "bash -s 4 'x y' < params.sh": [("make", "-j4", "4", "x y", "2", "bash")],
            "bash < params.sh": [("make", "-j", "0", "bash")], "/bin/bash -s 4 < params.sh": [
                ("make", "-j4", "4", "1", "/bin/bash")],
            "sh -s -- 5 <<'EOF'\nmake -j\"$1\" $# \"$0\"\nEOF": [("make", "-j5", "1", "sh")],
            "bash -s $U < params.sh": [("make", None, None, None, "bash")],
            "bash --rcfile x -s 4 < params.sh": [("make", "-j4", "4", "1", "bash")],
            "bash -O extglob -s 4 < params.sh": [("make", "-j4", "4", "1", "bash")],
            "bash -eo pipefail -s 4 < params.sh": [("make", "-j4", "4", "1", "bash")],
            "bash -e -s 4 < params.sh": [("make", "-j4", "4", "1", "bash")],
            "bash -s -- -x 4 < params.sh": [("make", "-j-x", "-x", "4", "2", "bash")],
            "bash -s - 4 < params.sh": [("make", "-j4", "4", "1", "bash")],
            "bash -s - -x 4 < params.sh": [("make", "-j-x", "-x", "4", "2", "bash")],
            "bash -s + < params.sh": [("make", "-j", "0", "bash")],
            "bash -s + 4 < params.sh": [("make", "-j4", "4", "1", "bash")],
        }
        for text, makes in params.items():
            with self.subTest(text=text):
                self.assertEqual(self.values(ShellText(text)), makes)

    def test_an_interpreter_that_runs_nothing_on_its_input(self) -> None:
        for text in ("bash -n <<< 'cd /x'", "bash -o noexec <<< 'cd /x'", "bash -eo noexec <<< 'cd /x'",
                     "bash +n -n <<< 'cd /x'", "sh -n < run.sh", "bash -n < missing.sh", "bash --version",
                     "bash --help", "bash -c", "sh -ec", "bash < /dev/null", "bash <<'EOF'\nEOF", "bash < prog"):
            with self.subTest(text=text):
                call = self.at(ShellText(text))
                self.assertEqual(([c.name for c in call.commands if c.name == "cd"], call.gaps), ([], ()))

    def test_an_interpreters_input_the_call_does_not_make_known(self) -> None:
        where = self.folder
        unknown = (ShellText("the script bash reads on its input is not known from the call"), model.GAP_FORM)
        missing = ShellText(f"the script {where}/missing.sh {scripts.MISSING}")
        path, folder = ShellText("the path of the script it runs is not known from the call"), ShellText(
            "the script run.sh is run from a directory the call does not make known")
        cases = {
            "cat run.sh | bash": [unknown], "bash": [unknown], "bash -s run.sh": [unknown], "bash -x": [unknown],
            "bash --": [unknown], "bash -": [unknown], "bash -o": [unknown], "bash < <(echo cd /x)": [unknown],
            "echo cd /x | bash < /dev/stdin": [unknown], "bash <&3": [unknown], "bash < run.sh <&3": [unknown],
            "bash < run.sh 0>&-": [unknown], "bash <<EOF\ncd $U\nEOF": [unknown],
            "bash 3<<'EOF'\ncd /x\nEOF": [unknown],
            "bash &": [unknown], "{ bash; } < run.sh": [unknown],
            "echo cd /x | sh": [
                (ShellText("the script sh reads on its input is not known from the call"), model.GAP_FORM)],
            "bash < missing.sh": [(missing, model.SCRIPT_FIND_FORM)], "bash < $U": [(path, model.SCRIPT_PATH_FORM)],
            "cd $U; bash < run.sh": [(folder, model.SCRIPT_PATH_FORM)],
            "bash < latin.sh": [(ShellText(f"the script {where}/latin.sh {scripts.NOT_TEXT}"), model.SCRIPT_READ_FORM)],
            "cat > w.sh <<EOF\ncd $(pwd)\nEOF\nbash < w.sh": [
                (ShellText(f"the call writes the script {where}/w.sh with text it does not make known"),
                 model.SCRIPT_WRITE_FORM)],
        }
        for text, gaps in {**cases, **{text: [unknown] for text in OLD_INPUT_UNKNOWN.values()}}.items():
            with self.subTest(text=text):
                self.assertEqual(self.gaps(ShellText(text)), gaps)
        [(what, form)] = self.gaps(ShellText("bash < bad.sh"))
        self.assertRegex(what, rf"^in the script {where}/bad.sh, bash cannot parse it: .+")
        self.assertEqual(form, model.SCRIPT_PARSE_FORM)
        with self.assertRaises(Unreadable):
            _ = self.at(ShellText("bash <<'EOF'\nif then fi (\nEOF"))

    def refused(self, text: ShellText, path: scripts.ScriptPath) -> None:
        """Assert that the call ``text`` reads no script and has the one gap naming ``path`` as a descriptor."""
        what = ShellText(f"the script {path} names a descriptor, which the kit does not read")
        cds = [c.words[1].value for c in self.at(text).commands if c.name == "cd"]
        self.assertEqual((cds, self.gaps(text)), ([], [(what, model.DESCRIPTOR_FORM)]))

    def test_a_script_named_by_a_descriptor_path(self) -> None:
        cases = {
            "bash /dev/stdin <<< 'cd /w'": "/dev/stdin", "sh /dev/stdin <<'EOF'\ncd /h\nEOF": "/dev/stdin",
            "dash /dev/fd/3 3< run.sh": "/dev/fd/3", "bash /proc/self/fd/0 < run.sh": "/proc/self/fd/0",
            "bash /proc/thread-self/fd/4 4<<< 'cd /t'": "/proc/thread-self/fd/4", "bash /dev/fd/97": "/dev/fd/97",
            "bash //dev/fd/3 3< sub/run.sh": "//dev/fd/3", "bash /dev/stdin < /dev/null": "/dev/stdin",
            "bash /dev/stdout": "/dev/stdout", "bash /dev/stderr": "/dev/stderr", "bash /dev/fd/3": "/dev/fd/3",
            "echo 'cd /x' | bash /dev/stdin": "/dev/stdin", "{ bash /dev/stdin; } < run.sh": "/dev/stdin",
            "bash /dev/stdin 3<<< 'cd /k' <&3": "/dev/stdin",
            "echo 'cd /x' | bash < /proc/self/fd/0": "/proc/self/fd/0",
        }
        for text, path in cases.items():
            with self.subTest(text=text):
                self.refused(ShellText(text), scripts.ScriptPath(path))
        self.assertIn("by its own path", model.DESCRIPTOR_FORM)
        unknown = (ShellText("the script bash reads on its input is not known from the call"), model.GAP_FORM)
        for text in ("bash 3< run.sh < /dev/fd/3", "bash 3<<< 'cd /q' < /dev/fd/3", "bash < /proc/self/fd/3 3< run.sh",
                     "bash < /dev/stdin"):
            with self.subTest(text=text):
                self.assertEqual(self.gaps(ShellText(text)), [unknown])
        folder = ShellText("the script dev/stdin is run from a directory the call does not make known")
        self.assertEqual(self.gaps(ShellText("cd $U; bash < dev/stdin")), [(folder, model.SCRIPT_PATH_FORM)])

    def test_a_script_sourced_or_run_by_a_descriptor_path(self) -> None:
        cases = {
            "source /dev/stdin <<< 'cd /w'": "/dev/stdin", ". /dev/fd/3 3< run.sh": "/dev/fd/3",
            "source /proc/self/fd/3 3<<'EOF'\ncd /h\nEOF": "/proc/self/fd/3", ". /proc/thread-self/fd/0 < sub/run.sh":
            "/proc/thread-self/fd/0", "/dev/fd/3 3< run.sh": "/dev/fd/3", "/dev/stdin < sub/run.sh": "/dev/stdin",
            "/proc/self/fd/4 4<<< 'cd /t'": "/proc/self/fd/4", "/dev/fd/3 3< bash.sh": "/dev/fd/3",
            "source /dev/stdin < /dev/null": "/dev/stdin", "/dev/stdin < /dev/null": "/dev/stdin",
            "source /dev/stdin": "/dev/stdin", "{ . /dev/fd/3; } 3< run.sh": "/dev/fd/3", "/dev/fd/3": "/dev/fd/3",
            "echo cd /x | /dev/stdin": "/dev/stdin", "/dev/fd/3 3< z.sh": "/dev/fd/3",
            "/dev/fd/3 3< bad.sh": "/dev/fd/3",
            "source /dev/fd/3 3< bad.sh": "/dev/fd/3", ". /dev/stdin < bad.sh": "/dev/stdin",
        }
        for text, path in cases.items():
            with self.subTest(text=text):
                self.refused(ShellText(text), scripts.ScriptPath(path))

    def test_a_link_to_a_descriptor_path(self) -> None:
        cases = {
            "bash stdin.lnk <<< 'cd /k'": "stdin.lnk", "bash chain.lnk < sub/run.sh": "chain.lnk",
            "bash fd3.lnk 3< run.sh": "fd3.lnk", "bash dev.lnk/stdin <<< 'cd /v'": "dev.lnk/stdin",
            "bash self.lnk/fd/3 3< run.sh": "self.lnk/fd/3", "source stdin.lnk <<< 'cd /s'": "stdin.lnk",
            "./chain.lnk < run.sh": "chain.lnk", "bash stdin.lnk": "stdin.lnk", "bash 3< run.sh < fd3.lnk": "fd3.lnk",
            "bash 3< run.sh < dev.lnk/fd/3": "dev.lnk/fd/3",
        }
        for text, name in cases.items():
            with self.subTest(text=text):
                self.refused(ShellText(text), scripts.ScriptPath(f"{self.folder}/{name}"))
        run = ShellText("bash run.lnk")
        cd = self.last(run, ShellText("cd"))
        self.assertEqual((cd.words[1].value, cd.shells, self.at(run).gaps), ("/d", ("script",), ()))
        unknown = ShellText("the script bash reads on its input is not known from the call")
        self.assertEqual(self.gaps(ShellText("bash < fd3.lnk 3< run.sh")), [(unknown, model.GAP_FORM)])
        call = self.at(ShellText("bash null.lnk; bash /dev/null"))
        self.assertEqual(([c.name for c in call.commands], call.gaps), (["bash", "bash"], ()))
        loop = ShellText(f"the script {self.folder}/loop.lnk cannot be read: Too many levels of symbolic links")
        self.assertEqual(self.gaps(ShellText("bash loop.lnk")), [(loop, model.SCRIPT_READ_FORM)])

    def test_a_script_that_is_no_file_is_never_opened(self) -> None:
        cases = {"bash fifo": "fifo", "source fifo": "fifo", "./fifo": "fifo", "bash < fifo": "fifo",
                 "bash sock": "sock", "bash /dev/zero": "/dev/zero"}
        kinds = {"fifo": "a FIFO", "sock": "a socket", "/dev/zero": "a device"}
        for text, name in cases.items():
            with self.subTest(text=text):
                path = name if name.startswith("/") else f"{self.folder}/{name}"
                what = ShellText(f"the script {path} is {kinds[name]}, which the kit does not open")
                self.assertEqual(self.gaps(ShellText(text)), [(what, model.SCRIPT_READ_FORM)])
        for path in ("/dev/null", f"{self.folder}/null.lnk"):
            with self.subTest(path=path):
                self.assertEqual(scripts.on_disk(scripts.ScriptPath(path), Count(8)), scripts.Found(ShellText(""), None))

    def test_a_descriptor_path_is_known_by_its_spelling(self) -> None:
        cases = {"bash /dev/stdin <<< 'cd /w'": "/dev/stdin", "bash //dev/fd/3 3< run.sh": "//dev/fd/3",
                 "source /proc/self/fd/0 < sub/run.sh": "/proc/self/fd/0",
                 "/proc/thread-self/fd/4 4< run.sh": "/proc/thread-self/fd/4"}
        with mock.patch.object(model, "islink", return_value=False):
            for text, path in cases.items():
                with self.subTest(text=text):
                    self.refused(ShellText(text), scripts.ScriptPath(path))

    def test_a_lone_dash_ends_an_interpreters_options(self) -> None:
        for text in ("bash - -c 'cd /x'", "sh - -c 'cd /x'", "dash - -c 'cd /x'"):
            with self.subTest(text=text):
                cd = self.last(ShellText(text), ShellText("cd"))
                self.assertEqual((cd.words[1].value, cd.shells), ("/minus-c", ("script",)))

    def test_a_sourced_script_runs_in_the_shell(self) -> None:
        for text in ("source lib.sh; pwd", ". ./lib.sh; pwd", "sh -c '. ./lib.sh; pwd'", "source -- lib.sh; pwd",
                     f"cd /; PATH=/nowhere:{self.folder}; source lib.sh; pwd",
                     f"cd /; source -p /nowhere:{self.folder} lib.sh; pwd"):
            with self.subTest(text=text):
                pwd = self.last(ShellText(text), ShellText("pwd"))
                self.assertEqual((pwd.cwd, self.last(ShellText(text), ShellText("cd")).shells), ("/lib", pwd.shells))
        self.assertEqual(self.last(ShellText("source lib.sh; echo $V"), ShellText("echo")).words[1].value, "/from-lib")
        for text, folder in {"PATH=sub; source run.sh; pwd": "/sub", "PATH=sub; bash run.sh": "/d",
                             "PATH=sub; echo 'cd /pw' > sub/pw.sh; source pw.sh; pwd": "/pw"}.items():
            with self.subTest(text=text):
                self.assertEqual(self.last(ShellText(text), ShellText("cd")).words[1].value, folder)
        call = self.at(ShellText("sh -c 'source lib.sh; pwd'"))
        self.assertEqual(([c.name for c in call.commands], call.commands[-1].cwd),
                         (["sh", "source", "pwd"], str(self.folder)))

    def test_a_scripts_positional_parameters(self) -> None:
        cases = {
            "./params.sh 4 'x y'": [("make", "-j4", "4", "x y", "2", "./params.sh")],
            "bash params.sh 4": [("make", "-j4", "4", "1", "params.sh")],
            "./params.sh": [("make", "-j", "0", "./params.sh")],
            "./params.sh $UNKNOWN": [("make", None, None, None, "./params.sh")],
            "./params.sh 4 $UNKNOWN": [("make", "-j4", None, None, "./params.sh")],
            "source params.sh 5": [("make", "-j5", "5", "1", None)],
            "./shift.sh 4 5": [("make", None, "./shift.sh")],
            "./func.sh 4": [("make", None), ("make", "-j4")],
            "./carrier.sh 4": [("make", None)],
            "./split.sh 'a b'": [("make", None, None)], "./split.sh 3": [("make", "-j3", "3")],
            "N=3 ./env.sh": [("make", "-j3")], "env N=5 ./env.sh": [("make", "-j5")],
            "./params.sh 'a b'": [("make", "-ja b", "a b", "1", "./params.sh")], "./bshift.sh 4 5": [("make", None)],
            "./seto.sh 4": [("make", "-j4")], "./setx.sh 4": [("make", None)], "./fshift.sh 4": [("make", "-j4")],
            "./ifshift.sh 4 5": [("make", None)], "./atpart.sh 6": [("make", "-j6")], "./atpart.sh": [("make", "-j")],
            "./atpart.sh 6 7": [("make", None)], "./star.sh a b": [("make", "a b")], "./atun.sh 'a b' c": [("make", None)],
            "./atun.sh a c": [("make", "a", "c")], "./starun.sh a c": [("make", "a", "c")], "./atx.sh a b": [("make", None)],
            "./ops.sh 4": [("make", None, None)], "./empty.sh ''": [("make", None, "x")], "./subst.sh 'a b'": [("make", None)],
            "./evals.sh 4": [("make", None)], "./feval.sh 4": [("make", None)],
            "./outer.sh 4": [("make", "-j4", "./outer.sh")], "./outer2.sh 4": [("make", "-j5", "./outer2.sh")],
            "./atun.sh 'x[' c": [("make", "x[", "c")], "./atun.sh '[x]' c": [("make", None)],
            "./split.sh 'x['": [("make", None, None)], "./split.sh 'x]'": [("make", None, None)],
        }
        for text, makes in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.values(ShellText(text)), makes)

    def test_a_script_the_call_writes(self) -> None:
        cases = {
            "cat > w.sh <<'EOF'\ncd /written\nEOF\nbash w.sh": ["/written"],
            "printf 'cd /p\\n' > w.sh; bash w.sh": ["/p"], "printf '%s %s\\n' cd /q > w.sh; bash w.sh": ["/q"],
            "echo 'cd /e' > w.sh; ./w.sh": ["/e"], "echo -n 'cd /n' > w.sh; ./w.sh": ["/n"],
            "tee w.sh <<'EOF' >/dev/null\ncd /t\nEOF\nbash w.sh": ["/t"], "tee -a w.sh <<< 'cd /h'; bash w.sh": ["/h"],
            "D=/u; cat > w.sh <<EOF\ncd $D\nEOF\nbash w.sh": ["/u"], "cat <<-EOF > w.sh\n\tcd /tab\n\tEOF\n./w.sh": ["/tab"],
            "echo 'cd /a' > w.sh; echo 'cd /b' > w.sh; bash w.sh": ["/b"],
            "echo 'cd /a' > w.sh; echo 'cd /b' >> w.sh; bash w.sh": ["/a", "/b"],
            "echo 'cd /over' > run.sh; ./run.sh": ["/over"], ": > w.sh; bash w.sh": [],
            "echo 'cd /x' > w.sh > v.sh; bash w.sh; bash v.sh": ["/x"],
            f"echo 'cd /abs' > {self.folder}/w.sh; ./w.sh": ["/abs"],
            "echo -n 'cd ' > w.sh; echo /nn >> w.sh; bash w.sh": ["/nn"],
            "echo -en 'cd /x\\ncd /y\\n' > w.sh; bash w.sh": ["/x", "/y"], "echo -x 'cd /k' > w.sh; bash w.sh": [],
            "printf -- 'cd /dd\\n' > w.sh; bash w.sh": ["/dd"], "printf -v x 'cd /v' > w.sh; bash w.sh": [],
            "printf 'cd \\\"/qq\\\"\\n' > w.sh; bash w.sh": ["/qq"], "printf 'cd /%%\\n' > w.sh; bash w.sh": ["/%"],
            "printf '%b' 'cd /b1\\n' > w.sh; bash w.sh": ["/b1"], "printf 'cd /%d\\n' 007 > w.sh; bash w.sh": ["/7"],
            "printf '%s\\n' 'cd /r1' 'cd /r2' > w.sh; bash w.sh": ["/r1", "/r2"], "> w.sh; bash w.sh": [],
            "tee w.sh <<< 'cd /h' > v.sh; bash v.sh": ["/h"], "tee -- -a <<< 'cd /m'; bash ./-a": ["/m"],
            "echo 'cd /a' > w.sh; tee -a w.sh <<< 'cd /b' > /dev/null; bash w.sh": ["/a", "/b"],
            "echo 'cd /a' > w.sh; tee --append w.sh <<< 'cd /b' > /dev/null; bash w.sh": ["/a", "/b"],
            "cat > w.sh <<'EOF'\ncd /q$1\nEOF\nbash w.sh 2": ["/q2"], "cat > w.sh <<'EOF'\ncd '/\\$y'\nEOF\nbash w.sh": ["/\\$y"],
            "cat <<-EOF > w.sh\n\tcd '/t\n\tu'\n\tEOF\nbash w.sh": ["/t\nu"],
            "D=/u; cat <<-EOF > w.sh\n\tcd \"$D\tv\"\n\tEOF\nbash w.sh": ["/u\tv"],
            "echo \"cd '/a\" > w.sh; cat <<-EOF >> w.sh\n\tb'\n\tEOF\nbash w.sh": ["/a\nb"],
            "cat > w.sh <<EOF\ncd '/\\$x'\nEOF\nbash w.sh": ["/$x"], "./gen.sh '7 8'": ["/g7"],
            "tee w.sh <<< 'cd /h1' >/dev/null; echo 'cd /h2' >> w.sh; bash w.sh": ["/h1", "/h2"],
            "echo 'cd /more' >> run.sh; ./run.sh": ["/d", "/more"], "echo 'cd /new' >> w.sh; bash w.sh": ["/new"],
        }
        for text, folders in cases.items():
            with self.subTest(text=text):
                call = self.at(ShellText(text))
                self.assertEqual(([c.words[1].value for c in call.commands if c.name == "cd"], call.gaps),
                                 (folders, ()))

    def test_a_script_the_kit_cannot_read(self) -> None:
        missing, path = ShellText(model.SCRIPT_FIND_FORM), ShellText(model.SCRIPT_PATH_FORM)
        write, unread = ShellText(model.SCRIPT_WRITE_FORM), ShellText(model.SCRIPT_READ_FORM)
        foreign = ShellText(model.SCRIPT_FOREIGN_FORM)
        where = self.folder
        unknown = ShellText("the path of the script it runs is not known from the call")
        written = ShellText(f"the call writes the script {where}/w.sh with text it does not make known")
        cases = {
            "./missing.sh": [(ShellText(f"the script {where}/missing.sh {scripts.MISSING}"), missing)],
            "bash missing.sh; source gone.sh": [(ShellText(f"the script {where}/missing.sh {scripts.MISSING}"), missing),
                                                (ShellText(f"the script {where}/gone.sh {scripts.MISSING}"), missing)],
            "$X/run.sh": [(unknown, path)], 'bash "$X"; source $X': [(unknown, path)] * 2,
            "cd $X; ./run.sh": [(ShellText("the script ./run.sh is run from a directory the call does not make known"),
                                 path)],
            "cat > w.sh <<EOF\ncd $(pwd)\nEOF\nbash w.sh": [(written, write)],
            "sed s/a/b/ x > w.sh; bash w.sh": [(written, write)], "{ echo 'cd /d'; } > w.sh; ./w.sh": [(written, write)],
            "echo 'cd /d' 2> w.sh; ./w.sh": [(written, write)], "printf %q x > w.sh; ./w.sh": [(written, write)],
            "echo $X > w.sh; bash w.sh": [(written, write)], "cat x > w.sh; bash w.sh": [(written, write)],
            "printf > w.sh; bash w.sh": [(written, write)], "cat x <<'EOF' > w.sh\ncd /z\nEOF\nbash w.sh": [(written, write)], "echo -e 'cd /x\\q' > w.sh; bash w.sh": [(written, write)],
            "printf 'cd /x\\q' > w.sh; bash w.sh": [(written, write)],
            "printf 'cd /%d\\n' x > w.sh; bash w.sh": [(written, write)],
            "cat > w.sh <<EOF\ncd $UNSET\nEOF\nbash w.sh": [(written, write)],
            "cat <<'EOF' < other > w.sh\ncd /z\nEOF\nbash w.sh": [(written, write)],
            "echo 'cd /l' >> latin.sh; ./latin.sh": [
                (ShellText(f"the call writes the script {where}/latin.sh with text it does not make known"), write)],
            "cd $X; echo 'cd /q' > w.sh; bash /w.sh": [(ShellText(f"the script /w.sh {scripts.MISSING}"), missing)],
            "cd $X; PATH=bin; source true": [
                (ShellText("the script true is run from a directory the call does not make known"), path)],
            "./latin.sh": [(ShellText(f"the script {where}/latin.sh {scripts.NOT_TEXT}"), unread)],
            "./locked.sh": [(ShellText(f"the script {where}/locked.sh cannot be read: Permission denied"), unread)],
            "./z.sh": [(ShellText(f"it runs {where}/z.sh, a zsh script, which the kit does not read"), foreign)],
            "./f.fish": [(ShellText(f"it runs {where}/f.fish, a fish script, which the kit does not read"), foreign)],
        }
        for text, gaps in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.gaps(ShellText(text)), gaps)
        [(what, form)] = self.gaps(ShellText("source bad.sh"))
        self.assertRegex(what, rf"^in the script {where}/bad.sh, bash cannot parse it: .+ at line \d+, column \d+$")
        self.assertEqual(form, model.SCRIPT_PARSE_FORM)
        self.assertEqual([gap.text for gap in self.at(ShellText("cd /d; ./missing.sh x")).gaps], ["./missing.sh x"])

    def test_a_file_left_to_its_own_reader(self) -> None:
        for text in ("./prog", "./nul.dat", "./node.js", "run.sh", str(self.folder), "$CMD x"):
            with self.subTest(text=text):
                call = self.at(ShellText(text))
                self.assertEqual((len(call.commands), call.gaps), (1, ()))

    def test_the_limits(self) -> None:
        with self.assertRaises(Unreadable) as raised:
            _ = self.at(ShellText("./self.sh"))
        self.assertIn(f"carriers (bash -c, sh -c, eval), scripts and Python code more than {model.MAX_DEPTH} deep",
                      raised.exception.reason)
        with self.assertRaises(Unreadable) as raised:
            _ = self.at(ShellText("bash long.sh"))
        self.assertIn("with the text its carriers, scripts and Python code run", raised.exception.reason)
        with self.assertRaises(Unreadable) as raised:
            _ = self.at(ShellText("bash huge.sh"))
        self.assertEqual(raised.exception.form, model.TOO_LONG_FORM)
        with self.assertRaises(Unreadable) as raised:
            _ = self.at(ShellText("source selfsrc.sh"))
        self.assertIn(f"more than {model.MAX_DEPTH} deep", raised.exception.reason)


PYTHON_FILES = {ScriptName(name): ScriptBody(body) for name, body in {
    "shebang.py": b"#!/usr/bin/env python3\nimport subprocess\nsubprocess.run(['make', '-j1'])\n",
    "plain.py": b"import os\nos.system('make -j2')\n", "quiet.py": b"#!/usr/bin/env python3\nimport os\n",
    "broken.py": b"#!/usr/bin/python3\nprint(1\n", "pkg/__init__.py": b"",
    "pkg/mod.py": b"import subprocess\nsubprocess.call(['make', '-j3'])\n",
    "pkg/__main__.py": b"import subprocess\nsubprocess.call(['make', '-j4'])\n",
    "deep.py": b"import subprocess\nsubprocess.run(['python3', 'deep.py'])\n",
    "sleeper.py": b"#!/usr/bin/env python3\nimport time\nwhile True:\n    time.sleep(1)\n",
}.items()}
type Seen = tuple[Values, tuple[Shell, ...]]
type Slept = list[tuple[LineNumber, bool]]
N = LineNumber
T = ShellText
MAKE: Values = (T("make"), T("-j"))
SH_MAKE: Values = (T("/bin/sh"), T("-c"), T("make -j"))


class Python(unittest.TestCase):
    """The Python code a call runs is read for the processes it starts: each start whose program and arguments are
    literal a command of a python process of its own, one the code does not spell counted, code the kit cannot
    read a gap naming a form."""

    folder: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp()).resolve()
        for name, body in PYTHON_FILES.items():
            (cls.folder / name).parent.mkdir(exist_ok=True)
            _ = (cls.folder / name).write_bytes(body)
        (cls.folder / "stdin.lnk").symlink_to("/dev/stdin")
        (cls.folder / "fd3.lnk").symlink_to("/dev/fd/3")
        os.mkfifo(cls.folder / "fifo")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder)

    def at(self, text: ShellText) -> Call:
        """The call model of ``text`` run from the files' folder."""
        return read(text, Directory(str(self.folder)))

    def started(self, text: ShellText) -> list[Seen]:
        """The word values and shells of each command a Python process of ``text`` starts."""
        return [(tuple(w.value for w in c.words), c.shells) for c in self.at(text).commands if "python" in c.shells]

    def code(self, body: PythonText) -> ShellText:
        """A call running the Python code ``body`` from a quoted heredoc."""
        return ShellText(f"python3 - <<'EOF'\n{body}\nEOF")

    def make(self, body: PythonText) -> Command:
        """The make the Python code ``body`` starts."""
        return [command for command in self.at(self.code(body)).commands if command.name == "make"][-1]

    def gaps(self, text: ShellText) -> Gaps:
        """Each gap of ``text`` as its prose and its form."""
        return [(ShellText(gap.what), ShellText(gap.form)) for gap in self.at(text).gaps]

    def test_each_start_form(self) -> None:
        sub, o, run = "import subprocess\nsubprocess.", "import os\nos.", "import os\nos.posix_spawn"
        cases = {
            **{f"{sub}{name}(['make', '-j'])": [MAKE] for name in ("run", "call", "check_call", "check_output",
                                                                   "Popen")},
            f"{sub}run(('make', '-j'))": [MAKE], f"{sub}run(args=['make', '-j'])": [MAKE],
            f"{sub}run([f'make', '-j'])": [MAKE], f"{sub}run('make')": [(T("make"),)],
            f"{sub}run('make -j', shell=True)": [SH_MAKE, MAKE],
            f"{sub}run(['make -j', 'zero'], shell=1)": [(*SH_MAKE, T("zero")), MAKE],
            f"{sub}Popen('make -j', -1, None, None, None, None, None, True, True)": [SH_MAKE, MAKE],
            f"{sub}run(['x', '-j'], executable='make')": [MAKE],
            f"{sub}run('make -j', shell=True, executable='/bin/bash')": [(T("/bin/bash"), *SH_MAKE[1:]), MAKE],
            f"{sub}run(['make', '-j'], shell=False, executable=None)": [MAKE],
            f"{sub}getoutput('make -j')": [SH_MAKE, MAKE], f"{sub}getstatusoutput(cmd='make -j')": [SH_MAKE, MAKE],
            f"{o}system('make -j')": [SH_MAKE, MAKE], f"{o}system(command='make -j')": [SH_MAKE, MAKE],
            f"{o}popen('make -j')": [SH_MAKE, MAKE], f"{o}execl('/usr/bin/make', 'm', '-j')": [(T("/usr/bin/make"),
                                                                                                T("-j"))],
            **{f"{o}exec{s}('make', 'm', '-j'{e})": [MAKE] for s, e in (("lp", ""), ("le", ", {}"), ("lpe", ", {}"))},
            **{f"{o}exec{s}('make', ['m', '-j']{e})": [MAKE] for s, e in (("v", ""), ("vp", ""), ("ve", ", {}"),
                                                                           ("vpe", ", {}"))},
            **{f"{o}spawn{s}(os.P_WAIT, 'make', 'm', '-j'{e})": [MAKE] for s, e in (("l", ""), ("lp", ""),
                                                                                     ("le", ", {}"), ("lpe", ", {}"))},
            **{f"{o}spawn{s}(os.P_WAIT, 'make', ['m', '-j']{e})": [MAKE]
               for s, e in (("v", ""), ("vp", ""), ("ve", ", {}"), ("vpe", ", {}"))},
            f"{run}('make', ['m', '-j'], os.environ)": [MAKE], f"{run}p('make', ['m', '-j'], os.environ)": [MAKE],
            "import asyncio\nasyncio.create_subprocess_exec('make', '-j')": [MAKE],
            "import asyncio\nasyncio.create_subprocess_shell('make -j')": [SH_MAKE, MAKE],
            "import asyncio.subprocess\nasyncio.subprocess.create_subprocess_exec('make', '-j')": [MAKE],
            "import pty\npty.spawn(['make', '-j'])": [MAKE], "import pty\npty.spawn('make')": [(T("make"),)],
            "import pty\npty.spawn(argv='make')": [(T("make"),)],
        }
        for body, starts in cases.items():
            with self.subTest(body=body):
                self.assertEqual([values for values, _ in self.started(self.code(PythonText(body)))], starts)

    def test_each_alias_form(self) -> None:
        cases = {
            "import subprocess as sp\nsp.run(['make', '-j'])": [MAKE],
            "from subprocess import run\nrun(['make', '-j'])": [MAKE],
            "from subprocess import run as go\ngo(['make', '-j'])": [MAKE],
            "from subprocess import *\ncheck_call(['make', '-j'])": [MAKE],
            "from os import *\nsystem('make -j')": [SH_MAKE, MAKE],
            "import os.path\nos.system('make -j')": [SH_MAKE, MAKE],
            "import asyncio.subprocess as asp\nasp.create_subprocess_exec('make', '-j')": [MAKE],
            "from asyncio import subprocess as s\ns.create_subprocess_exec('make', '-j')": [MAKE],
            "from os import execvp as e\ne('make', ['m', '-j'])": [MAKE],
            "def f():\n    import subprocess\nsubprocess.run(['make', '-j'])": [MAKE],
            "subprocess.run(['make', '-j'])": [], "from .subprocess import run\nrun(['make', '-j'])": [],
            "import subprocess\nrun(['make', '-j'])": [], "import subprocess as sp\nsubprocess.run(['make', '-j'])": [],
            "from subprocess import *\nsystem('make -j')": [], "import mine\nmine.run(['make', '-j'])": [],
            "import subprocess\nsubprocess.run.x(['make', '-j'])": [], "import subprocess\n(f)(['make', '-j'])": [],
        }
        for body, starts in cases.items():
            with self.subTest(body=body):
                self.assertEqual([values for values, _ in self.started(self.code(PythonText(body)))], starts)

    def test_each_way_code_reaches_python(self) -> None:
        body = "import subprocess; subprocess.run(['make', '-j'])"
        for text in (f'python3 -c "{body}"', f'python3 -c"{body}"', f'python3 -IBc "{body}"',
                     f'python3 -W ignore -X dev -c "{body}"', f'python3 -Wignore -c "{body}"',
                     f'python3 --check-hash-based-pycs always -c "{body}"', f'python3 -u -c "{body}"',
                     f'.venv/bin/python -c "{body}"', f'/usr/bin/python3.14 -c "{body}"', f'python3.14t -c "{body}"',
                     f'python -c "{body}"', f'nohup python3 -c "{body}"', f"python3 - <<'EOF'\n{body}\nEOF",
                     f"python3 <<'EOF'\n{body}\nEOF", f"python3 - x <<'EOF'\n{body}\nEOF", f'python3 <<< "{body}"',
                     f"python3 -- <<'EOF'\n{body}\nEOF", f'python3 -Xfrozen_modules=off -c "{body}"',
                     "M=make; python3 <<EOF\nimport subprocess; subprocess.run(['$M', '-j'])\nEOF"):
            with self.subTest(text=text):
                call = self.at(ShellText(text))
                self.assertEqual(([(c.name, c.shells) for c in call.commands if c.shells], call.gaps),
                                 ([("make", ("python",))], ()))
        one, two = (T("make"), T("-j1")), (T("make"), T("-j2"))
        files = {
            "python3 shebang.py": [(one, ("python",))], "./shebang.py": [(one, ("python",))],
            f"{self.folder}/shebang.py": [(one, ("python",))], "python3 -- shebang.py": [(one, ("python",))],
            "python3 plain.py": [((T("/bin/sh"), T("-c"), T("make -j2")), ("python",)), (two, ("python", "carrier"))],
            "python3 -m pkg.mod": [((T("make"), T("-j3")), ("python",))],
            "python3 -mpkg.mod": [((T("make"), T("-j3")), ("python",))],
            "python3 -m pkg": [((T("make"), T("-j4")), ("python",))],
            "cat > w.py <<'EOF'\nimport os\nos.execvp('make', ['make', '-j5'])\nEOF\npython3 w.py":
                [((T("make"), T("-j5")), ("python",))],
            "echo 'import pty; pty.spawn(\"make\")' > w.py; python3 w.py": [((T("make"),), ("python",))],
            "mkdir m; echo 'import pty; pty.spawn(\"make\")' > m/r.py; python3 -m m.r": [((T("make"),), ("python",))],
            "bash -c 'python3 shebang.py'": [(one, ("carrier", "python"))],
            self.code(PythonText("import subprocess\nsubprocess.run(['python3', 'shebang.py'])")):
                [((T("python3"), T("shebang.py")), ("python",)), (one, ("python", "python"))],
            "python3 quiet.py": [], "python3 -m json.tool": [], "python3 -m pkg.missing": [], "python3 -m": [],
            "python3 -c": [], "python3 -V -c 'import os; os.system(\"make\")'": [], "python3 --version": [],
            "python3 --help-env -c 'import os; os.system(\"make\")'": [], "python3 -m pkg/mod": [],
            "python3 -m .pkg": [],
            "python3 -m pkg.": [], "python3 -I -m pkg.mod": [], "python3 -P -m pkg": [], "python3 -Impkg": [],
            "python3 -Im pkg": [],
            "echo 'import pty; pty.spawn(\"make\")' > 'x-y.py'; python3 -m x-y": [((T("make"),), ("python",))],
            "python3 -I shebang.py": [(one, ("python",))],
        }
        for text, started in files.items():
            with self.subTest(text=text):
                self.assertEqual((self.started(ShellText(text)), self.at(ShellText(text)).gaps), (started, ()))

    def test_the_process_of_a_start(self) -> None:
        twice = self.code(PythonText("import subprocess\nsubprocess.run(['make'])\nsubprocess.run(['ninja'])"))
        call = self.at(ShellText(twice + "\n" + self.code(PythonText("import pty; pty.spawn('make')"))))
        first, second, third = [command for command in call.commands if command.shells]
        self.assertEqual(first.processes, second.processes)
        self.assertNotEqual(first.processes, third.processes)
        self.assertEqual((first.text, first.function), ("subprocess.run(['make'])", None))
        nested = PythonText("import subprocess\nprint(subprocess.run(['make']))\nsubprocess.run(['ninja'])")
        self.assertEqual([seen[0][0] for seen in self.started(self.code(nested))], ["make", "ninja"])
        body = PythonText("import subprocess\nsubprocess.run(['timeout', '9', 'env', 'A=1', 'make'])")
        [got] = [got for got in peels(lambda: self.at(self.code(body))) if got.name == "make"]
        self.assertEqual((self.make(body).wrappers, got.environ.assigns),
                         (("timeout", "env"), (Assignment(VarName("A"), T("1")),)))
        for wrapper in ("command", "exec", "builtin"):
            with self.subTest(wrapper=wrapper):
                body = PythonText(f"import subprocess\nsubprocess.run(['{wrapper}', 'make'])")
                [started] = self.started(self.code(body))
                self.assertEqual(started[0], (T(wrapper), T("make")))
        self.assertEqual(self.at(ShellText("f() { python3 plain.py; }")).commands[-1].function, "f")

    def test_the_directory_of_a_start(self) -> None:
        sub, folder = "import subprocess, os\nsubprocess.", str(self.folder)
        cases = {f"{sub}run(['make'], cwd='sub')": f"{folder}/sub", f"{sub}run(['make'], cwd='/abs/x/..')": "/abs",
                 f"{sub}run(['make'], cwd=None)": folder, f"{sub}run(['make'])": folder,
                 f"{sub}run(['make'], cwd=where)": None, f"{sub}run(['make'], cwd=b'/b')": None,
                 f"os.chdir('/x')\n{sub}run(['make'])": None, f"os.fchdir(3)\n{sub}run(['make'])": None,
                 f"os.chdir('/x')\n{sub}run(['make'], cwd='/y')": "/y",
                 f"{sub}Popen(['make'], -1, None, None, None, None, None, True, False, '/pos')": "/pos",
                 "import asyncio\nasyncio.create_subprocess_exec('make', cwd='/a')": "/a",
                 "import os\nos.execvp('make', ['make'])\nos.chdir('/x')": None}
        for body, cwd in cases.items():
            with self.subTest(body=body):
                self.assertEqual(self.make(PythonText(body)).cwd, cwd)
        run = "import subprocess\nsubprocess.run(['make']"
        for text, cwd in {"cd $U; " + self.code(PythonText(f"{run}, cwd='sub')")): None,
                          "cd $U; " + self.code(PythonText(f"{run}, cwd='/abs')")): "/abs",
                          "cd /d; " + self.code(PythonText(f"{run})")): "/d"}.items():
            with self.subTest(text=text):
                self.assertEqual([c.cwd for c in self.at(ShellText(text)).commands if c.name == "make"], [cwd])

    def test_the_environment_of_a_start(self) -> None:
        a1, a_none = Assignment(VarName("A"), T("1")), Assignment(VarName("A"), None)
        sub = "import subprocess, os\nsubprocess.run(['make']"
        cases = {
            f"{sub})": ((), (), False), f"{sub}, env={{'A': '1'}})": ((a1,), (), True),
            f"{sub}, env={{'A': x}})": ((a_none,), (), True),
            f"{sub}, env={{**os.environ, 'A': '1'}})": ((a1,), (), False),
            f"{sub}, env={{'A': '1', **os.environ}})": ((a_none,), (), False),
            f"{sub}, env=dict(os.environ, A='1'))": ((a1,), (), False),
            f"{sub}, env=os.environ.copy())": ((), (), False),
            f"{sub}, env=os.environ)": ((), (), False), f"{sub}, env=None)": ((), (), False),
            f"{sub}, env=mine)": ((), (None,), False), f"{sub}, env={{k: '1'}})": ((), (None,), False),
            f"{sub}, env={{**mine}})": ((), (None,), False),
            f"{sub}, env=dict(os.environ, **extra))": ((), (None,), False),
            f"{sub}, env=dict(mine))": ((), (None,), False), f"{sub}, env=os.environ.copy(1))": ((), (None,), False),
            f"{sub}, env=os.environ.keys())": ((), (None,), False),
            f"os.environ['X'] = '1'\n{sub})": ((), (None,), False),
            f"os.putenv('X', '1')\n{sub})": ((), (None,), False), f"os.unsetenv('X')\n{sub})": ((), (None,), False),
            f"os.environ.update(X='1')\n{sub})": ((), (None,), False),
            f"del os.environ['X']\n{sub})": ((), (None,), False),
            f"os.environ['X'] = '1'\n{sub}, env={{'A': '1'}})": ((a1,), (), True),
            f"os.environ['X'] = '1'\n{sub}, env={{**os.environ, 'A': '1'}})": ((a1,), (None,), False),
            f"x = os.environ['X']\n{sub})": ((), (), False), f"os.environ.get('X')\n{sub})": ((), (), False),
            f"d = {{}}\nd['X'] = '1'\n{sub})": ((), (), False),
            "import os\nos.execle('make', 'm', {'A': '1'})": ((a1,), (), True),
            "import os\nos.posix_spawn('make', ['m'], {'A': '1'})": ((a1,), (), True),
            f"{sub}, -1, None, None, None, None, None, True, False, None, {{'A': '1'}})": ((a1,), (), True),
        }
        for body, (assigns, unsets, cleared) in cases.items():
            with self.subTest(body=body):
                [got] = [got for got in peels(lambda: self.at(self.code(PythonText(body)))) if got.name == "make"]
                self.assertEqual(got.environ, (assigns, unsets, cleared))
        replaced = python.starts(PythonText(f"os.environ['X'] = '1'\n{sub}, env={{'A': '1'}})"))
        self.assertEqual(replaced.starts[0].env, python.Env(((VarName("A"), T("1")),), True))
        cpu, cpu_none = Assignment(VarName("N"), T("5")), Assignment(VarName("N"), None)
        inherited = {f"{sub})": ((cpu,), ()), f"os.environ['X'] = '1'\n{sub})": ((cpu_none,), (None,)),
                     f"{sub}, env={{**os.environ, 'A': '1'}})": ((cpu, a1), ())}
        for body, (assigns, unsets) in inherited.items():
            with self.subTest(body=body):
                [got] = [got for got in peels(lambda: self.at(ShellText("N=5 " + self.code(PythonText(body)))))
                         if got.name == "make"]
                self.assertEqual((got.environ.assigns, got.environ.unsets), (assigns, unsets))

    def test_an_unspelled_start_is_counted(self) -> None:
        for body in ("subprocess.run(cmd)", "subprocess.run(['make', *args])", "subprocess.run(['make', x])",
                     "subprocess.run(f'make {x}', shell=True)", "subprocess.run(['make'], shell=flag)",
                     "subprocess.run(['make'], shell='yes')", "subprocess.run(['make'], executable=exe)",
                     "subprocess.run(['make'], **options)", "subprocess.run([])", "subprocess.run()",
                     "subprocess.run(b'make')", "os.system(cmd)", "os.system()", "os.execv(path, ['m'])",
                     "os.execl('make', x)", "os.execv('make', args)", "os.execl()", "os.posix_spawn('make', argv, {})",
                     "asyncio.create_subprocess_exec(prog)", "asyncio.create_subprocess_exec()", "pty.spawn(argv)",
                     "pty.spawn([])", "os.execle('make', 'm', *rest)"):
            with self.subTest(body=body):
                call = self.at(self.code(PythonText(f"import subprocess, os, asyncio, pty\n{body}")))
                self.assertEqual((call.unspelled, len(call.commands), call.gaps), (1, 1, ()))
        code = PythonText("import subprocess, os\nsubprocess.run(['make'])\nsubprocess.run(c)\nos.system(c)")
        call = self.at(self.code(code))
        self.assertEqual((call.unspelled, [c.name for c in call.commands]), (2, ["python3", "make"]))
        unspelled = "python3 -c 'import os; os.system(c)'"
        self.assertEqual(self.at(ShellText(f"{unspelled}; {unspelled}")).unspelled, 2)
        self.assertEqual(self.at(ShellText(f"for i in 1 2; do X=$i; {unspelled}; done")).unspelled, 1)
        self.assertEqual(self.at(ShellText("python3 -c 'print(1)'")).unspelled, 0)

    def test_the_code_read_is_kept(self) -> None:
        """Call.python holds each Python text the model read, in reading order, and whether the call carries it (spells
        or writes it) rather than finding it on disk: code it cannot parse is not kept, nor a loop's first pass where
        the loop is judged again."""
        start = "import subprocess\nsubprocess.run(['python3', '-c', 'print(5)'])"
        for case, (text, kept) in {
            "-c": (ShellText("python3 -c 'print(1)'"), [("print(1)", True)]),
            "heredoc": (self.code(PythonText("print(2)")), [("print(2)\n", True)]),
            "script on disk": (ShellText("python3 plain.py"), [("import os\nos.system('make -j2')\n", False)]),
            "module": (ShellText("python3 -m pkg"), [("import subprocess\nsubprocess.call(['make', '-j4'])\n", False)]),
            "script by its path": (ShellText("./quiet.py"), [("#!/usr/bin/env python3\nimport os\n", False)]),
            "written script": (ShellText("cat > w.py <<'EOF'\nprint(3)\nEOF\npython3 w.py"), [("print(3)\n", True)]),
            "written by printf": (ShellText("printf 'print(4)\\n' > plain.py; python3 plain.py"),
                                         [("print(4)\n", True)]),
            "in order": (ShellText("python3 -c 'print(1)'; python3 plain.py"),
                         [("print(1)", True), ("import os\nos.system('make -j2')\n", False)]),
            "a start's code": (ShellText(f'python3 -c "{start}"'), [(start, True), ("print(5)", True)]),
            "unparsed": (ShellText("python3 broken.py"), []),
            "a loop judged again": (ShellText("for i in 1 2; do X=$i; python3 -c 'print(1)'; done"),
                                    [("print(1)", True)]),
        }.items():
            with self.subTest(case=case):
                self.assertEqual(list(self.at(text).python), kept)

    def test_code_the_kit_cannot_read(self) -> None:
        where, py_form = self.folder, model.PYTHON_PARSE_FORM
        unknown_input = (r"the code python3 reads on its input is not known from the call", model.GAP_FORM)
        zsh = self.code(PythonText('import pty\npty.spawn("zsh")'))
        cases = {
            "python3 -c 'print(1'": [(r"Python cannot parse the code python3 -c runs: .+ \(line 1\)", py_form)],
            self.code(PythonText("x = 1\nprint(1")): [(r"Python cannot parse the code python3 reads on its input: .+ "
                                                       r"\(line 2\)", py_form)],
            "python3 broken.py": [(rf"Python cannot parse the code of the script {where}/broken.py: .+", py_form)],
            "./broken.py": [(rf"Python cannot parse the code of the script {where}/broken.py: .+", py_form)],
            "python3 -c 'a = \"\\0\"' x": [],
            "python3 -c \"$U\"": [(r"the code python3 -c runs is not known from the call", model.GAP_FORM)],
            "python3 <<EOF\n$U\nEOF": [unknown_input], "python3 <<< \"$U\"": [unknown_input],
            "python3 missing.py": [(rf"the script {where}/missing.py is neither on disk nor written by the call",
                                    model.SCRIPT_FIND_FORM)],
            "python3 $U": [(r"the path of the script it runs is not known from the call", model.SCRIPT_PATH_FORM)],
            f"cd /; PATH={where}; python3 shebang.py": [(r"the script /shebang.py is neither on disk nor written by "
                                                          r"the call", model.SCRIPT_FIND_FORM)],
            "python3 -u -- -c": [(rf"the script {where}/-c is neither on disk nor written by the call",
                                  model.SCRIPT_FIND_FORM)],
            zsh: [(r"it runs zsh, which the kit does not read", model.FOREIGN_FORM)],
            self.code(PythonText("import subprocess\nsubprocess.run(['bash', '-c', 'cd $U'])")): [],
        }
        for text, gaps in cases.items():
            with self.subTest(text=text):
                got = self.gaps(ShellText(text))
                self.assertEqual(len(got), len(gaps), got)
                for (what, form), (pattern, wanted) in zip(got, gaps, strict=True):
                    self.assertRegex(what, f"^{pattern}$")
                    self.assertEqual(form, wanted)
        [gap] = self.at(zsh).gaps
        self.assertEqual(gap.text, 'pty.spawn("zsh")')
        [(what, _)] = self.gaps(ShellText("python3 -c 'print(1'"))
        self.assertEqual(what, "Python cannot parse the code python3 -c runs: '(' was never closed (line 1)")
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self.assertEqual(self.started(ShellText("python3 -c 'import os; os.system(\"\\( make\")'")),
                             [((T("/bin/sh"), T("-c"), T("\\( make")), ("python",)), ((T("("), T("make")),
                                                                                 ("python", "carrier"))])
        self.assertEqual(seen, [])
        with self.assertRaises(Unreadable) as raised:
            _ = self.at(ShellText("python3 deep.py"))
        self.assertEqual(raised.exception.form, model.TOO_DEEP_FORM)

    def test_a_file_on_pythons_input_is_read_as_its_script(self) -> None:
        plain = [((T("/bin/sh"), T("-c"), T("make -j2")), ("python",)), ((T("make"), T("-j2")), ("python", "carrier"))]
        for text in ("python3 < plain.py", "python3 - < plain.py", "python3 0< plain.py", "python3 -u <> plain.py",
                     f"python3 < {self.folder}/plain.py", "cd pkg; python3 < ../plain.py", "python3 <<< x < plain.py",
                     "python3 3< quiet.py < plain.py", "python3 plain.py <&3"):
            with self.subTest(text=text):
                self.assertEqual((self.started(ShellText(text)), self.at(ShellText(text)).gaps), (plain, ()))
        written = "echo 'import pty; pty.spawn(\"make\")' > w.py; python3 < w.py"
        self.assertEqual(self.started(ShellText(written)), [((T("make"),), ("python",))])

    def test_a_script_named_by_a_descriptor_path(self) -> None:
        cases = {
            "python3 /dev/stdin < plain.py": "/dev/stdin", "python3 /dev/fd/3 3< plain.py": "/dev/fd/3",
            "python3 /proc/self/fd/0 < plain.py": "/proc/self/fd/0", "python3 3< plain.py < /dev/fd/3": "/dev/fd/3",
            "python3 3< plain.py < /proc/self/fd/3 3< quiet.py": "/proc/self/fd/3",
            "python3 /dev/stdin <<'EOF'\nimport os\nos.system('make -j2')\nEOF": "/dev/stdin",
            "python3 /dev/stdin < /dev/null": "/dev/stdin", "echo 'import os' | python3 /dev/stdin": "/dev/stdin",
            "python3 /dev/stdin": "/dev/stdin", "python3 /dev/fd/3": "/dev/fd/3",
            "python3 < /proc/self/fd/3 3< plain.py": "/proc/self/fd/3",
            "echo 'import os' | python3 < /dev/stdin": "/dev/stdin", "python3 < /dev/stdin": "/dev/stdin",
            "python3 < /dev/fd/3": "/dev/fd/3", "echo x | python3 3<&0 < /dev/fd/3": "/dev/fd/3",
        }
        for text, path in cases.items():
            with self.subTest(text=text):
                what = f"the script {path} names a descriptor, which the kit does not read"
                self.assertEqual((self.started(ShellText(text)), self.gaps(ShellText(text))),
                                 ([], [(what, model.DESCRIPTOR_FORM)]))

    def test_a_script_linked_to_a_descriptor_path_or_no_file(self) -> None:
        for text, name in {"python3 stdin.lnk < plain.py": "stdin.lnk", "python3 3< plain.py < fd3.lnk": "fd3.lnk",
                           "python3 fd3.lnk 3< plain.py": "fd3.lnk"}.items():
            with self.subTest(text=text):
                what = f"the script {self.folder}/{name} names a descriptor, which the kit does not read"
                self.assertEqual((self.started(ShellText(text)), self.gaps(ShellText(text))),
                                 ([], [(what, model.DESCRIPTOR_FORM)]))
        fifo = (f"the script {self.folder}/fifo is a FIFO, which the kit does not open", model.SCRIPT_READ_FORM)
        for text in ("python3 fifo", "python3 < fifo"):
            with self.subTest(text=text):
                self.assertEqual(self.gaps(ShellText(text)), [fifo])

    def test_input_the_call_does_not_make_known(self) -> None:
        where = self.folder
        unknown = (r"the code python3 reads on its input is not known from the call", model.GAP_FORM)
        cases = {
            "python3": [unknown], "echo x | python3": [unknown], "cat plain.py | python3": [unknown],
            "python3 -": [unknown], "python3 -u --": [unknown], "python3 3< plain.py": [unknown],
            "python3 < plain.py 0<&3": [unknown], "python3 <&3": [unknown],
            "python3 < plain.py <&3": [unknown], "python3 < plain.py 0>&-": [unknown],
            "python3 3< quiet.py < plain.py 0<&3": [unknown], "python3 < plain.py 0> out.txt": [unknown],
            "python3 3<<'EOF'\nimport os\nEOF": [unknown],
            "python3 < /dev/null": [],
            self.code(PythonText("import subprocess\nsubprocess.run('python3 < /dev/stdin', shell=True, input=b'')")):
                [(r"the script /dev/stdin names a descriptor, which the kit does not read", model.DESCRIPTOR_FORM)],
            self.code(PythonText("import subprocess\nsubprocess.run(['python3'], input=b'')")):
                [(r"the code python3 reads on its input is not known from the call", model.GAP_FORM)],
            "python3 < missing.py": [(rf"the script {where}/missing.py is neither on disk nor written by the call",
                                      model.SCRIPT_FIND_FORM)],
            "python3 < $U": [(r"the path of the script it runs is not known from the call", model.SCRIPT_PATH_FORM)],
            "cd $U; python3 < plain.py": [(r"the script plain.py is run from a directory the call does not make known",
                                           model.SCRIPT_PATH_FORM)],
            "python3 < broken.py": [(rf"Python cannot parse the code of the script {where}/broken.py: .+",
                                     model.PYTHON_PARSE_FORM)],
            "echo x | python3 -c 'import os'": [], "python3 quiet.py < $U": [],
        }
        for text, gaps in cases.items():
            with self.subTest(text=text):
                got = self.gaps(ShellText(text))
                self.assertEqual(len(got), len(gaps), got)
                for (what, form), (pattern, wanted) in zip(got, gaps, strict=True):
                    self.assertRegex(what, f"^{pattern}$")
                    self.assertEqual(form, wanted)

    def test_a_module_the_call_does_not_make_known(self) -> None:
        form = model.MODULE_FORM
        cases = {
            "python3 -m $M": [(r"the module python3 -m runs is not known from the call", form)],
            "cd $U; python3 -m pkg.mod": [(r"the module pkg.mod is run from a directory the call does not make known",
                                           form)],
            "cd $U; python3 -m json.tool": [(r"the module json.tool is run from a directory the call does not make "
                                             r"known", form)],
            "python3 -I -m $M": [], "cd $U; python3 -I -m pkg.mod": [], "cd $U; python3 -Pm json.tool": [],
            "cd $U; python3 -m pkg/mod": [], "python3 -m json.tool": [], "python3 -m pkg.missing": [],
        }
        for text, gaps in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.gaps(ShellText(text)), [(T(what), T(wanted)) for what, wanted in gaps])
        self.assertEqual(self.started(ShellText("python3 -m pkg.mod")), [((T("make"), T("-j3")), ("python",))])

    def slept(self, text: ShellText) -> list[Slept]:
        """Each command of ``text`` as the line and the spin of each loop of its Python code that sleeps."""
        return [[(loop.line, loop.spins) for loop in command.sleeps] for command in self.at(text).commands]

    def test_each_loop_that_sleeps(self) -> None:
        cases: dict[PythonText, Slept] = {
            "import time\nwhile True:\n    time.sleep(1)": [(N(2), True)],
            "import time as t\nfor i in range(3):\n    t.sleep(1)": [(N(2), True)],
            "from time import sleep\nwhile 1:\n    sleep(1)": [(N(2), True)],
            "from time import sleep as nap\nwhile 1:\n    nap(1)": [(N(2), True)],
            "from time import *\nwhile 1:\n    sleep(1)": [(N(2), True)],
            "import asyncio\nasync def f():\n    async for x in g():\n        await asyncio.sleep(1)": [(N(3), True)],
            "from asyncio import *\nasync def f():\n    while 1:\n        await sleep(1)": [(N(3), True)],
            "import time\nfor x in []:\n    pass\nelse:\n    print(x)\n    time.sleep(1)": [(N(2), True)],
            "import time\nwhile 1:\n    def f():\n        time.sleep(1)": [(N(2), False)],
            "import time\ndef f():\n    while 1:\n        time.sleep(1)\nwhile 1:\n    for i in range(2):\n        time.sleep(2)":
                [(N(3), True), (N(5), False), (N(6), True)],
            "import time\nwhile time.sleep(1) or True:\n    pass": [], "import time\nfor x in time.sleep(1):\n    pass": [],
            "import time\nwhile 1:\n    time.time()": [], "while 1:\n    sleep(1)": [],
            "import mine as time\nwhile 1:\n    time.sleep(1)": [], "import time\n[time.sleep(1) for _ in range(3)]": [],
        }
        for body, sleeps in cases.items():
            with self.subTest(body=body):
                self.assertEqual(self.slept(self.code(PythonText(body))), [sleeps])

    def test_what_only_sleeps_and_checks(self) -> None:
        cases = {
            "if os.path.exists('f') or time.time() > 5:\n   break\n  elif n < 2:\n   continue\n  else:\n   pass": True,
            "now = time.monotonic()\n  n: int = 0\n  n += 1\n  n < 2": True, "print(n)": False,
            "if ready():\n   break": False, "if n:\n   print(n)": False, "if n:\n   pass\n  else:\n   print(n)": False,
            "yield n": False, "with n:\n   pass": False,
        }
        for body, spins in cases.items():
            with self.subTest(body=body):
                code = PythonText(f"import os, time\ndef g(n):\n while True:\n  {body}\n  time.sleep(1)")
                self.assertEqual(self.slept(self.code(code)), [[(N(3), spins)]])

    def test_the_interpreter_of_each_python_text_carries_its_loops(self) -> None:
        loop = "import time\nwhile True:\n    time.sleep(1)"
        inner = "import subprocess\nsubprocess.run(['python3', '-c', 'import time\\nwhile 1: time.sleep(1)'])"
        texts: dict[ShellText, list[Slept]] = {
            f"python3 -c '{loop}'": [[(N(2), True)]], f"nohup python3 -c '{loop}'": [[], [(N(2), True)]],
            f"sleep 1; python3 - <<'EOF'\n{loop}\nEOF": [[], [(N(2), True)]], "./sleeper.py": [[(N(3), True)]],
            f"cat > s.py <<'EOF'\n{loop}\nEOF\npython3 s.py": [[], [(N(2), True)]],
            f"mkdir m; cat > m/w.py <<'EOF'\n{loop}\nEOF\npython3 -m m.w": [[], [], [(N(2), True)]],
            self.code(PythonText(inner)): [[], [(N(2), True)]], "python3 -c 'while True: pass'": [[]],
        }
        for text, sleeps in texts.items():
            with self.subTest(text=text):
                self.assertEqual(self.slept(ShellText(text)), sleeps)

    def paused(self, text: ShellText) -> list[bool]:
        """Whether the Python code of each command of ``text`` sleeps outside its loops that sleep."""
        return [command.pauses for command in self.at(text).commands]

    def test_a_sleep_outside_the_loops_that_sleep(self) -> None:
        cases: dict[PythonText, bool] = {
            "import time\ntime.sleep(1)": True, "import asyncio\nasyncio.run(asyncio.sleep(1))": True,
            "import time\ndef f():\n    time.sleep(1)\nwhile 1:\n    f()": True,
            "import time\nwhile time.sleep(1) or True:\n    pass": True,
            "import time\nwhile 1:\n    time.sleep(1)\ntime.sleep(2)": True,
            "import time\n[time.sleep(1) for _ in range(3)]": True,
            "import time\nwhile 1:\n    time.sleep(1)": False, "import time\nfor i in range(2):\n    time.sleep(1)": False,
            "import asyncio\nasync def f():\n    async for x in g():\n        await asyncio.sleep(1)": False,
            "import time\nfor x in []:\n    pass\nelse:\n    time.sleep(1)": False,
            "import time\ntime.time()": False, "import mine as time\ntime.sleep(1)": False,
        }
        for body, pauses in cases.items():
            with self.subTest(body=body):
                self.assertEqual(self.paused(self.code(PythonText(body))), [pauses])

    def test_the_interpreter_carries_whether_its_code_sleeps_outside_them(self) -> None:
        nap = "import time; time.sleep(1)"
        inner = f"import subprocess\nsubprocess.run(['python3', '-c', '{nap}'])"
        texts: dict[ShellText, list[bool]] = {
            f"python3 -c '{nap}'": [True], f"nohup python3 -c '{nap}'": [False, True],
            self.code(PythonText(inner)): [False, True], "sleep 1": [False],
        }
        for text, pauses in texts.items():
            with self.subTest(text=text):
                self.assertEqual(self.paused(ShellText(text)), pauses)

    def test_too_many_starts(self) -> None:
        kept = model.MAX_COMMANDS
        model.MAX_COMMANDS = Count(3)
        try:
            _ = self.at(ShellText("python3 -c 'import pty; pty.spawn(\"a\"); pty.spawn(\"b\")'"))
            with self.assertRaises(Unreadable):
                _ = self.at(ShellText("python3 -c 'import pty; pty.spawn(\"a\"); pty.spawn(\"b\"); pty.spawn(\"c\")'"))
        finally:
            model.MAX_COMMANDS = kept


INHERITING = {ScriptName(name): ScriptBody(body) for name, body in {
    "cmd.sh": b"cmd\n", "nest.sh": b"B=2 bash -c cmd\n", "over.sh": b"A=2\ncmd\n", "param.sh": b'make -j"$A"\n',
}.items()}
OS_SYSTEM = "python3 -c \"import os; os.system('cmd')\""
RUN = "python3 -c \"import os, subprocess; subprocess.run({argv}{env})\""
A1 = ((T("A"), T("1")),)


class Inheritance(unittest.TestCase):
    """The environment a command gives the script it runs, a carrier or a Python interpreter is the environment every
    command inside starts with, before that command's own: prefixes, an env wrapper's assignments, emptying and
    unsets, a Python start's env=, a name the inner shell sets taking the value it sets."""

    folder: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp()).resolve()
        for name, body in INHERITING.items():
            _ = (cls.folder / name).write_bytes(body)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder)

    def at(self, text: ShellText) -> Call:
        """The call model of ``text`` run from the scripts' folder."""
        return read(text, Directory(str(self.folder)))

    def environment(self, text: ShellText) -> Environ:
        """What the call does to the environment the last cmd ``text`` runs starts with."""
        return [got.environ for got in peels(lambda: self.at(text)) if got.name == "cmd"][-1]

    def test_a_prefix_or_an_env_wrapper_reaches_every_command_inside(self) -> None:
        argv = RUN.format(argv="['bash', 'cmd.sh']", env="")
        cases = {
            "A=1 bash cmd.sh": (A1, (), False), "A=1 ./cmd.sh": (A1, (), False), "A=1 bash -c cmd": (A1, (), False),
            "A=1 sh -c 'cd /x; cmd'": (A1, (), False), f"A=1 {OS_SYSTEM}": (A1, (), False),
            f"A=1 {argv}": (A1, (), False), "env A=1 bash cmd.sh": (A1, (), False),
            "env A=1 ./cmd.sh": (A1, (), False), "env A=1 bash -c cmd": (A1, (), False),
            f"env A=1 {OS_SYSTEM}": (A1, (), False), f"env A=1 {argv}": (A1, (), False),
            "A=1 bash -c '(cmd) | cat'": (A1, (), False), "A=1 bash -c 'echo $(cmd)'": (A1, (), False),
            "A=1 taskset -c 0 env B=2 bash -c cmd": ((("A", "1"), ("B", "2")), (), False),
            "A=1 bash -c x; cmd": ((), (), False), "A=1 bash -c x; bash -c cmd": ((), (), False),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.environment(ShellText(text)), expected)

    def test_nesting_two_deep(self) -> None:
        both = (("A", "1"), ("B", "2"))
        python_c = 'python3 -c "import subprocess; subprocess.run([\\"bash\\", \\"-c\\", \\"cmd\\"])"'
        cases = {
            "A=1 bash -c \"B=2 bash -c 'cmd'\"": (both, (), False), "A=1 bash -c 'B=2 bash cmd.sh'": (both, (), False),
            "A=1 bash nest.sh": (both, (), False), "env A=1 bash -c 'env B=2 ./cmd.sh'": (both, (), False),
            "A=1 " + RUN.format(argv="['bash', 'nest.sh']", env=""): (both, (), False),
            f"A=1 bash -c 'B=2 {python_c}'": (both, (), False),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.environment(ShellText(text)), expected)

    def test_an_inner_override(self) -> None:
        a2, a_none = (("A", "2"),), (("A", None),)
        cases = {
            "A=1 bash -c 'A=2 cmd'": ((*A1, *a2), (), False), "A=1 bash -c 'env A=2 cmd'": ((*A1, *a2), (), False),
            "A=1 bash -c 'A=2 bash -c cmd'": ((*A1, *a2), (), False), "A=1 bash -c 'A=2; cmd'": (a2, (), False),
            "A=1 bash -c 'export A=2; cmd'": (a2, (), False), "A=1 bash over.sh": (a2, (), False),
            "A=1 bash -c 'A=2; bash -c cmd'": (a2, (), False), "A=1 bash -c 'A=$U; cmd'": (a_none, (), False),
            "A=1 bash -c 'if x; then A=2; fi; cmd'": (a_none, (), False), "A=1 bash -c 'B=2; cmd'": (A1, (), False),
            "A=1 bash -c '(A=2); cmd'": (A1, (), False), "A=1 bash -c 'A=2 x; cmd'": (A1, (), False),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.environment(ShellText(text)), expected)

    def test_an_inner_emptying_or_unset(self) -> None:
        cases = {
            "A=1 bash -c 'env -i B=2 cmd'": ((("B", "2"),), (), True), "A=1 bash -c 'env -i cmd'": ((), (), True),
            "A=1 bash -c 'env -u A cmd'": ((), ("A",), False), "A=1 bash -c 'env -u C cmd'": (A1, ("C",), False),
            "A=1 bash -c 'env -u \"$U\" cmd'": ((("A", None),), (None,), False),
            "env -i A=1 bash -c 'B=2 cmd'": ((("A", "1"), ("B", "2")), (), True),
            "env -u A bash -c 'env -u C cmd'": ((), ("A", "C"), False),
            "env -i bash -c 'env -u A cmd'": ((), (), True), "env -u A bash cmd.sh": ((), ("A",), False),
            f"env -i {OS_SYSTEM}": ((), (), True), "env -i A=1 ./cmd.sh": (A1, (), True),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.environment(ShellText(text)), expected)

    def test_a_python_starts_environment(self) -> None:
        bash_c = "['bash', '-c', 'cmd']"
        cases = {
            RUN.format(argv="'cmd'", env=", shell=True, env={'A': '1'}"): (A1, (), True),
            RUN.format(argv="['bash', 'cmd.sh']", env=", env={'A': '1'}"): (A1, (), True),
            "B=2 " + RUN.format(argv=bash_c, env=", env={**os.environ, 'A': '1'}"): ((("B", "2"), *A1), (), False),
            "B=2 " + RUN.format(argv=bash_c, env=", env=mine"): ((("B", None),), (None,), False),
            "B=2 " + RUN.format(argv=bash_c, env=", env={'A': '1'}"): (A1, (), True),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.environment(ShellText(text)), expected)

    def test_a_scripts_parameters(self) -> None:
        cases = {"A=1 bash -c 'bash param.sh'": "-j1", "A=1 bash -c 'A=2; bash param.sh'": "-j2",
                 "env -i A=1 bash -c './param.sh'": "-j1", "A=1 bash -c 'A=2 bash param.sh'": "-j2"}
        for text, value in cases.items():
            with self.subTest(text=text):
                make = [c for c in self.at(ShellText(text)).commands if c.name == "make"][-1]
                self.assertEqual(make.words[1].value, value)


EndText = NewType("EndText", str)
type Ends = tuple[EndText, EndText, EndText]
C = "call"


def spelled_end(end: End) -> EndText:
    """An end as the tests compare it: its reach, or for a file how it is opened and its word as written."""
    return EndText(end.reach if end.file is None else f"{end.opening} {end.file.text}")


def streams(text: ShellText) -> list[tuple[ShellText | None, Ends]]:
    """Each command's name and where its descriptors 0, 1 and 2 end, in order."""
    return [(command.name, (spelled_end(command.streams.input), spelled_end(command.streams.output),
                            spelled_end(command.streams.errors))) for command in read(text).commands]


class Streams(unittest.TestCase):
    """Where each command's descriptors 0, 1 and 2 end, read as bash 5.3 sets them."""

    def test_a_statement_of_redirections_alone(self) -> None:  # a command of no words, bash's `$(< F)` among them
        cases = {
            "> X": [(None, (C, "write X", C))], "< F": [(None, ("read F", C, C))],
            "> X; a": [(None, (C, "write X", C)), ("a", (C, C, C))],
            "> X |& b": [(None, (C, "write X", "write X")), ("b", ("pipe", C, C))],
            "> $(a)": [("a", (C, "substitution", C)), (None, (C, "write $(a)", C))],
            'a "$(< F)"': [(None, ("read F", "substitution", C)), ("a", (C, C, C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)
        whole = commands(ShellText('a "$(< F)"'))[0]
        self.assertEqual((whole.words, [(r.op, r.fd, r.target.text) for r in whole.redirs], whole.shells, whole.text),
                         ((), [("<", None, "F")], ("substitution",), "< F"))

    def test_own_redirections(self) -> None:
        cases = {
            "a": [("a", (C, C, C))], "a > X 2>&1": [("a", (C, "write X", "write X"))],
            "a 2>&1 > X": [("a", (C, "write X", C))], "a >& X": [("a", (C, "write X", "write X"))],
            "a &> X": [("a", (C, "write X", "write X"))], "a &>> X": [("a", (C, "append X", "append X"))],
            "a >> X": [("a", (C, "append X", C))], "a >| X": [("a", (C, "write X", C))],
            "> X a": [("a", (C, "write X", C))], "a 2> X": [("a", (C, C, "write X"))],
            "a < F": [("a", ("read F", C, C))], "a <> F": [("a", ("update F", C, C))],
            "a 1<> F": [("a", (C, "update F", C))],
            'a > "$LOG" 2>&1': [("a", (C, 'write "$LOG"', 'write "$LOG"'))],
            "a <<< w": [("a", ("text", C, C))], "a <<E\nb\nE": [("a", ("text", C, C))],
            "a <<-E\n\tb\n\tE": [("a", ("text", C, C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_copies_and_closes(self) -> None:
        lost: Ends = ("unknown", "unknown", "unknown")
        cases = {
            "a > X 3>&1 1>&2 2>&3": [("a", lost)], "a < F 2<&0": [("a", ("read F", C, "read F"))],
            "a >&-": [("a", (C, "closed", C))], "a <&-": [("a", ("closed", C, C))],
            "a 2> X 1>&2-": [("a", (C, "write X", "closed"))], "a >&5": [("a", lost)],
            "a >&$V": [("a", (C, "unknown", "unknown"))], "a 2>&$V": [("a", (C, C, "unknown"))],
            "a 2>&w": [("a", (C, C, "unknown"))], "a <&w": [("a", ("unknown", C, C))],
            "a {v}> X": [("a", lost)], "a 10> Y >&10": [("a", lost)], "a 3> Y": [("a", lost)],
            "a >&3-": [("a", lost)], "a 3>&1 > X": [("a", ("unknown", "write X", "unknown"))],
            "a > 3": [("a", (C, "write 3", C))], "{ a; } 3> Y": [("a", lost)],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_devices_and_substitutions_as_targets(self) -> None:
        cases = {
            "a > /dev/null 2>&1": [("a", (C, "null", "null"))],
            "a 2> X > /dev/stderr": [("a", (C, "write X", "write X"))],
            "a > X 2> /dev/stdout": [("a", (C, "write X", "write X"))],
            "a < F > /dev/stdin": [("a", ("read F", "read F", C))],
            "exec 3> Y; a > /dev/fd/3": [("exec", ("unknown", "unknown", "unknown")),
                                         ("a", ("unknown", "unknown", "unknown"))],
            'a > "/dev/null"': [("a", (C, "null", C))],
            "a > /dev/fd/7": [("a", (C, "unknown", C))], "a > /dev/tty": [("a", (C, "write /dev/tty", C))],
            "a > >(b)": [("b", ("substitution", C, C)), ("a", (C, "substitution", C))],
            "a < <(b)": [("b", (C, "substitution", C)), ("a", ("substitution", C, C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_exec_keeps_its_redirections(self) -> None:
        cases = {
            "exec > X 2>&1; a": [("exec", (C, "write X", "write X")), ("a", (C, "write X", "write X"))],
            "exec 3> Y; a >&3": [("exec", ("unknown", "unknown", "unknown")),
                                 ("a", ("unknown", "unknown", "unknown"))],
            "exec > X; { a; } 2> Y": [("exec", (C, "write X", C)), ("a", (C, "write X", "write Y"))],
            "{ exec > Z; a; } > X; b": [("exec", (C, "write Z", C)), ("a", (C, "write Z", C)), ("b", (C, C, C))],
            "{ exec > Z; } 2> X; b": [("exec", (C, "write Z", "write X")), ("b", (C, "write Z", C))],
            "command exec > X; a": [("command", (C, "write X", C)), ("exec", (C, "write X", C)),
                                    ("a", (C, "write X", C))],
            "exec a > X; b": [("exec", (C, "write X", C)), ("a", (C, "write X", C)), ("b", (C, C, C))],
            "exec > X; { a; } > Y; b": [("exec", (C, "write X", C)), ("a", (C, "write Y", C)),
                                        ("b", (C, "write X", C))],
            "(exec > X); a": [("exec", (C, "write X", C)), ("a", (C, C, C))],
            "bash -c 'exec > X'; a": [("bash", (C, C, C)), ("exec", (C, "write X", C)), ("a", (C, C, C))],
            "exec > X; bash -c a": [("exec", (C, "write X", C)), ("bash", (C, "write X", C)),
                                    ("a", (C, "write X", C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_paths_joined(self) -> None:
        cases = {
            "if c; then exec > X; fi; a": [("c", (C, C, C)), ("exec", (C, "write X", C)), ("a", (C, "unknown", C))],
            "if c; then exec > X; else exec > X; fi; a": [("c", (C, C, C)), ("exec", (C, "write X", C)),
                                                         ("exec", (C, "write X", C)), ("a", (C, "write X", C))],
            "case w in w) exec > X;; esac; a": [("exec", (C, "write X", C)), ("a", (C, "unknown", C))],
            "while c; do exec > X; done; a": [("c", (C, "unknown", C)), ("exec", (C, "write X", C)),
                                              ("a", (C, "unknown", C))],
            "while c; do a; done > X": [("c", (C, "write X", C)), ("a", (C, "write X", C))],
            "f() { exec > X; }; a": [("exec", (C, "write X", C)), ("a", (C, "unknown", C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_enclosing_statements(self) -> None:
        log = (C, "write x.log", "write x.log")
        cases = {
            "{ cargo test; } > x.log 2>&1": [("cargo", log)], "( cargo test ) > x.log 2>&1": [("cargo", log)],
            "cargo test > x.log 2>&1": [("cargo", log)],
            "while true; do cargo test; done > x.log": [("true", (C, "write x.log", C)),
                                                         ("cargo", (C, "write x.log", C))],
            "f() { cargo test; }; f > x.log 2>&1": [("cargo", (C, C, C)), ("f", log)],
            "time { a; } > X 2>&1": [("a", (C, "write X", "write X"))],
            "f() { a; } > X; f": [("a", (C, "write X", C)), ("f", (C, C, C))],
            "if c; then a; fi > X": [("c", (C, "write X", C)), ("a", (C, "write X", C))],
            "{ a > Y; } > X": [("a", (C, "write Y", C))],
            "{ a; } > X; b": [("a", (C, "write X", C)), ("b", (C, C, C))],
            "eval a > X; b": [("eval", (C, "write X", C)), ("a", (C, "write X", C)), ("b", (C, C, C))],
            "cat > s.sh <<'E'\na\nE\n. ./s.sh > X; b": [("cat", ("text", "write s.sh", C)), (".", (C, "write X", C)),
                                                        ("a", (C, "write X", C)), ("b", (C, C, C))],
            "declare A=1 > X": [("declare", (C, "write X", C))],
            "export X=/dev/null; (a > $X)": [("export", (C, C, C)), ("a", (C, "null", C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_pipeline_seats(self) -> None:
        cases = {
            "a | b": [("a", (C, "pipe", C)), ("b", ("pipe", C, C))],
            "a | b | c": [("a", (C, "pipe", C)), ("b", ("pipe", "pipe", C)), ("c", ("pipe", C, C))],
            "cargo test |& tee x": [("cargo", (C, "pipe", "pipe")), ("tee", ("pipe", C, C))],
            "a > X |& b": [("a", (C, "write X", "write X")), ("b", ("pipe", C, C))],
            "a |& b | c": [("a", (C, "pipe", "pipe")), ("b", ("pipe", "pipe", C)), ("c", ("pipe", C, C))],
            "{ a 2> Y; } |& b": [("a", (C, "pipe", "write Y")), ("b", ("pipe", C, C))],
            "{ a; } |& b": [("a", (C, "pipe", "pipe")), ("b", ("pipe", C, C))],
            "{ a; } > X | b": [("a", (C, "write X", C)), ("b", ("pipe", C, C))],
            "{ a | b; } > X": [("a", (C, "pipe", C)), ("b", ("pipe", "write X", C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_an_end_holds_only_what_its_reach_has(self) -> None:
        a, b = read(ShellText("a > /dev/null 2> F | b")).commands
        c = read(ShellText("coproc c")).commands[0]
        self.assertEqual(tuple(a.streams.input), ("call", None, None, None))
        self.assertEqual(tuple(a.streams.output), ("null", None, None, None))
        self.assertEqual(tuple(b.streams.input), ("pipe", None, None, a.pipelines[0].pipeline))
        self.assertEqual(tuple(c.streams.input), ("pipe", None, None, None))
        self.assertEqual(a.streams.errors.opening, "write")

    def test_a_pipe_is_its_pipelines(self) -> None:
        a, b, c, _ = read(ShellText("a | b; c | d")).commands
        self.assertEqual(a.streams.output.pipeline, a.pipelines[0].pipeline)
        self.assertEqual(b.streams.input.pipeline, a.pipelines[0].pipeline)
        self.assertEqual(c.streams.output.pipeline, c.pipelines[0].pipeline)
        self.assertNotEqual(a.pipelines[0].pipeline, c.pipelines[0].pipeline)

    def test_substitutions(self) -> None:
        cases = {
            "b $(a)": [("a", (C, "substitution", C)), ("b", (C, C, C))],
            "b $(a) > X": [("a", (C, "substitution", C)), ("b", (C, "write X", C))],
            "b `a`": [("a", (C, "substitution", C)), ("b", (C, C, C))],
            "b ${ a; }; c": [("a", (C, "substitution", C)), ("b", (C, C, C)), ("c", (C, C, C))],
            "b ${| a; }": [("a", (C, C, C)), ("b", (C, C, C))],
            "b <(a)": [("a", (C, "substitution", C)), ("b", (C, C, C))],
            "b >(a)": [("a", ("substitution", C, C)), ("b", (C, C, C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_jobs(self) -> None:
        cases = {
            "a &": [("a", ("null", C, C))], "a < F &": [("a", ("read F", C, C))],
            "{ a; } &": [("a", ("null", C, C))], "a & b": [("a", ("null", C, C)), ("b", (C, C, C))],
            "coproc a": [("a", ("pipe", "pipe", C))], "coproc a; b": [("a", ("pipe", "pipe", C)), ("b", (C, C, C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_processes_inherit(self) -> None:
        log = (C, "write x.log", "write x.log")
        cases = {
            "bash -c 'cargo test' > x.log 2>&1": [("bash", log), ("cargo", log)],
            "bash -c 'a > Y' 2> X": [("bash", (C, C, "write X")), ("a", (C, "write Y", "write X"))],
            "cat > s.sh <<'E'\na > Y\nb\nE\nbash s.sh > X 2>&1": [
                ("cat", ("text", "write s.sh", C)), ("bash", (C, "write X", "write X")),
                ("a", (C, "write Y", "write X")), ("b", (C, "write X", "write X"))],
            "cat > s.sh <<'E'\n#!/bin/bash\na\nE\n./s.sh 2> X": [
                ("cat", ("text", "write s.sh", C)), ("./s.sh", (C, C, "write X")), ("a", (C, C, "write X"))],
            "env bash -c a > X": [("env", (C, "write X", C)), ("bash", (C, "write X", C)), ("a", (C, "write X", C))],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(streams(ShellText(text)), expected)

    def test_python_starts_reach_unknown_ends(self) -> None:  # whatever their stream keywords
        x, y, u = EndText("write X"), EndText("write Y"), EndText("unknown")
        unknown = (u, u, u)
        sub = "import subprocess; subprocess."
        cases = {
            f"{sub}run(['a'])": [("a", unknown)],
            f"{sub}run(['a'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)": [
                ("a", unknown)],
            f"{sub}run(['a'], capture_output=True, input=b'x')": [("a", unknown)],
            f"{sub}getoutput('a')": [("/bin/sh", unknown), ("a", unknown)],
            "import os; os.popen('a', 'w')": [("/bin/sh", unknown), ("a", unknown)],
            "import os; os.system('a')": [("/bin/sh", unknown), ("a", unknown)],
            f"{sub}run(['bash', '-c', 'b > Z'], stdout=subprocess.PIPE)": [
                ("bash", unknown), ("b", (u, EndText("write Z"), u))],
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                got = streams(ShellText(f'python3 -c "{code}" > X 2> Y'))
                self.assertEqual(got, [("python3", (C, x, y)), *expected])



class Planted(unittest.TestCase):
    """planted() names each call of a Python text that writes a file whose path ends `.py` or is not literal, as
    written, and no other."""

    def check(self, cases: dict[PythonText, tuple[ShellText, ...]]) -> None:
        """Each code's planted() is its expected calls."""
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(python.planted(code), expected)

    def test_the_writers(self) -> None:
        self.check({
            PythonText('from pathlib import Path\nPath("a.py").write_text("")'): (ShellText('Path("a.py").write_text("")'),),
            PythonText('p.write_bytes(b"")'): (ShellText('p.write_bytes(b"")'),),
            PythonText('import shutil\nshutil.copy("a", "b.py")'): (ShellText('shutil.copy("a", "b.py")'),),
            PythonText('import shutil as s\ns.copy2("a", "b.py")'): (ShellText('s.copy2("a", "b.py")'),),
            PythonText('from shutil import copyfile\ncopyfile("a", dst="b.py")'): (ShellText('copyfile("a", dst="b.py")'),),
            PythonText('import shutil\nshutil.move("a", "b.py")'): (ShellText('shutil.move("a", "b.py")'),),
            PythonText('import os\nos.replace("a", "b.py")'): (ShellText('os.replace("a", "b.py")'),),
            PythonText('import os\nos.rename("a", "b.py")'): (ShellText('os.rename("a", "b.py")'),),
            PythonText('open( "a.py" , "w")'): (ShellText('open( "a.py" , "w")'),),
            PythonText('open("a.py", mode="a")'): (ShellText('open("a.py", mode="a")'),),
            PythonText('open(file="a.py", mode="x")'): (ShellText('open(file="a.py", mode="x")'),),
            PythonText('open("a.py", "r+")'): (ShellText('open("a.py", "r+")'),),
            PythonText('open("a.py", m)'): (ShellText('open("a.py", m)'),),
            PythonText('import io\nio.open("a.py", "w")'): (ShellText('io.open("a.py", "w")'),),
            PythonText('open("a.py", "w")\nx = "\\d"'): (ShellText('open("a.py", "w")'),),
        })

    def test_a_path_not_literal(self) -> None:
        self.check({
            PythonText('open(name, "w")'): (ShellText('open(name, "w")'),),
            PythonText('P("out.json", k=1).write_text("")'): (ShellText('P("out.json", k=1).write_text("")'),),
            PythonText('import shutil\nshutil.copy("a", where)'): (ShellText('shutil.copy("a", where)'),),
        })

    def test_what_writes_no_python_file(self) -> None:
        self.check({
            PythonText('open("a.py")'): (),
            PythonText('open("a.py", "r")'): (),
            PythonText('open("a.txt", "w")'): (),
            PythonText('from pathlib import Path\nPath("out.json").write_text("")'): (),
            PythonText('import shutil\nshutil.copy("a.py", "b.txt")'): (),
            PythonText('from shutil import copyfile\ncopyfile("a", dst="b.txt")'): (),
            PythonText('open(file="a.txt", mode="w")'): (),
            PythonText('"a.py".replace("a", b)'): (),
            PythonText('def ('): (),
        })


if __name__ == "__main__":
    unittest.main()
