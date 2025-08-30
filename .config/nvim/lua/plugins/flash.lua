return {
	"folke/flash.nvim",
	event = "VeryLazy",
	opts = {
		labels = "asdfghjklqwertyuiopzxcvbnm",
		modes = {
			search = { enabled = true },
			char = { enabled = false }, -- keep native f/F/t/T as-is
		},
	},
	config = function(_, opts)
		local flash = require("flash")
		flash.setup(opts)

		-- Jump anywhere quickly
		vim.keymap.set({ "n", "x" }, "s", flash.jump, { desc = "Flash jump" })
		-- Treesitter-powered structural jump
		vim.keymap.set({ "n", "x" }, "S", flash.treesitter, { desc = "Flash treesitter" })
		-- Remote operator targets (only in operator-pending, so we don't steal normal 'r')
		vim.keymap.set("o", "r", flash.remote, { desc = "Flash remote (operator)" })
		-- Search current word occurrences with labels
		vim.keymap.set({ "n", "x", "o" }, "gs", flash.jump, { desc = "Flash (alt key)" })

		-- which-key hints (non-fatal)
		pcall(function()
			require("which-key").add({
				{ "s", desc = "Flash jump" },
				{ "S", desc = "Flash treesitter" },
				{ "r", mode = "o", desc = "Flash remote (operator)" },
				{ "gs", desc = "Flash (alt key)" },
			})
		end)
	end,
}
