-- util/python.lua — deterministic Python path from current venv / project
local M = {}

function M.current_venv_python()
	local v = vim.env.VIRTUAL_ENV
	if v and #v > 0 then
		local p = v .. "/bin/python"
		if vim.uv.fs_stat(p) then
			return p
		end
		local p2 = v .. "/Scripts/python.exe"
		if vim.uv.fs_stat(p2) then
			return p2
		end
	end
	local cwd = vim.uv.cwd()
	for _, p in ipairs({ cwd .. "/.venv/bin/python", cwd .. "/.venv/Scripts/python.exe" }) do
		if vim.uv.fs_stat(p) then
			return p
		end
	end
	return (vim.fn.exepath("python") ~= "" and vim.fn.exepath("python")) or "python"
end

return M
