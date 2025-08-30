return {
	"echasnovski/mini.align",
	version = "*",
	event = "VeryLazy",
	opts = {
		-- Avoid clobbering your existing `gA` mapping (you use it for code actions preview)
		mappings = {
			start = "ga", -- operator-pending align (e.g., `gaip=`)
			start_with_preview = "g=", -- live preview variant (easy to reach, no conflict)
		},
	},
	config = function(_, opts)
		require("mini.align").setup(opts)

		-- which-key helpers (non-fatal)
		pcall(function()
			require("which-key").add({
				{ "ga", desc = "Align (operator) — textobj/motion then delimiter(s)" },
				{ "g=", desc = "Align (preview) — live preview version of ga" },
			})
		end)
	end,
}
