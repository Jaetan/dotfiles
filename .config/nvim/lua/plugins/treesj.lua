return {
	"Wansmer/treesj",
	dependencies = { "nvim-treesitter/nvim-treesitter" },
	keys = {
		{
			"gS",
			function()
				require("treesj").split()
			end,
			desc = "Treesj: split node",
		},
		{
			"gJ",
			function()
				require("treesj").join()
			end,
			desc = "Treesj: join node",
		},
		{
			"<leader>J",
			function()
				require("treesj").toggle()
			end,
			desc = "Treesj: split/join toggle",
		},
	},
	opts = {
		use_default_keymaps = false,
		check_syntax_error = true,
		max_join_length = 120,
		cursor_behavior = "hold",
		notify = true,
	},
	config = function(_, opts)
		require("treesj").setup(opts)
		pcall(function()
			require("which-key").add({
				{ "<leader>J", desc = "Treesj: split/join toggle" },
			})
		end)
	end,
}
