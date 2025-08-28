-- config/autocmds.lua — corrected

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

-- Ensure no plugin/custom statuscolumn draws fold levels as digits
vim.api.nvim_create_autocmd({ "VimEnter", "BufWinEnter", "WinNew" }, {
	callback = function(ev)
		-- window-local setting
		pcall(function()
			vim.wo[ev.win] = vim.wo[ev.win]
		end) -- noop guard for older APIs
		vim.wo.statuscolumn = "" -- default rendering
		vim.wo.foldcolumn = "1" -- or "0" to hide completely
	end,
})

-- Custom, no-numbers statuscolumn (signs | fold icon | numbers)
-- relies on _G._fold_icon from options.lua (snippet-162)
local sc = table.concat({
	"%s", -- signs (gitsigns/diagnostics)
	"%{v:lua._fold_icon()} ", -- our fold icon only on headers
	"%=%{&nu?(&rnu?v:relnum:v:lnum):''} ", -- (relative) line numbers
})

vim.api.nvim_create_autocmd({ "VimEnter", "BufWinEnter", "WinNew", "TermOpen" }, {
	group = vim.api.nvim_create_augroup("StatusColApply", { clear = true }),
	callback = function()
		vim.wo.statuscolumn = sc
	end,
})
