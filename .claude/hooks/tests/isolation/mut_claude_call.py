#!/home/nicolas/.local/bin/python3.14
"""Mutation run over claude-call and its zshenv line: each mutant must fail at least one case of the suite.

usage: mut_claude_call.py [ID...]   (on CPUs 0-19, under the soft address-space cap the suite asks for)

A mutant is one exact replacement in a copy of the wrapper, the snippet or the rmdir interposer, with the suite's
cases that guard it; the suite runs against the copies (CLAUDE_CALL_UNDER_TEST, CLAUDE_CALL_SNIPPET_UNDER_TEST; the
wrapper builds the interposer from the source beside it). A replacement that does not match exactly once is an error
of this list, not a surviving mutant. Exit 0 when every mutant is killed.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).with_name("test_claude_call.py")
W, Z, C = "claude-call", "claude-call.zshenv", "claude-call-rmdir.c"
MUTANTS = [
    ("stop-after-call", W, "    stop()  # the call's leftovers hold its pipes: stopping them ends the pumps",
     "    pass", ["leftover"]),
    ("no-conflict-check", W,
     "                if lstat_key(self.top / p) != self.real.get(p):\n                    conflicts.append(p)\n"
     "                    keep",
     "                if False:\n                    conflicts.append(p)\n                    keep", ["conflict"]),
    ("no-preload", W, '        return [*argv, *preload(), "--chdir", self.cwd]',
     '        return [*argv, "--chdir", self.cwd]', ["live_dir_and_rm"]),
    ("no-asan-option", W, '[*options, "verify_asan_link_order=0"]', '[*options, "verify_asan_link_order=1"]',
     ["asan"]),
    ("marker-trusted", W, "        return marker is not None and marker.stat().st_size == 0", "        return False",
     ["fail_open"]),
    ("marker-always-empty", W, "        return marker is not None and marker.stat().st_size == 0",
     "        return marker is not None", ["fail_open"]),
    ("marker-unwritten", W, """'printf 1 > "$0" && exec "$@"'""", """'exec "$@"'""", ["fail_open"]),
    ("c-unlinkat-errno", C, "return rc != 0 && errno == EBUSY && (flags & AT_REMOVEDIR)",
     "return rc != 0 && errno == EPERM && (flags & AT_REMOVEDIR)", ["live_dir_and_rm"]),
    ("c-rmdir-errno", C, "    return rc != 0 && errno == EBUSY && empty_mount_point(AT_FDCWD, path) ? 0 : rc;",
     "    return rc != 0 && errno == EPERM && empty_mount_point(AT_FDCWD, path) ? 0 : rc;", ["live_dir_and_rm"]),
    ("c-mkdir-errno", C, "    int rc = real(path, mode);\n    return rc != 0 && errno == EEXIST",
     "    int rc = real(path, mode);\n    return rc != 0 && errno == EPERM", ["live_dir_and_rm"]),
    ("c-mkdirat-errno", C, "    int rc = real(dirfd, path, mode);\n    return rc != 0 && errno == EEXIST",
     "    int rc = real(dirfd, path, mode);\n    return rc != 0 && errno == EPERM", ["live_dir_and_rm"]),
    ("c-mount-id", C, "parent.stx_mnt_id != self.stx_mnt_id", "parent.stx_mnt_id == self.stx_mnt_id",
     ["live_dir_and_rm"]),
    ("c-content-ignored", C, "                    result = 0;\n                    break;",
     "                    break;", ["live_dir_and_rm"]),
    ("preload-stacked", W, 'if p and p != str(built)]', "if p]", ["nested_calls"]),
    ("asan-stacked", W, 'if o and o != "verify_asan_link_order=0"]', "if o]", ["nested_calls"]),
    ("scope-expands", W, '"--collect", "--expand-environment=no", ', '"--collect", ', ["text_verbatim"]),
    ("nested-dropped", W, "| gitlinks | nested\n", "| gitlinks\n", ["nested_repo"]),
    ("link-as-dir", W, "if rel + d not in self.mounts and d not in links]", "if rel + d not in self.mounts]",
     ["symlink_dir"]),
    ("lock-copied", W, 'p.endswith(".lock") or ', "", ["scattered"]),
    ("big-copied", W, "BIND_BYTES = 1 << 20", "BIND_BYTES = 1 << 30", ["scattered"]),
    ("gnu-tar", W, '"tar", "--format=posix", ', '"tar", ', ["mtime_and_outputs"]),
    ("no-output-dirs", W, "                live.add(outputs[-1])", "                pass", ["mtime_and_outputs"]),
    ("git-env", W, 'GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}',
     "GIT_ENV = dict(os.environ)", ["git_env"]),
    ("no-view", W, "            view = View(top, cwd, ident, unit)", "            view = None",
     ["isolated_from_edits"]),
    ("no-delete", W, "                    os.unlink(self.top / p)", "                    pass", ["sync_back"]),
    ("no-replace", W, "                os.replace(tmp, self.top / p)", "                pass", ["sync_back"]),
    ("no-mode", W, "        shutil.copy2(src, dst)", "        shutil.copyfile(src, dst)", ["sync_back"]),
    ("no-new-dirs", W, "                (self.top / d).mkdir(parents=True, exist_ok=True)", "                pass",
     ["new_cwd"]),
    ("no-cwd-dir", W, "            (self.snap / rel).mkdir(parents=True, exist_ok=True)", "            pass",
     ["new_cwd"]),
    ("unsynced-unrecorded", W, "                view.record_unsynced(log)", "                pass", ["signal"]),
    ("signal-no-stop", W, "    def on_signal(signum: int, _frame: object) -> None:\n        stop()",
     "    def on_signal(signum: int, _frame: object) -> None:\n        pass", ["signal"]),
    ("no-log-output", W, "            log.write(chunk)", "            pass", ["log"]),
    ("prune-count", W, "len(logs) - i > KEEP_LOGS - 1", "len(logs) - i > KEEP_LOGS + 50", ["prune"]),
    ("prune-age", W, "now - mtime > KEEP_DAYS * 86400 or", "False or", ["prune"]),
    ("prune-bytes", W, " or total > KEEP_BYTES:", ":", ["prune"]),
    ("no-reap", W, "            if start_time(int(pid)) != started:", "            if False:", ["stale_view"]),
    ("no-fail-open", W, '        os.execv(ZSH, [ZSH, "-c", sys.argv[1]])', "        sys.exit(1)", ["fail_open"]),
    ("cwd-lost", W, '*started, ZSH, "-c", line]', '*started, ZSH, "-c", line.replace("pwd -P >|", "true >|")]',
     ["cd_persists"]),
    ("snippet-nesting", Z, "&& -z $CLAUDE_CALL ", "", ["off_and_nesting"]),
    ("snippet-off", Z, "&& ! -e ${CLAUDE_CALL_OFF:-$HOME/.claude/claude-call.off}", "", ["off_and_nesting"]),
    ("snippet-shape", Z,
     '\n      && $ZSH_EXECUTION_STRING == "source "*/shell-snapshots/snapshot-zsh-*" && pwd -P >| "*', "", ["shape"]),
]

chosen = [m for m in MUTANTS if len(sys.argv) == 1 or m[0] in sys.argv[1:]]
survivors, errors = [], []
with tempfile.TemporaryDirectory(prefix="mut-claude-call-") as tmp:
    for ident, target, old, new, cases in chosen:
        files = {name: (HOOKS / name).read_text() for name in (W, Z, C)}
        if files[target].count(old) != 1:
            errors.append(ident)
            print(f"ERROR {ident}: the replacement matches {files[target].count(old)} times", flush=True)
            continue
        files[target] = files[target].replace(old, new)
        for name, text in files.items():
            (Path(tmp) / name).write_text(text)
            (Path(tmp) / name).chmod(0o755)
        env = dict(os.environ, CLAUDE_CALL_UNDER_TEST=f"{tmp}/{W}", CLAUDE_CALL_SNIPPET_UNDER_TEST=f"{tmp}/{Z}")
        p = subprocess.run([sys.executable, str(SUITE), *cases], env=env, capture_output=True, text=True,
                           timeout=900, check=False)
        killed = p.returncode != 0
        print(f"{'KILLED ' if killed else 'SURVIVED'} {ident} ({', '.join(cases)})", flush=True)
        if not killed:
            survivors.append(ident)
print(f"{len(chosen) - len(survivors) - len(errors)} killed, {len(survivors)} survived, {len(errors)} list errors"
      + (": " + ", ".join(survivors + errors) if survivors or errors else ""))
sys.exit(1 if survivors or errors else 0)
