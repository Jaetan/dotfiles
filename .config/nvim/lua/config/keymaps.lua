-- Small helper for concise mappings
local function map(mode, lhs, rhs, desc)
	vim.keymap.set(mode, lhs, rhs, { desc = desc, silent = true })
end

----------------------------------------------------------------
-- UI toggles / actions
----------------------------------------------------------------

-- Toggle indent guides per-buffer
map("n", "<leader>i", function()
	local ibl = require("ibl")
	local hidden = vim.b._ibl_hidden or false
	if hidden then
		ibl.setup_buffer(0, { enabled = true })
	else
		ibl.setup_buffer(0, { enabled = false })
	end
	vim.b._ibl_hidden = not hidden
	vim.notify("Indent guides: " .. (vim.b._ibl_hidden and "OFF" or "ON"))
end, "Toggle indent guides (buffer)")

-- Diagnostics float for current line
map("n", "<leader>e", function()
	vim.diagnostic.open_float(nil, { scope = "line", border = "rounded", source = "always", focus = false })
end, "Line diagnostics (float)")

-- One-shot format
map("n", "<leader>f=", function()
	require("conform").format({ async = false, lsp_fallback = true, timeout_ms = 3000 })
end, "Format buffer (Conform → LSP fallback)")

-- Toggle format-on-save (respected by Conform setup)
vim.g._format_on_save_disabled = false
map("n", "<leader>tf", function()
	vim.g._format_on_save_disabled = not vim.g._format_on_save_disabled
	vim.notify("Format on save: " .. (vim.g._format_on_save_disabled and "OFF" or "ON"))
end, "Toggle format-on-save")

-- Toggle relative line numbers
map("n", "<leader>n", function()
	vim.wo.relativenumber = not vim.wo.relativenumber
	vim.notify("Relative numbers: " .. (vim.wo.relativenumber and "ON" or "OFF"))
end, "Toggle relative line numbers")

----------------------------------------------------------------
-- Windows / tabs / resize
----------------------------------------------------------------
map("n", "<leader>sv", ":vsplit<CR>", "Vertical split")
map("n", "<leader>sh", ":split<CR>", "Horizontal split")
map("n", "<leader>ww", "<C-w>w", "Switch window")
map("n", "<leader>wh", "<C-w>h", "Focus left")
map("n", "<leader>wj", "<C-w>j", "Focus down")
map("n", "<leader>wk", "<C-w>k", "Focus up")
map("n", "<leader>wl", "<C-w>l", "Focus right")
map("n", "<leader>wH", "<C-w>H", "Move window far left")
map("n", "<leader>wJ", "<C-w>J", "Move window to bottom")
map("n", "<leader>wK", "<C-w>K", "Move window to top")
map("n", "<leader>wL", "<C-w>L", "Move window far right")
map("n", "<leader>w=", "<C-w>=", "Equalize window sizes")
map("n", "<leader>wq", "<C-w>q", "Close window")
map("n", "<leader>wo", "<C-w>o", "Only (close others)")

-- Resize with arrows
map("n", "<C-Up>", ":resize +2<CR>", "Increase height")
map("n", "<C-Down>", ":resize -2<CR>", "Decrease height")
map("n", "<C-Left>", ":vertical resize -4<CR>", "Narrow")
map("n", "<C-Right>", ":vertical resize +4<CR>", "Widen")

-- Tabs
map("n", "<leader>tn", ":tabnew<CR>", "New tab")
map("n", "<leader>tq", ":tabclose<CR>", "Close tab")
map("n", "<leader>to", ":tabonly<CR>", "Close other tabs")
map("n", "<leader>tl", ":tabnext<CR>", "Next tab")
map("n", "<leader>th", ":tabprevious<CR>", "Prev tab")
for i = 1, 9 do
	map("n", "<leader>t" .. i, i .. "gt", "Go to tab " .. i)
end

