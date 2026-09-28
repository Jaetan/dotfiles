"""Read shell command text the way the guard hooks need it.

Nothing here runs a command, and this is not a whole bash grammar.  It reads
what the hooks decide on: every simple command a call would run, at any
nesting (lists, pipelines, subshells, brace groups, if/while/until/for/case
bodies, function bodies, `time` and `coproc`, $( ) and backquote
substitutions, process substitutions, substitutions inside [[ ]], case
subjects, ${ } and unquoted heredocs), each with

  * its words, quotes removed, and each word's value where every expansion in
    it is known: a variable assigned earlier in the same text or in the
    environment, a positional parameter a caller bound (`"$@"` to each of
    them; `shift` and `set --` followed), `${V:-w}` and its kin, the trims
    `${V%p}` `${V#p}`, `~`, `$(pwd)`,
    `$(cd X && pwd)`, `$(dirname X)`, `$(realpath X)`, `$(git rev-parse
    --show-toplevel)`, and `$(mktemp)` (a path under the temporary
    directory); a literal array's elements (`$a` every one in zsh, the
    first in bash, `"${a[@]}"` every one); None where one is not, with the
    known part kept apart (a variable keeps it too), so a path can be placed
    by its known directory;
  * its redirections, those of every enclosing compound command (and of an
    earlier `exec >file`, in a group too) first, then its pipe, then its own,
    in the order bash applies them;
  * the directory it runs in, as far as `cd`, `pushd`, `popd` and `cd -`
    earlier in the same text say (None once that is unknowable); in the
    tool's zsh, whose `cd` is zoxide's, a target that is no directory there
    (and none the call made) goes where `zoxide query` sends it;
  * the shell that runs it (zsh for the tool's own text, bash or sh for the
    carriers and scripts that name them), whether it runs in the background,
    whether zsh's `&!` disowns it, and which process and job `&` forked for it, whether its standard input, output or error is a pipe, which
    shell scope it runs in (a subshell, `&` item, pipeline element or
    substitution is a scope of its own; eval and source run in their
    caller's), the function or trap it is part of, the loop it runs in, and
    what a substitution it came from feeds;
  * the heredoc and here-string bodies it reads, so a caller can tell data
    (`cat > f <<EOF`) from a script (`bash <<EOF`).

It reads bash and the zsh spellings the Bash tool's shell accepts (`>!`,
`&!`, `&|`).  A trap's text is read once the rest has run, and a sourced file
is read in place where the caller supplies its text.  An unquoted glob is
the files it matches where they exist (up to GLOB_CAP), and a value or known
part longer than VALUE_CAP is unknown.  Text it cannot follow raises
ParseError; a call past MAX_COMMANDS commands, MAX_NODES nodes, or
MAX_LEXED bytes raises BudgetExceeded.  The callers decide what either means
for them; a fault of the reader itself raises something else, which the hooks
report.  A hook calls bound_memory() first, so a reading no budget here
foresaw ends in a MemoryError inside the hook rather than in the host's OOM
killer.
"""

from __future__ import annotations

import fnmatch
import functools
import glob
import itertools
import os
import re
import resource
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["Command", "ParseError", "Redirect", "Word", "commands", "tree_of"]


class ParseError(Exception):
    """The text is not shell this reader can follow."""


class BudgetExceeded(Exception):
    """A call too large to read within its budget: unreadable as a whole. Not a ParseError, so no handler of
    a syntax error swallows it and reads the call in part."""


@functools.lru_cache(maxsize=8192)
def tree_of(path: str) -> str | None:
    """The git work tree holding `path` (symlinks resolved; `.git` internals belong to their tree). One hook run
    reads the filesystem at one moment, so an answer holds for the run."""
    try:
        p = os.path.realpath(path)
        d = p if os.path.isdir(p) else os.path.dirname(p)
        while True:
            if os.path.basename(d) == ".git":
                return os.path.dirname(d)
            if os.path.lexists(os.path.join(d, ".git")):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent
    except (ValueError, UnicodeError, OSError):
        return None


# ─── words ────────────────────────────────────────────────────────────────────


@dataclass
class Word:
    """One shell word: its text with quotes removed, and its expanded value."""

    text: str
    value: str | None
    quoted: bool = False
    glob: bool = False  # an unquoted * ? or [ remains in it
    substitutions: list[str] = field(default_factory=list)  # $( ) / ` ` / <( ) bodies
    process_substitution: bool = False  # the whole word is <( ) or >( )
    partial: str = ""  # the known parts expanded, each unknown part left as written ($1, ${X}, $(...))


@dataclass
class Redirect:
    """One redirection: `fd op target`, or a heredoc / here-string body.

    A pipeline's pipes are ones too, op `|` (stdout), `|&` (both) and `<|` (stdin), placed where bash makes
    them: after what the element inherits, before its own redirections.  An enclosing group's redirection is
    the one object every command in the group holds, as bash opens the file once for all of them.
    """

    fd: int | None
    op: str
    target: Word | None
    body: str | None = None
    body_literal: bool = True  # a quoted heredoc delimiter: the body is not expanded


@dataclass
class Command:
    """One simple command, with the context it runs in."""

    words: list[Word]
    assigns: dict[str, str | None]
    redirects: list[Redirect]
    cwd: str | None
    env: dict[str, str | None]
    background: bool = False
    pipe_in: bool = False
    pipe_out: bool = False
    pipe_err: bool = False
    depth: int = 0
    scope: int = 0
    job_scope: int = 0  # the shell that started it as a job: a pipeline's elements share their parent's
    bg_scope: int = 0  # for a command in the background: the shell whose `wait` can reap it
    function: str | None = None
    loop: str | None = None  # "while", "until" or "for" when it runs in a loop's condition or body
    loop_id: int = 0  # the innermost loop it runs in: one id per loop, so each loop is judged alone
    loop_body: bool = False  # in that loop's body rather than its condition (a for loop is all body)
    feeds: str = ""  # for a command in a substitution: "assign", "cd", "arg" or "redirect"
    # For a command in the background: the process `&` forked for it, shared by everything that process runs.
    # A bare pipeline is one process per element, since `$!` names only the last.
    proc: int = 0
    job: int = 0  # the `&` item it belongs to: what a job spec (`%1`) names, every process of it
    inlined: bool = False  # a `source` whose file the reader read in place, as the shell runs it
    disowned: bool = False  # started with zsh's `&!` or `&|`: no wait of this shell reaps it
    unparsed: str | None = None  # text it runs later that the reader could not follow (a trap's)
    dialect: str = "bash"  # the shell that runs it: "zsh" (the tool's own), "bash", "sh" or "fish"

    @property
    def argv(self) -> list[str]:
        """The words as the command would receive them, where known; the known parts of them, where not."""
        return [w.value if w.value is not None else (w.partial or w.text) for w in self.words]

    def bodies(self) -> list[str]:
        """The heredoc and here-string bodies this command reads on stdin."""
        return [r.body for r in self.redirects if r.body is not None]


