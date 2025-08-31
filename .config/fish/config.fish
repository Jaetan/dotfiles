# -------- Fish config (WSL, Tokyo Night Night) ------------------------

# PATH
fish_add_path $HOME/.local/bin $HOME/bin

# Atuin sh-installer PATH (if present)
if test -d $HOME/.atuin/bin
    fish_add_path $HOME/.atuin/bin
end

# zsh PATH extras -> fish
for p in $HOME/.cargo/bin $HOME/.npm-global/bin $HOME/.rvm/bin $HOME/.local/share/coursier/bin $HOME/go/bin $HOME/.juliaup/bin $HOME/.ghcup/bin
    if test -d $p
        fish_add_path $p
    end
end

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

# Use lesspipe if available (better 'less' for many formats)
if test -x /usr/bin/lesspipe
    set -gx LESSOPEN "|/usr/bin/lesspipe %s"
    set -gx LESSCLOSE "/usr/bin/lesspipe %s %s"
end

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

# quick up-directory abbreviations (bash/zsh '..'/'...' aliases)
abbr -a .. 'cd ..'
abbr -a ... 'cd ../..'

# optional: make 'cd' behave like 'z'
if type -q zoxide
    abbr -a cd z
end

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

# fzf: fish keybindings/completions (system examples if present)
if test -f /usr/share/doc/fzf/examples/key-bindings.fish
    source /usr/share/doc/fzf/examples/key-bindings.fish
end
if test -f /usr/share/doc/fzf/examples/completion.fish
    source /usr/share/doc/fzf/examples/completion.fish
end

# fzf + preview with bat (Tokyo Night Night)
set -gx FZF_DEFAULT_COMMAND 'fd --type f --hidden --follow --exclude .git 2>/dev/null || find . -type f'
set -gx FZF_DEFAULT_OPTS '--height=80% --border --preview-window=right,60%,border \
 --color=fg:#c0caf5,bg:#1a1b26,hl:#7dcfff,fg+:#c0caf5,bg+:#292e42,hl+:#bb9af7,info:#7aa2f7,prompt:#7dcfff,pointer:#f7768e,marker:#e0af68,spinner:#bb9af7,header:#565f89'
set -gx FZF_CTRL_T_OPTS "--preview 'bat --style=plain --color=always --line-range :200 {}'"
# zsh's ALT-C customizations -> fish
set -gx FZF_ALT_C_COMMAND 'fd --type d --hidden --follow --exclude .git 2>/dev/null || find . -type d'
set -gx FZF_ALT_C_OPTS "--preview 'eza --tree --color=always {} | head -200'"

# atuin (history: searchable, deduped)
if type -q atuin
    atuin init fish --disable-up-arrow | source
end

# thefuck (command-line correction) — mirror zsh aliases
if type -q thefuck
    thefuck --alias | source
    thefuck --alias fk | source
    thefuck --alias dwim | source
end

# uv completions (mirror the zsh init lines)
if type -q uv
    uv generate-shell-completion fish | source
end
if type -q uvx
    uvx --generate-shell-completion fish | source
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

# Right-side bit on that transient line (time)
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
function extract
    switch $argv[1]
        case '*.tar.bz2' '*.tbz2'
            tar xjf $argv[1]
        case '*.tar.gz' '*.tgz'
            tar xzf $argv[1]
        case '*.tar'
            tar xf $argv[1]
        case '*.bz2'
            bunzip2 $argv[1]
        case '*.gz'
            gunzip $argv[1]
        case '*.zip'
            unzip $argv[1]
        case '*.rar'
            unrar x $argv[1]
        case '*.7z'
            7z x $argv[1]
        case '*'
            echo "don't know how to extract '$argv[1]'"
    end
end

# zsh's gdel helper -> fish
function gdel
    git ls-files -d -z | git update-index --remove -z --stdin
end

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

# WSL niceties (like bash/zsh WSLSYS probe)
set -gx WSLSYS (test -r /proc/sys/fs/binfmt_misc/WSLInterop; and echo 1; or echo 0)

# OCaml (opam) — fish init
if test -r $HOME/.opam/opam-init/init.fish
    source $HOME/.opam/opam-init/init.fish ^/dev/null ^&1
end

# Haskell (ghcup) — add bin path if present
if test -d $HOME/.cabal/bin
    fish_add_path $HOME/.cabal/bin
end

# envman (if it provides fish integration)
if test -f $HOME/.config/envman/load.fish
    source $HOME/.config/envman/load.fish
end

# keybindings similar to zsh: Ctrl-P/N search by prefix
if status is-interactive
    bind \cp history-search-backward
    bind \cn history-search-forward
end

# aliases from zsh
alias emacs-tag='~/.emacs.d/scripts/tag-current.sh'
alias wezterm='cd dev/wezterm; and cargo run --release --bin wezterm -- start'

# mise (version manager) — correct fish activation
if type -q mise
    mise activate fish | source
end

# ----------------------------------------------------------------------
