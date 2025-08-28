vim.g.mapleader = "\\"

vim.opt.termguicolors = true
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.numberwidth = 4
vim.opt.signcolumn = "yes" -- keep diagnostic icons aligned

-- tabs/spaces
vim.opt.expandtab = true
vim.opt.shiftwidth = 2
vim.opt.tabstop = 2
vim.opt.softtabstop = 2
vim.opt.smartindent = true

-- timings (for Emmet <C-y>, etc.)
vim.opt.timeout = true
vim.opt.ttimeout = true
vim.opt.timeoutlen = 500
vim.opt.ttimeoutlen = 50

-- completion behavior (also set in cmp, keeping here is fine)
vim.opt.completeopt = { "menu", "menuone", "noselect" }