_LIT, _VAR, _SUB, _HOME, _UNKNOWN, _DEFAULT = "lit", "var", "sub", "home", "unknown", "default"
_DEFAULTED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*|\d)(:?)([-=?+])(.*)", re.S)  # ${V:-w} ${V:=w} ${V:?m} ${V:+w}
_TRIMMED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*|\d)(%%|%|##|#)(.*)", re.S)  # ${V%pat} ${V##pat} ...

_OPERATORS = sorted(
    ["&&", "||", ";;&", ";;", ";&", "|&", "&!", "&|", "&>>", "&>", ">>", ">|", ">!", ">&", "<<<", "<<-", "<<",
     "<>", "<&", ";", "&", "|", "(", ")", "<", ">"],
    key=len,
    reverse=True,
)
_OP_ALIASES = {">!": ">|"}  # zsh's `&!` and `&|` stay: they disown the job
_REDIR_OPS = {"&>>", "&>", ">>", ">|", ">&", "<<<", "<<-", "<<", "<>", "<&", "<", ">"}
_META = set(" \t\n;&|()<>")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SPECIAL_PARAMS = set("@*#?-$!0123456789")
_FD_PREFIX = re.compile(r"\d{1,9}(?=[<>])")  # a longer number is a word: no descriptor has that many digits
# Words after which the next word again stands in command position.
_LEADS_COMMAND = {"then", "do", "else", "elif", "if", "while", "until", "!", "time", "{", "coproc", "exec"}
_ARRAY_START = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")


_SCANS: dict[tuple[str, int, str], int | ParseError] = {}  # a scan's answer by (text, position, kind), failures too


def _memo(kind: str, s: str, i: int, scan: object) -> int:
    """Each scan runs once per position: an unterminated `$(( $(` would otherwise be rescanned per level."""
    key = (s, i, kind)  # the text itself: an id can be reused once a text is freed
    hit = _SCANS.get(key)
    if hit is None:
        try:
            hit = scan()  # type: ignore[operator]
        except ParseError as exc:
            hit = exc
        _SCANS[key] = hit
    if isinstance(hit, ParseError):
        raise hit
    return hit


def _match_close(s: str, i: int, open_: str, close: str) -> int:
    return _memo("match" + open_, s, i, lambda: _charged(i, _match_close_scan(s, i, open_, close)))


def _charged(start: int, end: int) -> int:
    """A bracket scan spends the call's byte budget too: `${` nested 900 deep rescans the text at each level."""
    _lexed[0] += end - start
    if _lexed[0] > MAX_LEXED:
        raise BudgetExceeded(f"more than {MAX_LEXED} bytes of text to read")
    return end


def _match_close_scan(s: str, i: int, open_: str, close: str) -> int:
    """Index of the `close` matching the `open_` just before `i`, quote-aware (for ${ } and arithmetic)."""
    depth = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ParseError("unterminated single quote")
            i = j + 1
            continue
        if c == '"':
            i = _skip_double(s, i + 1)
            continue
        if c == "`":
            i = _skip_backtick(s, i + 1) + 1
            continue
        if c == "$" and s.startswith("$(", i) and not s.startswith("$((", i):
            i = _comsub_end(s, i + 2) + 1
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ParseError(f"unmatched {open_}")


def _skip_double(s: str, i: int) -> int:
    """Index just past the `"` closing a double-quoted string opened before `i`."""
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        if c == "$" and s.startswith("$((", i):
            i = _match_close(s, i + 3, "(", ")") + 1
            continue
        if c == "$" and s.startswith("$(", i):
            i = _comsub_end(s, i + 2) + 1
            continue
        if c == "$" and s.startswith("${", i):
            i = _match_close(s, i + 2, "{", "}") + 1
            continue
        if c == "`":
            i = _skip_backtick(s, i + 1) + 1
            continue
        i += 1
    raise ParseError("unterminated double quote")


def _skip_backtick(s: str, i: int) -> int:
    """Index of the backquote closing one opened before `i`."""
    n = len(s)
    while i < n:
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "`":
            return i
        i += 1
    raise ParseError("unterminated backquote")


def _comsub_end(s: str, i: int) -> int:
    return _memo("comsub", s, i, lambda: _comsub_end_scan(s, i))


def _comsub_end_scan(s: str, i: int) -> int:
    """Index of the `)` closing a `$(` (or `<(`) whose body starts at `i`, read as shell."""
    lx = _Lexer(s, start=i, comsub=True)
    lx.run()
    if lx.end is None:
        raise ParseError("unterminated $(")
    return lx.end


def _decode_ansi(text: str) -> str:
    out = []
    k = 0
    n = len(text)
    simple = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "a": "\a", "b": "\b",
              "e": "\x1b", "E": "\x1b", "f": "\f", "v": "\v", "?": "?"}
    while k < n:
        c = text[k]
        if c != "\\" or k + 1 >= n:
            out.append(c)
            k += 1
            continue
        e = text[k + 1]
        if e in simple:
            out.append(simple[e])
            k += 2
        elif e == "x":
            m = re.match(r"[0-9a-fA-F]{1,2}", text[k + 2 :])
            out.append(chr(int(m.group(0), 16)) if m else "x")
            k += 2 + (m.end() if m else 0)
        elif e in "uU":
            m = re.match(r"[0-9a-fA-F]{1,%d}" % (4 if e == "u" else 8), text[k + 2 :])
            try:
                out.append(chr(int(m.group(0), 16)) if m else e)
            except (ValueError, OverflowError):
                out.append("?")
            k += 2 + (m.end() if m else 0)
        elif e in "01234567":
            m = re.match(r"[0-7]{1,3}", text[k + 1 :])
            out.append(chr(int(m.group(0), 8)))
            k += 1 + m.end()
        elif e == "c" and k + 2 < n:
            out.append(chr(ord(text[k + 2]) & 0x1F))
            k += 3
        else:
            out.append("\\" + e)
            k += 2
    return "".join(out)


class _WordTok:
    """A word as lexed: its segments, and whether any part was quoted."""

    def __init__(self) -> None:
        self.segments: list[tuple[str, str]] = []
        self._buf: list[str] = []
        self.quoted = False
        self.glob = False
        self.raw = ""
        self.procsub: str | None = None

    def lit(self, text: str) -> None:
        self._buf.append(text)

    def seg(self, kind: str, text: str) -> None:
        self._flush()
        self.segments.append((kind, text))

    def _flush(self) -> None:
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf = []
        if self.segments and self.segments[-1][0] == _LIT:
            self.segments[-1] = (_LIT, self.segments[-1][1] + text)
        else:
            self.segments.append((_LIT, text))

    def finish(self) -> _WordTok:
        self._flush()
        return self

    @property
    def plain(self) -> str | None:
        """The word's text when it is one literal segment, else None."""
        if all(k == _LIT for k, _ in self.segments):
            return "".join(t for _, t in self.segments)
        return None


class _Tok:
    def __init__(self, kind: str, value: object = None, pos: int = 0) -> None:
        self.kind = kind  # "word", "op", "redir", "arith", "nl", "eof"
        self.value = value
        self.pos = pos
        self.fd: int | None = None
        self.body: str | None = None
        self.body_literal = True
        self.target: _WordTok | None = None

    def __repr__(self) -> str:
        return f"<{self.kind} {self.value!r}>"


MAX_LEXED = 512 * 1024  # bytes read as shell for one call, nested texts and bracket scans included
VALUE_CAP = 64 * 1024  # a value or known part longer than this is unknown: `X=$X$X` in a loop doubles without bound
MAX_MEMORY = 1 << 30  # address space of a hook process; an ordinary call measured 48-64 MiB
_lexed = [0]


def bound_memory() -> None:
    """Lower this process's address-space limit to MAX_MEMORY: past it Python raises MemoryError."""
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = MAX_MEMORY if hard == resource.RLIM_INFINITY else min(MAX_MEMORY, hard)
    if soft == resource.RLIM_INFINITY or soft > cap:
        resource.setrlimit(resource.RLIMIT_AS, (cap, hard))


def reset_budget() -> None:
    """A new call to read: its byte budget starts again."""
    _lexed[0] = 0
    _TREES.clear()


