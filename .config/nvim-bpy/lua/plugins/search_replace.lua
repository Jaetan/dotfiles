return {
	"nvim-pack/nvim-spectre",
	dependencies = { "nvim-lua/plenary.nvim" },
	cmd = "Spectre",
	keys = {
		{
			"<leader>S",
			function()
				require("spectre").open()
			end,
			desc = "Spectre: Project replace",
		},
		{
			"<leader>Sw",
			function()
				require("spectre").open_visual({ select_word = true })
			end,
			desc = "Spectre: Replace word",
		},
		{
			"<leader>Sf",
			function()
				require("spectre").open_file_search()
			end,
			desc = "Spectre: Replace in file",
		},
	},
	opts = { open_cmd = "vnew" },
	config = function(_, opts)
		require("spectre").setup(opts)
	end,
}
