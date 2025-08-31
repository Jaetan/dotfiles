return {
	"kevinhwang91/nvim-ufo",
	event = "VeryLazy",
	dependencies = { "kevinhwang91/promise-async" },

	--- Ufo setup
	opts = function()
		-- Treesitter availability check (per-buffer)
		local function has_ts(bufnr)
			local ok, parsers = pcall(require, "nvim-treesitter.parsers")
			if not ok then
				return false
			end
			local lang = parsers.get_buf_lang(bufnr)
			if not lang or lang == "" then
				return false
			end
			return parsers.has_parser(lang)
		end

		-- Compact fold text: keep original prefix, add a “N lines” badge.
		local function fold_text(virt_text, lnum, end_lnum, width, truncate)
			local new = {}
			local suffix = (" 󰁂 %d "):format(end_lnum - lnum)
			local sufw = vim.fn.strdisplaywidth(suffix)
			local target = width - sufw
			local curw = 0

			for _, chunk in ipairs(virt_text) do
				local txt, hl = chunk[1], chunk[2]
				local w = vim.fn.strdisplaywidth(txt)
				if target > curw + w then
					table.insert(new, chunk)
					curw = curw + w
				else
					local cut = truncate(txt, target - curw)
					table.insert(new, { cut, hl })
					break
				end
			end
			table.insert(new, { suffix, "Folded" })
			return new
		end

		return {
			open_fold_hl_timeout = 0,
			-- IMPORTANT: return only { main, fallback }
			-- (take just the bufnr to avoid unused-parameter warnings)
			provider_selector = function(bufnr)
				if has_ts(bufnr) then
					return { "treesitter", "indent" }
				else
					return { "lsp", "indent" }
				end
			end,
			fold_virt_text_handler = fold_text,
			enable_get_fold_virt_text = true,
			preview = {
				win_config = { border = "rounded", winblend = 0 },
			},
		}
	end,

	config = function(_, opts)
		local ok, ufo = pcall(require, "ufo")
		if not ok then
			return
		end
		ufo.setup(opts)

		-- Keymaps (keep it minimal; native z* remain intact)
		local map = function(lhs, rhs, desc)
			vim.keymap.set("n", lhs, rhs, { silent = true, desc = desc })
		end
		map("zR", ufo.openAllFolds, "Open all folds")
		map("zM", ufo.closeAllFolds, "Close all folds")

		-- NOTE: We intentionally do NOT bind `K` here to avoid clashing with
		-- language-specific hovers (e.g. OCaml type-on-hover). Use <leader>zp from keymaps.lua.
	end,
}
