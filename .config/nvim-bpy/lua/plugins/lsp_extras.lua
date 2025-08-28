-- lua/plugins/lsp_extras.lua
return {
	-- LSP progress notifications
	{
		"j-hui/fidget.nvim",
		event = "LspAttach",
		opts = {
			notification = { window = { border = "rounded" } },
			progress = { display = { done_icon = "✔" } },
		},
	},

	-- Powerful folds via LSP (main) with indent fallback
	{
		"kevinhwang91/nvim-ufo",
		event = "BufReadPost",
		dependencies = { "kevinhwang91/promise-async" },
		init = function()
			vim.o.foldcolumn = "1"
			vim.o.foldlevel = 99
			vim.o.foldlevelstart = 99
			vim.o.foldenable = true
		end,
		opts = {
			-- must return exactly { main, fallback }
			provider_selector = function(_, _, _)
				return { "lsp", "indent" }
			end,
		},
		config = function(_, opts)
			local ok, ufo = pcall(require, "ufo")
			if not ok then
				return
			end
			ufo.setup(opts)
			vim.keymap.set("n", "zR", ufo.openAllFolds, { desc = "Open all folds" })
			vim.keymap.set("n", "zM", ufo.closeAllFolds, { desc = "Close all folds" })
		end,
	},
}
