#!/usr/bin/env fish

set KITTY_HOME $HOME/.config/kitty/

mkdir -p $KITTY_HOME
mkdir -p $KITTY_HOME/themes

# Set dark icon
curl -sL https://raw.githubusercontent.com/DinkDonk/kitty-icon/main/kitty-dark.icns -o $KITTY_HOME/kitty.app.icns

# Download kittens (plugins)
curl -sL https://raw.githubusercontent.com/trygveaa/kitty-kitten-search/master/search.py -o $KITTY_HOME/search.py
curl -sL https://raw.githubusercontent.com/trygveaa/kitty-kitten-search/master/scroll_mark.py -o $KITTY_HOME/scroll_mark.py

# Download catppuccin themes
curl -sL https://raw.githubusercontent.com/catppuccin/kitty/main/mocha.conf -o $KITTY_HOME/themes/frappe.conf
curl -sL https://raw.githubusercontent.com/catppuccin/kitty/main/mocha.conf -o $KITTY_HOME/themes/latte.conf
curl -sL https://raw.githubusercontent.com/catppuccin/kitty/main/mocha.conf -o $KITTY_HOME/themes/macchiato.conf
curl -sL https://raw.githubusercontent.com/catppuccin/kitty/main/mocha.conf -o $KITTY_HOME/themes/mocha.conf

# Set theme to Catppuccin-Macchiato
kitty +kitten themes --reload-in=all Catppuccin-Macchiato
