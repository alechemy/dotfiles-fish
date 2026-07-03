-- Post-Enrich & Archive
--
-- Runs after AI enrichment completes. Performs four steps in a single pass:
--   1. Extracts action items and sends them to Things 3 (handwritten docs only)
--   2. Processes daily notes: extracts journal sections from handwritten docs
--      and appends wikilinks for documents with EventDate (skipped for web clips)
--   3. Syncs H1 heading to record name for markdown documents
--   4. Archives the record to 99_ARCHIVE
--
-- This consolidates the previous Extract: Action Items, Process: Daily Notes,
-- and Archive: Processed Items rules into one script, eliminating two polling
-- rules and the TasksExtracted / DailyNotesProcessed pipeline gate flags.
--
-- Smart rule criteria:
--   Search in: 00_INBOX
--   NeedsProcessing is On
--   Recognized is On
--   Commented is On
--   AIEnriched is On
--   Trigger: Every Minute

on performSmartRule(theRecords)
  -- [follower-guard] only the DEVONthink pipeline driver mutates documents (see should-run-dt-driver)
  try
    do shell script "$HOME/.local/bin/should-run-dt-driver"
  on error
    return
  end try
	tell application id "DNtp"
		-- Archive destination
		set archiveGroup to get record at "/99_ARCHIVE" in database "Lorebook"
		if archiveGroup is missing value then
			log message "Post-Enrich & Archive: could not locate /99_ARCHIVE — aborting"
			return
		end if

		-- Daily notes setup
		set groupPath to "/10_DAILY"
		set sectionHeader to "## Today's Notes"
		set targetDB to database "Lorebook"
		set destGroup to get record at groupPath in targetDB
		if destGroup is missing value then
			log message "Post-Enrich & Archive: group " & groupPath & " not found — daily notes will be skipped"
		end if

		set todayStr to do shell script "date '+%Y-%m-%d'"
		set todayFilename to todayStr & ".md"

		repeat with theRecord in theRecords
			set recName to name of theRecord
			set recUUID to uuid of theRecord

			-- Determine if this is a web clip record (skip action items + daily notes)
			set clipSource to ""
			try
				set clipSource to (get custom meta data for "WebClipSource" from theRecord) as text
				if clipSource is "missing value" then set clipSource to ""
			end try
			set isWebClip to (clipSource is not "")

			-- Get document text (used by both action items and daily notes)
			set isHandwritten to (get custom meta data for "Handwritten" from theRecord)
			if isHandwritten is 1 then
				set docText to comment of theRecord
			else
				set docText to plain text of theRecord
			end if

			if not isWebClip then
				-- Shared values for daily notes processing
				set docBaseName to do shell script "echo " & quoted form of recName & " | sed 's/\\.[^.]*$//'"

				set eventDate to ""
				try
					set eventDate to (get custom meta data for "EventDate" from theRecord) as text
					if eventDate is "missing value" then set eventDate to ""
				end try
				set hasValidEventDate to false
				if eventDate is not "" ¬
					and (count of eventDate) is 10 ¬
					and character 5 of eventDate is "-" ¬
					and character 8 of eventDate is "-" then
					set hasValidEventDate to true
				end if

				-- =============================================
				-- Step 1: Extract Action Items → Things 3
				-- =============================================
				-- Gated on Handwritten so meeting-notes imports (e.g. Granola)
				-- don't dump every "Tasks" / "Action Items" bullet into Things.
				if isHandwritten is 1 then
					try
						if docText is not "" then
							set pyScript to "import sys, re\ntext = sys.stdin.read()\nin_tasks = False\nfor line in text.splitlines():\n    if re.match(r'^\\s*#*\\s*(Action Items|Todos|To-Dos|To Do|Tasks):?\\s*$', line, re.IGNORECASE):\n        in_tasks = True\n        continue\n    if in_tasks:\n        if re.match(r'^\\s*#+\\s', line):\n            break\n        m = re.match(r'^\\s*[-*•]\\s*(?:\\[\\s?[xX]?\\]\\s*)?(.+)', line.strip())\n        if m:\n            print(m.group(1).strip())"

							set tmpPath to do shell script "mktemp /tmp/dt-tasks.XXXXXX"
							set fileRef to open for access (POSIX file tmpPath) with write permission
							write docText to fileRef as «class utf8»
							close access fileRef
							set extractedTasks to do shell script "/usr/bin/python3 -c " & quoted form of pyScript & " < " & quoted form of tmpPath
							do shell script "rm -f " & quoted form of tmpPath

							if extractedTasks is not "" then
								set theTasks to paragraphs of extractedTasks
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

								if (count of newTasksToCreate) > 0 then
									try
										tell application "Things3"
											repeat with taskStr in newTasksToCreate
												set taskNotes to "From DEVONthink: " & recName & return & docLink
												make new to do with properties {name:taskStr, notes:taskNotes}
											end repeat
										end tell
									on error thingsErr
										log message "Post-Enrich & Archive: Things 3 error: " & thingsErr info recName
									end try
								end if

								-- Save the full list of tasks so future updates ignore them
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
							end if
						end if
					on error errMsg
						log message "Post-Enrich & Archive: action item extraction failed: " & errMsg info recName
					end try
				end if

				-- =============================================
				-- Step 2: Process Daily Notes
				-- =============================================

				-- 2a. Extract daily notes sections (handwritten only)
				if destGroup is not missing value and isHandwritten is 1 and docText is not "" then
					try
						set pyScript to "import sys, re\ntext = sys.stdin.read()\nin_section = False\nfor line in text.splitlines():\n    if re.match(r'^\\s*#*\\s*(Daily Notes?|Today|Journal|Log|Update):?\\s*$', line, re.IGNORECASE):\n        in_section = True\n        continue\n    if in_section:\n        if re.match(r'^\\s*#+\\s', line):\n            break\n        if line.strip() != '':\n            print(line)\n"

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
								if hasValidEventDate then
									set extractTargetDate to eventDate
								else
									set extractTargetDate to todayStr
								end if

								set extractTargetNote to my getOrCreateDailyNote(targetDB, destGroup, groupPath, extractTargetDate)

								if extractTargetNote is not missing value then
									set docUUID to uuid of theRecord
									set contentBlock to "### From [✏️ " & docBaseName & "](x-devonthink-item://" & docUUID & "):" & return & return
									repeat with aLine in newLinesToAppend
										set contentBlock to contentBlock & aLine & return
									end repeat

									my appendToSection(extractTargetNote, sectionHeader, contentBlock)

									-- Save updated state
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
									log message "Post-Enrich & Archive: daily note (" & extractTargetDate & ".md) could not be created, skipping extraction" info recName
								end if
							end if
						end if
					on error errMsg
						log message "Post-Enrich & Archive: daily notes extraction failed: " & errMsg info recName
					end try
				end if

				-- 2b. Append wikilink to daily note (all non-web-clip documents)
				if destGroup is not missing value then
					set isLinked to (get custom meta data for "DailyNoteLinked" from theRecord)
					if isLinked is not 1 then
						try
							if hasValidEventDate then
								set targetDate to eventDate
							else
								set cDate to creation date of theRecord
								set cYear to year of cDate as text
								set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
								set cDay to text -2 thru -1 of ("0" & (day of cDate))
								set targetDate to cYear & "-" & cMonth & "-" & cDay
							end if

							set targetNote to my getOrCreateDailyNote(targetDB, destGroup, groupPath, targetDate)

							if targetNote is not missing value then
								-- Determine emoji by document type
								set docType to type of theRecord
								if isHandwritten is 1 then
									set emoji to "✏️"
								else if hasValidEventDate then
									set emoji to "📅"
								else if docType is bookmark then
									set emoji to "🔗"
								else if docType is PDF document then
									set emoji to "📄"
								else
									set emoji to "📝"
								end if

								-- Format creation time as h:mmam/pm
								set recDate to creation date of theRecord
								set secSinceMidnight to time of recDate
								set cHour to secSinceMidnight div 3600
								set cMin to (secSinceMidnight mod 3600) div 60
								if cHour ≥ 12 then
									set ampm to "pm"
									if cHour > 12 then set cHour to cHour - 12
								else
									set ampm to "am"
									if cHour is 0 then set cHour to 12
								end if
								set timeStr to (cHour as text) & ":" & text -2 thru -1 of ("0" & (cMin as text)) & ampm

								set docUUID to uuid of theRecord
								set itemLink to "x-devonthink-item://" & docUUID
								set linkText to "- " & timeStr & ": [" & emoji & " " & docBaseName & "](" & itemLink & ")"

								-- Only append if this document isn't already linked (by UUID)
								if (plain text of targetNote) does not contain docUUID then
									my appendToSection(targetNote, sectionHeader, linkText & return)
								end if

								add custom meta data 1 for "DailyNoteLinked" to theRecord
							else
								log message "Post-Enrich & Archive: daily note (" & targetDate & ".md) could not be created, skipping wikilink" info recName
							end if
						on error errMsg
							log message "Post-Enrich & Archive: wikilink append failed: " & errMsg info recName
						end try
					end if
				end if
			end if

			-- =============================================
			-- Step 3: Sync H1 to filename (markdown only)
			-- =============================================
			if type of theRecord is markdown then
				try
					set mdText to plain text of theRecord
					if mdText is not "" then
						set titleForH1 to do shell script "echo " & quoted form of recName & " | sed 's/\\.[^.]*$//'"
						set tmpPath to do shell script "mktemp /tmp/dt-h1.XXXXXX"
						set fileRef to open for access (POSIX file tmpPath) with write permission
						write mdText to fileRef as «class utf8»
						close access fileRef
						set newText to do shell script "/usr/bin/python3 ~/.local/bin/sync-markdown-h1.py " & quoted form of titleForH1 & " < " & quoted form of tmpPath
						do shell script "rm -f " & quoted form of tmpPath
						if newText is not mdText then
							set plain text of theRecord to newText
						end if
					end if
				on error errMsg
					log message "Post-Enrich & Archive: H1 sync failed: " & errMsg info recName
				end try
			end if

			-- =============================================
			-- Step 3.5: Propagate name to web clip siblings
			-- =============================================
			-- When a SingleFile-ingested markdown has been renamed by AI
			-- enrichment (typically because the source page had no <title>
			-- and the ingester intentionally left NameLocked unset on the
			-- triad), push the new name to the linked bookmark and HTML
			-- snapshot so all three records share the same title. Only
			-- replaces names that still look like the ingester's "No title"
			-- placeholder, so a manually-edited bookmark name is never
			-- overwritten.
			if isWebClip and (type of theRecord is markdown) and clipSource is not "" then
				try
					set bmUUID to my uuidFromItemLink(clipSource)
					if bmUUID is not "" then
						set bmRecord to get record with uuid bmUUID
						if bmRecord is not missing value then
							my replaceIfPlaceholder(bmRecord, recName)

							set htmlRef to ""
							try
								set htmlRef to (get custom meta data for "WebClipSnapshot" from bmRecord) as text
								if htmlRef is "missing value" then set htmlRef to ""
							end try
							if htmlRef is not "" then
								set htmlUUID to my uuidFromItemLink(htmlRef)
								if htmlUUID is not "" then
									set htmlRecord to get record with uuid htmlUUID
									if htmlRecord is not missing value then
										my replaceIfPlaceholder(htmlRecord, recName)
									end if
								end if
							end if
						end if
					end if
				on error errMsg
					log message "Post-Enrich & Archive: web clip name propagation failed: " & errMsg info recName
				end try
			end if
			-- =============================================
			-- Step 3.6: Daily-note link for web clips
			-- =============================================
			-- The SingleFile ingester defers daily-note logging when the
			-- page had no usable <title> (NameLocked=0 + placeholder
			-- name). Step 3.5 above has now propagated the AI-enriched
			-- name to the bookmark sibling, so we can append a
			-- daily-note line with a real title. Idempotent: skipped
			-- when DailyNoteLinked is already 1 (the typical path,
			-- where the ingester logged with a good title up front).
			if isWebClip and (type of theRecord is markdown) and clipSource is not "" and destGroup is not missing value then
				try
					set bmUUID to my uuidFromItemLink(clipSource)
					if bmUUID is not "" then
						set bmRecord to get record with uuid bmUUID
						if bmRecord is not missing value then
							set isLinked to (get custom meta data for "DailyNoteLinked" from bmRecord)
							if isLinked is not 1 then
								set bmCreated to creation date of bmRecord
								set cYear to year of bmCreated as text
								set cMonth to text -2 thru -1 of ("0" & ((month of bmCreated) as integer))
								set cDay to text -2 thru -1 of ("0" & (day of bmCreated))
								set targetDate to cYear & "-" & cMonth & "-" & cDay
								set targetNote to my getOrCreateDailyNote(targetDB, destGroup, groupPath, targetDate)

								if targetNote is not missing value then
									set bmName to name of bmRecord
									set secSinceMidnight to time of bmCreated
									set cHour to secSinceMidnight div 3600
									set cMin to (secSinceMidnight mod 3600) div 60
									if cHour ≥ 12 then
										set ampm to "pm"
										if cHour > 12 then set cHour to cHour - 12
									else
										set ampm to "am"
										if cHour is 0 then set cHour to 12
									end if
									set timeStr to (cHour as text) & ":" & text -2 thru -1 of ("0" & (cMin as text)) & ampm
									set linkText to "- " & timeStr & ": [🔗 " & bmName & "](x-devonthink-item://" & bmUUID & ")"

									if (plain text of targetNote) does not contain bmUUID then
										my appendToSection(targetNote, sectionHeader, linkText & return)
									end if
									add custom meta data 1 for "DailyNoteLinked" to bmRecord
								else
									log message "Post-Enrich & Archive: daily note (" & targetDate & ".md) could not be created, skipping web clip link" info recName
								end if
							end if
						end if
					end if
				on error errMsg
					log message "Post-Enrich & Archive: web clip daily note link failed: " & errMsg info recName
				end try
			end if


			-- =============================================
			-- Step 4: Archive
			-- =============================================
			try
				move record theRecord to archiveGroup
				add custom meta data 0 for "NeedsProcessing" to theRecord
				my pipelineLog("Post-Enrich & Archive", "INFO", "archived", recName, recUUID)
			on error errMsg
				log message "Post-Enrich & Archive: archive failed: " & errMsg info recName
				my pipelineLog("Post-Enrich & Archive", "ERROR", "archive failed: " & errMsg, recName, recUUID)
			end try
		end repeat
	end tell
