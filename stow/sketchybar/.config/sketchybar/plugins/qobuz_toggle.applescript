on run
  tell application "System Events" to tell process "Qobuz"
    set value of attribute "AXManualAccessibility" to true
    set rootElement to window 1
    repeat 14 times
      try
        if ((value of attribute "AXDOMClassList" of rootElement) as text) contains "grid-layout--root" then exit repeat
      end try
      set rootElement to UI element 1 of rootElement
    end repeat

    set bottomPanel to missing value
    repeat with candidate in UI elements of rootElement
      try
        if ((value of attribute "AXDOMClassList" of candidate) as text) contains "panel-outer-bottom" then
          set bottomPanel to candidate
          exit repeat
        end if
      end try
    end repeat
    if bottomPanel is missing value then error "Qobuz bottom panel not found" number 2

    repeat with candidate in UI elements of (UI element 1 of bottomPanel)
      try
        set classNames to value of attribute "AXDOMClassList" of candidate
        repeat with className in classNames
          if (className as text) is in {"player__action-pause", "player__action-play"} then
            perform action "AXPress" of candidate
            return
          end if
        end repeat
      end try
    end repeat
    error "Qobuz play/pause control not found" number 3
  end tell
end run
