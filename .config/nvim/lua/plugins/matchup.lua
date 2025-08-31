return {
	"andymass/vim-matchup",
	event = { "BufReadPost", "BufNewFile" },

	-- Lightweight core settings via globals (official way for vim-matchup)
	init = function()
		-- Show matches that scroll offscreen in a small popup
		vim.g.matchup_matchparen_offscreen = { method = "popup" }
		-- Faster/less jumpy updates
		vim.g.matchup_matchparen_deferred = 1
		vim.g.matchup_matchparen_deferred_show_delay = 50
		vim.g.matchup_matchparen_deferred_hide_delay = 300
		-- Tweak motion behavior a bit (feel free to adjust)
		vim.g.matchup_motion_enabled = 1
		vim.g.matchup_text_obj_enabled = 1
	end,

	config = function()
		-- Enable the Treesitter integration for smarter matching
		local ok, ts_configs = pcall(require, "nvim-treesitter.configs")
		if not ok then
			return
		end
		---@diagnostic disable-next-line: missing-fields
		ts_configs.setup({
			matchup = {
				enable = true,
				-- include_match_words is harmless and improves matching for some langs
				include_match_words = true,
			},
		})
	end,
}
