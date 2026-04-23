-- 02b - Format & Comment Handwritten Notes
--
-- Waits for 02a's async OCR to populate `plain text`, then sends the raw
-- transcription to the LLM for markdown formatting. The formatted text is
-- written to the Finder Comment. If `plain text` is still empty, the script
-- skips the record and retries on the next poll. A 5-minute timeout (based
-- on the `RecognizedAt` timestamp set by 02a) prevents records from staying
-- in limbo indefinitely if OCR stalls.
--
-- The `Commented` flag is flipped inside this script (not as a declarative
-- action) so it is only set when `plain text` was actually available and
-- processed.

on performSmartRule(theRecords)
    tell application id "DNtp"
        set maxWaitSeconds to 300 -- 5 minutes
        set theRole to "You are a markdown formatting assistant."
        set theInstructions to "Reformat the following OCR transcription of a handwritten note as clean Markdown. Preserve ALL original content exactly — do not add, remove, or rephrase anything." & linefeed & linefeed & ¬
            "Rules:" & linefeed & ¬
            "- Use #/##/### headers for titles and section breaks (replace underlines or horizontal rules)." & linefeed & ¬
            "- Replace middle dots (·), bullet characters (•), and any other non-standard list markers with standard Markdown list bullets (-), preserving nesting via indentation." & linefeed & ¬
            "- Replace drawn arrows (→, ↓, etc.) and connectors with nested lists or blockquotes to show relationships." & linefeed & ¬
            "- When text appears to wrap across multiple lines as a single thought or sentence, join it into one line rather than treating each line as a separate list item." & linefeed & ¬
            "- Replace curly braces or brackets used to group items with nested lists." & linefeed & ¬
            "- Preserve line breaks between distinct thoughts." & linefeed & ¬
            "- Use **bold** for emphasized text." & linefeed & ¬
            "- Replace circled numbers (①, ②, ③, etc.) or other enclosed Unicode number forms with standard Markdown ordered list items (1., 2., 3.)." & linefeed & ¬
            "- Output ONLY the reformatted Markdown."

        repeat with theRecord in theRecords
            set recName to name of theRecord
            set theText to plain text of theRecord

            if theText is "" then
                -- Check if we've been waiting too long
                set stampDate to (get custom meta data for "RecognizedAt" from theRecord)
                if stampDate is not missing value and stampDate is not "" then
                    set elapsed to (current date) - stampDate
                    if elapsed > maxWaitSeconds then
                        log message "Format Boox Comments: timed out waiting for plain text after " & elapsed & "s, advancing with empty comment" info recName
                        set comment of theRecord to ""
                        set currentErrors to (get custom meta data for "ErrorCount" from theRecord)
                        if currentErrors is missing value or currentErrors is "" then set currentErrors to 0
                        add custom meta data (currentErrors + 1) for "ErrorCount" to theRecord
                        add custom meta data 1 for "Commented" to theRecord
                    else
                    end if
                else
                    log message "Format Boox Comments: no RecognizedAt timestamp found, advancing" info recName
                    set comment of theRecord to ""
                    set currentErrors to (get custom meta data for "ErrorCount" from theRecord)
                    if currentErrors is missing value or currentErrors is "" then set currentErrors to 0
                    add custom meta data (currentErrors + 1) for "ErrorCount" to theRecord
                    add custom meta data 1 for "Commented" to theRecord
                end if
            else
                set thePrompt to theInstructions & linefeed & linefeed & theText

                try
                    set formatted to get chat response for message thePrompt ¬
                        role theRole ¬
                        mode "text" ¬
                        thinking false ¬
                        tool calls false

                    if formatted is not "" and formatted is not missing value then
                        -- Run markdownlint --fix via temp file
                        try
                            set tmpFile to do shell script "mktemp /tmp/dt-mdlint-XXXXXX.md"
                            set fileRef to open for access (POSIX file tmpFile) with write permission
                            write formatted to fileRef as «class utf8»
                            close access fileRef
                            do shell script "/opt/homebrew/bin/markdownlint " & quoted form of tmpFile & " --quiet --fix || true"
                            -- Safety net: replace any Unicode circled numbers the LLM missed
                            set pyFixCircled to "import sys" & linefeed & ¬
                                "f = sys.argv[1]" & linefeed & ¬
                                "t = open(f).read()" & linefeed & ¬
                                "circled = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'" & linefeed & ¬
                                "for i, c in enumerate(circled):" & linefeed & ¬
                                "    t = t.replace(c, str(i + 1) + '.')" & linefeed & ¬
                                "open(f, 'w').write(t)"
                            do shell script "/usr/bin/python3 -c " & quoted form of pyFixCircled & " " & quoted form of tmpFile
                            -- Safety net: replace non-standard bullet markers the LLM missed
                            set pyFixBullets to "import sys, re" & linefeed & ¬
                                "f = sys.argv[1]" & linefeed & ¬
                                "t = open(f).read()" & linefeed & ¬
                                "t = re.sub(r'^(\\s*)[·•‣⁃◦▪▸](\\s*)', r'\\1-\\2', t, flags=re.MULTILINE)" & linefeed & ¬
                                "open(f, 'w').write(t)"
                            do shell script "/usr/bin/python3 -c " & quoted form of pyFixBullets & " " & quoted form of tmpFile
                            set formatted to do shell script "cat " & quoted form of tmpFile
                            do shell script "rm -f " & quoted form of tmpFile
                        on error lintErr
                            log message "Format Boox Comments: markdownlint failed, using unlinted output: " & lintErr info recName
                            do shell script "rm -f " & quoted form of tmpFile
                        end try
                        set comment of theRecord to formatted
                    else
                        log message "Format Boox Comments: LLM returned empty, falling back to raw text" info recName
                        set comment of theRecord to theText
                        set currentErrors to (get custom meta data for "ErrorCount" from theRecord)
                        if currentErrors is missing value or currentErrors is "" then set currentErrors to 0
                        add custom meta data (currentErrors + 1) for "ErrorCount" to theRecord
                    end if
                on error errMsg
                    log message "Format Boox Comments: LLM formatting failed: " & errMsg info recName
                    set comment of theRecord to theText
                    set currentErrors to (get custom meta data for "ErrorCount" from theRecord)
                    if currentErrors is missing value or currentErrors is "" then set currentErrors to 0
                    add custom meta data (currentErrors + 1) for "ErrorCount" to theRecord
                end try

                add custom meta data 1 for "Commented" to theRecord
            end if
        end repeat
    end tell
end performSmartRule
