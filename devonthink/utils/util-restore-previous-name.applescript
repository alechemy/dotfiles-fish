on performSmartRule(theRecords)
  tell application id "DNtp"
    repeat with theRecord in theRecords
      set prevName to (get custom meta data for "PreviousName" from theRecord)
      if prevName is not "" and prevName is not missing value then
        -- NameLocked is already On from the original AI rename;
        -- re-assert it before renaming so the on-rename guard
        -- (02c-guard) won't fire for this rename event.
        add custom meta data 1 for "NameLocked" to theRecord
        try
          set name of theRecord to prevName
        on error errMsg
          log message "Restore Name failed: " & errMsg info (name of theRecord)
        end try
        -- Clear PreviousName so the rule no longer matches this record
        add custom meta data "" for "PreviousName" to theRecord
      end if
    end repeat
  end tell
end performSmartRule
