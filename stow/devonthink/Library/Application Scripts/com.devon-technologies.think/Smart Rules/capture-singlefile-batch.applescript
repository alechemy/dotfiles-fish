-- Capture: SingleFile Batch
--
-- Collects all bookmarks with NeedsSingleFile=1 and runs them through
-- the capture-with-singlefile script in a single browser session.
--
-- For each successful capture, this script also locates the imported
-- HTML record and sets cross-link metadata (WebClipSource on the HTML →
-- bookmark, WebClipSnapshot on the bookmark → HTML) so Process:
-- SingleFile Import can find the triggering bookmark deterministically
-- via the item link, rather than guessing by URL match.
--
-- Matching strategy: we snapshot the time before the batch runs, then
-- match imports in the same order the capture script returned them —
-- the Nth successful capture corresponds to the Nth-oldest HTML record
-- created after the snapshot, across Global Inbox and Lorebook/00_INBOX.
-- This is filename-agnostic, so it survives (a) the fullwidth-char
-- lookalikes SingleFile inserts into filenames, (b) Process: SingleFile
-- Import's clean-web-title rename racing our poll window, and (c)
-- DEVONthink's own `lookup records with file` quirks.
--
-- NeedsSingleFile is cleared on any successful capture; if a match
-- can't be found the HTML falls through to Process: SingleFile Import's
-- path-B flow (create fresh bookmark). Same as pre-refactor, not worse.
--
-- The resulting HTML files land in DEVONthink's inbox via the
-- ~/Downloads/SingleFile symlink.
--
-- Smart rule criteria:
--   NeedsSingleFile is On
--   Kind is Bookmark
--   Trigger: Manually / On Schedule (e.g. midnight)
--
-- Dependencies: capture-with-singlefile (drives Chromium via AppleScript + SingleFile ext)

on performSmartRule(theRecords)
	tell application id "DNtp"
		if (count of theRecords) is 0 then return

		-- Build a temp file with one URL per line, and a parallel list
		-- of records so we can match results back
		set tmpFile to do shell script "mktemp /tmp/singlefile-urls.XXXXXX"
		set recList to {}
		set urlList to {}

		repeat with theRecord in theRecords
			set recURL to URL of theRecord
			if recURL is not "" and recURL is not missing value then
				do shell script "echo " & quoted form of recURL & " >> " & quoted form of tmpFile
				set end of recList to theRecord
				set end of urlList to recURL
			else
				-- No URL — just clear the flag
				add custom meta data 0 for "NeedsSingleFile" to theRecord
			end if
		end repeat

		if (count of urlList) is 0 then
			do shell script "rm -f " & quoted form of tmpFile
			return
		end if

		-- Snapshot the time just before batch starts. Any HTML record
		-- created after this timestamp is a candidate import.
		set batchStartDate to current date

		-- Run the batch capture — one browser session for all URLs.
		-- Output: one line per URL, either a file path (success) or
		-- "FAIL\tURL\tReason" (failure), in input order.
		set envPrefix to "export PATH=/opt/homebrew/bin:$HOME/.local/share/mise/bin:$HOME/.local/bin:$PATH && "
		try
			set captureOutput to do shell script envPrefix & "capture-with-singlefile --url-file " & quoted form of tmpFile
		on error errMsg number errNum
			-- Partial output may still contain successful captures
			set captureOutput to errMsg
		end try
		do shell script "rm -f " & quoted form of tmpFile

		-- Walk results in order. capture-with-singlefile processes URLs
		-- serially and emits one line per URL in the same order, so the
		-- Nth successful result corresponds to the Nth-oldest HTML
		-- record imported after batchStartDate.
		set outputLines to paragraphs of captureOutput
		set lineIdx to 1
		set lastMatchDate to batchStartDate

		repeat with i from 1 to count of urlList
			if lineIdx > (count of outputLines) then exit repeat
			set outputLine to item lineIdx of outputLines
			set theRecord to item i of recList

			if outputLine starts with "/" then
				-- Success — always clear NeedsSingleFile so we don't re-capture
				add custom meta data 0 for "NeedsSingleFile" to theRecord

				-- Find the next HTML record imported after the previous
				-- match (or batchStartDate if this is the first). Polls
				-- briefly in case DT hasn't finished ingesting yet.
				set htmlRecord to my findNextImportedHTML(lastMatchDate)

				if htmlRecord is not missing value then
					set lastMatchDate to creation date of htmlRecord
					set bmLink to "x-devonthink-item://" & (uuid of theRecord)
					set htmlLink to "x-devonthink-item://" & (uuid of htmlRecord)
					add custom meta data bmLink for "WebClipSource" to htmlRecord
					add custom meta data htmlLink for "WebClipSnapshot" to theRecord
				else
					log message "Capture: SingleFile Batch — no imported HTML found after " & (lastMatchDate as string) & " for " & outputLine & "; will be processed as manual save" info (name of theRecord as string)
				end if
			else
				-- Failure — log and leave flag for retry
				log message "Capture: SingleFile Batch — " & outputLine info (name of theRecord as string)
			end if

			set lineIdx to lineIdx + 1
		end repeat
	end tell
end performSmartRule

-- Poll for the next HTML record imported strictly after `afterDate`.
-- Checks Global Inbox + Lorebook/00_INBOX and returns the oldest match.
-- Returns missing value if nothing appears within ~30 seconds.
on findNextImportedHTML(afterDate)
	tell application id "DNtp"
		set pollDeadline to (current date) + 30
		repeat while (current date) < pollDeadline
			set chosen to missing value
			set chosenDate to missing value

			set searchGroups to {}
			try
				set end of searchGroups to inbox
			end try
			try
				set end of searchGroups to (get record at "/00_INBOX" in database "Lorebook")
			end try

			repeat with g in searchGroups
				try
					repeat with r in (children of g)
						try
							if (type of r) as string is "html" then
								set d to creation date of r
								if d > afterDate then
									if chosenDate is missing value or d < chosenDate then
										set chosen to contents of r
										set chosenDate to d
									end if
								end if
							end if
						end try
					end repeat
				end try
			end repeat

			if chosen is not missing value then return chosen
			delay 2
		end repeat
		return missing value
	end tell
end findNextImportedHTML
