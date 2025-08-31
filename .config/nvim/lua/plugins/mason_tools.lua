return {
	"WhoIsSethDaniel/mason-tool-installer.nvim",
	dependencies = { "williamboman/mason.nvim" },
	event = "VeryLazy",
	opts = {
		ensure_installed = {
			"ocamlformat", -- formatter used by Conform for OCaml
		},
		auto_update = false,
		run_on_start = true,
		start_delay = 200, -- ms
		debounce_hours = 24,
	},
	config = function(_, opts)
		require("mason-tool-installer").setup(opts)
	end,
}
