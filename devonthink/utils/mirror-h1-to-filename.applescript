use AppleScript version "2.4"
use scripting additions

-- rename_record_from_h1.applescript
--
-- Purpose:
-- - Rename DEVONthink Markdown records based on the first non-empty H1 in their content.
-- - H1 is defined as a line that matches: ^#\s+(.+)$ (i.e., "# " followed by non-empty text).
-- - Skips YAML frontmatter blocks at the top of the file (--- ... ---).
-- - If no non-empty H1 is found, does nothing for that record.
--
-- Notes:
-- - DEVONthink’s AppleScript dictionary differs across versions. This script is defensive and will no-op
--   if it can’t read the record’s plain text.
-- - Smart Rules typically run “On import” and “On modification” (not literally “before saving” / “after saving”).
--
-- Recommended Smart Rule:
-- - Name: “Rename Markdown from H1”
-- - Trigger: On modification (or scheduled)
-- - Scope: Database or specific groups (e.g., 00_INBOX, 99_ARCHIVE)
-- - Condition: Kind is Markdown (or extension is md)
-- - Action: Execute Script… (this file)

-- ======== Configuration ========

-- Max length of the resulting record name (DEVONthink tolerates long names, but Finder/UI can get awkward)
property MAX_TITLE_LENGTH : 160

-- Characters that are commonly problematic in names/paths or annoying in UI
property INVALID_CHARS : {":", "/", "\\", "|", "\t", "\r"}

-- If true, skip rename when only the case differs (e.g., "My Title" vs "my title")
property IGNORE_CASE_DIFFERENCES : true

-- Set to true to log diagnostic messages to DEVONthink's Log panel
property DEBUG_LOGGING : false

-- ======== Entry Point ========

on performSmartRule(theRecords)
	-- DEVONthink passes a list of records
	repeat with r in theRecords
		try
			my renameRecordFromH1(r)
		on error errMsg number errNum
			-- Intentionally swallow per-record errors so one bad record doesn't break the whole rule run.
			-- If you want debugging, temporarily uncomment the next line:
			-- display dialog ("rename_record_from_h1 error (" & errNum & "): " & errMsg)
		end try
	end repeat
end performSmartRule

-- ======== Core Logic ========

on renameRecordFromH1(r)
	-- Read the record's plain text. If not available, do nothing.
	set bodyText to my getRecordPlainText(r)
	if bodyText is missing value then
		my logDebug("Skipping: Could not read plain text")
		return
	end if

	-- Extract first non-empty H1 (skipping YAML frontmatter if present).
	set h1 to my firstNonEmptyH1(bodyText)
	if h1 is missing value then
		my logDebug("Skipping: No H1 found")
		return
	end if

	-- Normalize/sanitize into a safe record name.
	set newName to my sanitizeTitle(h1)
	if newName is "" then
		my logDebug("Skipping: H1 sanitized to empty string")
		return
	end if

	-- Avoid needless renames.
	set currentName to ""
	tell application id "DNtp"
		try
			set currentName to (name of r) as text
		end try
	end tell

	if IGNORE_CASE_DIFFERENCES then
		-- Compare case-insensitively to avoid churn when only case differs
		ignoring case
			if currentName is newName then
				my logDebug("Skipping: Name already matches (case-insensitive)")
				return
			end if
		end ignoring
	else
		if currentName is newName then
			my logDebug("Skipping: Name already matches")
			return
		end if
	end if

	-- Apply rename.
	tell application id "DNtp"
		try
			set name of r to newName
			my logDebug("Renamed: \"" & currentName & "\" → \"" & newName & "\"")
		end try
	end tell
end renameRecordFromH1

-- ======== DEVONthink Record Access ========

