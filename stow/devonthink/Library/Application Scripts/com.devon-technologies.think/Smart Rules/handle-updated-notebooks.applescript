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

					set replaceOK to true
					if identicalReimport then
						log message "Handle Updated Notebooks: identical re-export of " & recordKey & ", trashing new copy" info recordKey
						-- Trash, not delete record: delete is a hard delete, and
						-- the hash match is a heuristic on user-created content.
						move record currentRecord to trash group of targetDatabase
					else
						-- Replace existing at the filesystem level, then re-index.
						-- Stage + atomic mv (same volume) so the backing file inside
						-- the database package is always either the old or the new
						-- content — a direct cp truncates in place, and a mid-write
						-- failure would corrupt the record with no undo.
						try
							do shell script "cp " & quoted form of newPath & " " & quoted form of (existingPath & ".dt-replace-tmp") & " && /bin/mv -f " & quoted form of (existingPath & ".dt-replace-tmp") & " " & quoted form of existingPath
						on error errMsg
							set replaceOK to false
							do shell script "rm -f " & quoted form of (existingPath & ".dt-replace-tmp")
							log message "Handle Updated Notebooks: content replace failed for " & recordKey & ": " & errMsg info recordKey
							my pipelineLog("Handle Updated Notebooks", "ERROR", "content replace failed, leaving new import in inbox for retry: " & errMsg, currentName, uuid of currentRecord)
						end try

						if replaceOK then
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

							-- Trash the temporary import (its content now lives in
							-- existingMatch); trash rather than hard-delete so a bad
							-- SourceFile match is recoverable
							move record currentRecord to trash group of targetDatabase
						end if
					end if

					if replaceOK then
						set survivingRecord to existingMatch
					else
						set survivingRecord to currentRecord
					end if
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

-- Forward an event to the centralized pipeline log. Fails silently if
-- the helper isn't present, so scripts remain functional before the
-- stow/setup step that puts it in place.
on pipelineLog(component, level, msg, recName, recUUID)
	try
		do shell script "$HOME/.local/bin/pipeline-log " & ¬
			quoted form of component & " " & ¬
			quoted form of level & " " & ¬
			quoted form of msg & " " & ¬
			quoted form of (recName as string) & " " & ¬
			quoted form of (recUUID as string)
	end try
end pipelineLog
