"""The wrapper table read by the model's peeler: cases generated from every row, each peeling `grep -r x .` to grep,
beside cases written by hand for the command kept per wrapper, the shell text a wrapper runs and the shells the kit
does not read; every case reads its text with the kit's own shell reader, bin/shell-reader."""

import shlex
import unittest
from collections.abc import Iterator
from pathlib import Path

from dispatcher.model import (FOREIGN_FORM, GAP_FORM, INPUT_WORDS, WRAPPED_SHELL_FORM, Assignment, Call, Command,
                              ReadSettings, build)
from dispatcher.tree import Count, ReaderPath, Seconds, ShellText
from dispatcher.words import Directory, Environment, VarName
from dispatcher.wrappers import UNKNOWN_SHELL, WRAPPERS, Wrapper
from tests.test_model import dialects, peels

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
ENV: Environment = {VarName("HOME"): ShellText("/home/u")}
SETTINGS = ReadSettings(READER, Seconds(5.0), ENV)
WITH_SHELL = ReadSettings(READER, Seconds(5.0), {**ENV, VarName("SHELL"): ShellText("/bin/bash")})
T = ShellText
GREP = (T("grep"), T("-r"), T("x"), T("."))
BASH = T("SHELL=/bin/bash ")
LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
type Case = tuple[ShellText, ShellText, Wrapper]


def read(text: ShellText, settings: ReadSettings = SETTINGS) -> Call:
    """The call model of ``text`` run from /r."""
    return build(text, Directory("/r"), settings)


def spelled(command: Command) -> tuple[ShellText, ...]:
    """A command's words as the model knows them."""
    return tuple(word.spelled for word in command.words)


def known(row: Wrapper) -> frozenset[ShellText]:
    """Every option ``row`` names."""
    return frozenset(row.valued.keys() | row.optional | row.stops | row.ends | row.texts.keys()
                     | row.switches.keys() | row.clears | row.unsets)


def opening(row: Wrapper) -> list[ShellText]:
    """The words after which ``row`` runs the command that follows: the option or subcommand it needs, with a value
    where it takes one, and its operands, before the need where it is an end option."""
    operands = [T("op")] * row.operands
    if not row.needs:
        return operands
    need = min(row.needs)
    words = [need, T("v")] if need in row.valued else [need]
    return operands + words if need in row.ends else words + operands


def ran(row: Wrapper) -> tuple[ShellText, ...]:
    """The words of `grep -r x .` run past ``row``: with the word for its input after them where it appends."""
    return (*GREP, INPUT_WORDS.spelled) if row.appends is not None else GREP


def joined(*words: ShellText) -> ShellText:
    """Words joined into a call's text, each quoted where it needs it."""
    return T(" ".join(shlex.quote(word) for word in words))


def attachable(row: Wrapper) -> ShellText:
    """A value to attach to a short option of ``row``: the letter of one of its short options taking a value, which
    a short option read as a flag would take as that option, else `v`."""
    letters = sorted(option[1] for option in row.valued if len(option) == 2 and option not in row.stops)
    return T(letters[0] if letters else "v")


def given(option: ShellText, count: Count, row: Wrapper) -> Iterator[list[ShellText]]:
    """Each form ``option`` takes its ``count`` values in: as the next words, and after `=` or attached."""
    values = [T("v")] * count
    yield [option, *values]
    if option.startswith("--") or len(option) > 2:
        yield [T(f"{option}=v"), *values[1:]]
    else:
        yield [T(f"{option}v"), *values[1:]]


