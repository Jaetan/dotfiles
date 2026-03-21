-- util/root.lua — project root detection using vim.fs.root() (0.11+)
local M = {}

M.patterns = {
	".git",
	-- Python
	"pyproject.toml",
	"poetry.lock",
	"Pipfile",
	"requirements.txt",
	"setup.cfg",
	"setup.py",
	".python-version",
	".venv",
	-- JS/TS
	"package.json",
	"tsconfig.json",
	-- C/C++
	"CMakeLists.txt",
	"compile_commands.json",
	"Makefile",
	-- Rust / Go
	"Cargo.toml",
	"go.mod",
	-- VCS
	".hg",
	".bzr",
	".svn",
}

--- Return the project root for the given buffer (default: current),
--- falling back to cwd.
---@param bufnr? integer
---@return string
function M.get(bufnr)
	local root = vim.fs.root(bufnr or 0, M.patterns)
	return root or vim.uv.cwd() or "."
end

return M
