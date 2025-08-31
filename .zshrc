# PATH="${PATH:+${PATH}:}~/opt/bin"   # appending
# PATH="~/opt/bin${PATH:+:${PATH}}"   # prepending

[[ -d "$HOME/bin" && $PATH != *$HOME/bin* ]] && PATH="$HOME/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.cargo/bin" && $PATH != *$HOME/.cargo/bin* ]] && PATH="$HOME/.cargo/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.npm-global/bin" && $PATH != *$HOME/.npm-global/bin* ]] && PATH="$HOME/.npm-global/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.local/bin" && $PATH != *$HOME/.local/bin* ]] && PATH="$HOME/.local/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.rvm/bin" && $PATH != *$HOME/.rvm/bin* ]] && PATH="$HOME/.rvm/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.local/share/coursier/bin" && $PATH != *$HOME/.local/share/coursier/bin* ]] && PATH="$HOME/.local/share/coursier/bin${PATH:+:${PATH}}"
[[ -d "$HOME/go/bin" && $PATH != *$HOME/go/bin* ]] && PATH="$HOME/go/bin${PATH:+:${PATH}}"

# --- zinit (zsh plugin manager) ---
ZINIT_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}/zinit/zinit.git"
if [ ! -d "$ZINIT_HOME" ] ; then
  mkdir -p "$(dirname $ZINIT_HOME)"
  git clone https://github.com/zdharma-continuum/zinit.git "$ZINIT_HOME"
fi
source "${ZINIT_HOME}/zinit.zsh"

zinit light zsh-users/zsh-syntax-highlighting
zinit light zsh-users/zsh-completions
zinit light zsh-users/zsh-auto-suggestions
zinit light Aloxaf/fzf-tab

zinit snippet OMZP::git
zinit snippet OMZP::sudo
zinit snippet OMZP::archlinux
zinit snippet OMZP::aws
zinit snippet OMZP::kubectl
zinit snippet OMZP::kubectx
zinit snippet OMZP::command-not-found

fpath+=~/.zfunc

autoload -U compinit && compinit

zinit cdreplay -q

eval "$(oh-my-posh init zsh --config $HOME/.config/ohmyposh/nico.toml)"
# eval "$(oh-my-posh init zsh --config https://raw.githubusercontent.com/JanDeDobbeleer/oh-my-posh/refs/heads/main/themes/powerlevel10k_rainbow.omp.json)"

bindkey -e
bindkey '^p' history-search-backward
bindkey '^n' history-search-forward

HISTSIZE=5000
HISTFILE="~/.zsh_history"
SAVEHIST="$HISTSIZE"
HISTDUP=erase
setopt appendhistory
setopt sharehistory
setopt hist_ignore_space
setopt hist_ignore_all_dups
setopt hist_save_no_dups
setopt hist_ignore_dups
setopt hist_find_no_dups

zstyle ':completion:*' matcher-list 'm:{a-z}={A-Za-z}'
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"
zstyle ':completion:*' menu no
zstyle ':fzf-tab:complete:cd' fzf-preview 'ls --color $realpath'
zstyle ':fzf-tab:complete:__zoxide_z:*' fzf-preview 'ls --color $realpath'

# --- eza and ls ----
alias eza='eza --color=always --long --git --icons=always'
eval $(dircolors -b)
alias ls='ls --color=auto'
alias ll='eza'

# --- Bat (better cat) ---
alias bat=batcat
export BAT_THEME=tokyonight_night

# --- thefuck (command-line correction) ---
eval "$(thefuck --alias dwim)"

# Shell integrations
eval "$(fzf --zsh)"
eval "$(zoxide init --cmd cd zsh)"

# --- setup fzf theme ---
fg="#CBE0F0"
bg="#011628"
bg_highlight="#143652"
purple="#B388FF"
blue="#06BCE4"
cyan="#2CF9ED"

export FZF_DEFAULT_OPTS="--color=fg:${fg},bg:${bg},hl:${purple},fg+:${fg},bg+:${bg_highlight},hl+:${purple},info:${blue},prompt:${cyan},pointer:${cyan},marker:${cyan},spinner:${cyan},header:${cyan}"

