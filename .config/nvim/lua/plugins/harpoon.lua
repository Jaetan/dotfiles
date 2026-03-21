return {
	{
		"ThePrimeagen/harpoon",
		branch = "harpoon2",
		dependencies = { "nvim-lua/plenary.nvim" },
		keys = {
			{
				"<leader>ma",
				function()
					require("harpoon"):list():add()
					vim.notify("Harpoon: added file")
				end,
				desc = "Harpoon: add file",
			},
			{
				"<leader>mm",
				function()
					local h = require("harpoon")
					h.ui:toggle_quick_menu(h:list())
				end,
				desc = "Harpoon: menu",
			},
			{
				"<leader>mn",
				function()
					require("harpoon"):list():next()
				end,
				desc = "Harpoon: next",
			},
			{
				"<leader>mp",
				function()
					require("harpoon"):list():prev()
				end,
				desc = "Harpoon: prev",
			},
			{
				"<leader>m1",
				function()
					require("harpoon"):list():select(1)
				end,
				desc = "Harpoon: go to 1",
			},
			{
				"<leader>m2",
				function()
					require("harpoon"):list():select(2)
				end,
				desc = "Harpoon: go to 2",
			},
			{
				"<leader>m3",
				function()
					require("harpoon"):list():select(3)
				end,
				desc = "Harpoon: go to 3",
			},
			{
				"<leader>m4",
				function()
					require("harpoon"):list():select(4)
				end,
				desc = "Harpoon: go to 4",
			},
		},
		opts = {
			settings = {
				save_on_toggle = true,
				sync_on_ui_close = true,
				key = function()
					return require("util.root").get()
				end,
			},
		},
		config = function(_, opts)
			require("harpoon"):setup(opts)
		end,
	},
}