class _Lexer:
    def __init__(self, s: str, start: int = 0, comsub: bool = False, charge: bool = True) -> None:
        # A $( )'s text, and a part of a text read again (a substitution's body, its words expanded), were
        # charged with the text they are part of: `charge` is False for them.  A whole text is parsed once per
        # call (_parse), so it is charged once however often it runs.
        if not comsub and charge:
            _lexed[0] += len(s) - start
            if _lexed[0] > MAX_LEXED:
                raise BudgetExceeded(f"more than {MAX_LEXED} bytes of text to read")
        self.s = s
        self.i = start
        self.toks: list[_Tok] = []
        self.pending: list[tuple[_Tok, str, bool]] = []  # heredoc redirs awaiting a body
        self.comsub = comsub
        self.end: int | None = None
        self._depth = 0
        self._case = 0
        self._cmdpos = True  # the next word stands where a command (or a reserved word) may
        self._arith_tries = 0

    def run(self) -> list[_Tok]:
        s = self.s
        n = len(s)
        while self.i < n:
            c = s[self.i]
            if c in " \t\r":
                self.i += 1
                continue
            if c == "\\" and self.i + 1 < n and s[self.i + 1] == "\n":
                self.i += 2
                continue
            if c == "#":
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j
                continue
            if c == "\n":
                self.toks.append(_Tok("nl", pos=self.i))
                self.i += 1
                self._cmdpos = True
                if self.pending:
                    self._read_heredocs()
                continue
            if c in "<>" and self.i + 1 < n and s[self.i + 1] == "(":
                end = _comsub_end(s, self.i + 2)
                w = _WordTok()
                w.procsub = s[self.i + 2 : end]
                w.raw = s[self.i : end + 1]
                self.toks.append(_Tok("word", w, self.i))
                self.i = end + 1
                continue
            if s.startswith("((", self.i) and self._arith():
                continue
            if self.comsub and c == ")" and self._depth == 0 and self._case == 0:
                self.end = self.i
                return self.toks
            op = self._operator()
            if op is not None:
                if self.comsub and self._case == 0:  # a case pattern's `)` closes nothing
                    if op == "(":
                        self._depth += 1
                    elif op == ")":
                        self._depth -= 1
                if op not in _REDIR_OPS:
                    self._cmdpos = True
                continue
            at_command = self._cmdpos
            w = self._word()
            if w is None:
                continue
            plain = None if w.quoted else w.plain
            if self.comsub and at_command and plain in ("case", "esac"):
                self._case += 1 if plain == "case" else -1
            self._cmdpos = plain in _LEADS_COMMAND
        if self.pending:
            self._read_heredocs()
        self.toks.append(_Tok("eof", pos=self.i))
        return self.toks

    def _arith(self) -> bool:
        """`(( ... ))` as one arithmetic token, when the parens close adjacently; else no."""
        s = self.s
        self._arith_tries += 1
        if self._arith_tries > 256:
            return False  # pathological input: read the parens as subshells
        try:
            end = _match_close(s, self.i + 2, "(", ")")
        except ParseError:
            return False
        if end + 1 < len(s) and s[end + 1] == ")":
            self.toks.append(_Tok("arith", s[self.i + 2 : end], self.i))
            self.i = end + 2
            return True
        return False

    def _operator(self) -> str | None:
        s = self.s
        for op in _OPERATORS:
            if s.startswith(op, self.i):
                pos = self.i
                self.i += len(op)
                op = _OP_ALIASES.get(op, op)
                if op in _REDIR_OPS:
                    tok = _Tok("redir", op, pos)
                    self.toks.append(tok)
                    if op in ("<<", "<<-"):
                        self._heredoc_delimiter(tok, op)
                else:
                    self.toks.append(_Tok("op", op, pos))
                return op
        return None

    def _heredoc_delimiter(self, tok: _Tok, op: str) -> None:
        s = self.s
        while self.i < len(s) and s[self.i] in " \t":
            self.i += 1
        start = self.i
        w = self._word(emit=False)
        text = w.plain
        raw = s[start : self.i]
        if text is None:
            text = "".join(t for _, t in w.segments)
        tok.body_literal = any(q in raw for q in "'\"\\")
        self.pending.append((tok, text, op == "<<-"))

    def _read_heredocs(self) -> None:
        s = self.s
        for tok, delim, strip_tabs in self.pending:
            lines = []
            while self.i < len(s):
                j = s.find("\n", self.i)
                line_start = self.i
                line = s[self.i :] if j < 0 else s[self.i : j]
                self.i = len(s) if j < 0 else j + 1
                check = line.lstrip("\t") if strip_tabs else line
                if check == delim or (self.comsub and check.startswith(delim + ")")):
                    if check != delim:  # `EOF)` closing a substitution on the delimiter line: resume at the `)`
                        self.i = line_start + (len(line) - len(check)) + len(delim)
                    break
                lines.append(line)
            tok.body = "\n".join(lines) + ("\n" if lines else "")
        self.pending = []

    def _word(self, emit: bool = True) -> _WordTok | None:
        s = self.s
        n = len(s)
        w = _WordTok()
        start = self.i
        m = _FD_PREFIX.match(s, self.i)
        if m and emit:
            self.i = m.end()
            op = self._operator()
            if op in _REDIR_OPS:
                self.toks[-1].fd = int(m.group(0))
                return None
            self.i = start
        if s.startswith("~", self.i) and (self.i + 1 >= n or s[self.i + 1] == "/" or s[self.i + 1] in _META):
            w.seg(_HOME, "")
            self.i += 1
        while self.i < n:
            c = s[self.i]
            if c == "(" and not w.segments and not w.quoted and _ARRAY_START.fullmatch("".join(w._buf)):
                end = _comsub_end(s, self.i + 1)
                w.seg(_UNKNOWN, s[self.i : end + 1])  # an array value
                self.i = end + 1
                continue
            if c in _META:
                break
            if c == "\\":
                if self.i + 1 < n and s[self.i + 1] == "\n":
                    self.i += 2
                    continue
                w.lit(s[self.i + 1 : self.i + 2])
                w.quoted = True
                self.i += 2
                continue
            if c == "'":
                j = s.find("'", self.i + 1)
                if j < 0:
                    raise ParseError("unterminated single quote")
                w.lit(s[self.i + 1 : j])
                w.quoted = True
                self.i = j + 1
                continue
            if c == "$" and s.startswith("$'", self.i):
                j = self.i + 2
                while j < n and s[j] != "'":
                    j += 2 if s[j] == "\\" else 1
                if j >= n:
                    raise ParseError("unterminated $' quote")
                w.lit(_decode_ansi(s[self.i + 2 : j]))
                w.quoted = True
                self.i = j + 1
                continue
            if c == "$" and s.startswith('$"', self.i):
                self.i += 1  # a locale string reads as a double-quoted one
                self._double(w)
                continue
            if c == '"':
                self._double(w)
                continue
            if c == "$":
                self._dollar(w)
                continue
            if c == "`":
                j = _skip_backtick(s, self.i + 1)
                w.seg(_SUB, s[self.i + 1 : j].replace("\\`", "`"))
                self.i = j + 1
                continue
            if c in "*?[":
                w.glob = True
            w.lit(c)
            self.i += 1
        w.finish()
        w.raw = s[start : self.i]
        if emit:
            self.toks.append(_Tok("word", w, start))
        return w

    def _double(self, w: _WordTok) -> None:
        s = self.s
        n = len(s)
        self.i += 1
        w.quoted = True
        if not w.segments and not w._buf:
            w.seg(_LIT, "")  # an empty quoted string is still a word
        while self.i < n:
            c = s[self.i]
            if c == '"':
                self.i += 1
                return
            if c == "\\" and self.i + 1 < n and s[self.i + 1] in '$`"\\\n':
                if s[self.i + 1] != "\n":
                    w.lit(s[self.i + 1])
                self.i += 2
                continue
            if c == "$":
                self._dollar(w)
                continue
            if c == "`":
                j = _skip_backtick(s, self.i + 1)
                w.seg(_SUB, s[self.i + 1 : j].replace("\\`", "`"))
                self.i = j + 1
                continue
            w.lit(c)
            self.i += 1
        raise ParseError("unterminated double quote")

    def _dollar(self, w: _WordTok) -> None:
        s = self.s
        n = len(s)
        nxt = s[self.i + 1] if self.i + 1 < n else ""
        if s.startswith("$((", self.i):
            try:
                end = _match_close(s, self.i + 3, "(", ")")
            except ParseError:
                end = -1
            if end >= 0 and end + 1 < n and s[end + 1] == ")":
                w.seg(_UNKNOWN, s[self.i : end + 2])
                self.i = end + 2
                return
        if nxt == "(":
            end = _comsub_end(s, self.i + 2)
            w.seg(_SUB, s[self.i + 2 : end])
            self.i = end + 1
            return
        if nxt == "[":
            end = _match_close(s, self.i + 2, "[", "]")
            w.seg(_UNKNOWN, s[self.i : end + 1])
            self.i = end + 1
            return
        if nxt == "{":
            end = _match_close(s, self.i + 2, "{", "}")
            inner = s[self.i + 2 : end]
            m = _DEFAULTED.fullmatch(inner) or _TRIMMED.fullmatch(inner)
            if _NAME.fullmatch(inner) or inner.isdigit():
                w.seg(_VAR, inner)
            elif inner in ("BASH_SOURCE[0]", "BASH_SOURCE"):
                w.seg(_VAR, "BASH_SOURCE")
            elif m:
                w.seg(_DEFAULT, inner)
            else:
                w.seg(_UNKNOWN, s[self.i : end + 1])
            self.i = end + 1
            return
        m = _NAME.match(s, self.i + 1)
        if m:
            w.seg(_VAR, m.group(0))
            self.i = m.end()
            return
        if nxt.isdigit():  # a positional parameter: known where a script or function is given its words
            w.seg(_VAR, nxt)
            self.i += 2
            return
        if nxt and nxt in _SPECIAL_PARAMS:
            w.seg(_UNKNOWN, "$" + nxt)
            self.i += 2
            return
        w.lit("$")
        self.i += 1


def _scan_substitutions(text: str) -> list[str]:
    """The $( ) and backquote bodies in expandable text (an unquoted heredoc, a ${ })."""
    out: list[str] = []
    i = 0
    n = len(text)
    try:
        while i < n:
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if text.startswith("$((", i):
                try:
                    end = _match_close(text, i + 3, "(", ")")
                    out.extend(_scan_substitutions(text[i + 3 : end]))
                    i = end + 2
                    continue
                except ParseError:
                    pass
            if text.startswith("$(", i):
                end = _comsub_end(text, i + 2)
                out.append(text[i + 2 : end])
                i = end + 1
                continue
            if text.startswith("${", i):
                end = _match_close(text, i + 2, "{", "}")
                out.extend(_scan_substitutions(text[i + 2 : end]))
                i = end + 1
                continue
            if c == "`":
                j = _skip_backtick(text, i + 1)
                out.append(text[i + 1 : j].replace("\\`", "`"))
                i = j + 1
                continue
            i += 1
    except ParseError:
        pass
    return out


# ─── parser ───────────────────────────────────────────────────────────────────


@dataclass
class _Simple:
    words: list[_WordTok]
    redirs: list[_Tok]


