"""What a rule answers, and the two faults that refuse a call whatever the rules say.

A rule gives a Verdict or None. A refusal blocks the call (exit 2, its text on stderr); a context is a note the model
reads beside the call. Unreadable is the call's fault: the kit cannot read it, and the refusal names a readable form.
KitFault is the kit's own: a reader that crashed, a node the model does not hold; it refuses too, since a kit that
cannot judge a call passes nothing.
"""

from typing import Literal, NamedTuple, NewType

Prose = NewType("Prose", str)
RuleName = NewType("RuleName", str)
VerdictKind = Literal["refuse", "context"]


class Verdict(NamedTuple):
    """A rule's answer: a refusal of the call, or a note beside it."""

    kind: VerdictKind
    text: Prose


def refuse(text: Prose) -> Verdict:
    """A refusal of the call, ``text`` saying why and what passes."""
    return Verdict("refuse", text)


def context(text: Prose) -> Verdict:
    """A note the model reads beside the call, which goes ahead."""
    return Verdict("context", text)


class Unreadable(Exception):
    """A call the kit cannot read: why, and the form it can read."""

    def __init__(self, reason: Prose, form: Prose) -> None:
        super().__init__(reason)
        self.reason = reason
        self.form = form


class KitFault(Exception):
    """A fault of the kit's own, which refuses the call it was judging."""

    def __init__(self, reason: Prose) -> None:
        super().__init__(reason)
        self.reason = reason
