-- lua/plugins/project.lua
return {
	"ahmedkhalf/project.nvim",
	event = "VeryLazy",
	opts = {
		-- Don’t hijack your workflow; just pick smart roots quietly.
		-- You can still :cd anywhere; Telescope pickers already use your project root helper too.
		manual_mode = false,
		detection_methods = { "lsp", "pattern" }, -- prefer LSP root, fall back to patterns
		patterns = {
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
			"vite.config.*",
			"next.config.*",
			-- C/C++
			"CMakeLists.txt",
			"conanfile.py",
			"compile_commands.json",
			"meson.build",
			"Makefile",
			-- Rust / Go / PHP, etc.
			"Cargo.toml",
			"go.mod",
			"composer.json",
			-- Docs / misc
			".hg",
			".bzr",
			".svn",
			"_darcs",
		},
		ignore_lsp = {}, -- don’t ignore any servers
		exclude_dirs = { -- skip noisy/global dirs
			"~/.local/*",
			"~/.cache/*",
			"~/.config/*",
			"/tmp/*",
		},
		show_hidden = false, -- keep Telescope clean; you already have hidden in find_files
		silent_chdir = true, -- don’t spam messages when cd happens
		scope_chdir = "global", -- project root applies globally (you use tabs rarely for projects)
		datapath = vim.fn.stdpath("data"),
	},
	config = function(_, opts)
		require("project_nvim").setup(opts)

		-- Telescope integration
		local ok_t, telescope = pcall(require, "telescope")
		if ok_t then
			pcall(telescope.load_extension, "projects")
			-- Quick picker: recent projects (respects the roots above)
			vim.keymap.set("n", "<leader>fp", function()
				telescope.extensions.projects.projects({})
			end, { desc = "Projects (Telescope)" })
		end

		-- which-key label (non-fatal)
		pcall(function()
			require("which-key").add({
				{ "<leader>f", group = "+files/search" },
				{ "<leader>fp", desc = "Projects (Telescope)" },
			})
		end)
	end,
}
