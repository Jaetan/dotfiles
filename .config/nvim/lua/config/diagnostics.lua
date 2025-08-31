-- diagnostic highlight + config (no plugin spec needed)

-- subtle undercurls (Tokyonight-friendly)
vim.api.nvim_set_hl(0, "DiagnosticUnderlineError", { undercurl = true, sp = "#db4b4b" })
vim.api.nvim_set_hl(0, "DiagnosticUnderlineWarn", { undercurl = true, sp = "#e0af68" })
vim.api.nvim_set_hl(0, "DiagnosticUnderlineInfo", { undercurl = true, sp = "#0db9d7" })
vim.api.nvim_set_hl(0, "DiagnosticUnderlineHint", { undercurl = true, sp = "#10B981" })

-- signs + float behavior
vim.diagnostic.config({
	virtual_text = false,
	severity_sort = true,
	float = {
		border = "rounded",
		source = true, -- was "always"; valid values: true|false|"if_many"
		focusable = false,
	},
	signs = {
		priority = 10,
		text = {
			[vim.diagnostic.severity.ERROR] = " ",
			[vim.diagnostic.severity.WARN] = " ",
			[vim.diagnostic.severity.INFO] = " ",
			[vim.diagnostic.severity.HINT] = "󰠠 ",
		},
	},
})