----------------------------------------------------------------
-- Move current buffer to neighbor split (create if missing)
----------------------------------------------------------------
local function move_buf_to(dir)
	local cur_win = vim.api.nvim_get_current_win()
	local cur_buf = vim.api.nvim_get_current_buf()

	vim.cmd("wincmd " .. dir)
	local target_win = vim.api.nvim_get_current_win()

	if target_win == cur_win then
		if dir == "h" or dir == "l" then
			vim.cmd("vsplit")
		else
			vim.cmd("split")
		end
		vim.cmd("wincmd " .. dir)
		target_win = vim.api.nvim_get_current_win()
	end

	vim.api.nvim_win_set_buf(target_win, cur_buf)

	if cur_win ~= target_win then
		if vim.fn.bufexists("#") == 1 then
			vim.api.nvim_set_current_win(cur_win)
			vim.cmd("buffer #")
		else
			vim.api.nvim_set_current_win(cur_win)
			vim.cmd("enew")
		end
	end
	vim.api.nvim_set_current_win(target_win)
end

map("n", "<leader>wmh", function()
	move_buf_to("h")
end, "Move buffer to left split")
map("n", "<leader>wmj", function()
	move_buf_to("j")
end, "Move buffer to down split")
map("n", "<leader>wmk", function()
	move_buf_to("k")
end, "Move buffer to up split")
map("n", "<leader>wml", function()
	move_buf_to("l")
end, "Move buffer to right split")

----------------------------------------------------------------
-- Telescope convenience
----------------------------------------------------------------
map("n", "<leader>f,", "<cmd>Telescope resume<CR>", "Resume last Telescope")

----------------------------------------------------------------
-- Trouble / TODO / Aerial
----------------------------------------------------------------

-- Trouble command builder
local function trouble_cmd(cmd)
	return "<cmd>Trouble " .. cmd .. "<CR>"
end

-- Trouble panels
vim.keymap.set("n", "<leader>x", trouble_cmd("diagnostics toggle"), { desc = "Trouble: diagnostics (workspace)" })
vim.keymap.set(
	"n",
	"<leader>X",
	trouble_cmd("diagnostics toggle filter.buf=0"),
	{ desc = "Trouble: diagnostics (buffer)" }
)
vim.keymap.set("n", "<leader>xs", trouble_cmd("symbols toggle win.position=right"), { desc = "Trouble: symbols (doc)" })
vim.keymap.set(
	"n",
	"<leader>xr",
	trouble_cmd("lsp toggle focus=true win.position=right"),
	{ desc = "Trouble: LSP refs/defs" }
)
vim.keymap.set("n", "<leader>xq", trouble_cmd("qflist toggle"), { desc = "Trouble: quickfix list" })
vim.keymap.set("n", "<leader>xl", trouble_cmd("loclist toggle"), { desc = "Trouble: location list" })

-- TODO comments (project-rooted + buffer)
do
	local function project_root()
		if vim.system then
			local r = vim.system({ "git", "rev-parse", "--show-toplevel" }):wait()
			if r and r.code == 0 and r.stdout and #r.stdout > 0 then
				return (r.stdout:gsub("%s+$", ""))
			end
		end
		return vim.loop.cwd()
	end

	-- Run a thunk with local cwd temporarily set to root
	local function with_lcd_root(fn)
		local old = vim.fn.getcwd(-1, -1) -- current window’s local dir (if any)
		local root = project_root()
		local had_lcd = (old ~= "")

		vim.cmd("silent! lcd " .. vim.fn.fnameescape(root))
		local ok, err = pcall(fn)
		if had_lcd then
			vim.cmd("silent! lcd " .. vim.fn.fnameescape(old))
		else
			vim.cmd("silent! lcd .")
		end
		if not ok then
			vim.notify(("TODO mapping error: %s"):format(err), vim.log.levels.ERROR)
		end
	end

	-- Trouble list of TODOs
	vim.keymap.set("n", "<leader>td", function()
		with_lcd_root(function()
			pcall(require, "todo-comments")
			pcall(require, "trouble")
			if vim.fn.exists(":TodoTrouble") == 2 then
				vim.cmd.TodoTrouble()
			else
				vim.cmd.TodoQuickFix()
				vim.cmd.copen()
			end
		end)
	end, { desc = "TODOs (Trouble @ project root)" })

	-- Telescope TODOs
	vim.keymap.set("n", "<leader>tD", function()
		with_lcd_root(function()
			local ok_t, telescope = pcall(require, "telescope")
			if ok_t then
				pcall(telescope.load_extension, "todo-comments")
			end
			local ok_ext = ok_t and telescope.extensions and telescope.extensions["todo-comments"]
			if ok_ext and telescope.extensions["todo-comments"].todo then
				telescope.extensions["todo-comments"].todo({ cwd = vim.loop.cwd() })
			else
				vim.cmd.TodoTelescope()
			end
		end)
	end, { desc = "TODOs (Telescope @ project root)" })

	-- Quickfix
	vim.keymap.set("n", "<leader>tq", function()
		vim.cmd.TodoQuickFix()
		vim.cmd.copen()
	end, { desc = "TODOs (quickfix)" })
