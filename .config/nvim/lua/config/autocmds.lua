-- retab on write (but not for Makefiles which need real tabs)
vim.api.nvim_create_autocmd("BufWritePre", {
	pattern = "*",
	callback = function()
		if vim.bo.filetype ~= "make" and vim.bo.expandtab then
			local view = vim.fn.winsaveview()
			vim.cmd("silent! retab!")
			vim.fn.winrestview(view)
		end
	end,
})

-- toggle relativenumber when entering/leaving insert
vim.api.nvim_create_autocmd("InsertEnter", {
	callback = function()
		vim.wo.relativenumber = false
	end,
})
vim.api.nvim_create_autocmd("InsertLeave", {
	callback = function()
		vim.wo.relativenumber = true
	end,
})

-- gitsigns refresh after write (optional nicety)
vim.api.nvim_create_autocmd("BufWritePost", {
	callback = function()
		if package.loaded.gitsigns then
			pcall(require("gitsigns").refresh)
		end
	end,
})

-- Reapply IBL highlight colors on colorscheme change
vim.api.nvim_create_autocmd("ColorScheme", {
	callback = function()
		vim.schedule(function()
			pcall(vim.api.nvim_set_hl, 0, "IblIndent", { fg = "#3b4261", nocombine = true })
			pcall(vim.api.nvim_set_hl, 0, "IblScope", { fg = "#7aa2f7", nocombine = true })
		end)
	end,
})

-- -------------------------------------------------------------------
-- Force our statuscolumn everywhere and keep the fold column hidden.
-- -------------------------------------------------------------------

-- Custom, no-numbers statuscolumn (signs | fold icon | numbers)
-- relies on _G._fold_icon from options.lua
local sc = table.concat({
	"%s", -- signs (gitsigns/diagnostics)
	"%{v:lua._fold_icon()} ", -- our fold icon only on headers
	"%=%{&nu?(&rnu?v:relnum:v:lnum):''} ", -- (relative) line numbers
})

local function apply_statuscol(win)
	win = win or 0
	vim.schedule(function()
		pcall(function()
			vim.wo[win].foldcolumn = "0" -- never show dedicated fold column
			vim.wo[win].statuscolumn = sc -- always use our custom statuscolumn
		end)
	end)
end

vim.api.nvim_create_autocmd({ "VimEnter", "BufWinEnter", "WinNew", "TermOpen", "BufEnter", "TabEnter", "FileType" }, {
	group = vim.api.nvim_create_augroup("StatusColForce", { clear = true }),
	callback = function()
		-- Use current window (0); avoids relying on non-standard args.win
		apply_statuscol(0)
	end,
})

-- Auto-open quickfix after :grep / :vimgrep if there are results
vim.api.nvim_create_autocmd("QuickFixCmdPost", {
	pattern = { "grep", "vimgrep" },
	callback = function()
		if #vim.fn.getqflist() > 0 then
			vim.cmd("cwindow") -- smart height
		else
			vim.notify("No matches (quickfix empty)", vim.log.levels.INFO)
		end
	end,
})

-- -------------------------------------------------------------------
-- QoL autocmds (no last-cursor-position)
-- -------------------------------------------------------------------
local qol = vim.api.nvim_create_augroup("QolExtras", { clear = true })

-- Briefly highlight yanked text
vim.api.nvim_create_autocmd("TextYankPost", {
	group = qol,
	callback = function()
		pcall(vim.highlight.on_yank, { higroup = "IncSearch", timeout = 120, on_visual = true })
	end,
})

-- Equalize splits when terminal window size changes
vim.api.nvim_create_autocmd("VimResized", {
	group = qol,
	callback = function()
		vim.cmd("tabdo wincmd =")
	end,
})

-- Don’t auto-continue comments on new lines
vim.api.nvim_create_autocmd("FileType", {
	group = qol,
	pattern = "*",
	callback = function()
		vim.opt_local.formatoptions:remove({ "o", "r" })
	end,
})
