"""The session state: keyed by the claude process and kept across a /clear, a file per agent and a shared one, every
record readable by every agent of the process, an unreadable state refused, the state of an ended process swept, and
many writers at once losing no record and tearing no read.

Every case keeps its state in a sessions folder of its own under the temporary folder, and most read processes from a
proc tree of their own, whose boot_id and PID/stat files the case writes; the cases on real processes read /proc.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import NewType
from unittest import mock

from dispatcher.core import AgentId, Event, Payload, SessionId
from dispatcher.model import ReadSettings
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.verdict import KitFault, Prose
from dispatcher.words import Environment, VarName
from state.session import (
    LIVE,
    PROC,
    BootId,
    FieldName,
    FieldValue,
    Pid,
    Places,
    ProcessKey,
    Record,
    RecordId,
    Session,
    StartTicks,
    StateFault,
    process_key,
    sweep,
)

KIT = Path(__file__).resolve().parents[1]
Name = NewType("Name", str)
StatText = NewType("StatText", str)
StateBytes = NewType("StateBytes", bytes)
SourceText = NewType("SourceText", str)
Count = NewType("Count", int)

BOOT = Prose("bbbb-0001")
COMM = Prose("claude")
FIRST = Prose("s1")
B = BootId("bbbb0001")
KEY = ProcessKey(B, Pid(10), StartTicks(100))
NONE: Mapping[FieldName, FieldValue] = {}
WRITERS = 8
STEPS = 100
WORKER = SourceText("""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from state.session import BootId, Pid, Places, ProcessKey, RecordId, Session, StartTicks, StateFault

sessions, agent, label, steps, shared = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6] == "1"
session = Session(Places(Path(sessions)), ProcessKey(BootId("b"), Pid(1), StartTicks(1)), agent)
torn = drops = unseen = seen = 0
for step in range(steps):
    ident = RecordId(f"{label}-{step}")
    session.record("run", ident, {}, shared=shared)
    try:
        found = session.records("run")
    except StateFault:
        torn += 1
        continue
    drops += len(found) < seen
    seen = len(found)
    unseen += all(record.ident != ident for record in found)
