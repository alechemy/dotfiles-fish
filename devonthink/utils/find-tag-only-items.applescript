-- find-tag-only-items.applescript
-- Detects records in Lorebook that exist ONLY in tag groups
-- (i.e., they have no parent that is a regular group, inbox, or archive folder).
--
-- Run from DEVONthink's Script menu or the macOS Script Editor.
--
-- Strategy: instead of fetching every record in the database (which can fail
-- with "AppleEvent handler failed" on large databases), we walk the Tags
-- group directly.  Every tag-only item must, by definition, live inside a
-- tag group, so this approach is both safer and more targeted.

tell application id "DNtp"
	set db to database "Lorebook"
	set tagOnlyNames to {}
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
					-- real (non-tag) group?  If every parent is a tag group the
					-- record is "tag-only."
					set parentList to parents of r
					set inRealGroup to false
					repeat with p in parentList
						try
							if (tag of p) is false then
								set inRealGroup to true
								exit repeat
							end if
						on error
							-- If we cannot read the property, err on the safe side
							set inRealGroup to true
							exit repeat
						end try
					end repeat

					if not inRealGroup then
						-- Build a display string for reporting
						set rTags to tags of r
						set tagStr to ""
						repeat with t in rTags
							if tagStr is "" then
								set tagStr to t
							else
								set tagStr to tagStr & ", " & t
							end if
						end repeat
						set end of tagOnlyNames to "• " & (name of r) & "  [" & tagStr & "]"
					end if
				end if
			end if
		end repeat
	end repeat

	-- Report results
	set itemCount to count of tagOnlyNames
	if itemCount > 0 then
		set report to "Found " & itemCount & " tag-only item(s):" & return & return
		repeat with ln in tagOnlyNames
			set report to report & ln & return
		end repeat
		display dialog report buttons {"OK"} default button "OK"
	else
		display dialog "No tag-only items found." buttons {"OK"} default button "OK"
	end if
end tell
