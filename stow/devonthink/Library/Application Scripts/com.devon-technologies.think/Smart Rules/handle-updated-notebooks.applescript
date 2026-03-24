on performSmartRule(theRecords)
	tell application id "DNtp"
		try
			set targetDatabase to database "Lorebook"
			set searchContainer to root of targetDatabase
		on error
			return
		end try

		repeat with currentRecord in theRecords
			-- Only process records flagged as handwritten Boox notes (set by Hazel at import)
			set isHandwritten to (get custom meta data for "Handwritten" from currentRecord)
			if isHandwritten is 1 then
				set currentName to name of currentRecord as string
				-- Extract the key by stripping the extension
				set recordKey to do shell script "echo " & quoted form of currentName & " | sed 's/\\.[^.]*$//'"

				set searchQuery to "mdsourcefile==" & recordKey
				set searchResults to search searchQuery in searchContainer

				-- Look for an existing record with the same SourceFile key
				set existingMatch to missing value
				if searchResults is not missing value then
					repeat with matchedRecord in searchResults
						if (get custom meta data for "SourceFile" from matchedRecord) is recordKey then
							set existingMatch to matchedRecord
							exit repeat
						end if
					end repeat
				end if

				set isCollision to false
				if existingMatch is not missing value then
					set existingPath to path of existingMatch
					set newPath to path of currentRecord

					-- Layer 1: Page count check (Fast, no image I/O)
					-- Assumes users rarely delete pages from notebooks; if new is smaller, it's a new notebook.
					try
						set oldPages to page count of existingMatch
						set newPages to page count of currentRecord

						-- Fallback to ImageMagick if DT doesn't index 'page count' natively for these TIFFs
						if oldPages is missing value or oldPages is 0 or newPages is missing value or newPages is 0 then
							set oldPages to (do shell script "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; magick identify -format '%n' " & quoted form of existingPath) as integer
							set newPages to (do shell script "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; magick identify -format '%n' " & quoted form of newPath) as integer
						end if

						if newPages < oldPages then
							set isCollision to true
						end if
					on error
						-- If page count checks fail for any reason, let Layer 2 handle it
					end try

					set needsRmse to false

					-- Layer 2: OCR Text Containment (only exercised for Notebook-X; custom names bypass via fallback below)
					if not isCollision then
						set pyScript to "import sys, subprocess, difflib, tempfile, os" & linefeed & ¬
							"def get_text(path):" & linefeed & ¬
							"    tmp = ''" & linefeed & ¬
							"    try:" & linefeed & ¬
							"        tmp_file = tempfile.NamedTemporaryFile(suffix='.tiff', delete=False)" & linefeed & ¬
							"        tmp = tmp_file.name" & linefeed & ¬
							"        tmp_file.close()" & linefeed & ¬
							"        subprocess.run(['/opt/homebrew/bin/magick', path + '[0]', tmp], stderr=subprocess.DEVNULL, check=True)" & linefeed & ¬
							"        out = subprocess.check_output(['/opt/homebrew/bin/tesseract', tmp, 'stdout', '-l', 'eng', '--psm', '3'], stderr=subprocess.DEVNULL)" & linefeed & ¬
							"        return out.decode('utf-8').strip()" & linefeed & ¬
							"    except:" & linefeed & ¬
							"        return ''" & linefeed & ¬
							"    finally:" & linefeed & ¬
							"        if tmp:" & linefeed & ¬
							"            try: os.unlink(tmp)" & linefeed & ¬
							"            except: pass" & linefeed & ¬
							"t1 = get_text(sys.argv[1])" & linefeed & ¬
							"t2 = get_text(sys.argv[2])" & linefeed & ¬
							"if len(t1) < 15 and len(t2) < 15:" & linefeed & ¬
							"    print('USE_RMSE')" & linefeed & ¬
							"else:" & linefeed & ¬
							"    shorter = min(len(t1), len(t2))" & linefeed & ¬
							"    if shorter == 0:" & linefeed & ¬
							"        print(0)" & linefeed & ¬
							"    else:" & linefeed & ¬
							"        m = difflib.SequenceMatcher(None, t1, t2, autojunk=False)" & linefeed & ¬
							"        containment = sum(b.size for b in m.get_matching_blocks() if b.size >= 3) / shorter" & linefeed & ¬
							"        print(containment)"

						set ocrCmd to "/usr/bin/python3 -c " & quoted form of pyScript & " " & quoted form of existingPath & " " & quoted form of newPath
						try
							set ocrResult to do shell script ocrCmd
							if ocrResult is "USE_RMSE" then
								set needsRmse to true
							else
								set textSim to ocrResult as number
								if textSim < 0.3 then
									set isCollision to true
								end if
							end if
						on error errMsg
							log message "OCR text diff failed for " & recordKey & ": " & errMsg
							set needsRmse to true
						end try
					end if

					-- Layer 3: ImageMagick RMSE check (Fallback for sparse/doodle notes with no text)
					if needsRmse and not isCollision then
						try
							set oldSize to do shell script "stat -f%z " & quoted form of existingPath
							set newSize to do shell script "stat -f%z " & quoted form of newPath
							set oldDims to do shell script "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; magick identify -format '%wx%h' " & quoted form of (existingPath & "[0]")
							set newDims to do shell script "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; magick identify -format '%wx%h' " & quoted form of (newPath & "[0]")
						on error metaErr
							log message "Failed to fetch metadata for " & recordKey info metaErr
						end try

						-- Run magick compare with -verbose and log raw output before parsing
						set rawRmseCmd to "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; magick compare -verbose -metric RMSE " & quoted form of (existingPath & "[0]") & " " & quoted form of (newPath & "[0]") & " null: 2>&1 || true"
						set rawOutput to do shell script rawRmseCmd

						-- Parse the raw output to get the metric (ImageMagick outputs distance: 0 = identical)
						set parseCmd to "echo " & quoted form of rawOutput & " | /usr/bin/tr '\\r' '\\n' | /usr/bin/awk '/[Aa]ll: / {print $NF}' | /usr/bin/tr -d '()' | /usr/bin/tail -n 1"
						try
							set rmseScoreStr to do shell script parseCmd
							set rmseScore to rmseScoreStr as number
							if rmseScore > 0.05 then
								set isCollision to true
							end if
							log message "RMSE distance for " & recordKey & ": " & rmseScore info "Threshold: > 0.05"
						on error errMsg
							-- If parsing fails, log and assume collision
							log message "RMSE parse failed for " & recordKey & ": " & errMsg
							set isCollision to true
						end try
					end if
				end if

				-- Fallback: If it's intentionally named, assume it's an update, ignoring collision metrics
				if isCollision then
					set isDefaultName to do shell script "echo " & quoted form of recordKey & " | grep -cE '^Notebook-[0-9]+(-[0-9]+)?$' || true"
					if isDefaultName is "0" then
						set isCollision to false
					end if
				end if

				if (existingMatch is not missing value) and (not isCollision) then
					-- Replace existing: overwrite file at the filesystem level, re-index
					do shell script "cp " & quoted form of newPath & " " & quoted form of existingPath
					synchronize record existingMatch

					-- Reset pipeline flags so Extract Boox Handwritten criteria will match again
					add custom meta data 0 for "Recognized" to existingMatch
					add custom meta data 0 for "Commented" to existingMatch
					add custom meta data 0 for "AIEnriched" to existingMatch
					add custom meta data 1 for "NameLocked" to existingMatch
					add custom meta data 1 for "NeedsProcessing" to existingMatch
					add custom meta data 0 for "TasksExtracted" to existingMatch
					add custom meta data 0 for "DailyNotesProcessed" to existingMatch

					-- Move back to 00_INBOX so the Extract and Format processing rules can see it
					set inboxGroup to get record at "/00_INBOX" in targetDatabase
					move record existingMatch to inboxGroup

					-- Clean up the temporary import
					set survivingRecord to existingMatch
					delete record currentRecord
				else
					if isCollision then
						-- Fork the ID by appending a timestamp so it doesn't collide anymore
						set oldRecordKey to recordKey
						set recordKey to recordKey & "-" & (do shell script "date +%s")
						-- Pre-set NameLocked before renaming so Util: Lock Name on Rename doesn't catch this programmatic rename,
						-- then immediately clear it so AI enrichment can still rename the forked document.
						add custom meta data 1 for "NameLocked" to currentRecord
						set name of currentRecord to recordKey
						add custom meta data 0 for "NameLocked" to currentRecord
						log message "Collision detected for " & oldRecordKey & ": forking as " & recordKey info (name of currentRecord)
					end if

					-- New document: set source key, let the normal pipeline continue
					add custom meta data recordKey for "SourceFile" to currentRecord

					set survivingRecord to currentRecord
				end if

				-- Re-assert Handwritten flag (already set by Hazel; belt-and-suspenders)
				add custom meta data 1 for "Handwritten" to survivingRecord
			end if
		end repeat
	end tell
end performSmartRule
