return {
	"rcarriga/nvim-notify",
	event = "VeryLazy",
	opts = { stages = "fade_in_slide_out", timeout = 2000, render = "compact", background_colour = "#1a1b26" },
	config = function(_, opts)
		local notify = require("notify")
		notify.setup(opts)
		vim.notify = notify
	end,
}
