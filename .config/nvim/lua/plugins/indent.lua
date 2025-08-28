-- lua/plugins/indent.lua
return {
	"lukas-reineke/indent-blankline.nvim",
	main = "ibl",
	event = { "BufReadPre", "BufNewFile" },

	opts = {
		indent = {
			-- Use a glyph that stands out from split/gutter lines
			char = "┆",
			tab_char = "┆",
		},
		whitespace = {
			remove_blankline_trail = false, -- draw guides through empty lines
		},
		scope = {
			enabled = true,
			show_start = false,
			show_end = false,
		},
		exclude = {
			filetypes = {
				"help",
				"alpha",
				"dashboard",
				"gitcommit",
				"TelescopePrompt",
				"lspinfo",
				"checkhealth",
				"neo-tree",
			},
			buftypes = { "terminal", "nofile" },
		},
	},

	config = function(_, opts)
		local ibl = require("ibl")
		ibl.setup(opts)

		-- Theme-friendly highlights (Tokyonight-ish defaults)
		local function set_ibl_hl()
			-- dim rail + brighter current scope
			pcall(vim.api.nvim_set_hl, 0, "IblIndent", { fg = "#3b4261", nocombine = true })
			pcall(vim.api.nvim_set_hl, 0, "IblScope", { fg = "#7aa2f7", nocombine = true })
		end

		set_ibl_hl()
		-- keep colors correct if you change colorschemes
		vim.api.nvim_create_autocmd("ColorScheme", {
			callback = function()
				vim.schedule(set_ibl_hl)
			end,
		})
	end,
}