class Generated(unittest.TestCase):
    """Every row of the table read as it says, each case running `grep -r x .` past the wrapper."""

    def peels(self, cases: Iterator[Case]) -> None:
        """Each case's last command is grep, past its wrapper, or inside the carrier a joining wrapper runs in the
        shell the environment names; the wrapper is a command of its own before it."""
        for name, text, row in cases:
            with self.subTest(text=text):
                call = read(text, WITH_SHELL)
                *before, last = call.commands
                self.assertEqual(spelled(last), ran(row))
                if row.joins is None:
                    self.assertEqual(last.wrappers, (name,))
                else:
                    self.assertEqual(last.shells, ("carrier",))
                self.assertEqual([command.name for command in before], [name])
                self.assertEqual(call.gaps, ())

    def stays(self, cases: Iterator[Case]) -> None:
        """Each case's wrapper runs no command the kit follows: it is the last command, peeled from nothing."""
        for name, text, _ in cases:
            with self.subTest(text=text):
                command = read(text).commands[-1]
                self.assertEqual((command.name, command.wrappers), (name, ()))

    def test_each_valued_option(self) -> None:
        def cases() -> Iterator[Case]:
            for name, row in WRAPPERS.items():
                for option, count in row.valued.items():
                    if option not in row.stops and option not in row.texts:
                        for words in given(option, count, row):
                            yield name, joined(name, *words, *opening(row), *GREP), row
        self.peels(cases())

    def test_each_optional_value(self) -> None:
        def cases() -> Iterator[Case]:
            for name, row in WRAPPERS.items():
                for option in row.optional:
                    attached = f"{option}=v" if option.startswith("--") else f"{option}{attachable(row)}"
                    for words in ([option], [T(attached)]):
                        yield name, joined(name, *words, *opening(row), *GREP), row
        self.peels(cases())

    def test_a_cluster(self) -> None:
        def cases() -> Iterator[Case]:
            for name, row in WRAPPERS.items():
                flag = next(letter for letter in LETTERS if f"-{letter}" not in known(row))
                for option, count in row.valued.items():
                    if len(option) == 2 and not row.long_only and option not in row.stops | row.texts.keys():
                        values = [T("v")] * count
                        yield name, joined(name, T(f"-{flag}{option[1]}"), *values, *opening(row), *GREP), row
                        yield name, joined(name, T(f"-{flag}{option[1]}v"), *values[1:], *opening(row), *GREP), row
        self.peels(cases())

    def test_each_stop_option(self) -> None:
        self.stays((name, joined(name, stop, *opening(row), *GREP), row)
                   for name, row in WRAPPERS.items() for stop in row.stops)

    def test_operands(self) -> None:
        self.peels((name, joined(name, *opening(row), *GREP), row) for name, row in WRAPPERS.items())
        for name, row in WRAPPERS.items():
            if row.operands and not row.needs:
                with self.subTest(name=name):
                    self.assertEqual(spelled(read(joined(name, *GREP)).commands[-1])[0], GREP[row.operands])

    def test_a_numbered_operand(self) -> None:
        for name, row in WRAPPERS.items():
            if row.numbered:
                self.peels(iter([(name, joined(name, T("0"), *GREP), row), (name, joined(name, *GREP), row)]))
                self.assertEqual(spelled(read(joined(name, T("0x1"), *GREP)).commands[-1])[0], "0x1")

    def test_the_subcommand(self) -> None:
        for name, row in WRAPPERS.items():
            if row.subcommand is not None:
                after = [option for option, count in row.valued.items() if count == 1][:1]
                self.peels(iter([(name, joined(name, row.subcommand, *GREP), row),
                                 (name, joined(name, row.subcommand, *after, *([T("v")] * len(after)), *GREP),
                                  row)]))
                without = read(joined(name, *GREP)).commands[-1]
                self.assertEqual(without.name, name if row.subcommand in row.needs else "grep")

    def test_what_it_needs(self) -> None:
        self.stays((name, joined(name, *[T("op")] * row.operands, *GREP), row)
                   for name, row in WRAPPERS.items() if row.needs)

    def test_the_end_of_its_options(self) -> None:
        for name, row in WRAPPERS.items():
            for end in row.ends:
                spellings = [end, T(end[1:])] if row.long_only else [end]
                self.peels((name, joined(name, spelling, *GREP), row) for spelling in spellings)
                self.assertEqual(spelled(read(joined(name, end, T("-q"), *GREP), WITH_SHELL).commands[-1])[0], "-q")

    def test_each_text_option(self) -> None:
        for name, row in WRAPPERS.items():
            for option, runner in row.texts.items():
                forms = [[*[T("op")] * row.operands, option, T("grep -r x .")]]
                forms += [[option, T("grep -r x .")]] if option in row.valued else []
                for words in forms:
                    with self.subTest(name=name, words=words):
                        call = read(BASH + joined(name, *words))
                        if runner == UNKNOWN_SHELL:
                            self.assertEqual([gap.form for gap in call.gaps], [WRAPPED_SHELL_FORM])
                        else:
                            self.assertEqual((spelled(call.commands[-1]), call.commands[-1].shells),
                                             (GREP, ("carrier",)))

    def test_each_switch(self) -> None:
        for name, row in WRAPPERS.items():
            for option, runner in row.switches.items():
                with self.subTest(name=name, option=option):
                    call = read(BASH + joined(name, option, *GREP))
                    if runner is None:
                        self.assertEqual(call.commands[-1].wrappers, (name,))
                    elif runner == UNKNOWN_SHELL:
                        self.assertEqual([gap.form for gap in call.gaps], [WRAPPED_SHELL_FORM])
                    else:
                        self.assertEqual(call.commands[-1].shells, ("carrier",))
                    self.assertEqual(spelled(call.commands[-1]) == GREP, runner != UNKNOWN_SHELL)

    def test_a_builtin_and_a_program(self) -> None:
        for name, row in WRAPPERS.items():
            start = "import subprocess; subprocess.run(" + repr([name, *opening(row), *GREP]) + ")"
            for text, peeled in ((joined(T("python3"), T("-c"), T(start)), not row.builtin),
                                 (joined(T("sh"), T("-c"), joined(name, *opening(row), *GREP)),
                                  not row.builtin or "sh" in row.builtin)):
                with self.subTest(text=text):
                    command = read(text).commands[-1]
                    if peeled and row.joins is None:
                        self.assertEqual((spelled(command), command.wrappers), (ran(row), (name,)))
                    elif not peeled:
                        self.assertEqual(command.name, name)


