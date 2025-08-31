-- lua/plugins/telescope_extras.lua
return {
	-- Native fzf sorter for Telescope (requires make or cmake)
	{
		"nvim-telescope/telescope-fzf-native.nvim",
		build = "make",
		cond = function()
			return vim.fn.executable("make") == 1 or vim.fn.executable("cmake") == 1
		end,
		config = function()
			local ok, telescope = pcall(require, "telescope")
			if ok then
				pcall(telescope.load_extension, "fzf")
			end
		end,
	},
}
