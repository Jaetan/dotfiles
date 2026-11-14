"""The kit's own heavy rows (dispatcher/heavy_table.py) over the fixture of tests/test_heavy.py, whose project rows name
none of these tools but `cabal run shake`, which decides over the kit's `cabal run` of fewer words. The old heavy-
run-guard cases these rows decide keep their ids, read as heavy or not; the others hold each row heavy, light where it
lists, collects, dry-runs or names only its targets, and each exception the table's docstring lists. A case lists,
for each command of the call heavy() answers, its name and the deciding row's matcher field and value."""

import shutil
import unittest
from collections.abc import Sequence
from typing import ClassVar

from dispatcher.heavy import Matched, heavy
from dispatcher.model import build
from dispatcher.tree import ShellText
from dispatcher.words import Directory
from tests.test_heavy import SETTINGS, Answer, Case, CaseId, Key, Places, Where, places


def named(found: Matched) -> Key:
    """How a case names what heavy() gave: `unknown`, `None`, or its row's matcher field and value."""
    if found is None or isinstance(found, str):
        return Key(str(found))
    fields = (("module", found.module), ("script", found.script), ("argv", " ".join(found.argv)),
              ("program", found.program))
    return Key(next(f"{field} {value}" for field, value in fields if value))


def ran(name: ShellText, row: Key) -> list[Answer]:
    """One command ``name`` run heavy by the row ``row`` names."""
    return [(name, row)]


def spelled(text: ShellText) -> tuple[ShellText, ...]:
    """The words of ``text``, split at its spaces."""
    return tuple(ShellText(word) for word in text.split(" "))


PYTHON3 = ShellText("python3")
MODULE_PYTEST = Key("module pytest")
PROGRAM_PYTEST = Key("program pytest")
MUTMUT_TEXT = ShellText("mutmut")
MUTMUT = Key("program mutmut")
MODULE_MUTMUT = Key("module mutmut")
CARGO_TEXT = ShellText("cargo")
CARGO_TEST = Key("argv cargo test")
GO_TEXT = ShellText("go")
CMAKE_TEXT = ShellText("cmake")
CMAKE = Key("program cmake")
CTEST_TEXT = ShellText("ctest")
CTEST = Key("program ctest")
CABAL_TEXT = ShellText("cabal")
CABAL_BUILD = Key("argv cabal build")
CABAL_RUN = Key("argv cabal run")
SHAKE = Key("argv cabal run shake")
AGDA_TEXT = ShellText("agda")
AGDA = Key("program agda")
GREMLINS_TEXT = ShellText("gremlins")
UNLEASH = Key("argv gremlins unleash")
MULL_TEXT = ShellText("mull-runner-23")
MULL = Key("program mull-runner")
BAZEL_TEXT = ShellText("bazel")
DOCKER_TEXT = ShellText("docker")
DOCKER_BUILD = Key("argv docker build")

CARGO_WRITERS = spelled(ShellText("build b test t clippy bench run r check c doc d fix"))
GO_BUILDS = spelled(ShellText("build run vet generate install"))
CABAL_COMMANDS = spelled(ShellText("build v2-build new-build test v2-test bench install v2-install run v2-run new-run"))
BAZEL_COMMANDS = spelled(ShellText("build test run"))
MAKE_LIGHT = spelled(ShellText("-n --just-print --dry-run --recon -q --question"))
GNU_MAKES = spelled(ShellText("make gmake"))
MUTMUT_LIGHT = spelled(ShellText("apply badge browse export-cicd-stats print-time-estimates results show tests-for-mutant"))
MAKES = (*GNU_MAKES, ShellText("ninja"))

BASEDPYRIGHT_VALUED = spelled(ShellText(
    "--baselinefile --createstub --gitlabcodequality --level --project --pythonpath --pythonplatform --pythonversion "
    "--threads --typeshedpath --venvpath --verifytypes -p -t -v"))
