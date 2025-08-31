return {
	"nvim-treesitter/nvim-treesitter-textobjects",
	event = { "BufReadPre", "BufNewFile" },
	dependencies = { "nvim-treesitter/nvim-treesitter" },

	opts = {
		select = {
			enable = true,
			lookahead = true, -- jump forward to next textobject automatically
			keymaps = {
				-- functions
				["af"] = "@function.outer",
				["if"] = "@function.inner",
				-- classes / structs / modules
				["ac"] = "@class.outer",
				["ic"] = "@class.inner",
				-- conditionals / loops
				["ai"] = "@conditional.outer",
				["ii"] = "@conditional.inner",
				["al"] = "@loop.outer",
				["il"] = "@loop.inner",
				-- parameters/arguments
				["aa"] = "@parameter.outer",
				["ia"] = "@parameter.inner",
			},
			selection_modes = {
				["@function.outer"] = "V",
				["@class.outer"] = "V",
			},
		},

		move = {
			enable = true,
			set_jumps = true,
			goto_next_start = {
				["]f"] = "@function.outer",
				["]c"] = "@class.outer",
			},
			goto_next_end = {
				["]F"] = "@function.outer",
				["]C"] = "@class.outer",
			},
			goto_previous_start = {
				["[f"] = "@function.outer",
				["[c"] = "@class.outer",
			},
			goto_previous_end = {
				["[F"] = "@function.outer",
				["[C"] = "@class.outer",
			},
		},

		swap = {
			enable = true,
			swap_next = {
				["]a"] = "@parameter.inner",
			},
			swap_previous = {
				["[a"] = "@parameter.inner",
			},
		},
	},

	config = function(_, opts)
		-- Treesitter’s setup accepts partial tables at any time and merges them.
		-- Adding the top-level TSConfig fields here would risk overriding your main
		-- nvim-treesitter config, so we keep this narrowly scoped and silence the
		-- typechecker for missing top-level fields.
		---@diagnostic disable-next-line: missing-fields
		require("nvim-treesitter.configs").setup({
			textobjects = opts,
		})
	end,
}
