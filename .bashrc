# ~/.bashrc — Catppuccin Mocha edition (WSL)
# ------------------------------------------------------------
# Interactive guard
[[ $- != *i* ]] && return

# --- PATHs ---------------------------------------------------
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

# Language toolchain PATHs (mirrors fish config)
for p in \
  "$HOME/.cargo/bin" \
  "$HOME/.npm-global/bin" \
  "$HOME/.rvm/bin" \
  "$HOME/.local/share/coursier/bin" \
  "$HOME/go/bin" \
  "$HOME/.juliaup/bin" \
  "$HOME/.ghcup/bin" \
  "$HOME/.cabal/bin" \
  "$HOME/.atuin/bin"; do
  [ -d "$p" ] && [[ ":$PATH:" != *":$p:"* ]] && export PATH="$p:$PATH"
done

# --- ssh-agent via keychain ----------------------------------
if command -v keychain >/dev/null 2>&1; then
  if [ -f ~/.ssh/id_ed25519 ]; then
    eval "$(keychain --eval --quiet --agents ssh --inherit any ~/.ssh/id_ed25519)"
  else
    eval "$(keychain --eval --quiet --agents ssh --inherit any)"
  fi
fi

# --- Editor & pager / colors ---------------------------------
export EDITOR=/home/nicolas/nvim
export CLICOLOR=1
export LESS='-R --mouse -F -X -M'
export GREP_COLORS='ms=01;36'
export LS_COLORS="di=01;34:ln=01;36:so=33:pi=33:ex=01;32:bd=01;33:cd=01;33:or=01;31:mi=01;31"
export BAT_THEME="catppuccin-mocha"
export MANPAGER="sh -c 'col -bx | bat -l man -p'"

# --- lesspipe (better 'less') --------------------------------
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# --- History: big, deduped, timestamped, shared ---------------
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

# --- Tool fallbacks for Debian/Ubuntu naming quirks -----------
if ! command -v bat >/dev/null 2>&1 && command -v batcat >/dev/null 2>&1; then
  alias bat='batcat'
fi
if ! command -v fd >/dev/null 2>&1 && command -v fdfind >/dev/null 2>&1; then
  alias fd='fdfind'
fi

# --- fzf (bindings + completion) ------------------------------
if [ -f /usr/share/doc/fzf/examples/key-bindings.bash ]; then
  . /usr/share/doc/fzf/examples/key-bindings.bash
fi
if [ -f /usr/share/doc/fzf/examples/completion.bash ]; then
  . /usr/share/doc/fzf/examples/completion.bash
fi

# fzf + preview with bat (Catppuccin Mocha)
export FZF_DEFAULT_COMMAND='fd --type f --hidden --follow --exclude .git 2>/dev/null || find . -type f'
export FZF_DEFAULT_OPTS="
  --height=80%
  --border
  --preview-window=right,60%,border
  --color=bg+:#313244,bg:#1E1E2E,spinner:#F5E0DC,hl:#F38BA8
  --color=fg:#CDD6F4,header:#F38BA8,info:#CBA6F7,pointer:#F5E0DC
  --color=marker:#B4BEFE,fg+:#CDD6F4,prompt:#CBA6F7,hl+:#F38BA8
  --color=selected-bg:#45475A,border:#6C7086,label:#CDD6F4
"
export FZF_CTRL_T_OPTS="--preview 'bat --style=plain --color=always --line-range :200 {}'"
export FZF_ALT_C_COMMAND='fd --type d --hidden --follow --exclude .git 2>/dev/null || find . -type d'
export FZF_ALT_C_OPTS="--preview 'eza --tree --color=always {} | head -200'"

# --- direnv (project env auto-load) --------------------------
if command -v direnv >/dev/null 2>&1; then
  eval "$(direnv hook bash)"
fi

# --- zoxide (smarter cd) -------------------------------------
if command -v zoxide >/dev/null 2>&1; then
  eval "$(zoxide init bash)"
  alias cd='z'
  alias zz='z -'
fi

# --- atuin (history: searchable, deduped) ---------------------
if [ -f "$HOME/.atuin/bin/env" ]; then
  . "$HOME/.atuin/bin/env"
fi
if [[ -f ~/.bash-preexec.sh ]]; then
  . ~/.bash-preexec.sh
fi
if command -v atuin >/dev/null 2>&1; then
  eval "$(atuin init bash)"
fi

# --- thefuck (command-line correction) ------------------------
if command -v thefuck >/dev/null 2>&1; then
  eval "$(thefuck --alias)"
  eval "$(thefuck --alias fk)"
  eval "$(thefuck --alias dwim)"
fi

