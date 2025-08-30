return {
	"lambdalisue/suda.vim",
	event = "VeryLazy",
	init = function()
		-- Enable smart edit: transparently read/write root-only files via sudo
		vim.g.suda_smart_edit = 1
	end,
	config = function()
		-- Keep your muscle memory: `:w!!` writes with sudo
		vim.cmd([[
      cnoreabbrev <expr> w!! (getcmdtype() == ':' && getcmdline() == 'w!!') ? 'SudaWrite' : 'w!!'
    ]])

		-- Optional: explicit command if you prefer
		vim.api.nvim_create_user_command("Wsudo", function()
			vim.cmd("SudaWrite")
		end, { desc = "Write buffer with sudo" })
	end,
}