CARGO_GLOBAL_VALUED = spelled(ShellText("--explain --color -C --config -Z"))
CABAL_GLOBAL_VALUED = spelled(ShellText("--config-file --http-transport --store-dir --active-repositories"))
CABAL_VALUED = (*CABAL_GLOBAL_VALUED, *spelled(ShellText(
    "--builddir -w --with-compiler --with-hc-pkg --prefix --bindir --libdir --libsubdir --dynlibdir --libexecdir "
    "--libexecsubdir --datadir --datasubdir --docdir --htmldir --haddockdir --sysconfdir --program-prefix "
    "--program-suffix --profiling-detail --library-profiling-detail --configure-option --package-db -f --flags "
    "--extra-include-dirs --ipid --cid --extra-lib-dirs --extra-lib-dirs-static --extra-framework-dirs "
    "--extra-prog-path --instantiate-with --coverage-for --cabal-lib-version -c --constraint --preference --solver "
    "--write-ghc-environment-files --doc-index-file --max-backjumps --reject-unconstrained-dependencies "
    "--index-state --root-cmd --build-summary --build-log --remote-build-reporting --haddock-html-location "
    "--haddock-css --haddock-hscolour-css --haddock-contents-location --haddock-base-url --haddock-resources-dir "
    "--haddock-output-dir --test-log --test-machine-log --test-show-details --test-wrapper --test-options "
    "--test-option --benchmark-options --benchmark-option --project-dir --project-file --package-env "
    "--overwrite-policy --install-method --installdir")))
BAZEL_STARTUP_VALUED = spelled(ShellText(
    "--bazelrc --connect_timeout_secs --digest_function --experimental_cgroup_parent --extra_classpath "
    "--failure_detail_out --io_nice_level --local_startup_timeout_secs --macos_qos_class --max_idle_secs "
    "--output_base --output_user_root --server_jvm_out --host_jvm_args --server_javabase"))
DOCKER_VALUED = spelled(ShellText("--config -c --context -H --host -l --log-level --tlscacert --tlscert --tlskey"))
CMAKE_VALUED = spelled(ShellText(
    "-S -B -C -D -U -G -T -A --toolchain --install-prefix --project-file --preset --install --open -P --config"))
CTEST_VALUED = spelled(ShellText(
    "--preset -C --build-config --test-output-size-passed --test-output-size-failed --test-output-truncation -O "
    "--output-log --output-junit -E --exclude-regex -LE --label-exclude -FA --fixture-exclude-any -FS "
    "--fixture-exclude-setup -FC --fixture-exclude-cleanup -D --dashboard -M --test-model -T --test-action --group "
    "-S --script -SP --script-new-process -A --add-notes --tests-from-file --exclude-from-file --repeat "
    "--repeat-until-fail --max-width --resource-spec-file --test-dir --extra-submit --http-header --timeout "
    "--stop-time"))
AGDA_VALUED = spelled(ShellText(
    "--emacs-mode --compile-dir -i --include-path -l --library --library-file -v --verbose --profile "
    "--termination-depth --instance-search-depth --inversion-max-depth -W --warning --ghc-flag --with-compiler "
    "--dependency-graph --dependency-graph-include --html-dir --css --latex-dir"))
GREMLINS_VALUED = spelled(ShellText(
    "--config -S --output-statuses -t --tags --coverpkg -D --diff -o --output -E --exclude-files "
    "--threshold-efficacy --threshold-mcover --workers --test-cpu --timeout-coefficient"))
MULL_VALUED = spelled(ShellText(
    "--test-program --workers --timeout --ld-search-path --coverage-info --minimum-timeout --reporters --report-dir "
    "--report-name --report-patch-base --mutation-score-threshold --sqlite-busy-timeout"))

