return {
	"echasnovski/mini.move",
	version = "*",
	event = "VeryLazy",
	opts = {
		-- Use Alt+hjkl to move lines/selections
		mappings = {
			left = "<A-h>",
			right = "<A-l>",
			down = "<A-j>",
			up = "<A-k>",

			line_left = "<A-h>",
			line_right = "<A-l>",
			line_down = "<A-j>",
			line_up = "<A-k>",
		},
		options = {
			reindent_linewise = true, -- keep indentation sensible when moving lines
		},
	},
	config = function(_, opts)
		require("mini.move").setup(opts)

		-- which-key hints (optional)
		pcall(function()
			require("which-key").add({
				{ "<A-j>", desc = "Move line/selection down" },
				{ "<A-k>", desc = "Move line/selection up" },
				{ "<A-h>", desc = "Move left" },
				{ "<A-l>", desc = "Move right" },
			})
		end)
	end,
}
