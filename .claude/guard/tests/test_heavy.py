"""The heavy matcher over the rows of declarations/examples/aletheia.toml, read through the kit's own declaration
reader in a repository of its own: the old heavy-run-guard cases a declared row decides keep their ids, read as heavy
or not (A27, whose name the call does not make known, as unknown); the other cases name what each matcher and the
light test hold, that the retired keys are not read, and the ranks which of several rows that run a command decides
it. A case lists, for each command of the call heavy() answers, its name and the row it runs: a module, a script
relative to the work tree, an argv joined, a program, or `unknown`."""

import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, Literal, NamedTuple, NewType
from unittest import mock

from declarations.lookup import Declarations, Facts
from declarations.schema import Heavy, ModuleName, ProgramName, Word
from dispatcher.heavy import VALUED_BY, Matched, heavy
from dispatcher.model import ReadSettings, build
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.words import Directory, VarName

KIT = Path(__file__).resolve().parents[1]
EXAMPLE = KIT / "declarations" / "examples" / "aletheia.toml"
SETTINGS = ReadSettings(ReaderPath(str(KIT / "bin" / "shell-reader")), Seconds(5.0),
                        {VarName("HOME"): ShellText("/home/u")})
NO_CONFIG = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
TomlSource = NewType("TomlSource", str)
EXTRA = TomlSource('schema = 1\n[[heavy]]\nargv = ["make"]\nlight_targets = ["help"]\nvalued_options = ["-C"]\n'
                   '[[heavy]]\nargv = ["ninja", "all"]\nvalued_options = ["-C"]\nlight_with = ["-dry"]\n')
Key = NewType("Key", str)
CaseId = NewType("CaseId", str)
Where = Literal["work", "python", "probes", "link"]
type Answer = tuple[ShellText | None, Key]
type Case = tuple[CaseId, ShellText, Where, list[Answer]]
type Rank = tuple[CaseId, ShellText, Facts, tuple[Heavy, ...], list[Answer]]
BARE = Heavy(None, None, (), None, False, (), (), (), None, ())
MAKE = BARE._replace(argv=(Word("make"),))
RUN_CI = [(ShellText("python3"), Key("tools.run_ci"))]
PROBES = [(ShellText("bash"), Key("probes/run_all.sh"))]
BENCHMARK = [(None, Key("benchmark"))]
UNKNOWN = [(None, Key("unknown"))]


class Places(NamedTuple):
    """The fixture: its folder, the aletheia work tree, and the facts declared for it and for the extra repository."""

    base: Path
    work: Path
    aletheia: Facts
    extra: Facts


def repository(root: Path, declaration: TomlSource) -> None:
    """A git repository at ``root`` whose `.claude/guard.toml` holds ``declaration``."""
    (root / ".claude").mkdir(parents=True)
    _ = (root / ".claude" / "guard.toml").write_text(declaration)
    _ = subprocess.run(["git", "init", "-q"], cwd=root, env={**os.environ, **NO_CONFIG}, check=True,
                       capture_output=True)


def places() -> Places:
    """A fresh fixture: the aletheia example in a repository with python/ and probes/ folders and a link to it, and
    a repository declaring EXTRA."""
    base = Path(tempfile.mkdtemp()).resolve()
    work = base / "aletheia"
    repository(work, TomlSource(EXAMPLE.read_text()))
    repository(base / "extra", EXTRA)
    (work / "python").mkdir()
    (work / "probes").mkdir()
    (base / "link").symlink_to(work)
    declarations = Declarations(base / "machine.toml", base / "home")
    return Places(base, work, declarations.for_path(work), declarations.for_path(base / "extra"))


def key(found: Matched, work: Path) -> Key:
    """How a case names what heavy() gave: `unknown`, or the matcher its row sets."""
    if found is None or isinstance(found, str):
        return Key(str(found))
    named = (found.module, found.script and os.path.relpath(found.script, work), " ".join(found.argv), found.program)
    return Key(next(text for text in named if text))


