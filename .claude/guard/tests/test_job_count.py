"""The job-count rule, through the dispatcher with every other rule switched off: a call starting a tool of the table
is refused unless the tool's last job-count word names a literal count within the agents' CPUs, the refusal naming
the tool, that word and the form that passes; the rest pass. The LIM cases carry the old heavy-run-guard rule 5
cases' meaning under their names; the old cases whose verdict the table-only rule moves are gathered in Moved,
those PYTHON_CPU_COUNT decided in Environment."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Literal, NamedTuple, NewType

from dispatcher.core import Answer, EventBytes, FolderPath, Settings, answer
from dispatcher.model import ReadSettings
from dispatcher.tree import Count, ReaderPath, Seconds, ShellText
from dispatcher.verdict import Prose
from dispatcher.words import VarName
from tests.test_declarations import NONE_DECLARED, TomlSource, declaring

KIT = Path(__file__).resolve().parents[1]
READER = ReaderPath(str(KIT / "bin" / "shell-reader"))
RULE = "job_count"
MARK = Prose("guard job-count:")
CaseId = NewType("CaseId", str)
Machine = Literal["agents", "narrow", "none", "missing"]
Status = Literal[0, 2]
Case = tuple[CaseId, ShellText, Status]
C = CaseId
T = ShellText
BASH = T("Bash")
FULL = Count(20)
FIVE = {VarName("PYTHON_CPU_COUNT"): T("5")}
MACHINES: dict[Machine, TomlSource | None] = {
    "agents": declaring(cpus=TomlSource('[cpus]\nagents = "0-19"\n')),
    "narrow": declaring(cpus=TomlSource('[cpus]\nagents = "0-7"\n')),
    "none": NONE_DECLARED,
    "missing": None,
}


class Places(NamedTuple):
    """The fixture's folders: one home per machine file under the base, the switch folder, the folder calls run in."""

    base: Path
    off: Path
    work: Path


def places() -> Places:
    """A fresh fixture, every rule but this one switched off, the old cases' three scripts, a script starting no tool
    and a virtual environment's interpreter, a binary, in the work folder."""
    base = Path(tempfile.mkdtemp()).resolve()
    at = Places(base, base / "off", base / "work")
    for folder in (at.off, at.work):
        folder.mkdir(parents=True)
    for machine, text in MACHINES.items():
        folder = base / machine / ".config" / "claude-guard"
        folder.mkdir(parents=True)
        if text is not None:
            (folder / "machine.toml").write_text(text)
    (at.work / "tidy.sh").write_text("run-clang-tidy-23 -quiet -p build src\n")
    (at.work / "tidy_bound.sh").write_text("run-clang-tidy-23 -j 4 -quiet -p build src\n")
    (at.work / "run-clang-tidy.py").write_text("import sys\n")
    (at.work / "tidy.py").write_text("import sys\n")
    (at.work / "quiet.sh").write_text("echo done\n")
    (at.work / ".venv" / "bin").mkdir(parents=True)
    (at.work / ".venv" / "bin" / "python").symlink_to(sys.executable)
    for entry in os.listdir(KIT / "rules"):
        stem, suffix = os.path.splitext(entry)
        if suffix == ".py" and not stem.startswith("_") and stem != RULE:
            (at.off / stem.replace("_", "-")).touch()
    return at


def judged(at: Places, call: ShellText, machine: Machine = "agents", tool: ShellText = BASH,
           env: dict[VarName, ShellText] | None = None) -> Answer:
    """The dispatcher's answer to a PreToolUse event for ``call``, under ``machine``'s file and ``env``."""
    home = at.base / machine
    settings = Settings(FolderPath(str(KIT / "rules")), FolderPath(str(at.off)),
                        ReadSettings(READER, Seconds(5.0), {VarName("HOME"): ShellText(str(home)), **(env or {})}),
                        home=home)
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(at.work),
              "tool_input": {"command": call}}
    return answer(EventBytes(json.dumps(fields).encode()), settings)


