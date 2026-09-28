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

# ssh-agent: a systemd --user service (ssh-agent.service) runs one agent at a
# fixed socket for the whole WSL session, so SSH_AUTH_SOCK is a stable constant
# instead of a rotating, per-shell value. Set it globally (-gx, NOT -U) for
# EVERY shell -- including non-interactive ones (scripts, `fish -c`, editors'
# integrated terminals) -- so they share the live agent. Load the key once per
# session with `ssh-add ~/.ssh/id_ed25519` (or set `AddKeysToAgent yes` in
# ~/.ssh/config to load it on first use).
#   systemctl --user enable --now ssh-agent
set -gx SSH_AUTH_SOCK /run/user/(id -u)/ssh-agent.socket

# Load the key once at the first interactive shell of the session, restoring
# keychain's old "prompt in the terminal at login" behaviour. ssh-add prompts
# on the TTY (not the off-screen WSLg GUI) when a terminal is present, and only
# runs when the agent has no identity yet, so later shells don't re-prompt.
if status is-interactive; and test -f ~/.ssh/id_ed25519
    ssh-add -l >/dev/null 2>&1; or ssh-add ~/.ssh/id_ed25519
end

# Unlock the GNOME keyring in THIS terminal, not the off-screen WSLg GUI
# prompter. Keeps the keyring encrypted (UnlockWithMasterPassword over D-Bus);
# prompts (hidden) only on the first shell after a boot, while it's still
# locked. Must stay un-piped so the passphrase prompt keeps the terminal.
if status is-interactive
    if type -q wsl-keyring-unlock
        wsl-keyring-unlock
    end
end

# Editor & pager / colors
set -gx EDITOR /home/nicolas/nvim
set -gx LESS "-R --mouse -F -X -M"
set -gx GREP_COLORS "ms=01;36"
set -gx BAT_THEME "Catppuccin Mocha"
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

alias shake='cabal run shake --'

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

# fzf + preview with bat (Catppuccin Mocha)
set -gx FZF_DEFAULT_COMMAND 'fd --type f --hidden --follow --exclude .git 2>/dev/null || find . -type f'
set -gx FZF_DEFAULT_OPTS '--height=80% --border --preview-window=right,60%,border \
 --color=bg+:#313244,bg:#1E1E2E,spinner:#F5E0DC,hl:#F38BA8 \
 --color=fg:#CDD6F4,header:#F38BA8,info:#CBA6F7,pointer:#F5E0DC \
 --color=marker:#B4BEFE,fg+:#CDD6F4,prompt:#CBA6F7,hl+:#F38BA8 \
 --color=selected-bg:#45475A,border:#6C7086,label:#CDD6F4'
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
    set_color 6c7086
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

# gmod: stage only modified files; interactive picker if fzf is available
function gmod
    set -l files (git -c core.quotepath=off ls-files -m -z | string split0)
    if test (count $files) -eq 0
        echo "No modified files."
        return 0
    end
    if type -q fzf
        set -l preview 'if [ -d {} ]; then eza --tree --color=always {} | head -200; else bat -n --color=always --line-range :500 {}; fi'
        set -l pick (printf '%s\n' $files | fzf --multi --preview "$preview")
        if test -n "$pick"
            git add -- $pick
        else
            echo "No selection."
        end
    else
        git add -- $files
    end
end

# gnew: stage only new/untracked files; interactive picker if fzf is available
function gnew
    set -l files (git -c core.quotepath=off ls-files --others --exclude-standard -z | string split0)
    if test (count $files) -eq 0
        echo "No new untracked files."
        return 0
    end
    if type -q fzf
        set -l preview 'if [ -d {} ]; then eza --tree --color=always {} | head -200; else bat -n --color=always --line-range :500 {}; fi'
        set -l pick (printf '%s\n' $files | fzf --multi --preview "$preview")
        if test -n "$pick"
            git add -- $pick
        else
            echo "No selection."
        end
    else
        git add -- $files
    end
end

# gunstage: unstage files; interactive picker if fzf is available; --all to unstage everything
function gunstage
    argparse 'a/all' -- $argv
    if set -q _flag_all
        git restore --staged :/
        return
    end
    set -l files (git -c core.quotepath=off diff --name-only --cached -z | string split0)
    if test (count $files) -eq 0
        echo "Nothing is staged."
        return 0
    end
    if type -q fzf
        set -l preview 'if [ -d {} ]; then eza --tree --color=always {} | head -200; else git --no-pager diff --staged --color=always -- {} | delta || git --no-pager diff --staged --color=always -- {}; fi'
        set -l pick (printf '%s\n' $files | fzf --multi --preview "$preview")
        if test -n "$pick"
            git restore --staged -- $pick
        else
            echo "No selection."
        end
    else
        git restore --staged -- $files
    end
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

# Claude Code: more terminal colors
set -gx COLORTERM truecolor

# Claude Code runs in a session scope: every process it starts is held to CPUs 0-19 and the session's memory
# is capped, so a runaway is killed inside the session instead of the WSL VM (see session-limits).
function claude --wraps claude --description 'Claude Code held to CPUs 0-19 with its memory capped'
    session-limits (command -s claude) $argv
end

# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# PATH hygiene, last thing so it sees every earlier addition.
#
# Two ways a duplicate gets in. Some tools prepend unconditionally rather
# than checking first (opam's init.fish is one), so starting fish from a
# shell that already ran them lists the directory twice. And a tool that
# injects its own directories into the environment can leave entries for
# directories that no longer exist, which every command lookup then stats
# for nothing.
#
# Keep the first occurrence of each directory, drop the rest, and drop
# what is not there. Symlinks are resolved for the comparison only, so
# /bin and /usr/bin are recognised as one directory. Of two spellings
# for the same directory the real one is kept, in the position the first
# one held: a spelling is never invented, only chosen from the entries
# already present, so a version manager's own path is passed through
# untouched rather than pinned to whatever it points at today.
set -l seen
set -l kept
for p in $PATH
    test -d $p; or continue
    set -l real (realpath $p 2>/dev/null; or echo $p)
    if set -l at (contains -i -- $real $seen)
        # Already have this directory. Upgrade the spelling if the one
        # kept is a symlink and this one is the directory itself.
        if test -L $kept[$at]; and not test -L $p
            set kept[$at] $p
        end
        continue
    end
    set -a seen $real
    set -a kept $p
end
set -gx PATH $kept
