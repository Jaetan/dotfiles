return {
	"ThePrimeagen/harpoon",
	branch = "harpoon2",
	dependencies = { "nvim-lua/plenary.nvim" },
	opts = {},
	config = function(_, _)
		local harpoon = require("harpoon")
		harpoon:setup()

		vim.keymap.set("n", "<leader>ma", function()
			harpoon:list():add()
		end, { desc = "Harpoon: Add file" })
		vim.keymap.set("n", "<leader>mm", function()
			harpoon.ui:toggle_quick_menu(harpoon:list())
		end, { desc = "Harpoon: Menu" })
		for i = 1, 4 do
			vim.keymap.set("n", "<leader>m" .. i, function()
				harpoon:list():select(i)
			end, { desc = "Harpoon: Go to " .. i })
		end

		pcall(function()
			require("which-key").add({ { "<leader>m", group = "+marks (harpoon)" } })
		end)
	end,
}