print(json.dumps({"torn": torn, "drops": drops, "unseen": unseen}))
""")


def stat_line(pid: Pid, start: StartTicks, comm: Prose = COMM) -> StatText:
    """A /proc/PID/stat line whose field 22, the start time, is ``start``, every other field its own number."""
    after = ["S", *(str(field) for field in range(4, 53))]
    after[19] = str(start)
    return StatText(f"{pid} ({comm}) {' '.join(after)}\n")


def scratch(case: unittest.TestCase) -> Path:
    """A fresh folder for one case, removed after it."""
    base = Path(tempfile.mkdtemp()).resolve()
    case.addCleanup(shutil.rmtree, base, ignore_errors=True)
    return base


def proc_tree(base: Path, boot: Prose | None, stats: Mapping[Pid, StatText]) -> Path:
    """A proc tree under ``base``: the boot id ``boot`` (none where None) and a stat file per process."""
    proc = base / "proc"
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    if boot is not None:
        _ = (proc / "sys" / "kernel" / "random" / "boot_id").write_text(boot + "\n", encoding="ascii")
    for pid, text in stats.items():
        (proc / str(pid)).mkdir()
        _ = (proc / str(pid) / "stat").write_text(text, encoding="utf-8")
    return proc


def claude_pid(pid: Pid | Prose) -> Environment:
    """A hook environment naming ``pid`` as CLAUDE_PID."""
    return {VarName("CLAUDE_PID"): ShellText(str(pid))}


def hook_event(env: Environment, agent: Prose | None = None, session: Prose = FIRST) -> Event:
    """A PreToolUse event of session ``session`` from ``agent`` (the main thread where None), its hook in ``env``."""
    payload: Payload = {"hook_event_name": "PreToolUse", "session_id": SessionId(session)}
    if agent is not None:
        payload["agent_id"] = AgentId(agent)
    return Event(payload, ReadSettings(ReaderPath(""), Seconds(1.0), env))


def as_agent(places: Places, agent: Prose | None, key: ProcessKey = KEY) -> Session:
    """The state of process ``key`` as ``agent`` keeps it."""
    return Session(places, key, None if agent is None else AgentId(agent))


def rec(kind: Prose, ident: Prose, agent: Prose | None = None,
        fields: Mapping[FieldName, FieldValue] = NONE) -> Record:
    """A record as records() gives it."""
    return Record(kind, RecordId(ident), fields, None if agent is None else AgentId(agent))


class Keys(unittest.TestCase):
    """The key is the claude process CLAUDE_PID names: its boot, its pid, its start time; nothing else."""

    def test_the_key_reads_the_named_process(self) -> None:
        proc = proc_tree(scratch(self), Prose("aaaa-bbbb"), {Pid(4242): stat_line(Pid(4242), StartTicks(777),
                                                                                  Prose("cl) (au de"))})
        self.assertEqual(process_key(claude_pid(Pid(4242)), proc),
                         ProcessKey(BootId("aaaabbbb"), Pid(4242), StartTicks(777)))

    def test_the_key_of_a_real_process(self) -> None:
        key = process_key(claude_pid(Pid(os.getpid())), PROC)
        self.assertEqual(key.pid, os.getpid())
        self.assertGreater(key.start, 0)
        self.assertEqual((len(key.boot), set(key.boot) <= set("0123456789abcdef")), (32, True))

    def test_no_key(self) -> None:
        base = scratch(self)
        proc = proc_tree(base, BOOT, {Pid(10): stat_line(Pid(10), StartTicks(100)),
                                      Pid(13): StatText("13 (claude)\n"), Pid(14): stat_line(Pid(14), StartTicks(1)),
                                      Pid(15): StatText("15 claude S 1\n")})
        (proc / "14" / "stat").chmod(0)
        cases: dict[Prose, tuple[Environment, Path]] = {
            Prose("no CLAUDE_PID"): ({}, proc),
            Prose("an empty CLAUDE_PID"): (claude_pid(Prose("")), proc),
            Prose("a CLAUDE_PID that is no number"): (claude_pid(Prose("12a")), proc),
            Prose("a CLAUDE_PID of a digit not ASCII"): (claude_pid(Prose("²")), proc),
            Prose("a CLAUDE_PID naming no process"): (claude_pid(Pid(99)), proc),
            Prose("a stat file giving no start time"): (claude_pid(Pid(13)), proc),
            Prose("a stat file giving no command name"): (claude_pid(Pid(15)), proc),
            Prose("a stat file that cannot be read"): (claude_pid(Pid(14)), proc),
            Prose("no boot id"): (claude_pid(Pid(10)), proc_tree(base / "noboot", None,
                                                                 {Pid(10): stat_line(Pid(10), StartTicks(100))})),
        }
        for label, (env, where) in cases.items():
            with self.subTest(label), self.assertRaises(StateFault):
                _ = process_key(env, where)
        _ = (base / "noboot" / "proc" / "sys" / "kernel" / "random" / "boot_id").write_bytes(b"\xff\n")
        with self.subTest("a boot id not ASCII"), self.assertRaises(StateFault):
            _ = process_key(claude_pid(Pid(10)), base / "noboot" / "proc")
        self.assertTrue(issubclass(StateFault, KitFault))

    def test_two_processes_never_share_a_key(self) -> None:
        places = Places(scratch(self) / "sessions")
        keys = (KEY, KEY._replace(pid=Pid(11)), KEY._replace(start=StartTicks(101)), KEY._replace(boot=BootId("c")))
        self.assertEqual(len({key.name for key in keys}), len(keys))
        as_agent(places, None).record("run", RecordId("r"), NONE)
        self.assertEqual([as_agent(places, None, key).records("run") for key in keys[1:]], [(), (), ()])

    def test_a_clear_keeps_the_state(self) -> None:
        base = scratch(self)
        places = Places(base / "sessions", proc_tree(base, BOOT, {Pid(10): stat_line(Pid(10), StartTicks(100))}))
        Session.of(hook_event(claude_pid(Pid(10)), session=Prose("s1")), places).record("watch", RecordId("w"), NONE)
        after = Session.of(hook_event(claude_pid(Pid(10)), session=Prose("s2")), places)
        self.assertEqual(after.records("watch"), (rec(Prose("watch"), Prose("w")),))

    def test_the_default_places(self) -> None:
        self.assertEqual(LIVE, Places(Path.home() / ".local" / "state" / "claude-guard" / "sessions", Path("/proc")))


class Layout(unittest.TestCase):
    """A folder per process, a file per agent and a shared one, each written whole and renamed into place."""

    def test_one_file_per_agent(self) -> None:
        places = Places(scratch(self) / "sessions")
        as_agent(places, None).record("run", RecordId("m"), NONE)
        as_agent(places, Prose("a1")).record("run", RecordId("x"), NONE)
        as_agent(places, Prose("a/b")).record("run", RecordId("y"), NONE)
        as_agent(places, Prose("a1")).record("run", RecordId("s"), NONE, shared=True)
        folder = places.sessions / "10-100-bbbb0001"
        self.assertEqual(os.listdir(places.sessions), ["10-100-bbbb0001"])
        self.assertEqual(sorted(os.listdir(folder)), ["agent-612f62.json", "agent-612f62.lock", "agent-6131.json",
                                                      "agent-6131.lock", "main.json", "main.lock", "shared.json",
                                                      "shared.lock"])
        held = {name: json.loads((folder / name).read_text(encoding="utf-8"))
                for name in ("main.json", "agent-6131.json", "shared.json")}
        self.assertEqual(held, {
            "main.json": {"records": [{"kind": "run", "id": "m", "fields": {}, "agent": None}]},
            "agent-6131.json": {"records": [{"kind": "run", "id": "x", "fields": {}, "agent": "a1"}]},
            "shared.json": {"records": [{"kind": "run", "id": "s", "fields": {}, "agent": "a1"}]},
        })

    def test_a_write_renames_a_temporary_file_from_the_folder(self) -> None:
        places = Places(scratch(self) / "sessions")
        renames: list[tuple[Path, Path]] = []
        real = os.replace

        def spy(source: Path, target: Path) -> None:
            renames.append((Path(source), Path(target)))
            real(source, target)

        with mock.patch("state.session.os.replace", spy):
            as_agent(places, None).record("run", RecordId("r"), NONE)
        [(source, target)] = renames
        self.assertEqual((source.parent, target, source.suffix), (target.parent,
                                                                   places.sessions / KEY.name / "main.json", ".tmp"))

    def test_a_failed_write_leaves_no_temporary_file(self) -> None:
        places = Places(scratch(self) / "sessions")
        session = as_agent(places, None)
        session.record("run", RecordId("kept"), NONE)
        with mock.patch("state.session.os.replace", side_effect=OSError("planted")), self.assertRaises(OSError):
            session.record("run", RecordId("lost"), NONE)
        self.assertEqual(sorted(os.listdir(places.sessions / KEY.name)), ["main.json", "main.lock"])
        self.assertEqual(session.records("run"), (rec(Prose("run"), Prose("kept")),))


class Records(unittest.TestCase):
    """What rules record and read: by kind, every agent's, an own file and a shared one."""

    def setUp(self) -> None:
        self.places = Places(scratch(self) / "sessions")
        self.main, self.a1, self.a2 = (as_agent(self.places, agent) for agent in (None, Prose("a1"), Prose("a2")))

    def test_a_fresh_process_keeps_nothing(self) -> None:
        self.assertEqual(self.main.records("run"), ())
        self.assertFalse(self.places.sessions.exists())

    def test_every_agent_reads_every_agent(self) -> None:
        log = {FieldName("log"): FieldValue("/x")}
        self.main.record("run", RecordId("m"), log)
        self.a1.record("run", RecordId("x"), NONE)
        self.a2.record("run", RecordId("y"), NONE)
        self.a1.record("run", RecordId("s"), NONE, shared=True)
        every = (rec(Prose("run"), Prose("x"), Prose("a1")), rec(Prose("run"), Prose("y"), Prose("a2")),
                 rec(Prose("run"), Prose("m"), None, log), rec(Prose("run"), Prose("s"), Prose("a1")))
        self.assertEqual([session.records("run") for session in (self.main, self.a1, self.a2)], [every] * 3)

    def test_records_by_kind(self) -> None:
        for kind in ("run", "watch", "workflow", "notice"):
            self.main.record(kind, RecordId(kind[0]), NONE)
        self.assertEqual([self.a1.records(kind) for kind in ("run", "watch", "workflow", "notice")],
                         [(rec(Prose(kind), Prose(kind[0])),) for kind in ("run", "watch", "workflow", "notice")])

    def test_a_record_replaces_its_kind_and_id(self) -> None:
        self.main.record("run", RecordId("r"), {FieldName("n"): FieldValue("1")})
        self.main.record("watch", RecordId("r"), NONE)
        self.main.record("run", RecordId("r"), {FieldName("n"): FieldValue("2")})
        self.assertEqual((self.main.records("run"), self.main.records("watch")),
                         ((rec(Prose("run"), Prose("r"), None, {FieldName("n"): FieldValue("2")}),),
                          (rec(Prose("watch"), Prose("r")),)))

    def test_forget(self) -> None:
        self.main.record("run", RecordId("r"), NONE)
        self.main.record("watch", RecordId("r"), NONE)
        self.a1.record("run", RecordId("r"), NONE)
        self.assertEqual((self.main.forget("run", RecordId("r")), self.main.forget("run", RecordId("r"))),
                         (True, False))
        self.assertEqual((self.main.records("run"), self.main.records("watch")),
                         ((rec(Prose("run"), Prose("r"), Prose("a1")),), (rec(Prose("watch"), Prose("r")),)))

    def test_the_shared_file(self) -> None:
        self.a1.record("workflow", RecordId("w"), NONE, shared=True)
        self.assertEqual(self.a1.forget("workflow", RecordId("w")), False)
        self.assertEqual(self.main.records("workflow"), (rec(Prose("workflow"), Prose("w"), Prose("a1")),))
        self.assertEqual(self.main.forget("workflow", RecordId("w"), shared=True), True)
        self.assertEqual(self.a2.records("workflow"), ())

    def test_the_event_agent(self) -> None:
        base = scratch(self)
        places = Places(base / "sessions", proc_tree(base, BOOT, {Pid(10): stat_line(Pid(10), StartTicks(100))}))
        agents = [Session.of(hook_event(claude_pid(Pid(10)), agent), places).agent
                  for agent in (None, Prose(""), Prose("a1"))]
        self.assertEqual(agents, [None, None, "a1"])


