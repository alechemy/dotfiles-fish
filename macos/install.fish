#!/usr/bin/env fish

fish_add_path /opt/homebrew/bin || true

# Create symlink from Chromium bookmarks to Chrome, primarily so that Alfred can see it
mv ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks.bak
ln -s ~/Library/Application\ Support/Chromium/Default/Bookmarks ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks
