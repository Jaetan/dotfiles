return {
	"folke/neodev.nvim",
	event = "VeryLazy",
	opts = { library = { plugins = true, types = true } },
	config = function(_, opts)
		require("neodev").setup(opts)
	end,
}
