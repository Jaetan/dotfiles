"""A unit systemd-run starts lands in the machine's slice, under its memory and CPU caps.

THE RULE (user, 2026-10-10; the slice a machine fact, 2026-11-07; every word read, 2026-11-09). A user unit naming no
slice lands in app.slice, outside every cap. So a Bash or Monitor call is refused when a systemd-run it runs does not
spell a user unit in the machine's declared slice (Machine.slice); a machine declaring none holds nothing. A
systemd-run spells one when every one of its own words, those before the command it runs, is on the list below, they
hold --user with no --system after it, and they place the unit by --slice-inherit or by naming slices that are each
the declared slice or one beneath it. A slice is named by --slice=NAME, --property=Slice=NAME or -p Slice=NAME; NAME
gains `.slice` where it has none, and claude-WORD.slice is beneath claude.slice.

THE LIST, read from `systemd-run --help` (systemd 262): the options taking no value, as written, and the options
taking a value, as --name=VALUE, with -p VALUE and -E VALUE also as two words. Off it are -H, --host, -M, --machine,
-C and --capsule, which send the unit to another manager, and every other spelling: a value as the next word or
attached to a short option, an abbreviation, a bundle of short options, `--`.

Every systemd-run of the call model counts, found by its base name, wrappers peeled and carriers and scripts read, and
each is judged alone.

EXCEPTIONS:
  - a word the call does not make known, among the own words or as the command, refuses before anything is read: it
    may spell --system, another slice or an option off the list;
  - -h, --help and --version pass where every own word is on the list: they start no unit.
"""

from collections.abc import Sequence
from pathlib import Path

from declarations.schema import SliceName
from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import Command, Word
from dispatcher.tree import Count, ShellText
from dispatcher.verdict import Prose, Verdict, refuse
from dispatcher.wrappers import named

EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Monitor")})
SYSTEMD_RUN = ShellText("systemd-run")
FLAGS = named(ShellText(
    "-h --help --version --no-ask-password --user --system --scope --slice-inherit --no-block -r --remain-after-exit "
    "--wait --send-sighup -d --same-dir -R --same-root-dir -t --pty -T --pty-late -P --pipe -q --quiet -v --verbose "
    "-G --collect -S --shell --ignore-failure --no-pager --on-timezone-change --on-clock-change"))
VALUED = named(ShellText(
    "--unit --property --description --slice --expand-environment --service-type --uid --gid --nice "
    "--working-directory --root-directory --setenv --output --json --job-mode --background --path-property "
    "--socket-property --on-active --on-boot --on-startup --on-unit-active --on-unit-inactive --on-calendar "
    "--timer-property"))
PAIRED = {ShellText("-p"): ShellText("--property="), ShellText("-E"): ShellText("--setenv=")}
NO_UNIT = frozenset({ShellText("-h"), ShellText("--help"), ShellText("--version")})
MANAGERS = frozenset({ShellText("--user"), ShellText("--system")})
USER = ShellText("--user")
INHERIT = ShellText("--slice-inherit")
JOINED = (ShellText("--slice="), ShellText("--property=Slice="))
SUFFIX = ShellText(".slice")
ADVICE = """this call starts a systemd unit outside {slice}, the machine's slice, and its memory and CPU caps.

{problems}

A user unit naming no slice lands in app.slice, outside every cap. Name the user manager and the slice:

  systemd-run --user --slice={slice} ...

or a slice beneath it, --slice={stem}-NAME.slice, or --slice-inherit, which keeps the unit in the slice the call
runs in."""


def is_systemd_run(words: Sequence[Word]) -> bool:
    """Whether ``words`` run systemd-run."""
    return bool(words) and words[0].value is not None and words[0].value.rsplit("/", 1)[-1] == SYSTEMD_RUN


def spelled(commands: Sequence[Command], at: Count) -> tuple[tuple[Word, ...], tuple[Word, ...]]:
    """The words the systemd-run ``commands[at]`` spells itself, and those of the command the model records it running
    just after it, none where the model peels no command from it."""
    words = commands[at].words
    if at + 1 < len(commands) and commands[at + 1].wrappers == (*commands[at].wrappers, SYSTEMD_RUN):
        cut = len(words) - len(commands[at + 1].words)
        return words[:cut], words[cut:]
    return words, ()


def joined(said: Sequence[ShellText]) -> list[ShellText]:
    """The words ``said``, each -p or -E joined to the word after it as its long option's --name=VALUE."""
    words: list[ShellText] = []
    for word in said:
        if words and words[-1] in PAIRED:
            words[-1] = ShellText(PAIRED[words[-1]] + word)
        else:
            words.append(word)
    return words


def listed(word: ShellText) -> bool:
    """Whether ``word`` is on the list: an option taking no value, or one taking a value as --name=VALUE."""
    name, equals, _ = word.partition("=")
    return word in FLAGS or (bool(equals) and name in VALUED)


def sliced(name: ShellText) -> SliceName:
    """The slice ``name`` names, `.slice` appended where it has none."""
    return SliceName(name if name.endswith(SUFFIX) else name + SUFFIX)


def slices(said: Sequence[ShellText]) -> list[SliceName]:
    """The slices the words ``said`` name."""
    return [sliced(ShellText(word.removeprefix(form))) for word in said for form in JOINED if word.startswith(form)]


def within(name: SliceName, declared: SliceName) -> bool:
    """Whether the slice ``name`` is ``declared``, or beneath it: its stem, a dash and a word."""
    beneath = f"{declared.removesuffix(SUFFIX)}-"
    return name == declared or (name.startswith(beneath) and len(name) > len(beneath) + len(SUFFIX))


def problem(own: Sequence[Word], command: Sequence[Word], declared: SliceName) -> Prose | None:
    """What keeps the systemd-run of the words ``own``, running the command of the words ``command``, from spelling a
    user unit in ``declared``, as the refusal says it; None where nothing does."""
    unknown = [word.text for word in (*own, *command[:1]) if word.value is None]
    if unknown:
        return Prose(f"holds `{unknown[0]}`, a word the call does not make known")
    said = joined([word.spelled for word in own[1:]])
    unread = [word for word in said if not listed(word)]
    if unread:
        return Prose(f"holds `{unread[0]}`, which the rule does not read: an option's value is given as --name=VALUE")
    if NO_UNIT & set(said):
        return None
    if [word for word in said if word in MANAGERS][-1:] != [USER]:
        return Prose("names no --user, or a --system after it")
    if INHERIT in said:
        return None
    names = slices(said)
    if not names:
        return Prose("names no slice, so its unit lands in app.slice")
    outside = [name for name in names if not within(name, declared)]
    return Prose(f"places its unit in {outside[0]}, neither {declared} nor beneath it") if outside else None


def judge(event: Event) -> Verdict | None:
    """Refuse a call whose systemd-run does not spell a user unit in the machine's declared slice."""
    commands = event.call().commands
    runs = [Count(at) for at, command in enumerate(commands) if is_systemd_run(command.words)]
    if not runs:
        return None
    declared = event.declaration(Path("/")).machine.slice
    if declared is None:
        return None
    lines: list[Prose] = []
    for at in runs:
        own, after = spelled(commands, at)
        if (why := problem(own, after, declared)) is not None:
            quoted = [word.spelled for word in (*own, *after[:1])] + ([ShellText("...")] if len(after) > 1 else [])
            lines.append(Prose(f"  * `{' '.join(quoted)}` {why}"))
    if not lines:
        return None
    return refuse(Prose(ADVICE.format(slice=declared, stem=declared.removesuffix(SUFFIX), problems="\n".join(lines))))
