-- config/lsp_setup.lua — shared LSP helpers (0.11+ native API)
local M = {}
local configured = {}

function M.setup_once(name, opts)
	if configured[name] then
		return
	end
	configured[name] = true
	opts = opts or {}

	local capabilities = vim.lsp.protocol.make_client_capabilities()
	local ok, cmp_lsp = pcall(require, "cmp_nvim_lsp")
	if ok and cmp_lsp then
		capabilities = cmp_lsp.default_capabilities(capabilities)
	end
	opts.capabilities = vim.tbl_deep_extend("force", opts.capabilities or {}, capabilities)

	vim.lsp.config(name, opts)
	vim.lsp.enable(name)
end

return M
