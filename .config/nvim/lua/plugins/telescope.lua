return {
	"nvim-telescope/telescope.nvim",
	version = false,
	dependencies = { "nvim-lua/plenary.nvim" },
	cmd = "Telescope",
	opts = function()
		local function project_root()
			if vim.system then
				local res = vim.system({ "git", "rev-parse", "--show-toplevel" }):wait()
				if res and res.code == 0 and res.stdout and #res.stdout > 0 then
					return (res.stdout:gsub("%s+$", ""))
				end
			else
				local ok, pipe = pcall(io.popen, "git rev-parse --show-toplevel 2>/dev/null")
				if ok and pipe then
					local out = pipe:read("*a") or ""
					pipe:close()
					out = out:gsub("%s+$", "")
					if #out > 0 then
						return out
					end
				end
			end
			return vim.loop.cwd()
		end

		local vimgrep = { "rg", "--vimgrep", "--no-heading", "--smart-case", "--hidden", "--glob", "!.git" }

		return {
			defaults = {
				sorting_strategy = "ascending",
				layout_config = { prompt_position = "top" },
				mappings = {
					i = { ["<C-j>"] = "move_selection_next", ["<C-k>"] = "move_selection_previous" },
					n = { ["j"] = "move_selection_next", ["k"] = "move_selection_previous" },
				},
				path_display = { "smart" },
				vimgrep_arguments = vimgrep,
				file_ignore_patterns = { "%.git/", "node_modules/", "dist/", "build/" },
			},
			pickers = {
				find_files = { hidden = true, follow = true },
				live_grep = { only_sort_text = true },
				buffers = {
					sort_lastused = true,
					mappings = { i = { ["<C-d>"] = "delete_buffer" }, n = { ["dd"] = "delete_buffer" } },
				},
				oldfiles = { cwd_only = false },
				lsp_definitions = { show_line = false },
				lsp_references = { include_declaration = false, show_line = false },
				lsp_implementations = { show_line = false },
				lsp_type_definitions = { show_line = false },
			},
			-- Make sure fzf is configured here so it overrides Telescope sorters
			extensions = {
				fzf = {
					fuzzy = true, -- true: fuzzy match; false: exact
					override_generic_sorter = true,
					override_file_sorter = true,
					case_mode = "smart_case", -- or "ignore_case" | "respect_case"
				},
			},
			_helpers = { project_root = project_root },
		}
	end,
	config = function(_, opts)
		local ok_tel, telescope = pcall(require, "telescope")
		if not ok_tel then
			return
		end
		telescope.setup(opts)

		-- Ensure the extension is actually loaded here (safe even if also loaded elsewhere)
		pcall(telescope.load_extension, "fzf")

		-- Pickers / keymaps
		local tb = require("telescope.builtin")
		local root = (opts and opts._helpers and opts._helpers.project_root) or vim.loop.cwd

		-- Files
		vim.keymap.set("n", "<leader>ff", function()
			local cwd = root()
			local ok = pcall(tb.git_files, { show_untracked = true, cwd = cwd })
			if not ok then
				tb.find_files({ cwd = cwd, hidden = true })
			end
		end, { desc = "Find files (project-aware)" })
		vim.keymap.set("n", "<leader>f.", function()
			tb.find_files({ cwd = vim.fn.expand("%:p:h"), hidden = true })
		end, { desc = "Find files (this dir)" })
		vim.keymap.set("n", "<leader>fg", function()
			tb.live_grep({ cwd = root() })
		end, { desc = "Live grep (project)" })
		vim.keymap.set("n", "<leader>fs", function()
			tb.grep_string({ cwd = root(), word_match = "-w" })
		end, { desc = "Grep word under cursor" })
		vim.keymap.set("n", "<leader>f/", tb.current_buffer_fuzzy_find, { desc = "Search in current buffer" })
		vim.keymap.set("n", "<leader>fb", tb.buffers, { desc = "Buffers" })
		vim.keymap.set("n", "<leader>fo", tb.oldfiles, { desc = "Recent files" })
		vim.keymap.set("n", "<leader>fh", tb.help_tags, { desc = "Help tags" })
		vim.keymap.set("n", "<leader>fk", tb.keymaps, { desc = "Keymaps" })
		vim.keymap.set("n", "<leader>fc", tb.commands, { desc = "Commands" })
		vim.keymap.set("n", "<leader>fd", function()
			tb.diagnostics({ bufnr = nil })
		end, { desc = "Diagnostics (workspace)" })
		vim.keymap.set("n", "<leader>fR", tb.resume, { desc = "Resume last Telescope" })

		-- LSP “goto” via Telescope with fallbacks
		local function _has_lsp(buf)
			return next(vim.lsp.get_clients({ bufnr = buf })) ~= nil
		end
		local function _has_telescope()
			return pcall(require, "telescope.builtin")
		end
		local function _map_g(lhs, tele_fn, lsp_fn, fallback, desc)
			vim.keymap.set("n", lhs, function()
				local ok, builtins = _has_telescope()
				if _has_lsp(0) and ok and builtins[tele_fn] then
					builtins[tele_fn]()
				elseif _has_lsp(0) and lsp_fn then
					lsp_fn()
				else
					vim.cmd.normal({ args = { fallback }, bang = true })
				end
			end, { silent = true, desc = desc })
		end
		_map_g("gd", "lsp_definitions", vim.lsp.buf.definition, "gd", "Goto definition (Telescope)")
		_map_g("gD", "lsp_definitions", vim.lsp.buf.declaration, "gD", "Goto declaration (Telescope)")
		_map_g("gi", "lsp_implementations", vim.lsp.buf.implementation, "gi", "Goto implementation (Telescope)")
		_map_g("gt", "lsp_type_definitions", vim.lsp.buf.type_definition, "gt", "Goto type definition (Telescope)")
		_map_g("gr", "lsp_references", function()
			vim.lsp.buf.references({ includeDeclaration = false })
		end, "gr", "Goto references (Telescope)")

		-- which-key group label for g stays in plugins/whichkey.lua
	end,
}
