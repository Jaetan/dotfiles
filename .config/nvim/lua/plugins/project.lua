-- lua/plugins/project.lua — native project root auto-cd (no plugin)
local root = require("util.root")

-- Auto-cd to project root on BufEnter
vim.api.nvim_create_autocmd("BufEnter", {
	group = vim.api.nvim_create_augroup("AutoProjectRoot", { clear = true }),
	callback = function(ev)
		-- skip special buffers
		if vim.bo[ev.buf].buftype ~= "" then
			return
		end
		local dir = root.get(ev.buf)
		local cwd = vim.uv.cwd() or "."
		if dir ~= cwd then
			vim.cmd.cd({ args = { dir } })
		end
	end,
})

-- Telescope picker: recent projects (derived from oldfiles)
vim.keymap.set("n", "<leader>fp", function()
	local seen, items = {}, {}
	for _, f in ipairs(vim.v.oldfiles or {}) do
		local dir = vim.fn.fnamemodify(f, ":p:h")
		if dir and #dir > 0 and not seen[dir] then
			local r = vim.fs.root(0, root.patterns)
			-- check if the directory itself looks like a project root
			local dr = vim.fs.root(dir, root.patterns)
			if dr and not seen[dr] then
				seen[dr] = true
				table.insert(items, dr)
			end
		end
	end

	vim.ui.select(items, { prompt = "Projects" }, function(choice)
		if choice then
			vim.cmd.cd({ args = { choice } })
			vim.notify("cd " .. choice)
		end
	end)
end, { desc = "Projects (recent)" })

-- Return empty spec — no plugin needed
return {}
