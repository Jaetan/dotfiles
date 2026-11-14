"""What one claude process's hooks remember across its calls, its agents' calls included, kept on persistent disk.

THE NEED (user rulings 2026-11-07). The whole session is held alike, main thread, subagents and workflow agents, so a
rule that must remember a live run, watch or workflow from one call to the next keeps it where every hook of the session
finds it, and so does a rule that gives a note once per process, as a notice that it has: a kind of its own, so a rule
reading the live kinds never meets one. A hook runs as hook, sh, claude, the same claude for the main thread and its
agents, with CLAUDE_PID in its environment, and a /clear changes session_id while that process, its tasks and its agents
go on. So the state is keyed by the claude process: its pid, its start time from /proc/PID/stat, and the boot id, since
the disk outlives a reboot and a pid can recur at the same tick count in the next boot. Nothing is kept in /tmp, which
is RAM.

THE LAYOUT. Under SESSIONS a folder per process, named PID-START-BOOT (the boot id without its dashes), holds
  main.json        the main thread's records,
  agent-HEX.json   an agent's records, HEX its agent_id's UTF-8 bytes in hex,
  shared.json      records any agent of the process may forget,
each with a NAME.lock beside it. A file changes only under an flock on its lock, since one agent's parallel calls run
their hooks at once, and is written whole to a temporary file in the folder, then renamed over it: a reader takes no
lock and sees the old state or the new one, never a torn one. A file holds {"records": [ROW, ...]}, each ROW
{"kind": KIND, "id": ID, "fields": {NAME: VALUE, ...}, "agent": AGENT_ID or null}; one of any other shape, or one
that cannot be read, raises StateFault, a KitFault, so the call a rule was judging is refused naming it.

THE API. Session.of(event) is the state of the event's claude process as the event's agent (its agent_id, absent for the
main thread) keeps it. record() keeps a run, a watch, a workflow or a notice under its id, replacing one of that kind
and id, in the agent's own file or the shared one; forget() removes one; records(kind) reads every file of the process,
so any agent reads every agent's records. sweep(), run at SessionStart, removes the folder of each process that has
ended: of another boot, its pid gone, or its start time changed. A folder whose name no key gives, or whose process
cannot be read, is kept. """

import contextlib
import fcntl
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal, NamedTuple, NewType, TypedDict, get_args

from dispatcher.core import AgentId, Event
from dispatcher.verdict import KitFault, Prose
from dispatcher.words import Environment, VarName

Kind = Literal["run", "watch", "workflow", "notice"]
KINDS: frozenset[Kind] = frozenset(get_args(Kind))
RecordId = NewType("RecordId", str)
FieldName = NewType("FieldName", str)
FieldValue = NewType("FieldValue", str)
Pid = NewType("Pid", int)
StartTicks = NewType("StartTicks", int)
BootId = NewType("BootId", str)
FileStem = NewType("FileStem", str)

SESSIONS = Path.home() / ".local" / "state" / "claude-guard" / "sessions"
PROC = Path("/proc")
MAIN = FileStem("main")
SHARED = FileStem("shared")


class Places(NamedTuple):
    """Where the state lives and where processes are read: the sessions folder and the proc file system."""

    sessions: Path = SESSIONS
    proc: Path = PROC


LIVE = Places()


class ProcessKey(NamedTuple):
    """A claude process, told apart from every other of any boot: the boot id, its pid and its start time."""

    boot: BootId
    pid: Pid
    start: StartTicks

    @property
    def name(self) -> FileStem:
        """The name of the process's folder."""
        return FileStem(f"{self.pid}-{self.start}-{self.boot}")


class Record(NamedTuple):
    """A live run, watch or workflow, or a notice given: its kind, its id, what a rule keeps about it, and the agent
    that recorded it, None for the main thread."""

    kind: Kind
    ident: RecordId
    fields: Mapping[FieldName, FieldValue]
    agent: AgentId | None


class Row(TypedDict):
    """A record as a state file holds it."""

    kind: Kind
    id: RecordId
    fields: dict[FieldName, FieldValue]
    agent: AgentId | None


class Stored(TypedDict):
    """A state file."""

    records: list[Row]


class StateFault(KitFault):
    """A session state that cannot be keyed or read, which refuses the call a rule was judging."""


def boot_of(proc: Path) -> BootId:
    """This boot's id, without its dashes."""
    try:
        text = (proc / "sys" / "kernel" / "random" / "boot_id").read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as fault:
        raise StateFault(Prose(f"the boot id cannot be read: {fault}")) from None
    return BootId(text.strip().replace("-", ""))