class Kept(unittest.TestCase):
    """Each wrapper peeled is a command of its own, in the place it runs, before the command it runs."""

    def test_each_wrapper_a_command(self) -> None:
        text = T("cd /d; echo a | A=1 unshare -rn env B=2 timeout 9 grep -r x . > out")
        call = read(text)
        unshare, env, timeout, grep = call.commands[-4:]
        self.assertEqual([spelled(command)[0] for command in (unshare, env, timeout, grep)],
                         ["unshare", "env", "timeout", "grep"])
        self.assertEqual(spelled(env), ("env", "B=2", "timeout", "9", *GREP))
        self.assertEqual([command.wrappers for command in (unshare, env, timeout, grep)],
                         [(), ("unshare",), ("unshare", "env"), ("unshare", "env", "timeout")])
        a, b = Assignment(VarName("A"), T("1")), Assignment(VarName("B"), T("2"))
        self.assertEqual(peels(lambda: read(text))[-1].environ.assigns, (a, b))
        for command in (unshare, env, timeout):
            with self.subTest(command=command.name):
                self.assertEqual((command.shells, command.processes, command.pipelines, command.cwd, command.redirs,
                                  command.text),
                                 (grep.shells, grep.processes, grep.pipelines, grep.cwd, grep.redirs, grep.text))

    def test_what_each_wrapper_does_to_the_environment(self) -> None:
        cases = {"exec -c grep -r x .": (True, ()), "setpriv --reset-env grep -r x .": (True, ()),
                 "bwrap --clearenv grep -r x .": (True, ()), "bwrap --unsetenv A grep -r x .": (False, ("A",)),
                 "sudo -E grep -r x .": (False, ()), "env -u A sudo B=1 grep -r x .": (False, ("A",))}
        for text, (cleared, unsets) in cases.items():
            with self.subTest(text=text):
                environ = peels(lambda: read(T(text)))[-1].environ
                self.assertEqual((spelled(read(T(text)).commands[-1]), environ.cleared, environ.unsets),
                                 (GREP, cleared, unsets))
        self.assertEqual(peels(lambda: read(T("env -u A sudo B=1 grep -r x .")))[-1].environ.assigns,
                         (Assignment(VarName("B"), T("1")),))

    def test_a_python_start_keeps_each_wrapper(self) -> None:
        code = "import subprocess; subprocess.run(['strace', '-f', 'timeout', '9', 'grep', '-r', 'x', '.'])"
        strace, timeout, grep = read(joined(T("python3"), T("-c"), T(code))).commands[-3:]
        self.assertEqual([(command.name, command.wrappers, command.shells) for command in (strace, timeout, grep)],
                         [("strace", (), ("python",)), ("timeout", ("strace",), ("python",)),
                          ("grep", ("strace", "timeout"), ("python",))])

    def test_the_calls_past_calls_ran(self) -> None:
        cases = {
            "unshare -rn python3.14 run.py": ("python3.14", "run.py"),
            "prlimit --as=1000 --nofile=64 python3 x.py": ("python3", "x.py"),
            "prlimit --as=$((4<<30)) python3 -I - <<EOF\nprint(1)\nEOF": ("python3", "-I", "-"),
            "timeout --signal=$s 5 grep -r x .": GREP,
            "timeout -k$k 5 grep -r x .": GREP,
            "strace -f$x grep -r x .": GREP,
            'env "-$x" grep -r x .': GREP,
            'env "--$x" grep -r x .': GREP,
            'strace "--$x" -f grep -r x .': GREP,
            'timeout "--signal$x" 5 grep -r x .': GREP,
            "gdb -q -nx -batch -ex 'set breakpoint pending on' -ex 'break a.cpp:24' -x cmds.gdb --args ./prog a": (
                "./prog", "a"),
            "strace -f -qq -o strace.log -e trace=execve -s 200 grep -r x .": GREP,
            "setpriv --landlock-access fs --landlock-rule path-beneath:read-file:/ grep -r x .": GREP,
            "bwrap --ro-bind / / --dev /dev --tmpfs /tmp --chdir /r grep -r x .": GREP,
            "sudo -n true": (T("true"),),
            "uv run --no-project python3.15 x.py": ("python3.15", "x.py"),
            "nsenter -t 1 -m -u -i -n -p grep -r x .": GREP,
            "chroot --userspec=u:g /srv grep -r x .": GREP,
            "ionice -c3 nice -n 19 chrt -i 0 grep -r x .": GREP,
            "flock -n -w 5 /tmp/l grep -r x .": GREP,
            "gdb --args -q ./prog": ("-q", "./prog"),
            "uv run --with x python3.15 y.py": ("python3.15", "y.py"),
            "ionice -c 3 grep -r x .": GREP,
            "chroot --userspec u:g /srv grep -r x .": GREP,
            "systemd-run --user --slice claude.slice --scope -p MemoryMax=1G -u u grep -r x .": GREP,
        }
        for text, words in cases.items():
            with self.subTest(text=text):
                self.assertEqual(spelled(read(T(text)).commands[-1]), words)


    def test_what_runs_no_command(self) -> None:
        for text in ("runuser nobody grep -r x .", "uv pip install x", "uv run -m pytest", "gdb ./prog core",
                     "systemd-run --user --shell grep -r x .", "systemd-run --version grep -r x .",
                     "prlimit --pid=$p grep -r x .", "taskset -p$p grep -r x ."):
            with self.subTest(text=text):
                command = read(T(text)).commands[-1]
                self.assertEqual((command.name, command.wrappers), (text.split()[0], ()))
        self.assertEqual(read(T("watch -x grep -r x .")).commands[-1].shells, ())


