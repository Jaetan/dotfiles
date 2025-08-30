return {
	-- Enhanced % matching (pairs, HTML tags, nested lang-aware with Treesitter)
	"andymass/vim-matchup",
	event = { "BufReadPost", "BufNewFile" },
	config = function()
		-- Nice offscreen match preview instead of jumping the cursor
		vim.g.matchup_matchparen_offscreen = { method = "popup" }
		-- Defer highlighting a bit for smoother performance
		vim.g.matchup_matchparen_deferred = 1
		-- Enable motions like z% across lines/blocks
		vim.g.matchup_motion_enabled = 1
		-- Don’t spam in super-huge files
		vim.g.matchup_matchparen_stopline = 200

		-- Treesitter integration (enables smart tag/block matching)
		pcall(function()
			require("nvim-treesitter.configs").setup({
				matchup = { enable = true },
			})
		end)
	end,
}
