-- Prose-check (On Demand)
-- Invokes the /prose-check Claude Code skill on the selected record.
-- Works with: markdown, txt, rtf, rtfd, and formatted note records.
-- Passes the source record's UUID so the skill can fetch plain text via
-- DEVONthink and set RewriteSource as an item link back to the original.
-- Output is a new DEVONthink record in 00_INBOX, created by the skill.
-- Runs in the background; check ~/Library/Logs/prose-check.log for progress.

on performSmartRule(theRecords)
    tell application id "DNtp"
        repeat with theRecord in theRecords
            set recName to name of theRecord
            set recType to (type of theRecord) as string
            set recUUID to uuid of theRecord

            -- Only handle text-based records that contain prose to rewrite.
            -- Bookmarks, PDFs, and images are intentionally skipped: prose-check
            -- is for cleaning up your own writing, not rewriting third-party
            -- web or PDF content. For those, invoke /prose-check directly.
            if recType is "markdown" or recType is "txt" or recType is "rtf" or recType is "rtfd" or recType is "formatted note" then
                set claudePath to "/Users/alec/.local/bin/claude"
                set logPath to (POSIX path of (path to home folder)) & "Library/Logs/prose-check.log"
                set thePrompt to "/prose-check --dt-source " & recUUID
                -- Auth via setup-token from 1Password; PATH for tools.
                set envSetup to "export CLAUDE_CODE_OAUTH_TOKEN=$(/opt/homebrew/bin/op read 'op://Private/Claude Code/setup-token'); export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; "
                set cmd to envSetup & "nohup " & quoted form of claudePath & " -p " & quoted form of thePrompt & " >> " & quoted form of logPath & " 2>&1 &"
                do shell script cmd
            else
                log message "Prose-check: record type " & recType & " not supported, skipping" info recName
            end if
        end repeat
    end tell
end performSmartRule
