return {
	"echasnovski/mini.bufremove",
	version = false,
	keys = {
		{
			"<leader>bd",
			function()
				require("mini.bufremove").delete(0, false)
			end,
			desc = "Delete buffer",
		},
		{
			"<leader>bD",
			function()
				require("mini.bufremove").delete(0, true)
			end,
			desc = "Force delete buffer",
		},
	},
	config = function(_, _)
		pcall(function()
			require("which-key").add({ { "<leader>b", group = "+buffers" } })
		end)
	end,
}
