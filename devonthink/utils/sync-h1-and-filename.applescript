use AppleScript version "2.4"
use scripting additions

-- sync-h1-and-filename.applescript
--
-- Purpose:
-- - Unified Smart Rule script that keeps the Markdown H1 and the
--   DEVONthink record name in sync.
--
-- Behaviour (evaluated in order):
--   1. H1 exists and matches filename → do nothing.
--   2. H1 exists and differs from filename → rename the record to match the H1.
--   3. No H1 exists → inject "# <filename>" into the document body.
--
-- Parsing notes:
-- - YAML frontmatter (--- … ---) at the top of the file is skipped.
-- - Fenced code blocks (``` / ~~~) are skipped.
-- - The first line matching ^#\s+.+$ outside those regions is treated as the H1.
-- - When injecting an H1, it is placed immediately after frontmatter (if any),
--   or at the very top of the file.
--
-- Recommended Smart Rule:
-- - Name: "Sync H1 ↔ Filename"
-- - Trigger: On modification / On saving (or scheduled)
-- - Condition: Kind is Markdown (or extension is md)
-- - Action: Execute Script… (this file)

-- ======== Configuration ========

-- Max length of the resulting record name
property MAX_TITLE_LENGTH : 160

-- Characters that are problematic in names/paths
property INVALID_CHARS : {":", "/", "\\", "|", "\t", "\r"}

-- If true, treat names that differ only in case as matching
property IGNORE_CASE_DIFFERENCES : true

-- Set to true to log diagnostic messages to DEVONthink's Log panel
property DEBUG_LOGGING : false

-- ======== Entry Point ========

on performSmartRule(theRecords)
	repeat with r in theRecords
		try
			my syncRecord(r)
		on error errMsg number errNum
			-- Swallow per-record errors so one bad record doesn't break the batch
			my logDebug("Error (" & errNum & "): " & errMsg)
		end try
	end repeat
end performSmartRule

-- ======== Core Logic ========

on syncRecord(r)
	-- 1. Read filename
	set recName to ""
	tell application id "DNtp"
		try
			set recName to (name of r) as text
		end try
	end tell
	if recName is "" then
		my logDebug("Skipping: empty record name")
		return
	end if

	-- 2. Read body text
	set bodyText to my getRecordPlainText(r)
	if bodyText is missing value then set bodyText to ""

	-- Strip BOM if present
	if (count of bodyText) > 0 then
		if id of first character of bodyText is 65279 then
			if (count of bodyText) > 1 then
				set bodyText to text 2 thru -1 of bodyText
			else
				set bodyText to ""
			end if
		end if
	end if

	-- 3. Parse: locate the H1 and note where frontmatter ends
	set parseResult to my parseH1(bodyText)
	set h1Value to h1 of parseResult
	-- h1LineIndex is the 1-based line number of the H1 (0 if none found)
	-- frontmatterEndIndex is the 1-based line number of the first content line after frontmatter (1 if no frontmatter)

	-- 4. Decide action
	if h1Value is missing value then
		-- ── No H1 found → inject one from the filename ──
		my logDebug("No H1 found – injecting from filename")
		my injectH1(r, recName, bodyText, frontmatterEndIndex of parseResult)
	else
		-- ── H1 exists → check if rename is needed ──
		set sanitized to my sanitizeTitle(h1Value)
		if sanitized is "" then
			-- H1 sanitized to nothing; treat as absent
			my logDebug("H1 sanitized to empty – injecting from filename")
			my injectH1(r, recName, bodyText, frontmatterEndIndex of parseResult)
			return
		end if

		-- Compare
		set namesMatch to false
		if IGNORE_CASE_DIFFERENCES then
			ignoring case
				if recName is sanitized then set namesMatch to true
			end ignoring
		else
			if recName is sanitized then set namesMatch to true
		end if

		if namesMatch then
			my logDebug("H1 and filename already match – nothing to do")
		else
			-- Rename the record to match the H1
			tell application id "DNtp"
				try
					set name of r to sanitized
					my logDebug("Renamed: \"" & recName & "\" → \"" & sanitized & "\"")
				end try
			end tell
		end if
	end if
end syncRecord

-- ======== H1 Injection ========

on injectH1(r, recName, bodyText, insertAtLine)
	(*
	Inserts "# <recName>" into the body text.
	- If the file is effectively empty, creates "# name\n\n".
	- Otherwise inserts the H1 line at `insertAtLine` (after frontmatter).
	*)
	if my isEffectivelyEmpty(bodyText) then
		set newContent to "# " & recName & linefeed & linefeed
		my setRecordText(r, newContent, "Inserted H1 into empty record")
		return
	end if

	set linesList to my splitLines(bodyText)

	if insertAtLine > (count of linesList) then
		-- Frontmatter fills the whole file; append H1 after it
		set end of linesList to ""
		set end of linesList to "# " & recName
		set end of linesList to ""
	else if insertAtLine is 1 then
		-- No frontmatter – prepend
		set newContent to "# " & recName & linefeed & linefeed & bodyText
		my setRecordText(r, newContent, "Prepended H1 to content")
		return
	else
		-- Insert H1 after frontmatter with a blank line before it
		set beforeLines to items 1 thru (insertAtLine - 1) of linesList
		set afterLines to {}
		if insertAtLine ≤ (count of linesList) then
			set afterLines to items insertAtLine thru -1 of linesList
		end if

		-- Build: frontmatter … blank line, H1, blank line, rest
		set end of beforeLines to ""
		set end of beforeLines to "# " & recName
		-- Only add a blank separator if the next line isn't already blank
		if (count of afterLines) > 0 then
			set nextLine to my trimWhitespace(item 1 of afterLines)
			if nextLine is not "" then
				set end of beforeLines to ""
			end if
		end if

		set linesList to beforeLines & afterLines
	end if

	set newContent to my joinLines(linesList)
	my setRecordText(r, newContent, "Injected H1 after frontmatter")
end injectH1

-- ======== Markdown Parsing ========

on parseH1(t)
	(*
	Parses the text and returns a record:
	  { h1: <string or missing value>,
	    h1LineIndex: <integer>,
	    frontmatterEndIndex: <integer> }

	- h1: the trimmed text of the first H1 (or missing value)
	- h1LineIndex: 1-based line number of the H1 line (0 if none)
	- frontmatterEndIndex: 1-based index of the first line after frontmatter
	                       (1 if no frontmatter is present)
	*)
	set linesList to my splitLines(t)
	set lineCount to (count of linesList)

	if lineCount is 0 then
		return {h1:missing value, h1LineIndex:0, frontmatterEndIndex:1}
	end if

	set i to 1
	set fmEnd to 1

	-- Skip YAML frontmatter if present at top
	set firstLine to my trimWhitespace(item 1 of linesList)
	if firstLine is "---" then
		set i to 2
		repeat while i ≤ lineCount
			if (my trimWhitespace(item i of linesList)) is "---" then
				set i to i + 1
				set fmEnd to i
				exit repeat
			end if
			set i to i + 1
		end repeat
		if fmEnd is 1 then
			-- Never found closing ---; treat entire file as frontmatter
			set fmEnd to lineCount + 1
			set i to lineCount + 1
		end if
	end if

	-- Search for first non-empty H1, respecting fenced code blocks
	set inCodeBlock to false

	repeat while i ≤ lineCount
		set ln to item i of linesList
		set lnTrim to my trimWhitespace(ln)

		-- Toggle code block state
		if lnTrim begins with "```" or lnTrim begins with "~~~" then
			set inCodeBlock to not inCodeBlock
		else if not inCodeBlock and lnTrim begins with "# " then
			set candidate to text 3 thru -1 of lnTrim
			set candidate to my trimWhitespace(candidate)
			if candidate is not "" then
				return {h1:candidate, h1LineIndex:i, frontmatterEndIndex:fmEnd}
			end if
		end if

		set i to i + 1
	end repeat

	return {h1:missing value, h1LineIndex:0, frontmatterEndIndex:fmEnd}
end parseH1

-- ======== DEVONthink Record Access ========

on getRecordPlainText(r)
	tell application id "DNtp"
		try
			set t to (plain text of r) as text
			if t is not "" then return t
		end try
		try
			set t to (content of r) as text
			if t is not "" then return t
		end try
	end tell
	return missing value
end getRecordPlainText

on setRecordText(r, t, logMsg)
	tell application id "DNtp"
		try
			set plain text of r to t
			my logDebug(logMsg & ": \"" & (name of r) & "\"")
		on error errMsg
			my logDebug("Failed to set text: " & errMsg)
		end try
	end tell
end setRecordText

-- ======== Sanitization ========

on sanitizeTitle(s)
	set t to my trimWhitespace(s)

	-- Collapse internal whitespace runs to a single space
	set t to my collapseWhitespace(t)

	-- Replace invalid characters
	repeat with ch in INVALID_CHARS
		set t to my replaceText(ch as text, " ", t)
	end repeat

	-- Remove leading/trailing punctuation clutter
	set t to my stripEdgePunctuation(t)

	-- Clamp length
	if (count of t) > MAX_TITLE_LENGTH then
		set t to text 1 thru MAX_TITLE_LENGTH of t
		set t to my stripEdgePunctuation(t)
	end if

	return t
end sanitizeTitle

-- ======== Helpers ========

on isEffectivelyEmpty(t)
	if t is "" then return true
	return ((my trimWhitespace(t)) is "")
end isEffectivelyEmpty

on splitLines(t)
	set t2 to my replaceText(return, linefeed, t)
	set t2 to my replaceText(character id 13, linefeed, t2)

	set AppleScript's text item delimiters to linefeed
	set itemsList to text items of t2
	set AppleScript's text item delimiters to ""
	return itemsList
end splitLines

on joinLines(l)
	set AppleScript's text item delimiters to linefeed
	set t to l as text
	set AppleScript's text item delimiters to ""
	return t
end joinLines

on trimWhitespace(t)
	set s to t as text
	repeat while s begins with " " or s begins with tab
		if (count of s) = 0 then exit repeat
		set s to text 2 thru -1 of s
	end repeat
	repeat while s ends with " " or s ends with tab
		if (count of s) = 0 then exit repeat
		set s to text 1 thru -2 of s
	end repeat
	return s
end trimWhitespace

on collapseWhitespace(t)
	set s to my replaceText(tab, " ", t)
	repeat while s contains "  "
		set s to my replaceText("  ", " ", s)
	end repeat
	return s
end collapseWhitespace

on stripEdgePunctuation(t)
	set s to t as text
	set punct to {".", ",", ";", ":", "-", "–", "—", "!", "?", "\"", "'", "(", ")", "[", "]", "{", "}"}

	repeat
		set changed to false
		set s2 to my trimWhitespace(s)
		if s2 is not s then
			set s to s2
			set changed to true
		end if
		if s is "" then exit repeat

		repeat with p in punct
			set ptxt to p as text
			if s begins with ptxt then
				if (count of s) = 1 then
					set s to ""
				else
					set s to text 2 thru -1 of s
				end if
				set changed to true
				exit repeat
			end if
		end repeat
		if s is "" then exit repeat

		repeat with p in punct
			set ptxt to p as text
			if s ends with ptxt then
				if (count of s) = 1 then
					set s to ""
				else
					set s to text 1 thru -2 of s
				end if
				set changed to true
				exit repeat
			end if
		end repeat

		if changed is false then exit repeat
	end repeat

	return my trimWhitespace(s)
end stripEdgePunctuation

on replaceText(findText, replaceWith, sourceText)
	set AppleScript's text item delimiters to findText
	set parts to text items of sourceText
	set AppleScript's text item delimiters to replaceWith
	set outText to parts as text
	set AppleScript's text item delimiters to ""
	return outText
end replaceText

-- ======== Logging ========

on logDebug(msg)
	if not DEBUG_LOGGING then return
	tell application id "DNtp"
		log message "[sync-h1-and-filename] " & msg
	end tell
end logDebug
