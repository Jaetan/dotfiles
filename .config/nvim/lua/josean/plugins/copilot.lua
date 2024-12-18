return {
  "zbirenbaum/copilot-cmp",
  event = "InsertEnter",
  config = function()
    require("copilot_cmp").setup()
  end,
  dependencies = {
    "zbirenbaum/copilot.lua",
    cmd = "Copilot",
    config = function()
      require("copilot").setup({
        suggestion = { enabled = false },
        panel = { enabled = false },
        filetypes = {
          ruby = true,
          eruby = true,
          html = true,
          python = true,
          cpp = true,
        },
      })
    end,
  },
}