# --- uv completions ------------------------------------------
if command -v uv >/dev/null 2>&1; then
  eval "$(uv generate-shell-completion bash)"
fi
if command -v uvx >/dev/null 2>&1; then
  eval "$(uvx --generate-shell-completion bash)"
fi

# --- nvm ------------------------------------------------------
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# --- OCaml (opam) --------------------------------------------
if [ -r "$HOME/.opam/opam-init/init.sh" ]; then
  . "$HOME/.opam/opam-init/init.sh" >/dev/null 2>&1
fi

# --- mise (version manager) ----------------------------------
if command -v mise >/dev/null 2>&1; then
  eval "$(mise activate bash)"
fi

# --- envman ---------------------------------------------------
if [ -f "$HOME/.config/envman/load.sh" ]; then
  . "$HOME/.config/envman/load.sh"
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
alias shake='cabal run shake --'
alias emacs-tag='~/.emacs.d/scripts/tag-current.sh'
alias please='sudo $(fc -ln -1)'
# safer coreutils
alias rm='rm -I --preserve-root'
alias mv='mv -i'
alias cp='cp -i'

# --- Helper functions -----------------------------------------
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

# gdel: remove deleted files from git index
gdel(){ git ls-files -d -z | git update-index --remove -z --stdin; }

# gmod: stage only modified files; interactive picker if fzf is available
gmod(){
  local files
  mapfile -d '' files < <(git -c core.quotepath=off ls-files -m -z)
  if [ ${#files[@]} -eq 0 ]; then
    echo "No modified files."
    return 0
  fi
  if command -v fzf >/dev/null 2>&1; then
    local preview='if [ -d {} ]; then eza --tree --color=always {} | head -200; else bat -n --color=always --line-range :500 {}; fi'
    local pick
    pick=$(printf '%s\n' "${files[@]}" | fzf --multi --preview "$preview")
    if [ -n "$pick" ]; then
      echo "$pick" | xargs git add --
    else
      echo "No selection."
    fi
  else
    git add -- "${files[@]}"
  fi
}

# gnew: stage only new/untracked files; interactive picker if fzf is available
gnew(){
  local files
  mapfile -d '' files < <(git -c core.quotepath=off ls-files --others --exclude-standard -z)
  if [ ${#files[@]} -eq 0 ]; then
    echo "No new untracked files."
    return 0
  fi
  if command -v fzf >/dev/null 2>&1; then
    local preview='if [ -d {} ]; then eza --tree --color=always {} | head -200; else bat -n --color=always --line-range :500 {}; fi'
    local pick
    pick=$(printf '%s\n' "${files[@]}" | fzf --multi --preview "$preview")
    if [ -n "$pick" ]; then
      echo "$pick" | xargs git add --
    else
      echo "No selection."
    fi
  else
    git add -- "${files[@]}"
  fi
}

# gunstage: unstage files; interactive picker if fzf is available; --all to unstage everything
gunstage(){
  if [ "$1" = "--all" ] || [ "$1" = "-a" ]; then
    git restore --staged :/
    return
  fi
  local files
  mapfile -d '' files < <(git -c core.quotepath=off diff --name-only --cached -z)
  if [ ${#files[@]} -eq 0 ]; then
    echo "Nothing is staged."
    return 0
  fi
  if command -v fzf >/dev/null 2>&1; then
    local preview='if [ -d {} ]; then eza --tree --color=always {} | head -200; else git --no-pager diff --staged --color=always -- {} | delta || git --no-pager diff --staged --color=always -- {}; fi'
    local pick
    pick=$(printf '%s\n' "${files[@]}" | fzf --multi --preview "$preview")
    if [ -n "$pick" ]; then
      echo "$pick" | xargs git restore --staged --
    else
      echo "No selection."
    fi
  else
    git restore --staged -- "${files[@]}"
  fi
}

# --- WSL clipboard helpers -----------------------------------
if [ -n "$WSL_DISTRO_NAME" ]; then
  pbcopy(){ clip.exe < /dev/stdin; }
  pbpaste(){ powershell.exe -NoProfile -Command Get-Clipboard | tr -d '\r'; }
fi
export WSLSYS="$([ -r /proc/sys/fs/binfmt_misc/WSLInterop ] && echo 1 || echo 0)"

# --- Readline keybindings (Ctrl-P/N prefix search) -----------
bind '"\C-p": history-search-backward' 2>/dev/null
bind '"\C-n": history-search-forward' 2>/dev/null

# --- Starship prompt ------------------------------------------
if command -v starship >/dev/null 2>&1; then
  eval "$(starship init bash)"
fi

# --- Claude Code ----------------------------------------------
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=100000

# ------------------------------------------------------------
