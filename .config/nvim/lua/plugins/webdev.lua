-- lua/plugins/webdev.lua
return {
	"windwp/nvim-ts-autotag",
	event = { "BufReadPost", "BufNewFile" },
	opts = {
		-- You can restrict/extend filetypes if you like:
		-- filetypes = { "html", "xml", "javascript", "typescriptreact", "javascriptreact", "svelte", "vue" },
	},
	config = function(_, opts)
		require("nvim-ts-autotag").setup(opts)
	end,
}
