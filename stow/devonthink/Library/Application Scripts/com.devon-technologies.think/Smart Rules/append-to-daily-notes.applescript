on performSmartRule(theRecords)
    tell application id "DNtp"
        set dbName to "Lorebook"
        set groupPath to "/10_DAILY"
        set sectionHeader to "## Today's Notes"

        try
            set targetDB to database dbName
        on error
            log message "Process Daily Notes: database " & dbName & " not found."
            return
        end try

        set destGroup to get record at groupPath in targetDB
        if destGroup is missing value then
            log message "Process Daily Notes: group " & groupPath & " not found."
            return
        end if

        set todayStr to do shell script "date '+%Y-%m-%d'"
        set todayFilename to todayStr & ".md"

        repeat with theRecord in theRecords
            set docName to name of theRecord
            set docBaseName to do shell script "echo " & quoted form of docName & " | sed 's/\\.[^.]*$//'"
            set isHandwritten to (get custom meta data for "Handwritten" from theRecord)

            if isHandwritten is 1 then
                set docText to comment of theRecord
            else
                set docText to plain text of theRecord
            end if

            -- Read and validate EventDate once for use by both features
            set eventDate to (get custom meta data for "EventDate" from theRecord) as text
            set hasValidEventDate to false
            if eventDate is not missing value and eventDate is not "" ¬
                and (count of eventDate) is 10 ¬
                and character 5 of eventDate is "-" ¬
                and character 8 of eventDate is "-" then
                set hasValidEventDate to true
            end if

            -- =========================================================
            -- 1. Extract and Append Daily Notes sections (Handwritten)
            -- =========================================================
            if isHandwritten is 1 and docText is not "" then
                set pyScript to "import sys, re\ntext = sys.stdin.read()\nin_section = False\nfor line in text.splitlines():\n    if re.match(r'^\\s*#*\\s*(Daily Notes?|Today|Journal|Log|Update):?\\s*$', line, re.IGNORECASE):\n        in_section = True\n        continue\n    if in_section:\n        if re.match(r'^\\s*#+\\s', line):\n            break\n        if line.strip() != '':\n            print(line)\n"

                try
                    set tmpPath to do shell script "mktemp /tmp/dt-daily.XXXXXX"
                    set fileRef to open for access (POSIX file tmpPath) with write permission
                    write docText to fileRef as «class utf8»
                    close access fileRef
                    set extractedLines to do shell script "/usr/bin/python3 -c " & quoted form of pyScript & " < " & quoted form of tmpPath
                    do shell script "rm -f " & quoted form of tmpPath

                    if extractedLines is not "" then
                        set theLines to paragraphs of extractedLines

                        -- Load previously extracted lines
                        set oldLinesRaw to (get custom meta data for "PreviousDailyNotes" from theRecord)
                        if oldLinesRaw is missing value or oldLinesRaw is "" then
                            set oldLinesList to {}
                        else
                            set oldLinesList to paragraphs of oldLinesRaw
                        end if

                        set newLinesToAppend to {}
                        repeat with i from 1 to count of theLines
                            set lineStr to item i of theLines as text
                            if lineStr is not "" then
                                set isDuplicate to false
                                repeat with j from 1 to count of oldLinesList
                                    if (item j of oldLinesList as text) is lineStr then
                                        set isDuplicate to true
                                        exit repeat
                                    end if
                                end repeat

                                if not isDuplicate then
                                    set end of newLinesToAppend to lineStr
                                end if
                            end if
                        end repeat

                        if (count of newLinesToAppend) > 0 then
                            -- Use EventDate's daily note if valid, fall back to today
                            if hasValidEventDate then
                                set extractTargetFilename to eventDate & ".md"
                            else
                                set extractTargetFilename to todayFilename
                            end if

                            set extractTargetNote to get record at (groupPath & "/" & extractTargetFilename) in targetDB

                            if extractTargetNote is not missing value then
                                -- Build the block to append
                                set contentBlock to "### From [[" & docBaseName & "]]:" & return & return
                                repeat with aLine in newLinesToAppend
                                    set contentBlock to contentBlock & aLine & return
                                end repeat

                                my appendToSection(extractTargetNote, sectionHeader, contentBlock)

                                -- Save updated state to avoid duplicates next time
                                set updatedLinesRaw to ""
                                if oldLinesRaw is not missing value then
                                    set updatedLinesRaw to oldLinesRaw as text
                                end if
                                repeat with aLine in newLinesToAppend
                                    if updatedLinesRaw is not "" then
                                        set updatedLinesRaw to updatedLinesRaw & return & aLine
                                    else
                                        set updatedLinesRaw to aLine as text
                                    end if
                                end repeat
                                add custom meta data updatedLinesRaw for "PreviousDailyNotes" to theRecord
                            else
                                log message "Process Daily Notes: daily note (" & extractTargetFilename & ") not found, skipping extraction for " & docName
                            end if
                        end if
                    else
                        -- If no daily notes found, we leave PreviousDailyNotes alone
                        -- so we don't 'forget' them and re-append if they reappear.
                    end if
                on error errMsg
                    log message "Extract Daily Notes: Failed to parse sections: " & errMsg info docName
                end try
            end if

            -- =========================================================
            -- 2. Append Wikilink to daily note (All documents)
            -- =========================================================
            set isLinked to (get custom meta data for "DailyNoteLinked" from theRecord)
            if isLinked is not 1 then
                if hasValidEventDate then
                    set targetFilename to eventDate & ".md"
                else
                    -- Fall back to the document's creation date so a note
                    -- taken on Tuesday still links to Tuesday even if the
                    -- smart rule doesn't run until Thursday.
                    set cDate to creation date of theRecord
                    set cYear to year of cDate as text
                    set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
                    set cDay to text -2 thru -1 of ("0" & (day of cDate))
                    set targetFilename to cYear & "-" & cMonth & "-" & cDay & ".md"
                end if

                set targetNote to get record at (groupPath & "/" & targetFilename) in targetDB

                if targetNote is not missing value then
                    set linkText to "- [[" & docBaseName & "]]"

                    -- Only append if the link isn't already in the note
                    if (plain text of targetNote) does not contain linkText then
                        my appendToSection(targetNote, sectionHeader, linkText & return)
                    end if

                    add custom meta data 1 for "DailyNoteLinked" to theRecord
                else
                    log message "Process Daily Notes: Target daily note (" & targetFilename & ") not found, skipping wikilink for " & docName
                end if
            end if

            -- Mark as processed for daily notes so it can advance
            add custom meta data 1 for "DailyNotesProcessed" to theRecord

        end repeat
    end tell
