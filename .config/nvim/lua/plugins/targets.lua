return {
	"wellle/targets.vim",
	event = { "BufReadPost", "BufNewFile" },
	init = function()
		-- Make “next target” seeking a touch more helpful across lines/sentences.
		-- Safe default; remove if you prefer vanilla behavior.
		vim.g.targets_seekRanges = "cc cC c fF"
	end,
}
