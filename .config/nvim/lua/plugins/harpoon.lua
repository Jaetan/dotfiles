return {
	{
		"ThePrimeagen/harpoon",
		branch = "harpoon2",
		event = "VeryLazy",
		dependencies = { "nvim-lua/plenary.nvim" },
		opts = {
			settings = {
				save_on_toggle = true,
				sync_on_ui_close = true,
				-- keep one list per git root (fallback to cwd when not in a repo)
				key = function()
					local root = vim.fn.systemlist("git rev-parse --show-toplevel")[1]
					if root and #root > 0 then
						return root
					end
					return vim.loop.cwd()
				end,
			},
		},
		config = function(_, opts)
			require("harpoon"):setup(opts)
		end,
	},
}
