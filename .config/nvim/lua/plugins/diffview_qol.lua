return {
	"sindrets/diffview.nvim",
	dependencies = { "nvim-lua/plenary.nvim" },
	cmd = {
		"DiffviewOpen",
		"DiffviewClose",
		"DiffviewToggleFiles",
		"DiffviewFocusFiles",
		"DiffviewFileHistory",
	},
	keys = {
		-- Toggle main Diffview (open vs close based on current state)
		{
			"<leader>gv",
			function()
				local ok, lib = pcall(require, "diffview.lib")
				if not ok then
					return
				end
				if next(lib.views) == nil then
					vim.cmd("DiffviewOpen")
				else
					vim.cmd("DiffviewClose")
				end
			end,
			desc = "Diffview: toggle (open/close)",
		},

		-- File history: current file / whole repo
		{ "<leader>gf", "<cmd>DiffviewFileHistory %<CR>", desc = "Diffview: file history (current file)" },
		{ "<leader>gH", "<cmd>DiffviewFileHistory<CR>", desc = "Diffview: repo history" },

		-- Files panel controls (when Diffview is open)
		{ "<leader>gF", "<cmd>DiffviewToggleFiles<CR>", desc = "Diffview: toggle files panel" },
		{ "<leader>g>", "<cmd>DiffviewFocusFiles<CR>", desc = "Diffview: focus files panel" },

		-- Explicit close (if you prefer not to use the toggle)
		{ "<leader>gV", "<cmd>DiffviewClose<CR>", desc = "Diffview: close" },
	},
	opts = {
		enhanced_diff_hl = true,
		view = {
			default = { winbar = nil }, -- keep it clean; your lualine already shows context
			merge_tool = {
				layout = "diff3_mixed",
				disable_diagnostics = true,
			},
		},
		file_panel = {
			listing_style = "tree",
			win_config = { width = 36 },
		},
		commit_log = { merge_base = nil },
		hooks = {
			-- auto-focus the diff on open; you can comment this out if you prefer the files panel
			diff_buf_win_enter = function()
				pcall(function()
					vim.cmd("wincmd l")
				end)
			end,
		},
	},
	config = function(_, opts)
		require("diffview").setup(opts)
		-- which-key niceties
		pcall(function()
			require("which-key").add({
				{ "<leader>g", group = "+git" },
				{ "<leader>gv", desc = "Diffview: toggle (open/close)" },
				{ "<leader>gV", desc = "Diffview: close" },
				{ "<leader>gf", desc = "Diffview: file history (current)" },
				{ "<leader>gH", desc = "Diffview: repo history" },
				{ "<leader>gF", desc = "Diffview: toggle files panel" },
				{ "<leader>g>", desc = "Diffview: focus files panel" },
			})
		end)
	end,
}
