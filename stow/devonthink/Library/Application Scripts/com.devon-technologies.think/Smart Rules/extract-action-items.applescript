on performSmartRule(theRecords)
    tell application id "DNtp"
        repeat with theRecord in theRecords
            set isHandwritten to (get custom meta data for "Handwritten" from theRecord)
            if isHandwritten is 1 then
                set docText to comment of theRecord
            else
                set docText to plain text of theRecord
            end if


            if docText is not "" then
                -- Python parser: finds the target header, grabs list items until the next header
                set pyScript to "import sys, re\ntext = sys.stdin.read()\nin_tasks = False\nfor line in text.splitlines():\n    if re.match(r'^\\s*#*\\s*(Action Items|Todos|To-Dos|To Do|Tasks):?\\s*$', line, re.IGNORECASE):\n        in_tasks = True\n        continue\n    if in_tasks:\n        if re.match(r'^\\s*#+\\s', line):\n            break\n        m = re.match(r'^\\s*[-*•]\\s*(?:\\[\\s?[xX]?\\]\\s*)?(.+)', line.strip())\n        if m:\n            print(m.group(1).strip())"

                try
                    set tmpPath to do shell script "mktemp /tmp/dt-tasks.XXXXXX"
                    set fileRef to open for access (POSIX file tmpPath) with write permission
                    write docText to fileRef as «class utf8»
                    close access fileRef
                    set extractedTasks to do shell script "/usr/bin/python3 -c " & quoted form of pyScript & " < " & quoted form of tmpPath
                    do shell script "rm -f " & quoted form of tmpPath

                    if extractedTasks is not "" then
                        set theTasks to paragraphs of extractedTasks
                        set docName to name of theRecord
                        set docLink to reference URL of theRecord

                        -- Load previously extracted tasks to avoid duplicates
                        set oldTasksRaw to (get custom meta data for "PreviousTasks" from theRecord)
                        if oldTasksRaw is missing value or oldTasksRaw is "" then
                            set oldTaskList to {}
                        else
                            set oldTaskList to paragraphs of oldTasksRaw
                        end if


                        set newTasksToCreate to {}
                        repeat with i from 1 to count of theTasks
                            set taskStr to item i of theTasks as text
                            if taskStr is not "" then
                                set isDuplicate to false
                                repeat with j from 1 to count of oldTaskList
                                    if (item j of oldTaskList as text) is taskStr then
                                        set isDuplicate to true
                                        exit repeat
                                    end if
                                end repeat

                                if not isDuplicate then
                                    set end of newTasksToCreate to taskStr
                                end if
                            end if
                        end repeat

                        set sentCount to count of newTasksToCreate

                        if sentCount > 0 then
                            tell application "Things3"
                                repeat with taskStr in newTasksToCreate
                                    set taskNotes to "From DEVONthink: " & docName & return & docLink
                                    make new to do with properties {name:taskStr, notes:taskNotes}
                                end repeat
                            end tell
                        else
                        end if

                        -- Save the new full list of tasks so future updates ignore them
                        set updatedTasksRaw to ""
                        if oldTasksRaw is not missing value then
                            set updatedTasksRaw to oldTasksRaw as text
                        end if
                        repeat with aTask in newTasksToCreate
                            if updatedTasksRaw is not "" then
                                set updatedTasksRaw to updatedTasksRaw & return & aTask
                            else
                                set updatedTasksRaw to aTask as text
                            end if
                        end repeat
                        add custom meta data updatedTasksRaw for "PreviousTasks" to theRecord

                    else
                        -- If no tasks found, we leave PreviousTasks alone
                        -- so we don't 'forget' them and re-send if they reappear.
                    end if
                on error errMsg
                    log message "Extract Action Items: Failed to parse or send tasks: " & errMsg info (name of theRecord)
                end try
            else
            end if

            -- Mark as extracted so it advances to Archive
            add custom meta data 1 for "TasksExtracted" to theRecord
        end repeat
    end tell
end performSmartRule
