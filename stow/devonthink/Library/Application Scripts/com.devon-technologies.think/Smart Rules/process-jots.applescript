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
			set jotLine to plain text of theRecord
			if jotLine is "" then
				log message "Process Jots: empty jot, skipping"
			else
				-- Use creation date to find the right daily note
				set cDate to creation date of theRecord
				set cYear to year of cDate as text
				set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
				set cDay to text -2 thru -1 of ("0" & (day of cDate))
				set targetFilename to cYear & "-" & cMonth & "-" & cDay & ".md"

				set targetNote to get record at (groupPath & "/" & targetFilename) in targetDB

				if targetNote is not missing value then
					-- Skip if already present (idempotency)
					if (plain text of targetNote) does not contain jotLine then
						set pyScript to "import sys, os, re" & linefeed & ¬
							"note = sys.stdin.read()" & linefeed & ¬
							"jot = os.environ['JOT_LINE']" & linefeed & ¬
							"marker = os.environ['SECTION_HEADER']" & linefeed & ¬
							"lines = note.splitlines()" & linefeed & ¬
							"empty_bullet = re.compile(r'^\\s*[-*]\\s*$')" & linefeed & ¬
							"content_bullet = re.compile(r'^\\s*[-*]\\s+\\S')" & linefeed & ¬
							"h2 = None" & linefeed & ¬
							"for i, l in enumerate(lines):" & linefeed & ¬
							"    if l.strip() == marker:" & linefeed & ¬
							"        h2 = i" & linefeed & ¬
							"        break" & linefeed & ¬
							"if h2 is None:" & linefeed & ¬
							"    lines += ['', jot]" & linefeed & ¬
							"else:" & linefeed & ¬
							"    last_content = None" & linefeed & ¬
							"    for i in range(h2 - 1, -1, -1):" & linefeed & ¬
							"        if content_bullet.match(lines[i]):" & linefeed & ¬
							"            last_content = i" & linefeed & ¬
							"            break" & linefeed & ¬
							"    if last_content is not None:" & linefeed & ¬
							"        insert_at = last_content + 1" & linefeed & ¬
							"        while insert_at < h2 and re.match(r'^[ \\t]', lines[insert_at]):" & linefeed & ¬
							"            insert_at += 1" & linefeed & ¬
							"        lines.insert(insert_at, jot)" & linefeed & ¬
							"    else:" & linefeed & ¬
							"        placeholder = None" & linefeed & ¬
							"        for i in range(h2 - 1, -1, -1):" & linefeed & ¬
							"            if empty_bullet.match(lines[i]):" & linefeed & ¬
							"                placeholder = i" & linefeed & ¬
							"                break" & linefeed & ¬
							"        if placeholder is not None:" & linefeed & ¬
							"            lines[placeholder] = jot" & linefeed & ¬
							"        else:" & linefeed & ¬
							"            ins = h2" & linefeed & ¬
							"            while ins > 0 and lines[ins - 1].strip() == '':" & linefeed & ¬
							"                ins -= 1" & linefeed & ¬
							"            lines[ins:h2] = ['', jot, '']" & linefeed & ¬
							"print('\\n'.join(lines), end='')"

						set noteBody to plain text of targetNote
						set tmpPath to do shell script "mktemp /tmp/dt-jot.XXXXXX"
						set fileRef to open for access (POSIX file tmpPath) with write permission
						write noteBody to fileRef as «class utf8»
						close access fileRef

						set newBody to do shell script ¬
							"export JOT_LINE=" & quoted form of jotLine & ¬
							" && export SECTION_HEADER=" & quoted form of sectionHeader & ¬
							" && /usr/bin/python3 -c " & quoted form of pyScript & ¬
							" < " & quoted form of tmpPath
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
