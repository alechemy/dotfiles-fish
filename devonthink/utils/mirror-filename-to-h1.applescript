use AppleScript version "2.4"
use scripting additions

-- add_h1_from_filename.applescript
--
-- Purpose:
-- - Intended for use in a DEVONthink Smart Rule triggered "On Rename".
-- - Updates the Markdown H1 header to match the filename.
-- - Logic:
--   1. Empty file: Insert "# Filename\n\n"
--   2. File starts with H1: Replace H1 with "# Filename"
--   3. File starts with other text: Prepend "# Filename\n\n"
--
-- Recommended Smart Rule:
-- - Name: "Sync H1 with Filename"
-- - Trigger: On Rename
-- - Condition: Kind is Markdown
-- - Action: Execute Script… (this file)

-- ======== Configuration ========

property DEBUG_LOGGING : false

-- ======== Entry Point ========

on performSmartRule(theRecords)
	repeat with r in theRecords
		try
			my processRecord(r)
		on error errMsg number errNum
			my logDebug("Error (" & errNum & "): " & errMsg)
		end try
	end repeat
end performSmartRule

-- ======== Core Logic ========

on processRecord(r)
	-- 1. Get Filename
	tell application id "DNtp"
		set recName to (name of r) as rich text
	end tell
	if recName is "" then return

	-- 2. Get Content
	set bodyText to my getRecordPlainText(r)
	if bodyText is missing value then set bodyText to ""

	-- Strip BOM if present (character id 65279)
	if (count of bodyText) > 0 then
		if id of first character of bodyText is 65279 then
			set bodyText to rich texts 2 thru -1 of bodyText
		end if
	end if

	-- 3. Handle Empty File
	if my isEffectivelyEmpty(bodyText) then
		-- Insert H1 + 2 newlines (to start writing on line 3)
		set newContent to "# " & recName & linefeed & linefeed
		my setRecordText(r, newContent, "Inserted H1 into empty record")
		return
	end if

	-- 4. Handle Non-Empty File
	set linesList to my splitLines(bodyText)

	-- Safety check
	if (count of linesList) = 0 then
		-- Fallback for weirdly empty state not caught above
		set newContent to "# " & recName & linefeed & linefeed & bodyText
		my setRecordText(r, newContent, "Prepended H1 to record")
		return
	end if

	set firstLine to item 1 of linesList

	if firstLine begins with "# " then
		-- Case: Existing H1 at top. Replace it.

		-- Optimization: if it matches, do nothing
		if firstLine is ("# " & recName) then
			return
		end if

		set item 1 of linesList to "# " & recName
		set newContent to my joinLines(linesList)
		my setRecordText(r, newContent, "Updated existing H1")
	else
		-- Case: No H1 at top. Prepend.
		set newContent to "# " & recName & linefeed & linefeed & bodyText
		my setRecordText(r, newContent, "Prepended H1 to content")
	end if
end processRecord

-- ======== Helpers ========

on getRecordPlainText(r)
	tell application id "DNtp"
		try
			set t to (plain text of r) as rich text
			return t
		on error
			return missing value
		end try
	end tell
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

on isEffectivelyEmpty(t)
	if t is "" then return true
	set tClean to my trimWhitespace(t)
	return (tClean is "")
end isEffectivelyEmpty

on splitLines(t)
	-- Normalize line endings to LF then split
	set t2 to my replaceText(return, linefeed, t)
	set t2 to my replaceText(character id 13, linefeed, t2)

	set AppleScript's text item delimiters to linefeed
	set itemsList to text items of t2
	set AppleScript's text item delimiters to ""
	return itemsList
end splitLines

on joinLines(l)
	set AppleScript's text item delimiters to linefeed
	set t to l as rich text
	set AppleScript's text item delimiters to ""
	return t
end joinLines

on replaceText(findText, replaceWith, sourceText)
	set AppleScript's text item delimiters to findText
	set parts to text items of sourceText
	set AppleScript's text item delimiters to replaceWith
	set outText to parts as rich text
	set AppleScript's text item delimiters to ""
	return outText
end replaceText

on trimWhitespace(t)
	set s to t as rich text
	set whitespaceChars to {space, tab, return, linefeed, character id 10, character id 13}

	repeat while (count of s) > 0
		if first character of s is in whitespaceChars then
			set s to rich texts 2 thru -1 of s
		else
			exit repeat
		end if
	end repeat

	repeat while (count of s) > 0
		if last character of s is in whitespaceChars then
			set s to rich texts 1 thru -2 of s
		else
			exit repeat
		end if
	end repeat

	return s
end trimWhitespace

on logDebug(msg)
	if not DEBUG_LOGGING then return
	tell application id "DNtp"
		log message "[add_h1_from_filename] " & msg
	end tell
end logDebug
