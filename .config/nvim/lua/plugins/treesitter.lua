return {
	{
		"nvim-treesitter/nvim-treesitter",
		build = ":TSUpdate",
		event = { "BufReadPre", "BufNewFile" },
		opts = {
			-- Only install what your AppImage does NOT already ship
			ensure_installed = {
				"python",
				"html",
				"css",
				"javascript",
				"json",
				"regex",
				-- If/when you want OCaml highlighting (and ABI settles), add:
				-- "ocaml", "ocaml_interface",
			},
			highlight = { enable = true, additional_vim_regex_highlighting = false },
			indent = { enable = false },
			auto_install = true,
		},
		config = function(_, opts)
			-- Let TS use its defaults; don't override install dir or rtp
			require("nvim-treesitter.configs").setup(opts)
		end,
	},

	{
		"nvim-treesitter/nvim-treesitter-context",
		event = { "BufReadPre", "BufNewFile" },
		opts = {
			enable = true,
			max_lines = 3,
			min_window_height = 0,
			line_numbers = true,
			multiline_threshold = 20,
			trim_scope = "outer",
			mode = "cursor",
			separator = "─",
			zindex = 20,
		},
		config = function(_, opts)
			require("treesitter-context").setup(opts)
			vim.keymap.set("n", "<leader>tc", function()
				require("treesitter-context").toggle()
			end, { desc = "Toggle Treesitter Context" })
		end,
	},
}
