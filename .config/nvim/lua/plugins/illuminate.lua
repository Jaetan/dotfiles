return {
	"RRethy/vim-illuminate",
	event = "VeryLazy",
	config = function()
		local illuminate = require("illuminate")

		illuminate.configure({
			-- Try LSP first, then Treesitter, then a fast regex fallback.
			providers = { "lsp", "treesitter", "regex" },
			delay = 120, -- ms before highlighting
			large_file_cutoff = 2000, -- disable on huge files
			large_file_overrides = { providers = { "lsp" } },

			-- Keep the dashboard & prompts clean.
			filetypes_denylist = {
				"alpha",
				"neo-tree",
				"TelescopePrompt",
				"lazy",
				"lspinfo",
				"checkhealth",
				"gitcommit",
				"help",
			},
			modes_denylist = { "i" }, -- no highlight while typing
			under_cursor = true, -- include the word under cursor
			min_count_to_highlight = 1,
		})

		-- Use LSP reference highlights (Tokyonight styles these nicely).
		vim.api.nvim_set_hl(0, "IlluminatedWordText", { link = "LspReferenceText" })
		vim.api.nvim_set_hl(0, "IlluminatedWordRead", { link = "LspReferenceRead" })
		vim.api.nvim_set_hl(0, "IlluminatedWordWrite", { link = "LspReferenceWrite" })

		-- Quick jumps between references (buffer-local friendly defaults).
		vim.keymap.set("n", "]r", function()
			illuminate.goto_next_reference(false)
		end, { desc = "Illuminate: next reference" })

		vim.keymap.set("n", "[r", function()
			illuminate.goto_prev_reference(false)
		end, { desc = "Illuminate: prev reference" })

		-- Toggle highlight quickly (pause/resume provider updates).
		local paused = false
		vim.keymap.set("n", "<leader>uI", function()
			if paused then
				illuminate.resume()
			else
				illuminate.pause()
			end
			paused = not paused
			vim.notify("Symbol highlight: " .. (paused and "OFF" or "ON"))
		end, { desc = "Toggle symbol highlight" })

		-- which-key label (non-fatal)
		pcall(function()
			require("which-key").add({
				{ "]r", desc = "Next reference" },
				{ "[r", desc = "Prev reference" },
				{ "<leader>u", group = "+utils/session" }, -- keep existing group
				{ "<leader>uI", desc = "Toggle symbol highlight" },
			})
		end)
	end,
}