@dataclass
class _Compound:
    kind: str  # "subshell", "group", "if", "while", "until", "for", "case", "func", "test", "bg"
    bodies: list[object]
    redirs: list[_Tok]
    name: str | None = None  # a for loop's variable, a function's name
    header: list[_WordTok] = field(default_factory=list)  # words read for substitutions
    arith: str = ""  # arithmetic text read for substitutions


@dataclass
class _Pipeline:
    items: list[object]
    err: list[bool]  # item i's stderr also goes to the pipe (|&)


@dataclass
class _AndOr:
    items: list[_Pipeline]


@dataclass
class _List:
    items: list[tuple[_AndOr, str]]  # separator after each: ";", "&", "\n" or ""


class _Parser:
    def __init__(self, toks: list[_Tok]) -> None:
        self.toks = toks
        self.k = 0

    def peek(self, ahead: int = 0) -> _Tok:
        return self.toks[min(self.k + ahead, len(self.toks) - 1)]

    def word_is(self, *names: str, ahead: int = 0) -> bool:
        t = self.peek(ahead)
        return t.kind == "word" and not t.value.quoted and t.value.plain in names

    def op_is(self, *ops: str) -> bool:
        t = self.peek()
        return t.kind == "op" and t.value in ops

    def take(self) -> _Tok:
        t = self.peek()
        if t.kind != "eof":
            self.k += 1
        return t

    def expect_word(self, name: str) -> None:
        self.skip_nl()
        if not self.word_is(name):
            raise ParseError(f"expected {name}")
        self.take()

    def skip_nl(self) -> None:
        while self.peek().kind == "nl" or self.op_is(";"):
            self.take()

    def parse(self) -> _List:
        lst = self.list_until(set())
        if self.peek().kind != "eof":
            raise ParseError(f"unexpected {self.peek()!r}")
        return lst

    def list_until(self, stop_words: set[str], stop_ops: frozenset[str] = frozenset()) -> _List:
        items: list[tuple[_AndOr, str]] = []
        while True:
            while self.peek().kind == "nl" or self.op_is(";"):
                self.take()
            t = self.peek()
            if t.kind == "eof":
                break
            if t.kind == "op" and (t.value in stop_ops or t.value in (")", ";;", ";&", ";;&")):
                break
            if t.kind == "word" and not t.value.quoted and t.value.plain in stop_words:
                break
            andor = self.andor()
            sep = ""
            t = self.peek()
            if t.kind == "op" and t.value in (";", "&", "&!", "&|"):
                sep = t.value
                self.take()
            elif t.kind == "nl":
                sep = "\n"
                self.take()
            items.append((andor, sep))
        return _List(items)

    def andor(self) -> _AndOr:
        items = [self.pipeline()]
        while self.op_is("&&", "||"):
            self.take()
            while self.peek().kind == "nl":
                self.take()
            items.append(self.pipeline())
        return _AndOr(items)

    def pipeline(self) -> _Pipeline:
        while self.word_is("!"):
            self.take()
        nxt = self.peek(1)
        if self.word_is("time") and (self.word_is("{", "for", "while", "until", "if", "case", "select", "[[", "!",
                                                  "-p", ahead=1) or nxt.kind == "arith"
                                     or (nxt.kind == "op" and nxt.value == "(")):
            self.take()
            if self.word_is("-p"):
                self.take()
            while self.word_is("!"):
                self.take()
        items = [self.command()]
        err = [False]
        while self.op_is("|", "|&"):
            err[-1] = self.take().value == "|&"
            while self.peek().kind == "nl":
                self.take()
            items.append(self.command())
            err.append(False)
        return _Pipeline(items, err)

    def redirects(self) -> list[_Tok]:
        out = []
        while self.peek().kind == "redir":
            r = self.take()
            if r.value not in ("<<", "<<-"):
                if self.peek().kind != "word":
                    raise ParseError("redirection without a target")
                r.target = self.take().value
            out.append(r)
        return out

    def command(self) -> object:
        t = self.peek()
        if t.kind == "eof":
            raise ParseError("unexpected end of input")
        if t.kind == "arith":
            self.take()
            return _Compound("test", [], self.redirects(), arith=t.value)
        if t.kind == "op" and t.value == "(":
            self.take()
            body = self.list_until(set(), frozenset({")"}))
            if not self.op_is(")"):
                raise ParseError("unclosed subshell")
            self.take()
            return _Compound("subshell", [body], self.redirects())
        if t.kind == "word" and not t.value.quoted:
            name = t.value.plain
            if name == "{":
                self.take()
                body = self.list_until({"}"})
                self.expect_word("}")
                return _Compound("group", [body], self.redirects())
            if name == "[[":
                self.take()
                header = []
                while not self.word_is("]]"):
                    if self.peek().kind == "eof":
                        raise ParseError("unclosed [[")
                    tk = self.take()
                    if tk.kind == "word":
                        header.append(tk.value)
                    elif tk.kind == "redir" and self.peek().kind == "word":
                        header.append(self.take().value)
                self.take()
                return _Compound("test", [], self.redirects(), header=header)
            if name == "if":
                return self.if_()
            if name in ("while", "until"):
                self.take()
                cond = self.list_until({"do"})
                self.expect_word("do")
                body = self.list_until({"done"})
                self.expect_word("done")
                return _Compound(name, [cond, body], self.redirects())
            if name in ("for", "select"):
                return self.for_()
            if name == "case":
                return self.case_()
            if name == "coproc":
                self.take()
                if self.peek().kind == "word" and (self.word_is("{", ahead=1) or self.peek(1).kind == "op"
                                                   and self.peek(1).value == "("):
                    self.take()  # the coprocess's name
                return _Compound("bg", [self.command()], [])
            if name == "function":
                self.take()
                fname = self.take()
                if fname.kind != "word":
                    raise ParseError("function without a name")
                if self.op_is("("):
                    self.take()
                    if self.op_is(")"):
                        self.take()
                while self.peek().kind == "nl":
                    self.take()
                return _Compound("func", [self.command()], [], name=fname.value.plain)
        if (
            t.kind == "word"
            and self.peek(1).kind == "op"
            and self.peek(1).value == "("
            and self.peek(2).kind == "op"
            and self.peek(2).value == ")"
        ):
            fname = t.value.plain
            self.k += 3
            while self.peek().kind == "nl":
                self.take()
            return _Compound("func", [self.command()], [], name=fname)
        return self.simple()

    def if_(self) -> _Compound:
        self.take()
        bodies = [self.list_until({"then"})]
        self.expect_word("then")
        bodies.append(self.list_until({"elif", "else", "fi"}))
        while self.word_is("elif"):
            self.take()
            bodies.append(self.list_until({"then"}))
            self.expect_word("then")
            bodies.append(self.list_until({"elif", "else", "fi"}))
        if self.word_is("else"):
            self.take()
            bodies.append(self.list_until({"fi"}))
        self.expect_word("fi")
        return _Compound("if", bodies, self.redirects())

    def for_(self) -> _Compound:
        self.take()
        var = None
        header: list[_WordTok] = []
        arith = ""
        if self.peek().kind == "arith":  # for (( ... ))
            arith = self.take().value
        else:
            var_tok = self.take()
            if var_tok.kind != "word":
                raise ParseError("for without a variable")
            var = var_tok.value.plain
            while self.peek().kind == "nl":
                self.take()
            if self.word_is("in"):
                self.take()
                while self.peek().kind == "word" and not self.word_is("do"):
                    header.append(self.take().value)
            else:  # `for f; do`: the positional parameters
                every = _WordTok()
                every.seg(_UNKNOWN, "$@")
                header.append(every)
            if self.op_is(";"):
                self.take()
        self.skip_nl()
        self.expect_word("do")
        body = self.list_until({"done"})
        self.expect_word("done")
        return _Compound("for", [body], self.redirects(), name=var, header=header, arith=arith)

    def case_(self) -> _Compound:
        self.take()
        subject = self.take()
        if subject.kind != "word":
            raise ParseError("case without a subject")
        header = [subject.value]
        self.skip_nl()
        self.expect_word("in")
        bodies = []
        while True:
            self.skip_nl()
            if self.word_is("esac"):
                self.take()
                break
            if self.peek().kind == "eof":
                raise ParseError("unclosed case")
            if self.op_is("("):
                self.take()
            while not self.op_is(")"):
                if self.peek().kind == "eof":
                    raise ParseError("unclosed case pattern")
                tk = self.take()
                if tk.kind == "word":
                    header.append(tk.value)
            self.take()
            bodies.append(self.list_until({"esac"}, frozenset({";;", ";&", ";;&"})))
            if self.op_is(";;", ";&", ";;&"):
                self.take()
        return _Compound("case", bodies, self.redirects(), header=header)

    def simple(self) -> _Simple:
        words: list[_WordTok] = []
        redirs: list[_Tok] = []
        while True:
            t = self.peek()
            if t.kind == "word":
                words.append(self.take().value)
            elif t.kind == "redir":
                redirs.extend(self.redirects())
            else:
                break
        if not words and not redirs:
            raise ParseError(f"unexpected {self.peek()!r}")
        return _Simple(words, redirs)


