-- Process: SingleFile Import
--
-- Processes SingleFile HTML captures that land in 00_INBOX. These are
-- produced either by Capture: SingleFile Batch (path A — bookmark saved
-- first, then captured on schedule) or by manual SingleFile saves in the
-- browser (path B — no pre-existing bookmark).
--
-- For each SingleFile HTML in 00_INBOX:
--   1. Extracts the source URL from the SingleFile comment header
--   2. Runs defuddle on the local file to produce readable markdown
--   3. Compresses embedded base64 images (Python + sips)
--   4. If markdown was produced: moves HTML to 99_ARCHIVE, imports
--      markdown to 00_INBOX for full pipeline treatment
--   5. If defuddle failed: HTML stays in 00_INBOX so Enrich: AI
--      Metadata can process it directly (fallback enrichment)
--   6. Resolves the bookmark: path A reads WebClipSource (set at capture
--      time by Capture: SingleFile Batch) and looks it up by UUID; path B
--      creates a fresh bookmark in 99_ARCHIVE
--   7. Cross-links all records via item link custom metadata
--
-- Smart rule criteria:
--   Search in: 00_INBOX
--   NeedsProcessing is On
--   Recognized is Off
--   Kind is HTML Page
--   Trigger: Every Minute
--
-- NeedsProcessing is set by the Sweep: Global Inbox rule before moving
-- to 00_INBOX. Native Text Bypass must exclude HTML (Kind is not HTML
-- Page) so this rule gets first crack at HTML files.
-- Non-SingleFile HTML is fast-tracked (Recognized=1, Commented=1).
--
-- Dependencies: defuddle (npm -g via mise)

