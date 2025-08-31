export PATH="$HOME/.local/bin:$PATH"

# --- guard: only for interactive shells ------------------------------
[[ $- != *i* ]] && return

# --- paths ------------------------------------------------------------
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

# --- colors on by default --------------------------------------------
export CLICOLOR=1
export GREP_OPTIONS=             # (unset deprecated var)
export GREP_COLORS='mt=01;36'    # cyan-ish matches on dark bg
export LESS='-R --mouse -F -X -M' # -R colors, -F/X smart paging, -M long prompt

# Use bat as man pager (pretty, with syntax highlight)
export MANPAGER="sh -c 'col -bx | bat -l man -p'"

# --- history: big, deduped, timestamped, shared ----------------------
export HISTCONTROL=ignoredups:erasedups
export HISTSIZE=200000
export HISTFILESIZE=200000
export HISTTIMEFORMAT='%F %T '
shopt -s histappend              # append rather than overwrite
PROMPT_COMMAND="history -a; history -c; history -r; $PROMPT_COMMAND"

# --- bash behavior tweaks --------------------------------------------
set -o noclobber                  # prevent accidental '>' clobbers
shopt -s checkwinsize             # fixes wrapped lines on resize
shopt -s cmdhist                  # multi-line cmds as one history entry
shopt -s extglob                  # extended globs
shopt -s globstar                 # ** recursive glob
shopt -s dirspell                 # fix minor dir typos
shopt -s cdspell                  # ditto for 'cd'

# --- completion -------------------------------------------------------
# stock bash-completion
if [ -f /usr/share/bash-completion/bash_completion ]; then
  . /usr/share/bash-completion/bash_completion
fi

# fzf: keybindings + completion (Ctrl-R fuzzy history, ** fuzzy paths)
if [ -f /usr/share/doc/fzf/examples/key-bindings.bash ]; then
  . /usr/share/doc/fzf/examples/key-bindings.bash
fi
if [ -f /usr/share/doc/fzf/examples/completion.bash ]; then
  . /usr/share/doc/fzf/examples/completion.bash
fi

# --- direnv (project envs auto-load) ---------------------------------
eval "$(direnv hook bash)"

# --- zoxide (supercharged cd) ----------------------------------------
if command -v zoxide >/dev/null 2>&1; then
  eval "$(zoxide init bash)"
fi

# --- aliases: sensible, color, dark-friendly -------------------------
alias ls='eza --group-directories-first --icons=auto --git -F'
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

# --- prompt: minimal, informative ------------------------------------
# Pure-bash prompt: shows [time] [exit] [git] [cwd] [duration if >2s]
__pr_exit_color() { [[ $1 -eq 0 ]] && echo 32 || echo 31; }   # green/red
__pr_git() {
  command -v git >/dev/null || return
  local b dirty
  b=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || return
  git diff --quiet --ignore-submodules HEAD 2>/dev/null; dirty=$?
  [[ $dirty -ne 0 ]] && echo "  ${b}*" || echo "  ${b}"
}
__TIMER_START=0
PROMPT_COMMAND='__ELAPSED=""; __END=$SECONDS; if (( __TIMER_START )); then
  __DT=$(( __END-__TIMER_START )); (( __DT>2 )) && __ELAPSED=" ⏱ ${__DT}s";
fi; __TIMER_START=$SECONDS; history -a'
PS1='\[\e[90m\][\t]\[\e[0m\] \[\e[$(__pr_exit_color $?)m\]•\[\e[0m\]\[$(tput sgr0)\]$(__pr_git) \[\e[36m\]\w\[\e[0m\]$__ELAPSED\n\$ '

# --- lesspipe (better 'less' content) --------------------------------
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# --- LS_COLORS (dark theme) ------------------------------------------
# fallback dark-friendly LS_COLORS if none set
if [ -z "${LS_COLORS:-}" ]; then
  eval "$(dircolors -b)"
  export LS_COLORS="${LS_COLORS}:di=01;34:ln=01;36:so=35:pi=33:ex=01;32:bd=01;33:cd=01;33:or=31;mi=01;31"
fi

export FZF_DEFAULT_COMMAND='fd --type f --hidden --follow --exclude .git'
export FZF_CTRL_T_OPTS="--preview 'bat --style=plain --color=always --line-range :200 {}'"

# mcd: make & cd
mcd(){ mkdir -p -- "$1" && cd -- "$1"; }

# mkvenv: quickly spin a Python venv
mkvenv(){ python3 -m venv .venv && . .venv/bin/activate && pip -q install -U pip wheel; }

# extract: unzip/untar anything
extract(){ 
  case "$1" in
    *.tar.bz2) tar xjf "$1" ;;
    *.tar.gz)  tar xzf "$1" ;;
    *.bz2)     bunzip2 "$1" ;;
    *.rar)     unrar x "$1" ;;
    *.gz)      gunzip "$1" ;;
    *.tar)     tar xf "$1" ;;
    *.tbz2)    tar xjf "$1" ;;
    *.tgz)     tar xzf "$1" ;;
    *.zip)     unzip "$1" ;;
    *.7z)      7z x "$1" ;;
    *)         echo "don't know how to extract '$1'" ;;
  esac
}

# mkcdtmp: temp dir you can nuke later
mkcdtmp(){ local d; d=$(mktemp -d) && cd "$d" && pwd; }
