tell application id "DNtp"
    try
        set theWindow to think window 1
        set targetRecord to missing value

        -- 1. Identify record via UUID re-resolution (more defensive)
        try
            set rawRecord to content record of theWindow
            if rawRecord is not missing value then
                set recordUUID to uuid of rawRecord
                set targetRecord to get record with uuid recordUUID
            end if
        end try
        if targetRecord is missing value then
            set selectedList to selected records of theWindow
            if (count of selectedList) > 0 then
                set recordUUID to uuid of item 1 of selectedList
                set targetRecord to get record with uuid recordUUID
            end if
        end if

        if targetRecord is missing value then return

        -- 2. Only post-process Markdown — still save non-markdown normally.
        set recType to type of targetRecord as string
        if recType is not in {"markdown", "«constant ****mkdn»"} then
            save theWindow
            return
        end if

        -- 3. Flush the editor's in-memory edits into DT's record state so
        --    `plain text` reflects what the user just typed. (Previously this
        --    script ran `sed -i ''` on `path of theRecord` then
        --    `synchronize record` — exactly the pattern devonthink/CLAUDE.md
        --    warns against, because sync races DT's buffered write and can
        --    overwrite the in-memory record with stale/empty disk state.)
        save theWindow

        set originalText to plain text of targetRecord
        if originalText is missing value or originalText is "" then return

        -- 4. Transform via a stdin-style shell pipeline using a tempfile we
        --    own. We never touch `path of theRecord` — only our scratch file.
        --    `markdownlint-stdin-fix` is the existing repo helper that wraps
        --    `markdownlint --fix` (which is file-only) into a stdin/stdout
        --    transformer; the leading sed handles tabs → two spaces.
        set lintHelper to (POSIX path of (path to home folder)) & ".local/bin/markdownlint-stdin-fix"
        set tmpPath to do shell script "mktemp /tmp/dt-save-override.XXXXXX"
        set newText to originalText
        try
            set fileRef to open for access (POSIX file tmpPath) with write permission
            write originalText to fileRef as «class utf8»
            close access fileRef
            -- `without altering line endings`: otherwise the lint output comes
            -- back CR-delimited, which stores the body as one line and makes
            -- the changed-check below true on every save.
            set newText to do shell script ¬
                "/usr/bin/sed 's/\\t/  /g' " & quoted form of tmpPath & ¬
                " | " & quoted form of lintHelper without altering line endings
        on error errMsg number errNum
            try
                close access (POSIX file tmpPath)
            end try
            do shell script "rm -f " & quoted form of tmpPath
            error errMsg number errNum
        end try
        do shell script "rm -f " & quoted form of tmpPath

        -- 5. Write back through DT only if the lint actually changed
        --    something, then save again to persist the cleanup pass.
        if newText is not equal to originalText then
            set plain text of targetRecord to newText
            save theWindow
        end if

    on error error_message number error_number
        if error_number is not -128 then
            display alert "Lint Error" message (error_message & " (" & error_number & ")")
        end if
    end try
end tell
