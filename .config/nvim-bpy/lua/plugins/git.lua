return {
	-- gitsigns
	{
		"lewis6991/gitsigns.nvim",
		dependencies = { "nvim-lua/plenary.nvim" },
		event = { "BufReadPre", "BufNewFile" },
		opts = {
			signs = {
				add = { text = "▎" },
				change = { text = "▎" },
				delete = { text = "▎" },
				topdelete = { text = "▔" },
				changedelete = { text = "~" },
				untracked = { text = "▎" },
			},
			signcolumn = true,
			sign_priority = 10,
			attach_to_untracked = true,
			watch_gitdir = { follow_files = true },
			preview_config = { border = "rounded" },
			on_attach = function(bufnr)
				local gs = package.loaded.gitsigns
				local map = function(mode, lhs, rhs, desc)
					vim.keymap.set(mode, lhs, rhs, { buffer = bufnr, desc = desc, silent = true })
				end
				map("n", "]h", gs.next_hunk, "Next hunk")
				map("n", "[h", gs.prev_hunk, "Prev hunk")
				map({ "n", "v" }, "<leader>hs", gs.stage_hunk, "Stage hunk")
				map({ "n", "v" }, "<leader>hr", gs.reset_hunk, "Reset hunk")
				map("n", "<leader>hS", gs.stage_buffer, "Stage buffer")
				map("n", "<leader>hu", gs.undo_stage_hunk, "Undo stage")
				map("n", "<leader>hR", gs.reset_buffer, "Reset buffer")
				map("n", "<leader>hp", gs.preview_hunk, "Preview hunk")
				map("n", "<leader>hd", gs.diffthis, "Diffthis")
				map("n", "<leader>hD", function()
					gs.diffthis("~")
				end, "Diffthis (against HEAD)")
				map("n", "<leader>hb", gs.toggle_current_line_blame, "Toggle line blame")
				map("n", "<leader>hB", function()
					gs.blame_line({ full = true })
				end, "Blame (full)")
			end,
		},
	},

	-- fugitive + keymaps
	{
		"tpope/vim-fugitive",
		cmd = { "G", "Git", "Gdiffsplit", "Gvdiffsplit", "Gclog", "Gedit" },
		keys = { { "<leader>g", desc = "+git (fugitive)" } },
		config = function()
			local map = function(lhs, rhs, desc)
				vim.keymap.set("n", lhs, rhs, { desc = desc, silent = true })
			end
			map("<leader>gs", ":Git<CR>", "Git status")
			map("<leader>gc", ":Git commit<CR>", "Git commit")
			map("<leader>gP", ":Git push<CR>", "Git push")
			map("<leader>gp", ":Git pull --rebase<CR>", "Git pull --rebase")
			map("<leader>gb", ":Git blame<CR>", "Git blame (buffer)")
			map("<leader>gd", ":Gdiffsplit<CR>", "Diff split (index)")
			map("<leader>gD", ":Gvdiffsplit!<CR>", "Diff vs HEAD (vertical)")
			map("<leader>gl", ":Git log --oneline --graph<CR>", "Repo log")
			map("<leader>gL", ":0Gclog<CR>", "File log (quickfix)")
			pcall(function()
				require("which-key").add({ { "<leader>g", group = "+git (fugitive)" } })
			end)
		end,
	},
}
