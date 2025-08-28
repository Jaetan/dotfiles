return {
	{
		"stevearc/dressing.nvim",
		event = "VeryLazy",
		opts = { input = { border = "rounded" }, select = { backend = { "telescope", "builtin" } } },
	},
	{
		"nvim-telescope/telescope-ui-select.nvim",
		lazy = true,
		config = function()
			local ok, telescope = pcall(require, "telescope")
			if ok then
				telescope.setup({ extensions = { ["ui-select"] = {} } })
				pcall(telescope.load_extension, "ui-select")
			end
		end,
	},
}
