return {
	"AckslD/nvim-neoclip.lua",
	event = "VeryLazy",
	dependencies = {
		"nvim-lua/plenary.nvim",
		"nvim-telescope/telescope.nvim",
		-- Optional persistence backend:
		-- "kkharji/sqlite.lua",
	},
	opts = {
		history = 200,
		enable_persistent_history = false, -- set true if you add sqlite
		length_limit = 1048576,
		continuous_sync = true,
		filter = nil,
		default_register = '"',
		default_register_plus = "+",
		content_spec_column = false,
		on_paste = {
			set_reg = false,
		},
		keys = {
			telescope = {
				i = { select = "<cr>", paste = "<c-p>", paste_behind = "<c-k>" },
				n = { select = "<cr>", paste = "p", paste_behind = "P" },
			},
		},
	},
	config = function(_, opts)
		require("neoclip").setup(opts)

		-- Load Telescope extension
		local ok_t, telescope = pcall(require, "telescope")
		if ok_t then
			pcall(telescope.load_extension, "neoclip")
		end

		-- Picker: yank history
		vim.keymap.set("n", "<leader>fy", function()
			local ok = pcall(require("telescope").extensions.neoclip.default)
			if ok then
				require("telescope").extensions.neoclip.default()
			else
				vim.notify("neoclip telescope extension not available", vim.log.levels.WARN)
			end
		end, { desc = "Yank history (Telescope)" })

		-- which-key hint (optional)
		pcall(function()
			require("which-key").add({ { "<leader>fy", desc = "Yank history" } })
		end)
	end,
}
