-- Summarize (On Demand)
-- Invokes the /summarize Claude Code skill on the selected record.
-- Works with: bookmarks (uses URL) and PDFs (uses file path).
-- Passes the source record's UUID so the skill can set SummarySource.
-- Output is imported into 00_INBOX by the skill and flows through the pipeline.
-- Runs in the background; check ~/Library/Logs/summarize.log for progress.

on performSmartRule(theRecords)
    tell application id "DNtp"
        repeat with theRecord in theRecords
            set recName to name of theRecord
            set recType to (type of theRecord) as string
            set recUUID to uuid of theRecord
            set srcInput to ""

            -- Bookmarks: use the URL
            if recType is "bookmark" then
                set srcInput to URL of theRecord

            -- Files on disk: use the file path
            else
                try
                    set recPath to path of theRecord
                    if recPath is not missing value and recPath is not "" then
                        set srcInput to recPath
                    end if
                end try
            end if

            if srcInput is "" then
                log message "Summarize: no URL or file path found, skipping" info recName
            else
                -- Pass the source record UUID so the skill can set SummarySource
                -- as an item link back to this record.
                set claudePath to "/Users/alec/.local/bin/claude"
                set logPath to (POSIX path of (path to home folder)) & "Library/Logs/summarize.log"
                set thePrompt to "/summarize " & srcInput & " --dt-source " & recUUID
                -- Auth via setup-token from 1Password; PATH for tools (pdftotext, defuddle, etc.)
                set envSetup to "export CLAUDE_CODE_OAUTH_TOKEN=$(/opt/homebrew/bin/op read 'op://Private/Claude Code/setup-token'); export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; "
                set cmd to envSetup & "nohup " & quoted form of claudePath & " -p " & quoted form of thePrompt & " >> " & quoted form of logPath & " 2>&1 &"
                do shell script cmd
            end if
        end repeat
    end tell
end performSmartRule
