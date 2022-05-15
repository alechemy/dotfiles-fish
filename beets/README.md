# Beets Guide

## Installation

```fish
brew install aubio chromaprint imagemagick && pip3 install -r requirements.txt
```

## Usage

Beets is great. Beets is also a little finicky. So that I don't forget later on,
these are the basic commands I use to manage my library.

- Locate the config:

```fish
beet config -p
# Open it in VS Code:
code (beet config -p)
```

- Import newly downloaded music:

```fish
beet import '/Users/alec/Downloads/Nick Drake - Pink Moon/'
```

- Search for an album and get its path:

```fish
beet ls -ap "velvet underground"
```

- Remove an album from the beets db:

```fish
beet remove "/Users/alec/Music/Music/Media.localized/Music/The Velvet Underground/Peel Slowly and See"
# also delete the files:
beet remove -d "/Users/alec/Music/Music/Media.localized/Music/The Velvet Underground/Peel Slowly and See"
```

- Update the beets db (in case files were moved or deleted without beets' knowledge):

```fish
beet update
# dry run:
beet update -p
```

- Get album art from online sources:

```fish
beet fetchart -f '/Users/alec/Music/Music/Media.localized/Music/Arcade Fire and Owen Pallett/Her/'
```

- Fetch genres:

```fish
beet lastgenre '/Users/alec/Music/Music/Media.localized/Music/Arcade Fire and Owen Pallett/Her/'
```

- Modify fields:

```fish
beet modify -a "laugh now cry later" genre="Hip-Hop"
```
