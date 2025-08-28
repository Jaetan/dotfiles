return {
	"stevearc/aerial.nvim",
	event = { "BufReadPost", "BufNewFile" },
	opts = {
		backends = { "lsp", "treesitter", "markdown" },
		layout = { default_direction = "prefer_left", min_width = 28 },
		attach_mode = "global",
		show_guides = true,
	},
	config = function(_, opts)
		require("aerial").setup(opts)
		vim.keymap.set("n", "<leader>o", "<cmd>AerialToggle! left<CR>", { desc = "Symbols (Aerial)" })
	end,
}
