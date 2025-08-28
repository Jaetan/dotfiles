return {
	"NvChad/nvim-colorizer.lua",
	event = { "BufReadPost", "BufNewFile" },
	opts = {
		filetypes = { "css", "scss", "less", "html", "javascript", "typescript", "lua" },
		user_default_options = { names = false }, -- avoid named colors like "red"
	},
	config = function(_, opts)
		require("colorizer").setup(opts)
	end,
}
