-- Process Jots
--
-- Handles jot documents created from the Drafts Quick Jot action.
-- Inserts each jot into the matching daily note body (before "## Today's
-- Notes") and trashes the jot document.
--
-- macOS fallback creates records with IsJot=1. iOS (DTTG) can't set
-- custom metadata via x-callback-url, so those arrive with a "Jot "
-- name prefix instead. The smart rule should use:
--
-- Smart Rule setup:
--   Trigger:    On Import, Every Minute
--   Conditions: Any of:
--                 - IsJot is On
--                 - Name begins with "Jot "
--               AND Kind is Markdown
--   Action:     Execute Script → this file
--
-- The Every Minute trigger retries jots that arrived when their target daily
-- note didn't exist yet (e.g. a jot created before 3am launchd fires, or on
-- a day the launchd job missed). Without it, such jots sit in the Global
-- Inbox forever since On Import only fires once.

on performSmartRule(theRecords)
	tell application id "DNtp"
		set dbName to "Lorebook"
		set groupPath to "/10_DAILY"
		set sectionHeader to "## Today's Notes"

		try
			set targetDB to database dbName
		on error
			log message "Process Jots: database " & dbName & " not found."
			return
		end try

		repeat with theRecord in theRecords
			set jotText to plain text of theRecord
			if jotText is "" then
				log message "Process Jots: empty jot, skipping"
			else
				-- Stable per-jot idempotency marker. Embed the source
				-- record's UUID as an HTML comment trailing the bullet so
				-- the "already imported" check matches on the marker rather
				-- than on the body text. The previous substring-on-body
				-- check collapsed two distinct jots that happened to share
				-- a bullet line (identical timestamp + text, e.g. duplicate
				-- Drafts sends or a race-fire of the Every-Minute trigger
				-- before the trash-move propagated). HTML comments are
				-- invisible in rendered Markdown.
				set jotMarker to "<!-- jot:" & (uuid of theRecord) & " -->"
				set jotLine to jotText & " " & jotMarker

				-- Use creation date to find the right daily note
				set cDate to creation date of theRecord
				set cYear to year of cDate as text
				set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
				set cDay to text -2 thru -1 of ("0" & (day of cDate))
				set targetFilename to cYear & "-" & cMonth & "-" & cDay & ".md"

				set targetNote to get record at (groupPath & "/" & targetFilename) in targetDB

				if targetNote is not missing value then
					-- Skip if already imported (idempotency by UUID marker)
					if (plain text of targetNote) does not contain jotMarker then
						-- Insertion logic lives in a standalone helper script
						-- (~/.local/bin/insert-jot-into-daily-note.py) rather
						-- than an inlined heredoc — the heredoc form was
						-- ~40 lines of Python encoded one-string-per-line
						-- with no syntax highlighting and no way to test
						-- outside of triggering an actual smart rule. The
						-- helper takes the note body on stdin, JOT_LINE and
						-- SECTION_HEADER via env, and prints the modified
						-- body on stdout. Wire format matches the prior
						-- inlined version verbatim.
						set pyHelper to (POSIX path of (path to home folder)) & ".local/bin/insert-jot-into-daily-note.py"

						set noteBody to plain text of targetNote
						set tmpPath to do shell script "mktemp /tmp/dt-jot.XXXXXX"
						-- Wrap tmpPath consumption in try/on error so the tempfile
						-- is removed even when the helper invocation or file I/O
						-- raises. Without this, an error mid-block leaves
						-- /tmp/dt-jot.XXXXXX behind until macOS's periodic /tmp
						-- sweep collects it (~3 days). The inner `close access`
						-- guard handles the case where `open for access` succeeded
						-- but `write` failed.
						set newBody to ""
						try
							set fileRef to open for access (POSIX file tmpPath) with write permission
							write noteBody to fileRef as «class utf8»
							close access fileRef

							set newBody to do shell script ¬
								"export JOT_LINE=" & quoted form of jotLine & ¬
								" && export SECTION_HEADER=" & quoted form of sectionHeader & ¬
								" && /usr/bin/python3 " & quoted form of pyHelper & ¬
								" < " & quoted form of tmpPath
						on error errMsg number errNum
							try
								close access (POSIX file tmpPath)
							end try
							do shell script "rm -f " & quoted form of tmpPath
							error errMsg number errNum
						end try
						do shell script "rm -f " & quoted form of tmpPath

						set plain text of targetNote to newBody
					end if

					move record theRecord to trash group of targetDB
				else
					log message "Process Jots: daily note " & targetFilename & " not found, skipping"
				end if
			end if
		end repeat
	end tell
end performSmartRule