class Faults(unittest.TestCase):
    """A state file that is not one refuses every read and every write of the process, and stays as it was."""

    def test_an_unreadable_state_refuses(self) -> None:
        row = {"kind": "run", "id": "r", "fields": {"a": "b"}, "agent": None}
        cases = {Prose(label): StateBytes(held) for label, held in (
            ("not JSON", b"not json"),
            ("not UTF-8", b"\xff\xfe"),
            ("not an object", b"[]"),
            ("no records", b'{"rows": []}'),
            ("records not a list", b'{"records": {}}'),
            ("a row not an object", b'{"records": [1]}'),
            ("an unknown kind", json.dumps({"records": [{**row, "kind": "job"}]}).encode()),
            ("an id not a string", json.dumps({"records": [{**row, "id": 5}]}).encode()),
            ("fields not an object", json.dumps({"records": [{**row, "fields": []}]}).encode()),
            ("a field not a string", json.dumps({"records": [{**row, "fields": {"a": 1}}]}).encode()),
            ("no agent", json.dumps({"records": [{k: v for k, v in row.items() if k != "agent"}]}).encode()),
            ("an agent not a string", json.dumps({"records": [{**row, "agent": 5}]}).encode()),
        )}
        for label, held in cases.items():
            with self.subTest(label):
                places = Places(scratch(self))
                (places.sessions / KEY.name).mkdir()
                state = places.sessions / KEY.name / "main.json"
                _ = state.write_bytes(held)
                with self.assertRaises(StateFault):
                    _ = as_agent(places, Prose("a1")).records("run")
                with self.assertRaises(StateFault):
                    as_agent(places, None).record("run", RecordId("r"), NONE)
                self.assertEqual(state.read_bytes(), held)
        places = Places(scratch(self))
        (places.sessions / KEY.name / "main.json").mkdir(parents=True)
        with self.subTest("a folder in place of the file"), self.assertRaises(StateFault):
            _ = as_agent(places, None).records("run")


