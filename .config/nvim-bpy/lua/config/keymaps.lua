local map = function(mode, lhs, rhs, desc)
	vim.keymap.set(mode, lhs, rhs, { desc = desc, silent = true })
end

-- diagnostics float & format
map("n", "<leader>e", function()
	vim.diagnostic.open_float(nil, { scope = "line", border = "rounded", source = "always", focus = false })
end, "Line diagnostics (float)")

map("n", "<leader>f", function()
	require("conform").format({ async = false, lsp_fallback = true, timeout_ms = 3000 })
end, "Format buffer (Conform → LSP fallback)")

-- toggle format-on-save (used by Conform setup)
vim.g._format_on_save_disabled = false
map("n", "<leader>tf", function()
	vim.g._format_on_save_disabled = not vim.g._format_on_save_disabled
	vim.notify("Format on save: " .. (vim.g._format_on_save_disabled and "OFF" or "ON"))
end, "Toggle format-on-save")

-- windows / tabs (your snippet-88)
map("n", "<leader>sv", ":vsplit<CR>", "Vertical split")
map("n", "<leader>sh", ":split<CR>", "Horizontal split")
map("n", "<leader>ww", "<C-w>w", "Switch window")
map("n", "<leader>wh", "<C-w>h", "Focus left")
map("n", "<leader>wj", "<C-w>j", "Focus down")
map("n", "<leader>wk", "<C-w>k", "Focus up")
map("n", "<leader>wl", "<C-w>l", "Focus right")
map("n", "<leader>wH", "<C-w>H", "Move window far left")
map("n", "<leader>wJ", "<C-w>J", "Move window to bottom")
map("n", "<leader>wK", "<C-w>K", "Move window to top")
map("n", "<leader>wL", "<C-w>L", "Move window far right")
map("n", "<leader>w=", "<C-w>=", "Equalize window sizes")
map("n", "<leader>wq", "<C-w>q", "Close window")
map("n", "<leader>wo", "<C-w>o", "Only (close others)")

-- resize with arrows
map("n", "<C-Up>", ":resize +2<CR>", "Increase height")
map("n", "<C-Down>", ":resize -2<CR>", "Decrease height")
map("n", "<C-Left>", ":vertical resize -4<CR>", "Narrow")
map("n", "<C-Right>", ":vertical resize +4<CR>", "Widen")

-- tabs
map("n", "<leader>tn", ":tabnew<CR>", "New tab")
map("n", "<leader>tq", ":tabclose<CR>", "Close tab")
map("n", "<leader>to", ":tabonly<CR>", "Close other tabs")
map("n", "<leader>tl", ":tabnext<CR>", "Next tab")
map("n", "<leader>th", ":tabprevious<CR>", "Prev tab")
for i = 1, 9 do
	map("n", "<leader>t" .. i, i .. "gt", "Go to tab " .. i)
end

-- buffer move helpers (from snippet-89)
local function move_buf_to(dir)
	local cur_win = vim.api.nvim_get_current_win()
	local cur_buf = vim.api.nvim_get_current_buf()
	vim.cmd("wincmd " .. dir)
	local target_win = vim.api.nvim_get_current_win()
	if target_win == cur_win then
		if dir == "h" or dir == "l" then
			vim.cmd("vsplit")
		else
			vim.cmd("split")
		end
		vim.cmd("wincmd " .. dir)
		target_win = vim.api.nvim_get_current_win()
	end
	vim.api.nvim_win_set_buf(target_win, cur_buf)
	if cur_win ~= target_win then
		if vim.fn.bufexists("#") == 1 then
			vim.api.nvim_set_current_win(cur_win)
			vim.cmd("buffer #")
		else
			vim.api.nvim_set_current_win(cur_win)
			vim.cmd("enew")
		end
	end
	vim.api.nvim_set_current_win(target_win)
end
map("n", "<leader>wmh", function()
	move_buf_to("h")
end, "Move buffer to left split")
map("n", "<leader>wmj", function()
	move_buf_to("j")
end, "Move buffer to down split")
map("n", "<leader>wmk", function()
	move_buf_to("k")
end, "Move buffer to up split")
map("n", "<leader>wml", function()
	move_buf_to("l")
end, "Move buffer to right split")
