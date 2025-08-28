return {
	-- Emmet (classic vim plugin, <C-y>,)
	{
		"mattn/emmet-vim",
		ft = { "html", "css", "scss", "less" },
		init = function()
			vim.g.user_emmet_install_global = 0
			vim.g.user_emmet_mode = "a"
		end,
		config = function()
			vim.api.nvim_create_autocmd("FileType", {
				pattern = { "html", "css", "scss", "less" },
				callback = function()
					vim.cmd("EmmetInstall")
				end,
			})
		end,
	},

	-- Comment.nvim (gc/gcc/gb)
	{
		"numToStr/Comment.nvim",
		event = { "BufReadPre", "BufNewFile" },
		opts = { mappings = { basic = true, extra = true } },
		config = function(_, opts)
			require("Comment").setup(opts)
			pcall(function()
				require("which-key").add({ { "gc", group = "+comment" }, { "gb", group = "+block comment" } })
			end)
		end,
	},

	-- Autopairs (with refined quotes) + toggle
	{
		"windwp/nvim-autopairs",
		event = "InsertEnter",
		dependencies = { "nvim-treesitter/nvim-treesitter", "hrsh7th/nvim-cmp" },
		config = function()
			local npairs = require("nvim-autopairs")
			npairs.setup({
				check_ts = true,
				enable_check_bracket_line = true,
				ignored_next_char = "[%w%.]",
				fast_wrap = { map = "<M-e>", chars = { "{", "[", "(", '"', "'" }, end_key = "$" },
			})

			-- cmp integration: add () after function/method confirm, with type-safe shim
			local ok_cmp, cmp = pcall(require, "cmp")
			if ok_cmp then
				---@type CmpModule
				local cmpM = cmp
				local ok_pair, cmp_autopairs = pcall(require, "nvim-autopairs.completion.cmp")
				if ok_pair and cmpM and cmpM.event then
					cmpM.event:on("confirm_done", cmp_autopairs.on_confirm_done())
				end
			end

			-- refined quote rules (snippet-85)
			local Rule = require("nvim-autopairs.rule")
			local function neighbors(opts)
				local line, col = opts.line, opts.col
				local prev = col > 1 and line:sub(col - 1, col - 1) or ""
				local next = line:sub(col, col)
				return prev, next
			end
			npairs.remove_rule('"')
			npairs.add_rule(Rule('"', '"')
				:with_pair(function(opts)
					local prev, next = neighbors(opts)
					if next == '"' then
						return false
					end
					if prev:match("%w") then
						return false
					end
					if next:match("%w") then
						return false
					end
					return true
				end)
				:with_move(function(opts)
					local _, n = neighbors(opts)
					return n == '"'
				end)
				:use_key('"'))
			npairs.remove_rule("'")
			npairs.add_rule(Rule("'", "'")
				:with_pair(function(opts)
					local prev, next = neighbors(opts)
					if next == "'" then
						return false
					end
					if prev:match("%w") then
						return false
					end
					if next:match("%w") then
						return false
					end
					return true
				end)
				:with_move(function(opts)
					local _, n = neighbors(opts)
					return n == "'"
				end)
				:use_key("'"))

			-- toggle
			vim.g._autopairs_disabled = false
			vim.keymap.set("n", "<leader>ap", function()
				if vim.g._autopairs_disabled then
					npairs.enable()
					vim.g._autopairs_disabled = false
					vim.notify("Autopairs: ON")
				else
					npairs.disable()
					vim.g._autopairs_disabled = true
					vim.notify("Autopairs: OFF")
				end
			end, { desc = "Toggle autopairs" })
		end,
	},
}
