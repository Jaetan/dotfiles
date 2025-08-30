return {
	"echasnovski/mini.bufremove",
	version = "*",
	-- Lazy-load on first use of these keys
	keys = {
		{
			"<leader>bd",
			function()
				require("mini.bufremove").delete(0, false)
			end,
			desc = "Buffer: delete (keep window)",
		},
		{
			"<leader>bD",
			function()
				require("mini.bufremove").delete(0, true)
			end,
			desc = "Buffer: FORCE delete",
		},
		{
			"<leader>bw",
			function()
				require("mini.bufremove").wipeout(0, false)
			end,
			desc = "Buffer: wipeout",
		},
		{
			"<leader>bo",
			function()
				local cur = vim.api.nvim_get_current_buf()
				local B = require("mini.bufremove")
				local closed, skipped = 0, 0
				for _, b in ipairs(vim.api.nvim_list_bufs()) do
					if b ~= cur and vim.bo[b].buflisted then
						local ok = pcall(B.delete, b, false)
						if ok then
							closed = closed + 1
						else
							skipped = skipped + 1
						end
					end
				end
				vim.notify(
					("Closed %d buffer(s)%s"):format(closed, skipped > 0 and ("; skipped " .. skipped) or ""),
					vim.log.levels.INFO
				)
			end,
			desc = "Buffer: close others",
		},
	},
	config = function(_, _)
		-- Defaults are fine; functions are available as:
		--   require('mini.bufremove').delete(buf, force?)
		--   require('mini.bufremove').wipeout(buf, force?)
		pcall(function()
			require("which-key").add({ { "<leader>b", group = "+buffers" } })
		end)
	end,
}
