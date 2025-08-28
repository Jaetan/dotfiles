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
vim.api.nvim_create_autocmd("InsertEnter", function()
	vim.wo.relativenumber = false
end)
vim.api.nvim_create_autocmd("InsertLeave", function()
	vim.wo.relativenumber = true
end)

-- gitsigns refresh after write (optional nicety)
vim.api.nvim_create_autocmd("BufWritePost", function()
	if package.loaded.gitsigns then
		pcall(require("gitsigns").refresh)
	end
end)
