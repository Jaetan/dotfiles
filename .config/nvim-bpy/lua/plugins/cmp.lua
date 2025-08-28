return {
	"hrsh7th/nvim-cmp",
	event = "InsertEnter",
	dependencies = {
		"L3MON4D3/LuaSnip",
		"saadparwaiz1/cmp_luasnip",
		"hrsh7th/cmp-nvim-lsp",
		"hrsh7th/cmp-buffer",
		"hrsh7th/cmp-path",
		{ "rafamadriz/friendly-snippets", lazy = true },
	},
	config = function()
		local cmp = require("cmp")
		local luasnip = require("luasnip")

		require("luasnip.loaders.from_vscode").lazy_load()
		luasnip.config.set_config({
			history = true,
			updateevents = "TextChanged,TextChangedI",
			enable_autosnippets = false,
		})

		cmp.setup({
			snippet = {
				expand = function(args)
					luasnip.lsp_expand(args.body)
				end,
			},
			window = { completion = cmp.config.window.bordered(), documentation = cmp.config.window.bordered() },
			mapping = cmp.mapping.preset.insert({
				["<CR>"] = cmp.mapping.confirm({ select = true }),
				["<C-Space>"] = cmp.mapping.complete(),
				["<C-e>"] = cmp.mapping.abort(),
				["<C-f>"] = cmp.mapping.scroll_docs(4),
				["<C-d>"] = cmp.mapping.scroll_docs(-4),
				["<Tab>"] = function(fallback)
					if cmp.visible() then
						cmp.select_next_item()
					elseif luasnip.expand_or_jumpable() then
						luasnip.expand_or_jump()
					else
						fallback()
					end
				end,
				["<S-Tab>"] = function(fallback)
					if cmp.visible() then
						cmp.select_prev_item()
					elseif luasnip.jumpable(-1) then
						luasnip.jump(-1)
					else
						fallback()
					end
				end,
			}),
			sources = cmp.config.sources(
				{ { name = "nvim_lsp" }, { name = "luasnip" } },
				{ { name = "path" }, { name = "buffer" } }
			),
			formatting = {
				fields = { "abbr", "kind", "menu" },
				format = function(entry, vim_item)
					local menus = { nvim_lsp = "[LSP]", luasnip = "[Snip]", buffer = "[Buf]", path = "[Path]" }
					vim_item.menu = menus[entry.source.name] or ""
					return vim_item
				end,
			},
			completion = { completeopt = "menu,menuone,noselect" },
			experimental = { ghost_text = false },
		})

		-- optional snippet jumps
		vim.keymap.set({ "i", "s" }, "<C-j>", function()
			if luasnip.jumpable(1) then
				luasnip.jump(1)
			end
		end, { desc = "LuaSnip jump next" })
		vim.keymap.set({ "i", "s" }, "<C-k>", function()
			if luasnip.jumpable(-1) then
				luasnip.jump(-1)
			end
		end, { desc = "LuaSnip jump prev" })
	end,
}
