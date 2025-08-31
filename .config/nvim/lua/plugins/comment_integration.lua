return {
	-- Augment existing Comment.nvim config to use Treesitter-aware commentstrings
	"numToStr/Comment.nvim",
	event = "VeryLazy",
	dependencies = { "JoosepAlviste/nvim-ts-context-commentstring" },
	opts = function(_, opts)
		opts = opts or {}

		-- If you already had a pre_hook, we’ll chain it after the TS one.
		local existing_pre = opts.pre_hook

		local ok, tscc = pcall(require, "ts_context_commentstring.integrations.comment_nvim")
		if ok and tscc and tscc.create_pre_hook then
			local ts_pre = tscc.create_pre_hook()
			if existing_pre then
				opts.pre_hook = function(ctx)
					ts_pre(ctx)
					return existing_pre(ctx)
				end
			else
				opts.pre_hook = ts_pre
			end
		end

		return opts
	end,
}
