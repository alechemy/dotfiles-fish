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

        -- 2. Only process Markdown files (still save non-markdown!)
        set recType to type of targetRecord as string
        if recType is not in {"markdown", "«constant ****mkdn»"} then
            save theWindow
            return
        end if

        set thePath to path of targetRecord
        if thePath is "" or thePath is missing value then
            save theWindow
            return
        end if

        -- 3. Flush editor to disk
        save theWindow

        -- 4. Wait for file to be stable on disk
        set deadline to (current date) + 2
        repeat
            try
                do shell script "test -s " & quoted form of thePath
                exit repeat
            end try
            if (current date) > deadline then
                display alert "Lint Warning" message "Timed out waiting for file on disk."
                return
            end if
            delay 0.1
        end repeat
        delay 0.3

        -- 5. Modify in a single shell invocation
        do shell script ¬
            "sed -i '' 's/\\t/  /g' " & quoted form of thePath & ¬
            " && /opt/homebrew/bin/markdownlint " & quoted form of thePath & " --quiet --fix || true"

        -- 6. Refresh DEVONthink
        synchronize record targetRecord

    on error error_message number error_number
        if error_number is not -128 then
            display alert "Lint Error" message (error_message & " (" & error_number & ")")
        end if
    end try
end tell
