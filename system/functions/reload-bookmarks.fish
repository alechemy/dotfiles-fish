function reload-bookmarks -d "sync bookmarks for alfred"
    mv ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks.bak
    ln -s ~/Library/Application\ Support/Chromium/Default/Bookmarks ~/Library/Application\ Support/Google/Chrome/Default/Bookmarks
end
