# Setup fzf
# ---------
if [[ ! "$PATH" == */home/nicolas/.fzf/bin* ]]; then
  PATH="${PATH:+${PATH}:}/home/nicolas/.fzf/bin"
fi

eval "$(fzf --bash)"
