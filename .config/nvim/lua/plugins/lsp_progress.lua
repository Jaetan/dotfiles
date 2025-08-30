-- Lightweight LSP progress UI
return {
	{
		"j-hui/fidget.nvim",
		event = "LspAttach",
		opts = {
			progress = {
				suppress_on_insert = true,
				display = {
					render_limit = 16,
					done_ttl = 1,
				},
			},
			notification = {
				window = { winblend = 0 }, -- keep opaque; you already theme nicely
			},
		},
	},
}
