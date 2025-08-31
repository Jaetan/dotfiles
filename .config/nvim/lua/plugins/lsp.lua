local setup_once = require("config.lsp_setup").setup_once
local py = require("util.python")

return {
	-- base LSP plugin
	{ "neovim/nvim-lspconfig" },

	-- mason (optional install helper)
	{ "williamboman/mason.nvim", config = true },
	{ "williamboman/mason-lspconfig.nvim" },

	-- configure servers
	{
		"neovim/nvim-lspconfig",
		event = { "BufReadPre", "BufNewFile" },
		config = function()
			------------------------------------------------------------------
			-- Neodev: enrich lua_ls with Neovim runtime & plugin typings
			------------------------------------------------------------------
			local ok_nd, neodev = pcall(require, "neodev")
			if ok_nd then
				neodev.setup({
					library = { plugins = true, types = true },
					pathStrict = true,
				})
			end

			-- Lua
			setup_once("lua_ls", {
				settings = {
					Lua = {
						runtime = { version = "LuaJIT" },
						diagnostics = { globals = { "vim" } },
						workspace = { checkThirdParty = false },
						telemetry = { enable = false },
					},
				},
			})

			-- Python: BasedPyright
			setup_once("basedpyright", {
				before_init = function(_, config)
					config.settings = config.settings or {}
					config.settings.python = config.settings.python or {}
					config.settings.python.pythonPath = py.current_venv_python()
				end,
				settings = {
					basedpyright = {
						analysis = {
							autoSearchPaths = true,
							diagnosticMode = "workspace",
							typeCheckingMode = "standard",
						},
						loggingLevel = "information",
					},
					python = { pythonPath = py.current_venv_python() },
				},
				on_new_config = function(new_config)
					new_config.settings = new_config.settings or {}
					new_config.settings.python = new_config.settings.python or {}
					new_config.settings.python.pythonPath = py.current_venv_python()
				end,
			})

			-- HTML
			setup_once("html", {
				filetypes = { "html" },
				single_file_support = true,
				settings = {
					html = {
						format = { wrapLineLength = 120, wrapAttributes = "force-aligned" },
						hover = { documentation = true, references = true },
					},
				},
			})

			-- CSS / SCSS / Less
			setup_once("cssls", {
				filetypes = { "css", "scss", "less" },
				single_file_support = true,
				settings = {
					css = { validate = true },
					scss = { validate = true },
					less = { validate = true },
				},
			})

			-- Emmet language server
			setup_once("emmet_language_server", {
				filetypes = { "html", "css", "scss", "less" },
				single_file_support = true,
				init_options = {
					preferences = {},
					syntaxProfiles = {},
					snippetVariables = {},
					showExpandedAbbreviation = "always",
					showAbbreviationSuggestions = true,
					showSuggestionsAsSnippets = true,
				},
			})

			-- C/C++: clangd
			setup_once("clangd", {
				cmd = {
					"clangd",
					"--background-index",
					"--clang-tidy",
					"--completion-style=detailed",
					"--header-insertion=iwyu",
				},
				capabilities = { offsetEncoding = { "utf-16" } },
				single_file_support = true,
			})

			-- tiny sanity UI for basedpyright
			vim.api.nvim_create_autocmd("LspAttach", {
				callback = function(ev)
					local c = vim.lsp.get_client_by_id(ev.data.client_id)
					if c and c.name == "basedpyright" then
						-- avoid undefined-field warning by using a safe lookup
						local p = vim.tbl_get(c, "config", "settings", "python", "pythonPath") or "<none>"
						vim.notify("basedpyright pythonPath=" .. p)
					end
				end,
			})
		end,
	},
}
