-- rescue-tag-only-items.applescript
-- Finds records in Lorebook that exist ONLY in tag groups (no real parent group)
-- and moves them to 99_ARCHIVE so they are no longer orphaned.
--
-- Run from DEVONthink's Script menu or the macOS Script Editor.
-- Recommended: run find-tag-only-items.applescript first to preview what will be moved.
--
-- Strategy: walks the Tags group instead of fetching every record in the
-- database (which can fail with "AppleEvent handler failed" on large databases).

tell application id "DNtp"
	set db to database "Lorebook"
	set archiveGroup to get record at "/99_ARCHIVE" in db
	if archiveGroup is missing value then
		display dialog "Could not find 99_ARCHIVE group in Lorebook." buttons {"OK"} default button "OK" with icon stop
		return
	end if

	set tagOnlyItems to {}
	set tagOnlyLabels to {}
	set checkedIDs to {}

	-- Locate the Tags group using DT's path-based accessor
	-- (avoids 'root', which collides with an AppleScript class name).
	set tagsGroup to get record at "/Tags" in db
	if tagsGroup is missing value then
		display dialog "Could not locate /Tags in Lorebook." buttons {"OK"} default button "OK" with icon stop
		return
	end if

	-- Iterate each individual tag group (e.g. "Finance", "Manual", …)
	repeat with tg in (children of tagsGroup)
		repeat with r in (children of tg)
			set rid to id of r

			-- A record tagged with multiple tags appears in multiple tag groups;
			-- skip duplicates we have already inspected.
			if rid is not in checkedIDs then
				set end of checkedIDs to rid

				-- Skip sub-tag-groups (nested tags) — we only care about documents.
				set isTagGroup to false
				try
					set isTagGroup to (tag of r)
				end try

				if not isTagGroup then
					-- Core check: does this record have ANY parent that is a
					-- real (non-tag) group?
					set parentList to parents of r
					set inRealGroup to false
					repeat with p in parentList
						try
							if (tag of p) is false then
								set inRealGroup to true
								exit repeat
							end if
						on error
							set inRealGroup to true
							exit repeat
						end try
					end repeat

					if not inRealGroup then
						set end of tagOnlyItems to r
						-- Build a display label for the confirmation dialog
						set rTags to tags of r
						set tagStr to ""
						repeat with t in rTags
							if tagStr is "" then
								set tagStr to t
							else
								set tagStr to tagStr & ", " & t
							end if
						end repeat
						set end of tagOnlyLabels to "• " & (name of r) & "  [" & tagStr & "]"
					end if
				end if
			end if
		end repeat
	end repeat

	set itemCount to count of tagOnlyItems
	if itemCount is 0 then
		display dialog "No tag-only items found. Nothing to rescue." buttons {"OK"} default button "OK"
		return
	end if

	-- Build a preview list before confirming
	set preview to "Found " & itemCount & " tag-only item(s) to rescue:" & return & return
	repeat with ln in tagOnlyLabels
		set preview to preview & ln & return
	end repeat
	set preview to preview & return & "Move all to 99_ARCHIVE?"

	set userChoice to button returned of (display dialog preview buttons {"Cancel", "Move All"} default button "Cancel" with icon caution)
	if userChoice is "Cancel" then return

	-- Move each tag-only item to 99_ARCHIVE
	set movedCount to 0
	set failedCount to 0
	repeat with r in tagOnlyItems
		try
			move record r to archiveGroup
			set movedCount to movedCount + 1
		on error errMsg
			set failedCount to failedCount + 1
			log message "rescue-tag-only: failed to move " & (name of r) & ": " & errMsg
		end try
	end repeat

	-- Summary
	set summary to "Rescue complete." & return & return
	set summary to summary & "Moved: " & movedCount & return
	if failedCount > 0 then
		set summary to summary & "Failed: " & failedCount & " (see Log for details)" & return
	end if
	display dialog summary buttons {"OK"} default button "OK"
end tell
