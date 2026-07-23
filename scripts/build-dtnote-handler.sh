#!/bin/bash
# Builds and registers DTNote.app, the dtnote:// URL-scheme handler the daily
# note briefing's create-on-click event links route through (see
# devonthink/utils/dtnote-handler.applescript and dtnote-open.py). A URL
# scheme needs a registered app bundle — there is no lighter LaunchServices
# hook — so the applet is compiled locally from the tracked source; nothing
# prebuilt is committed. Safe to re-run; lsregister -f re-registers in place.
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$DOTFILES/devonthink/utils/dtnote-handler.applescript"
APP="$HOME/Applications/DTNote.app"
PLIST="$APP/Contents/Info.plist"
PB=/usr/libexec/PlistBuddy

mkdir -p "$HOME/Applications"
osacompile -o "$APP" "$SRC"

"$PB" -c "Delete :CFBundleURLTypes" "$PLIST" 2>/dev/null || true
"$PB" -c "Add :CFBundleURLTypes array" "$PLIST"
"$PB" -c "Add :CFBundleURLTypes:0 dict" "$PLIST"
"$PB" -c "Add :CFBundleURLTypes:0:CFBundleURLName string DEVONthink note opener" "$PLIST"
"$PB" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "$PLIST"
"$PB" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string dtnote" "$PLIST"
"$PB" -c "Set :CFBundleIdentifier com.user.dtnote" "$PLIST" 2>/dev/null \
  || "$PB" -c "Add :CFBundleIdentifier string com.user.dtnote" "$PLIST"
"$PB" -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
  || "$PB" -c "Set :LSUIElement true" "$PLIST"

codesign --force --sign - "$APP"
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -f "$APP"
echo "Built and registered $APP (dtnote://)"
