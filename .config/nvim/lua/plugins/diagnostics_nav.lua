return {
	-- 1) Trouble: unified diagnostics/refs/quickfix UI (v3)
	{
		"folke/trouble.nvim",
		main = "trouble", -- <— add this line
		cmd = "Trouble",
		opts = {
			focus = true,
			warn_no_results = false,
			use_diagnostic_signs = true,
		},
	},

	-- 2) TODO comments: highlight & navigate TODO/FIXME/NOTE etc.
	{
		"folke/todo-comments.nvim",
		dependencies = { "nvim-lua/plenary.nvim" },
		-- Load when editing files, but also expose commands so Alpha can trigger it
		event = { "BufReadPost", "BufNewFile" },
		cmd = { "TodoTrouble", "TodoTelescope", "TodoQuickFix", "TodoLocList" },
		-- Optional direct keys so Lazy can load on these too (keeps your keymaps file functional)
		keys = {
			{
				"]t",
				function()
					require("todo-comments").jump_next()
				end,
				desc = "Next TODO",
			},
			{
				"[t",
				function()
					require("todo-comments").jump_prev()
				end,
				desc = "Prev TODO",
			},
		},
		opts = {
			signs = true,
			highlight = { multiline = false },
			-- keep ripgrep simple & inclusive
			search = {
				command = "rg",
				args = {
					"--hidden",
					"--glob",
					"!.git",
					"--no-heading",
					"--with-filename",
					"--line-number",
					"--column",
					"--color",
					"never",
				},
				-- default regex requires a trailing colon after the keyword:
				-- TODO: fixme: NOTE: etc.
				-- pattern = [[\b(KEYWORDS):]],
			},
		},
		config = function(_, opts)
			require("todo-comments").setup(opts)
			-- Load telescope extension if telescope is available
			pcall(function()
				require("telescope").load_extension("todo-comments")
			end)
		end,
	},

	-- 3) Aerial: symbols/outline pane (LSP + Treesitter)
	{
		"stevearc/aerial.nvim",
		cmd = { "AerialToggle", "AerialOpen", "AerialClose", "AerialNavToggle" },
		event = "LspAttach",
		opts = {
			backends = { "lsp", "treesitter", "markdown" },
			layout = { default_direction = "right", min_width = 30 },
			attach_mode = "global",
			show_guides = true,
			filter_kind = false, -- show all kinds by default
		},
	},

	-- 4) Better Quickfix (preview, filtering)
	{
		"kevinhwang91/nvim-bqf",
		ft = "qf",
		opts = {
			preview = { win_height = 15, win_vheight = 15, delay_syntax = 50 },
			func_map = { open = "<CR>" },
		},
	},

	-- Project-wide search/replace UI
	{
		"nvim-pack/nvim-spectre",
		dependencies = { "nvim-lua/plenary.nvim" },
		event = "VeryLazy",
		opts = {
			live_update = true,
			is_block_ui_break = true,
			open_cmd = "vnew", -- open results in a vertical split
		},
	},
}