class Input(unittest.TestCase):
    """A wrapper that adds the words it reads from its input runs its command with one word more, nothing of it
    known, and its row's command where its words name none."""

    def test_the_command_and_the_word_it_adds(self) -> None:
        cargo = (T("cargo"), T("test"))
        cases = {"xargs -0 -r cargo test < /dev/null": cargo, "xargs": (T("echo"),), "xargs -0 -r": (T("echo"),),
                 "xargs -n1 -P 4 -d , grep -r x": GREP[:3], "xargs --show-limits cargo test": cargo,
                 "xargs -I {} cargo test {}": (*cargo, T("{}")), "xargs -i cargo test": cargo,
                 "xargs -l -e cargo test": cargo, "xargs --eof=x --arg-file f cargo test": cargo,
                 "xargs -a f -- cargo test": cargo, "xargs timeout 5 cargo test": cargo,
                 "xargs --process-slot-var V -s 99 cargo test": cargo, "xargs -eI -iI -lI cargo test": cargo}
        for text, words in cases.items():
            with self.subTest(text=text):
                command = read(T(text)).commands[-1]
                self.assertEqual((spelled(command), command.words[-1].value), ((*words, INPUT_WORDS.spelled), None))
                self.assertEqual(command.wrappers[0], "xargs")

    def test_what_runs_no_command(self) -> None:
        for text in ("xargs --help cargo test", "xargs --version cargo test"):
            with self.subTest(text=text):
                command = read(T(text)).commands[-1]
                self.assertEqual((command.name, command.wrappers), ("xargs", ()))


