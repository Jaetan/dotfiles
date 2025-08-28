-- lua/plugins/session.lua
return {
	"folke/persistence.nvim",
	main = "persistence",
	event = "VeryLazy", -- was BufReadPre; load early so Alpha buttons work
	opts = { options = { "buffers", "curdir", "tabpages", "winsize" } },
	keys = {
		{
			"<leader>qe",
			function()
				vim.g._session_disabled = false
				vim.notify("Session saving: ON")
			end,
			desc = "Session: enable saving",
		},
		{
			"<leader>qs",
			function()
				vim.cmd("SessionRestore")
			end,
			desc = "Session: restore",
		},
		{
			"<leader>ql",
			function()
				vim.cmd("SessionLast")
			end,
			desc = "Session: last",
		},
		{
			"<leader>qd",
			function()
				vim.cmd("SessionStop")
			end,
			desc = "Session: don't save",
		},
	},
	config = function(_, opts)
		local persistence = require("persistence")
		persistence.setup(opts)

		-- flags for footer
		vim.g._session_disabled = vim.g._session_disabled or false
		vim.g._last_session_saved = vim.g._last_session_saved or nil

		-- Wrapper commands (still useful for leader keys)
		vim.api.nvim_create_user_command("SessionRestore", function()
			vim.g._session_disabled = false
			persistence.load()
		end, {})

		vim.api.nvim_create_user_command("SessionLast", function()
			vim.g._session_disabled = false
			persistence.load({ last = true })
		end, {})

		vim.api.nvim_create_user_command("SessionStop", function()
			vim.g._session_disabled = true
			persistence.stop()
		end, {})

		vim.api.nvim_create_autocmd("VimLeavePre", {
			callback = function()
				if not vim.g._session_disabled then
					vim.g._last_session_saved = os.date("%Y-%m-%d %H:%M")
				end
			end,
		})
	end,
}