OLD: list[Case] = [
    (CaseId("A13"), ShellText("python/.venv/bin/python -m pytest tests/"), "work",
     ran(ShellText("python/.venv/bin/python"), MODULE_PYTEST)),
    (CaseId("A55"), ShellText("python3 -m pytest tests/test_x.py -q"), "work", []),
    (CaseId("CX5"), ShellText("tests=$(cd python && .venv/bin/python -m pytest tests/test_x.py -q 2>&1 | tail -1)"),
     "work", []),
    (CaseId("N1b"), ShellText("python/.venv/bin/python -m pytest tests/test_x.py -q"), "work", []),
    (CaseId("N2a"), ShellText("python3 -m pytest tests/test_x.py -q -p no:randomly"), "work", []),
    (CaseId("N2b"), ShellText("python3 -m pytest tests/test_x.py -k name"), "work", []),
    (CaseId("P04"), ShellText("pytest tests/"), "work", ran(ShellText("pytest"), PROGRAM_PYTEST)),
    (CaseId("P08"), ShellText("mutmut run"), "work", ran(MUTMUT_TEXT, MUTMUT)),
    (CaseId("P27"), ShellText("python3 -m pytest tests/test_x.py"), "work", []),
    (CaseId("P31"), ShellText("python3 /q2/venv/bin/pytest tests/"), "work", ran(PYTHON3, PROGRAM_PYTEST)),
    (CaseId("P32"), ShellText("python3 /q2/venv/bin/mutmut run"), "work", ran(PYTHON3, MUTMUT)),
    (CaseId("P33"), ShellText("python3 /q2/venv/bin/pylint aletheia/"), "work", ran(PYTHON3, Key("program pylint"))),
    (CaseId("Q04"), ShellText("uv run pytest tests/"), "work", ran(ShellText("pytest"), PROGRAM_PYTEST)),
    (CaseId("Q05"), ShellText("uv run --with x python -m pytest tests/"), "work",
     ran(ShellText("python"), MODULE_PYTEST)),
    (CaseId("V02"), ShellText("python3 -m pytest tests/test_a.py tests/"), "work", ran(PYTHON3, MODULE_PYTEST)),
    (CaseId("V02b"), ShellText("python3 -m pytest tests/*.py"), "work", ran(PYTHON3, MODULE_PYTEST)),
    (CaseId("V14e"), ShellText("pytest -h"), "work", []),
]

PYTEST: list[Case] = [
    (CaseId("py.test"), ShellText("py.test tests/"), "work", ran(ShellText("py.test"), Key("program py.test"))),
    (CaseId("a versioned pytest"), ShellText("pytest-3 tests/"), "work", ran(ShellText("pytest-3"), PROGRAM_PYTEST)),
    (CaseId("an interpreter's py.test"), ShellText("python3 /q2/venv/bin/py.test tests/"), "work",
     ran(PYTHON3, Key("program py.test"))),
    (CaseId("pytest alone"), ShellText("pytest"), "work", ran(ShellText("pytest"), PROGRAM_PYTEST)),
    (CaseId("-m pytest alone"), ShellText("python3 -m pytest"), "work", ran(PYTHON3, MODULE_PYTEST)),
    (CaseId("pytest --collect-only"), ShellText("pytest --collect-only"), "work", []),
    (CaseId("pytest --co"), ShellText("pytest --co tests/"), "work", []),
    (CaseId("py.test --collect-only"), ShellText("py.test --collect-only"), "work", []),
    (CaseId("py.test --co"), ShellText("py.test --co"), "work", []),
    (CaseId("-m pytest --collect-only"), ShellText("python3 -m pytest --collect-only"), "work", []),
    (CaseId("-m pytest --co"), ShellText("python3 -m pytest --co tests/"), "work", []),
    (CaseId("pytest's files and test ids"), ShellText("pytest tests/test_a.py tests/test_b.py::test_c"), "work", []),
    (CaseId("py.test's test id"), ShellText("py.test tests/test_a.py::test_c"), "work", []),
    (CaseId("-m pytest's test id"), ShellText("python3 -m pytest tests/test_a.py::test_c"), "work", []),
    (CaseId("an interpreter's pytest targeted"), ShellText("python3 /q2/venv/bin/pytest tests/test_a.py"), "work",
     []),
    (CaseId("pytest's glob"), ShellText("pytest 'tests/test_?.py'"), "work", ran(ShellText("pytest"), PROGRAM_PYTEST)),
    (CaseId("-m pytest's unknown operand"), ShellText('python3 -m pytest "$x"'), "work", ran(PYTHON3, MODULE_PYTEST)),
]