end performSmartRule

-- Forward an event to the centralized pipeline log. Fails silently if
-- the helper isn't present, so scripts remain functional before the
-- stow/setup step that puts it in place.
on pipelineLog(component, level, msg, recName, recUUID)
	try
		do shell script "$HOME/.local/bin/pipeline-log " & ¬
			quoted form of component & " " & ¬
			quoted form of level & " " & ¬
			quoted form of msg & " " & ¬
			quoted form of (recName as string) & " " & ¬
			quoted form of (recUUID as string)
	end try
end pipelineLog

-- Strip the "x-devonthink-item://" prefix off an item link, returning
-- just the UUID. Used by the web clip name propagation step.
on uuidFromItemLink(s)
	set s to s as text
	set prefixStr to "x-devonthink-item://"
	set prefixLen to length of prefixStr
	if (length of s) > prefixLen and (text 1 thru prefixLen of s) is prefixStr then
		return text (prefixLen + 1) thru -1 of s
	end if
	return s
end uuidFromItemLink

-- Replace a record's name only if it currently matches the ingester's
-- "No title" placeholder. Sets NameLocked=1 before the rename so
-- After Renaming, Lock Name (which matches NameLocked is Off) doesn't
-- race against this propagation; the sibling ends up in the same
-- protected state Enrich: AI Metadata gives the markdown.
on replaceIfPlaceholder(theRecord, newName)
	tell application id "DNtp"
		set currentName to name of theRecord as text
		if currentName is newName then
			add custom meta data 1 for "NameLocked" to theRecord
			return
		end if
		set lowerName to do shell script "printf '%s' " & quoted form of currentName & " | tr '[:upper:]' '[:lower:]'"
		if lowerName starts with "no title" or lowerName is "untitled" then
			add custom meta data 1 for "NameLocked" to theRecord
			set name of theRecord to newName
		end if
	end tell