# ─── evaluation: context, expansion, the flat list of simple commands ─────────


_scopes = itertools.count(1)
_procs = itertools.count(1)
_loops = itertools.count(1)


@dataclass
class _Ctx:
    cwd: str | None
    env: dict[str, str | None]
    redirects: list[Redirect]
    background: bool = False
    pipe_in: bool = False
    pipe_out: bool = False
    pipe_err: bool = False
    depth: int = 0
    dirstack: list[str | None] = field(default_factory=list)
    oldpwd: str | None = None
    scope: int = 0
    job_scope: int = 0
    bg_scope: int = 0
    function: str | None = None
    loop: str | None = None
    feeds: str = ""
    # A variable whose value is unknown but starts known (`L=$S/x-$(date +%s).log`): its known part, the rest as
    # written, so a write through it is charged to the known directory rather than to wherever the call runs.
    partials: dict[str, str] = field(default_factory=dict)
    proc: int = 0
    job: int = 0
    disowned: bool = False
    value_loops: int = 0  # enclosing for loops read once per value: two deep at most, or 8^n readings
    loop_id: int = 0
    loop_body: bool = False

    def child(self, new_scope: bool = False, **changes: object) -> _Ctx:
        scope = next(_scopes) if new_scope else self.scope
        c = _Ctx(self.cwd, dict(self.env), list(self.redirects), self.background, self.pipe_in, self.pipe_out,
                 self.pipe_err, self.depth, list(self.dirstack), self.oldpwd, scope,
                 scope if new_scope else self.job_scope, self.bg_scope, self.function, self.loop, self.feeds,
                 dict(self.partials), self.proc, self.job, self.disowned, self.value_loops, self.loop_id,
                 self.loop_body)
        for k, v in changes.items():
            setattr(c, k, v)
        return c


_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?(\+?)=(.*)", re.S)
_GIT_TOP = re.compile(r"git(?:\s+-C\s+(\S+))?\s+rev-parse\s+--show-toplevel")
MAX_DEPTH = 8
MAX_COMMANDS = 8000  # past this a call is not something to read: it is refused as unreadable while a run is live
MAX_NODES = 20000  # nodes read, a test or a group as much as a command: kept apart, since each costs more to read
GLOB_CAP = 256  # a glob matching more is left as written


def _set_partial(ctx: _Ctx, name: str, partial: str | None) -> None:
    if partial:
        ctx.partials[name] = partial
    else:
        ctx.partials.pop(name, None)


