# PATH="${PATH:+${PATH}:}~/opt/bin"   # appending
# PATH="~/opt/bin${PATH:+:${PATH}}"   # prepending

[[ -d "$HOME/bin" && $PATH != *$HOME/bin* ]] && PATH="$HOME/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.cargo/bin" && $PATH != *$HOME/.cargo/bin* ]] && PATH="$HOME/.cargo/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.npm-global/bin" && $PATH != *$HOME/.npm-global/bin* ]] && PATH="$HOME/.npm-global/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.local/bin" && $PATH != *$HOME/.local/bin* ]] && PATH="$HOME/.local/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.rvm/bin" && $PATH != *$HOME/.rvm/bin* ]] && PATH="$HOME/.rvm/bin${PATH:+:${PATH}}"
[[ -d "$HOME/.local/share/coursier/bin" && $PATH != *$HOME/.local/share/coursier/bin* ]] && PATH="$HOME/.local/share/coursier/bin${PATH:+:${PATH}}"
[[ -d "$HOME/go/bin" && $PATH != *$HOME/fo/bin* ]] && PATH="$HOME/go/bin${PATH:+:${PATH}}"
export PATH

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
alias eza="eza --color=always --long --git --icons=always"
eval $(dircolors -b)
alias ls="ls --color=auto"

# --- Bat (better cat) ---
alias bat=batcat
export BAT_THEME=tokyonight_night

# --- thefuck (command-line correction) ---
eval "$(thefuck --alias dwim)"

# Shell integrations
eval "$(fzf --zsh)"
eval "$(zoxide init --cmd cd zsh)"

test -r '/home/nicolas/.opam/opam-init/init.sh' && . '/home/nicolas/.opam/opam-init/init.sh' > /dev/null 2> /dev/null || true
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
[[ -s "$HOME/.rvm/scripts/rvm" ]] && source "$HOME/.rvm/scripts/rvm" # Load RVM into a shell session *as a function*

# --- perl ---
# perl
if (command -v perl && command -v cpanm) >/dev/null 2>&1; then
  test -d "$HOME/perl5/lib/perl5" && eval $(perl -I "$HOME/perl5/lib/perl5" -Mlocal::lib)
fi

# --- pyenv ---
# source "$HOME/.venv/py/bin/activate"
# export PYTHONPATH='/home/nicolas/.venv/py/lib/python3.12/site-packages'
# Load pyenv automatically by appending
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
function nvimvenv {
  if [[ -e "$VIRTUAL_ENV" && -f "$VIRTUAL_ENV/bin/activate" ]]; then
    source "$VIRTUAL_ENV/bin/activate"
    "$HOME"/nvim.appimage "$@"
    deactivate
  else
    "$HOME"/nvim.appimage "$@"
  fi
}

alias nvim=nvimvenv

# colored GCC warnings and errors
export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# Support for 16M colours
export COLORTERM=truecolor

# Generated for envman. Do not edit.
[ -s "$HOME/.config/envman/load.sh" ] && source "$HOME/.config/envman/load.sh"
