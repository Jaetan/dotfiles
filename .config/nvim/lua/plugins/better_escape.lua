return {
	"max397574/better-escape.nvim",
	event = "InsertEnter",
	opts = {
		mapping = { "jk" }, -- only 'jk' to keep it predictable
		timeout = 200, -- must press within 200ms (adjust if you like)
		clear_empty_lines = true,
		keys = function()
			return "<Esc>"
		end,
	},
	config = function(_, opts)
		require("better_escape").setup(opts)
	end,
}
