-- Native last-place restoration (replaces archived nvim-lastplace)
local ignore_bt = { quickfix = true, nofile = true, help = true }
local ignore_ft = {
	gitcommit = true, gitrebase = true, svn = true, hgcommit = true,
	alpha = true, ["neo-tree"] = true, TelescopePrompt = true,
}

vim.api.nvim_create_autocmd("BufReadPost", {
	group = vim.api.nvim_create_augroup("LastPlace", { clear = true }),
	callback = function(ev)
		if ignore_bt[vim.bo[ev.buf].buftype] or ignore_ft[vim.bo[ev.buf].filetype] then
			return
		end
		local mark = vim.api.nvim_buf_get_mark(ev.buf, '"')
		local line = mark[1]
		if line > 0 and line <= vim.api.nvim_buf_line_count(ev.buf) then
			pcall(vim.api.nvim_win_set_cursor, 0, mark)
			if vim.fn.foldclosed(line) ~= -1 then
				vim.cmd("normal! zv")
			end
		end
	end,
})

return {}