class Sweep(unittest.TestCase):
    """At SessionStart the folder of each ended process goes, and nothing else."""

    def test_the_sweep_removes_only_ended_processes(self) -> None:
        base = scratch(self)
        proc = proc_tree(base, BOOT, {Pid(10): stat_line(Pid(10), StartTicks(100)),
                                      Pid(12): stat_line(Pid(12), StartTicks(5)), Pid(13): StatText("13 (claude)\n")})
        (proc / "12" / "stat").chmod(0)
        sessions = base / "sessions"
        kept = [Name(n) for n in ("10-100-bbbb0001", "12-5-bbbb0001", "13-5-bbbb0001", "notakey", "011-100-bbbb0001",
                                  "11-100-", "11-100-bé")]
        gone = [Name(n) for n in ("10-100-cccc0002", "10-99-bbbb0001", "11-100-bbbb0001")]
        for name in kept + gone:
            (sessions / name).mkdir(parents=True)
            _ = (sessions / name / "main.json").write_text('{"records": []}', encoding="utf-8")
        self.assertEqual(sweep(Places(sessions, proc)),
                         (KEY._replace(boot=BootId("cccc0002")), KEY._replace(start=StartTicks(99)),
                          KEY._replace(pid=Pid(11))))
        self.assertEqual(sorted(os.listdir(sessions)), sorted(kept))
        self.assertEqual(os.listdir(sessions / "10-100-bbbb0001"), ["main.json"])

    def test_no_sessions_folder(self) -> None:
        base = scratch(self)
        self.assertEqual(sweep(Places(base / "sessions", proc_tree(base, BOOT, {}))), ())

    def test_a_real_process_is_kept_until_it_ends(self) -> None:
        places = Places(scratch(self) / "sessions", PROC)
        child = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        try:
            key = process_key(claude_pid(Pid(child.pid)), PROC)
            own = process_key(claude_pid(Pid(os.getpid())), PROC)
            for each in (key, own):
                Session(places, each, None).record("run", RecordId("r"), NONE)
            self.assertEqual(sweep(places), ())
        finally:
            _ = child.communicate()
        self.assertEqual((sweep(places), os.listdir(places.sessions)), ((key,), [own.name]))


