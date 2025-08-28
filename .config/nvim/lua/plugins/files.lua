-- lua/plugins/files.lua
return {
	{
		"nvim-neo-tree/neo-tree.nvim",
		branch = "v3.x",
		dependencies = {
			"nvim-lua/plenary.nvim",
			"nvim-tree/nvim-web-devicons",
			"MunifTanjim/nui.nvim",
		},

		-- Create mappings *before* the plugin loads so they trigger lazy-load.
		keys = {
			{
				"<leader>ft",
				function()
					require("neo-tree.command").execute({ toggle = true, reveal = true, position = "left" })
				end,
				desc = "File tree (toggle)",
			},
			{
				"<leader>fr",
				function()
					require("neo-tree.command").execute({ action = "reveal", position = "left" })
				end,
				desc = "Reveal current file in tree",
			},
			-- NOTE: We avoid <leader>fR here to prevent conflict with Telescope resume.
			{
				"<leader>fC",
				function()
					-- force root to cwd, then reveal
					require("neo-tree.command").execute({
						action = "reveal",
						position = "left",
						reveal_force_cwd = true,
					})
				end,
				desc = "Reveal (root at CWD)",
			},
			{
				"<leader>fD",
				function()
					-- open tree rooted at the file's directory
					vim.cmd("Neotree left dir=%:p:h reveal")
				end,
				desc = "Open tree at file directory",
			},
		},

		-- Lazy-load on command as well (optional).
		cmd = { "Neotree" },

		opts = {
			close_if_last_window = true,
			enable_git_status = true,
			enable_diagnostics = true,
			popup_border_style = "rounded",
			sources = { "filesystem", "buffers", "git_status" },
			filesystem = {
				hijack_netrw_behavior = "open_default",
				follow_current_file = { enabled = true, leave_dirs_open = false },
				use_libuv_file_watcher = true,
				filtered_items = { visible = false, hide_dotfiles = false, hide_gitignored = true },
				commands = {
					chmod = function(state)
						local node = state.tree:get_node()
						if not node or not node.path then
							return
						end
						local mode = vim.fn.input("chmod mode (e.g. 644, +x, u+x): ")
						if not mode or mode == "" then
							return
						end
						local ok
						if vim.system then
							ok = (vim.system({ "chmod", mode, node.path }):wait() or {}).code == 0
						else
							ok = os.execute(string.format("chmod %q %q", mode, node.path)) == 0
						end
						if ok then
							vim.notify(("chmod %s %s"):format(mode, node.path))
							require("neo-tree.sources.filesystem.commands").refresh(state)
						else
							vim.notify("chmod failed", vim.log.levels.ERROR)
						end
					end,
				},
			},
			window = {
				width = 34,
				mappings = {
					["<CR>"] = "open",
					["o"] = "open",
					["s"] = "open_split",
					["v"] = "open_vsplit",
					["t"] = "open_tabnew",
					["a"] = "add",
					["r"] = "rename",
					["d"] = "delete",
					["c"] = "copy",
					["m"] = "move",
					["x"] = "chmod",
					["."] = "toggle_hidden",
					["R"] = "refresh",
					["q"] = "close_window",
				},
			},
			default_component_configs = {
				indent = { with_markers = true, padding = 1 },
				icon = { folder_closed = "", folder_open = "", default = "" },
				git_status = {
					symbols = {
						added = "",
						modified = "",
						deleted = "",
						renamed = "",
						untracked = "",
						ignored = "",
						unstaged = "",
						staged = "",
						conflict = "",
					},
				},
			},
		},

		config = function(_, opts)
			require("neo-tree").setup(opts)
			-- optional which-key group label
			pcall(function()
				require("which-key").add({ { "<leader>f", group = "+file/format" } })
			end)
		end,
	},
}
