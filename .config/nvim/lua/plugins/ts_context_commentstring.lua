return {
	"JoosepAlviste/nvim-ts-context-commentstring",
	event = "VeryLazy",
	opts = {
		-- we’ll drive it from Comment.nvim’s pre_hook instead of the autocmd
		enable_autocmd = false,
	},
	config = function(_, opts)
		require("ts_context_commentstring").setup(opts)
	end,
}
