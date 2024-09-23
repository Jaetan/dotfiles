# My dotfiles

This directory contains the dotfiles for my system.

## Requirements

Ensure you have the following installed on your system:

### Git

```
$ sudo apt-get install git
```

### Stow

```
$ sudo apt-get install stow
```

## Installation

First, check that the ssh-agent has your github identity loaded, and load it otherwise.

```
$ ps -ef | grep ssh-agent | grep -v grep
$ eval "$(ssh-agent -s)         # if the process isn't running"
$ ssh-add -l
$ ssh-add ~/.ssh/id_ed25519     # if the identity isn't there
```

Then, check out the dotfiles repo in your `$HOME` directory using git:

```
$ git clone git@github.com/Jaetan/dotfiles.git
$ cd dotfiles
```

Then, use stow to create symlinks:

```
$ stow .
```

Stow will report errors if there already are files (not links) with the same names in your `$HOME` directory.
More information can be found at [Dreams of Autonomy](https://www.youtube.com/watch?v=y6XCebnB9gs).