class Contention(unittest.TestCase):
    """Many processes recording at once, into the shared file and into one agent's own, each reading every record
    after each write: no record is lost and no read is torn."""

    def test_many_writers_lose_nothing_and_tear_nothing(self) -> None:
        sessions = scratch(self) / "sessions"
        writers = [(f"s{n}", f"s{n}", "1") for n in range(WRITERS)] + [("a1", f"o{n}", "0") for n in range(WRITERS)]
        started = [subprocess.Popen([sys.executable, "-c", WORKER, str(KIT), str(sessions), agent, label, str(STEPS),
                                     shared], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                   for agent, label, shared in writers]
        ended = [(child.communicate(), child.returncode) for child in started]
        self.assertEqual([status for _, status in ended], [0] * len(writers),
                         "\n".join(err for (_, err), _ in ended))
        counts = [json.loads(out) for (out, _), _ in ended]
        final = Session(Places(sessions), ProcessKey(BootId("b"), Pid(1), StartTicks(1)), None).records("run")
        said = {"records": Count(len(final)), "distinct": Count(len({record.ident for record in final})),
                **{what: Count(sum(count[what] for count in counts)) for what in ("torn", "drops", "unseen")}}
        self.assertEqual(said, {"records": len(writers) * STEPS, "distinct": len(writers) * STEPS, "torn": 0,
                                "drops": 0, "unseen": 0})


if __name__ == "__main__":
    unittest.main()
