on performSmartRule(theRecords)
  tell application id "DNtp"
    set archiveGroup to get record at "/99_ARCHIVE" in database "Lorebook"
    if archiveGroup is missing value then
      log message "Archive Processed Items: could not locate /99_ARCHIVE — aborting"
      return
    end if
    repeat with r in theRecords
      try
        move record r to archiveGroup
        -- Only clear the flag AFTER a successful move
        add custom meta data 0 for "NeedsProcessing" to r
      on error errMsg
        -- Move failed — leave NeedsProcessing ON so 02z retries next poll
        log message "Archive Processed Items: archive failed for " & (name of r) & ": " & errMsg
      end try
    end repeat
  end tell
end performSmartRule
