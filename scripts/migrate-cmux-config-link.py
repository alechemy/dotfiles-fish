#!/usr/bin/env python3

from pathlib import Path
import sys


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: migrate-cmux-config-link.py DOTFILES")
    dotfiles = Path(sys.argv[1]).resolve()
    config = Path.home() / ".config/ghostty/config"
    if not config.is_symlink():
        return
    target = config.resolve(strict=False)
    retired = (dotfiles / "stow/ghostty/.config/ghostty/config").resolve(strict=False)
    current = (dotfiles / "stow/cmux/.config/ghostty/config").resolve(strict=False)
    if target == retired:
        config.unlink()
        return
    if target != current:
        raise SystemExit(f"Refusing to replace unrelated Ghostty config link: {config} -> {target}")


if __name__ == "__main__":
    main()
