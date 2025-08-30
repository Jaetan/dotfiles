return {
	"kevinhwang91/nvim-bqf",
	ft = "qf",
	opts = {
		auto_resize_height = true,
		preview = {
			win_height = 15,
			win_vheight = 15,
			wrap = false,
			-- delay applying treesitter highlight in preview a bit on huge files
			delay_syntax = 50,
		},
	},
}
