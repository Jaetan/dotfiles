# Fish syntax-highlighting theme: Catppuccin Mocha (dark).
# Values taken from fish's bundled theme: /usr/share/fish/themes/catppuccin-mocha.theme ([dark]).
# Set as --global from conf.d (loaded at startup) so the theme is committable in the dotfiles
# repo. `fish_variables` (universal scope) is gitignored, and `fish_config theme save` refuses
# to overwrite these globals, so this file is the source of truth. To re-pick via the GUI,
# delete this file and run `fish_config`.

set --global fish_color_normal cdd6f4
set --global fish_color_command 89b4fa
set --global fish_color_keyword cba6f7
set --global fish_color_param f2cdcd
set --global fish_color_quote a6e3a1
set --global fish_color_redirection f5c2e7
set --global fish_color_end fab387
set --global fish_color_comment 7f849c
set --global fish_color_error f38ba8
set --global fish_color_gray 6c7086
set --global fish_color_selection --background=313244
set --global fish_color_search_match --background=313244
set --global fish_color_option a6e3a1
set --global fish_color_operator f5c2e7
set --global fish_color_escape eba0ac
set --global fish_color_autosuggestion 6c7086
set --global fish_color_cancel f38ba8
set --global fish_color_cwd f9e2af
set --global fish_color_cwd_root f38ba8
set --global fish_color_user 94e2d5
set --global fish_color_host 89b4fa
set --global fish_color_host_remote a6e3a1
set --global fish_color_status f38ba8
set --global fish_color_history_current --bold
set --global fish_color_valid_path --underline
set --global fish_pager_color_progress 6c7086
set --global fish_pager_color_prefix f5c2e7
set --global fish_pager_color_completion cdd6f4
set --global fish_pager_color_description 6c7086
set --global fish_pager_color_selected_background -r
