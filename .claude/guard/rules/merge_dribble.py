"""A dribble merges a pull request through pr-all-green alone, in a shell where errexit is on.

THE RULE (user, 2026-10-10 and 2026-11-04, made blunt 2026-11-09). The dribble is the `.commands-to-run.sh` at the root
of a git work tree, which the user runs. It merges only by `pr-all-green N --head SHA --squash-with-body FILE` after a
`set -e`, `set -o errexit` or a bundle holding e (`set -euo pipefail`) in the merge's own shell: pr-all-green merges
only once every check of SHA is green past its conclusion and the merge state is CLEAN, and errexit stops the script
where it refuses. Every other merge is refused whatever its flags: `gh pr merge`, a `gh api` naming a `pulls/N/merge`
path, and a `gh api graphql` naming mergePullRequest or enablePullRequestAutoMerge. The failure this holds: a
`gh pr merge --auto` squashed once the required contexts passed, the other lanes unread, and a merge dribble went out
BEHIND and failed in the user's hands.

After each Bash, Edit, Write or MultiEdit call, the dribble of the call's work tree is read where its sha256 is not the
one the session state keeps for it (state/session.py), with the call model reading it as `bash DRIBBLE` run from the
root does: every script it runs is read too, a shell of its own, as are a subshell, a pipeline element and a
substitution. A pr-all-green is a merge where a later word starts `--sq`, the shortest prefix argparse takes for
--squash-with-body. The refusal names each command and the form that passes; the dribble stands, read once per version.

EXCEPTIONS, each with the failure it holds:
  * the last `set` touching errexit before the merge decides, so `set +e` or `set +o errexit` turns it off again:
    `set -e; set +e; pr-all-green ...` runs on past a refusal;
  * a `gh api graphql` taking its query from a file (`KEY=@FILE`), its input (`--input`) or a word the dribble does
    not make known is refused, since either mutation can hide there unread.
"""

import hashlib
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

from dispatcher.core import Event, HookEvent, ToolName
from dispatcher.model import Command, ProcessId, Word
from dispatcher.tree import ShellText
from dispatcher.verdict import KitFault, Prose, Verdict, refuse
from rules._dribble import model
from rules.job_lifetime import base
from rules.pr_watch import tree

EVENTS: frozenset[HookEvent] = frozenset({"PostToolUse"})
TOOLS = frozenset({ToolName("Bash"), ToolName("Edit"), ToolName("Write"), ToolName("MultiEdit")})
KIND: Final = "notice"
IDENT = "merge-dribble:"
DRIBBLE = ".commands-to-run.sh"
MERGER = ShellText("pr-all-green")
SQUASH = "--sq"
MERGE_PATH = re.compile(r"pulls/[^/]+/merge")
MUTATION = re.compile(r"mergePullRequest|enablePullRequestAutoMerge")
GH_MERGE = Prose("merges with `gh pr merge`, past pr-all-green's verdict")
REST = Prose("merges through a `pulls/N/merge` path, past pr-all-green's verdict")
GRAPHQL = Prose("names a GraphQL merge mutation, past pr-all-green's verdict")
UNREAD = Prose("takes its GraphQL query from a file, its input or a word the dribble does not make known, where a "
               "merge mutation hides unread")
ERREXIT = Prose("runs where errexit is off: no `set -e` before it in its own shell leaves it on (a subshell, a "
                "pipeline element, a substitution and each script run are shells of their own), so the script runs "
                "on past a refusal")
ADVICE = """the dribble {dribble} merges a pull request in a form that skips pr-all-green's verdict:

{problems}

A dribble merges only by `pr-all-green N --head SHA --squash-with-body FILE` after `set -euo pipefail` in the same
script: it merges once every check of SHA is green past its conclusion and the merge state is CLEAN, and its refusal
stops what follows."""


def errexit(words: Sequence[Word]) -> bool | None:
    """What the command ``words`` leaves errexit where it is a `set` touching it, True on and False off; else None."""
    if base(words) != "set":
        return None
    left: bool | None = None
    values = iter(word.value for word in words[1:])
    for value in values:
        if value is None or len(value) < 2 or value[0] not in "-+" or value == "--":
            break
        if "e" in value[1:]:
            left = value[0] == "-"
        for _ in range(value.count("o")):  # each o of a bundle takes the next word as its option name
            if next(values, None) == "errexit":
                left = value[0] == "-"
    return left


def merges(words: Sequence[Word]) -> bool:
    """Whether ``words`` name pr-all-green, by its name or path, with a later word starting --sq."""
    names = [word.spelled.rsplit("/", 1)[-1] for word in words]
    return MERGER in names and any(word.spelled.startswith(SQUASH) for word in words[names.index(MERGER) + 1:])


def gh_merge(words: Sequence[Word]) -> Prose | None:
    """Why the gh command ``words`` merges a pull request outside pr-all-green; None where it does not."""
    if base(words) != "gh":
        return None
    spelled = [word.spelled for word in words[1:]]
    if ("pr", "merge") in zip(spelled, spelled[1:], strict=False):
        return GH_MERGE
    if "api" not in spelled:
        return None
    if any(MERGE_PATH.search(text) for text in spelled):
        return REST
    if "graphql" not in spelled:
        return None
    if any(MUTATION.search(text) for text in spelled):
        return GRAPHQL
    if any(word.value is None or "=@" in word.value or word.value.split("=")[0] == "--input" for word in words):
        return UNREAD
    return None


def findings(commands: Sequence[Command]) -> Iterator[tuple[Command, Prose]]:
    """Each command of ``commands`` that merges outside the one form, with why."""
    on: dict[tuple[ProcessId, ...], bool] = {}
    for command in commands:
        if (left := errexit(command.words)) is not None:
            on[command.processes] = left
        elif merges(command.words):
            if not on.get(command.processes, False):
                yield command, ERREXIT
        elif (why := gh_merge(command.words)) is not None:
            yield command, why


def judge(event: Event) -> Verdict | None:
    """Refuse a dribble of the call's work tree that merges outside the one form, once per version of it."""
    if event.cwd is None:
        return None
    root = tree(Path(event.cwd))
    dribble = root / DRIBBLE
    if not (root / ".git").exists() or not dribble.is_file():
        return None
    try:
        digest = hashlib.sha256(dribble.read_bytes()).hexdigest()
    except OSError as fault:
        raise KitFault(Prose(f"the dribble {dribble} cannot be read: {fault}")) from None
    from state.session import FieldName, FieldValue, RecordId  # imported for an event this rule judges

    ident, field, seen = RecordId(IDENT + str(dribble)), FieldName("sha256"), FieldValue(digest)
    session = event.state()
    if any(record.ident == ident and record.fields.get(field) == seen for record in session.records(KIND)):
        return None
    call = model(dribble, event)
    commands = call.commands if call is not None else ()
    problems = [Prose(f"  * `{command.text}` {why}") for command, why in findings(commands)]
    session.record(KIND, ident, {field: seen}, shared=True)
    return refuse(Prose(ADVICE.format(dribble=dribble, problems="\n".join(problems)))) if problems else None
