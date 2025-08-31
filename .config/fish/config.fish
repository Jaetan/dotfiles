# -------- Fish config (WSL, Tokyo Night Night) ------------------------

# PATH
fish_add_path $HOME/.local/bin $HOME/bin

# ssh-agent via keychain (interactive shells only)
if status is-interactive
    if type -q keychain
        if test -f ~/.ssh/id_ed25519
            keychain --eval --quiet --agents ssh --inherit any ~/.ssh/id_ed25519 | source
        else
            # start/reuse agent without loading a specific key if it doesn't exist
            keychain --eval --quiet --agents ssh --inherit any | source
        end
    end
end

# Editor & pager / colors
set -gx EDITOR nvim
set -gx LESS "-R --mouse -F -X -M"
set -gx GREP_COLORS "ms=01;36"
set -gx BAT_THEME "tokyonight_night"
set -gx MANPAGER "sh -c 'col -bx | bat -l man -p'"

# Fallback LS_COLORS (eza already colors nicely)
set -q LS_COLORS; or set -gx LS_COLORS "di=01;34:ln=01;36:so=33:pi=33:ex=01;32:bd=01;33:cd=01;33:or=01;31:mi=01;31"

# --- Tool fallbacks for Debian/Ubuntu naming quirks -------------------
if not type -q bat
    if type -q batcat
        alias bat="batcat"
    end
end
if not type -q fd
    if type -q fdfind
        alias fd="fdfind"
    end
end

# eza defaults
if type -q eza
    alias ls="eza --group-directories-first --icons=auto --git -F"
else
    alias ls="ls --color=auto -F"
end
alias ll="ls -lh"
alias la="ls -lha"
alias lt="ls --tree"

# cat via bat (uses BAT_THEME above)
alias cat="bat --paging=never"

# fd with sensible defaults (works whether it's fd or fdfind)
if type -q fd
    alias fd="fd --hidden --follow --exclude .git"
else if type -q fdfind
    alias fd="fdfind --hidden --follow --exclude .git"
end

alias rg="rg --hidden --smart-case"
alias gs="git status -sb"
alias gd="git diff"
alias gl="git log --oneline --graph --decorate"

# direnv
if type -q direnv
    direnv hook fish | source
end

# zoxide
if type -q zoxide
    zoxide init fish | source
    abbr -a z z
    abbr -a zz "z -"
end

# fzf + preview with bat (Tokyo Night Night)
set -gx FZF_DEFAULT_COMMAND 'fd --type f --hidden --follow --exclude .git 2>/dev/null || find . -type f'
set -gx FZF_DEFAULT_OPTS '--height=80% --border --preview-window=right,60%,border \
 --color=fg:#c0caf5,bg:#1a1b26,hl:#7dcfff,fg+:#c0caf5,bg+:#292e42,hl+:#bb9af7,info:#7aa2f7,prompt:#7dcfff,pointer:#f7768e,marker:#e0af68,spinner:#bb9af7,header:#565f89'
set -gx FZF_CTRL_T_OPTS "--preview 'bat --style=plain --color=always --line-range :200 {}'"

# atuin (history: searchable, deduped)
if type -q atuin
    atuin init fish --disable-up-arrow | source
end

# --- Starship transient prompt separator line -------------------------
# Draw a dim rule where the old prompt was, to separate outputs.
function starship_transient_prompt_func
    set -l cols $COLUMNS
    if test -z "$cols"
        set cols (tput cols ^/dev/null)
    end
    set_color 565f89
    echo (string repeat -n $cols '─')
    set_color normal
end

# Right-side bit on that transient line (time, matching your theme)
function starship_transient_rprompt_func
    starship module time
end

# starship prompt
if type -q starship
    starship init fish | source
    enable_transience
end

# safer coreutils
alias rm='rm -I --preserve-root'
alias mv='mv -i'
alias cp='cp -i'

# helper funcs
function mcd; mkdir -p -- $argv[1]; and cd -- $argv[1]; end
function mkcdtmp; set d (mktemp -d); cd $d; pwd; end
function mkvenv; python3 -m venv .venv; and source .venv/bin/activate.fish; and pip -q install -U pip wheel; end

# "please": rerun last command with sudo (fish uses $history with newest first)
function please
    if test (count $history) -gt 0
        eval sudo $history[1]
    else
        echo "no history yet"
    end
end

# WSL clipboard helpers
if test -n "$WSL_DISTRO_NAME"
    function pbcopy; clip.exe < /dev/stdin; end
    function pbpaste; powershell.exe -NoProfile -Command Get-Clipboard | tr -d '\r'; end
end

# mise (version manager) — correct fish activation
if type -q mise
    mise activate fish | source
end

# ----------------------------------------------------------------------
