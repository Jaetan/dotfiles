return {
	"gbprod/yanky.nvim",
	dependencies = { "nvim-telescope/telescope.nvim" },
	opts = {
		ring = { history_length = 100 },
		highlight = { on_put = true, on_yank = true },
	},
	keys = {
		{ "[y", "<Plug>(YankyCycleBackward)", desc = "Yank ring prev" },
		{ "]y", "<Plug>(YankyCycleForward)", desc = "Yank ring next" },
		{
			"<leader>fy",
			function()
				require("telescope").extensions.yank_history.yank_history()
			end,
			desc = "Telescope: Yank history",
		},
	},
	config = function(_, opts)
		require("yanky").setup(opts)
		local ok, telescope = pcall(require, "telescope")
		if ok then
			pcall(telescope.load_extension, "yank_history")
		end
	end,
}