end

-- Current buffer TODOs (single window, no duplicates)
vim.keymap.set("n", "<leader>tb", function()
	local ok, tc = pcall(require, "todo-comments")
	if ok and tc and type(tc.loclist) == "function" then
		local ok_call = pcall(tc.loclist, { buffer = 0, open = true })
		if ok_call then
			return
		end
	end
	vim.cmd("TodoLocList")
end, { desc = "TODOs (this buffer)" })

-- Aerial (symbols outline)
map("n", "<leader>o", "<cmd>AerialToggle! right<CR>", "Symbols outline (Aerial)")
map("n", "<leader>O", "<cmd>AerialNavToggle<CR>", "Aerial nav window")

----------------------------------------------------------------
-- Folds (nvim-ufo): peek + open/close all
----------------------------------------------------------------
pcall(function()
	require("which-key").add({ { "<leader>z", group = "+folds" } })
end)

vim.keymap.set("n", "<leader>zp", function()
	local ok, ufo = pcall(require, "ufo")
	if ok then
		local winid = ufo.peekFoldedLinesUnderCursor()
		if winid then
			return
		end
	end
	local buf = vim.api.nvim_get_current_buf()
	for _, client in ipairs(vim.lsp.get_clients({ bufnr = buf })) do
		if client.server_capabilities and client.server_capabilities.hoverProvider then
			vim.lsp.buf.hover()
			return
		end
	end
	vim.notify("No fold preview or hover here.", vim.log.levels.INFO)
end, { desc = "Peek fold / Hover" })

vim.keymap.set("n", "<leader>zR", function()
	local ok, ufo = pcall(require, "ufo")
	if ok then
		ufo.openAllFolds()
	else
		vim.cmd("normal! zR")
	end
end, { desc = "Open all folds" })

vim.keymap.set("n", "<leader>zM", function()
	local ok, ufo = pcall(require, "ufo")
	if ok then
		ufo.closeAllFolds()
	else
		vim.cmd("normal! zM")
	end
end, { desc = "Close all folds" })

----------------------------------------------------------------
-- which-key labels
----------------------------------------------------------------
pcall(function()
	require("which-key").add({
		{ "<leader>x", group = "+trouble/diagnostics" },
		{ "<leader>t", group = "+tabs/todo" },
		{ "<leader>z", group = "+folds" },
		{ "<leader>o", desc = "Symbols outline (Aerial)" },
	})
end)

