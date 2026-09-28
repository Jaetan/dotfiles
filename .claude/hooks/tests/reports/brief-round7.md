> Historical. The user stopped this round on 2026-09-28 and retired review rounds in favour of runtime
> enforcement (a session scope, isolated runs, a log and a cleanup per call). Kept as the record of what round 7
> set out to test.

# Round 7 breaker brief (shared by every lens)

You are one lens of an adversarial review of two Claude Code PreToolUse/PostToolUse hooks that guard this
user's Bash calls. Find where they are wrong. Report only what you prove.

## What is under test

- Installed hooks: `/home/nicolas/.claude/hooks/` (a symlink to `/home/nicolas/dotfiles/.claude/hooks/`):
  `heavy-run-guard.py`, `no-polling-loops.py`, and the readers they share, `_shell.py`, `_commands.py`,
  `_python.py`. Read their module docstrings first: every sentence there is a claim you may test.
- The user's rules they enforce (heavy-run-guard.py's docstring states them): a heavy command writes its
  whole output to a file, never through `head`, under `taskset -c 0-19`, alone in its call; no file in a git
  tree is written while a heavy run is live in it; no shell polling loop; no background job left running.
- The suite: `tests/test_guards.py` (1186 cases) and `tests/test_memory_bound.py`. The round-6 reports and
  their outcome: `tests/reports/round6.txt`. Do not re-report what round 6 found unless it is still wrong.

## How to probe (all of it binding)

- Pipe an event into a hook by its path, e.g.
  `{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":CMD},"cwd":CWD,"session_id":S}`.
  Exit 2 blocks, 0 allows, 1 is an internal error that lets the call through.
- Run every probe driver under a memory cap and on CPUs 0-19: `(ulimit -v 4000000; taskset -c 0-19 python3 ...)`.
  The hooks cap themselves at 1 GiB; your driver must still be capped. Never start a real heavy command.
- Build your own test trees under your scratchpad (git init them). To make a run "live", start a harmless
  sleeper whose argv is `["cargo", "test"]` (e.g. `exec -a` or a copied `sleep` binary named cargo) with its
  cwd in the tree, under `unshare -rpf --mount-proc` if you do not want the session's own hooks to see it;
  kill it in a `finally`.
- Pass `_ZO_DATA_DIR=<your scratchpad>/zoxide` in the hook's environment: never touch the user's zoxide data.
- Do not edit anything under `/home/nicolas/dotfiles`, `/home/nicolas/.claude/hooks` or
  `/home/nicolas/dev/agda/aletheia`. No network. No `git push`, no commits.
- Save every driver and its log in your scratchpad; cite them in your report.

## What to report

Write your report to `/home/nicolas/dotfiles/.claude/hooks/tests/reports/round7-<lens>.txt` (plain text,
the only file you may write outside your scratchpad) and also return it as your final answer. For each finding:
the case (exact CMD and CWD, run live or not), observed exit code and first stderr line, expected, the cause
in the code (file:function), realism (how likely a real session writes this; say if the recorded corpus holds
it), and the smallest fix. Order by realism. Say explicitly at the end whether your lens found anything
realistic ("not dry") or nothing ("dry").