OLD: list[Case] = [
    (CaseId("A16"), ShellText("bash benchmarks/run_all.sh --frames 10"), "work",
     [(ShellText("bash"), Key("benchmarks/run_all.sh"))]),
    (CaseId("A17"), ShellText("python3 -m tools.coverage_run"), "work",
     [(ShellText("python3"), Key("tools.coverage_run"))]),
    (CaseId("A22"),
     ShellText('taskset -c 0-19 cabal run shake -- build > /tmp/x.log 2>&1; echo "EXIT=$?" >> /tmp/x.log'),
     "work", [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("A23"), ShellText("python3 -m tools.check_build_incremental"), "work",
     [(ShellText("python3"), Key("tools.check_build_incremental"))]),
    (CaseId("A27"), ShellText('"$py" -m tools.mutation_run'), "work", UNKNOWN),
    (CaseId("A27b"), ShellText('py=python/.venv/bin/python; "$py" -m tools.mutation_run'), "work",
     [(ShellText("python/.venv/bin/python"), Key("tools.mutation_run"))]),
    (CaseId("A29"), ShellText("bash -x probes/run_all.sh"), "work", PROBES),
    (CaseId("A41b"), ShellText("cabal run shake -- count-modules"), "work", []),
    (CaseId("A45"), ShellText("python3 -u tools/run_ci.py"), "work", RUN_CI),
    (CaseId("A47"), ShellText("cabal run -v0 shake -- build"), "work", [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("R06c"), ShellText("python3 -m tools.coverage_run --scope"), "work", []),
    (CaseId("V13i"), ShellText("cpp/build/unit_tests"), "work",
     [(ShellText("cpp/build/unit_tests"), Key("unit_tests"))]),
]

MATCHERS: list[Case] = [
    (CaseId("isolated"), ShellText("python3 -I -m tools.iwyu"), "work", [(ShellText("python3"), Key("tools.iwyu"))]),
    (CaseId("safe path"), ShellText("python3 -P -m tools.iwyu"), "work", [(ShellText("python3"), Key("tools.iwyu"))]),
    (CaseId("joined -m"), ShellText("python3 -Imtools.iwyu"), "work", [(ShellText("python3"), Key("tools.iwyu"))]),
    (CaseId("module file by path"), ShellText("./tools/run_ci.py"), "work",
     [(ShellText("./tools/run_ci.py"), Key("tools.run_ci"))]),
    (CaseId("module file from below"), ShellText("python3 ../tools/run_ci.py"), "python", RUN_CI),
    (CaseId("a module file spelled from anywhere"), ShellText("python3 tools/run_ci.py"), "python", RUN_CI),
    (CaseId("a path ending in a module file's text"), ShellText("python3 xtools/run_ci.py"), "work", []),
    (CaseId("another module file"), ShellText("python3 tools/other.py"), "work", []),
    (CaseId("python code"), ShellText('python3 -c "x = 1"'), "work", []),
    (CaseId("script by path"), ShellText("./probes/run_all.sh"), "work",
     [(ShellText("./probes/run_all.sh"), Key("probes/run_all.sh"))]),
    (CaseId("sourced"), ShellText("source probes/run_all.sh"), "work",
     [(ShellText("source"), Key("probes/run_all.sh"))]),
    (CaseId("dotted"), ShellText(". probes/run_all.sh"), "work", [(ShellText("."), Key("probes/run_all.sh"))]),
    (CaseId("source alone"), ShellText("source"), "work", []),
    (CaseId("bare name"), ShellText("run_all.sh"), "probes", []),
    (CaseId("through sh"), ShellText("sh probes/run_all.sh"), "work", [(ShellText("sh"), Key("probes/run_all.sh"))]),
    (CaseId("a script under noexec runs nothing"), ShellText("bash -n probes/run_all.sh"), "work", []),
    (CaseId("noexec turned off runs the script"), ShellText("bash -n +n probes/run_all.sh"), "work", PROBES),
    (CaseId("--norc"), ShellText("bash --norc probes/run_all.sh"), "work", PROBES),
    (CaseId("carried text"), ShellText("bash -c probes/run_all.sh"), "work",
     [(ShellText("probes/run_all.sh"), Key("probes/run_all.sh"))]),
    (CaseId("linked directory"), ShellText("bash probes/run_all.sh"), "link", PROBES),
    (CaseId("a spelled script from an unknown directory"), ShellText('cd "$x" && bash probes/run_all.sh'), "work",
     PROBES),
    (CaseId("a path ending in a script from an unknown directory"), ShellText('cd "$x" && bash ../probes/run_all.sh'),
     "work", PROBES),
    (CaseId("a script placed from its directory"), ShellText("bash ./run_all.sh"), "probes", PROBES),
    (CaseId("no placing from an unknown directory"), ShellText('cd "$x" && bash run_all.sh'), "work", []),
    (CaseId("behind wrappers"), ShellText("taskset -c 0-19 nice cabal run shake -- build"), "work",
     [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("argv by path"), ShellText("~/.cabal/bin/cabal run shake -- build"), "work",
     [(ShellText("/home/u/.cabal/bin/cabal"), Key("cabal run shake"))]),
    (CaseId("argv cut short"), ShellText("cabal run"), "work", []),
    (CaseId("argv of another name"), ShellText("other run shake"), "work", []),
    (CaseId("another argv"), ShellText("cabal run other"), "work", []),
    (CaseId("unknown name"), ShellText("$(command -v cabal) run shake"), "work", UNKNOWN),
    (CaseId("program by path"), ShellText("./benchmark --frames 10"), "work",
     [(ShellText("./benchmark"), Key("benchmark"))]),
    (CaseId("an option before argv's words"), ShellText("cabal --verbose=0 run shake"), "work",
     [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("an option word before argv's words"), ShellText("cabal -v run -- shake"), "work",
     [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("a light flag before argv's words"), ShellText("cabal --help run shake"), "work", []),
    (CaseId("a versioned program"), ShellText("cpp/build/unit_tests-23"), "work",
     [(ShellText("cpp/build/unit_tests-23"), Key("unit_tests"))]),
    (CaseId("a dotted version"), ShellText("benchmark-3.10 --frames 10"), "work",
     [(ShellText("benchmark-3.10"), Key("benchmark"))]),
    (CaseId("no version"), ShellText("benchmark-1x"), "work", []),
    (CaseId("another program's version"), ShellText("other-1"), "work", []),
]

UNKNOWNS: list[Case] = [
    (CaseId("a name from its written text"), ShellText('"$x/benchmark" --frames 10'), "work", BENCHMARK),
    (CaseId("a name from its written text, single quotes"), ShellText("\"$x\"/'benchmark'"), "work", BENCHMARK),
    (CaseId("an expansion in the written name"), ShellText('"$x/$y"'), "work", UNKNOWN),
    (CaseId("a glob in the written name"), ShellText('"$x"/bench*'), "work", UNKNOWN),
    (CaseId("a question mark in the written name"), ShellText('"$x"/bench?'), "work", UNKNOWN),
    (CaseId("a bracket in the written name"), ShellText('"$x"/bench[1]'), "work", UNKNOWN),
    (CaseId("a command substitution in the written name"), ShellText('"$x"/`y`'), "work", UNKNOWN),
    (CaseId("a backslash in the written name"), ShellText('"$x"/a\\b'), "work", UNKNOWN),
    (CaseId("a written name no row names"), ShellText('"$x/ls" -la'), "work", []),
    *((CaseId(f"an unknown name asking {flag}"), ShellText(f'"$c" {flag}'), "work", [])
      for flag in ("--help", "-h", "--version", "-V")),
    (CaseId("an unknown unquoted name asking its version"), ShellText("$c --version"), "work", []),
    (CaseId("an unknown name asking help past an operand"), ShellText('"$c" build --help'), "work", []),
    (CaseId("an unknown name's other option"), ShellText('"$c" --verbose'), "work", UNKNOWN),
    (CaseId("an unknown name's light flag with a value"), ShellText('"$c" --version=1'), "work", UNKNOWN),
    (CaseId("an unknown name's unknown word"), ShellText('"$c" "$v"'), "work", UNKNOWN),
    (CaseId("unknown operands of a command no row names"), ShellText('cd "$x" && ls "$y" probes/*'), "work", []),
    (CaseId("an unknown argv word matches any"), ShellText('cabal "$x" shake'), "work",
     [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("an unknown module is any module"), ShellText('python3 -I -m "$m"'), "work", RUN_CI),
    (CaseId("an interpreter's unknown script is any module file"), ShellText('python3 "$s"'), "work", RUN_CI),
    (CaseId("an interpreter's script of a module file's base name"), ShellText('python3 "$d/run_ci.py"'), "work",
     RUN_CI),
    (CaseId("an interpreter's script of another base name"), ShellText('python3 "$d/probe.py"'), "work", []),
    (CaseId("an unknown script operand is any script"), ShellText('bash "$s"'), "work", PROBES),
    (CaseId("a glob operand is any script"), ShellText("bash probes/run_al*.sh"), "work", PROBES),
    (CaseId("a script of its base name"), ShellText('bash "$d/run_all.sh"'), "work", PROBES),
    (CaseId("a script of another base name"), ShellText('bash "$d/other.sh"'), "probes", []),
    (CaseId("a module file run by a written path"), ShellText('"$d/tools/run_ci.py"'), "work",
     [(None, Key("tools.run_ci"))]),
]

MODIFIERS: list[Case] = [
    (CaseId("--help"), ShellText("python3 -m tools.run_ci --help"), "work", []),
    (CaseId("-h"), ShellText("python3 -m tools.run_ci -h"), "work", []),
    (CaseId("--version"), ShellText("python3 -m tools.run_ci --version"), "work", []),
    (CaseId("-V"), ShellText("python3 -m tools.run_ci -V"), "work", []),
    (CaseId("--help takes no value"), ShellText("python3 -m tools.run_ci --help=x"), "work", RUN_CI),
    (CaseId("-h takes no letters"), ShellText("python3 -m tools.run_ci -hv"), "work", RUN_CI),
    (CaseId("an unknown word beside --help"), ShellText('python3 -m tools.run_ci "$x" --help'), "work", []),
    (CaseId("an unknown word alone"), ShellText('python3 -m tools.run_ci "$x"'), "work", RUN_CI),
    (CaseId("a Python file under no kind"), ShellText("python3 -m tools.run_ci a.py"), "work", RUN_CI),
    (CaseId("a verbose -V"), ShellText("cabal run shake -- -V"), "work",
     [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("shake --version"), ShellText("cabal run shake -- --version"), "work", []),
    (CaseId("a light target alone"), ShellText("cabal run shake help"), "work", []),
    (CaseId("light targets alone"), ShellText("cabal run shake -- count-modules help"), "work", []),
    (CaseId("a heavy target beside"), ShellText("cabal run shake -- help build"), "work",
     [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("no target"), ShellText("cabal run shake"), "work", [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("an unknown target"), ShellText('cabal run shake -- count-modules "$t"'), "work",
     [(ShellText("cabal"), Key("cabal run shake"))]),
    (CaseId("named tests"), ShellText('cpp/build/unit_tests "[parser]"'), "work", []),
    (CaseId("two test specs"), ShellText('unit_tests "[a]" "[b]"'), "work",
     [(ShellText("unit_tests"), Key("unit_tests"))]),
    (CaseId("a reporter"), ShellText("cpp/build/unit_tests -r compact"), "work",
     [(ShellText("cpp/build/unit_tests"), Key("unit_tests"))]),
    (CaseId("a reporter and a tag"), ShellText('integration_tests -r compact "[x]"'), "work", []),
    (CaseId("--warn"), ShellText("unit_tests --warn NoAssertions"), "work",
     [(ShellText("unit_tests"), Key("unit_tests"))]),
    (CaseId("--shard-count"), ShellText("unit_tests --shard-count 4"), "work",
     [(ShellText("unit_tests"), Key("unit_tests"))]),
    (CaseId("--min-duration"), ShellText("unit_tests --min-duration 1"), "work",
     [(ShellText("unit_tests"), Key("unit_tests"))]),
    (CaseId("an unknown test"), ShellText('unit_tests "$t"'), "work", [(ShellText("unit_tests"), Key("unit_tests"))]),
    (CaseId("light_with attached"), ShellText("python3 -m tools.coverage_run --scope=x"), "work", []),
    (CaseId("a valued option's value"), ShellText("benchmark --json --help"), "work",
     [(ShellText("benchmark"), Key("benchmark"))]),
    (CaseId("unit_tests --list-tests"), ShellText("cpp/build/unit_tests --list-tests"), "work", []),
    (CaseId("excel_tests --list-tags"), ShellText("excel_tests --list-tags"), "work", []),
    (CaseId("yaml_tests --list-reporters"), ShellText("yaml_tests --list-reporters"), "work", []),
    (CaseId("integration_tests --list-listeners"), ShellText("integration_tests --list-listeners"), "work", []),
    (CaseId("benchmark --list-tests"), ShellText("benchmark --list-tests"), "work", []),
    (CaseId("stability_bench --list-tags"), ShellText("stability_bench --list-tags"), "work", []),
]

EXTRA_CASES: list[Case] = [
    (CaseId("a valued option's value"), ShellText("make -C build help"), "work", []),
    (CaseId("a valued option and a target"), ShellText("make -C build all"), "work",
     [(ShellText("make"), Key("make"))]),
    (CaseId("a valued option before argv's words"), ShellText("ninja -C build all"), "work",
     [(ShellText("ninja"), Key("ninja all"))]),
    (CaseId("a valued option's value is no argv word"), ShellText("ninja -C all"), "work", []),
    (CaseId("a one-dash option alone"), ShellText("ninja all -dry"), "work", []),
    (CaseId("a one-dash option is no prefix"), ShellText("ninja all -dryx"), "work",
     [(ShellText("ninja"), Key("ninja all"))]),
    (CaseId("a one-dash option with its value"), ShellText("ninja all -dry=x"), "work", []),
]

FORMS = (BARE._replace(argv=(Word("cargo"),), light_targets=(Word("help"),)),
         BARE._replace(argv=(Word("cargo"), Word("test"))),
         BARE._replace(argv=(Word("ninja"), Word("all"))),
         BARE._replace(program=ProgramName("cmake"), valued_options=(Word("-B"),)),
         BARE._replace(program=ProgramName("pytest"), light_with=(Word("--collect-only"),)))

FORM_CASES: list[Case] = [
    (CaseId("a `+` option before argv's words"), ShellText("cargo +nightly test"), "work",
     [(ShellText("cargo"), Key("cargo test"))]),
    (CaseId("a `+` option is no operand"), ShellText("cargo +nightly help"), "work", []),
    (CaseId("`+` leads an option on any row"), ShellText("ninja +x all"), "work",
     [(ShellText("ninja"), Key("ninja all"))]),
    (CaseId("a program row runs whatever its options"), ShellText("cmake -S . -B b"), "work", [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("a build option"), ShellText("cmake --build b"), "work", [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("a workflow option"), ShellText("cmake --workflow --preset x"), "work",
     [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("a build option attached"), ShellText("cmake --build=b"), "work", [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("a help target is no light flag"), ShellText("cmake --build b --target help"), "work",
     [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("a heavy value beside"), ShellText("cmake --build b --target help --target unit_tests"), "work",
     [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("a heavy value under another option"), ShellText("cmake --build b --target help -t unit_tests"), "work",
     [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("another option's light value"), ShellText("cmake --build b -t clean"), "work",
     [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("an option with no value"), ShellText("cmake --build b --target"), "work",
     [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("an unknown target"), ShellText('cmake --build b --target "$t"'), "work",
     [(ShellText("cmake"), Key("cmake"))]),
    (CaseId("an interpreter's script runs a program row"), ShellText("python3 .venv/bin/pytest tests/"), "work",
     [(ShellText("python3"), Key("pytest"))]),
    (CaseId("an interpreter's versioned script runs a program row"), ShellText("python3 bin/pytest-9.2 tests/"),
     "work", [(ShellText("python3"), Key("pytest"))]),
    (CaseId("an interpreter's script run light"), ShellText("python3 .venv/bin/pytest --collect-only"), "work", []),
    (CaseId("an interpreter's script of another name"), ShellText("python3 .venv/bin/pytester tests/"), "work", []),
    (CaseId("an interpreter's unknown script runs a program row"), ShellText('python3 "$s" tests/'), "work",
     [(ShellText("python3"), Key("cmake"))]),
    (CaseId("an interpreter's code runs no program row"), ShellText('python3 -c "import pytest" tests/'), "work", []),
    (CaseId("an interpreter's module is no script"), ShellText("python3 -m pytest"), "work", []),
    (CaseId("a script read by no interpreter"), ShellText("cat .venv/bin/pytest"), "work", []),
]

SCOPES = (BARE._replace(program=ProgramName("agda")),
          BARE._replace(argv=(Word("go"), Word("vet"))),
          BARE._replace(program=ProgramName("tool")),
          BARE._replace(module=ModuleName("pytest"), targeted_by="pytest-operands"),
          BARE._replace(program=ProgramName("pylint"), targeted_by="python-files"),
          BARE._replace(module=ModuleName("tools.lint"), light_targets=(Word("help"),)))

SCOPE_CASES: list[Case] = [
    (CaseId("a version flag of its own is no light flag"), ShellText("agda --numeric-version"), "work",
     [(ShellText("agda"), Key("agda"))]),
    (CaseId("AG5"), ShellText("agda src/Aletheia/Main.agda"), "work", [(ShellText("agda"), Key("agda"))]),
    (CaseId("V14d"), ShellText("agda -V"), "work", []),
    (CaseId("a literate module"), ShellText("agda x.lagda"), "work", [(ShellText("agda"), Key("agda"))]),
    (CaseId("a literate module in Markdown"), ShellText("agda x.lagda.md"), "work",
     [(ShellText("agda"), Key("agda"))]),
    (CaseId("a library build"), ShellText("agda --build-library"), "work", [(ShellText("agda"), Key("agda"))]),
    (CaseId("an interaction"), ShellText("agda --interaction"), "work", [(ShellText("agda"), Key("agda"))]),
    (CaseId("a JSON interaction"), ShellText("agda --interaction-json"), "work",
     [(ShellText("agda"), Key("agda"))]),
    (CaseId("a heavy run asking for help"), ShellText("agda --help x.agda"), "work", []),
    (CaseId("an argv row runs whatever its operands"), ShellText("go vet ./"), "work",
     [(ShellText("go"), Key("go vet"))]),
    (CaseId("every package"), ShellText("go vet ./..."), "work", [(ShellText("go"), Key("go vet"))]),
    (CaseId("an option alone"), ShellText("tool --all"), "work", [(ShellText("tool"), Key("tool"))]),
    (CaseId("an option with `=VALUE`"), ShellText("tool --all=x"), "work",
     [(ShellText("tool"), Key("tool"))]),
    (CaseId("an operand alone"), ShellText("tool x"), "work", [(ShellText("tool"), Key("tool"))]),
    (CaseId("A55"), ShellText("python3 -m pytest tests/test_x.py -q"), "work", []),
    (CaseId("V02"), ShellText("python3 -m pytest tests/test_a.py tests/"), "work",
     [(ShellText("python3"), Key("pytest"))]),
    (CaseId("V02b"), ShellText("python3 -m pytest tests/*.py"), "work", [(ShellText("python3"), Key("pytest"))]),
    (CaseId("a test id"), ShellText("python3 -m pytest tests/test_a.py::test_b"), "work", []),
    (CaseId("a star"), ShellText("python3 -m pytest 'tests/*.py'"), "work",
     [(ShellText("python3"), Key("pytest"))]),
    (CaseId("a question mark"), ShellText("python3 -m pytest 'tests/test_?.py'"), "work",
     [(ShellText("python3"), Key("pytest"))]),
    (CaseId("a bracket"), ShellText("python3 -m pytest 'tests/test_[ab].py'"), "work",
     [(ShellText("python3"), Key("pytest"))]),
    (CaseId("no operand"), ShellText("python3 -m pytest"), "work", [(ShellText("python3"), Key("pytest"))]),
    (CaseId("an unknown operand never targets"), ShellText('python3 -m pytest "$x"'), "work",
     [(ShellText("python3"), Key("pytest"))]),
    (CaseId("a Python file"), ShellText("pylint a.py b.py"), "work", []),
    (CaseId("a test id is no Python file"), ShellText("pylint tests/test_a.py::test_b"), "work",
     [(ShellText("pylint"), Key("pylint"))]),
    (CaseId("a folder"), ShellText("pylint a.py src/"), "work", [(ShellText("pylint"), Key("pylint"))]),
    (CaseId("a glob character in a file"), ShellText("pylint 'test_?.py'"), "work",
     [(ShellText("pylint"), Key("pylint"))]),
    (CaseId("no file"), ShellText("pylint"), "work", [(ShellText("pylint"), Key("pylint"))]),
    (CaseId("a module's own word is no operand"), ShellText("python3 -m tools.lint help"), "work", []),
    (CaseId("a module file's own word is no operand"), ShellText("python3 tools/lint.py help"), "work", []),
    (CaseId("a module file run heavy"), ShellText("python3 tools/lint.py check"), "work",
     [(ShellText("python3"), Key("tools.lint"))]),
]

PYTEST_VALUED: tuple[ShellText, ...] = (
    ShellText("--assert"), ShellText("--cov-config"), ShellText("--doctest-report"), ShellText("--ignore"),
    ShellText("--last-failed-no-failures"), ShellText("--log-date-format"), ShellText("--log-file-mode"),
    ShellText("-p"), ShellText("--show-capture"))
LINTER_VALUED: tuple[ShellText, ...] = (
    ShellText("--additional-builtins"), ShellText("--allow-wildcard-with-all"), ShellText("--argument-rgx"),
    ShellText("--check-fixme-in-docstring"), ShellText("--check-str-concat-over-line-jumps"),
    ShellText("--class-const-naming-style"), ShellText("--confidence"), ShellText("--deprecated-modules"),
    ShellText("--evaluation"), ShellText("--expected-line-ending-format"), ShellText("--fail-on"),
    ShellText("--good-names-rgxs"), ShellText("--ignore-long-lines"), ShellText("--ignore-pattern-in-long-lines"),
    ShellText("--ignored-checks-for-mixins"), ShellText("--include-naming-hint"), ShellText("--inlinevar-rgx"),
    ShellText("--known-standard-library"), ShellText("--logging-modules"), ShellText("--max-module-lines"),
    ShellText("--max-returns"), ShellText("--min-public-methods"), ShellText("--missing-member-max-choices"),
    ShellText("--never-returning-functions"), ShellText("--overgeneral-exceptions"), ShellText("--py-version"),
    ShellText("--single-line-class-stmt"), ShellText("--spelling-ignore-comment-directives"),
    ShellText("--spelling-store-unknown-words"), ShellText("--typevar-rgx"), ShellText("--variable-naming-style"))
VALUED_CASES: list[Case] = [
    *((CaseId(f"pytest's {option}"), ShellText(f"python3 -m pytest {option} v tests/test_a.py"), "work", [])
      for option in PYTEST_VALUED),
    *((CaseId(f"a linter's {option}"), ShellText(f"pylint {option} v a.py"), "work", []) for option in LINTER_VALUED),
    (CaseId("a linter's flag"), ShellText("pylint --enable-all-extensions a.py"), "work", [])]

SPELLINGS = (BARE._replace(program=ProgramName("values"), valued_options=(Word("-tx"),), light_with=(Word("-ty"),)),
             BARE._replace(program=ProgramName("scoped"), light_with=(Word("-a"),)),
             BARE._replace(program=ProgramName("needs"), light_with=(Word("-n"),)),
             BARE._replace(program=ProgramName("verbose"), verbose_flags=(Word("-VV"),)),
             BARE._replace(program=ProgramName("kinds"), targeted_by="catch2-operands"),
             BARE._replace(program=ProgramName("ends"), light_with=(Word("--"),)),
             BARE._replace(program=ProgramName("marks"), light_with=(Word("+ab"),)))

SPELLING_CASES: list[Case] = [
    (CaseId("a valued option is no short one attached"), ShellText("values -tx v"), "work",
     [(ShellText("values"), Key("values"))]),
    (CaseId("a light option is no short one attached"), ShellText("values -ty"), "work", []),
    (CaseId("a longer option is no light one attached"), ShellText("scoped -ab"), "work",
     [(ShellText("scoped"), Key("scoped"))]),
    (CaseId("a short light option takes no value attached"), ShellText("scoped -ac"), "work",
     [(ShellText("scoped"), Key("scoped"))]),
    (CaseId("a light option with letters after it"), ShellText("needs -nx"), "work",
     [(ShellText("needs"), Key("needs"))]),
    (CaseId("a verbose flag is no -V attached"), ShellText("verbose -VV"), "work",
     [(ShellText("verbose"), Key("verbose"))]),
    (CaseId("a kind's valued option takes its value"), ShellText("kinds -sx v"), "work",
     [(ShellText("kinds"), Key("kinds"))]),
    (CaseId("-- spells no longer option"), ShellText("ends --x"), "work", [(ShellText("ends"), Key("ends"))]),
    (CaseId("-- alone"), ShellText("ends --"), "work", []),
    (CaseId("a `+` light option with `=VALUE`"), ShellText("marks +ab=x"), "work", []),
]


SEVERAL = (BARE._replace(program=ProgramName("lists"), valued_options=(Word("-B"),)),
           BARE._replace(argv=(Word("tool"), Word("test"))))

LISTS = [(ShellText("lists"), Key("lists"))]
SEVERAL_CASES: list[Case] = [
    (CaseId("an option of no listed value takes none"), ShellText("lists --of a b"), "work", LISTS),
    (CaseId("a value past the first out of the list"), ShellText("lists --of a c"), "work", LISTS),
    (CaseId("a valued option's value is no option of several"), ShellText("lists -B --of x a"), "work", LISTS),
    (CaseId("an option past one of no value"), ShellText("lists -z --of a c"), "work", LISTS),
    (CaseId("a value past the first is no argv word"), ShellText("tool --several a test"), "work", []),
    (CaseId("a word not known is one more value"), ShellText('tool --several a "$x" test'), "work", []),
]


class Matching(unittest.TestCase):
    """heavy() over a fixture of its own."""

    at: ClassVar[Places]

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def setUp(self) -> None:
        """Every case reads the rows it declares alone; the kit's own rows are tests/test_heavy_table.py's."""
        self.enterContext(mock.patch("dispatcher.heavy.KIT_ROWS", ()))

    def answers(self, text: ShellText, where: Where, facts: Facts) -> list[Answer]:
        """What heavy() answers for each command of ``text`` run from ``where``, None answers left out."""
        folders = {"work": self.at.work, "python": self.at.work / "python", "probes": self.at.work / "probes",
                   "link": self.at.base / "link"}
        found = [(command.name, heavy(command, facts)) for command in build(text, Directory(str(folders[where])),
                                                                             SETTINGS).commands]
        return [(name, key(row, self.at.work)) for name, row in found if row is not None]

    def check(self, cases: Sequence[Case], facts: Facts | None = None) -> None:
        """Each case's answers."""
        for case, text, where, want in cases:
            with self.subTest(case=case):
                self.assertEqual(self.answers(text, where, facts or self.at.aletheia), want)

    def test_old_cases(self) -> None:
        self.check(OLD)

    def test_matchers(self) -> None:
        self.check(MATCHERS)

    def test_unknown_words(self) -> None:
        """A name from the written text, UNKNOWN where none, and a word not known matching any row's word, module or
        file, a path's written base name narrowing the files."""
        self.check(UNKNOWNS)

    def test_modifiers(self) -> None:
        self.check(MODIFIERS)

    def test_valued_options(self) -> None:
        self.check(EXTRA_CASES, self.at.extra)

    def test_row_forms(self) -> None:
        """`+` leading an option, a program row run whatever its options, and a program run as a Python
        interpreter's script of its base name."""
        self.check(FORM_CASES, replace(self.at.extra, project=self.at.extra.project._replace(heavy=FORMS)))

    def test_row_scope(self) -> None:
        """A row run whatever its operands, and the targeted_by kinds pytest-operands and python-files with the
        options each reads a value of."""
        self.check([*SCOPE_CASES, *VALUED_CASES],
                   replace(self.at.extra, project=self.at.extra.project._replace(heavy=SCOPES)))

    def test_spellings(self) -> None:
        """A word spells an option whole, a light one alone or with `=VALUE`, never with a value attached: the
        catch2-operands kind given a valued -sx for its case."""
        kinds = {**VALUED_BY, "catch2-operands": VALUED_BY["catch2-operands"] | frozenset((ShellText("-sx"),))}
        with mock.patch.dict("dispatcher.heavy.VALUED_BY", kinds):
            self.check(SPELLING_CASES, replace(self.at.extra, project=self.at.extra.project._replace(heavy=SPELLINGS)))

    def test_several_values(self) -> None:
        """An option no list gives a value takes none: the words after it are operands."""
        self.check(SEVERAL_CASES, replace(self.at.extra, project=self.at.extra.project._replace(heavy=SEVERAL)))

    def test_ranks(self) -> None:
        """Of several rows that run a command, the one with the most words of its own decides, light or heavy, and
        at equal count the project's over the kit's own."""
        program = self.at.extra.project._replace(heavy=(BARE._replace(program=ProgramName("make"),
                                                                      light_targets=(Word("help"),)),))
        ranks: list[Rank] = [
            (CaseId("the row of more words"), ShellText("make help"), self.at.extra,
             (MAKE._replace(argv=(Word("make"), Word("help"))),), [(ShellText("make"), Key("make help"))]),
            (CaseId("light by the row of more words"), ShellText("cabal run shake help"), self.at.aletheia,
             (BARE._replace(argv=(Word("cabal"), Word("run"))),), []),
            (CaseId("the project's row at equal count"), ShellText("make help"), self.at.extra, (MAKE,), []),
            (CaseId("a program row's one word"), ShellText("make help"), replace(self.at.extra, project=program),
             (MAKE,), []),
        ]
        for case, text, facts, kit, want in ranks:
            with self.subTest(case=case), mock.patch("dispatcher.heavy.KIT_ROWS", kit):
                self.assertEqual(self.answers(text, "work", facts), want)

    def test_the_row_itself(self) -> None:
        command = build(ShellText("python3 -m tools.run_ci"), Directory(str(self.at.work)), SETTINGS).commands[0]
        self.assertIs(heavy(command, self.at.aletheia), self.at.aletheia.project.heavy[0])

    def test_absolute_from_an_unknown_directory(self) -> None:
        text = ShellText(f'cd "$x" && bash {self.at.work}/probes/run_all.sh')
        self.assertEqual(self.answers(text, "work", self.at.aletheia), PROBES)

    def test_no_words(self) -> None:
        command = build(ShellText("cabal run shake"), Directory(str(self.at.work)), SETTINGS).commands[0]
        self.assertIsNone(heavy(command._replace(words=()), self.at.aletheia))


if __name__ == "__main__":
    _ = unittest.main()