def line(tool: Prose, said: Prose, form: Prose, bound: Count = FULL) -> Prose:
    """The refusal's line for one command."""
    return Prose(f"  * `{tool}` {said}: name `{form} N`, N from 1 to {bound}\n")


class Fixture(unittest.TestCase):
    """A class of cases sharing one fixture."""

    at: Places

    @classmethod
    def setUpClass(cls) -> None:
        cls.at = places()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.at.base)

    def check(self, cases: list[Case], machine: Machine = "agents", tool: ShellText = BASH,
              env: dict[VarName, ShellText] | None = None) -> None:
        """Every case answered with its status, a refusal this rule's alone."""
        for case, call, status in cases:
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, machine, tool, env)
                self.assertEqual(got.status, status, got.stderr)
                if status == 2:
                    self.assertIn(MARK, got.stderr)
                    self.assertNotIn("guard kit:", got.stderr)
                else:
                    self.assertEqual(got.stderr, "")

    def says(self, call: ShellText, *lines: Prose, machine: Machine = "agents") -> None:
        """``call`` refused, its refusal holding each of ``lines``."""
        got = judged(self.at, call, machine)
        self.assertEqual(got.status, 2, got.stderr)
        for expected in lines:
            self.assertIn(expected, got.stderr)


class OldCases(Fixture):
    """The old rule 5 cases whose verdict the table-only rule keeps."""

    def test_refused(self) -> None:
        self.check([
            (C("LIM1"), T("run-clang-tidy-23 -quiet -p build cpp/src/"), 2),
            (C("LIM5"), T("run-clang-tidy-23 -j 0 -p build src"), 2),
            (C("LIM6"), T("run-clang-tidy-23 -j 24 -p build src"), 2),
            (C("LIM11"), T("PYTHON_CPU_COUNT=0 run-clang-tidy-23 -p build src"), 2),
            (C("LIM12"), T("python3 /usr/lib/llvm-23/bin/run-clang-tidy -p build src"), 2),
            (C("LIM13"), T("out=$(cd cpp && run-clang-tidy-23 -quiet -p build src 2>&1); echo \"$out\" | tail -1"), 2),
            (C("LIM14"), T("bash -c 'run-clang-tidy-23 -p build src'"), 2),
            (C("LIM15"), T("bash tidy.sh"), 2),
            (C("LIM17"), T("N=$(nproc); run-clang-tidy-23 -j $N -p build src"), 2),
            (C("LIM20"), T("python3 -c \"import subprocess; subprocess.run(['run-clang-tidy-23', '-p', 'build', "
                           "'src'])\""), 2),
            (C("LIM21"), T("python3 run-clang-tidy.py -p build src"), 2),
            (C("LIM23"), T("run-clang-tidy-23 -j 21 -p build src"), 2),
            (C("LIM25"), T("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -jx -p build src"), 2),
            (C("LIM27"), T("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -j 24 -p build src"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("LIM2"), T("run-clang-tidy-23 -j 5 -quiet -p build cpp/src/"), 0),
            (C("LIM3"), T("run-clang-tidy-23 -j5 -p build src"), 0),
            (C("LIM4"), T("run-clang-tidy-23 -j=5 -p build src"), 0),
            (C("LIM16"), T("bash tidy_bound.sh"), 0),
            (C("LIM18"), T("clang-tidy-23 -p build src/a.cpp"), 0),
            (C("LIM19"), T("git grep -n run-clang-tidy -- tools"), 0),
            (C("LIM22"), T("run-clang-tidy-23 -j 20 -p build src"), 0),
            (C("LIM24"), T("run-clang-tidy-23 -j 15 -p build src"), 0),
            (C("LIM28"), T("python3 -c 'print(1)'"), 0),
        ])

    def test_the_last_j_counts(self) -> None:
        self.check([
            (C("N-TIDY1"), T("run-clang-tidy-23 -j 24 -j 5 -p build src"), 0),
            (C("N-TIDY2"), T("run-clang-tidy-23 -j 5 -j 24 -p build src"), 2),
            (C("N-TIDY3"), T("run-clang-tidy-23 -j=5 -j0 -p build src"), 2),
        ])


class Moved(Fixture):
    """The old cases whose verdict the table-only rule moves: a listed tool with no count is refused whatever its
    environment or its own default, a letter bundle and an option that takes the next word as its value are not
    read, and a ninja passes on a word in its form where the old rule read it as -C's directory."""

    def test_refused(self) -> None:
        tidy = "import subprocess; subprocess.run(['run-clang-tidy-23', '-p', 'b']"
        self.check([
            (C("LIM7"), T("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p build src"), 2),
            (C("LIM8"), T("env PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p build src"), 2),
            (C("LIM9"), T("export PYTHON_CPU_COUNT=5; run-clang-tidy-23 -p build src"), 2),
            (C("LIM10"), T("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -j 0 -p build src"), 2),
            (C("N-PY1"), T(f"PYTHON_CPU_COUNT=5 python3 -c \"{tidy})\""), 2),
            (C("N-PY3"), T(f"python3 -c \"{tidy}, env={{'PYTHON_CPU_COUNT': '5'}})\""), 2),
            (C("N-MK17"), T("make -sj4"), 2), (C("N-MK18"), T("make -Cj"), 2),
            (C("N-MK19"), T("make -C -j all"), 2), (C("N-MK20"), T("make -- -j"), 2),
            (C("N-MK22"), T("make --directory -j"), 2), (C("N-NJ11"), T("ninja -vj8"), 2),
            (C("N-MM10"), T("PYTHON_CPU_COUNT=8 mutmut run"), 2),
        ])

    def test_passed(self) -> None:
        self.check([(C("N-NJ7"), T("ninja -C -j8"), 0)])


class Changed(Fixture):
    """The old cases whose refusal the new kit gives in another form: a Python script that is neither on disk nor
    written by the call is a call the kit cannot read, as a shell script is."""

    def test_new_verdicts(self) -> None:
        got = judged(self.at, T("python3 /opt/llvm/share/clang/run-clang-tidy.py -p build src"), "agents")
        self.assertEqual(got.status, 2)
        self.assertIn("guard kit: this call cannot be read: the script /opt/llvm/share/clang/run-clang-tidy.py is "
                      "neither on disk nor written by the call", got.stderr)


class Read(Fixture):
    """A shell script or Python code the call runs is read for the tools it starts, its interpreter no tool."""

    def test_no_tool_started(self) -> None:
        self.check([
            (C("N-READ1"), T("bash quiet.sh"), 0),
            (C("N-READ2"), T("python3 -c \"import subprocess; subprocess.run(['echo', 'done'])\""), 0),
        ])


class Python(Fixture):
    """A tool the Python code a call runs starts is judged as a command."""

    def test_started(self) -> None:
        tidy = "import subprocess; subprocess.run(['run-clang-tidy-23', '-p', 'b']"
        self.check([
            (C("N-PY2"), T(f"PYTHON_CPU_COUNT=5 python3 -c \"{tidy}, env={{'PYTHON_CPU_COUNT': '24'}})\""), 2),
            (C("N-PY4"), T("python3 -c \"import os; os.system('make -j')\""), 2),
            (C("N-PY5"), T("python3 -c \"import subprocess; subprocess.run(cmd)\""), 0),
            (C("N-PY6"), T("python3 -c \"import os; os.system('make -j 4')\""), 0),
        ])


class Refusal(Fixture):
    """The refusal names each command's tool as written, its last job-count word or none, and the form that passes."""

    def test_lines(self) -> None:
        self.says(T("make -j; ctest -j 24; ninja -j $N; mutmut run --max-children=0; run-clang-tidy-23 -j 4 -j; ninja"),
                  line(Prose("make"), Prose("with `-j`"), Prose("-j")),
                  line(Prose("ctest"), Prose("with `-j 24`"), Prose("-j")),
                  line(Prose("ninja"), Prose("with `-j $N`"), Prose("-j")),
                  line(Prose("mutmut"), Prose("with `--max-children=0`"), Prose("--max-children")),
                  line(Prose("run-clang-tidy-23"), Prose("with `-j`"), Prose("-j")),
                  line(Prose("ninja"), Prose("with no job count"), Prose("-j")))

    def test_the_lead_names_the_bound(self) -> None:
        self.says(T("make -j"), Prose("guard job-count: this call starts a tool without a literal job count from 1 to "
                                      "the agents' 20 CPUs.\n\n  * `make` with `-j`"),
                  Prose("\n\ntaskset and claude.slice's AllowedCPUs bound where a call's threads run, not how many jobs "
                        "a tool starts. Write each\ntool's count on its own command line, N a number from 1 to 20 shared "
                        "with what runs beside it."))


class Machines(Fixture):
    """The bound is the count of the machine's declared agents' CPUs; a machine declaring none bounds nothing, and a
    machine without its file refuses the call."""

    def test_a_narrower_set(self) -> None:
        self.check([(C("N-M1"), T("make -j 10"), 2), (C("N-M2"), T("make -j 8"), 0), (C("N-M3"), T("ninja -j 9"), 2),
                    (C("N-M4"), T("ninja -j 8"), 0)], machine="narrow")
        self.says(T("ninja"), line(Prose("ninja"), Prose("with no job count"), Prose("-j"), Count(8)),
                  machine="narrow")

    def test_no_agents_cpus(self) -> None:
        cases = [(C("N-M5"), T("make -j"), 0), (C("N-M6"), T("run-clang-tidy-23 -p b"), 0), (C("N-M7"), T("ninja"), 0)]
        self.check(cases, machine="none")
        file = self.at.base / "missing" / ".config" / "claude-guard" / "machine.toml"
        for case, call, _ in cases:
            with self.subTest(case=case, call=call):
                got = judged(self.at, call, "missing")
                self.assertEqual(got.status, 2, got.stderr)
                self.assertTrue(got.stderr.startswith(f"guard kit: the machine file {file} is missing: "), got.stderr)


class Table(Fixture):
    """Each tool of the table passes with a count in each of its own forms, and is refused with none or with a form
    only another tool reads."""

    def test_each_form_passes(self) -> None:
        self.check([
            (C("N-T1"), T("run-clang-tidy -j 4 -p b"), 0), (C("N-T2"), T("run-clang-tidy.py -j4 -p b"), 0),
            (C("N-T3"), T("run-clang-tidy-23 -j=4 -p b"), 0), (C("N-T4"), T("mutmut run --max-children 4"), 0),
            (C("N-T5"), T("mutmut run --max-children=4"), 0), (C("N-T6"), T("ctest -j 4"), 0),
            (C("N-T7"), T("ctest -j4"), 0), (C("N-T8"), T("ctest -j=4"), 0), (C("N-T9"), T("ctest --parallel 4"), 0),
            (C("N-T10"), T("ctest --parallel=4"), 0), (C("N-T11"), T("make -j 4"), 0), (C("N-T12"), T("make -j4"), 0),
            (C("N-T13"), T("make --jobs 4"), 0), (C("N-T14"), T("make --jobs=4"), 0), (C("N-T15"), T("gmake -j 4"), 0),
            (C("N-T16"), T("gmake --jobs=4"), 0), (C("N-T17"), T("ninja -j 4"), 0), (C("N-T18"), T("ninja -j4"), 0),
            (C("N-T19"), T("make -j 1"), 0),
        ])

    def test_no_count_refused(self) -> None:
        self.check([
            (C("N-T20"), T("run-clang-tidy -p b"), 2), (C("N-T21"), T("run-clang-tidy.py -p b"), 2),
            (C("N-T22"), T("mutmut run"), 2), (C("N-T25"), T("ninja"), 2), (C("N-T24b"), T("gmake -j"), 2),
        ])

    def test_one_job_by_default(self) -> None:
        """ctest, make and gmake given no count run one job and pass, unless a word may name a job option."""
        self.check([
            (C("N-CT10"), T("ctest --test-dir build --output-on-failure"), 0), (C("N-MK12"), T("make"), 0),
            (C("N-T23"), T("ctest"), 0), (C("N-T24"), T("gmake"), 0), (C("N-1J1"), T("make --version"), 0),
            (C("N-1J2"), T("ctest -t compdb"), 0), (C("N-1J3"), T("make DESTDIR=$D install"), 0),
            (C("N-1J4"), T("ctest -R x"), 0), (C("N-1J13"), T("ctest --output-junit r.xml"), 0), (C("N-1J14"), T("make objs"), 0),
            (C("N-1J5"), T("make $FLAGS"), 2), (C("N-1J6"), T("make -sj"), 2), (C("N-1J7"), T("gmake -sj4"), 2),
            (C("N-1J8"), T("ctest --parallel"), 2), (C("N-1J9"), T("make --jobs"), 2), (C("N-1J10"), T("ctest -j0"), 2),
            (C("N-1J11"), T("make --jobsx"), 2), (C("N-1J12"), T("ctest --parallelx 4"), 2),
        ])

    def test_another_tools_form_refused(self) -> None:
        self.check([
            (C("N-T26"), T("run-clang-tidy-23 --parallel 4"), 2), (C("N-T27"), T("run-clang-tidy-23 --jobs=4"), 2),
            (C("N-T28"), T("mutmut run -j 4"), 2), (C("N-T29"), T("mutmut run -j4"), 2),
            (C("N-T30"), T("ctest --jobs 4"), 2), (C("N-T31"), T("ctest --jobs=4"), 2),
            (C("N-T32"), T("make --parallel 4"), 2), (C("N-T33"), T("make -j=4"), 2),
            (C("N-T34"), T("ninja -j=4"), 2), (C("N-T35"), T("ninja --jobs 4"), 2), (C("N-T36"), T("ninja --jobs=4"), 2),
            (C("N-T37"), T("ninja --parallel 4"), 2), (C("N-T38"), T("gmake --parallel=4"), 2),
        ])

    def test_a_literal_count(self) -> None:
        self.check([
            (C("N-T39"), T("make -j 4 -j$N"), 2), (C("N-T40"), T("make -j +4"), 2), (C("N-T41"), T("ninja -j4x"), 2),
            (C("N-T42"), T("ctest -j=$N"), 2), (C("N-T43"), T("ninja -j 4 -j $N"), 2),
            (C("N-T44"), T("mutmut run --max-children 4 --max-children"), 2),
        ])


class Ctest(Fixture):
    """ctest with a count past the bound, none, or one the call does not make known is refused."""

    def test_refused(self) -> None:
        self.check([
            (C("N-CT1"), T("ctest -j"), 2), (C("N-CT2"), T("ctest --parallel"), 2), (C("N-CT3"), T("ctest -j0"), 2),
            (C("N-CT4"), T("ctest --test-dir build -j 24"), 2), (C("N-CT5"), T("ctest -j -V"), 2),
            (C("N-CT6"), T("ctest -j 4 -j"), 2), (C("N-CT7"), T("ctest -j $(nproc)"), 2),
            (C("N-CT8"), T("ctest --parallel=30"), 2), (C("N-CT9"), T("ctest -j=0"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("N-CT11"), T("ctest -j 8"), 0), (C("N-CT12"), T("ctest -j8"), 0), (C("N-CT13"), T("ctest -j=8"), 0),
            (C("N-CT14"), T("ctest --parallel 8"), 0), (C("N-CT15"), T("ctest --parallel=8"), 0),
            (C("N-CT16"), T("ctest -j -j 4"), 0),
        ])


class Make(Fixture):
    """make with a count past the bound, none, or one the call does not make known is refused."""

    def test_refused(self) -> None:
        self.check([
            (C("N-MK1"), T("make -j"), 2), (C("N-MK2"), T("make --jobs"), 2), (C("N-MK3"), T("make -sj"), 2),
            (C("N-MK4"), T("make -j all"), 2), (C("N-MK5"), T("make -j=4"), 2), (C("N-MK6"), T("make -j 21"), 2),
            (C("N-MK7"), T("make -j4 -j"), 2), (C("N-MK8"), T("make -j $N"), 2), (C("N-MK9"), T("make -j0"), 2),
            (C("N-MK10"), T("make -C build -j"), 2), (C("N-MK11"), T("make --jobs=$N"), 2),
            (C("N-MK23"), T("make -sj$N 4"), 2), (C("N-MK24"), T("make -j ²"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("N-MK13"), T("make -j8"), 0), (C("N-MK14"), T("make -j 8 all"), 0), (C("N-MK15"), T("make --jobs=8"), 0),
            (C("N-MK16"), T("make --jobs 8"), 0), (C("N-MK21"), T("make -j -j 4"), 0),
        ])


class Ninja(Fixture):
    """ninja with a count past the bound, none, or one the call does not make known is refused."""

    def test_refused(self) -> None:
        self.check([
            (C("N-NJ1"), T("ninja"), 2), (C("N-NJ2"), T("ninja -C build"), 2), (C("N-NJ3"), T("ninja -j=8"), 2),
            (C("N-NJ4"), T("ninja -j0"), 2), (C("N-NJ5"), T("ninja -j 21"), 2), (C("N-NJ6"), T("ninja -j 8 -j 30"), 2),
            (C("N-NJ8"), T("ninja -vj$N"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("N-NJ9"), T("ninja -j 8"), 0), (C("N-NJ10"), T("ninja -j8"), 0), (C("N-NJ14"), T("ninja -j 30 -j 8"), 0),
        ])


class Mutmut(Fixture):
    """mutmut run is refused without --max-children within the bound; mutmut's other commands start no child."""

    def test_refused(self) -> None:
        self.check([
            (C("N-MM1"), T("mutmut run"), 2), (C("N-MM2"), T("python -m mutmut run"), 2),
            (C("N-MM3"), T("mutmut run --max-children 24"), 2), (C("N-MM4"), T("mutmut run --max-children 0"), 2),
            (C("N-MM5"), T("PYTHON_CPU_COUNT=4 mutmut run --max-children 0"), 2),
            (C("N-MM6"), T("python3 -mmutmut run"), 2), (C("N-MM7"), T("mutmut run --max-children=$N"), 2),
        ])

    def test_passed(self) -> None:
        self.check([
            (C("N-MM14"), T("mutmut results -j"), 0),
            (C("N-MM8"), T("mutmut run --max-children 8"), 0), (C("N-MM9"), T("mutmut run --max-children=8"), 0),
            (C("N-MM11"), T("mutmut results"), 0), (C("N-MM12"), T(".venv/bin/python -m mutmut run --max-children 4"), 0),
            (C("N-MM13"), T("python3 -mmutmut run --max-children 4"), 0),
        ])


class StartsNoJob(Fixture):
    """The exceptions: a tool given -h or --help, and a ninja given -t or --version, start no job and pass; no other
    tool's --version or -t does."""

    def test_help(self) -> None:
        self.check([
            (C("N-H1"), T("run-clang-tidy-23 --help 2>&1 | sed -n '1,80p'"), 0), (C("N-H2"), T("run-clang-tidy-23 -h"), 0),
            (C("N-H3"), T("mutmut run --help"), 0), (C("N-H4"), T("ninja -h"), 0), (C("N-H5"), T("make -j --help"), 0),
            (C("N-H6"), T("ctest -j -h"), 0), (C("N-H7"), T("gmake --help"), 0),
        ])

    def test_ninja_subtool_and_version(self) -> None:
        self.check([
            (C("N-NJ12"), T("ninja -C build -t compdb"), 0), (C("N-NJ13"), T("ninja --version"), 0),
            (C("N-NJ15"), T("ninja -tcompdb"), 2), (C("N-NJ16"), T("run-clang-tidy-23 --version"), 2),
            (C("N-NJ17"), T("mutmut run -t compdb"), 2),
        ])


class WithinTheAffinity(Fixture):
    """cargo, go test, gremlins and mull-runner are no tools of the table."""

    def test_passed(self) -> None:
        self.check([
            (C("N-AF1"), T("cargo build --release"), 0), (C("N-AF2"), T("cargo test"), 0),
            (C("N-AF3"), T("go test ./aletheia/ -race"), 0), (C("N-AF4"), T("gremlins unleash ./aletheia"), 0),
            (C("N-AF5"), T("mull-runner-23 ./unit_tests"), 0),
        ])


class Reach(Fixture):
    """A tool is judged past the model's wrappers, in its carriers, its substitutions and its subshells, on either
    shell tool, and through a Python interpreter's options."""

    def test_wrappers_and_carriers(self) -> None:
        self.check([
            (C("N-R1"), T("taskset -c 0-19 make -j"), 2), (C("N-R2"), T("timeout 60 ninja"), 2),
            (C("N-R3"), T("rtk proxy make -j"), 2), (C("N-R4"), T("nohup ctest -j > log 2>&1 &"), 2),
            (C("N-R5"), T("sh -c 'make -j'"), 2), (C("N-R6"), T("eval 'make -j'"), 2),
            (C("N-R7"), T("(cd cpp && make -j)"), 2), (C("N-R8"), T("/usr/bin/make -j"), 2),
            (C("N-R19"), T("'X=1' make -j"), 0), (C("N-R20"), T("taskset -c 0-19 make -j 4"), 0),
        ])

    def test_monitor(self) -> None:
        self.check([(C("N-R9"), T("make -j"), 2), (C("N-R10"), T("make -j 4"), 0)], tool=T("Monitor"))

    def test_python_options(self) -> None:
        self.check([
            (C("N-R11"), T("python3 -u /usr/bin/run-clang-tidy-23 -p b"), 2),
            (C("N-R12"), T("python3 -X dev /usr/bin/run-clang-tidy-23 -p b"), 2),
            (C("N-R13"), T("python3 -Xdev /usr/bin/run-clang-tidy-23 -p b"), 2),
            (C("N-R14"), T("python3 -c run-clang-tidy-23 -p b"), 0),
            (C("N-R15"), T("python3 -W ignore /usr/bin/run-clang-tidy-23 -j 4 -p b"), 0),
            (C("N-R16"), T("python3 -- /usr/bin/run-clang-tidy-23 -p b"), 2),
            (C("N-R17"), T("python3 - run-clang-tidy-23 < tidy.py"), 0),
            (C("N-R18"), T("python3 --check-hash-based-pycs never /usr/bin/run-clang-tidy-23 -p b"), 2),
        ])


class Environment(Fixture):
    """PYTHON_CPU_COUNT is not read: a tool with no count is refused whatever the hook's environment, an export, an
    assignment or an env wrapper gives it."""

    def test_the_hook_environment(self) -> None:
        self.check([(C("N-P1"), T("run-clang-tidy-23 -p b"), 2), (C("N-P2"), T("mutmut run"), 2),
                    (C("N-P4"), T("PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p b"), 2)], env=FIVE)
        self.check([(C("N-P3"), T("run-clang-tidy-23 -p b"), 2)], env={VarName("PYTHON_CPU_COUNT"): T("30")})

    def test_the_call(self) -> None:
        self.check([
            (C("N-P5"), T("(export PYTHON_CPU_COUNT=5); run-clang-tidy-23 -p b"), 2),
            (C("N-P6"), T("export PYTHON_CPU_COUNT=5; bash -c 'run-clang-tidy-23 -p b'"), 2),
            (C("N-P7"), T("export PYTHON_CPU_COUNT=5; PYTHON_CPU_COUNT=30 run-clang-tidy-23 -p b"), 2),
            (C("N-P8"), T("export PYTHON_CPU_COUNT=30; export PYTHON_CPU_COUNT=5; run-clang-tidy-23 -p b"), 2),
            (C("N-P9"), T("run-clang-tidy-23 -p b; export PYTHON_CPU_COUNT=5"), 2),
            (C("N-P10"), T("env PYTHON_CPU_COUNT=5 PYTHON_CPU_COUNT=30 run-clang-tidy-23 -p b"), 2),
            (C("N-P11"), T("PYTHON_CPU_COUNT=30 env PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p b"), 2),
            (C("N-P12"), T("env PYTHON_CPU_COUNT=$N run-clang-tidy-23 -p b"), 2),
            (C("N-P13"), T("PYTHON_CPU_COUNT=$N run-clang-tidy-23 -p b"), 2),
            (C("N-P14"), T("taskset -c 0-19 env PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p b"), 2),
            (C("N-P15"), T("env PYTHON_CPU_COUNT=5 mutmut run"), 2),
            (C("N-P16"), T("env A=$X PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p b"), 2),
            (C("N-P17"), T("env A=$X B=1 make -j"), 2),
            (C("N-P18"), T("env PYTHON_CPU_COUNT=30 run-clang-tidy-23 -p b PYTHON_CPU_COUNT=5"), 2),
            (C("N-P19"), T("export PYTHON_CPU_COUNT=30 PYTHON_CPU_COUNT=5; run-clang-tidy-23 -p b"), 2),
            (C("N-P32"), T("PYTHON_CPU_COUNT=5 bash tidy.sh"), 2),
            (C("N-P33"), T("PYTHON_CPU_COUNT=5 bash -c 'run-clang-tidy-23 -p b'"), 2),
            (C("N-P34"), T("taskset -c 0-19 env PYTHON_CPU_COUNT=20 bash tidy.sh"), 2),
            (C("N-P35"), T("PYTHON_CPU_COUNT=5 bash -c 'export PYTHON_CPU_COUNT=24; run-clang-tidy-23 -p b'"), 2),
            (C("N-P36"), T("PYTHON_CPU_COUNT=5 bash tidy_bound.sh"), 0),
        ])

    def test_env_empties_and_unsets(self) -> None:
        self.check([
            (C("N-P20"), T("env -i run-clang-tidy-23 -p b"), 2), (C("N-P21"), T("env - mutmut run"), 2),
            (C("N-P22"), T("env -u PYTHON_CPU_COUNT run-clang-tidy-23 -p b"), 2),
            (C("N-P23"), T("env -u \"$X\" run-clang-tidy-23 -p b"), 2),
            (C("N-P24"), T("env -i PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p b"), 2),
            (C("N-P25"), T("env -u PYTHON_CPU_COUNT PYTHON_CPU_COUNT=5 run-clang-tidy-23 -p b"), 2),
            (C("N-P26"), T("env -u A run-clang-tidy-23 -p b"), 2),
        ], env=FIVE)
        self.check([
            (C("N-P27"), T("export PYTHON_CPU_COUNT=5; env -i run-clang-tidy-23 -p b"), 2),
            (C("N-P28"), T("PYTHON_CPU_COUNT=5 env -u PYTHON_CPU_COUNT run-clang-tidy-23 -p b"), 2),
            (C("N-P29"), T("PYTHON_CPU_COUNT=5 env -i run-clang-tidy-23 -p b"), 2),
            (C("N-P30"), T("export PYTHON_CPU_COUNT=5; env -u A run-clang-tidy-23 -p b"), 2),
            (C("N-P31"), T("env -u \"$X\" run-clang-tidy-23 -p b"), 2),
        ])


if __name__ == "__main__":
    unittest.main()
