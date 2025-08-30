return {
	"echasnovski/mini.ai",
	version = "*",
	event = "VeryLazy",
	opts = function()
		local ai = require("mini.ai")
		local ts = ai.gen_spec.treesitter

		return {
			-- how many surrounding lines to search for textobjects
			n_lines = 500,

			-- Extra, Treesitter-powered textobjects
			custom_textobjects = {
				-- arguments/parameters → `aa` / `ia`
				a = ts({ a = "@parameter.outer", i = "@parameter.inner" }),
				-- functions            → `af` / `if`
				f = ts({ a = "@function.outer", i = "@function.inner" }),
				-- classes/structs      → `ac` / `ic`
				c = ts({ a = "@class.outer", i = "@class.inner" }),
				-- conditionals         → `ao` / `io`
				o = ts({ a = "@conditional.outer", i = "@conditional.inner" }),
				-- loops                → `al` / `il`
				l = ts({ a = "@loop.outer", i = "@loop.inner" }),
				-- whole comments block → `aC` / `iC`
				C = ts({ a = "@comment.outer", i = "@comment.outer" }),
			},
		}
	end,
	config = function(_, opts)
		require("mini.ai").setup(opts)

		-- which-key hints (optional)
		pcall(function()
			require("which-key").add({
				{ "aa", desc = "around argument" },
				{ "ia", desc = "inside argument" },
				{ "af", desc = "around function" },
				{ "if", desc = "inside function" },
				{ "ac", desc = "around class" },
				{ "ic", desc = "inside class" },
				{ "ao", desc = "around conditional" },
				{ "io", desc = "inside conditional" },
				{ "al", desc = "around loop" },
				{ "il", desc = "inside loop" },
				{ "aC", desc = "around comment block" },
				{ "iC", desc = "inside comment block" },
			})
		end)
	end,
}
