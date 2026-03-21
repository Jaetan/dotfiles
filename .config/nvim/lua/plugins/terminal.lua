return {
	"akinsho/toggleterm.nvim",
	version = "*",
	keys = {
		{
			"<leader>`",
			function()
				require("toggleterm").toggle(1)
			end,
			desc = "Terminal (toggle float)",
		},
		{
			"<leader>\\",
			function()
				require("toggleterm").toggle(1, 12, vim.uv.cwd(), "horizontal")
			end,
			desc = "Terminal (horizontal)",
		},
		{
			"<leader>|",
			function()
				require("toggleterm").toggle(2, 0, vim.uv.cwd(), "vertical")
			end,
			desc = "Terminal (vertical)",
		},
	},
	opts = { direction = "float", float_opts = { border = "rounded" } },
}
