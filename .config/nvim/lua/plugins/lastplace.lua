return {
	"ethanholz/nvim-lastplace",
	event = "BufReadPost",
	opts = {
		lastplace_ignore_buftype = { "quickfix", "nofile", "help" },
		lastplace_ignore_filetype = {
			"gitcommit",
			"gitrebase",
			"svn",
			"hgcommit",
			"alpha",
			"neo-tree",
			"TelescopePrompt",
		},
		lastplace_open_folds = true,
	},
	config = function(_, opts)
		require("nvim-lastplace").setup(opts)
	end,
}
