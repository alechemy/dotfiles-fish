-- Keyboard Maestro: New Note → DEVONthink Archive
--
-- Sends the current selection to the DEVONthink "99_ARCHIVE" group.
-- Dispatches on content:
--   - Single http(s) URL (after stripping wrappers/trailing punctuation)
--       → bookmark record with URL set
--   - Anything else
--       → Markdown record, with the same first-line-as-title heuristic as
--         the inbox variant
--
-- Keyboard Maestro macro shape:
--   Trigger: Hotkey (e.g. ⌃⌥⌘A)
--   Actions:
--     1. Copy to Named Clipboard "DT_Selection"  (grabs selection without
--                                                 touching the system clipboard)
--     2. Execute AppleScript → point at this file
--
-- Depends on: keyboard-maestro/classify-note.py (same directory)

property archiveGroupUUID : "6B07BF0B-4DFE-44E7-A044-663C7FD0D212"

on run
  set rawText to ""
  try
    tell application "Keyboard Maestro Engine"
      set rawText to process tokens "%NamedClipboard%DT_Selection%"
    end tell
  end try

  if rawText is "" then
    display notification "Clipboard is empty" with title "New DT Archive Note"
    return
  end if

  set scriptPath to (POSIX path of (path to home folder)) & ".dotfiles/keyboard-maestro/classify-note.py"

  try
    set pyOutput to do shell script "export RAW_TEXT=" & quoted form of rawText & " && " & quoted form of scriptPath without altering line endings
  on error errMsg
    display notification "Classifier failed: " & errMsg with title "New DT Archive Note"
    return
  end try

  set nlPos to offset of linefeed in pyOutput
  if nlPos is 0 then
    display notification "Classifier output malformed" with title "New DT Archive Note"
    return
  end if
  set modeFlag to text 1 thru (nlPos - 1) of pyOutput
  set payload to text (nlPos + 1) thru -1 of pyOutput

  if modeFlag is "bookmark" then
    tell application id "DNtp"
      set tgt to get record with uuid archiveGroupUUID
      set newRec to create record with {name:payload, type:bookmark, URL:payload} in tgt
      add custom meta data 1 for "NeedsProcessing" to newRec
    end tell
    display notification payload with title "Added bookmark to DT Archive"

  else if modeFlag is "markdown" then
    set sepPos to offset of "<<<SPLIT>>>" in payload
    if sepPos is 0 then
      display notification "Classifier output missing split marker" with title "New DT Archive Note"
      return
    end if
    set theTitle to text 1 thru (sepPos - 2) of payload
    set theBody to text (sepPos + 12) thru -1 of payload
    tell application id "DNtp"
      set tgt to get record with uuid archiveGroupUUID
      set newRec to create record with {name:theTitle, type:markdown, plain text:theBody} in tgt
      add custom meta data 1 for "NeedsProcessing" to newRec
    end tell
    display notification theTitle with title "Added to DT Archive"

  else
    display notification "Unknown mode: " & modeFlag with title "New DT Archive Note"
  end if
end run
