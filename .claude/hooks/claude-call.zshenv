# Sourced from ~/.zshenv. Claude Code's Bash tool runs each call as `zsh -c '<its line>'` with CLAUDECODE set, and
# this execs claude-call around that line: a scope, a log, a view of the repository. Only a line of the tool's own
# shape; never inside a wrapped call (CLAUDE_CALL); not while ~/.claude/claude-call.off exists; and not when the
# wrapper or its interpreter is missing, since a failed exec would end the shell and the call with it.
if [[ -n $CLAUDECODE && -z $CLAUDE_CALL && ! -e ${CLAUDE_CALL_OFF:-$HOME/.claude/claude-call.off}
      && $ZSH_EXECUTION_STRING == "source "*/shell-snapshots/snapshot-zsh-*" && pwd -P >| "*
      && -x ${CLAUDE_CALL_BIN:-$HOME/.claude/hooks/claude-call} && -x $HOME/.local/bin/python3.14 ]]; then
  exec ${CLAUDE_CALL_BIN:-$HOME/.claude/hooks/claude-call} "$ZSH_EXECUTION_STRING"
fi
