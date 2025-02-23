# Mac setup notes

These are some of the things I like to change on a new computer.

Probably some of it can be automated, but I can't be bothered.

## App Store

- Preferences
  - Disable "Video Autoplay"
  - Disable "In-App Ratings & Reviews"

## Finder

- Advanced
  - When performing a search:
    - Select "Search the current folder"
    - Disable "Show warning before removing from iCloud Drive"

## Safari

- General
  - Disable "Open 'safe' files after downloading"
- Search
  - Disable "Include Safari Suggestions"
  - Disable "Preload Top Hit in the background" [(Here's why.)](https://lapcatsoftware.com/articles/preload-top-hit.html)
- Privacy
  - Advanced
    - Disable "Allow privacy-preserving measurement of ad effectiveness"
- Websites
  - Scroll down to Notifications. Disable "Allow websites to ask for permission to send notifications"
- Advanced
  - Beside "Smart Search Field", enable "Show full website address"
  - Enable "Show features for web developers"
  - Disable "Allow Highlights to share web addresses with Apple"
- Develop
  - Allow JavaScript from Apple Events

## Chrome / Chromium

- Develop
  - Allow JavaScript from Apple Events
- DevTools
  - Network
    - Enable "Disable cache"
- Flags
  - Navigate to `chrome://flags`
  - Set these:
    - `#show-avatar-button` (Never)
    - `#keep-old-history` (Enabled)
    - `#disable-top-sites` (Enabled)
    - `#no-default-browser-check` (Enabled)

## Transmit

- Transfers
  - "Uploading folders:" → Choose "Merge the existing folder"

## System Settings

### Appearance

- De-select "Allow wallpaper tinting in windows"

### AirDrop & Handoff

- De-select "Allow Handoff between this Mac and your iCloud devices"

### Desktop & Dock

- Enable 'Automatically hide and show the Dock'
- Desktop & Stage Manager
  - "Click wallpaper to reveal desktop" - set to "Only in Stage Manager"
- Mission Control
  - Disable "Drag windows to top of screen to enter Mission Control"
- Click "Hot Corners...". De-select "Quick Note".

### Spotlight

- Search Results
  - De-select "Siri Suggestions"
- Disable "Help Apple Improve Search"

### Notifications & Focus

- Tips
  - Disable "Allow notifications"
- Focus
  - Disable "Share Focus Status"

### Internet Accounts

- Remove "Game Center" if present

### Security & Privacy

- General
  - Change "Require password x minutes after sleep..." option to "immediately".
- Privacy
  - Apple Advertising
    - De-select "Personalized Ads"
  - Analytics & Improvement
    - De-select everything

### Accessibility

- Display
  - Enable "Show window title icons" (AKA proxy icons)
- Pointer Control
  - Click "Trackpad Options". Enable "Enable dragging with three finger drag".

### Software Update

- Select "Automatically keep my Mac up to date"
- Click "Advanced". Select all options.

### Sound

- Sound Effects
  - Uncheck "Play sound on startup"
  - Uncheck "Play user interface sound effects"
  - Set "Show Sound in menu bar" to "Always"
- Input
  - Increase "Input Volume" to max value

### Keyboard

- Keyboard
  - "Key Repeat": Fastest
  - "Delay Until Repeat": Shortest
- Text
  - Input Sources -> Edit...
    - De-select "Capitalize words automatically"
    - De-select "Add period with double-space"
  - Delete the default "omw" -> "On my way!" text replacement
- Dictation
  - Disable "Shortcut"

### Trackpad

- Point & Click
  - Enable "Tap to click"
  - Increase tracking speed by 1
- Scroll & Zoom
  - Disable "Scroll direction: Natural"

## Mail.app

- Junk Mail
  - Enable "Enable junk mail filtering"
  - Under "When junk mail arrives:", select "Move it to the Junk mailbox"
- Viewing
  - Enable "Show most recent messages at the top"

## Messages.app

- General
  - Disable "Notify me about messages from unknown contacts"
  - Disable "Play sound effects"
- iMessage
  - Enable "Enable Messages in iCloud"

## Music.app

- General
  - Disable everything here ("Sync Library", "Automatic Downloads", "Always check...", "Show: ...", "Notifications")
- Files
  -Disable "Keep Music Media folder organized"

## Misc

- Disable iOS backups by changing `~/Library/Application Support/MobileSync/Backup` to "readonly"