MUTMUT_CASES: list[Case] = [
    (CaseId("mutmut run's mutants"), ShellText("mutmut run 'x.y__mutmut_*'"), "work", ran(MUTMUT_TEXT, MUTMUT)),
    (CaseId("mutmut results"), ShellText("mutmut results"), "work", []),
    (CaseId("mutmut show"), ShellText("mutmut show x.y__mutmut_1"), "work", []),
    (CaseId("mutmut apply"), ShellText("mutmut apply x.y__mutmut_1"), "work", []),
    *((CaseId(f"mutmut {sub} runs no mutant"), ShellText(f"mutmut {sub}"), "work", []) for sub in MUTMUT_LIGHT),
    *((CaseId(f"-m mutmut {sub} runs no mutant"), ShellText(f"python3 -m mutmut {sub}"), "work", [])
      for sub in MUTMUT_LIGHT),
    (CaseId("mutmut alone"), ShellText("mutmut"), "work", ran(MUTMUT_TEXT, MUTMUT)),
    (CaseId("an unknown subcommand"), ShellText('mutmut "$x"'), "work", ran(MUTMUT_TEXT, MUTMUT)),
    (CaseId("an interpreter's mutmut results"), ShellText("python3 /q2/venv/bin/mutmut results"), "work", []),
    (CaseId("-m mutmut run"), ShellText("python3 -m mutmut run"), "work", ran(PYTHON3, MODULE_MUTMUT)),
    (CaseId("-m mutmut results"), ShellText("python3 -m mutmut results"), "work", []),
    (CaseId("mutmut run asking for help"), ShellText("mutmut run --help"), "work", []),
    (CaseId("-m mutmut asking for its version"), ShellText("python3 -m mutmut --version"), "work", []),
]

LINTERS: list[Case] = [
    (CaseId("pylint's files"), ShellText("pylint a.py b.py"), "work", []),
    (CaseId("pylint's folder"), ShellText("pylint src/"), "work", ran(ShellText("pylint"), Key("program pylint"))),
    (CaseId("-m pylint's file"), ShellText("python3 -m pylint a.py"), "work", []),
    (CaseId("-m pylint's folder"), ShellText("python3 -m pylint src/"), "work", ran(PYTHON3, Key("module pylint"))),
    (CaseId("pylint's valued option"), ShellText("pylint --rcfile pylintrc a.py"), "work", []),
    (CaseId("pylint's flag"), ShellText("pylint --enable-all-extensions a.py"), "work", []),
    (CaseId("basedpyright's file"), ShellText("basedpyright a.py"), "work", []),
    (CaseId("basedpyright alone"), ShellText("basedpyright"), "work",
     ran(ShellText("basedpyright"), Key("program basedpyright"))),
    (CaseId("-m basedpyright's file"), ShellText("python3 -m basedpyright a.py"), "work", []),
    (CaseId("-m basedpyright alone"), ShellText("python3 -m basedpyright"), "work",
     ran(PYTHON3, Key("module basedpyright"))),
]

