-- Extract: Web Content
--
-- Handles Bookmark records arriving in 00_INBOX in one pass: cleans the
-- title, flags the record for later SingleFile capture (if URL present),
-- appends a wikilink to today's daily note, and archives to 99_ARCHIVE.
--
-- Bookmarks don't need AI enrichment, action-item extraction, or H1 sync,
-- so routing them through Post-Enrich & Archive just to archive them
-- forced us to set three fast-track flags (Recognized/Commented/AIEnriched)
-- purely to match its criteria, plus the record sat in 00_INBOX across
-- two rule firings. Each metadata write triggers a DT index update and a
-- DTTG sync event, and DT's UI can transiently double-render records
-- mid-mutation — the effect is amplified for phone-synced bookmarks where
-- several sync+index cycles overlap. Owning the bookmark's full journey
-- here cuts ~6 metadata writes down to ~3, and all of them happen inside
-- one AppleScript `tell` so DT has a better shot at coalescing.
--
-- Smart rule criteria:
--   Search in: 00_INBOX
--   NeedsProcessing is On
--   Recognized is Off
--   Kind is Bookmark
--   Trigger: On Import / Every Minute

on performSmartRule(theRecords)
	tell application id "DNtp"
		set archiveGroup to get record at "/99_ARCHIVE" in database "Lorebook"
		if archiveGroup is missing value then
			log message "Extract: Web Content — /99_ARCHIVE not found; aborting"
			return
		end if

		repeat with theRecord in theRecords
			set recName to name of theRecord as string
			set recUUID to uuid of theRecord
			try
				-- Clean up fullwidth-char substitutions (：→: ｜→|) and
				-- trailing "| Site Name" brand suffix. Bookmarks skip AI
				-- enrichment so this is the only chance to fix the name.
				try
					set cleanName to do shell script "printf '%s' " & quoted form of recName & " | ~/.local/bin/clean-web-title"
					if cleanName is not "" and cleanName is not recName then
						set name of theRecord to cleanName
						set recName to cleanName
					end if
				end try

				-- Flag for batch SingleFile capture if a URL exists.
				set recURL to URL of theRecord
				if recURL is not "" and recURL is not missing value then
					add custom meta data 1 for "NeedsSingleFile" to theRecord
				end if

				-- Append wikilink to today's daily note.
				my logBookmarkToDailyNote(theRecord)

				-- Archive last, and only clear NeedsProcessing on success so
				-- a failed move leaves the record for the next tick's retry.
				move record theRecord to archiveGroup
				add custom meta data 0 for "NeedsProcessing" to theRecord
				my pipelineLog("Extract: Web Content", "INFO", "archived bookmark", recName, recUUID)
			on error errMsg
				log message "Extract: Web Content failed: " & errMsg info recName
				my pipelineLog("Extract: Web Content", "ERROR", "failed: " & errMsg, recName, recUUID)
			end try
		end repeat
	end tell
end performSmartRule

-- Append a wikilink to today's daily note under "## Today's Notes".
-- Idempotent via DailyNoteLinked and a UUID-in-note check. Non-fatal —
-- if the daily note doesn't exist or the append fails, the record is
-- still archived normally.
on logBookmarkToDailyNote(theRecord)
	tell application id "DNtp"
		try
			set isLinked to (get custom meta data for "DailyNoteLinked" from theRecord)
			if isLinked is 1 then return

			set cDate to current date
			set cYear to year of cDate as text
			set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
			set cDay to text -2 thru -1 of ("0" & (day of cDate))
			set todayFilename to cYear & "-" & cMonth & "-" & cDay & ".md"
			set targetNote to get record at ("/10_DAILY/" & todayFilename) in database "Lorebook"
			if targetNote is missing value then return

			set docUUID to uuid of theRecord
			set noteText to plain text of targetNote
			if noteText contains docUUID then
				add custom meta data 1 for "DailyNoteLinked" to theRecord
				return
			end if

			set secSinceMidnight to time of cDate
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

			set recName to name of theRecord as string
			set linkText to "- " & timeStr & ": [🔗 " & recName & "](x-devonthink-item://" & docUUID & ")"

			set tmpPath to do shell script "mktemp /tmp/dt-extract-web.XXXXXX"
			set fileRef to open for access (POSIX file tmpPath) with write permission
			set eof of fileRef to 0
			write noteText to fileRef as «class utf8»
			close access fileRef

			set newText to do shell script ¬
				"/usr/bin/python3 $HOME/.local/bin/insert-daily-note-section.py" & ¬
				" --header " & quoted form of "## Today's Notes" & ¬
				" --content " & quoted form of (linkText & linefeed) & ¬
				" < " & quoted form of tmpPath without altering line endings
			do shell script "rm -f " & quoted form of tmpPath

			set plain text of targetNote to newText
			add custom meta data 1 for "DailyNoteLinked" to theRecord
		on error errMsg
			log message "Extract: Web Content — daily-note append failed: " & errMsg
			my pipelineLog("Extract: Web Content", "ERROR", "daily-note append failed: " & errMsg, "", "")
		end try
	end tell
end logBookmarkToDailyNote

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
