-- Adopt Meeting Note
--
-- Adopts markdown notes born from the briefing's create-on-click links
-- (dt-morning-brief renders every note-less event title as a createMarkdown
-- URL that creates "YYYY-MM-DD <event title>" in /99_ARCHIVE, tagged
-- "Meeting Note"). A URL command cannot set custom metadata, so this rule
-- finishes the job: it derives EventDate and the LinkedEvent key from the
-- name (brief_events.py adopt-key), stamps DocumentType "Meeting Notes" so
-- the entity layer sweeps the note as a source, pre-sets DailyNoteLinked so
-- it never double-lists under ## Today's Notes, and swaps that day's
-- briefing create-link for the note's item link in place — clicking the
-- title twice then opens the note instead of minting a duplicate.
-- dt-morning-brief re-derives the same link from LinkedEvent on every
-- regeneration, so the splice here only covers the gap until the next run.
--
-- Smart rule criteria:
--   Search in: Lorebook (database)
--   Kind is Markdown
--   Tag is: Meeting Note
--   Trigger: On Creation (plus Every Minute as catch-up)

on performSmartRule(theRecords)
	-- [follower-guard] only the DEVONthink pipeline driver mutates documents (see should-run-dt-driver)
	try
		do shell script "$HOME/.local/bin/should-run-dt-driver"
	on error
		return
	end try
	tell application id "DNtp"
		set targetDB to database "Lorebook"
		set groupPath to "/10_DAILY"
		repeat with theRecord in theRecords
			set recName to name of theRecord
			try
				set existingKey to ""
				try
					set existingKey to (get custom meta data for "LinkedEvent" from theRecord) as text
					if existingKey is "missing value" then set existingKey to ""
				end try
				if existingKey is "" then
					set keyOut to do shell script "/usr/bin/python3 $HOME/.local/bin/brief_events.py adopt-key " & quoted form of recName without altering line endings
					if keyOut is "" then
						log message "Adopt Meeting Note: name has no YYYY-MM-DD prefix, cannot derive its event" info recName
					else
						set oldTID to AppleScript's text item delimiters
						set AppleScript's text item delimiters to tab
						set keyParts to text items of keyOut
						set AppleScript's text item delimiters to oldTID
						set noteDate to item 1 of keyParts as text
						set noteKey to item 2 of keyParts as text

						add custom meta data noteDate for "EventDate" to theRecord
						add custom meta data noteKey for "LinkedEvent" to theRecord
						add custom meta data "Meeting Notes" for "DocumentType" to theRecord
						add custom meta data 1 for "DailyNoteLinked" to theRecord

						set docUUID to uuid of theRecord
						set dailyNote to missing value
						try
							set dailyNote to get record at (groupPath & "/" & noteDate) in targetDB
						end try
						if dailyNote is not missing value then
							set dailyText to plain text of dailyNote
							set tmpNote to do shell script "mktemp /tmp/dt-adopt.XXXXXX"
							set fileRef to open for access (POSIX file tmpNote) with write permission
							write dailyText to fileRef as «class utf8»
							close access fileRef
							set newText to do shell script "/usr/bin/python3 $HOME/.local/bin/brief_events.py link-title " & quoted form of noteDate & " " & quoted form of noteKey & " " & quoted form of docUUID & " < " & quoted form of tmpNote without altering line endings
							do shell script "rm -f " & quoted form of tmpNote
							if newText is not dailyText then
								set plain text of dailyNote to newText
							end if
						end if
					end if
				end if
			on error errMsg
				log message "Adopt Meeting Note: " & errMsg info recName
			end try
		end repeat
	end tell
end performSmartRule