class _Evaluator:
    def __init__(self) -> None:
        self.out: list[Command] = []
        self.funcs: dict[str, object] = {}
        self.calls = 0
        self.work = 0  # simple commands read
        self.nodes = 0  # nodes read: a test or a group counts as much as a command
        self.source_reader: Callable[[str], str | None] | None = None
        self.dialect = "bash"
        self.sourcing: list[str] = []
        self.pending_traps: list[tuple[str, _Ctx]] = []

    def expanded_text(self, t: str, ctx: _Ctx) -> str | None:
        """A substitution's text with every word expanded, or None when a word is unknown or it is not simple."""
        if "$" not in t and "`" not in t:
            return None
        try:
            toks = _Lexer(t, charge=False).run()
        except ParseError:
            return None
        parts = []
        for tok in toks:
            if tok.kind == "word":
                w = self.expand(tok.value, ctx)
                if w.value is None:
                    return None
                parts.append(shlex.quote(w.value) if re.search(r"[\s'\"]", w.value) else w.value)
            elif tok.kind == "op" and tok.value == "&&":
                parts.append("&&")
            elif tok.kind != "eof":
                return None
        if sum(map(len, parts)) > VALUE_CAP:
            return None
        return " ".join(parts)

    def known_sub(self, text: str, ctx: _Ctx) -> str | None:
        """The value of a substitution whose output the reader can tell.  It runs in a subshell: what its
        words assign (`${V:=w}`) stays there."""
        ctx = ctx.child(new_scope=True)
        t = re.sub(r"\s+\d*>>?\s*/dev/null|\s+\d*>&\d", "", text.strip())
        t = self.expanded_text(t, ctx) or t
        cwd = ctx.cwd
        m = re.fullmatch(r"cd\s+(\S+)\s*&&\s*(.+)", t)
        if m:
            d = m.group(1).strip("'\"")
            cwd = os.path.normpath(d) if os.path.isabs(d) else (os.path.normpath(os.path.join(cwd, d)) if cwd
                                                                 else None)
            t = m.group(2).strip()
        if t in ("pwd", "pwd -P", "pwd -L", "/bin/pwd"):
            return cwd
        m = re.fullmatch(r"(dirname|basename)\s+(\S+)", t)
        if m:
            d = m.group(2).strip("'\"")
            return os.path.dirname(d.rstrip("/")) or "." if m.group(1) == "dirname" else os.path.basename(d)
        m = re.fullmatch(r"(?:realpath|readlink\s+-f)\s+(\S+)", t)
        if m:
            d = m.group(1).strip("'\"")
            return os.path.normpath(d if os.path.isabs(d) else os.path.join(cwd, d)) if (cwd or os.path.isabs(d)) \
                else None
        m = _GIT_TOP.fullmatch(t)
        if m:
            d = m.group(1).strip("'\"") if m.group(1) else "."
            base = d if os.path.isabs(d) else (os.path.join(cwd, d) if cwd else None)
            return tree_of(base) if base else None
        m = re.fullmatch(r"mktemp((?:\s+-[dtuq]+)*)((?:\s+(?:-p|--tmpdir(?:=\S+)?)\s*\S*)?)(\s+\S*X{3,}\S*)?", t)
        if m:
            flags, tmpdir, template = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
            if "t" in flags or tmpdir.startswith(("-p", "--tmpdir")) or not template:
                base = tmpdir.split("=", 1)[1] if "=" in tmpdir else tmpdir[2:].strip() if tmpdir.startswith("-p") \
                    else ""
                return os.path.join(base or tempfile.gettempdir(), "tmp.mktemp")
            return os.path.normpath(os.path.join(cwd, template)) if cwd else None  # a bare template: the cwd
        return None

    def word_value(self, word: str, ctx: _Ctx, subs: list[str]) -> str | None:
        try:
            dw = _Lexer(word, charge=False).run()[0] if word else None
        except ParseError:
            return None
        if dw is None or dw.kind != "word":
            return ""
        ew = self.expand(dw.value, ctx)
        subs.extend(ew.substitutions)
        return ew.value

    def parameter(self, t: str, ctx: _Ctx, subs: list[str]) -> str | None:
        """The value of ${V:-w}, ${V:=w}, ${V:?m}, ${V:+w} and the trims ${V%p} ${V%%p} ${V#p} ${V##p}."""
        m = _DEFAULTED.fullmatch(t)
        name = m.group(1) if m else _TRIMMED.fullmatch(t).group(1)
        known = name in ctx.env or name in os.environ
        v = ctx.env.get(name) if name in ctx.env else os.environ.get(name)
        if name in ctx.env and v is None:
            return None  # assigned, but to something unknown
        if m:
            colon, op, word = m.group(2), m.group(3), m.group(4)
            unset = not known or (colon and v == "")
            if op == "+":
                return self.word_value(word, ctx, subs) if not unset else ""
            if op == "?":
                return v if not unset else None  # unset: the shell stops, so nothing after it is known
            if op == "=" and unset:
                value = self.word_value(word, ctx, subs)
                ctx.env[name] = value  # ${V:=w} assigns V
                return value
            return self.word_value(word, ctx, subs) if unset else v
        name, op, pat = _TRIMMED.fullmatch(t).groups()
        pattern = self.word_value(pat, ctx, subs)
        if v is None or pattern is None or len(v) > 4096:
            return None  # a trim tries every cut: past a few KB its value is left unknown
        cuts = range(len(v) + 1)
        if op.startswith("%"):
            order = cuts if op == "%%" else reversed(cuts)
            return next((v[:k] for k in order if fnmatch.fnmatchcase(v[k:], pattern)), v)
        order = reversed(cuts) if op == "##" else cuts
        return next((v[k:] for k in order if fnmatch.fnmatchcase(v[:k], pattern)), v)

    def array_values(self, inner: str, ctx: _Ctx) -> list[str] | None:
        """A literal array's elements (`a=(x "y z" $HOME/w)`), each as its word expands; None past what is known."""
        try:
            toks = _Lexer(inner, charge=False).run()
        except ParseError:
            return None
        if any(t.kind not in ("word", "eof") for t in toks):
            return None
        values = [w.value for w in self.words([t.value for t in toks if t.kind == "word"], ctx)]
        return None if any(v is None for v in values) else [v for v in values if v is not None]

    def array_words(self, w: _WordTok, ctx: _Ctx) -> list[Word] | None:
        """The words a whole-word reference to a literal array expands to: "${a[@]}" and ${a[*]} every element,
        $a every element in zsh and the first in bash; None when the word is no such reference."""
        segs = [seg for seg in w.segments if seg[0] != _LIT or seg[1]]
        if len(segs) != 1:
            return None
        kind, text = segs[0]
        m = re.fullmatch(r"\$\{(\w+)\[[@*]\]\}", text) if kind == _UNKNOWN else None
        name = m.group(1) if m else text if kind == _VAR else None
        key = f"{name}[@]" if name else None
        if key is None or key not in ctx.env:
            return None
        joined = ctx.env[key]
        if joined is None:
            return [Word(w.raw, None, w.quoted)]  # an array whose elements are not known
        elements = joined.split("\0") if joined else []
        if kind == _VAR and self.dialect in ("bash", "sh"):
            elements = elements[:1]
        return [Word(e, e, True, partial=e) for e in elements]

    def words(self, toks: list[_WordTok], ctx: _Ctx) -> list[Word]:
        """The words the tokens expand to: \"$@\" to each positional parameter, where they are known."""
        out: list[Word] = []
        for w in toks:
            array = self.array_words(w, ctx)
            if array is not None:
                out += array
                continue
            if [seg for seg in w.segments if seg[0] != _LIT or seg[1]] in ([(_UNKNOWN, "$@")], [(_UNKNOWN, "${@}")],
                                                                        [(_UNKNOWN, "$*")], [(_UNKNOWN, "${*}")]):
                count = ctx.env.get("#")
                params = [ctx.env.get(str(k)) for k in range(1, int(count) + 1)] if count and count.isdigit() \
                    and int(count) <= 9 else None
                if params is not None and all(p is not None for p in params):
                    out += [Word(p, p, True, partial=p) for p in params]
                    continue
            word = self.expand(w, ctx)
            if word.glob and not word.quoted and word.value and ctx.cwd:  # the files it names, where they exist
                try:
                    found = sorted(glob.glob(word.value, root_dir=None if os.path.isabs(word.value) else ctx.cwd,
                                             recursive=self.dialect == "zsh"))
                except (OSError, ValueError, re.error):
                    found = []
                if 0 < len(found) <= GLOB_CAP:
                    out += [Word(f, f, False, partial=f) for f in found]
                    continue
            if word.value == "" and not word.quoted:
                continue  # an unquoted expansion known empty is no word at all: `$E cmd` runs cmd
            if self.dialect in ("bash", "sh") and not word.quoted and word.value and re.search(r"\s", word.value) \
                    and any(seg[0] != _LIT for seg in w.segments):
                out += [Word(p, p, False, partial=p) for p in word.value.split()]  # bash splits it; zsh does not
                continue
            out.append(word)
        return out

    def bind_params(self, ctx: _Ctx, values: list[str | None] | None) -> None:
        """Set $1 ... $9 and $#: None for values the reader cannot know."""
        for k in range(1, 10):
            ctx.env[str(k)] = None if values is None else (values[k - 1] if k - 1 < len(values) else "")
        ctx.env["#"] = None if values is None or len(values) > 9 else str(len(values))

    def params(self, ctx: _Ctx) -> list[str | None] | None:
        count = ctx.env.get("#")
        if not (count and count.isdigit()) or int(count) > 9:
            return None
        return [ctx.env.get(str(k)) for k in range(1, int(count) + 1)]

    def expand(self, w: _WordTok, ctx: _Ctx) -> Word:
        text_parts: list[str] = []
        value_parts: list[str] | None = []
        partial: list[str] = []
        subs: list[str] = []
        for kind, t in w.segments:
            if kind == _LIT:
                text_parts.append(t)
                partial.append(t)
                if value_parts is not None:
                    value_parts.append(t)
            elif kind == _HOME:
                home = ctx.env.get("HOME", os.environ.get("HOME"))
                text_parts.append("~")
                partial.append(home if home is not None else "~")
                if value_parts is not None and home is not None:
                    value_parts.append(home)
                else:
                    value_parts = None
            elif kind == _VAR:
                text_parts.append("${" + t + "}")
                if t == "PWD":
                    v = ctx.cwd
                elif t == "OLDPWD":
                    v = ctx.oldpwd
                elif t in ctx.env:
                    v = ctx.env[t]
                else:
                    v = os.environ.get(t)
                partial.append(v if v is not None else ctx.partials.get(t, "${" + t + "}"))
                if value_parts is not None and v is not None:
                    value_parts.append(v)
                else:
                    value_parts = None
            elif kind == _SUB:
                text_parts.append("$(" + t + ")")
                subs.append(t)
                v = self.known_sub(t, ctx)
                partial.append(v if v is not None else "$(" + t + ")")
                if value_parts is not None and v is not None:
                    value_parts.append(v)
                else:
                    value_parts = None
            elif kind == _DEFAULT:
                text_parts.append("${" + t + "}")
                v = self.parameter(t, ctx, subs)
                partial.append(v if v is not None else "${" + t + "}")
                if value_parts is not None and v is not None:
                    value_parts.append(v)
                else:
                    value_parts = None
            else:
                text_parts.append(t)
                partial.append(t)
                if t.startswith("${"):
                    subs.extend(_scan_substitutions(t[2:-1]))
                elif t.startswith("$((") or t.startswith("$["):
                    subs.extend(_scan_substitutions(t[3:-2] if t.startswith("$((") else t[2:-1]))
                elif t.startswith("("):
                    subs.extend(_scan_substitutions(t[1:-1]))  # an array value
                value_parts = None
        if w.procsub is not None:
            subs.append(w.procsub)
            return Word(w.raw, None, quoted=False, substitutions=subs, process_substitution=True)
        if value_parts is not None and sum(map(len, value_parts)) > VALUE_CAP:
            value_parts = None  # counted before the join: 100000 references to a long value are not built
        if sum(map(len, partial)) > VALUE_CAP:
            partial = text_parts  # the word as written: nothing of it known
        value = None if value_parts is None else "".join(value_parts)
        if value is not None and "\0" in value:
            value = value.split("\0", 1)[0]  # a shell argument ends at a NUL
        return Word("".join(text_parts), value, w.quoted, w.glob, subs, partial="".join(partial))

    def run_list(self, lst: _List, ctx: _Ctx) -> None:
        for andor, sep in lst.items:
            if sep in ("&", "&!", "&|"):  # a subshell of its own: a job of this shell, whose own jobs are its own
                bare_pipe = len(andor.items) == 1 and len(andor.items[0].items) > 1
                job = next(_procs)
                self.run_andor(andor, ctx.child(new_scope=True, background=True, bg_scope=ctx.job_scope,
                                                proc=0 if bare_pipe else job, job=job, disowned=sep != "&"))
            else:  # in this shell, or in a background subshell's own list: one context, its cd kept
                self.run_andor(andor, ctx)

    def run_andor(self, andor: _AndOr, ctx: _Ctx) -> None:
        for pipe in andor.items:
            self.run_pipeline(pipe, ctx)

    def run_pipeline(self, pipe: _Pipeline, ctx: _Ctx) -> None:
        n = len(pipe.items)
        if n == 1:
            self.run_node(pipe.items[0], ctx)
            return
        for idx, node in enumerate(pipe.items):
            pipe_to = [Redirect(None, "|&" if pipe.err[idx] else "|", None)] if idx < n - 1 else []
            pipe_from = [Redirect(None, "<|", None)] if idx > 0 else []
            sub = ctx.child(
                new_scope=True,
                job_scope=ctx.job_scope,
                redirects=ctx.redirects + pipe_from + pipe_to,
                proc=next(_procs) if ctx.background and not ctx.proc else ctx.proc,
                pipe_in=idx > 0 or ctx.pipe_in,
                pipe_out=idx < n - 1 or ctx.pipe_out,
                pipe_err=(idx < n - 1 and pipe.err[idx]) or ctx.pipe_err,
            )
            self.run_node(node, sub)  # each element runs in a subshell: nothing propagates, but for zsh's last
            if self.dialect == "zsh" and idx == n - 1 and not ctx.background:
                self.persist(ctx, sub, len(pipe_from) + len(pipe_to))  # zsh runs it in the shell: cd, read persist

    def own_redirects(self, toks: list[_Tok], ctx: _Ctx) -> list[Redirect]:
        out = []
        for r in toks:
            word = self.expand(r.target, ctx) if r.target is not None else None
            if word is not None:
                for sub in word.substitutions:
                    self.sub(sub, ctx, "redirect")
            body = r.body
            literal = r.body_literal
            if r.value == "<<<" and word is not None:
                body = word.value if word.value is not None else word.text
                literal = word.quoted
            elif body is not None and not literal:
                for sub in _scan_substitutions(body):
                    self.sub(sub, ctx, "arg")
            out.append(Redirect(r.fd, r.value, word, body, literal))
        return out

    def run_node(self, node: object, ctx: _Ctx) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise BudgetExceeded(f"more than {MAX_NODES} nodes to read")
        if isinstance(node, _Simple):
            self.run_simple(node, ctx)
            return
        assert isinstance(node, _Compound)
        if node.kind == "func":
            self.funcs[node.name or ""] = node.bodies[0]  # read where it is called, with the caller's context
            return
        redirs = self.own_redirects(node.redirs, ctx)
        for w in node.header:
            for sub in self.expand(w, ctx).substitutions:
                self.sub(sub, ctx, "arg")
        for sub in _scan_substitutions(node.arith):
            self.sub(sub, ctx, "arg")
        if node.kind == "subshell":
            self.run_list(node.bodies[0], ctx.child(new_scope=True, redirects=ctx.redirects + redirs))
            return
        if node.kind == "bg":  # a coprocess: a job of this shell like `&`
            job = next(_procs)
            self.run_node(node.bodies[0], ctx.child(new_scope=True, background=True, bg_scope=ctx.job_scope,
                                                     proc=job, job=job))
            return
        if node.kind == "test":
            return
        scoped = ctx.child(redirects=ctx.redirects + redirs)
        if node.kind == "for":
            values = [w.value for w in self.words(node.header, ctx)]
            scoped.loop, scoped.loop_id, scoped.loop_body = "for", next(_loops), True
            # Read once per value, two loops deep at most: up to 64 values outermost, 8 in a nested loop.
            if node.name and values and all(v is not None for v in values) \
                    and len(values) <= (64 if ctx.value_loops == 0 else 8) and ctx.value_loops < 2:
                scoped.value_loops += 1
                for v in values:  # read the body once per value
                    scoped.env[node.name] = v
                    scoped.partials.pop(node.name, None)
                    for body in node.bodies:
                        self.run_list(body, scoped)
                self.persist(ctx, scoped, len(redirs))
                return
            if node.name:  # read once, the variable unknown but for what every value starts with
                scoped.env[node.name] = None
                known = [v for v in values if v is not None]
                _set_partial(scoped, node.name, os.path.commonprefix(known) if known and len(known) == len(values)
                             else None)
        if node.kind in ("while", "until"):
            scoped.loop, scoped.loop_id = node.kind, next(_loops)
        for k, body in enumerate(node.bodies):
            if node.kind in ("while", "until"):
                scoped.loop_body = k > 0  # the condition, then the body
            self.run_list(body, scoped)
        self.persist(ctx, scoped, len(redirs))

    def sub(self, text: str, ctx: _Ctx, feeds: str) -> None:
        if ctx.depth >= MAX_DEPTH:
            raise ParseError("substitutions nested too deeply")
        # Its output is captured: a pipe to this shell, after what it inherits.
        inner = ctx.child(new_scope=True, background=False, pipe_out=True, depth=ctx.depth + 1, feeds=feeds,
                          redirects=ctx.redirects + [Redirect(None, "|", None)])
        self.run_list(_parse(text, charge=False), inner)

    def call_function(self, name: str, ctx: _Ctx, label: str | None = None) -> None:
        """Read a function's body where it is called; `label` names the caller when a trap runs it."""
        if self.calls >= 32:
            return
        self.calls += 1
        body = self.funcs[name]
        saved = ctx.function
        ctx.function = label or name
        try:
            if isinstance(body, _List):
                self.run_list(body, ctx)
            else:
                self.run_node(body, ctx)
        finally:
            ctx.function = saved

    @staticmethod
    def persist(ctx: _Ctx, scoped: _Ctx, own: int) -> None:
        """A group, if, loop or case runs in this shell: its cd, assignments and `exec >` persist."""
        ctx.cwd, ctx.oldpwd, ctx.env, ctx.dirstack = scoped.cwd, scoped.oldpwd, scoped.env, scoped.dirstack
        ctx.partials = scoped.partials
        ctx.redirects = ctx.redirects + scoped.redirects[len(ctx.redirects) + own :]  # not its own `> f`

    def run_simple(self, node: _Simple, ctx: _Ctx) -> None:
        self.work += 1
        if self.work > MAX_COMMANDS:
            raise BudgetExceeded(f"more than {MAX_COMMANDS} commands to read")
        assigns: dict[str, str | None] = {}
        partials: dict[str, str] = {}
        arrays: dict[str, str | None] = {}  # name[@]: a literal array's elements, NUL-joined; None when unknown
        dropped: list[str] = []  # name[@] of a name given a plain value: no array under it any more
        i = 0
        words = node.words
        while i < len(words):
            w = words[i]
            m = _ASSIGN.fullmatch(w.raw) if w.raw and w.procsub is None else None
            if not m or not w.segments or w.segments[0][0] != _LIT:
                break
            value_tok = _WordTok()
            first = w.segments[0][1]
            prefix = len(m.group(1)) + len(m.group(2) or "") + len(m.group(3)) + 1
            head = first[prefix:]
            home = ctx.env.get("HOME", os.environ.get("HOME"))
            raw_value = w.raw.split("=", 1)[1]
            if home and (raw_value.startswith("~") or ":~" in raw_value):  # a quoted or escaped ~ stays
                head = re.sub(r"(^|:)~(?=/|:|$)", lambda mm: mm.group(1) + home, head)  # an assignment's tilde
            value_tok.segments = ([(_LIT, head)] if head else []) + list(w.segments[1:])
            value_tok.quoted = w.quoted
            ew = self.expand(value_tok, ctx)
            for sub in ew.substitutions:
                self.sub(sub, ctx, "assign")
            assigns[m.group(1)] = None if (m.group(2) or m.group(3)) else ew.value
            if assigns[m.group(1)] is None and not (m.group(2) or m.group(3)) and ew.partial:
                partials[m.group(1)] = ew.partial
            literal = not head and len(w.segments) == 2 and w.segments[1][0] == _UNKNOWN \
                and w.segments[1][1].startswith("(") and not (m.group(2) or m.group(3))
            elements = self.array_values(w.segments[1][1][1:-1], ctx) if literal else None
            key = m.group(1) + "[@]"
            if literal or m.group(2) or m.group(3):  # an element set or appended: the array is no longer known
                arrays[key] = "\0".join(elements) if elements is not None else None
            else:
                dropped.append(key)
            i += 1
        cmd_words = self.words(words[i:], ctx)
        name = cmd_words[0].text if cmd_words else ""
        feeds = "cd" if name in ("cd", "pushd") else "arg"
        for w in cmd_words:
            for sub in w.substitutions:
                self.sub(sub, ctx, feeds)
        redirs = self.own_redirects(node.redirs, ctx)
        if not cmd_words:
            if redirs:  # the environment it ran in, copied only for a command that is recorded
                self.out.append(self._cmd([], assigns, ctx.redirects + redirs, dict(ctx.env), ctx))
            ctx.env.update(assigns)
            for key in dropped:
                ctx.env.pop(key, None)
            ctx.env.update(arrays)
            for k in assigns:
                _set_partial(ctx, k, partials.get(k))
            return
        env = dict(ctx.env)
        self.out.append(self._cmd(cmd_words, assigns, ctx.redirects + redirs, env, ctx))
        if name == "exec" and len(cmd_words) == 1:
            ctx.redirects = ctx.redirects + redirs  # `exec >log` redirects what follows
        elif name in ("cd", "pushd", "popd"):
            self.change_dir(name, cmd_words[1:], ctx)
        elif name in ("source", ".") and len(cmd_words) > 1 and self.source_reader is not None \
                and cmd_words[1].value is not None and len(self.sourcing) < 4:
            path = cmd_words[1].value if os.path.isabs(cmd_words[1].value) or ctx.cwd is None else \
                os.path.normpath(os.path.join(ctx.cwd, cmd_words[1].value))
            text = self.source_reader(path)
            try:
                sourced_tree = _parse(text) if text is not None else None
            except ParseError:
                sourced_tree = None  # read as its own script instead, and marked unreadable there alone
            if sourced_tree is not None:  # it runs in this shell: its functions and variables are the call's
                self.out[-1].inlined = True
                self.sourcing.append(path)
                saved = {k: ctx.env.get(k, "") for k in [*map(str, range(1, 10)), "#"]}
                if len(cmd_words) > 2:  # `source f ARGS`: its own words while it runs
                    self.bind_params(ctx, [w.value for w in cmd_words[2:]])
                try:
                    self.run_list(sourced_tree, ctx)
                finally:
                    self.sourcing.pop()
                    if len(cmd_words) > 2:
                        ctx.env.update(saved)
        elif name in ("export", "declare", "typeset", "local", "readonly"):
            home = ctx.env.get("HOME", os.environ.get("HOME"))
            for tok, w in zip(words[i + 1 :], cmd_words[1:]):
                m = _ASSIGN.fullmatch(w.text)
                if m:
                    v = w.value.split("=", 1)[1] if w.value is not None else None
                    raw_value = tok.raw.split("=", 1)[1] if tok.raw and "=" in tok.raw else ""
                    if v is not None and home and (raw_value.startswith("~") or ":~" in raw_value):
                        v = re.sub(r"(^|:)~(?=/|:|$)", lambda mm: mm.group(1) + home, v)
                    ctx.env[m.group(1)] = v
                    _set_partial(ctx, m.group(1), w.partial.split("=", 1)[1] if v is None and "=" in w.partial
                                 else None)
        elif name in self.funcs:
            saved = {k: ctx.env.get(k, "") for k in [*map(str, range(1, 10)), "#"]}
            self.bind_params(ctx, [w.value for w in cmd_words[1:]])  # its own words, for its body only
            try:
                self.call_function(name, ctx)
            finally:
                ctx.env.update(saved)
        elif name == "shift":
            n = cmd_words[1].value if len(cmd_words) > 1 else "1"
            params = self.params(ctx)
            self.bind_params(ctx, params[int(n):] if params is not None and n is not None and n.isascii() and n.isdigit()
                             and len(n) < 10 else None)
        elif name == "set" and any(w.text == "--" for w in cmd_words[1:]):
            k = next(j for j, w in enumerate(cmd_words) if w.text == "--")
            self.bind_params(ctx, [w.value for w in cmd_words[k + 1 :]])
        elif name == "trap" and len(cmd_words) >= 2:
            action = cmd_words[1].value if cmd_words[1].value is not None else cmd_words[1].text
            if action.strip() in self.funcs:
                self.call_function(action.strip(), ctx, f"trap:{action.strip()}")
            elif _NAME.fullmatch(action.strip() or "-"):
                self.pending_traps.append((action.strip(), ctx, self.out[-1]))
            elif action.strip() and action.strip() != "-":
                self.pending_traps.append((action, ctx, self.out[-1]))  # text: read as shell once the rest ran
        elif name in ("read", "mapfile", "readarray"):
            for w in cmd_words[1:]:
                if _NAME.fullmatch(w.text):
                    ctx.env[w.text] = None
                    ctx.partials.pop(w.text, None)
        elif name == "unset":
            for w in cmd_words[1:]:
                ctx.env.pop(w.text, None)
                ctx.partials.pop(w.text, None)

    def _cmd(self, words: list[Word], assigns: dict[str, str | None], redirects: list[Redirect],
             env: dict[str, str | None], ctx: _Ctx) -> Command:
        return Command(words, assigns, redirects, ctx.cwd, env, ctx.background, ctx.pipe_in, ctx.pipe_out,
                       ctx.pipe_err, ctx.depth, ctx.scope, ctx.job_scope, ctx.bg_scope, ctx.function, ctx.loop,
                       ctx.loop_id, ctx.loop_body, ctx.feeds, ctx.proc, ctx.job, disowned=ctx.disowned,
                       dialect=self.dialect)

    def made(self, path: str) -> bool:
        """A directory an earlier mkdir of this call makes: the builtin cd enters it."""
        return any(c.words and os.path.basename(c.words[0].value or "") == "mkdir" and c.cwd and any(
            w.value and not w.value.startswith("-") and os.path.normpath(os.path.join(c.cwd, w.value)) == path
            for w in c.words[1:]) for c in self.out)

    def zoxide(self, args: list[Word], ctx: _Ctx) -> str | None:
        """Where the tool shell's cd goes when zoxide's __zoxide_z does not hand it to the builtin: the lookup
        `zoxide query --exclude "$PWD" -- ARGS`, which it runs.  None when the builtin runs, or nothing is found."""
        words = [a.value for a in args]
        if not words or ctx.cwd is None or any(w is None for w in words):
            return None
        one = words[0] if len(words) == 1 else None
        if len(words) == 2 and words[0] == "--" or one is not None and (
                one == "-" or re.fullmatch(r"[-+][0-9]", one) or os.path.isdir(os.path.join(ctx.cwd, one))
                or self.made(os.path.normpath(os.path.join(ctx.cwd, one)))):
            return None
        return _zoxide_query(ctx.cwd, tuple(w for w in words if w is not None))

    def change_dir(self, name: str, args: list[Word], ctx: _Ctx) -> None:
        if name == "cd" and self.dialect == "zsh" and _zoxide_cd():
            found = self.zoxide(args, ctx)
            if found is not None:
                ctx.oldpwd, ctx.cwd = ctx.cwd, found
                return
        operands = [a for a in args if not (a.text.startswith("-") and a.text != "-")]
        if name == "popd":
            ctx.oldpwd, ctx.cwd = ctx.cwd, (ctx.dirstack.pop() if ctx.dirstack else None)
            return
        if name == "pushd":
            ctx.dirstack.append(ctx.cwd)
        if not operands:
            target: str | None = ctx.env.get("HOME", os.environ.get("HOME"))
        elif operands[0].text == "-":
            target = ctx.oldpwd
        else:
            target = operands[0].value
        previous = ctx.cwd
        partial = operands[0].partial if operands and target is None else ""
        if target is None and partial.startswith("/") and "$" in partial:
            # Known up to an unknown part: the cwd keeps its known directory, the rest spelled unknown.
            known = partial[: partial.index("$")]
            ctx.cwd = os.path.join(os.path.dirname(known) if not known.endswith("/") else known.rstrip("/") or "/",
                                   "${UNKNOWN}")
        elif target is None:
            ctx.cwd = None
        elif os.path.isabs(target):
            ctx.cwd = os.path.normpath(target)
        elif ctx.cwd is not None:
            ctx.cwd = os.path.normpath(os.path.join(ctx.cwd, target))
        else:
            ctx.cwd = None
        ctx.oldpwd = previous