end performSmartRule

-- Appends contentBlock under the given section header in a daily note.
-- Creates the section at the end of the note if it doesn't exist yet.
on appendToSection(theNote, sectionHeader, contentBlock)
    tell application id "DNtp"
        set noteText to plain text of theNote

        -- Single Python call: find the section (or note its absence),
        -- insert the content block at the right position, print the result.
        set pyScript to "import sys, re, os" & linefeed & ¬
            "note = sys.stdin.read()" & linefeed & ¬
            "header = os.environ['SECTION_HEADER']" & linefeed & ¬
            "block = os.environ['CONTENT_BLOCK']" & linefeed & ¬
            "lines = note.splitlines()" & linefeed & ¬
            "header_idx = None" & linefeed & ¬
            "for i, line in enumerate(lines):" & linefeed & ¬
            "    if line.strip() == header:" & linefeed & ¬
            "        header_idx = i" & linefeed & ¬
            "        break" & linefeed & ¬
            "block_lines = block.rstrip('\\n').split('\\n')" & linefeed & ¬
            "if header_idx is None:" & linefeed & ¬
            "    lines.append('')" & linefeed & ¬
            "    lines.append(header)" & linefeed & ¬
            "    lines.append('')" & linefeed & ¬
            "    lines += block_lines" & linefeed & ¬
            "else:" & linefeed & ¬
            "    insert_idx = len(lines)" & linefeed & ¬
            "    for i in range(header_idx + 1, len(lines)):" & linefeed & ¬
            "        if re.match(r'^#{1,2}\\s', lines[i]):" & linefeed & ¬
            "            insert_idx = i" & linefeed & ¬
            "            break" & linefeed & ¬
            "    first_is_list = block_lines[0].lstrip().startswith('- ')" & linefeed & ¬
            "    prev_is_list = insert_idx > 0 and lines[insert_idx - 1].lstrip().startswith('- ')" & linefeed & ¬
            "    if not (prev_is_list and first_is_list):" & linefeed & ¬
            "        lines.insert(insert_idx, '')" & linefeed & ¬
            "        insert_idx += 1" & linefeed & ¬
            "    for bl in block_lines:" & linefeed & ¬
            "        lines.insert(insert_idx, bl)" & linefeed & ¬
            "        insert_idx += 1" & linefeed & ¬
            "print('\\n'.join(lines), end='')"

        set tmpPath to do shell script "mktemp /tmp/dt-daily.XXXXXX"
        set fileRef to open for access (POSIX file tmpPath) with write permission
        write noteText to fileRef as «class utf8»
        close access fileRef
        set newText to do shell script ¬
            "export SECTION_HEADER=" & quoted form of sectionHeader & ¬
            " && export CONTENT_BLOCK=" & quoted form of contentBlock & ¬
            " && /usr/bin/python3 -c " & quoted form of pyScript & ¬
            " < " & quoted form of tmpPath
        do shell script "rm -f " & quoted form of tmpPath

        set plain text of theNote to newText
    end tell
end appendToSection
