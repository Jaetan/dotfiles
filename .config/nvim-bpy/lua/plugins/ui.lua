return {
	-- indent guides
	{
		"lukas-reineke/indent-blankline.nvim",
		main = "ibl",
		event = { "BufReadPre", "BufNewFile" },
		opts = {
			indent = { char = "│", tab_char = "│" },
			whitespace = { remove_blankline_trail = false },
			scope = { enabled = true, show_start = false, show_end = false },
			exclude = {
				filetypes = { "help", "alpha", "dashboard", "gitcommit", "TelescopePrompt", "lspinfo", "checkhealth" },
				buftypes = { "terminal", "nofile" },
			},
		},
		config = function(_, opts)
			require("ibl").setup(opts)
			vim.api.nvim_set_hl(0, "IblIndent", { fg = "#3b4261", nocombine = true })
			vim.api.nvim_set_hl(0, "IblScope", { fg = "#7aa2f7", nocombine = true })
		end,
	},

	-- treesitter + context live in their own file
	-- diagnostic underline highlight colors
	{
		init = function()
			vim.api.nvim_set_hl(0, "DiagnosticUnderlineError", { undercurl = true, sp = "#db4b4b" })
			vim.api.nvim_set_hl(0, "DiagnosticUnderlineWarn", { undercurl = true, sp = "#e0af68" })
			vim.api.nvim_set_hl(0, "DiagnosticUnderlineInfo", { undercurl = true, sp = "#0db9d7" })
			vim.api.nvim_set_hl(0, "DiagnosticUnderlineHint", { undercurl = true, sp = "#10B981" })
			vim.diagnostic.config({
				virtual_text = false,
				severity_sort = true,
				float = { border = "rounded", source = "always", focusable = false },
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
		end,
	},

	-- (optional icons lib)
	{ "echasnovski/mini.icons", version = false, lazy = true },

	-- alpha dashboard
	{
		"goolord/alpha-nvim",
		dependencies = { "nvim-tree/nvim-web-devicons" },
		event = "VimEnter",
		config = function()
			local alpha = require("alpha")
			local dashboard = require("alpha.themes.dashboard")
			dashboard.section.header.val = { " ", "  neovim — minimal LSP profile (basedpyright)", " " }
			local btn = dashboard.button
			dashboard.section.buttons.val = {
				btn("e", "  New file", ":ene | startinsert<CR>"),
				btn("o", "  Open file", ":edit <CR>"),
				btn("m", "  Mason", ":Mason<CR>"),
				btn("l", "  LSP Info", ":LspInfo<CR>"),
				btn("u", "  Lazy", ":Lazy<CR>"),
				btn("q", "  Quit", ":qa<CR>"),
			}
			dashboard.section.footer.val = function()
				local v = vim.env.VIRTUAL_ENV or "none"
				return "venv: " .. v
			end
			dashboard.opts.opts.noautocmd = true
			alpha.setup(dashboard.opts)
		end,
	},
}
