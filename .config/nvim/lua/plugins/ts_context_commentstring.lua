return {
	"JoosepAlviste/nvim-ts-context-commentstring",
	event = "VeryLazy",
	opts = {
		-- We drive commentstring updates via Comment.nvim’s pre_hook
		enable_autocmd = false,
	},
	config = function(_, opts)
		require("ts_context_commentstring").setup(opts)
	end,
}