----------------------------------------------------------------------
-- Spectre: project/file/selection search & replace
----------------------------------------------------------------------
do
	local function spectre_ok()
		local ok, _ = pcall(require, "spectre")
		if not ok then
			vim.notify("nvim-spectre not available", vim.log.levels.WARN)
		end
		return ok
	end

	vim.keymap.set("n", "<leader>sr", function()
		if spectre_ok() then
			require("spectre").toggle()
		end
	end, { desc = "Spectre: search & replace (project)" })

	vim.keymap.set("n", "<leader>ss", function()
		if spectre_ok() then
			require("spectre").open_file_search()
		end
	end, { desc = "Spectre: search & replace (this file)" })

	vim.keymap.set("v", "<leader>sw", function()
		if spectre_ok() then
			require("spectre").open_visual({ select_word = false })
		end
	end, { desc = "Spectre: replace selection" })
	vim.keymap.set("n", "<leader>sw", function()
		if spectre_ok() then
			require("spectre").open_visual({ select_word = true })
		end
	end, { desc = "Spectre: replace word under cursor" })
end

----------------------------------------------------------------------
-- Harpoon 2: quick file marks & jump
----------------------------------------------------------------------
do
	local function H()
		return require("harpoon")
	end
	local function L()
		return H():list()
	end

	vim.keymap.set("n", "<leader>ma", function()
		L():add()
		vim.notify("Harpoon: added file")
	end, { desc = "Harpoon: add file" })

	vim.keymap.set("n", "<leader>mm", function()
		H().ui:toggle_quick_menu(L())
	end, { desc = "Harpoon: menu" })

	vim.keymap.set("n", "<leader>mn", function()
		L():next()
	end, { desc = "Harpoon: next" })
	vim.keymap.set("n", "<leader>mp", function()
		L():prev()
	end, { desc = "Harpoon: prev" })

	for i = 1, 4 do
		vim.keymap.set("n", "<leader>m" .. i, function()
			L():select(i)
		end, { desc = ("Harpoon: go to %d"):format(i) })
	end
end

-- which-key labels
pcall(function()
	require("which-key").add({
		{ "<leader>s", group = "+search/replace" },
		{ "<leader>m", group = "+harpoon" },
	})
end)

----------------------------------------------------------------------
-- Sessions (persistence.nvim) – quick toggles anywhere
----------------------------------------------------------------------
do
	local function P()
		local ok, p = pcall(require, "persistence")
		return ok and p or nil
	end

	vim.keymap.set("n", "<leader>uS", function()
		local p = P()
		vim.g._session_disabled = not vim.g._session_disabled
		if p and p.stop and vim.g._session_disabled then
			p.stop()
		end
		if p and p.start and not vim.g._session_disabled then
			p.start()
		end
		vim.notify("Session saving: " .. (vim.g._session_disabled and "OFF" or "ON"))
	end, { desc = "Session: toggle save on exit" })

	vim.keymap.set("n", "<leader>uR", function()
		local p = P()
		if p and p.load then
			vim.g._session_disabled = false
			p.load({ last = true })
		else
			vim.notify("persistence.nvim not available", vim.log.levels.WARN)
		end
	end, { desc = "Session: restore last" })

	vim.keymap.set("n", "<leader>uL", function()
		local p = P()
		if p and p.load then
			vim.g._session_disabled = false
			p.load()
		else
			vim.notify("persistence.nvim not available", vim.log.levels.WARN)
		end
	end, { desc = "Session: restore for CWD" })

	pcall(function()
		require("which-key").add({ { "<leader>u", group = "+utils/session" } })
	end)
end

-- Project-rooted :Grep {pattern} -> fills quickfix (uses rg via grepprg)
vim.api.nvim_create_user_command("Grep", function(opts)
	local pattern = opts.args ~= "" and opts.args or vim.fn.input("Grep pattern: ")
	if pattern == "" then
		return
	end

	local function project_root()
		if vim.system then
			local r = vim.system({ "git", "rev-parse", "--show-toplevel" }):wait()
			if r and r.code == 0 and r.stdout and #r.stdout > 0 then
				return (r.stdout:gsub("%s+$", ""))
			end
		end
		return vim.loop.cwd()
	end

	local root = project_root()
	local save = vim.loop.cwd()
	vim.cmd("lcd " .. vim.fn.fnameescape(root))
	vim.cmd("silent grep " .. vim.fn.shellescape(pattern))
	vim.cmd("lcd " .. vim.fn.fnameescape(save))
end, { nargs = "*", complete = "file" })

