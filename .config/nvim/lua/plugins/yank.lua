return {
	"gbprod/yanky.nvim",
	event = "VeryLazy",
	dependencies = { "nvim-telescope/telescope.nvim" },
	opts = {
		ring = {
			history_length = 200,
			storage = "shada", -- persist across sessions
			sync_with_numbered_registers = true, -- keep 0-9 in sync
		},
		system_clipboard = {
			sync_with_ring = true, -- sync with + register
		},
		highlight = {
			on_put = true,
			on_yank = true,
			timer = 120,
		},
		preserve_cursor_position = {
			enabled = true,
		},
	},
	keys = {
		{ "[y", "<Plug>(YankyCycleBackward)", desc = "Yank ring prev", mode = { "n", "x" } },
		{ "]y", "<Plug>(YankyCycleForward)", desc = "Yank ring next", mode = { "n", "x" } },
		{
			"<leader>fy",
			function()
				require("telescope").extensions.yank_history.yank_history({})
			end,
			desc = "Telescope: Yank history",
		},
		-- (Optional) enable these if you want yanky to drive all puts:
		{ "p", "<Plug>(YankyPutAfter)", mode = { "n", "x" }, desc = "Put after (yank ring)" },
		{ "P", "<Plug>(YankyPutBefore)", mode = { "n", "x" }, desc = "Put before (yank ring)" },
		{ "gp", "<Plug>(YankyGPutAfter)", mode = { "n", "x" }, desc = "Put after (stay)" },
		{ "gP", "<Plug>(YankyGPutBefore)", mode = { "n", "x" }, desc = "Put before (stay)" },
	},
	config = function(_, opts)
		require("yanky").setup(opts)
		local ok, telescope = pcall(require, "telescope")
		if ok then
			pcall(telescope.load_extension, "yank_history")
		end
	end,
}
