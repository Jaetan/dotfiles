-- Show diagnostics as wrapped virtual lines (toggable)
return {
	{
		"ErichDonGubler/lsp_lines.nvim", -- maintained fork of lsp_lines
		event = "LspAttach",
		opts = {},
		config = function(_, _)
			require("lsp_lines").setup()

			-- Start with lines OFF to respect your current style (no virtual_text, float on demand).
			-- lsp_lines adds the 'virtual_lines' option to vim.diagnostic.config().
			vim.diagnostic.config({ virtual_lines = false })

			-- Toggle: \xt  → show/hide inline diagnostic lines for the current UI session.
			vim.keymap.set("n", "<leader>xt", function()
				local cfg = vim.diagnostic.config()
				local on = cfg.virtual_lines == true
				vim.diagnostic.config({
					virtual_lines = not on,
					-- keep virtual_text disabled to avoid double-rendering; you use floats with <leader>e
					virtual_text = false,
				})
				vim.notify("Diagnostic lines: " .. ((not on) and "ON" or "OFF"))
			end, { desc = "Toggle diagnostic virtual lines" })

			-- which-key labels (non-fatal if which-key isn’t loaded yet)
			pcall(function()
				require("which-key").add({
					{ "<leader>x", group = "+trouble/diagnostics" },
					{ "<leader>xt", desc = "Toggle diagnostic virtual lines" },
				})
			end)
		end,
	},
}
