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

- Import while also setting known fields:

  ```fish
  # (see addendum below for a more complete example on how to import soundtracks)
  beet import '/Users/alec/Downloads/Euphoria/' --set genre="Soundtrack" --set album="Euphoria (Music from the HBO Original Series)"
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

## Addendum: Soundtrack Playlists

Here's how to import "soundtracks" (i.e., playlists containing a bunch of songs by different artists, usually associated with a TV series or film).

1. After downloading, assign track and disc numbers using Mp3tag.app.
2. Import the directory with `beet import`, taking care to specify `soundtrack-config.yaml`, which disables the Lastgenre auto-fetcher:

   ```fish
   beet -c "/Users/alec/.dotfiles/beets/soundtrack-config.yaml" import '/Users/alec/Downloads/To all the boys I’ve loved before/' \
    --quiet \
    --set album="To All the Boys I've Loved Before (Music from the Motion Picture)" \
    --set comp="True" \
    --set genre="Soundtrack" \
    --set year="2018"
   ```

3. Embed the artwork if necessary:

   ```fish
   beet embedart -f '/Users/alec/Music/Music/Media.localized/Music/Compilations/Euphoria (Music from the HBO Original Series)/cover.jpg' "euphoria"
   ```

   - After running `embedart`, will likely need to run the "Restore Artwork From Album Folder" script in Music.app to refresh the art.

4. Set BPM:

   ```fish
   beet bpmanalyser
   ```

## Addendum: YouTube Tracks

Example: "The Hillbillies" by Kendrick Lamar & Baby Keem 🤠

Prerequisite: `brew install youtube-dl atomicparsley`

1. Download the track

   ```fish
   yt-dlp -f bestaudio[ext=m4a] --embed-thumbnail --add-metadata 'https://www.youtube.com/watch?v=Yhivl6fln3s' -o '~/Downloads/%(title)s.%(ext)s'
   ```

2. Import to beets

   ```fish
   beet import --singletons '/Users/alec/Downloads/Baby Keem & Kendrick Lamar - The Hillbillies.m4a\'
   ```

   - When prompted, choose "**U**se as-is"

3. Fix up the metadata

   ```fish
   beet edit --all "hillbillies"
   beet bpmanalyser
   ```

4. Download and retrieve an image

   - Tip: Can use `&tbs=iar:s` query param on a Google Images search to narrow to square images.
   - Download the image, name it `cover.jpg`, and move it to the same folder as the song.
   - Then, embed it: `beet embedart -f <image-path> <song-path>`

## Addendum: Moving Library to Another Location

1. Copy the Music library file and its folder structure to the new location.

   - e.g., Copy `~/Music/Music/Music\ Library.musiclibrary` to `/Volumes/MyNewNAS/Music`

2. Create the Music directory in the new location.

   - e.g., `mkdir -p /Volumes/MyNewNAS/Music/Media.localized/Music`

3. Update the `beets` config `directory` value to point to the directory from step 2.

   - e.g. `directory: /Volumes/MyNewNAS/Music/Media.localized/Music`

4. Run `beet move` (do a dry run first: `beet move -p`).

5. Launch Music.app in the new location. Create a test library and disable app settings related to organizing files (see `gui-preferences.md` in this repo). Quit Music.app.

6. Launch Music.app in the new location. **Hold down the option key while launching.**. Select the moved `Music Library.musiclibrary` file.

7. Update `library` in `beets` config.

   - e.g., `library: /Volumes/MyNewNAS/Music/Beets/musiclibrary.db`
