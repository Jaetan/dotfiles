return {
	"folke/noice.nvim",
	event = "VeryLazy",
	dependencies = {
		"MunifTanjim/nui.nvim",
		"rcarriga/nvim-notify", -- we already set vim.notify to notify in your config
	},
	opts = {
		-- keep LSP hovers/signatures as-is (so K remains your LSP hover)
		lsp = {
			progress = { enabled = true },
			hover = { enabled = true },
			signature = { enabled = true },
			override = {
				["vim.lsp.util.convert_input_to_markdown_lines"] = true,
				["vim.lsp.util.stylize_markdown"] = true,
				["cmp.entry.get_documentation"] = true,
			},
		},

		presets = {
			bottom_search = true, -- command-line style search at the bottom
			command_palette = true, -- position cmdline+popup together
			long_message_to_split = true, -- long messages go to a split
			lsp_doc_border = true, -- borders for LSP docs if ever used
		},

		views = {
			mini = { timeout = 2000 },
			cmdline_popup = {
				position = { row = "30%", col = "50%" },
				size = { width = 60, height = "auto" },
				border = { style = "rounded" },
				win_options = { winblend = 0 },
			},
		},

		routes = {
			-- hide noisy bits
			{ filter = { event = "msg_show", kind = "search_count" }, opts = { skip = true } },
			{ filter = { event = "msg_show", find = "search hit BOTTOM" }, opts = { skip = true } },
			{ filter = { event = "msg_show", find = "search hit TOP" }, opts = { skip = true } },
			{ filter = { event = "msg_show", find = "written" }, opts = { skip = true } },
		},
	},
}