@functools.lru_cache(maxsize=1)
def _zoxide_cd() -> bool:
    """Whether the tool's shell has zoxide's cd: its latest snapshot defines `cd` to call __zoxide_z."""
    if shutil.which("zoxide") is None:
        return False
    snaps = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "shell-snapshots", "snapshot-zsh-*"))
    if not snaps:
        return False
    try:
        with open(max(snaps, key=os.path.getmtime), errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    return re.search(r"^cd \(\) \{\s*__zoxide_z", text, re.M) is not None


@functools.lru_cache(maxsize=256)
def _zoxide_query(cwd: str, words: tuple[str, ...]) -> str | None:
    """zoxide's answer, read-only: the directory its database ranks first for the words, or None."""
    try:
        p = subprocess.run(["zoxide", "query", "--exclude", cwd, "--", *words], capture_output=True, text=True,
                           timeout=1, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    found = p.stdout.strip()
    return os.path.normpath(found) if p.returncode == 0 and os.path.isabs(found) else None


_TREES: dict[str, _List] = {}  # each text parsed this call: a script read again with other words, a file that
# sources itself or a substitution in a loop is parsed once and read as often as it runs


def _parse(text: str, charge: bool = True) -> _List:
    """The parse of a text, once per call; its first reading is charged to the budget as any text is."""
    tree = _TREES.get(text)
    if tree is None:
        tree = _Parser(_Lexer(text, charge=charge).run()).parse()
        _TREES[text] = tree
    return tree


def commands(text: str, cwd: str | None, env: dict[str, str | None] | None = None,
             depth: int = 0, redirects: list[Redirect] | None = None, scope: int | None = None,
             source_reader: Callable[[str], str | None] | None = None, dialect: str = "bash") -> list[Command]:
    """Every simple command `text` would run, each with its context.

    `scope` is the shell it runs in when that is a caller's (eval, source); a fresh one otherwise.
    Raises ParseError where the text is not shell this reader can follow.
    """
    _SCANS.clear()
    try:
        tree = _parse(text)
        ev = _Evaluator()
        ev.source_reader = source_reader
        ev.dialect = dialect
        top = next(_scopes) if scope is None else scope  # eval and source run in their caller's shell
        ctx = _Ctx(cwd, dict(env or {}), list(redirects or []), depth=depth, scope=top, job_scope=top)
        ev.run_list(tree, ctx)
        for action, tctx, trap_cmd in ev.pending_traps:
            if action in ev.funcs:
                ev.call_function(action, tctx, f"trap:{action}")
            elif not _NAME.fullmatch(action):
                try:
                    tree_of_trap = _parse(action)
                except ParseError:
                    trap_cmd.unparsed = "the text of a trap"  # that command alone, not the whole call
                    continue
                trap_ctx = tctx.child(function="trap:")  # a trap's commands: its kills are counted as the trap's
                ev.run_list(tree_of_trap, trap_ctx)
    except RecursionError as exc:
        raise ParseError("nested too deeply") from exc
    return ev.out
