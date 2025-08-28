return {
	"sindrets/diffview.nvim",
	dependencies = { "nvim-lua/plenary.nvim" },
	cmd = { "DiffviewOpen", "DiffviewClose", "DiffviewFileHistory" },
	keys = {
		{ "<leader>vd", "<cmd>DiffviewOpen<CR>", desc = "Diffview: Open" },
		{ "<leader>vq", "<cmd>DiffviewClose<CR>", desc = "Diffview: Close" },
		{ "<leader>vh", "<cmd>DiffviewFileHistory %<CR>", desc = "Diffview: File history" },
		{ "<leader>vH", "<cmd>DiffviewFileHistory<CR>", desc = "Diffview: Repo history" },
	},
	opts = { enhanced_diff_hl = true },
	config = function(_, opts)
		require("diffview").setup(opts)
		pcall(function()
			require("which-key").add({ { "<leader>v", group = "+diffview" } })
		end)
	end,
}
