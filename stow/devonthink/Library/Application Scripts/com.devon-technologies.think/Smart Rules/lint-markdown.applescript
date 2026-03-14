-- Lint Markdown on Ingest
--
-- Runs markdownlint --fix on incoming Markdown files so they conform to
-- house style before entering the AI enrichment pipeline. Non-Markdown
-- records (RTF, Web Archives, Bookmarks, etc.) are skipped silently.
--
-- Intended to run as the first action in Extract: Native Text Bypass,
-- before the declarative flag-setting actions.

on performSmartRule(theRecords)
    tell application id "DNtp"
        repeat with theRecord in theRecords
            set recType to type of theRecord as string
            if recType is in {"markdown", "«constant ****mkdn»"} then
                set thePath to path of theRecord
                if thePath is not "" and thePath is not missing value then
                    try
                        do shell script ¬
                            "sed -i '' 's/\\t/  /g' " & quoted form of thePath & ¬
                            " && /opt/homebrew/bin/markdownlint " & quoted form of thePath & " --quiet --fix || true"
                        synchronize record theRecord
                    on error errMsg
                        log message "Lint Markdown: failed for " & (name of theRecord) & ": " & errMsg
                    end try
                end if
            end if
        end repeat
    end tell
end performSmartRule
