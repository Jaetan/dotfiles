return {
	"folke/which-key.nvim",
	event = "VeryLazy",
	opts = {
		win = { border = "rounded", padding = { 1, 2 }, title = true, title_pos = "center", zindex = 1000 },
		layout = { align = "left" },
	},
	config = function(_, opts)
		local wk = require("which-key")
		wk.setup(opts)
		wk.add({
			{ "<leader>w", group = "+windows" },
			{ "<leader>t", group = "+tabs" },
			{ "<leader>f", group = "+files/search" }, -- will also show +file/format from files.lua
			{ "<leader>h", group = "+git (gitsigns)" },
			{ "g", group = "+goto" },
		})
	end,
}
