-- lua/plugins/telescope_extras.lua
return {
	-- Native fzf sorter for Telescope (requires make or cmake)
	{
		"nvim-telescope/telescope-fzf-native.nvim",
		build = "make",
		cond = function()
			return vim.fn.executable("make") == 1 or vim.fn.executable("cmake") == 1
		end,
		-- no config needed; telescope.lua loads the extension
	},
}