def started(pid: Pid, proc: Path) -> StartTicks | None:
    """The start time of process ``pid`` in clock ticks since boot, None where no such process runs; an OSError where
    its stat file cannot be read, StateFault where it is not a stat file."""
    try:
        text = (proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        return None
    try:  # the command name, in parentheses, may hold spaces and parentheses; the fields after it start at field 3
        return StartTicks(int(text[text.rindex(")") + 1:].split()[19]))
    except (ValueError, IndexError):
        raise StateFault(Prose(f"the stat file of process {pid} gives no start time")) from None


def process_key(env: Environment, proc: Path) -> ProcessKey:
    """The key of the claude process CLAUDE_PID names; StateFault where it names no process the kit can read."""
    spelled = env.get(VarName("CLAUDE_PID"), "")
    if not (spelled.isascii() and spelled.isdigit()):
        raise StateFault(Prose(f"CLAUDE_PID is {spelled!r}, not a process id, so the session state has no key"))
    pid = Pid(int(spelled))
    try:
        start = started(pid, proc)
    except OSError as fault:
        raise StateFault(Prose(f"process {pid}, CLAUDE_PID, cannot be read: {fault}")) from None
    if start is None:
        raise StateFault(Prose(f"CLAUDE_PID {pid} names no running process, so the session state has no key"))
    return ProcessKey(boot_of(proc), pid, start)


def key_of(name: FileStem) -> ProcessKey | None:
    """The key whose folder ``name`` is, None for a name no key gives."""
    try:
        pid, start, boot = name.split("-")
        key = ProcessKey(BootId(boot), Pid(int(pid)), StartTicks(int(start)))
    except ValueError:  # not three parts, or a pid or a start time that is no number
        return None
    return key if key.name == name and boot.isascii() and boot.isalnum() else None


def ended(key: ProcessKey, boot: BootId, proc: Path) -> bool:
    """Whether the process ``key`` names has ended: another boot's, its pid gone, or its start time changed; one whose
    stat file cannot be read has not."""
    if key.boot != boot:
        return True
    try:
        return started(key.pid, proc) != key.start
    except (OSError, StateFault):
        return False


def sweep(places: Places = LIVE) -> tuple[ProcessKey, ...]:
    """Remove the folder of every process that has ended, keeping every other; the keys removed."""
    boot = boot_of(places.proc)
    try:
        names = sorted(os.listdir(places.sessions))
    except FileNotFoundError:
        return ()
    removed: list[ProcessKey] = []
    for name in names:
        key = key_of(FileStem(name))
        if key is not None and ended(key, boot, places.proc):
            shutil.rmtree(places.sessions / name, ignore_errors=True)
            removed.append(key)
    return tuple(removed)


def well_formed(row: Row) -> bool:
    """Whether a state file's row is a record."""
    if not isinstance(row, dict):
        return False
    fields = row.get("fields")
    return (row.get("kind") in KINDS and isinstance(row.get("id"), str) and isinstance(fields, dict)
            and all(isinstance(value, str) for value in fields.values())
            and "agent" in row and (row["agent"] is None or isinstance(row["agent"], str)))


def rows_in(path: Path) -> list[Row]:
    """The rows the state file ``path`` holds, none where it is missing; StateFault where it is no state file."""
    try:
        stored: Stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as fault:  # a text not UTF-8 is a ValueError too
        raise StateFault(Prose(f"the session state {path} cannot be read: {fault}")) from None
    rows = stored.get("records") if isinstance(stored, dict) else None
    if not isinstance(rows, list) or not all(well_formed(row) for row in rows):
        raise StateFault(Prose(f"the session state {path} is not a list of records"))
    return rows


def written(path: Path, stored: Stored) -> None:
    """Write ``stored`` whole to a temporary file beside ``path``, then rename it over ``path``."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(stored, out)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive flock on the lock file ``path``, made where it is missing."""
    handle = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        os.close(handle)


class Session:
    """The state of one claude process, as one of its agents, or its main thread, keeps and reads it."""

    __slots__ = ("agent", "folder")

    def __init__(self, places: Places, process: ProcessKey, agent: AgentId | None) -> None:
        self.folder = places.sessions / process.name
        self.agent = agent

    @classmethod
    def of(cls, event: Event, places: Places = LIVE) -> "Session":
        """The state of the claude process ``event``'s hook runs under, as the event's agent keeps it."""
        agent = event.payload.get("agent_id")
        return cls(places, process_key(event.reading.env, places.proc),
                   agent if isinstance(agent, str) and agent else None)

    def stem(self, shared: bool) -> FileStem:
        """The name, without its suffix, of the file this agent writes: its own, or the shared one."""
        if shared:
            return SHARED
        if self.agent is None:
            return MAIN
        return FileStem("agent-" + self.agent.encode().hex())

    @contextlib.contextmanager
    def changing(self, stem: FileStem) -> Iterator[list[Row]]:
        """The rows of the file ``stem``, under its lock, written back when the block ends without raising."""
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{stem}.json"
        with locked(self.folder / f"{stem}.lock"):
            rows = rows_in(path)
            yield rows
            written(path, Stored(records=rows))

    def record(self, kind: Kind, ident: RecordId, fields: Mapping[FieldName, FieldValue], *,
               shared: bool = False) -> None:
        """Keep a live ``kind`` under ``ident``, replacing one of that kind and id, in this agent's file or the
        shared one."""
        with self.changing(self.stem(shared)) as rows:
            rows[:] = [row for row in rows if (row["kind"], row["id"]) != (kind, ident)]
            rows.append(Row(kind=kind, id=ident, fields=dict(fields), agent=self.agent))

    def forget(self, kind: Kind, ident: RecordId, *, shared: bool = False) -> bool:
        """Remove the ``kind`` kept under ``ident`` from this agent's file or the shared one; whether one was kept."""
        with self.changing(self.stem(shared)) as rows:
            before = len(rows)
            rows[:] = [row for row in rows if (row["kind"], row["id"]) != (kind, ident)]
            return len(rows) != before

    def records(self, kind: Kind) -> tuple[Record, ...]:
        """Every record of ``kind`` the process keeps, in its main thread's, its agents' and the shared file."""
        try:
            names = sorted(os.listdir(self.folder))
        except FileNotFoundError:
            return ()
        return tuple(Record(row["kind"], row["id"], row["fields"], row["agent"])
                     for name in names if name.endswith(".json")
                     for row in rows_in(self.folder / name) if row["kind"] == kind)
