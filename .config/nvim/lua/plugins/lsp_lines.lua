return {
	"https://git.sr.ht/~whynothugo/lsp_lines.nvim",
	event = "VeryLazy",

	config = function()
		local ok, lsp_lines = pcall(require, "lsp_lines")
		if not ok then
			return
		end
		lsp_lines.setup()

		-- Read your diagnostics default (if present) without assuming shape.
		local enable_lines = false
		do
			local ok_cfg, cfg = pcall(require, "config.diagnostics_config")
			if ok_cfg and type(cfg) == "table" and cfg.virtual_lines == true then
				enable_lines = true
			end
		end

		-- Apply initial state (plugin honors 'virtual_lines' in vim.diagnostic.config)
		vim.diagnostic.config({
			virtual_text = not enable_lines,
			virtual_lines = enable_lines,
		})

		-- Toggle: swap between virtual text and virtual lines
		vim.keymap.set("n", "<leader>uL", function()
			local cur = vim.diagnostic.config().virtual_lines == true
			local newv = not cur
			vim.diagnostic.config({
				virtual_text = not newv,
				virtual_lines = newv,
			})
			vim.notify("Diagnostics: " .. (newv and "virtual LINES" or "virtual TEXT"))
		end, { desc = "Diagnostics: toggle virtual lines" })

		pcall(function()
			require("which-key").add({
				{ "<leader>u", group = "+utils/session" },
				{ "<leader>uL", desc = "Diagnostics: toggle virtual lines" },
			})
		end)
	end,
}
