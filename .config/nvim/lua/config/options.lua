vim.g.mapleader = "\\"

vim.opt.termguicolors = true
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.numberwidth = 4
vim.opt.signcolumn = "yes" -- keep diagnostic icons aligned

-- Allow the cursor to move past EOL (keeps the same visual column on ragged lines)
vim.opt.virtualedit = "all"

-- tabs/spaces
vim.opt.expandtab = true
vim.opt.shiftwidth = 2
vim.opt.tabstop = 2
vim.opt.softtabstop = 2
vim.opt.smartindent = true

-- timings (for Emmet <C-y>, etc.)
vim.opt.timeout = true
vim.opt.ttimeout = true
vim.opt.timeoutlen = 700
vim.opt.ttimeoutlen = 50

-- completion behavior (also set in cmp, keeping here is fine)
vim.opt.completeopt = { "menu", "menuone", "noselect" }

vim.g.python3_host_prog = vim.fn.expand("~/.local/share/nvim/py3/bin/python")

-- Custom fold icon for the margin (no numeric levels)
_G._fold_icon = function()
	local lnum = vim.v.lnum
	local lvl = vim.fn.foldlevel(lnum)
	if lvl <= 0 then
		return " " -- no fold here
	end
	if vim.fn.foldclosed(lnum) == -1 then
		-- line is in an open fold; only mark the **header** so we don't spam icons
		local prev = math.max(lnum - 1, 1)
		if vim.fn.foldlevel(prev) < lvl then
			return "" -- open fold head
		else
			return " " -- inside opened fold, show nothing
		end
	else
		return "" -- closed fold head
	end
end

-- Build a clean statuscolumn:
vim.o.statuscolumn = table.concat({
	"%s", -- signs
	"%{v:lua._fold_icon()} ", -- our fold icon (or space)
	"%=%{&nu?(&rnu?v:relnum:v:lnum):''} ", -- numbers (respect number/relativenumber)
})

-- Keep fold UI consistent (one narrow column reserved; icons come from statuscolumn)
vim.o.foldcolumn = "1"
vim.o.foldlevel = 99
vim.o.foldlevelstart = 99
vim.o.foldenable = true

-- Make sure default fold fillers never render digits
vim.opt.fillchars:append({
	foldopen = "",
	foldclose = "",
	fold = " ",
	foldsep = " ",
	eob = " ",
})

-- Use ripgrep for :grep and populate quickfix properly
if vim.fn.executable("rg") == 1 then
	vim.opt.grepprg = "rg --vimgrep --smart-case --hidden --glob !.git"
	vim.opt.grepformat = "%f:%l:%c:%m"
end
