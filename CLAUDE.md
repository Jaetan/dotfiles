# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A GNU Stow-managed dotfiles repo. The repo root mirrors `$HOME`, so every path here maps 1:1 to its location under `~`. To deploy (or re-deploy) symlinks:

```sh
cd ~/dotfiles && stow .
```

Stow will error if a real file (not a symlink) already exists at the target path.

## Repository Layout

| Path | Purpose |
|---|---|
| `.config/fish/config.fish` | Primary shell config (fish) |
| `.bashrc` | Bash fallback config (mirrors fish aliases/env) |
| `.config/nvim/` | Neovim config (Lua, lazy.nvim) — has its own `CLAUDE.md` |
| `.wezterm.lua` | WezTerm terminal config |
| `.config/starship.toml` | Starship prompt theme |
| `.config/ohmyposh/` | Oh My Posh prompt themes (alternate) |
| `.config/git/ignore` | Global gitignore |
| `.config/fzf/`, `.config/atuin/`, `.config/lazygit/` | Tool configs |
| `.config/systemd/user/` | User systemd services |

## Key Conventions

- **Shell**: Fish is the primary shell. Bash config exists as a fallback with mirrored aliases/env.
- **Theme**: Catppuccin Mocha everywhere — Neovim, bat, fzf, starship, WezTerm, lazygit.
- **Font**: VictorMono Nerd Font (set in WezTerm).
- **Tool replacements**: `eza` for `ls`, `bat` for `cat`, `fd` for `find`, `rg` for `grep`, `zoxide` for `cd`. Aliases handle Debian naming quirks (`batcat`→`bat`, `fdfind`→`fd`).
- **Environment**: WSL2 on Linux. Clipboard helpers (`pbcopy`/`pbpaste`) bridge to Windows.
- **Stow ignores**: `.gitignore` excludes runtime/generated files (fish_variables, gh hosts, gitk).

## When Editing

- Keep fish and bash configs in sync when changing aliases, env vars, or PATH entries.
- Respect the Catppuccin Mocha color palette when adding fzf/prompt/UI colors.
- The nvim config has its own CLAUDE.md at `.config/nvim/CLAUDE.md` — refer to it for Neovim-specific guidance.
- Files in `.config/git/ignore` affect all repos on the system — be conservative adding patterns there.
