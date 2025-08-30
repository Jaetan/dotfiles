return {
	"stevearc/dressing.nvim",
	event = "VeryLazy",
	opts = {
		input = {
			border = "rounded",
			win_options = { winblend = 0 },
		},
		select = {
			backend = { "telescope", "builtin" }, -- prefer telescope when available
			builtin = {
				border = "rounded",
				win_options = { winblend = 0 },
			},
		},
	},
}
