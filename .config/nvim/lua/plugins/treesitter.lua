return {
	{
		"nvim-treesitter/nvim-treesitter",
		build = ":TSUpdate",
		event = { "BufReadPre", "BufNewFile" },
		opts = {
			ensure_installed = {
				"lua",
				"python",
				"html",
				"css",
				"javascript",
				"json",
				"regex",
				"vim",
				"vimdoc",
				"markdown",
				"markdown_inline",
			},
			highlight = { enable = true, additional_vim_regex_highlighting = false },
			indent = { enable = false },
			auto_install = true,
		},
		config = function(_, opts)
			require("nvim-treesitter.install").prefer_git = true
			require("nvim-treesitter.install").compilers = { "clang", "gcc" }
			require("nvim-treesitter.install").parser_install_dir = vim.fn.stdpath("data") .. "/site/parser"
			vim.opt.runtimepath:append(vim.fn.stdpath("data") .. "/site")
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
