return {
	"echasnovski/mini.surround",
	version = false,
	event = "VeryLazy",
	opts = function()
		-- Helper: check if a mapping already exists in any of n/x/o modes
		local function is_taken(lhs)
			return vim.fn.maparg(lhs, "n") ~= "" or vim.fn.maparg(lhs, "x") ~= "" or vim.fn.maparg(lhs, "o") ~= ""
		end
		local function pick(lhs)
			if is_taken(lhs) then
				vim.schedule(function()
					vim.notify(
						("mini.surround: '%s' already mapped; leaving it unbound here."):format(lhs),
						vim.log.levels.INFO
					)
				end)
				return "" -- disable in Mini.surround (won’t override)
			end
			return lhs
		end

		-- Your preferred scheme under `gs…`; we only bind those that are free.
		local map_add = pick("gsa") -- add surrounding (operator-pending)
		local map_del = pick("gsd") -- delete surrounding
		local map_rep = pick("gsr") -- replace surrounding
		local map_find = pick("gsf") -- find right
		local map_find_left = pick("gsF") -- find left
		local map_highlight = pick("gsh") -- highlight surrounding
		local map_update = pick("gsn") -- update n_lines

		return {
			mappings = {
				add = map_add,
				delete = map_del,
				replace = map_rep,
				find = map_find,
				find_left = map_find_left,
				highlight = map_highlight,
				update_n_lines = map_update,
				-- keep default suffixes (used by find-next/last) by not overriding them
			},
		}
	end,
	config = function(_, opts)
		require("mini.surround").setup(opts)

		-- which-key group only (don’t spam individual entries)
		pcall(function()
			require("which-key").add({
				{ "gs", group = "+surround", mode = { "n", "v" } },
			})
		end)
	end,
}
