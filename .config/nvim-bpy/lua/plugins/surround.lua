return {
	"echasnovski/mini.surround",
	version = false,
	event = "VeryLazy",
	opts = {
		mappings = {
			add = "gsa", -- add surrounding
			delete = "gsd", -- delete surrounding
			replace = "gsr", -- replace surrounding
			find = "gsf", -- find to the right
			find_left = "gsF", -- find to the left
			highlight = "gsh", -- highlight surrounding
			update_n_lines = "gsn",
		},
	},
	config = function(_, opts)
		require("mini.surround").setup(opts)
		pcall(function()
			require("which-key").add({
				{ "gs", group = "+surround", mode = { "n", "v" } },
			})
		end)
	end,
}
