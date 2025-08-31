return {
	"stevearc/conform.nvim",
	event = { "BufReadPre", "BufNewFile" },
	opts = {
		formatters_by_ft = {
			python = { "isort", "black" },
			lua = { "stylua" },
			javascript = { "prettier" },
			typescript = { "prettier" },
			javascriptreact = { "prettier" },
			typescriptreact = { "prettier" },
			css = { "prettier" },
			scss = { "prettier" },
			less = { "prettier" },
			html = { "prettier" },
			json = { "prettier" },
			yaml = { "prettier" },
			markdown = { "prettier" },
			c = { "clang_format" },
			cpp = { "clang_format" },
			ocaml = { "ocamlformat" }, -- ← add this
		},
	},
	config = function(_, opts)
		local conform = require("conform")
		conform.setup(opts)

		conform.formatters.prettier = { prepend_args = {}, prefer_local = "node_modules/.bin" }

		local allow = {
			python = true,
			lua = true,
			html = true,
			css = true,
			scss = true,
			less = true,
			json = true,
			yaml = true,
			markdown = true,
			javascript = true,
			typescript = true,
			c = true,
			cpp = true,
			ocaml = true,
		}

		conform.setup({
			format_on_save = function(bufnr)
				if vim.g._format_on_save_disabled then
					return
				end
				local ft = vim.bo[bufnr].filetype
				if not allow[ft] then
					return
				end
				return { lsp_fallback = true, timeout_ms = 2000 }
			end,
		})
	end,
}
