return {
	-- Diagnostics/workspace lists
	{
		"folke/trouble.nvim",
		cmd = "Trouble",
		opts = { use_diagnostic_signs = true },
		keys = {
			{ "<leader>xx", "<cmd>Trouble diagnostics toggle<cr>", desc = "Trouble: Diagnostics" },
			{ "<leader>xs", "<cmd>Trouble symbols toggle focus=false<cr>", desc = "Trouble: Symbols (buffer)" },
		},
	},
	-- TODO/FIXME/NOTE highlights + Telescope/Trouble integrations
	{
		"folke/todo-comments.nvim",
		event = { "BufReadPost", "BufNewFile" },
		dependencies = { "nvim-lua/plenary.nvim" },
		opts = {
			keywords = {
				TODO = { icon = " ", color = "info" },
				FIX = { icon = " ", alt = { "FIXME", "BUG" } },
				HACK = { icon = " " },
				NOTE = { icon = " " },
			},
			highlight = { keyword = "bg" },
			search = { pattern = [[\b(KEYWORDS):]] },
		},
		config = function(_, opts)
			require("todo-comments").setup(opts)
			vim.keymap.set("n", "]t", function()
				require("todo-comments").jump_next()
			end, { desc = "Next TODO" })
			vim.keymap.set("n", "[t", function()
				require("todo-comments").jump_prev()
			end, { desc = "Prev TODO" })
			vim.keymap.set("n", "<leader>xt", "<cmd>TodoTrouble<cr>", { desc = "Trouble: TODOs" })
			vim.keymap.set("n", "<leader>xT", "<cmd>TodoTelescope<cr>", { desc = "Telescope: TODOs" })
			pcall(function()
				require("which-key").add({ { "<leader>x", group = "+trouble/todo" } })
			end)
		end,
	},
}
