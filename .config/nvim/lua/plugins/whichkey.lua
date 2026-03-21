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
			{ "<leader>b", group = "+buffers" },
			{ "<leader>c", group = "+code" },
			{ "<leader>d", group = "+dune" },
			{ "<leader>f", group = "+files/search" },
			{ "<leader>g", group = "+git" },
			{ "<leader>h", group = "+git (gitsigns)" },
			{ "<leader>j", group = "+jump" },
			{ "<leader>m", group = "+harpoon" },
			{ "<leader>s", group = "+search/replace" },
			{ "<leader>t", group = "+tabs/todo" },
			{ "<leader>u", group = "+utils/session" },
			{ "<leader>w", group = "+windows" },
			{ "<leader>x", group = "+trouble/diagnostics" },
			{ "<leader>z", group = "+folds" },
			{ "g", group = "+goto" },
			{ "gs", group = "+surround", mode = { "n", "v" } },
		})
	end,
}
