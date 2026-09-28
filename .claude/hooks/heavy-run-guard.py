#!/home/nicolas/.local/bin/python3.14
"""Enforce the user's rules for heavy runs, before a tool call runs and after it.

THE RULES (user, 2026-09-25):
  1. A heavy command writes its whole output to a file, so it never has to be
     run twice.
  2. No `head` on its output: the file keeps everything a long run printed.
  3. A heavy command uses CPUs 0-19 only; 20-23 belong to the user, and no
     call names one of them unless the user says so (2026-09-27).
  4. No file in a tree is edited while a heavy run reads that tree. Safe beats
     fast. The dribble, `.commands-to-run.sh`, is the one file the user said
     may be written meanwhile.

WHY A HOOK AND NOT A NOTE. Rules 1 and 2 were a memory and were broken anyway:
the probe store was appended to a batch of quick lint commands, so the call
never read as heavy, and the store had to be run twice. While it ran, a
CHANGELOG edit landed through a Python write in Bash and failed a probe. None
of that is caught by remembering; every part of it is caught by reading the
call.

HOW A CALL IS READ. `_shell.py` reads the text as shell and `_commands.py`
flattens it (both beside this file, shared with no-polling-loops.py): every
simple command at any nesting, trap actions included, with wrappers peeled
(env, taskset, nice, timeout, nohup, setsid, stdbuf, xargs, sudo, rtk, uv run
...), with what a carrier runs (bash/sh/zsh/fish -c, eval, script -c, watch,
tmux, screen, find -exec) read as more of the call, and with a script the
call runs read too, its own words bound to $0, $1 ...: by path or through
bash, one the same call has just written (a heredoc, echo or printf), or a
sourced file read in place, so its functions and variables are the call's.
Python code is read by `_python.py`. A call past 8000 commands is unreadable,
and so is one whose reading passes the hook's address-space limit
(`_shell.bound_memory`): a MemoryError is a budget spent, never a pass.

WHAT IS HEAVY is one table, `_commands.heavy()`, used both for the commands a
call runs and for the processes already running: the program and its
subcommand, never a word anywhere in a command line. Asking for help or a
version, listing, a dry run, and a targeted test (pytest on named files,
go test -run, ctest -R) are not heavy. A shell running `-c` text is never
itself a run; the programs it starts are processes of their own.

RULES 1-3, for a heavy command in a Bash call or in a script of the user's
it runs; a repository's own script is read for its writes, and the call that
runs one holding a heavy command is held to these rules for it: under a
taskset whose CPU set is within 0-19; standard output and standard error both
end in files, bash's order followed (what it inherits, then its pipe, then
its own redirections: `2>&1 > log` and a pipe inside a script redirected
whole are refused); not reading from a pipe; not in the background with `&`.
In the call itself, also: alone, beside nothing but cd, pushd, popd, echo,
printf, mkdir, export, set, true, `:`, and the substitutions that feed an
assignment, a cd or a redirection before it (`echo "$(date)"` is other
work); no `head` anywhere. Rule 3 also holds for every command, heavy or
not, in the call, in any script it runs and in any command its Python starts:
a CPU set naming 20-23 (a taskset or numactl it runs under, or taskset -p) is
refused, and so is one the call leaves unknown or one a repository script
computes from the machine's count. A heavy run Python starts may not send its
output into a pipe or /dev/null, nor run in the background, either. A heavy
command in text the hook cannot read (a carrier's fish, or a whole call it
cannot parse or reads past its budget) is refused: it goes in a call of its
own, where it can be read. The foreground passes as well as
run_in_background: the harness moves a call that outlives its timeout to the
background and notifies at its end, and kills nothing. Monitor watches work;
it is refused a heavy command.

RULE 4. Every path a call would write is resolved from the command's own
words (redirections; cp, mv (both ends), install, ln, rsync; rm, touch,
mkdir, tee, truncate, chmod; sed -i, perl -i, awk -i inplace; dd of=; tar,
unzip, patch, curl, wget; every git subcommand but the read-only ones;
formatters and fixers in write mode; pip install; a cmake configure (-B,
else where it runs); cargo clean, add, update, fix; find -delete and -exec;
an EnterWorktree's new worktree (none for one it enters by path), and an
Agent's under `isolation: "worktree"`; the worktree an ExitWorktree removes;
the write sites of code a Python,
Perl, Ruby or Node call runs, and the commands it starts in the directory
they run in; the build directory of a heavy command that writes), through
`realpath`, to the git tree holding it (`.git` internals included, but for
worktree metadata: `git worktree add` is charged its new directory only, and
`git init DIR` or `git clone SRC DIR` their DIR, as the user ruled
2026-09-27). A path known up to an unknown part is charged to the directory
before it; one wholly unknown, to where the command runs; xargs's operands
where its list or producer places them, and a call handing a writer operands
from nowhere known is read as unreadable. A write into a tree
where a heavy run is live is refused, and so is an Edit, Write or
NotebookEdit there. A run is live in a tree when a heavy process's working
directory, or a directory its arguments name, lies in that tree or in a tree
holding it. A call that cannot be read is refused while a run is live in a
tree it names or runs in; a write only behind a flag the call does not pass
is still charged (the user ruled 2026-09-27: safe beats fast). Python this
hook cannot parse runs nothing, since Python compiles a module before it runs
a line, unless a newer interpreter runs it.

AFTER THE CALL, since reading text cannot foresee every write. While a run is
live, the tracked and untracked-but-not-ignored files of every live tree are
stat-ed before a Bash or Monitor call and again at its PostToolUse or
PostToolUseFailure. A move in a tree the call runs in or names (its cwd, a
cwd it moves to, an absolute or home path its text holds; a write it makes
into a live tree was refused before it ran) exits 2 as possibly its doing,
unless every program the call runs only reads and nothing it runs was out of
sight: then, as the user ruled 2026-09-28, it is a note. A move elsewhere is a
note too: additionalContext at exit 0, not necessarily the call's doing. A
background call's or a Monitor's Post comes at launch, so what it writes later
is caught at the agent's next Bash or Monitor call: each agent (a session's
subagents apart, by the agent_id the hook input carries) keeps a baseline of
the trees its calls ran in or named, as its last finished call saw them; a
move since then is reported beside the next call, and again until a Post
moves the baseline on, since a call another hook refused never shows its note.
A call's snapshot whose Post never came is removed after ten minutes, an
agent's baseline after a day.

FAILURE. A hook that cannot read its event allows the call. An internal error
allows the call too, but says so on stderr with exit 1, which Claude Code shows
as a hook error: an unguarded call is never silent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _commands as C  # noqa: E402
import _shell  # noqa: E402

ALLOWED_CPUS = frozenset(range(20))
DRIBBLE = ".commands-to-run.sh"
ALLOWED_BESIDE = {"cd", "pushd", "popd", "echo", "printf", "mkdir", "export", "set", "true", ":"}
# Programs that only read: a call running nothing else, with no write target and nothing out of sight, is not
# blamed for a move in its tree (the user's ruling, 2026-09-28).
READERS = {"cat", "ls", "eza", "grep", "egrep", "fgrep", "rg", "ugrep", "head", "tail", "wc", "sed", "find", "fd", "git", "echo", "printf", "print", "pwd", "test", "[", "stat", "file", "diff", "cmp", "sort",
           "uniq", "cut", "tr", "jq", "date", "du", "df", "which", "type", "command", "realpath", "readlink",
           "basename", "dirname", "true", "false", ":", "cd", "pushd", "popd", "export", "set", "unset", "local",
           "tree", "nl", "column", "comm", "join", "paste", "fold", "od", "xxd", "sha256sum", "md5sum", "cksum",
           "env", "printenv", "id", "whoami", "uname", "nproc", "getconf", "ps", "pgrep", "sleep", "wait", "read",
           "exit", "return", "bat", "time", "seq", "yes", "tee"}
STATE_DIR = Path(tempfile.gettempdir()) / "claude-heavy-run-guard"
STATE_MAX_AGE = 600  # a call's snapshot whose Post never came
SESSION_MAX_AGE = 86400  # a session's baseline, rewritten at every call it makes


# ─── where a command writes ───────────────────────────────────────────────────


@dataclass
class Target:
    path: str
    why: str


def _glob_dir(path: str) -> str:
    """A path with glob characters stands for the directory its first glob sits in."""
    if not re.search(r"[*?\[]", path):
        return path
    parts = path.split("/")
    for k, part in enumerate(parts):
        if re.search(r"[*?\[]", part):
            if k == 0:
                return "."
            return "/".join(parts[:k]) or "/"
    return path


_UNPLACED: list[str] = []  # paths met that start unknown: where they land cannot be told


def _known_part(word: str) -> str:
    """A path as far as it is known: the whole of it, or the directory before its first unknown part.

    A path that starts unknown is charged to where the command runs: that is where a relative
    path lands, and a write the reader cannot place is refused where a run is live there.
    """
    cut = word.find("$")
    if cut < 0:
        return _glob_dir(word)
    prefix = word[:cut]
    if "/" not in prefix:
        if cut == 0:
            _UNPLACED.append(word)
        return "."
    return _glob_dir(prefix[: prefix.rfind("/")] or "/")


def _at(paths: list[str | None], cwd: str | None, why: str) -> list[Target]:
    out = []
    for p in paths:
        if not p:
            continue
        r = C.resolve(_known_part(p), cwd)
        if r:
            out.append(Target(r, why))
    return out


def _flag_cluster(args: list[str], letter: str) -> bool:
    return any(re.fullmatch(rf"-[a-zA-Z]*{letter}[a-zA-Z]*", a) for a in args if not a.startswith("--"))


def _cluster_value(args: list[str], letter: str) -> str | None:
    """The value of a short option ending a cluster (`-sSLo f`, `-czf f`)."""
    for k, a in enumerate(args):
        if re.fullmatch(rf"-[a-zA-Z]*{letter}", a) and k + 1 < len(args):
            return args[k + 1]
    return None


_GIT_CLONE_VALUES = {"-b", "--branch", "--depth", "-o", "--origin", "--reference", "--reference-if-able",
                     "--separate-git-dir", "-c", "--config", "--filter", "-j", "--jobs", "--template", "-u",
                     "--upload-pack", "--shallow-since", "--shallow-exclude", "--server-option", "--bundle-uri"}
_GIT_INIT_VALUES = {"-b", "--initial-branch", "--template", "--separate-git-dir", "--object-format", "--ref-format",
                    "--shared"}
_GIT_READ_ONLY = {"status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "grep", "blame",
                  "describe", "merge-base", "cat-file", "for-each-ref", "shortlog", "rev-list", "name-rev",
                  "check-ignore", "check-attr", "show-ref", "var", "version", "help", "count-objects", "whatchanged",
                  "range-diff", "cherry", "annotate", "diff-tree", "diff-files", "diff-index", "ls-remote",
                  "verify-commit", "verify-tag", "fsck", "reflog", "difftool", "instaweb", "check-ref-format"}


def _git_targets(args: list[str], cwd: str | None) -> list[Target]:
    k = 0
    repo = cwd
    while k < len(args):
        a = args[k]
        if a == "-C" and k + 1 < len(args):
            repo = C.resolve(args[k + 1], repo)
            k += 2
            continue
        if a in ("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path") and k + 1 < len(args):
            if a in ("--work-tree", "--git-dir"):
                repo = C.resolve(args[k + 1], repo)
            k += 2
            continue
        if a.startswith(("--work-tree=", "--git-dir=")):
            repo = C.resolve(a.split("=", 1)[1], repo)
            k += 1
            continue
        if a.startswith("-"):
            k += 1
            continue
        break
    if k >= len(args) or repo is None:
        return []
    sub, rest = args[k], args[k + 1 :]
    ops = [r for r in rest if not r.startswith("-")]
    output = C._opt(rest, ("--output",))
    if output and sub in _GIT_READ_ONLY:  # `git log --output=FILE`, `git diff --output FILE`
        return _at([output], cwd, f"git {sub} --output")
    if sub == "reflog" and ops[:1] in (["expire"], ["delete"]):
        return [Target(repo, f"git reflog {ops[0]}")]
    if sub in _GIT_READ_ONLY:
        return []
    if sub == "worktree":
        # A worktree's metadata lives under .git/worktrees, which no build reads: the user ruled it exempt
        # (2026-09-27). What is charged is the worktree's own directory.
        words, k2 = [], 0
        while k2 < len(rest):
            if rest[k2] in ("-b", "-B", "--reason", "--expire") and k2 + 1 < len(rest):
                k2 += 2
                continue
            if not rest[k2].startswith("-"):
                words.append(rest[k2])
            k2 += 1
        verb, paths = (words[0] if words else ""), words[1:]
        dirs = paths[:1] if verb in ("add", "remove") else paths[:2] if verb == "move" else []
        return _at(dirs, repo, f"git worktree {verb}") if dirs else []
    if sub in ("clone", "init"):
        valued = _GIT_CLONE_VALUES if sub == "clone" else _GIT_INIT_VALUES
        words, k2 = [], 0
        while k2 < len(rest):  # an option's value is never the destination
            a = rest[k2]
            if a == "--":
                words += rest[k2 + 1 :]
                break
            if a in valued and k2 + 1 < len(rest):
                k2 += 2
                continue
            if not a.startswith("-"):
                words.append(a)
            k2 += 1
        dest = words[1] if sub == "clone" and len(words) > 1 else (words[0] if sub == "init" and words else None)
        if dest:  # the new repository is the one written, never the one the call runs in
            return _at([dest], repo, f"git {sub}")
        if sub == "init" or len(words) == 1:  # into the cwd, a directory named for the source
            return [Target(repo, f"git {sub}")]
    reading = {
        "stash": bool(rest) and rest[0] in ("list", "show"),
        "apply": any(r in ("--check", "--stat", "--numstat", "--summary") for r in rest),
        "submodule": not ops or ops[0] in ("status", "summary"),
        "branch": not ops or any(r in ("--list", "-l", "--show-current", "--contains", "--merged",
                                       "--no-merged", "-r", "-a", "-v", "-vv") for r in rest) and not any(
            r in ("-d", "-D", "-m", "-M", "-f", "-c", "-C", "--delete", "--move", "--force") for r in rest),
        "tag": not ops or any(r in ("-l", "--list", "-v", "--verify") for r in rest),
        "remote": not ops or ops[0] in ("show", "get-url") or rest == ["-v"],
        "config": any(r in ("--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin") for r in rest)
        or (len(ops) == 1 and not any(r in ("--unset", "--unset-all", "--add", "--replace-all", "--rename-section",
                                            "--remove-section", "-e", "--edit") for r in rest)),
        "notes": not ops or ops[0] in ("show", "list"),
        "clean": _flag_cluster(rest, "n") or "--dry-run" in rest,
        "hash-object": "-w" not in rest,
    }
    if reading.get(sub):
        return []
    targets = [Target(repo, f"git {sub}")]
    if sub == "format-patch":
        targets += _at([C._opt(rest, ("-o", "--output-directory")) or "."], cwd, "git format-patch")
    if sub == "archive":
        o = C._opt(rest, ("-o", "--output"))
        return _at([o], cwd, "git archive -o") if o else []
    return targets


def _perl_in_place(arg: str) -> bool:
    """Whether a perl option cluster holds -i: letters that take a value end the cluster."""
    if not arg.startswith("-") or arg.startswith("--"):
        return False
    k = 1
    while k < len(arg):
        c = arg[k]
        if c == "i":
            return True
        if c in "0l":
            k += 1
            while k < len(arg) and arg[k] in "01234567":
                k += 1
            continue
        if c in "MmIeExCdDFV":
            return False
        k += 1
    return False


def command_targets(argv: list[str], cwd: str | None) -> list[Target]:
    """The paths a (wrapper-free) command writes, from its own words."""
    if not argv:
        return []
    name = C.prog(argv[0])
    args = argv[1:]
    ops = C._operands(args)

    def at(paths: list[str | None], why: str, base: str | None = cwd) -> list[Target]:
        return _at(paths, base, why)

    if name in ("cp", "mv", "install", "ln", "rsync", "scp"):
        t = C._opt(args, ("-t", "--target-directory"))
        dest = [t] if t else ops[-1:]
        if name == "scp":
            ops = [o for o in ops if not re.match(r"[^/]*:", o)]
        if name == "ln" and len(ops) == 1 and not t:
            return at(["."], "ln")
        out = at(dest, f"{name} into it")
        if name == "mv" or (name == "rsync" and "--remove-source-files" in args):
            out += at(ops if t else ops[:-1], f"{name} out of it")
        return out
    if name in ("rm", "rmdir", "unlink", "shred", "touch", "mkdir", "tee", "mkfifo", "mknod"):
        values = {"touch": ("-d", "-t", "-r"), "mkdir": ("-m",), "mkfifo": ("-m",)}.get(name, ())
        skip = {args[k] for k in range(1, len(args)) if args[k - 1] in values}
        return at([o for o in ops if o not in skip], name)
    if name == "truncate":
        skip = {args[k + 1] for k, a in enumerate(args) if a in ("-s", "--size", "-r", "--reference")
                and k + 1 < len(args)}
        return at([o for o in ops if o not in skip], "truncate")
    if name in ("chmod", "chown", "chgrp"):
        return at(ops if any(a.startswith("--reference") for a in args) else ops[1:], name)
    if name == "sed":
        if not any(re.match(r"-[a-zA-Z0-9]*i", a) or a.startswith("--in-place") for a in args if
                   not a.startswith("--") or a.startswith("--in-place")):
            return []
        has_script_opt = any(a in ("-e", "-f", "--expression", "--file") or a.startswith(("--expression=",
                             "--file=")) for a in args)
        values = {args[k + 1] for k, a in enumerate(args) if a in ("-e", "-f", "-l", "--expression", "--file")
                  and k + 1 < len(args)}
        files = [o for o in ops if o not in values]
        return at(files if has_script_opt else files[1:], "sed -i")
    if name == "perl":
        if not any(_perl_in_place(a) for a in args):
            return []
        values = {args[k + 1] for k, a in enumerate(args) if re.fullmatch(r"-[a-zA-Z0-9]*[eEIM]", a)
                  and k + 1 < len(args)}
        files = [o for o in ops if o not in values]
        has_e = any(re.fullmatch(r"-[a-zA-Z0-9]*[eE]", a) for a in args)
        return at(files if has_e else files[1:], "perl -i")
    if name in ("awk", "gawk"):
        if "inplace" not in args:
            return []
        values = {args[k + 1] for k, a in enumerate(args) if a in ("-f", "-v", "-F", "-i") and k + 1 < len(args)}
        files = [o for o in ops if o not in values]
        return at(files[1:], "awk -i inplace")
    if name == "dd":
        return at([a[3:] for a in args if a.startswith("of=")], "dd of=")
    if name == "sort":
        return at([C._opt(args, ("-o", "--output"))], "sort -o")
    if name == "patch":
        if "--dry-run" in args or _flag_cluster(args, "C"):
            return []
        o = C._opt(args, ("-o", "--output"))
        return at([o], "patch -o") if o else at([C._opt(args, ("-d", "--directory")) or "."], "patch")
    if name in ("tar", "bsdtar"):
        first = args[0] if args else ""
        bundled = re.fullmatch(r"-?[a-zA-Z]+", first) is not None and not first.startswith("--")
        mode = set(first.lstrip("-")) if bundled else set()
        for a in args[1:]:  # `tar -C dir -xf a.tar`: the mode can sit in any short cluster
            if re.fullmatch(r"-[a-zA-Z]+", a):
                mode |= set(a[1:]) - {"C"}
        extract = "x" in mode or any(a in ("-x", "--extract", "--get") for a in args)
        if extract:
            if "O" in mode or any(a in ("-O", "--to-stdout") for a in args):
                return []
            return at([C._opt(args, ("-C", "--directory")) or "."], "tar -x")
        create = mode & {"c", "r", "u"} or any(a in ("-c", "--create", "-r", "--append", "-u", "--update")
                                                  for a in args)
        f = C._opt(args, ("-f", "--file")) or next((args[k + 1] for k, a in enumerate(args[:-1])
                                                    if re.fullmatch(r"-[a-zA-Z]*f", a)), None) or (
            ops[1] if bundled and "f" in mode and len(ops) > 1 else None)
        return at([f], "tar -f") if create and f and f != "-" else []
    if name == "unzip":
        if any(a in ("-l", "-t", "-Z", "-p", "-v") for a in args):
            return []
        return at([C._opt(args, ("-d",)) or "."], "unzip")
    if name == "zip":
        return at(ops[:1], "zip")
    if name == "curl":
        o = C._opt(args, ("-o", "--output")) or _cluster_value(args, "o")
        if o and o != "-":
            return at([o], "curl -o")
        if _flag_cluster(args, "O") or any(a in ("--remote-name", "--remote-name-all") for a in args):
            return at([C._opt(args, ("--output-dir",)) or "."], "curl -O")
        return []
    if name == "wget":
        o = C._opt(args, ("-O", "--output-document")) or _cluster_value(args, "O")
        if o == "-" or re.search(r"-[a-zA-Z]*O-$", " ".join(args)) or any(re.fullmatch(r"-[a-zA-Z]*O-", a)
                                                                          for a in args):
            return []
        if o:
            return at([o], "wget -O")
        return at([C._opt(args, ("-P", "--directory-prefix")) or "."], "wget")
    if name == "git":
        return _git_targets(args, cwd)
    if name == "gh":
        if args[:2] == ["pr", "checkout"]:
            return at(["."], "gh pr checkout")
        if args[:2] == ["run", "download"]:
            return at([C._opt(args, ("-D", "--dir")) or "."], "gh run download")
        if args[:2] in (["repo", "clone"], ["release", "download"]):
            ops2 = C._operands(args[2:])
            dest = ops2[1] if args[0] == "repo" and len(ops2) > 1 else C._opt(args, ("-D", "--dir"))
            return at([dest or "."], f"gh {args[0]} {args[1]}")
        return []
    if name == "cargo":
        k = 0
        while k < len(args) and args[k].startswith(("-", "+")):  # global options, some with a value
            k += 2 if args[k] in ("--color", "--config", "-Z", "--manifest-path", "-C", "--target-dir") else 1
        sub = args[k] if k < len(args) else None
        manifest = C._opt(args, ("--manifest-path",))
        crate = (os.path.dirname(manifest) or ".") if manifest else "."
        if sub == "fmt" and "--check" not in args:
            return at([crate], "cargo fmt")
        if sub in ("clean", "update", "add", "remove", "rm", "generate-lockfile", "fix", "init", "new"):
            return at([crate], f"cargo {sub}")
        return []
    if name == "ruff":
        if args[:1] == ["format"] and not {"--check", "--diff"} & set(args):
            return at(ops[1:] or ["."], "ruff format")
        if args[:1] == ["check"] and any(a in ("--fix", "--unsafe-fixes", "--fix-only") for a in args) \
                and "--diff" not in args:
            return at(ops[1:] or ["."], "ruff check --fix")
        return []
    if name in ("clang-format", "cmake-format") and "-i" in args:
        return at(ops, f"{name} -i")
    if name == "clang-tidy" and any(a in ("-fix", "--fix", "-fix-errors", "--fix-errors") for a in args):
        return at(ops, "clang-tidy -fix")
    if name in ("gofmt", "shfmt", "goimports") and "-w" in args:
        return at(ops, f"{name} -w")
    if name in ("black", "rustfmt") and not {"--check", "--diff"} & set(args):
        return at(ops, name)
    if name == "isort" and not {"--check", "--check-only", "--diff", "-c"} & set(args):
        return at(ops, name)
    if name == "prettier" and any(a in ("--write", "-w") for a in args):
        return at(ops, "prettier --write")
    if name == "go":
        d = C._opt(args, ("-C",))
        sub = next((a for a in args if not a.startswith("-") and a != d), None)
        if sub in ("fmt", "generate", "get", "clean") or (sub == "mod" and "tidy" in args and "-diff" not in args):
            return at([d or "."], f"go {sub}")
        return []
    if name in ("pip", "pip3") or (name == "uv" and args[:1] == ["pip"]):
        rest = args[1:] if name == "uv" else args
        if rest[:1] == ["install"]:
            editable = [args[k + 1] for k, a in enumerate(args) if a in ("-e", "--editable") and k + 1 < len(args)]
            return at([re.sub(r"\[.*\]$", "", e) for e in editable] + ["."], "pip install")
        return []
    if name == "cmake" and not {"--build", "-E", "-P", "--install", "-N", "--help", "--version", "--find-package",
                                    "--workflow"} & set(args):
        b = C._opt(args, ("-B",))  # a configure writes its build directory: -B, else the directory it runs in
        return at([b or "."], "cmake -B" if b else "cmake configure")
    if name == "find":
        out = []
        if "-delete" in args:
            roots = []
            for a in args:
                if a.startswith(("-", "(", "!")):
                    break
                roots.append(a)
            out += at(roots or ["."], "find -delete")
        return out
    return []


def redirect_targets(cmd: _shell.Command) -> list[Target]:
    out = []
    for r in cmd.redirects:
        if r.target is None or r.target.process_substitution:
            continue
        if r.op in (">", ">>", ">|", "&>", "&>>", "<>") or (r.op == ">&" and not re.fullmatch(r"\d+|-",
                                                                                               r.target.text)):
            word = r.target.value if r.target.value is not None else (r.target.partial or r.target.text)
            if word.startswith(("/dev/", "/proc/")):
                continue
            out += _at([word], cmd.cwd, f"a `{r.op}` redirection")
    return out


# Python write sites: a method on a path, open() for writing, and the os/shutil calls.
_STRING = re.compile(r"""(?:[rRbBfFuU]{0,2})(['"])((?:\\.|(?!\1)[^\\])*?)\1""")
# A mode sits in a string, so these are read on the raw text: perl's `open(F, ">x")`, ruby's `File.open(x, "w")`.
_OTHER_OPEN = re.compile(r"\bopen\s*\(?[^;\n]{0,200}['\"]\+?>|File\.open\s*\([^)\n]{0,200}['\"][wa]")
_OTHER_WRITES = re.compile(r"\bunlink\b|\brename\b|\bmkdir\b|\brmdir\b|writeFile|appendFile|unlinkSync|rmSync|"
                           r"renameSync|mkdirSync|copyFileSync|File\.write|FileUtils\.|File\.delete|File\.rename|"
                           r"\bmake_path\b|\bmkpath\b|\bremove_tree\b|\brmtree\b|\bcopy\s*\(|\bmove\s*\(|"
                           r"createWriteStream|\bsystem\b|\bexec(?:Sync|File|FileSync)?\b|\bspawn(?:Sync)?\b|"
                           r"`[^`]*`|\bqx\b|%x|IO\.popen|Open3")
_NODE_WRITES = re.compile(_OTHER_WRITES.pattern.replace("|`[^`]*`|\\bqx\\b|%x", ""))
# A command a Perl, Ruby or Node call starts (system, exec, backticks) is out of this reader's sight: the
# directory it runs in is charged, as for any write it cannot place.
# A word inside a string or a pattern is data, not a call: `print if /mkdir/` writes nothing.  Perl's quote-like
# operators and Ruby's %-literals take any delimiter; a bracket closes with its partner.
_Q = r"(?:(?<![$@%&*>])\b(?:m|qr|q|qq|qw|qx)|(?<![\w)\]}$])%[rqQwWiIx]?)"
_LITERAL = re.compile(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|"""
                      rf"""{_Q}\s*\{{[^}}]*\}}|{_Q}\s*\([^)]*\)|{_Q}\s*\[[^\]]*\]|{_Q}\s*<[^>]*>|"""
                      rf"""{_Q}([^\w\s{{(\[<])(?:\\.|(?!\1)[^\\])*\1|"""
                      r"""(?:\b(?:m|qr))?/(?:\\.|[^/\\\n])*/|\b(?:s|tr|y)/(?:\\.|[^/\\\n])*/(?:\\.|[^/\\\n])*/|"""
                      r"""\b(?:s|tr|y)\{[^}]*\}\s*\{[^}]*\}""")
_LITERAL_START = re.compile(r"""['"/]|\b(?:m|qr|q|qq|qw|qx|s|tr|y)\s*[^\w\s]|%[rqQwWiIx]?[^\w\s]""")
_DIVISION = re.compile(r"[\w)\]}]\s*$")  # a slash after an operand divides
_PATTERN_AFTER = re.compile(r"\b(?:if|unless|and|or|not|split|grep|when|while|until|return)\s*$")


def _mask_literals(code: str) -> str:
    """The code with every string and pattern body blanked, line breaks kept."""
    out, k = [], 0
    while (hit := _LITERAL_START.search(code, k)) is not None:
        start = hit.start()
        out.append(code[k:start])
        m = _LITERAL.match(code, start)
        before = code[max(0, start - 64) : start]
        if m is None and code[start] in "'\"":  # a string never closed: the rest is its body, so no call is there
            out.append(re.sub(r"[^\n]", " ", code[start:]))
            k = len(code)
            break
        if m is None or (code[start] == "/" and _DIVISION.search(before) and not _PATTERN_AFTER.search(before)):
            out.append(code[start])  # a slash that divides: what follows it is code again
            k = start + 1
            continue
        out.append(re.sub(r"[^\n]", " ", m.group(0)))
        k = m.end()
    out.append(code[k:])
    return "".join(out)


def _newer_python(interpreter: str) -> bool:
    """Whether an interpreter is a Python newer than the one running this hook."""
    try:
        found = shutil.which(interpreter) if "/" not in interpreter else interpreter
        name = os.path.basename(os.path.realpath(found)) if found else os.path.basename(interpreter)
    except (OSError, ValueError, UnicodeError):
        return False  # a name no file system holds runs nothing
    m = re.search(r"python(\d+)\.(\d+)", name)
    return m is not None and (int(m.group(1)), int(m.group(2))) > sys.version_info[:2]


def code_targets(lang: str, code: str, cwd: str | None, operands: list[str], why: str,
                 interpreter: str = "") -> list[Target]:
    if lang == "python":
        facts = C.python_facts(code, cwd)
        if not facts.parsed:
            # Python compiles a whole module before it runs a line: code this parser refuses runs nothing,
            # unless the interpreter running it is newer than the one reading it, or a shell expansion the
            # reader could not make stands in it and the code does not parse with a placeholder there either.
            return [Target(cwd, f"{why} this reader cannot parse")] if cwd and (
                isinstance(code, C.Pending) or _newer_python(interpreter)) else []
        targets: list[Target] = []
        unknown = False
        for site in facts.sites:
            if site is None:
                unknown = True
            else:
                targets += _at(list(site), cwd, why)
        if unknown:  # a write it cannot place: its arguments when it reads them, else where it runs
            targets += _at(list(operands) if facts.uses_argv and operands else ["."], cwd, why)
        for text, dirs in facts.commands:
            for d in dirs or [cwd]:
                if text is None:  # a command out of sight
                    if cwd:
                        targets.append(Target(cwd, f"{why}: a command it starts that could not be read"))
                    continue
                try:  # a directory out of sight: read the command there, its relative writes charged here
                    for inv in C.expand(text, d, depth=C.MAX_DEPTH - 1, dialect="sh"):
                        targets += invocation_targets(inv, cwd)
                except _shell.ParseError:
                    targets.append(Target(d or cwd, f"{why}: a command it starts"))
        return targets
    masked = _mask_literals(code)
    writes = _OTHER_WRITES if lang != "node" else _NODE_WRITES  # a backtick is a template literal in JavaScript
    if not (writes.search(masked) or _OTHER_OPEN.search(code)):
        return []
    paths = []
    for line, bare in zip(code.splitlines(), masked.splitlines()):
        if writes.search(bare) or _OTHER_OPEN.search(line):
            paths += [m.group(2) for m in _STRING.finditer(line) if "/" in m.group(2) or "." in m.group(2)]
    return _at(paths, cwd, why) if paths else ([Target(cwd, why)] if cwd else [])


def invocation_targets(inv: C.Inv, event_cwd: str | None = None) -> list[Target]:
    cmd, core = inv.cmd, inv.core
    lost = cmd.cwd is None
    _UNPLACED.clear()
    if lost and event_cwd:
        cmd.cwd = event_cwd  # a cd the reader could not follow: charge where the call started
    targets = redirect_targets(cmd)
    if not core.argv:
        return targets
    if any(a in ("--help", "--version") for a in core.argv[1:]):
        return targets
    argv = core.argv
    if C.is_python(argv[0]) and "-m" in argv:
        k = argv.index("-m")
        if k + 1 < len(argv) and argv[k + 1] in ("ruff", "black", "isort", "pip", "clang_format"):
            argv = [argv[k + 1].replace("_", "-")] + argv[k + 2 :]
    # Under xargs the operands arrive on stdin: where the reader placed them (an -a or `<` list, find's roots,
    # git's directory), or, when it could not, nowhere known.  A copy's stdin brings its sources, which it only
    # reads; the destination is in its own words.
    from_stdin = core.via_xargs and C.prog(argv[0]) not in ("cp", "ln", "install", "rsync", "scp")
    placed = inv.xargs_paths if from_stdin else []
    targets += command_targets(argv + (placed if placed is not None else ["."]), cmd.cwd)
    if placed is None and command_targets(argv + ["."], cmd.cwd) and not inv.unreadable:
        inv.unreadable = "the paths xargs reads on its input"
    if inv.code is not None:
        lang, code = inv.code
        targets += code_targets(lang, code, cmd.cwd, inv.code_args, f"{lang} code", core.argv[0])
    h = inv.heavy
    if h is not None and h.writes and cmd.cwd:
        where = C.resolve(h.out_dir, cmd.cwd) if h.out_dir else cmd.cwd
        if where:
            targets.append(Target(where, f"starts {h.name}, which writes its outputs"))
    if lost and targets and not inv.unreadable:  # and it may land in any tree the call names
        inv.unreadable = "the directory an earlier cd moved to"
    if _UNPLACED and not inv.unreadable:  # a write to a path wholly unknown may land in any tree it names
        inv.unreadable = f"a path it computes ({_UNPLACED[0][:40]})"
    return targets


# ─── live runs ────────────────────────────────────────────────────────────────


tree_of = _shell.tree_of


@dataclass
class Run:
    pid: int
    what: str
    roots: set[str]


_DIR_OPTIONS = ("--manifest-path", "--test-dir", "--build", "--directory", "--target-dir", "-C", "-B", "-S")


def live_runs() -> list[Run]:
    me = {os.getpid(), os.getppid()}
    runs = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit() or int(entry.name) in me:
            continue
        try:
            with open(f"/proc/{entry.name}/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        if not argv:
            continue
        core = C.unwrap(argv)
        if core.script is not None:
            continue  # a shell running -c text: the programs it starts are processes of their own
        try:
            cwd = os.readlink(f"/proc/{entry.name}/cwd")
        except OSError:
            continue
        h = C.heavy(core.argv, cwd)  # its own cwd: `bash run_all.sh` started inside probes/
        if h is None:
            continue
        paths = [cwd]
        args = core.argv[1:]
        for k, a in enumerate(args[:64]):
            value = None
            if a in _DIR_OPTIONS and k + 1 < len(args):
                value = args[k + 1]
            elif "=" in a and a.split("=", 1)[0] in _DIR_OPTIONS:
                value = a.split("=", 1)[1]
            elif a[:2] in ("-C", "-B", "-S") and len(a) > 2:
                value = a[2:]
            elif a.startswith("/"):
                value = a
            if value:
                full = C.resolve(value, cwd)
                if full and os.path.exists(full):
                    paths.append(full)
        roots = {t for t in (tree_of(p) for p in paths) if t}
        if roots:
            runs.append(Run(int(entry.name), " ".join(argv)[:160], roots))
    return runs


def runs_in(tree: str, runs: list[Run]) -> list[Run]:
    """The runs live in `tree`: a run's tree is it, or holds it."""
    return [r for r in runs if any(tree == root or tree.startswith(root + "/") for root in r.roots)]


def exempt(path: str, tree: str) -> bool:
    try:
        return os.path.realpath(path) == os.path.join(tree, DRIBBLE)
    except (ValueError, OSError):
        return False


# ─── decisions ────────────────────────────────────────────────────────────────


def _fd_table(cmd: _shell.Command) -> dict[int, str]:
    """Where stdout and stderr end, following bash's order: inherited redirections, the pipe, its own."""
    fds = {1: "the terminal", 2: "the terminal"}

    def kind(w: _shell.Word | None) -> str:
        if w is None or w.process_substitution:
            return "a process substitution"
        v = w.value
        if v is None:
            return "file"  # an unexpanded variable: the recommended "$LOG" shape
        if v == "/dev/null":
            return "/dev/null"
        if v.startswith("/dev/shm/"):
            return "file"
        if v.startswith(("/dev/", "/proc/")):
            return v
        return "file"

    for r in cmd.redirects:
        fd = r.fd
        if r.op in ("|", "|&"):
            fds[1] = "a pipe"
            if r.op == "|&":
                fds[2] = "a pipe"
        elif r.op in (">", ">>", ">|"):
            fds[1 if fd is None else fd] = kind(r.target)
        elif r.op in ("&>", "&>>"):
            fds[1] = fds[2] = kind(r.target)
        elif r.op == ">&":
            t = r.target.text if r.target else ""
            if re.fullmatch(r"[0-9]+", t):
                fds[1 if fd is None else fd] = fds.get(int(t), "a closed descriptor")
            elif t == "-":
                fds[1 if fd is None else fd] = "a closed descriptor"
            elif fd is None:
                fds[1] = fds[2] = kind(r.target)
            else:
                fds[fd] = kind(r.target)
    return fds


def shape_problems(invs: list[C.Inv], tool: str) -> list[str]:
    shaped = [i for i in invs if i.origin in ("call", "script")]
    hidden = [(i, C.heavy_in_text(i.core.script, i.cmd.cwd)) for i in shaped if i.unreadable and i.core.script]
    problems = [f"the {i.core.script_lang} text of `{' '.join(i.core.argv[:2])[:40]}` runs {name}, which the guard "
                "cannot check there; run it from bash" for i, name in hidden if name]
    heavy_invs = [i for i in shaped if i.heavy is not None]
    if not heavy_invs:
        return problems
    names = ", ".join(sorted({i.heavy.name for i in heavy_invs}))
    if tool == "Monitor":
        return [f"`{names}` inside a Monitor script: Monitor watches work, it does not start it. Start it with "
                "Bash and run_in_background, and watch its log."]
    for inv in heavy_invs:
        name = inv.heavy.name + ("" if inv.origin == "call" else " (in a script this call runs)")
        if inv.core.bad_taskset:
            problems.append(f"`{name}`: {inv.core.bad_taskset}; name CPUs within 0-19 (`taskset -c 0-19`)")
        elif inv.core.cpus is None:
            problems.append(f"`{name}` is not under `taskset -c 0-19` (rule 3: CPUs 20-23 are the user's)")
        elif not inv.core.cpus <= ALLOWED_CPUS:
            extra = sorted(inv.core.cpus - ALLOWED_CPUS)
            problems.append(f"`{name}` may run on CPUs {extra}, outside 0-19 (rule 3)")
        fds = _fd_table(inv.cmd)
        if fds.get(1) != "file" or fds.get(2) != "file":
            problems.append(f"`{name}`: its output goes to {fds.get(1)} and its errors to {fds.get(2)}; both "
                            "must end in the log file (rule 1: `> \"$LOG\" 2>&1`, in that order)")
        if inv.cmd.pipe_in:
            problems.append(f"`{name}` reads from a pipe; run it alone")
        if inv.cmd.background or inv.core.detached:
            how = inv.core.detached or "`&`"
            problems.append(f"`{name}` is detached with {how}, which outlives the call; use the Bash tool's "
                            "run_in_background instead")
    call = [i for i in invs if i.origin == "call"]
    first_heavy = next((k for k, i in enumerate(call) if i.heavy is not None), len(call))
    others = set()
    for k, i in enumerate(call):
        if i.heavy is not None or i.carrier:
            continue
        if not i.core.argv:
            if i.cmd.depth > 0 and any(r.op == "<" for r in i.cmd.redirects):
                others.add("$(< file)")
            continue
        p = C.prog(i.core.argv[0])
        if p in ALLOWED_BESIDE:
            continue
        if i.cmd.depth > 0 and p in ("nproc", "getconf"):
            continue  # a CPU count feeding a CPU list: the list itself is what the shape check names
        if i.cmd.depth > 0 and i.cmd.feeds in ("assign", "cd", "redirect") and k < first_heavy:
            continue  # a substitution that feeds a setup step before the run
        others.add(p)
    if others:
        problems.append(f"the heavy command shares its call with other work ({', '.join(sorted(others))}); give "
                        "it a call of its own and query its log in a later one")
    if any(i.core.argv and C.prog(i.core.argv[0]) == "head" for i in call):
        problems.append("`head` in a call that starts a heavy run (rule 2)")
    return problems


def python_started(invs: list[C.Inv]) -> list[C.Inv]:
    """The commands the Python code of the call's own invocations starts, read as more of the call."""
    out: list[C.Inv] = []
    for inv in invs:
        if not (inv.code and inv.code[0] == "python" and inv.origin in ("call", "script")):
            continue
        for text, dirs in C.python_facts(inv.code[1], inv.cmd.cwd).commands:
            for d in (dirs or [inv.cmd.cwd]) if text is not None else []:
                try:
                    started = C.expand(text, d or inv.cmd.cwd, depth=C.MAX_DEPTH - 1, dialect="sh")
                except _shell.ParseError:
                    continue
                for s2 in started:
                    s2.origin = inv.origin
                out += started
    return out


def started_problems(started: list[C.Inv]) -> list[str]:
    """Rules 1 and 2 for a heavy run a Python driver starts: its output reaches the driver's log, not a pipe."""
    problems = []
    for s2 in started:
        if s2.heavy is not None and any(r.op in ("|", "|&") for r in s2.cmd.redirects):
            problems.append(f"`{s2.heavy.name}`, started from Python, sends its output into a pipe (rule 1)")
        if s2.heavy is not None and _fd_table(s2.cmd).get(1) == "/dev/null":
            problems.append(f"`{s2.heavy.name}`, started from Python, discards its output (rule 1)")
        if s2.heavy is not None and (s2.cmd.background or s2.core.detached):
            problems.append(f"`{s2.heavy.name}`, started from Python, runs in the background past the call")
        if s2.heavy is not None and (s2.core.bad_taskset or (s2.core.cpus is not None and
                                                              not s2.core.cpus <= ALLOWED_CPUS) or any(
                cpus is None or not cpus <= ALLOWED_CPUS for cpus, _ in s2.core.cpu_sets)):  # numactl -C too
            problems.append(f"`{s2.heavy.name}`, started from Python, may run on CPUs 20-23 (rule 3)")
        if s2.core.argv and C.prog(s2.core.argv[0]) == "head" and any(x.heavy for x in started):
            problems.append("`head` in a command Python starts beside a heavy run (rule 2)")
    return problems


def cpu_problems(invs: list[C.Inv]) -> list[str]:
    """Rule 3 over every command the call runs, heavy or not: a CPU set that names 20-23 is the user's.

    A list the call writes that cannot be read is refused too. One computed inside a repository's own script
    from `nproc` follows the affinity the script is given, so only an explicit 20-23 there is refused, and a
    count of the machine (getconf, `nproc --all`), which does not follow it.
    """
    problems = []
    for inv in invs:
        own = inv.origin in ("call", "script")
        if own and inv.heavy is not None:
            continue  # the shape check names a heavy command's CPUs
        for cpus, text in inv.core.cpu_sets:  # only a taskset or numactl the command runs under
            where = "" if own else " (in a repository script this call runs)"
            if cpus is None:
                if own:
                    problems.append(f"the CPU set `{text[:40]}` cannot be read before it runs{where}; name CPUs "
                                    "within 0-19")
                elif re.search(r"getconf|nproc\s+--all|cpu_count", text):
                    problems.append(f"`{text[:40]}` counts the machine's CPUs, not those the script was given"
                                    f"{where}: it reaches 20-23 under `taskset -c 0-19`")
            elif not cpus <= ALLOWED_CPUS:
                problems.append(f"`{text[:40]}` names CPUs {sorted(cpus - ALLOWED_CPUS)}{where}: CPUs 20-23 are "
                                "the user's, never used unless the user says so")
    return problems


CPU_ADVICE = """BLOCKED (rule 3): a CPU set that reaches the user's CPUs.

{problems}

CPUs 20-23 stay free for the user. Name CPUs within 0-19 (`taskset -c 0-19`)."""


SHAPE_ADVICE = """BLOCKED: a heavy command in a shape the user's rules refuse.

{problems}

The shape that passes, in a call of its own (run_in_background keeps the turn free):
  cd <repo> && taskset -c 0-19 <heavy command> > <scratchpad>/<name>.log 2>&1; echo "EXIT=$?" >> <scratchpad>/<name>.log
Then query the log in SEPARATE calls (grep -c, grep -n, sed -n with a range), never through head."""


def midrun_problems(targets: list[Target], runs_cache: list[list[Run]]) -> list[str]:
    by_tree: dict[str, list[Target]] = {}
    for t in targets:
        tree = tree_of(t.path)
        if tree is None or exempt(t.path, tree):
            continue
        by_tree.setdefault(tree, []).append(t)
    if not by_tree:
        return []
    if not runs_cache:
        runs_cache.append(live_runs())
    problems = []
    for tree, ts in by_tree.items():
        live = runs_in(tree, runs_cache[0])
        if not live:
            continue
        what = "; ".join(sorted({f"{os.path.relpath(t.path, tree) if t.path != tree else '.'} ({t.why})"
                                 for t in ts})[:6])
        runs = "\n    ".join(f"pid {r.pid}: {r.what}" for r in live[:5])
        problems.append(f"writes into {tree} ({what}) while a heavy run is live there:\n    {runs}")
    return problems


MIDRUN_ADVICE = """BLOCKED (rule 4, safe beats fast): {problems}

Wait for the run's completion notification, then make the edit; draft it in the scratchpad meanwhile. If the
edit is what the run needs, stop the run (TaskStop), edit, and relaunch. If these processes are the user's, the
edit waits for them too. The dribble ({dribble}) may be written meanwhile."""


def named_trees(text: str, cwd: str | None) -> set[str]:
    """The trees a call's text runs in or names: its cwd's, and every absolute or home path's."""
    paths = [cwd] if cwd else []
    home = os.environ.get("HOME", "")
    for m in re.finditer(r"(?<![\w.\-/~}])((?:~|\$HOME|\$\{HOME\})?/[^\s'\";|&<>(),:]+)", text):
        p = re.sub(r"^(?:~|\$HOME|\$\{HOME\})", lambda _: home, m.group(1))  # a path under home names its tree too
        if os.path.exists(os.path.dirname(p) or "/"):
            paths.append(p)
    return {t for t in map(tree_of, paths) if t}


def _lines(problems: list[str], mark: str = "") -> str:
    """Each problem once, in the order found: a loop read once per value repeats its body's."""
    return "\n".join(mark + p for p in dict.fromkeys(problems))


_OUT_OF_MEMORY = _shell.BudgetExceeded(f"more than {_shell.MAX_MEMORY >> 20} MiB to read it")


def unreadable_call(exc: Exception, command: str, cwd: str, runs_cache: list[list[Run]]) -> int:
    """A call that cannot be read: refused while a run is live in a tree it names (rule 4), and, as a carrier's
    unreadable text is, when a heavy command stands in its text (rules 1-3)."""
    msg = unreadable_problem(f"this call could not be read as shell ({exc})", command, cwd, runs_cache)
    found = [i.heavy for i in getattr(exc, "found", []) if i.heavy is not None]  # read before the budget ran out
    heavy = None if msg else (found[0] if found else C.heavy_in_text(command, cwd))
    if heavy:
        msg = SHAPE_ADVICE.format(problems=f"  * `{heavy.name}`, in a call this hook could not read ({exc}): give "
                                           "it a call of its own")
    if msg:
        print(msg, file=sys.stderr)
        return 2
    return 0


def unreadable_problem(what: str, text: str, cwd: str, runs_cache: list[list[Run]]) -> str | None:
    """Refuse a call that cannot be read, while a run is live in a tree it runs in or names."""
    trees = named_trees(text, cwd)
    if not trees:
        return None
    if not runs_cache:
        runs_cache.append(live_runs())
    live = [t for t in trees if runs_in(t, runs_cache[0])]
    if not live:
        return None
    return (f"BLOCKED (rule 4): {what}, and a heavy run is live in {', '.join(sorted(live))}, so what this call "
            "writes cannot be told. Split it into plainer calls, or wait for the run.")


# ─── the after-call check ─────────────────────────────────────────────────────


def _call_key(event: dict) -> str:
    key = event.get("tool_use_id")
    if not key:
        raw = event.get("tool_input")
        ti = dict(raw) if isinstance(raw, dict) else {"input": raw}
        if isinstance(ti.get("command"), str):
            ti["command"] = re.sub(r"^rtk\s+(proxy\s+)?", "", ti["command"])  # the rtk hook rewrites it
        blob = json.dumps([event.get("session_id"), event.get("tool_name"), ti], sort_keys=True, default=str)
        key = hashlib.sha256(blob.encode()).hexdigest()[:32]
    return re.sub(r"[^\w-]", "_", str(key))[:128]


def _baseline_path(event: dict) -> Path:
    """One baseline per agent: a session's subagents share its session id, and the hook input names each one."""
    who = f"{event.get('session_id') or 'none'}-{event.get('agent_id') or 'main'}"
    return STATE_DIR / ("session-" + re.sub(r"[^\w-]", "_", who)[:160] + ".json")


def _prune_state() -> None:
    try:
        now = time.time()
        for f in STATE_DIR.iterdir():
            age = SESSION_MAX_AGE if f.name.startswith("session-") else STATE_MAX_AGE
            if now - f.stat().st_mtime > age:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def _load(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and isinstance(state.get("trees"), dict) else None


def _save(path: Path, state: dict) -> None:
    """Written whole or not at all: parallel calls of one agent share its baseline."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def _moves(before: dict, now: dict) -> list[str]:
    """The files of trees held both times whose mtime, size or mode differ, the dribble aside."""
    moved = []
    for tree in sorted(set(before) & set(now)):
        b, n = before[tree], now[tree]
        moved += [os.path.join(tree, rel) for rel in sorted(set(b) | set(n)) if b.get(rel) != n.get(rel)]
    return [m for m in moved if os.path.basename(m) != DRIBBLE]


def _tree_files(tree: str) -> dict[str, list[int]]:
    listed = subprocess.run(["git", "-C", tree, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            capture_output=True, check=False, timeout=20,
                            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    out = {}
    for rel in listed.stdout.decode(errors="replace").split("\0"):
        if not rel:
            continue
        try:
            st = os.lstat(os.path.join(tree, rel))
        except OSError:
            out[rel] = [0, -1, 0]
            continue
        out[rel] = [st.st_mtime_ns, st.st_size, st.st_mode]
    return out


def _under(path: str, trees: list[str]) -> bool:
    return any(path == t or path.startswith(t + "/") for t in trees)


def _listing(moved: list[str], runs: list[dict]) -> str:
    """The moved files, and the runs live in the trees they moved in."""
    listed = "\n    ".join(moved[:20]) + ("" if len(moved) <= 20 else f"\n    ... and {len(moved) - 20} more")
    near = [r for r in runs if any(_under(m, r.get("roots", [])) for m in moved)]
    shown = "\n    ".join(f"pid {r['pid']}: {r['what']}" for r in near[:5])
    return f"    {listed}\n  The run(s):\n    {shown}" if shown else f"    {listed}"


def _context(event_name: str, text: str) -> None:
    """A note the model reads beside the call, at exit 0: no block, no error framing."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}))


RERUN = ("If a call of this session wrote them, the run read a tree that changed under it: its verdict is not a "
         "record of either state, so run it again once it ends.")


def checkpoint(event: dict, runs: list[Run], blame: set[str], reads_only: bool) -> str | None:
    """Before a Bash or Monitor call: snapshot the live trees for its Post, and compare this agent's baseline.

    The baseline is what this agent's last finished call saw, in the trees its calls run in or name. A move
    since then is one this agent saw no call of its own make: a background call's or a Monitor's (their Post
    comes at launch), a call's that failed or that another hook refused, a parallel call's, another agent's or
    session's, or the run's own step. It is reported beside this call; only a Post moves the baseline on, so a
    note given to a call that never ran is given again.
    """
    live = sorted({root for r in runs for root in r.roots})
    bpath = _baseline_path(event)
    base = _load(bpath) or {"trees": {}}
    if not live and not base["trees"]:
        return None
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _prune_state()
    now = {t: _tree_files(t) for t in sorted(set(live) | set(base["trees"])) if os.path.isdir(t)}  # a tree removed
    live = [t for t in live if t in now]  # since is not a tree that moved
    moved = _moves(base["trees"], now)
    shown = [{"pid": r.pid, "what": r.what, "roots": sorted(r.roots)} for r in runs]
    if live:
        _save(STATE_DIR / f"{_call_key(event)}.json",
              {"trees": {t: now[t] for t in live}, "runs": shown, "blame": sorted(blame), "reads_only": reads_only})
    else:
        bpath.unlink(missing_ok=True)  # the run ended: compared once more, then nothing is held
    if not moved:
        return None
    return ("heavy-run-guard (rule 4): these files moved since this agent's last call ended, while a heavy run was "
            "live for at least part of that time. None of this agent's calls was seen making the move: a background "
            "call or Monitor, a call that failed or was refused, a parallel call, another agent or session, or the "
            f"run's own step did. {RERUN}\n{_listing(moved, base.get('runs') or shown)}")


def after(event: dict) -> int:
    """After a Bash or Monitor call, failed or not: what moved in the live trees while it ran."""
    path = STATE_DIR / f"{_call_key(event)}.json"
    state = _load(path)
    if state is None:
        return 0
    path.unlink(missing_ok=True)
    now = {t: _tree_files(t) for t in state["trees"] if os.path.isdir(t)}  # a tree removed since did not move
    blame = [b for b in state.get("blame", []) if isinstance(b, str)]
    bpath = _baseline_path(event)
    base = _load(bpath) or {"trees": {}}
    mine = sorted(set(base.get("mine", [])) | set(blame))  # the trees this agent's calls run in or name
    _save(bpath, {"trees": {t: v for t, v in now.items() if _under(t, mine) or any(_under(m, [t]) for m in mine)},
                  "runs": state.get("runs", []), "mine": mine})
    moved = _moves(state["trees"], now)
    if not moved:
        return 0
    hit = [m for m in moved if _under(m, blame)]
    other = [m for m in moved if m not in hit]
    runs = state.get("runs", [])
    name = str(event.get("hook_event_name"))
    elsewhere = ("heavy-run-guard (rule 4): these files moved while this call ran, in live trees it neither runs in "
                 "nor names: not necessarily its doing (a background call, a Monitor, a parallel call, another agent "
                 f"or session, or the run's own step). {RERUN}\n{_listing(other, runs)}")
    if not hit:
        _context(name, elsewhere)
        return 0
    if state.get("reads_only"):  # every program it runs only reads, and nothing it runs was out of sight
        _context(name, "heavy-run-guard (rule 4): these files moved while this call ran, in trees it runs in or "
                       "names; the call only reads, so the move is not necessarily its doing (a background call, a "
                       f"Monitor, a parallel call, another agent or session, or the run's own step). {RERUN}\n"
                       f"{_listing(hit, runs)}" + (f"\n\n{elsewhere}" if other else ""))
        return 0
    print(f"heavy-run-guard (rule 4): these files moved while this call ran, in trees it runs in or names, while a "
          f"heavy run was live:\n{_listing(hit, runs)}\nIf this call wrote them, the run read a tree that changed "
          "under it: its verdict is not a record of either state, so run it again once it ends. (A step of the run "
          "itself, or a background call of this session, can also move a file there.)"
          + (f"\n\n{elsewhere}" if other else ""), file=sys.stderr)
    return 2


# ─── entry ────────────────────────────────────────────────────────────────────


def before(event: dict) -> int:
    tool = event.get("tool_name")
    ti = event.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    cwd_raw = event.get("cwd")
    cwd = cwd_raw if isinstance(cwd_raw, str) and cwd_raw else os.getcwd()
    runs_cache: list[list[Run]] = []
    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        path = ti.get("file_path") or ti.get("notebook_path")
        if not isinstance(path, str) or not path:
            return 0
        full = C.resolve(os.path.expanduser(path), cwd)
        if not full:
            return 0
        problems = midrun_problems([Target(full, tool)], runs_cache)
        if problems:
            print(MIDRUN_ADVICE.format(problems=_lines(problems), dribble=DRIBBLE), file=sys.stderr)
            return 2
        return 0
    if tool in ("EnterWorktree", "Agent"):  # a new worktree is <repository>/.claude/worktrees/<name>: a write
        if tool == "Agent" and ti.get("isolation") != "worktree" or isinstance(ti.get("path"), str) and ti.get("path"):
            return 0  # an agent sharing the session's tree, or a worktree that exists entered: nothing is written
        tree = tree_of(cwd) if cwd else None
        name = ti.get("name") if isinstance(ti.get("name"), str) and ti.get("name") else "worktree"
        problems = midrun_problems([Target(os.path.join(tree, ".claude", "worktrees", name), tool)],
                                   runs_cache) if tree else []
        if problems:
            print(MIDRUN_ADVICE.format(problems=_lines(problems), dribble=DRIBBLE), file=sys.stderr)
            return 2
        return 0
    if tool == "ExitWorktree":  # `remove` deletes the worktree the session is in, and its branch; `keep` nothing
        tree = tree_of(cwd) if cwd and ti.get("action") == "remove" else None
        problems = midrun_problems([Target(tree, "ExitWorktree removes it")], runs_cache) if tree else []
        if problems:
            print(MIDRUN_ADVICE.format(problems=_lines(problems), dribble=DRIBBLE), file=sys.stderr)
            return 2
        return 0
    if tool not in ("Bash", "Monitor"):
        return 0
    command = ti.get("command")
    if not isinstance(command, str) or not command.strip():
        return 0
    try:
        invs = C.expand(command, cwd)
        started = python_started(invs)  # the commands its Python starts share the call's budget
    except (_shell.ParseError, _shell.BudgetExceeded) as exc:
        return unreadable_call(exc, command, cwd, runs_cache)
    except MemoryError:
        invs = None  # answered below, once leaving this block has freed what the reading built
    except Exception as exc:  # noqa: BLE001 - a reader fault: unreadable while a run is live, reported otherwise
        msg = unreadable_problem(f"this call could not be read ({type(exc).__name__}: {exc})", command, cwd,
                                 runs_cache)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        print(f"heavy-run-guard: internal error reading this call, it goes ahead unchecked: {type(exc).__name__}: "
              f"{exc}", file=sys.stderr)
        return 1
    if invs is None:
        return unreadable_call(_OUT_OF_MEMORY, command, cwd, runs_cache)
    problems = cpu_problems(invs + started)
    if problems:
        print(CPU_ADVICE.format(problems=_lines(problems, "  * ")), file=sys.stderr)
        return 2
    problems = shape_problems(invs, tool) + started_problems(started)
    if problems:
        print(SHAPE_ADVICE.format(problems=_lines(problems, "  * ")), file=sys.stderr)
        return 2
    try:
        targets = [t for inv in invs for t in invocation_targets(inv, cwd)]
    except _shell.BudgetExceeded as exc:  # the code an interpreter runs, read last, spent the budget
        return unreadable_call(exc, command, cwd, runs_cache)
    except MemoryError:
        targets = None
    if targets is None:
        return unreadable_call(_OUT_OF_MEMORY, command, cwd, runs_cache)
    # The trees a move is blamed on: those the call names or runs in (a write into a live one was refused above).
    blame = named_trees(command, cwd) | {t for t in (tree_of(i.cmd.cwd) for i in invs + started if i.cmd.cwd) if t}
    problems = midrun_problems(targets, runs_cache)
    unreadable = [f"{inv.unreadable}" for inv in invs if inv.unreadable]
    if unreadable and not problems:
        # The trees it may reach: those the call names, and those the unreadable commands name or run in.
        reach = "\n".join([command] + [" ".join(i.cmd.argv) + " " + (i.cmd.cwd or "") for i in invs if i.unreadable])
        msg = unreadable_problem(f"this call runs {', '.join(unreadable)}, which could not be read", reach, cwd,
                                 runs_cache)
        if msg:
            print(msg, file=sys.stderr)
            return 2
    if problems:
        print(MIDRUN_ADVICE.format(problems=_lines(problems), dribble=DRIBBLE), file=sys.stderr)
        return 2
    if not runs_cache:
        runs_cache.append(live_runs())
    reads_only = not targets and not unreadable and not started and all(
        C.prog(i.core.argv[0]) in READERS and i.heavy is None and i.code is None for i in invs if i.core.argv)
    note = checkpoint(event, runs_cache[0], blame, reads_only)
    if note:
        _context("PreToolUse", note)
    return 0


def main() -> int:
    _shell.bound_memory()
    try:
        event = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return 0  # a hook that cannot read the event must not block the session
    if not isinstance(event, dict):
        return 0
    name = event.get("hook_event_name")
    if name not in ("PreToolUse", "PostToolUse", "PostToolUseFailure", None):
        return 0  # an event with no name is read as PreToolUse; the rest are nothing for this hook to decide
    try:
        if name in ("PostToolUse", "PostToolUseFailure"):
            return after(event)
        return before(event)
    except Exception as exc:  # noqa: BLE001 - an unguarded call is reported, never silent
        print(f"heavy-run-guard: internal error, the call goes ahead unguarded: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
