-- Export: Wiki Raw
--
-- Exports archived documents to ~/Wiki/raw/ as markdown files with YAML
-- frontmatter. Each file contains the document's metadata (title, date, type,
-- tags, summary) and text content, ready for LLM wiki compilation.
--
-- The export is a one-way bridge: DEVONthink owns the original documents,
-- and the wiki layer (maintained by an LLM agent) reads from raw/ to build
-- a structured, interlinked knowledge base.
--
-- Smart rule criteria:
--   Search in: 99_ARCHIVE
--   WikiExported is Off
--   Kind is Any Document
--   Trigger: Hourly
--
-- Requires: ~/Wiki/raw/ directory to exist (created by scripts/init-wiki.sh)

on performSmartRule(theRecords)
	tell application id "DNtp"
		set rawDir to (POSIX path of (path to home folder)) & "Wiki/raw"

		-- Ensure raw directory exists
		try
			do shell script "mkdir -p " & quoted form of rawDir
		on error errMsg
			log message "Export Wiki Raw: cannot create raw dir: " & errMsg
			return
		end try

		repeat with theRecord in theRecords
			set recName to name of theRecord
			set recUUID to uuid of theRecord

			try
				-- Skip redundant web clip records.
				-- For bookmarked URLs, the pipeline produces up to three records:
				-- a bookmark, an HTML snapshot, and a markdown extract. Only the
				-- richest enriched record should be exported — skip the others.

				-- Skip bookmarks that have a richer derived record (markdown or HTML snapshot)
				set clipMd to ""
				try
					set clipMd to (get custom meta data for "WebClipMarkdown" from theRecord) as text
					if clipMd is "missing value" then set clipMd to ""
				end try
				set clipSnapshot to ""
				try
					set clipSnapshot to (get custom meta data for "WebClipSnapshot" from theRecord) as text
					if clipSnapshot is "missing value" then set clipSnapshot to ""
				end try
				if clipMd is not "" or clipSnapshot is not "" then
					add custom meta data 1 for "WikiExported" to theRecord
				else

				-- Skip HTML snapshots that were not enriched (the markdown
				-- record carries enrichment in the normal path)
				set clipSrc to ""
				try
					set clipSrc to (get custom meta data for "WebClipSource" from theRecord) as text
					if clipSrc is "missing value" then set clipSrc to ""
				end try
				set recAIEnriched to 0
				try
					set recAIEnriched to (get custom meta data for "AIEnriched" from theRecord)
					if recAIEnriched is missing value then set recAIEnriched to 0
				end try
				if clipSrc is not "" and recAIEnriched is not 1 then
					add custom meta data 1 for "WikiExported" to theRecord
				else

				-- Gather metadata
				set recTitle to recName
				set recDate to ""
				try
					set recDate to (get custom meta data for "EventDate" from theRecord) as text
					if recDate is "missing value" then set recDate to ""
				end try

				set recType to ""
				try
					set recType to (get custom meta data for "DocumentType" from theRecord) as text
					if recType is "missing value" then set recType to ""
				end try

				set recSummary to ""
				try
					set recSummary to (get custom meta data for "summary" from theRecord) as text
					if recSummary is "missing value" then set recSummary to ""
				end try

				set recURL to ""
				try
					set recURL to URL of theRecord
					if recURL is missing value then set recURL to ""
				end try

				set recTags to {}
				try
					set recTags to tags of theRecord
				end try

				set isHandwritten to 0
				try
					set isHandwritten to (get custom meta data for "Handwritten" from theRecord)
					if isHandwritten is missing value then set isHandwritten to 0
				end try

				-- Get document content
				set docContent to ""
				if isHandwritten is 1 then
					set docContent to comment of theRecord
				else
					set docContent to plain text of theRecord
				end if
				if docContent is missing value then set docContent to ""

				-- Skip records with no content at all
				if docContent is "" and recSummary is "" then
					add custom meta data 1 for "WikiExported" to theRecord
				else

					-- Build YAML frontmatter via Python to handle escaping properly
					set dtLink to "x-devonthink-item://" & recUUID
					set exportTimestamp to do shell script "date '+%Y-%m-%dT%H:%M:%S'"

					-- Format tags as YAML array
					set tagYAML to "[]"
					if (count of recTags) > 0 then
						set tagItems to {}
						repeat with aTag in recTags
							set end of tagItems to quoted form of (aTag as text)
						end repeat
						-- Build via shell to avoid AppleScript delimiter pain
						set tid to AppleScript's text item delimiters
						set AppleScript's text item delimiters to ", "
						set tagListStr to tagItems as text
						set AppleScript's text item delimiters to tid
						set tagYAML to "[" & tagListStr & "]"
					end if

					-- Escape YAML string values (double quotes)
					set pyEscape to "import sys, os" & linefeed & ¬
						"v = os.environ.get('VAL', '')" & linefeed & ¬
						"print(v.replace('\\\\', '\\\\\\\\').replace('\"', '\\\\\"').replace(chr(10), ' ').strip(), end='')"

					set safeTitle to do shell script "export VAL=" & quoted form of recTitle & " && /usr/bin/python3 -c " & quoted form of pyEscape
					set safeSummary to do shell script "export VAL=" & quoted form of recSummary & " && /usr/bin/python3 -c " & quoted form of pyEscape

					-- Build the frontmatter
					set frontmatter to "---" & linefeed
					set frontmatter to frontmatter & "title: \"" & safeTitle & "\"" & linefeed
					if recDate is not "" then
						set frontmatter to frontmatter & "date: \"" & recDate & "\"" & linefeed
					end if
					if recType is not "" then
						set frontmatter to frontmatter & "type: \"" & recType & "\"" & linefeed
					end if
					set frontmatter to frontmatter & "tags: " & tagYAML & linefeed
					if safeSummary is not "" then
						set frontmatter to frontmatter & "summary: \"" & safeSummary & "\"" & linefeed
					end if
					if recURL is not "" then
						set frontmatter to frontmatter & "url: \"" & recURL & "\"" & linefeed
					end if
					set frontmatter to frontmatter & "dt_link: \"" & dtLink & "\"" & linefeed
					set frontmatter to frontmatter & "exported: \"" & exportTimestamp & "\"" & linefeed
					set frontmatter to frontmatter & "---" & linefeed & linefeed

					-- Truncate content to ~8000 words to keep exports manageable
					set truncatedContent to docContent
					if docContent is not "" then
						set truncPy to "import sys" & linefeed & ¬
							"text = sys.stdin.read()" & linefeed & ¬
							"words = text.split()" & linefeed & ¬
							"if len(words) > 8000:" & linefeed & ¬
							"    print(' '.join(words[:8000]) + '\\n\\n[...truncated...]', end='')" & linefeed & ¬
							"else:" & linefeed & ¬
							"    print(text, end='')"

						set tmpContent to do shell script "mktemp /tmp/dt-wiki-export.XXXXXX"
						set contentRef to open for access (POSIX file tmpContent) with write permission
						write docContent to contentRef as «class utf8»
						close access contentRef
						set truncatedContent to do shell script "/usr/bin/python3 -c " & quoted form of truncPy & " < " & quoted form of tmpContent
						do shell script "rm -f " & quoted form of tmpContent
					end if

					-- Write the markdown file
					set outputPath to rawDir & "/" & recUUID & ".md"
					set fullContent to frontmatter & truncatedContent

					set tmpOutput to do shell script "mktemp /tmp/dt-wiki-export.XXXXXX"
					set outRef to open for access (POSIX file tmpOutput) with write permission
					write fullContent to outRef as «class utf8»
					close access outRef
					do shell script "mv " & quoted form of tmpOutput & " " & quoted form of outputPath

					-- Mark as exported
					add custom meta data 1 for "WikiExported" to theRecord
				end if

				end if -- clipSrc (HTML snapshot guard)
				end if -- clipMd (bookmark guard)

			on error errMsg
				log message "Export Wiki Raw: failed: " & errMsg info recName
				-- Close any open file handles on error
				try
					close access contentRef
				end try
				try
					close access outRef
				end try
			end try
		end repeat
	end tell
end performSmartRule