class Script(unittest.TestCase):
    """A script runs the text of its -c, or its words after `--` joined, in the shell $SHELL names, and nothing the
    kit follows without either."""

    def test_what_it_runs(self) -> None:
        cases = {"SHELL=/bin/bash script -qc 'grep -r x .' /dev/null": "bash",
                 "SHELL=/bin/sh script --command='grep -r x .'": "sh",
                 "SHELL=/bin/bash script /dev/null -c 'grep -r x .'": "bash",
                 "SHELL=/bin/bash script -T t -O o -E never -m classic -o 9 -t -c 'grep -r x .' log": "bash",
                 "SHELL=/bin/bash script -q /dev/null -- grep -r x .": "bash",
                 "SHELL=/bin/bash script -q -- grep -r x .": "bash", "SHELL=/bin/bash script -- 'grep -r' x .": "bash"}
        for text, dialect in cases.items():
            with self.subTest(text=text):
                call = read(T(text))
                *_, holder, grep = call.commands
                self.assertEqual((spelled(grep), grep.shells, dialects(lambda: read(T(text)))["grep"], holder.name,
                                  call.gaps), (GREP, ("carrier",), dialect, "script", ()))

    def test_what_runs_no_command(self) -> None:
        for text in ("script -q /dev/null", "script -q grep -r x .", "script -h -c 'grep -r x .'",
                     "script -V -- grep -r x .", "script -q --", "script -tc 'grep -r x .'"):
            with self.subTest(text=text):
                call = read(T(text), WITH_SHELL)
                self.assertEqual(([command.name for command in call.commands], call.gaps), (["script"], ()))

    def test_a_gap_where_its_shell_is_not_one_the_kit_reads(self) -> None:
        cases = {"script -qc 'grep -r x .' /dev/null": WRAPPED_SHELL_FORM, "script -- grep -r x .": WRAPPED_SHELL_FORM,
                 "SHELL=/usr/bin/zsh script -qc 'grep -r x .' /dev/null": FOREIGN_FORM,
                 "SHELL=/usr/bin/fish script -q -- grep -r x .": FOREIGN_FORM}
        for text, form in cases.items():
            with self.subTest(text=text):
                self.assertEqual([(gap.text, gap.form) for gap in read(T(text)).gaps], [(text, form)])