-- Quick mapping to prompt & run :Grep (rooted)
vim.keymap.set("n", "<leader>/", function()
	vim.cmd("Grep")
end, { desc = "Project grep → quickfix" })

----------------------------------------------------------------------
-- OCaml / Dune helpers (lightweight task runner)  -------------------
----------------------------------------------------------------------
do
	local function project_root()
		if vim.system then
			local r = vim.system({ "git", "rev-parse", "--show-toplevel" }):wait()
			if r and r.code == 0 and r.stdout and #r.stdout > 0 then
				return (r.stdout:gsub("%s+$", ""))
			end
		end
		return vim.loop.cwd()
	end

	local function open_term(cmd_argv, title)
		local root = project_root()
		vim.cmd("botright 15split")
		local buf = vim.api.nvim_create_buf(true, false)
		vim.api.nvim_win_set_buf(0, buf)

		-- mark as terminal buffer; wipe on hide
		vim.bo[buf].buftype = "terminal"
		vim.bo[buf].bufhidden = "wipe"

		vim.fn.termopen(cmd_argv, { cwd = root })
		vim.cmd("startinsert")
		-- quick close
		vim.keymap.set("n", "q", "<cmd>close<CR>", { buffer = buf, nowait = true, silent = true })
		if title and title ~= "" then
			vim.b[buf].term_title = title
		end
	end

	-- Dune commands
	vim.keymap.set("n", "<leader>db", function()
		open_term({ "dune", "build" }, "dune build")
	end, { desc = "Dune: build" })

	vim.keymap.set("n", "<leader>dt", function()
		open_term({ "dune", "runtest" }, "dune runtest")
	end, { desc = "Dune: runtest" })

	vim.keymap.set("n", "<leader>dc", function()
		open_term({ "dune", "clean" }, "dune clean")
	end, { desc = "Dune: clean" })

	vim.keymap.set("n", "<leader>de", function()
		local default = vim.g._dune_last_exec or ""
		local exe = vim.fn.input("dune exec -- ", default)
		if exe == nil or exe == "" then
			return
		end
		vim.g._dune_last_exec = exe
		open_term({ "dune", "exec", "--", exe }, "dune exec -- " .. exe)
	end, { desc = "Dune: exec <program>" })

	vim.keymap.set("n", "<leader>dE", function()
		local exe = vim.g._dune_last_exec
		if not exe or exe == "" then
			vim.notify("No previous dune exec command.", vim.log.levels.WARN)
			return
		end
		open_term({ "dune", "exec", "--", exe }, "dune exec -- " .. exe .. " (repeat)")
	end, { desc = "Dune: exec last" })

	-- which-key group
	pcall(function()
		require("which-key").add({
			{ "<leader>d", group = "+dune" },
			{ "<leader>db", desc = "build" },
			{ "<leader>dt", desc = "runtest" },
			{ "<leader>dc", desc = "clean" },
			{ "<leader>de", desc = "exec <program>" },
			{ "<leader>dE", desc = "exec last" },
		})
	end)

	-- OCaml: alternate file (.ml <-> .mli)
	vim.api.nvim_create_user_command("OCamlAlternate", function()
		local ext = vim.fn.expand("%:e") -- ml | mli | something else
		if ext ~= "ml" and ext ~= "mli" then
			vim.notify("Not an OCaml source/interface file.", vim.log.levels.WARN)
			return
		end
		local cur = vim.fn.expand("%:p")
		local target = (ext == "ml") and (cur:gsub("%.ml$", ".mli")) or (cur:gsub("%.mli$", ".ml"))
		if vim.loop.fs_stat(target) then
			vim.cmd.edit(vim.fn.fnameescape(target))
		else
			vim.notify("Alternate not found: " .. target, vim.log.levels.INFO)
		end
	end, {})
end
