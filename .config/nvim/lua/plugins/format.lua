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
			ocaml = { "ocamlformat" }, -- OCaml support
		},
	},
	config = function(_, opts)
		local conform = require("conform")
		conform.setup(opts)

		-- Prefer project-local prettier if available
		conform.formatters.prettier = { prepend_args = {}, prefer_local = "node_modules/.bin" }

		-- Make ocamlformat work outside detected projects too
		conform.formatters.ocamlformat = {
			prepend_args = { "--enable-outside-detected-project" },
		}

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

		-- honor the toggle
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