class Texts(unittest.TestCase):
    """The shell text a wrapper runs is read as a `bash -c` text is, in the dialect of the shell its row names."""

    def test_a_text_in_a_shell_the_kit_reads(self) -> None:
        cases = {"SHELL=/bin/bash flock /l -c 'grep -r x .'": ("flock", "bash"),
                 "SHELL=/bin/sh flock /l -c 'grep -r x .'": ("flock", "sh"),
                 "SHELL=/usr/bin/dash flock /l --command 'grep -r x .'": ("flock", "sh"),
                 "watch -n 1 grep -r x .": ("watch", "sh"),
                 "env -i SHELL=/bin/bash flock /l -c 'grep -r x .'": ("flock", "bash"),
                 "SHELL=/usr/bin/zsh env SHELL=/bin/bash flock /l -c 'grep -r x .'": ("flock", "bash"),
                 "SHELL=/bin/bash sudo -u root -s grep -r x .": ("sudo", "bash")}
        for text, (wrapper, dialect) in cases.items():
            with self.subTest(text=text):
                call = read(T(text))
                *_, holder, grep = call.commands
                self.assertEqual((spelled(grep), grep.shells, dialects(lambda: read(T(text)))["grep"], grep.wrappers),
                                 (GREP, ("carrier",), dialect, ()))
                self.assertEqual((holder.name, holder.shells, holder.processes, call.gaps),
                                 (wrapper, (), (), ()))
                self.assertEqual(len(grep.processes), 1)

    def test_a_text_receives_the_wrappers_environment(self) -> None:
        got = peels(lambda: read(T("A=1 SHELL=/bin/bash env B=2 flock /l -c 'grep -r x .'")))[-1]
        self.assertEqual([(a.name, a.value) for a in got.environ.assigns],
                         [("A", "1"), ("SHELL", "/bin/bash"), ("B", "2")])
        got = peels(lambda: read(T("SHELL=/bin/bash sudo -s B=2 printf x")))[-1]
        self.assertEqual([(a.name, a.value) for a in got.environ.assigns], [("SHELL", "/bin/bash"), ("B", "2")])

    def test_the_environments_shell(self) -> None:
        call = read(T("flock /l -c 'grep -r x .'"), WITH_SHELL)
        self.assertEqual((spelled(call.commands[-1]), call.commands[-1].shells, call.gaps), (GREP, ("carrier",), ()))

    def test_a_text_option_without_its_text(self) -> None:
        call = read(T("SHELL=/bin/bash flock /l -c"))
        self.assertEqual(([command.name for command in call.commands], call.gaps), (["flock"], ()))

    def test_sudo_escapes_each_word_but_dollar_signs(self) -> None:
        call = read(T("SHELL=/bin/bash sudo -s printf '%s|' 'a b' '$HOME' '*'"))
        self.assertEqual(spelled(call.commands[-1]), ("printf", "%s|", "a b", "/home/u", "*"))

    def test_a_gap_where_the_shell_is_not_known(self) -> None:
        cases = {"flock /l -c 'grep -r x .'": SETTINGS, "SHELL=$S flock /l -c 'grep -r x .'": WITH_SHELL,
                 "env -i flock /l -c 'grep -r x .'": WITH_SHELL, "env -u SHELL flock /l -c 'grep -r x .'": WITH_SHELL,
                 "env -u $N flock /l -c 'grep -r x .'": WITH_SHELL, "sudo -i grep -r x .": WITH_SHELL,
                 "runuser -c 'grep -r x .' root": WITH_SHELL, "runuser -u u -c 'grep -r x .'": WITH_SHELL}
        for text, settings in cases.items():
            with self.subTest(text=text):
                call = read(T(text), settings)
                self.assertEqual([(gap.text, gap.form) for gap in call.gaps], [(text, WRAPPED_SHELL_FORM)])
                self.assertIn("runs its text in is not known from the call", call.gaps[0].what)
                self.assertNotIn("grep", [command.name for command in call.commands])

    def test_a_gap_where_the_text_is_not_known(self) -> None:
        for text in ("watch $CMD", "watch grep -r \"$X\" .", "SHELL=/bin/bash flock /l -c \"$CMD\""):
            with self.subTest(text=text):
                call = read(T(text))
                self.assertEqual([(gap.text, gap.form) for gap in call.gaps], [(text, GAP_FORM)])
                self.assertIn("runs is not known from the call", call.gaps[0].what)


class Foreign(unittest.TestCase):
    """A text in a shell the kit does not read, zsh, fish or another, is a gap naming the readable form, past a
    wrapper as in a wrapper's own text."""

    def test_fish_and_zsh_texts(self) -> None:
        cases = {"SHELL=/usr/bin/fish flock /l -c 'ls'": "flock runs its text in fish",
                 "SHELL=/usr/bin/zsh sudo -s ls": "sudo runs its text in zsh",
                 "SHELL=/bin/ksh flock /l -c 'ls'": "flock runs its text in ksh",
                 "sudo fish -c ls": "it runs fish", "strace -f zsh -c ls": "it runs zsh",
                 "uv run fish -c 'ls'": "it runs fish", "unshare -rn fish": "it runs fish"}
        for text, what in cases.items():
            with self.subTest(text=text):
                call = read(T(text))
                self.assertEqual([(gap.text, gap.form) for gap in call.gaps], [(text, FOREIGN_FORM)])
                self.assertEqual(call.gaps[0].what, f"{what}, which the kit does not read")


if __name__ == "__main__":
    unittest.main()
