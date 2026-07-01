on performSmartRule(theRecords)
  -- [follower-guard] only the DEVONthink pipeline driver mutates documents (see should-run-dt-driver)
  try
    do shell script "$HOME/.local/bin/should-run-dt-driver"
  on error
    return
  end try
	tell application id "DNtp"
		try
			set targetDatabase to database "Lorebook"
			set searchContainer to root of targetDatabase
		on error
			return
		end try

		repeat with currentRecord in theRecords
			-- Only process records flagged as handwritten Boox notes (set by the Boox import watcher)
			set isHandwritten to (get custom meta data for "Handwritten" from currentRecord)
			if isHandwritten is 1 then
				set currentName to name of currentRecord as string
				-- Extract the key by stripping the extension
				set recordKey to do shell script "echo " & quoted form of currentName & " | sed 's/\\.[^.]*$//'"

				set searchQuery to "mdsourcefile==" & recordKey
				set searchResults to search searchQuery in searchContainer

				-- Look for an existing record with the same SourceFile key. Exclude
				-- currentRecord itself: a SourceFile-bearing record that re-enters Sweep
				-- (e.g. during a manual repair) would otherwise match itself and end up
				-- in the delete-current-record branch below.
				set existingMatch to missing value
				if searchResults is not missing value then
					repeat with matchedRecord in searchResults
						if (get custom meta data for "SourceFile" from matchedRecord) is recordKey ¬
							and (uuid of matchedRecord) is not (uuid of currentRecord) then
							set existingMatch to matchedRecord
							exit repeat
						end if
					end repeat
				end if

				if existingMatch is not missing value then
					-- Same notebook re-exported by the Boox: replace the existing
					-- record's content in place so its UUID, name, tags, and WikiLinks
					-- survive. The import watcher drops untitled Notebook-<n> exports, so
					-- every imported note is intentionally named and a SourceFile match is
					-- always the same notebook being updated, never a name collision.
					set existingPath to path of existingMatch
					set newPath to path of currentRecord

					-- Short-circuit byte-identical re-exports. The Boox re-emits the same
					-- notebook PDF on every device sync; without this guard, an unchanged
					-- notebook tours the full pipeline again (OCR → Format → Enrich →
					-- Archive) for no reason, and each re-tour can race the
					-- async-OCR / 5-min Format timeout and blank the existing comment.
					set identicalReimport to false
					try
						set newHash to do shell script "shasum -a 256 " & quoted form of newPath & " | cut -d' ' -f1"
						set oldHash to do shell script "shasum -a 256 " & quoted form of existingPath & " | cut -d' ' -f1"
						if newHash is oldHash then set identicalReimport to true
					end try

					if identicalReimport then
						log message "Handle Updated Notebooks: identical re-export of " & recordKey & ", discarding new copy" info recordKey
						delete record currentRecord
					else
						-- Replace existing: overwrite file at the filesystem level, re-index
						do shell script "cp " & quoted form of newPath & " " & quoted form of existingPath
						synchronize record existingMatch

						-- Reset pipeline flags so Extract Boox Handwritten criteria will match again
						add custom meta data 0 for "Recognized" to existingMatch
						add custom meta data 0 for "Commented" to existingMatch
						add custom meta data 0 for "AIEnriched" to existingMatch
						add custom meta data 1 for "NameLocked" to existingMatch
						add custom meta data 1 for "NeedsProcessing" to existingMatch

						-- Move back to 00_INBOX so the Extract and Format processing rules can see it
						set inboxGroup to get record at "/00_INBOX" in targetDatabase
						move record existingMatch to inboxGroup

						-- Clean up the temporary import
						delete record currentRecord
					end if

					set survivingRecord to existingMatch
				else
					-- New document: set source key, let the normal pipeline continue
					add custom meta data recordKey for "SourceFile" to currentRecord
					set survivingRecord to currentRecord
				end if

				-- Re-assert Handwritten flag (already set by the Boox import watcher; belt-and-suspenders)
				add custom meta data 1 for "Handwritten" to survivingRecord
			end if
		end repeat
	end tell
end performSmartRule