end replaceIfPlaceholder

-- Appends contentBlock under the given section header in a daily note.
-- Creates the section at the end of the note if it doesn't exist yet.
on appendToSection(theNote, sectionHeader, contentBlock)
	tell application id "DNtp"
		set noteText to plain text of theNote

		set tmpPath to do shell script "mktemp /tmp/dt-daily.XXXXXX"
		set fileRef to open for access (POSIX file tmpPath) with write permission
		write noteText to fileRef as «class utf8»
		close access fileRef
		set newText to do shell script ¬
			"/usr/bin/python3 ~/.local/bin/insert-daily-note-section.py" & ¬
			" --header " & quoted form of sectionHeader & ¬
			" --content " & quoted form of contentBlock & ¬
			" < " & quoted form of tmpPath
		do shell script "rm -f " & quoted form of tmpPath

		set plain text of theNote to newText
	end tell
end appendToSection

-- Returns the daily note for dateStr (YYYY-MM-DD), creating it in destGroup
-- if it doesn't exist yet. The 6:15 AM launchd job (create-daily-note.sh)
-- normally seeds these, but an EventDate in the past or future, or a missed
-- run, can leave the target note absent; creating on demand keeps the
-- wikilink from being dropped. Mirrors create-daily-note.sh's content and
-- "Daily Note" tag so an on-demand note is indistinguishable from a seeded one.
on getOrCreateDailyNote(targetDB, destGroup, groupPath, dateStr)
	tell application id "DNtp"
		set noteFilename to dateStr & ".md"
		set existingNote to get record at (groupPath & "/" & noteFilename) in targetDB
		if existingNote is not missing value then return existingNote

		set headingDate to do shell script "date -j -f '%Y-%m-%d' " & quoted form of dateStr & " '+%A, %B %-d, %Y'"
		set noteContent to "# " & headingDate & return & return & "- " & return

		set newNote to create record with {name:dateStr, type:markdown} in destGroup
		set plain text of newNote to noteContent
		set tags of newNote to {"Daily Note"}
		return newNote
	end tell
end getOrCreateDailyNote
