return {
	"goolord/alpha-nvim",
	dependencies = { "nvim-tree/nvim-web-devicons" },
	event = "VimEnter",
	opts = function()
		local alpha = require("alpha")
		local dashboard = require("alpha.themes.dashboard")

		-- Clean, compact header (6 lines)
		dashboard.section.header.val = {
			"███╗   ██╗███████╗ ██████╗ ██╗   ██╗██╗███╗   ███╗",
			"████╗  ██║██╔════╝██╔═══██╗██║   ██║██║████╗ ████║",
			"██╔██╗ ██║█████╗  ██║   ██║██║   ██║██║██╔████╔██║",
			"██║╚██╗██║██╔══╝  ██║   ██║╚██╗ ██╔╝██║██║╚██╔╝██║",
			"██║ ╚████║███████╗╚██████╔╝ ╚████╔╝ ██║██║ ╚═╝ ██║",
			"╚═╝  ╚═══╝╚══════╝ ╚═════╝   ╚═══╝  ╚═╝╚═╝     ╚═╝",
		}

		-- Buttons (minimal)
		local btn = dashboard.button
		dashboard.section.buttons.val = {
			btn("e", "  New file", ":ene | startinsert<CR>"),
			btn("f", "  Find files", ":Telescope find_files<CR>"),
			btn("g", "  Live grep", ":Telescope live_grep<CR>"),
			btn("t", "  File tree", ":Neotree toggle reveal_force_cwd<CR>"),

			-- Sessions (redraw Alpha so footer updates immediately)
			btn(
				"s",
				"  Restore Session",
				":lua vim.g._session_disabled=false; require('persistence').load(); "
					.. "local ok,a=pcall(require,'alpha'); if ok then a.redraw() end<CR>"
			),
			btn(
				"S",
				"  Restore Last Session",
				":lua vim.g._session_disabled=false; require('persistence').load({ last = true }); "
					.. "local ok,a=pcall(require,'alpha'); if ok then a.redraw() end<CR>"
			),
			btn(
				"E",
				"  Toggle Session Saving",
				":lua (function() "
					.. "local ok,p=pcall(require,'persistence'); "
					.. "if vim.g._session_disabled then "
					.. "  vim.g._session_disabled=false; if ok and p.start then p.start() end; "
					.. "else "
					.. "  vim.g._session_disabled=true; if ok and p.stop then p.stop() end; "
					.. "end; "
					.. "local ok2,a=pcall(require,'alpha'); if ok2 then a.redraw() end "
					.. "end)()<CR>"
			),
			btn(
				"D",
				"  Don't Save This Session",
				":lua vim.g._session_disabled=true; require('persistence').stop(); "
					.. "local ok,a=pcall(require,'alpha'); if ok then a.redraw() end<CR>"
			),

			btn("q", "  Quit", ":qa<CR>"),
		}

		-- Footer: venv + session + last-saved (from persistence’s session dir)
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
			local newest = 0
			while true do
				local name, typ = vim.loop.fs_scandir_next(h)
				if not name then
					break
				end
				if typ == "file" or typ == nil then
					local st = vim.loop.fs_stat(dir .. "/" .. name)
					if st and st.mtime then
						local mt = (type(st.mtime) == "table") and st.mtime.sec or st.mtime
						if mt and mt > newest then
							newest = mt
						end
					end
				end
			end
			if newest == 0 then
				return nil
			end
			return os.date("%Y-%m-%d %H:%M", newest)
		end

		dashboard.section.footer.val = function()
			local venv = vim.env.VIRTUAL_ENV or "none"
			local sess = vim.g._session_disabled and "OFF" or "ON"
			local last = last_session_time()
			local last_str = last and (" • last: " .. last) or ""
			return ("venv: %s  •  session: %s%s"):format(venv, sess, last_str)
		end

		-- Compact layout: header → buttons → footer (no MRU/projects)
		dashboard.config.layout = {
			{ type = "padding", val = 1 },
			dashboard.section.header,
			{ type = "padding", val = 1 },
			dashboard.section.buttons,
			{ type = "padding", val = 1 },
			dashboard.section.footer,
		}

		dashboard.opts.opts.noautocmd = true
		return dashboard.opts
	end,
	config = function(_, opts)
		require("alpha").setup(opts)
	end,
}