# -- Use fd instead of fzf --

export FZF_DEFAULT_COMMAND="fd --hidden --strip-cwd-prefix --exclude .git"
export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
export FZF_ALT_C_COMMAND="fd --type=d --hidden --strip-cwd-prefix --exclude .git"

# Use fd (https://github.com/sharkdp/fd) for listing path candidates.
# - The first argument to the function ($1) is the base path to start traversal
# - See the source code (completion.{bash,zsh}) for the details.
_fzf_compgen_path() {
  fd --hidden --exclude .git . "$1"
}

# Use fd to generate the list for directory completion
_fzf_compgen_dir() {
  fd --type=d --hidden --exclude .git . "$1"
}

source ~/fzf-git.sh/fzf-git.sh

show_file_or_dir_preview="if [ -d {} ]; then eza --tree --color=always {} | head -200; else bat -n --color=always --line-range :500 {}; fi"

export FZF_CTRL_T_OPTS="--preview '$show_file_or_dir_preview'"
export FZF_ALT_C_OPTS="--preview 'eza --tree --color=always {} | head -200'"

# Advanced customization of fzf options via _fzf_comprun function
# - The first argument to the function is the name of the command.
# - You should make sure to pass the rest of the arguments to fzf.
_fzf_comprun() {
  local command=$1
  shift

  case "$command" in
    cd)           fzf --preview 'eza --tree --color=always {} | head -200' "$@" ;;
    export|unset) fzf --preview "eval 'echo \${}'"         "$@" ;;
    ssh)          fzf --preview 'dig {}'                   "$@" ;;
    *)            fzf --preview "$show_file_or_dir_preview" "$@" ;;
  esac
}

# ----- Bat (better cat) -----

export BAT_THEME=tokyonight_night

# ---- Eza (better ls) -----

alias ls="eza --icons=always"

# ---- TheFuck -----

# thefuck alias
eval $(thefuck --alias)
eval $(thefuck --alias fk)

# ---- Zoxide (better cd) ----
eval "$(zoxide init zsh)"

alias cd="z"

# OCaml
test -r '/home/nicolas/.opam/opam-init/init.sh' && . '/home/nicolas/.opam/opam-init/init.sh' > /dev/null 2> /dev/null || true

# Haskell
[ -f "/home/nicolas/.ghcup/env" ] && . "/home/nicolas/.ghcup/env" # ghcup-env

# >>> juliaup initialize >>>

# !! Contents within this block are managed by juliaup !!

case ":$PATH:" in
    *:/home/nicolas/.juliaup/bin:*)
        ;;

    *)
        export PATH=/home/nicolas/.juliaup/bin${PATH:+:${PATH}}
        ;;
esac

# <<< juliaup initialize <<<

# Add RVM to PATH for scripting. Make sure this is the last PATH variable change.

# --- perl ---
# perl
if (command -v perl && command -v cpanm) >/dev/null 2>&1; then
  test -d "$HOME/perl5/lib/perl5" && eval $(perl -I "$HOME/perl5/lib/perl5" -Mlocal::lib)
fi

# colored GCC warnings and errors
export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# Support for 16M colours
export COLORTERM=truecolor

# Generated for envman. Do not edit.
[ -s "$HOME/.config/envman/load.sh" ] && source "$HOME/.config/envman/load.sh"

# Add RVM to PATH for scripting. Make sure this is the last PATH variable change.
export PATH="$PATH:$HOME/.rvm/bin"
[[ -d "$HOME/.rvm/bin" && $PATH != *$HOME/.rvm/bin* ]] && PATH="$HOME/.rvm/bin${PATH:+:${PATH}}"

eval "$(uv generate-shell-completion zsh)"
eval "$(uvx --generate-shell-completion zsh)"

alias wezterm='cd dev/wezterm && cargo run --release --bin wezterm -- start'

setopt interactivecomments
alias emacs-tag='~/.emacs.d/scripts/tag-current.sh'
gdel() { git ls-files -d -z | git update-index --remove -z --stdin; }

. "$HOME/.atuin/bin/env"

eval "$(atuin init zsh)"
