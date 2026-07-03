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
  -- [follower-guard] only the DEVONthink pipeline driver mutates documents (see should-run-dt-driver)
  try
    do shell script "$HOME/.local/bin/should-run-dt-driver"
  on error
    return
  end try
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
				-- Dedup: if another bookmark with the same URL exists outside
				-- 00_INBOX (i.e. already processed + archived), this arrival
				-- is a duplicate. Delete it and move on. Concurrent arrivals
				-- (both still in 00_INBOX) intentionally fall through so we
				-- don't race-delete a sibling.
				set recURL to URL of theRecord
				if recURL is not "" and recURL is not missing value then
					set dupeUUID to my findArchivedDuplicate(recURL, recUUID)
					if dupeUUID is not "" then
						my pipelineLog("Extract: Web Content", "INFO", "deleting duplicate bookmark (existing=" & dupeUUID & ")", recName, recUUID)
						delete record theRecord
						-- raise a sentinel so the outer try skips the remaining
						-- per-record work and the outer loop moves to the next record
						error "__DUPE_SKIPPED__"
					end if
				end if

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

				-- Flag for batch SingleFile capture if a URL exists, unless
				-- the domain is on the skip list (youtube, spotify, etc. —
				-- see ~/.config/devonthink-pipeline/singlefile-skip-domains.txt).
				-- Skipped bookmarks get SkipSingleFile=1 for visibility; flip
				-- it off + set NeedsSingleFile=1 by hand to force a capture.
				set recURL to URL of theRecord
				if recURL is not "" and recURL is not missing value then
					set skipHelper to (POSIX path of (path to home folder)) & ".local/bin/should-skip-singlefile"
					set skipStatus to do shell script quoted form of skipHelper & " " & quoted form of recURL & "; echo $?"
					if skipStatus is "0" then
						add custom meta data 1 for "SkipSingleFile" to theRecord
					else
						add custom meta data 1 for "NeedsSingleFile" to theRecord
					end if
				end if

				-- Append wikilink to today's daily note.
				my logBookmarkToDailyNote(theRecord)

				-- Archive last, and only clear NeedsProcessing on success so
				-- a failed move leaves the record for the next tick's retry.
				move record theRecord to archiveGroup
				add custom meta data 0 for "NeedsProcessing" to theRecord
				my pipelineLog("Extract: Web Content", "INFO", "archived bookmark", recName, recUUID)
			on error errMsg
				if errMsg is "__DUPE_SKIPPED__" then
					-- already logged + deleted; fall through to next record
				else
					log message "Extract: Web Content failed: " & errMsg info recName
					my pipelineLog("Extract: Web Content", "ERROR", "failed: " & errMsg, recName, recUUID)
				end if
			end try
		end repeat
	end tell
end performSmartRule

-- Return the UUID of another bookmark in Lorebook with the same URL that
-- lives outside 00_INBOX (already archived / processed), or "" if none.
-- Used by the dedup guard at the top of performSmartRule — matches on URL
-- only, excludes the incoming record itself, and intentionally ignores
-- other 00_INBOX residents so concurrent arrivals don't race-delete each
-- other.
on findArchivedDuplicate(recURL, recUUID)
	tell application id "DNtp"
		try
			set candidates to lookup records with URL recURL in database "Lorebook"
			repeat with candidate in candidates
				if (uuid of candidate) is not recUUID and (type of candidate) is bookmark then
					set candLocation to location of candidate as text
					if candLocation does not start with "/00_INBOX" then
						return uuid of candidate
					end if
				end if
			end repeat
		end try
		return ""
	end tell
end findArchivedDuplicate

-- Append a wikilink to today's daily note under "## Today's Notes".
-- Idempotent via DailyNoteLinked and a UUID-in-note check. Non-fatal —
-- if the append fails, the record is still archived normally. The note is
-- created on demand: bookmarks processed between midnight and the 06:15
-- seeder would otherwise drop the link permanently (archived with
-- NeedsProcessing=0, nothing ever retries).
on logBookmarkToDailyNote(theRecord)
	tell application id "DNtp"
		try
			set isLinked to (get custom meta data for "DailyNoteLinked" from theRecord)
			if isLinked is 1 then return

			set cDate to current date
			set cYear to year of cDate as text
			set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
			set cDay to text -2 thru -1 of ("0" & (day of cDate))
			set todayStr to cYear & "-" & cMonth & "-" & cDay
			set targetDB to database "Lorebook"
			set dailyGroup to get record at "/10_DAILY" in targetDB
			if dailyGroup is missing value then return
			set targetNote to my getOrCreateDailyNote(targetDB, dailyGroup, "/10_DAILY", todayStr)
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

-- Returns the daily note for dateStr (YYYY-MM-DD), creating it in destGroup
-- if it doesn't exist yet. The 6:15 AM launchd job (create-daily-note.sh)
-- normally seeds these, but bookmarks arriving between midnight and 06:15
-- hit this rule before the note exists; creating on demand keeps the
-- wikilink from being dropped. Mirrors create-daily-note.sh's content and
-- "Daily Note" tag so an on-demand note is indistinguishable from a seeded
-- one. Same handler as Post-Enrich & Archive's.
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