on getRecordPlainText(r)
	(*
	Attempts common ways to retrieve Markdown text from a DEVONthink record.

	DEVONthink typically exposes:
	- 'plain text of record' for text-based records
	Some builds also expose 'content' as rich text/RTF or different accessors.

	This function tries 'plain text' first and falls back to other possibilities.
	*)

	tell application id "DNtp"
		-- Try plain text accessor
		try
			set t to (plain text of r) as text
			if t is not "" then return t
		end try

		-- Try 'content' accessor (may exist in some dictionaries)
		try
			set t to (content of r) as text
			if t is not "" then return t
		end try
	end tell

	-- No supported accessor found
	return missing value
end getRecordPlainText

-- ======== Markdown Parsing ========

on firstNonEmptyH1(t)
	(*
	Returns the first non-empty H1 found in the text.
	- Skips optional YAML frontmatter if it starts at the beginning with '---'
	- Skips fenced code blocks (``` or ~~~)
	- Finds lines matching: '# ' + non-empty title
	- Returns the captured title (trimmed)
	*)
	set linesList to my splitLines(t)
	if (count of linesList) is 0 then return missing value

	set i to 1

	-- Skip UTF-8 BOM if present on first line
	set firstLine to item 1 of linesList
	if firstLine begins with (character id 65279) then
		set item 1 of linesList to text 2 thru -1 of firstLine
	end if

	-- Skip YAML frontmatter if present at top
	if (item 1 of linesList) is "---" then
		set i to 2
		repeat while i ≤ (count of linesList)
			if (item i of linesList) is "---" then
				set i to i + 1
				exit repeat
			end if
			set i to i + 1
		end repeat
	end if

	-- Search for first non-empty H1, skipping fenced code blocks
	set inCodeBlock to false

	repeat while i ≤ (count of linesList)
		set ln to item i of linesList
		set lnTrim to my trimWhitespace(ln)

		-- Toggle code block state when encountering fences (``` or ~~~)
		if lnTrim begins with "```" or lnTrim begins with "~~~" then
			set inCodeBlock to not inCodeBlock
		else
			-- Only match H1 outside of code blocks
			if not inCodeBlock and lnTrim begins with "# " then
				set candidate to text 3 thru -1 of lnTrim
				set candidate to my trimWhitespace(candidate)
				if candidate is not "" then return candidate
			end if
		end if

		set i to i + 1
	end repeat

	return missing value
end firstNonEmptyH1

-- ======== Sanitization ========

on sanitizeTitle(s)
	set t to my trimWhitespace(s)

	-- Collapse internal whitespace runs to a single space
	set t to my collapseWhitespace(t)

	-- Replace invalid characters
	repeat with ch in INVALID_CHARS
		set t to my replaceText(ch as text, " ", t)
	end repeat

	-- Remove leading/trailing punctuation-ish clutter
	set t to my stripEdgePunctuation(t)

	-- Clamp length
	if (count of t) > MAX_TITLE_LENGTH then
		set t to text 1 thru MAX_TITLE_LENGTH of t
		set t to my stripEdgePunctuation(t)
	end if

	return t
end sanitizeTitle

-- ======== Helpers ========

on splitLines(t)
	-- Normalize line endings to LF then split
	set t2 to my replaceText(return, linefeed, t)
	set t2 to my replaceText(character id 13, linefeed, t2)

	set AppleScript's text item delimiters to linefeed
	set itemsList to text items of t2
	set AppleScript's text item delimiters to ""
	return itemsList
end splitLines

on trimWhitespace(t)
	set s to t as text
	-- Trim spaces and tabs
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
	-- Collapse runs of spaces/tabs to a single space
	set s to t as text
	-- Replace tabs with spaces first
	set s to my replaceText(tab, " ", s)

	-- Iteratively collapse double spaces
	repeat while s contains "  "
		set s to my replaceText("  ", " ", s)
	end repeat
	return s
end collapseWhitespace

on stripEdgePunctuation(t)
	set s to t as text

	-- Strip common leading/trailing punctuation that often appears in headers
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
		log message "[rename_record_from_h1] " & msg
	end tell
end logDebug
