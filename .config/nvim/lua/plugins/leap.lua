return {
	"ggandor/leap.nvim",
	event = "VeryLazy",
	config = function()
		local leap = require("leap")

		-- Keep defaults sensible; do NOT hijack plain `s`
		-- (we intentionally do NOT call leap.add_default_mappings())
		leap.opts.case_sensitive = false

		-- Leader-based keymaps:
		--   \jf  -> leap forward in current window
		--   \jb  -> leap backward in current window
		--   \jw  -> leap across all normal windows
		vim.keymap.set({ "n", "x", "o" }, "<leader>jf", function()
			leap.leap({ target_windows = { vim.api.nvim_get_current_win() } })
		end, { desc = "Leap forward (current window)" })

		vim.keymap.set({ "n", "x", "o" }, "<leader>jb", function()
			leap.leap({ backward = true, target_windows = { vim.api.nvim_get_current_win() } })
		end, { desc = "Leap backward (current window)" })

		vim.keymap.set({ "n", "x", "o" }, "<leader>jw", function()
			local wins = {}
			for _, win in ipairs(vim.api.nvim_list_wins()) do
				local buf = vim.api.nvim_win_get_buf(win)
				if vim.bo[buf].buftype == "" then
					table.insert(wins, win)
				end
			end
			leap.leap({ target_windows = wins })
		end, { desc = "Leap across windows" })

		-- which-key hints
		pcall(function()
			require("which-key").add({
				{ "<leader>j", group = "+jump/leap" },
				{ "<leader>jf", desc = "Leap forward" },
				{ "<leader>jb", desc = "Leap backward" },
				{ "<leader>jw", desc = "Leap across windows" },
			})
		end)
	end,
}
