return {
	"folke/tokyonight.nvim",
	priority = 1000,
	opts = {
		style = "night",
		styles = {
			comments = { italic = true },
			keywords = { italic = false },
			functions = { bold = false },
			variables = {},
		},
		dim_inactive = false,
	},
	config = function(_, opts)
		require("tokyonight").setup(opts)
		vim.cmd.colorscheme("tokyonight-night")
	end,
}
