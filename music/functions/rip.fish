function rip -d "download an album"
    if test (count $argv) -ne 2
        echo "Usage: rip <url> <genre>"
        echo "Example: rip 'https://play.somestreamingsite.com/album/id' 'Rock'"
        return
    end

    echo "🎵 Starting download: $argv[2] album from $argv[1]"
    # Replace with actual user+ip
    set DEST '<user>@<ip>'
    ssh $DEST "source ~/.profile && /share/CACHEDEV1_DATA/python-apps/rip-and-tag.sh '$argv[1]' '$argv[2]'"

    if test $status -eq 0
        echo "✅ Download completed successfully!"
    else
        echo "❌ Download failed!"
    end
end
