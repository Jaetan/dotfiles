-- Fancy Code Actions picker (Telescope-backed)
return {
	{
		"aznhe21/actions-preview.nvim",
		dependencies = { "nvim-lua/plenary.nvim", "nvim-telescope/telescope.nvim" },
		-- Load on first use
		keys = {
			{
				"<leader>ca",
				function()
					require("actions-preview").code_actions()
				end,
				mode = { "n", "v" },
				desc = "Code actions (preview)",
			},
			{
				"gA",
				function()
					require("actions-preview").code_actions()
				end,
				mode = { "n", "v" },
				desc = "Code actions (preview)",
			},
		},
		opts = function()
			local has_tel, themes = pcall(require, "telescope.themes")
			local picker = has_tel
					and themes.get_dropdown({
						initial_mode = "normal",
						previewer = true,
						results_title = false,
						prompt_title = "Code actions",
					})
				or {}
			return {
				telescope = picker,
				diff = { ctxlen = 6 },
			}
		end,
		config = function(_, opts)
			require("actions-preview").setup(opts)
			pcall(function()
				require("which-key").add({ { "<leader>c", group = "+code" } })
			end)
		end,
	},
}
