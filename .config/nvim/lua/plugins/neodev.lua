return {
	"folke/neodev.nvim",
	-- Lazy-load is fine because requiring 'neodev' in lsp.lua will trigger it
	lazy = true,
	opts = {
		library = { plugins = true, types = true },
		pathStrict = true,
	},
}
