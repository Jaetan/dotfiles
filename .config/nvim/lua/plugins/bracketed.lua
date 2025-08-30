return {
	"echasnovski/mini.bracketed",
	version = false,
	event = "VeryLazy",
	opts = {
		-- Enable only the groups we want to avoid stepping on custom maps.
		-- Defaults map to:
		--   [b ]b buffers, [d ]d diagnostics, [q ]q quickfix, [l ]l loclist,
		--   [o ]o oldfiles, [w ]w windows, [i ]i indent, [u ]u undo, [f ]f files
		buffer = { suffix = "" },
		diagnostic = { suffix = "" },
		quickfix = { suffix = "" },
		location = { suffix = "" },
		oldfile = { suffix = "" },
		window = { suffix = "" },
		indent = { suffix = "" },
		undo = { suffix = "" },
		file = { suffix = "" },
		-- leave others at default (=disabled) to keep things tidy
		treesitter = { suffix = "" }, -- handy: jumps between TS nodes
	},
	config = function(_, opts)
		require("mini.bracketed").setup(opts)

		-- which-key hints (non-fatal)
		pcall(function()
			require("which-key").add({
				{ "[b", desc = "Prev buffer" },
				{ "]b", desc = "Next buffer" },
				{ "[d", desc = "Prev diagnostic" },
				{ "]d", desc = "Next diagnostic" },
				{ "[q", desc = "Prev quickfix" },
				{ "]q", desc = "Next quickfix" },
				{ "[l", desc = "Prev loclist" },
				{ "]l", desc = "Next loclist" },
				{ "[o", desc = "Prev oldfile" },
				{ "]o", desc = "Next oldfile" },
				{ "[w", desc = "Prev window" },
				{ "]w", desc = "Next window" },
				{ "[i", desc = "Prev indent" },
				{ "]i", desc = "Next indent" },
				{ "[u", desc = "Prev undo" },
				{ "]u", desc = "Next undo" },
				{ "[f", desc = "Prev file" },
				{ "]f", desc = "Next file" },
			})
		end)
	end,
}