BUILD_OLD: list[Case] = [
    (CaseId("A01"), ShellText("cargo test"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("A03"), ShellText("(cd rust && cargo test)"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("A06"), ShellText("for i in 1 2; do cargo test; done"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("A14"), ShellText("cargo mutants --in-place"), "work", ran(CARGO_TEXT, Key("argv cargo mutants"))),
    (CaseId("A28"), ShellText("cargo +nightly test"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("A40"), ShellText("echo $(cargo test)"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("A41"), ShellText("ctest -N --test-dir cpp/build"), "work", []),
    (CaseId("A46"), ShellText("go -C go test ./aletheia/"), "work", ran(GO_TEXT, Key("argv go test"))),
    (CaseId("A48"), ShellText("if cargo test; then echo ok; fi"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("A50"), ShellText("n=${#x}; cargo test"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("F08"), ShellText("( (cd rust && cargo test) )"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("F2a"), ShellText("(cd go && go vet ./aletheia/) & (cd rust && cargo fmt --check) & wait"), "work",
     ran(GO_TEXT, Key("argv go vet"))),
    (CaseId("R06"), ShellText("cargo install --list"), "work", []),
    (CaseId("R06b"), ShellText("cargo mutants --list --json"), "work", []),
    (CaseId("R06e"), ShellText("cargo llvm-cov --branch --summary-only --no-run"), "work", []),
    (CaseId("R11"), ShellText("go vet ./"), "work", ran(GO_TEXT, Key("argv go vet"))),
    (CaseId("R11b"), ShellText("go test -run '^TestZZ$' -count=1 ."), "work", ran(GO_TEXT, Key("argv go test"))),
    (CaseId("R11c"), ShellText("ctest --test-dir cpp/build -R excel_tests -V"), "work", ran(CTEST_TEXT, CTEST)),
    (CaseId("R11d"), ShellText("ctest --test-dir cpp/build -V"), "work", ran(CTEST_TEXT, CTEST)),
    (CaseId("V13g"), ShellText("cargo fix --allow-dirty"), "work", ran(CARGO_TEXT, Key("argv cargo fix"))),
    (CaseId("CM6"), ShellText("cmake --version"), "work", []),
    (CaseId("CM7"), ShellText("cmake -E echo x"), "work", ran(CMAKE_TEXT, CMAKE)),
    (CaseId("CM8"), ShellText("cmake --build cpp/build --target help"), "work", ran(CMAKE_TEXT, CMAKE)),
    (CaseId("CM9"), ShellText("cmake --build cpp/build --target help --target unit_tests"), "work",
     ran(CMAKE_TEXT, CMAKE)),
]

CARGO_CASES: list[Case] = [
    *((CaseId(f"cargo {sub}"), ShellText(f"cargo {sub}"), "work", ran(CARGO_TEXT, Key(f"argv cargo {sub}")))
      for sub in (*CARGO_WRITERS, ShellText("mutants"), ShellText("llvm-cov"), ShellText("install"))),
    (CaseId("cargo mutants --list-files"), ShellText("cargo mutants --list-files"), "work", []),
    (CaseId("cargo mutants --emit-schema"), ShellText("cargo mutants --emit-schema config"), "work", []),
    (CaseId("cargo install --list is install's"), ShellText("cargo build --list"), "work",
     ran(CARGO_TEXT, Key("argv cargo build"))),
    (CaseId("--no-run is llvm-cov's"), ShellText("cargo test --no-run"), "work", ran(CARGO_TEXT, CARGO_TEST)),
    (CaseId("mutants' --list-files is not install's"), ShellText("cargo install --list-files"), "work",
     ran(CARGO_TEXT, Key("argv cargo install"))),
    (CaseId("cargo asking for help"), ShellText("cargo test --help"), "work", []),
]

GO_CASES: list[Case] = [
    *((CaseId(f"go {sub}"), ShellText(f"go {sub} ./..."), "work", ran(GO_TEXT, Key(f"argv go {sub}")))
      for sub in (ShellText("test"), *GO_BUILDS)),
    (CaseId("go vet alone"), ShellText("go vet"), "work", ran(GO_TEXT, Key("argv go vet"))),
    (CaseId("go test -run runs what it selects"), ShellText("go test -run X ./..."), "work",
     ran(GO_TEXT, Key("argv go test"))),
    (CaseId("go test -list with its value"), ShellText("go test -list X ./..."), "work", []),
    (CaseId("go test -list with its value attached"), ShellText("go test -list=X ./..."), "work", []),
    (CaseId("-list is go test's"), ShellText("go build -list X"), "work", ran(GO_TEXT, Key("argv go build"))),
]

CMAKE_CASES: list[Case] = [
    (CaseId("cmake --build"), ShellText("cmake --build b"), "work", ran(CMAKE_TEXT, CMAKE)),
    (CaseId("cmake --workflow"), ShellText("cmake --workflow --preset x"), "work", ran(CMAKE_TEXT, CMAKE)),
    (CaseId("cmake configuring"), ShellText("cmake -S . -B b"), "work", ran(CMAKE_TEXT, CMAKE)),
    (CaseId("an unknown word"), ShellText('cmake "$x"'), "work", ran(CMAKE_TEXT, CMAKE)),
    (CaseId("cmake asking for help"), ShellText("cmake --help"), "work", []),
]

CTEST_CASES: list[Case] = [
    (CaseId("ctest"), ShellText("ctest"), "work", ran(CTEST_TEXT, CTEST)),
    *((CaseId(f"ctest {option}"), ShellText(f"ctest {option}"), "work", [])
      for option in spelled(ShellText("-N --show-only --show-only=json-v1 --print-labels"))),
    *((CaseId(f"ctest {option} runs what it selects"), ShellText(f"ctest {option}"), "work", ran(CTEST_TEXT, CTEST))
      for option in (ShellText("-R x"), ShellText("-I 1,2"), ShellText("-L x"), ShellText("--tests-regex x"),
                     ShellText("--label-regex x"), ShellText("--tests-information 1"))),
    (CaseId("ctest --version"), ShellText("ctest --version"), "work", []),
]

MAKE_CASES: list[Case] = [
    *((CaseId(name), name, "work", ran(name, Key(f"program {name}"))) for name in MAKES),
    *((CaseId(f"{name} {option}"), ShellText(f"{name} {option}"), "work", []) for name in GNU_MAKES
      for option in MAKE_LIGHT),
    (CaseId("ninja -n"), ShellText("ninja -n"), "work", []),
    *((CaseId(f"{option} is no ninja dry run"), ShellText(f"ninja {option}"), "work",
       ran(ShellText("ninja"), Key("program ninja")))
      for option in (ShellText("-t targets"), ShellText("-q"), ShellText("--dry-run"), ShellText("--just-print"),
                     ShellText("--question"), ShellText("-v"))),
    *((CaseId(f"{name} -t touches"), ShellText(f"{name} -t"), "work", ran(name, Key(f"program {name}")))
      for name in GNU_MAKES),
]

RUNNERS_OLD: list[Case] = [
    (CaseId("A15"), ShellText("agda +RTS -M16G -RTS src/Aletheia/Main.agda"), "work", ran(AGDA_TEXT, AGDA)),
    (CaseId("A22"), ShellText('taskset -c 0-19 cabal run shake -- build > /tmp/x.log 2>&1; '
                              'echo "EXIT=$?" >> /tmp/x.log'), "work", ran(CABAL_TEXT, SHAKE)),
    (CaseId("A41b"), ShellText("cabal run shake -- count-modules"), "work", []),
    (CaseId("A47"), ShellText("cabal run -v0 shake -- build"), "work", ran(CABAL_TEXT, SHAKE)),
    (CaseId("AG3"), ShellText("/home/nicolas/.cabal/bin/agda --numeric-version"), "work", []),
    (CaseId("AG4"), ShellText("agda --print-agda-dir"), "work", []),
    (CaseId("AG5"), ShellText("agda src/Aletheia/Main.agda"), "work", ran(AGDA_TEXT, AGDA)),
    (CaseId("M08"), ShellText("cabal run shake -- build -V"), "work", ran(CABAL_TEXT, SHAKE)),
    (CaseId("MU1"), ShellText("mull-runner-23 ./unit_tests --dry-run --reporters=Elements"), "work", []),
    (CaseId("MU2"), ShellText("mull-runner-23 ./unit_tests"), "work", ran(MULL_TEXT, MULL)),
    (CaseId("P02"), ShellText("cabal run shake -- build"), "work", ran(CABAL_TEXT, SHAKE)),
    (CaseId("P05"), ShellText("agda +RTS -M16G -RTS src/Main.agda"), "work", ran(AGDA_TEXT, AGDA)),
    (CaseId("P07"), ShellText("gremlins unleash ./aletheia"), "work", ran(GREMLINS_TEXT, UNLEASH)),
    (CaseId("P16"), ShellText("mull-runner-23 unit_tests"), "work", ran(MULL_TEXT, MULL)),
    (CaseId("V13h"), ShellText("bazel build //..."), "work", ran(BAZEL_TEXT, Key("argv bazel build"))),
    (CaseId("V14d"), ShellText("agda -V"), "work", []),
]

CABAL_CASES: list[Case] = [
    *((CaseId(f"cabal {sub}"), ShellText(f"cabal {sub}"), "work", ran(CABAL_TEXT, Key(f"argv cabal {sub}")))
      for sub in CABAL_COMMANDS),
    (CaseId("cabal run another target"), ShellText("cabal run other"), "work", ran(CABAL_TEXT, CABAL_RUN)),
    (CaseId("cabal run's options name no target"), ShellText("cabal run -v2 --enable-tests"), "work",
     ran(CABAL_TEXT, CABAL_RUN)),
    (CaseId("the project's cabal run shake decides"), ShellText("cabal run shake"), "work", ran(CABAL_TEXT, SHAKE)),
    (CaseId("cabal --help build"), ShellText("cabal --help build"), "work", []),
    (CaseId("cabal's attached global option"), ShellText("cabal --store-dir=x build"), "work",
     ran(CABAL_TEXT, CABAL_BUILD)),
]

AGDA_CASES: list[Case] = [
    (CaseId("agda M.agda"), ShellText("agda M.agda"), "work", ran(AGDA_TEXT, AGDA)),
    (CaseId("agda alone"), ShellText("agda"), "work", ran(AGDA_TEXT, AGDA)),
    (CaseId("agda --version"), ShellText("agda --version"), "work", []),
    (CaseId("agda --numeric-version"), ShellText("agda --numeric-version"), "work", []),
    (CaseId("agda --print-agda-dir past a file"), ShellText("agda M.agda --print-agda-dir"), "work", []),
]

GREMLINS_CASES: list[Case] = [
    (CaseId("gremlins unleash"), ShellText("gremlins unleash"), "work", ran(GREMLINS_TEXT, UNLEASH)),
    (CaseId("gremlins unleash --dry-run"), ShellText("gremlins unleash --dry-run"), "work", []),
    (CaseId("gremlins unleash -d"), ShellText("gremlins unleash -d ./aletheia"), "work", []),
]

MULL_CASES: list[Case] = [
    (CaseId("mull-runner unversioned"), ShellText("mull-runner ./unit_tests"), "work",
     ran(ShellText("mull-runner"), MULL)),
    (CaseId("mull-runner --dry-run"), ShellText("mull-runner --dry-run ./unit_tests"), "work", []),
]

BAZEL_CASES: list[Case] = [
    *((CaseId(f"bazel {sub}"), ShellText(f"bazel {sub} //..."), "work", ran(BAZEL_TEXT, Key(f"argv bazel {sub}")))
      for sub in BAZEL_COMMANDS),
    (CaseId("bazel --version"), ShellText("bazel --version"), "work", []),
    (CaseId("bazel's attached startup option"), ShellText("bazel --output_base=/x test //..."), "work",
     ran(BAZEL_TEXT, Key("argv bazel test"))),
]

DOCKER_CASES: list[Case] = [
    (CaseId("docker build"), ShellText("docker build ."), "work", ran(DOCKER_TEXT, DOCKER_BUILD)),
    (CaseId("docker build --help"), ShellText("docker build --help"), "work", []),
]

SUBCOMMANDS: list[Case] = [(CaseId(text), text, "work", []) for text in (
    ShellText("cargo fmt --check"), ShellText("cargo help test"), ShellText("cargo --list"), ShellText("go mod tidy"),
    ShellText("go env"), ShellText("cabal"), ShellText("cabal clean"), ShellText("bazel query //..."),
    ShellText("docker run img"), ShellText("gremlins"))]

VALUED: list[Case] = [
    *((CaseId(f"cargo's global {option}"), ShellText(f"cargo {option} v test"), "work", ran(CARGO_TEXT, CARGO_TEST))
      for option in CARGO_GLOBAL_VALUED),
    (CaseId("a global option's value is no subcommand"), ShellText("cargo -C test help"), "work", []),
    (CaseId("go -C before build"), ShellText("go -C go build"), "work", ran(GO_TEXT, Key("argv go build"))),
    *((CaseId(f"cabal's {option} before the subcommand"), ShellText(f"cabal {option} x build"), "work",
       ran(CABAL_TEXT, CABAL_BUILD)) for option in CABAL_GLOBAL_VALUED),
    *((CaseId(f"bazel's {option} before the command"), ShellText(f"bazel {option} x build //..."), "work",
       ran(BAZEL_TEXT, Key("argv bazel build"))) for option in BAZEL_STARTUP_VALUED),
    *((CaseId(f"docker's {option} before build"), ShellText(f"docker {option} x build ."), "work",
       ran(DOCKER_TEXT, DOCKER_BUILD)) for option in DOCKER_VALUED),
    (CaseId("gremlins's --config before unleash"), ShellText("gremlins --config x unleash"), "work",
     ran(GREMLINS_TEXT, UNLEASH)),
    *((CaseId(f"basedpyright's {option}"), ShellText(f"basedpyright {option} v a.py"), "work", [])
      for option in BASEDPYRIGHT_VALUED),
    *((CaseId(f"-m basedpyright's {option}"), ShellText(f"python3 -m basedpyright {option} v a.py"), "work", [])
      for option in BASEDPYRIGHT_VALUED),
    (CaseId("--max-children takes --help as its value"), ShellText("mutmut run --max-children --help"), "work",
     ran(MUTMUT_TEXT, MUTMUT)),
    (CaseId("-m mutmut's --max-children takes --help"), ShellText("python3 -m mutmut run --max-children --help"),
     "work", ran(PYTHON3, MODULE_MUTMUT)),
    *((CaseId(f"cmake's {option} takes --help as its value"), ShellText(f"cmake {option} --help"), "work",
       ran(CMAKE_TEXT, CMAKE)) for option in CMAKE_VALUED),
    *((CaseId(f"ctest's {option} takes -N as its value"), ShellText(f"ctest {option} -N"), "work",
       ran(CTEST_TEXT, CTEST)) for option in CTEST_VALUED),
    *((CaseId(f"cabal run's {option} takes --help as its value"), ShellText(f"cabal run {option} --help"), "work",
       ran(CABAL_TEXT, CABAL_RUN)) for option in CABAL_VALUED),
    *((CaseId(f"agda's {option} takes --version as its value"), ShellText(f"agda {option} --version"), "work",
       ran(AGDA_TEXT, AGDA)) for option in AGDA_VALUED),
    *((CaseId(f"gremlins unleash's {option} takes -d as its value"), ShellText(f"gremlins unleash {option} -d"),
       "work", ran(GREMLINS_TEXT, UNLEASH)) for option in GREMLINS_VALUED),
    *((CaseId(f"mull-runner's {option} takes --dry-run as its value"),
       ShellText(f"mull-runner-23 {option} --dry-run ./unit_tests"), "work", ran(MULL_TEXT, MULL))
      for option in MULL_VALUED),
]

VERBOSE: list[Case] = [
    (CaseId("ctest -V alone"), ShellText("ctest -V"), "work", ran(CTEST_TEXT, CTEST)),
]

VERSION: list[Case] = [(CaseId(f"{name} -v"), ShellText(f"{name} -v"), "work", []) for name in GNU_MAKES]


class KitRows(unittest.TestCase):
    """heavy() over the kit's own rows, in the fixture of tests/test_heavy.py."""

    at: ClassVar[Places]

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def found(self, text: ShellText, where: Where) -> list[tuple[ShellText | None, Matched]]:
        """What heavy() answers for each command of ``text`` run from ``where``."""
        folders = {"work": self.at.work, "python": self.at.work / "python", "probes": self.at.work / "probes",
                   "link": self.at.base / "link"}
        return [(command.name, heavy(command, self.at.aletheia))
                for command in build(text, Directory(str(folders[where])), SETTINGS).commands]

    def check(self, cases: Sequence[Case]) -> None:
        """Each case's answers, None answers left out."""
        for case, text, where, want in cases:
            with self.subTest(case=case):
                self.assertEqual([(name, named(row)) for name, row in self.found(text, where) if row is not None],
                                 want)

    def test_old_cases(self) -> None:
        self.check(OLD)

    def test_pytest(self) -> None:
        self.check(PYTEST)

    def test_mutmut(self) -> None:
        self.check(MUTMUT_CASES)

    def test_linters(self) -> None:
        self.check(LINTERS)

    def test_build_old_cases(self) -> None:
        self.check(BUILD_OLD)

    def test_cargo(self) -> None:
        self.check(CARGO_CASES)

    def test_go(self) -> None:
        self.check(GO_CASES)

    def test_cmake(self) -> None:
        self.check(CMAKE_CASES)

    def test_ctest(self) -> None:
        self.check(CTEST_CASES)

    def test_make(self) -> None:
        self.check(MAKE_CASES)

    def test_runner_old_cases(self) -> None:
        self.check(RUNNERS_OLD)

    def test_cabal(self) -> None:
        self.check(CABAL_CASES)

    def test_agda(self) -> None:
        self.check(AGDA_CASES)

    def test_gremlins(self) -> None:
        self.check(GREMLINS_CASES)

    def test_mull_runner(self) -> None:
        self.check(MULL_CASES)

    def test_bazel(self) -> None:
        self.check(BAZEL_CASES)

    def test_docker(self) -> None:
        self.check(DOCKER_CASES)

    def test_subcommand_rows(self) -> None:
        """A tool of subcommand rows is light under every other subcommand."""
        self.check(SUBCOMMANDS)

    def test_valued_options(self) -> None:
        """The word after an option taking a value is neither a subcommand, an operand nor a light flag."""
        self.check(VALUED)

    def test_verbose_flag(self) -> None:
        """ctest's -V asks for verbose output, never its version."""
        self.check(VERBOSE)

    def test_version_flag(self) -> None:
        """make's and gmake's -v asks for their version."""
        self.check(VERSION)


if __name__ == "__main__":
    _ = unittest.main()
