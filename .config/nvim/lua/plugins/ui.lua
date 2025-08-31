return {
	"goolord/alpha-nvim",
	dependencies = { "nvim-tree/nvim-web-devicons" },
	event = "VimEnter",
	opts = function()
		local dashboard = require("alpha.themes.dashboard")

		--------------------------------------------------------------------
		-- Header (compact but fancy)
		--------------------------------------------------------------------
		dashboard.section.header.val = {
			"███╗   ██╗███████╗ ██████╗ ██╗   ██╗██╗███╗   ███╗",
			"████╗  ██║██╔════╝██╔═══██╗██║   ██║██║████╗ ████║",
			"██╔██╗ ██║█████╗  ██║   ██║██║   ██║██║██╔████╔██║",
			"██║╚██╗██║██╔══╝  ██║   ██║╚██╗ ██╔╝██║██║╚██╔╝██║",
			"██║ ╚████║███████╗╚██████╔╝ ╚████╔╝ ██║██║ ╚═╝ ██║",
			"╚═╝  ╚═══╝╚══════╝ ╚═════╝   ╚═══╝  ╚═╝╚═╝     ╚═╝",
		}

		--------------------------------------------------------------------
		-- Buttons row (keep minimal; session toggles redraw footer)
		--------------------------------------------------------------------
		local btn = dashboard.button
		dashboard.section.buttons.val = {
			btn("e", "  New file", ":ene | startinsert<CR>"),
			btn("f", "  Find files", ":Telescope find_files<CR>"),
			btn("g", "  Live grep", ":Telescope live_grep<CR>"),
			btn("t", "  File tree", ":Neotree toggle reveal_force_cwd<CR>"),
			btn(
				"s",
				"  Restore Session",
				":lua vim.g._session_disabled=false; require('persistence').load(); local ok,a=pcall(require,'alpha'); if ok then a.redraw() end<CR>"
			),
			btn(
				"S",
				"  Restore Last Session",
				":lua vim.g._session_disabled=false; require('persistence').load({ last = true }); local ok,a=pcall(require,'alpha'); if ok then a.redraw() end<CR>"
			),
			btn(
				"E",
				"  Toggle Session Saving",
				":lua (function() local ok,p=pcall(require,'persistence'); if vim.g._session_disabled then vim.g._session_disabled=false; if ok and p.start then p.start() end else vim.g._session_disabled=true; if ok and p.stop then p.stop() end end; local ok2,a=pcall(require,'alpha'); if ok2 then a.redraw() end end)()<CR>"
			),
			btn(
				"D",
				"  Don't Save This Session",
				":lua vim.g._session_disabled=true; require('persistence').stop(); local ok,a=pcall(require,'alpha'); if ok then a.redraw() end<CR>"
			),
			btn("q", "  Quit", ":qa<CR>"),
		}

		--------------------------------------------------------------------
		-- Helpers
		--------------------------------------------------------------------
		local function dispw(s)
			return vim.fn.strdisplaywidth(s)
		end
		local function trunc_left(s, w)
			if dispw(s) <= w then
				return s
			end
			return "…" .. s:sub(-(w - 1))
		end
		local function short_path(p, w)
			p = vim.fn.fnamemodify(p, ":~")
			p = vim.fn.pathshorten(p)
			return trunc_left(p, w)
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

		local function recent_projects(max_items)
			max_items = max_items or 8
			local seen, items = {}, {}
			for _, f in ipairs(vim.v.oldfiles or {}) do
				local dir = vim.fn.fnamemodify(f, ":p:h")
				if dir and #dir > 0 and not seen[dir] then
					if vim.loop.fs_stat(dir .. "/.git") then
						seen[dir] = true
						table.insert(items, dir)
						if #items >= max_items then
							break
						end
					end
				end
			end
			return items
		end

		local function mru_files(max_items, only_project)
			max_items = max_items or 8
			local files, seen = {}, {}
			local root = project_root()
			for _, f in ipairs(vim.v.oldfiles or {}) do
				if type(f) == "string" and #f > 0 and vim.loop.fs_stat(f) and not seen[f] then
					if (not only_project) or f:sub(1, #root) == root then
						table.insert(files, f)
						seen[f] = true
						if #files >= max_items then
							break
						end
					end
				end
			end
			return files
		end

		--------------------------------------------------------------------
		-- Three fixed-width columns with aligned separators
		--------------------------------------------------------------------
		local cols = vim.o.columns or 120
		local gap = 6
		local colw = math.floor((cols - gap * 2) / 3)
		if colw < 40 then
			colw = 40
		end

		local SEP = " | "
		local sep_w = vim.fn.strdisplaywidth(SEP)
		local right_w = math.max(22, math.floor(colw * 0.45))
		local left_w = colw - sep_w - right_w

		local function rpad_disp(s, w)
			local d = dispw(s)
			if d >= w then
				return s
			end
			return s .. string.rep(" ", w - d)
		end
		local function trunc_right_disp(s, w)
			if dispw(s) <= w then
				return rpad_disp(s, w)
			end
			local keep = math.max(1, w - 1)
			local cut = vim.fn.strcharpart(s, 0, keep)
			return rpad_disp(cut .. "…", w)
		end
		local function trunc_left_disp(s, w)
			if dispw(s) <= w then
				return rpad_disp(s, w)
			end
			local keep = math.max(1, w - 1)
			local chars = vim.fn.strchars(s)
			local start = math.max(0, chars - keep)
			local cut = vim.fn.strcharpart(s, start, keep)
			return rpad_disp("…" .. cut, w)
		end
		local function fit_two(left, right)
			left = trunc_right_disp(left, left_w)
			right = trunc_left_disp(right, right_w)
			return left .. SEP .. right
		end

		local projs = recent_projects(8)
		local mru_proj = mru_files(8, true)
		local mru_all = mru_files(8, false)
		local rows = math.min(math.max(#projs, #mru_proj, #mru_all), 8)

		local lines = {}
		local key_actions = {} -- key -> fn

		local function fmt_proj(i, dir)
			local k = tostring(i)
			local name = vim.fn.fnamemodify(dir, ":t")
			local left = ("[%s] %s"):format(k, name)
			local right = short_path(dir, right_w)
			key_actions[k] = function()
				local ok, tb = pcall(require, "telescope.builtin")
				if ok then
					tb.find_files({ cwd = dir, hidden = true, follow = true })
				else
					vim.cmd(("edit %s"):format(vim.fn.fnameescape(dir)))
				end
			end
			return fit_two(left, right)
		end

		local function fmt_mru_lower(i, file)
			local key = string.char(string.byte("a") + i - 1) -- a..h
			local base = vim.fn.fnamemodify(file, ":t")
			local left = ("[%s] %s"):format(key, base)
			local right = short_path(vim.fn.fnamemodify(file, ":p:h"), right_w)
			key_actions[key] = function()
				vim.cmd(("edit %s"):format(vim.fn.fnameescape(file)))
			end
			return fit_two(left, right)
		end

		local function fmt_mru_upper(i, file)
			local key = string.char(string.byte("A") + i - 1) -- A..H
			local base = vim.fn.fnamemodify(file, ":t")
			local left = ("[%s] %s"):format(key, base)
			local right = short_path(vim.fn.fnamemodify(file, ":p:h"), right_w)
			key_actions[key] = function()
				vim.cmd(("edit %s"):format(vim.fn.fnameescape(file)))
			end
			return fit_two(left, right)
		end

		-- Titles row
		do
			local t1 = rpad_disp("  Projects", colw)
			local t2 = rpad_disp("  MRU (proj)", colw)
			local t3 = rpad_disp("  MRU (all)", colw)
			table.insert(lines, {
				type = "text",
				val = t1 .. string.rep(" ", gap) .. t2 .. string.rep(" ", gap) .. t3,
				opts = { position = "center", hl = "SpecialComment" },
			})
		end

		for i = 1, rows do
			local c1 = projs[i] and fmt_proj(i, projs[i]) or string.rep(" ", colw)
			local c2 = mru_proj[i] and fmt_mru_lower(i, mru_proj[i]) or string.rep(" ", colw)
			local c3 = mru_all[i] and fmt_mru_upper(i, mru_all[i]) or string.rep(" ", colw)
			local line = c1 .. string.rep(" ", gap) .. c2 .. string.rep(" ", gap) .. c3
			table.insert(lines, { type = "text", val = line, opts = { position = "center" } })
		end

		local columns_group = { type = "group", val = lines, opts = { spacing = 0 } }

		--------------------------------------------------------------------
		-- Footer: venv + session + last session time
		--------------------------------------------------------------------
		local function last_session_time()
			local dir
			do
				local ok, persistence = pcall(require, "persistence")
				dir = (
					ok
					and persistence
					and (
						(persistence.options and persistence.options.dir)
						or (persistence.config and persistence.config.dir)
					)
				) or (vim.fn.stdpath("state") .. "/sessions")
			end
			local h = vim.loop.fs_scandir(dir)
			if not h then
				return nil
			end

			local newest = 0 ---@type integer
			while true do
				local name, typ = vim.loop.fs_scandir_next(h)
				if not name then
					break
				end
				if typ == "file" or typ == nil then
					local st = vim.loop.fs_stat(dir .. "/" .. name)
					if st and st.mtime then
						-- Narrow st.mtime → integer
						local mt = st.mtime ---@type integer|{ sec: integer, nsec: integer }
						if type(mt) == "table" then
							---@cast mt { sec: integer, nsec: integer }
							mt = mt.sec
						end
						---@cast mt integer
						if mt > newest then
							newest = mt
						end
					end
				end
			end
			if newest == 0 then
				return nil
			end
			---@cast newest integer
			return os.date("%Y-%m-%d %H:%M", newest)
		end

		dashboard.section.footer.val = function()
			local venv = vim.env.VIRTUAL_ENV or "none"
			local sess = vim.g._session_disabled and "OFF" or "ON"
			local last = last_session_time()
			local last_str = last and (" • last: " .. last) or ""
			return ("venv: %s  •  session: %s%s"):format(venv, sess, last_str)
		end

		--------------------------------------------------------------------
		-- Layout
		--------------------------------------------------------------------
		dashboard.config.layout = {
			{ type = "padding", val = 1 },
			dashboard.section.header,
			{ type = "padding", val = 1 },
			dashboard.section.buttons,
			{ type = "padding", val = 1 },
			columns_group,
			{ type = "padding", val = 1 },
			dashboard.section.footer,
		}

		dashboard.opts.opts.noautocmd = true

		-- Export actions so config() can see them
		_G._alpha_key_actions = key_actions

		return dashboard.opts
	end,

	config = function(_, opts)
		-- Register keybinding autocmds BEFORE setup so we don't miss AlphaReady
		local function bind_alpha_keys(buf)
			local acts = rawget(_G, "_alpha_key_actions") or {}
			if vim.b[buf]._alpha_keys_wired then
				return
			end
			vim.b[buf]._alpha_keys_wired = true

			-- Use explicit comma prefix to avoid <localleader> expansion issues
			for k, fn in pairs(acts) do
				vim.keymap.set(
					"n",
					"," .. k,
					vim.schedule_wrap(fn),
					{ buffer = buf, nowait = true, silent = true, noremap = true, desc = "Alpha: ," .. k }
				)
			end
		end

		vim.api.nvim_create_autocmd("User", {
			pattern = "AlphaReady",
			callback = function(ev)
				local buf = ev.buf or vim.api.nvim_get_current_buf()
				bind_alpha_keys(buf)
			end,
		})

		vim.api.nvim_create_autocmd("FileType", {
			pattern = "alpha",
			callback = function(ev)
				bind_alpha_keys(ev.buf)
			end,
		})

		-- Now start Alpha
		require("alpha").setup(opts)
	end,
}
