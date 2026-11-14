"""The syntax tree of a call, as the kit's shell reader prints it (reader/tree.go's header holds its shape).

read_tree runs `bin/shell-reader tree` on one text, under the process's address-space cap and a time limit of its
own, and returns the tree's file node. A text mvdan's bash mode cannot parse is Unreadable, with the reader's message
and its line and column; a reader that cannot run, outruns its limit, misses a node or prints no tree is a KitFault.
"""

import json
import subprocess
from typing import Literal, NewType, TypedDict

from dispatcher.verdict import KitFault, Prose, Unreadable

ShellText = NewType("ShellText", str)
ReaderPath = NewType("ReaderPath", str)
Seconds = NewType("Seconds", float)
Count = NewType("Count", int)
NodeType = Literal[
    "stmt", "andor", "pipeline", "call", "subshell", "block", "if", "while", "for", "case", "function", "arith_cmd",
    "test", "decl", "let", "time", "coproc", "case_item", "assign", "array", "array_elem", "redirect", "word", "lit",
    "single", "ansi_c", "double", "param", "cmdsubst", "arith", "procsubst", "extglob", "arith_binary", "arith_unary",
    "arith_paren", "test_binary", "test_unary", "test_paren", "words", "cstyle"]
SubstitutionForm = Literal["dollar", "backquote", "brace", "brace_reply"]

# One TypedDict holds every node kind's fields, each read only where its kind has it; an and-or or pipeline item and
# an if clause carry no type. A field two kinds spell alike with two shapes holds both: an assignment's name is text
# and a coprocess's a word, a function's body a statement and a heredoc's its text.
Node = TypedDict("Node", {
    "ok": bool, "error": "Node", "message": Prose, "line": Count, "col": Count,
    "type": NodeType, "text": ShellText, "stmts": list["Node"],
    "negated": bool, "background": bool, "coprocess": bool, "redirs": list["Node"], "cmd": "Node | None",
    "items": list["Node"], "op": ShellText | None, "stmt": "Node",
    "assigns": list["Node"], "args": list["Node"],
    "clauses": list["Node"], "cond": "list[Node] | Node", "then": list["Node"], "else": "list[Node] | None",
    "do": list["Node"], "loop": "Node", "word": "Node", "patterns": list["Node"],
    "name": "ShellText | Node | None", "body": "Node | ShellText", "variant": ShellText,
    "value": "ShellText | Node | None", "array": "Node | None", "index": "Node | None", "append": bool,
    "naked": bool, "fd": ShellText | None, "hdoc": "Node | None", "delim_quoted": bool,
    "parts": list["Node"], "short": bool, "excl": bool, "length": bool, "names": ShellText | None,
    "slice": "Node | None", "repl": "Node | None", "exp": "Node | None", "form": SubstitutionForm,
}, total=False)

PARSE_FORM = Prose(
    "Write the call as bash reads it, since the Bash tool runs every call in bash and the kit reads it so: quote the "
    "text at the place the message names.")


def read_tree(text: ShellText, reader: ReaderPath, timeout: Seconds) -> Node:
    """The file node of ``text``'s syntax tree, read by the shell reader at ``reader`` within ``timeout``."""
    try:
        source = text.encode("utf-8")
    except UnicodeEncodeError:
        raise Unreadable(Prose("the call text is not UTF-8"), Prose("Write the call as UTF-8 text.")) from None
    try:
        done = subprocess.run([reader, "tree"], input=source, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise KitFault(Prose(f"the shell reader outran its {timeout:g} s limit")) from None
    except OSError as fault:
        raise KitFault(Prose(f"the shell reader {reader} cannot run: {fault}")) from None
    said = done.stderr.decode("utf-8", "replace").strip()
    if done.returncode != 0:
        raise KitFault(Prose(f"the shell reader exited {done.returncode}: {said.splitlines()[0] if said else 'silent'}"))
    try:
        tree: Node = json.loads(done.stdout)
    except ValueError:
        raise KitFault(Prose("the shell reader printed no JSON tree")) from None
    if not isinstance(tree, dict) or not isinstance(tree.get("ok"), bool):
        raise KitFault(Prose("the shell reader printed JSON that is not a tree"))
    if not tree["ok"]:
        error = tree["error"]
        raise Unreadable(Prose(f"bash cannot parse it: {error['message']} at line {error['line']}, column "
                               f"{error['col']}"), PARSE_FORM)
    return tree
