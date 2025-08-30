return {
	-- augment existing Comment.nvim config to use context-aware comments
	"numToStr/Comment.nvim",
	event = "VeryLazy",
	opts = function(_, opts)
		opts = opts or {}
		local ok, tscc = pcall(require, "ts_context_commentstring.integrations.comment_nvim")
		if ok then
			opts.pre_hook = tscc.create_pre_hook()
		end
		return opts
	end,
}
