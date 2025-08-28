return {
	"nvim-lualine/lualine.nvim",
	dependencies = { "nvim-tree/nvim-web-devicons" },
	event = "VeryLazy",
	opts = function()
		local function lsp_names()
			local buf = vim.api.nvim_get_current_buf()
			local clients = vim.lsp.get_clients({ bufnr = buf }) or {}
			if vim.tbl_isempty(clients) then
				return ""
			end
			local uniq, names = {}, {}
			for _, c in pairs(clients) do
				if c and c.name then
					uniq[c.name] = true
				end
			end
			for name in pairs(uniq) do
				table.insert(names, name)
			end
			table.sort(names)
			return "  " .. table.concat(names, ",")
		end
		return {
			options = {
				theme = "tokyonight",
				icons_enabled = true,
				globalstatus = true,
				component_separators = { left = "", right = "" },
				section_separators = { left = "", right = "" },
			},
			sections = {
				lualine_a = { "mode" },
				lualine_b = {
					{ "branch", icon = "" },
					{ "diff", symbols = { added = " ", modified = " ", removed = " " } },
					{
						"diagnostics",
						sources = { "nvim_diagnostic" },
						symbols = { error = " ", warn = " ", info = " ", hint = "󰠠 " },
						colored = true,
					},
				},
				lualine_c = { { "filename", path = 1, symbols = { modified = " ", readonly = " " } } },
				lualine_x = {
					lsp_names,
					"encoding",
					{ "fileformat", symbols = { unix = "LF", dos = "CRLF", mac = "CR" } },
					"filetype",
				},
				lualine_y = { "progress" },
				lualine_z = { "location" },
			},
			inactive_sections = {
				lualine_a = {},
				lualine_b = {},
				lualine_c = { "filename" },
				lualine_x = { "location" },
				lualine_y = {},
				lualine_z = {},
			},
			extensions = { "quickfix", "man", "fugitive" },
		}
	end,
}
