# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Custom Neovim configuration using **lazy.nvim** as plugin manager. All configuration is in Lua.

## Architecture

- **Entry point**: `init.lua` loads five core modules in order: `options → keymaps → autocmds → diagnostics → lazy`
- **`lua/config/`**: Core editor configuration (options, keymaps, autocmds, diagnostics, LSP helpers, lazy.nvim bootstrap)
- **`lua/plugins/`**: ~66 plugin spec files, auto-discovered by lazy.nvim via `{ import = "plugins" }`
- **`lua/util/`**: Utilities (e.g., Python venv detection)
- **`lazy-lock.json`**: Version pinning for all plugins

## Key Design Patterns

- **Leader key**: `\` (backslash)
- **Plugins load eagerly by default** (`lazy = false` in lazy.nvim config)
- **LSP setup**: `config/lsp_setup.lua` exports `setup_once()` to prevent duplicate server configs; cmp capabilities are automatically added
- **Custom statuscolumn**: `_G._fold_icon()` in `options.lua` renders fold markers as icons (no fold column, no numeric levels)
- **Project root detection**: Telescope, Grep, TODOs, and Spectre all use git root, falling back to cwd
- **Python venv**: `util/python.lua` resolves pythonPath dynamically from `$VIRTUAL_ENV` → `.venv/` → system python
- **Format on save**: Managed by conform.nvim, toggleable with `<leader>tf`

## LSP Servers

lua_ls, basedpyright, ruff, html, cssls, emmet_language_server, clangd (via `./tools/clangd-from-bazel.sh`)

## Formatters (conform.nvim)

isort, black, prettier, stylua, clang_format, ocamlformat

## Keymap Groups

`<leader>w` windows, `<leader>t` tabs/todo, `<leader>f` files/search, `<leader>h` git hunks, `<leader>x` trouble/diagnostics, `<leader>d` dune/OCaml, `<leader>s` search/replace, `<leader>m` harpoon, `<leader>j` leap, `<leader>z` folds, `g` LSP goto

## Custom Commands

- `:Grep {pattern}` — project-rooted ripgrep → quickfix
- `:OCamlAlternate` — toggle between .ml and .mli
- `:SudaWrite` — write with sudo

## Useful Context

- Indentation: 2 spaces (expandtab), except Makefiles
- Autocmd retabs files on write (except Makefiles)
- Python 3 host prog: `~/.venvs/nvim/bin/python`
- Theme: tokyonight
- Session management: persistence.nvim (keymaps available, dashboard shows MRU)
