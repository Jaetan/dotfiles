local setup_once = require("config.lsp_setup").setup_once
local py = require("util.python")

return {
	-- mason (optional install helper)
	{
		"williamboman/mason.nvim",
		cmd = { "Mason", "MasonInstall", "MasonUpdate" },
		config = true,
	},

	-- lazydev: Neovim Lua API completions for lua_ls (replaces neodev)
	{
		"folke/lazydev.nvim",
		ft = "lua",
		opts = {
			library = {
				{ path = "${3rd}/luv/library", words = { "vim%.uv" } },
			},
		},
	},

	-- configure servers (native vim.lsp.config / vim.lsp.enable)
	{
		"williamboman/mason.nvim", -- merge into mason spec to trigger on file open
		event = { "BufReadPre", "BufNewFile" },
		config = function()
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

			-- Python: Ruff LSP (linting, quick fixes, organize imports)
			setup_once("ruff", {
				filetypes = { "python" },
				single_file_support = true,
				on_attach = function(client, _)
					if client and client.server_capabilities then
						client.server_capabilities.hoverProvider = false
					end
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
				cmd = { "./tools/clangd-from-bazel.sh" },
				capabilities = { offsetEncoding = { "utf-16" } },
				single_file_support = true,
			})

			-- sanity notification for basedpyright
			vim.api.nvim_create_autocmd("LspAttach", {
				callback = function(ev)
					local clients = vim.lsp.get_clients({ id = ev.data.client_id })
					local c = clients[1]
					if c and c.name == "basedpyright" then
						local p = vim.tbl_get(c, "config", "settings", "python", "pythonPath") or "<none>"
						vim.notify("basedpyright pythonPath=" .. p)
					end
				end,
			})
		end,
	},
}
