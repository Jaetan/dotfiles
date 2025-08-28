return {
	-- Native fzf sorter for Telescope (needs build tools)
	{
		"nvim-telescope/telescope-fzf-native.nvim",
		build = "make", -- or "cmake -S. -Bbuild -DCMAKE_BUILD_TYPE=Release && cmake --build build --config Release && cmake --install build --prefix build"
		cond = function()
			return vim.fn.executable("make") == 1 or vim.fn.executable("cmake") == 1
		end,
		event = "VeryLazy",
		config = function()
			local ok, telescope = pcall(require, "telescope")
			if ok then
				pcall(telescope.load_extension, "fzf")
			end
		end,
	},
}
