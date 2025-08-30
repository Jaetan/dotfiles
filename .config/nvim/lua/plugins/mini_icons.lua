return {
	"echasnovski/mini.icons",
	version = false,
	lazy = true,
	opts = function()
		-- Minimal setup; we keep nvim-web-devicons installed as-is.
		-- mini.icons will register its icon set without mocking devicons.
		return {}
	end,
	config = function(_, opts)
		require("mini.icons").setup(opts)
	end,
}
