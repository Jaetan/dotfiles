return {
	"isovector/cornelis",
	ft = "agda",
	build = "stack install",
	version = "v2.8.*",
	dependencies = {
		"neovimhaskell/nvim-hs.vim",
		"kana/vim-textobj-user",
	},
	config = function()
		-- Keymaps (buffer-local, set via autocmd so they only apply to .agda files)
		vim.api.nvim_create_autocmd({ "BufRead", "BufNewFile" }, {
			pattern = "*.agda",
			callback = function()
				local opts = { buffer = true, silent = true }
				vim.keymap.set("n", "<leader>l", "<cmd>CornelisLoad<CR>", opts)
				vim.keymap.set("n", "<leader>r", "<cmd>CornelisRefine<CR>", opts)
				vim.keymap.set("n", "<leader>d", "<cmd>CornelisMakeCase<CR>", opts)
				vim.keymap.set("n", "<leader>,", "<cmd>CornelisTypeContext<CR>", opts)
				vim.keymap.set("n", "<leader>.", "<cmd>CornelisTypeContextInfer<CR>", opts)
				vim.keymap.set("n", "<leader>n", "<cmd>CornelisSolve<CR>", opts)
				vim.keymap.set("n", "<leader>a", "<cmd>CornelisAuto<CR>", opts)
				vim.keymap.set("n", "gd", "<cmd>CornelisGoToDefinition<CR>", opts)
				vim.keymap.set("n", "[/", "<cmd>CornelisPrevGoal<CR>", opts)
				vim.keymap.set("n", "]/", "<cmd>CornelisNextGoal<CR>", opts)
			end,
		})
	end,
}
