on execute(draft)
	set theText to content of draft
	
	-- Get today's date in YYYY-MM-DD format to match your filenames
	set todayName to do shell script "date +'%Y-%m-%d'"
	
	tell application id "DNtp"
		-- 1. Grab the specific 10_DAILY group first
		set dailyGroup to get record at "/10_DAILY" in database "Lorebook"
		
		-- 2. Search ONLY inside that group
		set searchResults to search ("name:\"" & todayName & "\" kind:markdown") in dailyGroup
		
		if (count of searchResults) > 0 then
			set dailyNote to item 1 of searchResults
			
			-- Check if the draft already starts with a markdown bullet
			if theText starts with "- " or theText starts with "* " then
				set formattedText to theText
			else
				set formattedText to "- " & theText
			end if

			-- Insert at trailing empty bullet if present, otherwise append
			set noteBody to plain text of dailyNote
			set paraList to paragraphs of noteBody

			-- Find the last non-empty paragraph
			set lastLine to ""
			repeat with i from (count of paraList) to 1 by -1
				set candidateLine to item i of paraList
				if candidateLine is not "" then
					set lastLine to candidateLine
					exit repeat
				end if
			end repeat

			if lastLine is "-" or lastLine is "- " then
				-- Replace the trailing empty bullet with the new content + a fresh empty bullet
				-- Rebuild everything up to (but not including) the empty bullet line
				set AppleScript's text item delimiters to return
				set bodyBeforeLast to (items 1 thru (i - 1) of paraList) as text
				set AppleScript's text item delimiters to ""
				set plain text of dailyNote to bodyBeforeLast & return & formattedText & return & "- "
			else
				set plain text of dailyNote to noteBody & return & formattedText
			end if
			
		else
			-- FALLBACK: The daily note hasn't synced down yet.
			set inboxGroup to incoming group
			set fallbackRecord to create record with {name:("Draft intended for " & todayName), type:markdown, plain text:theText} in inboxGroup
			
			-- Optional: Add a specific tag so you know why it's here
			set tags of fallbackRecord to {"missed-daily-append"}
		end if
	end tell
end execute