on performSmartRule(theRecords)
	tell application id "DNtp"
		set archiveGroup to get record at "/99_ARCHIVE" in database "Lorebook"

		repeat with theRecord in theRecords
			set recName to name of theRecord as string
			set recType to (type of theRecord) as string

			-- Only process HTML records
			if recType is not "html" then
				-- Not HTML — skip, let other rules handle it
			else

				try
					set recPath to path of theRecord

					-- Check for SingleFile comment marker in the first 2KB
					set sfCheck to do shell script "head -c 2048 " & quoted form of recPath & " | grep -c 'Page saved with SingleFile' || true"
					if sfCheck is "0" then
						-- Not a SingleFile HTML — fast-track through pipeline
						-- (replaces Native Text Bypass, which excludes HTML to avoid racing this rule)
						add custom meta data 1 for "Recognized" to theRecord
						add custom meta data 1 for "Commented" to theRecord
					else

						-- PATH must include mise bin (for `mise exec`)
						set envPrefix to "export PATH=/opt/homebrew/bin:$HOME/.local/share/mise/bin:$PATH && "

						-- Extract source URL from SingleFile comment: " url: <url>"
						set recURL to do shell script "head -c 2048 " & quoted form of recPath & " | grep -oE 'url: https?://[^ ]+' | head -1 | sed 's/^url: //'"
						if recURL is "" then
							add custom meta data 1 for "Recognized" to theRecord
							add custom meta data 1 for "Commented" to theRecord
						else

							set targetGroup to location group of theRecord

							-- Use the record name (from SingleFile filename) as the title
							-- Strip the SingleFile date/time suffix if present:
							-- e.g. "Article Title (4-14-26 9_30_15 AM).html" → "Article Title"
							set pageTitle to do shell script "echo " & quoted form of recName & " | sed -E 's/ \\([0-9].*$//' | sed 's/\\.html$//i'"
							if pageTitle is "" then set pageTitle to recName

							-- Normalize fullwidth lookalikes + strip "| Site"
							-- suffix, then sanitize for filename use.
							set safeTitle to do shell script "printf '%s' " & quoted form of pageTitle & " | ~/.local/bin/clean-web-title | sed 's/[\\/:]/-/g; s/--*/-/g' | cut -c1-120"
							if safeTitle is "" then set safeTitle to "Web Clip"

							set workDir to do shell script "mktemp -d"
							set mdImported to false

							-- Extract readable markdown from the local HTML via defuddle
							try
								set mdFile to workDir & "/" & safeTitle & ".md"
								do shell script envPrefix & "mise exec node -- defuddle parse " & quoted form of recPath & " --markdown --output " & quoted form of mdFile

								-- Check content quality (same as extract-web-content)
								set wordCount to (do shell script "sed -E 's/!\\[[^]]*\\]\\([^)]*\\)//g; s/\\[[^]]*\\]\\([^)]*\\)//g; s/https?:\\/\\/[^ ]*//g' " & quoted form of mdFile & " | wc -w | tr -d ' '") as integer

								if wordCount ≥ 20 then
									set mdRecord to import mdFile to targetGroup
									set URL of mdRecord to recURL
									add custom meta data 1 for "NeedsProcessing" to mdRecord
									add custom meta data 1 for "NameLocked" to mdRecord
									set mdImported to true
								end if
							on error errMsg
								log message "Process: SingleFile Import — defuddle failed: " & errMsg info recName
							end try

							do shell script "rm -rf " & quoted form of workDir

							-- Compress embedded images in the SingleFile HTML
							try
								do shell script envPrefix & "python3 ~/.local/bin/compress-singlefile-images.py " & quoted form of recPath
							on error errMsg
								-- Image compression is non-fatal — skip silently
							end try

							-- Prepare the HTML record
							set URL of theRecord to recURL
							set tags of theRecord to {}
							add custom meta data 1 for "NameLocked" to theRecord
							set name of theRecord to safeTitle
							if mdImported then
								-- Markdown will carry enrichment — archive the HTML now
								move record theRecord to archiveGroup
							end if
							-- If defuddle failed, HTML stays in 00_INBOX so
							-- Enrich: AI Metadata can process it directly

							-- Resolve the bookmark, in priority order:
							--   1. WebClipSource UUID (set by Capture: SingleFile Batch
							--      at capture time — deterministic when the cross-link
							--      was written before this rule fired).
							--   2. URL match against existing bookmarks (catches the
							--      race where Capture's AppleScript is still blocked on
							--      the capture-with-singlefile shell call while DT's
							--      Every Minute tick fires this rule on the HTML that
							--      already landed in 00_INBOX — WebClipSource hasn't
							--      been set yet but the triggering bookmark exists).
							--      Also restores the pre-refactor path-B behavior of
							--      reusing an existing bookmark for manual SingleFile
							--      saves pointing at a URL already in the DB.
							--   3. Create a fresh bookmark in 99_ARCHIVE.
							-- Redirects between the bookmark's stored URL and the URL
							-- recorded by SingleFile can cause step 2 to miss; in that
							-- case step 3 creates a duplicate — same as pre-refactor.
							set bmRecord to missing value
							set clipSrc to ""
							try
								set clipSrc to (get custom meta data for "WebClipSource" from theRecord) as text
								if clipSrc is "missing value" then set clipSrc to ""
							end try
							if clipSrc starts with "x-devonthink-item://" then
								try
									set bmUUID to text ((length of "x-devonthink-item://") + 1) thru -1 of clipSrc
									set bmRecord to get record with uuid bmUUID
								end try
							end if
							if bmRecord is missing value then
								try
									set searchResults to search "URL==\"" & recURL & "\" kind:bookmark" in database "Lorebook"
									if (count of searchResults) > 0 then
										set bmRecord to item 1 of searchResults
									end if
								end try
							end if
							if bmRecord is missing value then
								set bmRecord to create record with {name:safeTitle, type:bookmark, URL:recURL} in archiveGroup
								add custom meta data 1 for "NameLocked" to bmRecord
							end if

							-- Fast-track the bookmark through the pipeline
							add custom meta data 1 for "Recognized" to bmRecord
							add custom meta data 1 for "Commented" to bmRecord
							add custom meta data 1 for "AIEnriched" to bmRecord

							-- Cross-link all three records
							set htmlLink to "x-devonthink-item://" & (uuid of theRecord)
							set bmLink to "x-devonthink-item://" & (uuid of bmRecord)
							add custom meta data htmlLink for "WebClipSnapshot" to bmRecord
							add custom meta data bmLink for "WebClipSource" to theRecord
							if mdImported then
								set mdLink to "x-devonthink-item://" & (uuid of mdRecord)
								add custom meta data mdLink for "WebClipMarkdown" to bmRecord
								add custom meta data bmLink for "WebClipSource" to mdRecord
								add custom meta data mdLink for "WebClipMarkdown" to theRecord
							end if

							-- Advance the HTML through pipeline gates
							add custom meta data 1 for "Recognized" to theRecord
							add custom meta data 1 for "Commented" to theRecord
							if mdImported then
								-- Markdown carries enrichment — fully fast-track the HTML
								add custom meta data 1 for "AIEnriched" to theRecord
							end if

					end if
					end if
				on error errMsg number errNum
					log message "Process: SingleFile Import — error: " & errMsg & " (" & errNum & ")" info recName
					add custom meta data 1 for "Recognized" to theRecord
					add custom meta data 1 for "Commented" to theRecord
				end try

			end if
		end repeat
	end tell
end performSmartRule
