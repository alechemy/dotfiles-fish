# .dotfiles

My dotfiles for fish shell.

## Installation

### Dependencies

First, make sure you have all those things installed:

- `git`: to clone the repo
- `curl`: to download some stuff
- `tar`: to extract downloaded stuff
- `fish`: the shell
- `sudo`: some configs may need that

### Install

```console
./script/bootstrap.fish
```

> All changed files will be backed up with a `.backup` suffix.

#### Update

To update, you just need to `git pull` and run the bootstrap script again:

```console
cd ~/.dotfiles
git pull origin master
./script/bootstrap.fish
```

## Revert

Reverting is not totally automated, but it pretty much consists in removing
the fish config and dotfiles folder, as well as moving back some config files.

**Remove the folders:**

```console
rm -rf ~/.dotfiles ~/.config/fish
```

**Some config files were changed, you can find them using `fd`:**

```console
fd -e backup -e local -H -E Library -d 3 .
```

And then manually inspect/revert them.

## macOS defaults

Set with:

```console
~/.dotfiles/macos/set-defaults.sh
```

And logging out and in again or restart.
