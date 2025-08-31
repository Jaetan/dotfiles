# ~/.bashrc — Tokyo Night Night edition (WSL)
# ------------------------------------------------------------
# Interactive guard
[[ $- != *i* ]] && return

# --- PATHs ---------------------------------------------------
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

# --- Colors & pager -----------------------------------------
export CLICOLOR=1
export LESS='-R --mouse -F -X -M'        # color, smart paging, verbose prompt
export GREP_COLORS='ms=01;36'            # cyan matches on dark bg
export LS_COLORS="di=01;34:ln=01;36:so=33:pi=33:ex=01;32:bd=01;33:cd=01;33:or=01;31:mi=01;31"

# --- bat (syntax highlighting) -------------------------------
# After you install the tokyonight theme (via .tmTheme + `bat cache --build`)
# this env var makes it the default everywhere (manpager, fzf previews, aliases).
export BAT_THEME="tokyonight_night"
export MANPAGER="sh -c 'col -bx | bat -l man -p'"

# --- History: big, deduped, timestamped, shared --------------
export HISTCONTROL=ignoredups:erasedups
export HISTSIZE=200000
export HISTFILESIZE=200000
export HISTTIMEFORMAT='%F %T '
shopt -s histappend cmdhist

# --- Bash behavior tweaks ------------------------------------
set -o noclobber
shopt -s checkwinsize extglob globstar dirspell cdspell

# --- Completion ----------------------------------------------
if [ -f /usr/share/bash-completion/bash_completion ]; then
  . /usr/share/bash-completion/bash_completion
fi

# --- fzf (bindings + completion) ------------------------------
if [ -f /usr/share/doc/fzf/examples/key-bindings.bash ]; then
  . /usr/share/doc/fzf/examples/key-bindings.bash
fi
if [ -f /usr/share/doc/fzf/examples/completion.bash ]; then
  . /usr/share/doc/fzf/examples/completion.bash
fi

# Tokyo Night Night colors for fzf UI + bat preview
export FZF_DEFAULT_COMMAND='fd --type f --hidden --follow --exclude .git 2>/dev/null || find . -type f'
export FZF_DEFAULT_OPTS="
  --height=80%
  --border
  --preview-window=right,60%,border
  --color=fg:#c0caf5,bg:#1a1b26,hl:#7dcfff
  --color=fg+:#c0caf5,bg+:#292e42,hl+:#bb9af7
  --color=info:#7aa2f7,prompt:#7dcfff,pointer:#f7768e,marker:#e0af68,spinner:#bb9af7,header:#565f89
"
# Ctrl-T preview via bat in tokyonight_night
export FZF_CTRL_T_OPTS="--preview 'bat --style=plain --color=always --line-range :200 {}'"

# --- direnv (project env auto-load) --------------------------
if command -v direnv >/dev/null 2>&1; then
  eval "$(direnv hook bash)"
fi

# --- zoxide (smarter cd) -------------------------------------
if command -v zoxide >/dev/null 2>&1; then
  eval "$(zoxide init bash)"
fi

# --- Aliases --------------------------------------------------
if command -v eza >/dev/null 2>&1; then
  alias ls='eza --group-directories-first --icons=auto --git -F'
else
  alias ls='ls --color=auto -F'
fi
alias ll='ls -lh'
alias la='ls -lha'
alias lt='ls --tree'
alias cat='bat --paging=never'
alias grep='grep --color=auto'
alias rg='rg --hidden --smart-case'
alias fd='fd --hidden --follow --exclude .git'
alias ..='cd ..'
alias ...='cd ../..'
alias gs='git status -sb'
alias gd='git diff'
alias gl='git log --oneline --graph --decorate'
alias please='sudo $(fc -ln -1)'
# safer coreutils
alias rm='rm -I --preserve-root'
alias mv='mv -i'
alias cp='cp -i'

# --- Helper functions ----------------------------------------
mcd(){ mkdir -p -- "$1" && cd -- "$1"; }
mkvenv(){ python3 -m venv .venv && . .venv/bin/activate && pip -q install -U pip wheel; }
extract(){
  case "$1" in
    *.tar.bz2) tar xjf "$1" ;; *.tar.gz) tar xzf "$1" ;; *.bz2) bunzip2 "$1" ;;
    *.rar) unrar x "$1" ;; *.gz) gunzip "$1" ;; *.tar) tar xf "$1" ;;
    *.tbz2) tar xjf "$1" ;; *.tgz) tar xzf "$1" ;; *.zip) unzip "$1" ;;
    *.7z) 7z x "$1" ;; *) echo "don't know how to extract '$1'";;
  esac
}
mkcdtmp(){ local d; d=$(mktemp -d) && cd "$d" && pwd; }

# --- lesspipe (better 'less') --------------------------------
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# --- Prompt: Tokyo Night Night harmonized --------------------
# Palette mapping: timestamp=gray(60), cwd=cyan(117), git=magenta(141),
# success/fail bullet=green(114)/red(204), timer=yellow(179).
__pr_exit_color() { [[ $1 -eq 0 ]] && echo 38\;5\;114 || echo 38\;5\;204; }
__pr_git() {
  command -v git >/dev/null || return
  local b dirty
  b=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || return
  git diff --quiet --ignore-submodules HEAD 2>/dev/null; dirty=$?
  if [[ $dirty -ne 0 ]]; then
    printf ' \e[38;5;141m %s*\e[0m' "$b"
  else
    printf ' \e[38;5;141m %s\e[0m' "$b"
  fi
}
__TIMER_START=0
__prompt_update() {
  # history: append, then reload for shared history across terminals
  builtin history -a
  builtin history -c
  builtin history -r
  # elapsed timer
  local elapsed=""
  local end=$SECONDS
  if (( __TIMER_START )); then
    local dt=$(( end - __TIMER_START ))
    (( dt > 2 )) && elapsed="\[\e[38;5;179m\] ⏱ ${dt}s\[\e[0m\]"
  fi
  __TIMER_START=$SECONDS
  PS1="\[\e[38;5;60m\][\t]\[\e[0m\] \[\e[$(__pr_exit_color $?)m\]•\[\e[0m\]$(__pr_git) \[\e[38;5;117m\]\w\[\e[0m\]${elapsed}\n\$ "
}
PROMPT_COMMAND="__prompt_update"

# --- WSL niceties --------------------------------------------
# speed up interop lookups a bit; harmless outside WSL
export WSLSYS="$([ -r /proc/sys/fs/binfmt_misc/WSLInterop ] && echo 1 || echo 0)"

# --- End ------------------------------------------------------

export PATH="$HOME/.local/bin:$PATH"

. "$HOME/.atuin/bin/env"

[[ -f ~/.bash-preexec.sh ]] && source ~/.bash-preexec.sh
eval "$(atuin init bash)"
