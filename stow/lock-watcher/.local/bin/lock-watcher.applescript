#!/usr/bin/osascript

use framework "Foundation"
use scripting additions

property lockMacroName : "Screen Locked"

on screenLocked:aNotification
    my triggerMacro(lockMacroName)
end screenLocked:

-- run script compiles the tell at runtime, so this file parses on machines
-- where Keyboard Maestro was never launched (no dictionary registered yet).
on triggerMacro(macroName)
    try
        run script "tell application \"Keyboard Maestro Engine\" to do script \"" & macroName & "\""
        my logLine("triggered macro: " & macroName)
    on error errMsg
        my logLine("macro '" & macroName & "' failed: " & errMsg)
    end try
end triggerMacro

on logLine(msg)
    log ((current date) as text) & " lock-watcher: " & msg
end logLine

on run
    if not (current application's NSFileManager's defaultManager()'s fileExistsAtPath:"/Applications/Keyboard Maestro.app") then
        my logLine("Keyboard Maestro not installed; staying dormant")
        return
    end if
    -- Lock only: unlock-side work belongs on KM's native Unlock trigger, which
    -- exists (11.0.4); a lock trigger is the one KM lacks.
    set nc to current application's NSDistributedNotificationCenter's defaultCenter()
    nc's addObserver:me selector:"screenLocked:" |name|:"com.apple.screenIsLocked" object:(missing value)
    -- Harmless AppleEvent at load so the one-time Automation prompt fires while
    -- the user is present, not behind a locked screen at the first real event.
    try
        run script "tell application \"Keyboard Maestro Engine\" to getvariable \"lockWatcherStartupPing\""
        my logLine("started; Keyboard Maestro Engine reachable")
    on error errMsg
        my logLine("started; startup ping failed (grant pending?): " & errMsg)
    end try
    current application's NSRunLoop's currentRunLoop()'s |run|()
end run
