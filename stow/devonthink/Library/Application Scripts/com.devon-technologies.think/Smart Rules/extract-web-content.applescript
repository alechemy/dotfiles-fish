-- Extract: Web Content
--
-- Intercepts Bookmark records in 00_INBOX and downloads the page content
-- in two formats:
--   1. Readable markdown (defuddle) — clean extracted article content,
--      imported to 00_INBOX for full pipeline treatment (AI enrichment, etc.)
--   2. Faithful HTML snapshot (monolith) — single-file archive with all
--      assets inlined as data: URIs, imported directly to 99_ARCHIVE
--
-- The original bookmark is kept as a lightweight live link to the page.
-- All three records are cross-linked via item link custom metadata:
--   WebClipSource   (on markdown/HTML)  → points to the bookmark
--   WebClipMarkdown (on bookmark)       → points to the markdown
--   WebClipSnapshot (on bookmark)       → points to the HTML
-- They also share the same URL metadata as a secondary join key.
--
-- On success: the bookmark is fast-tracked through the pipeline (all flags
-- set) so it archives alongside its derived records. The markdown record
-- is the only one that receives full AI enrichment.
--
-- On failure: the bookmark is passed through as-is by setting Recognized=1
-- and Commented=1, so it reaches AI enrichment with minimal content rather
-- than getting stuck.
--
-- Smart rule criteria:
--   Search in: 00_INBOX
--   NeedsProcessing is On
--   Recognized is Off
--   Kind is Bookmark
--   Trigger: Every Minute
--
-- Dependencies: monolith (brew), defuddle (npm -g via mise)

on performSmartRule(theRecords)
	tell application id "DNtp"
		set archiveGroup to get record at "/99_ARCHIVE" in database "Lorebook"

		repeat with theRecord in theRecords
			set recName to name of theRecord as string
			set recURL to URL of theRecord

			if recURL is "" or recURL is missing value then
				-- No URL (shouldn't happen, but be safe) — pass through
				log message "Extract: Web Content — no URL, passing through" info recName
				add custom meta data 1 for "Recognized" to theRecord
				add custom meta data 1 for "Commented" to theRecord
			else
				set targetGroup to location group of theRecord

				try
					-- PATH must include Homebrew (monolith) and mise shims (defuddle)
					set envPrefix to "export PATH=/opt/homebrew/bin:$HOME/.local/share/mise/shims:$PATH && "

					-- Create temp directory
					set workDir to do shell script "mktemp -d"

					-- Use the bookmark's name (from DT clip extension) as the title.
					-- This is the browser's rendered page title, which is typically
					-- more descriptive than defuddle's extraction for non-article pages.
					set pageTitle to recName
					if pageTitle is "" then set pageTitle to "Web Clip"

					-- Sanitize for filename: only replace filesystem-unsafe chars (/ and :),
					-- collapse consecutive dashes, strip control chars, trim, truncate.
					set safeTitle to do shell script "echo " & quoted form of pageTitle & " | sed 's/[\\/:]/-/g; s/--*/-/g' | tr -d '[:cntrl:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | cut -c1-120"
					if safeTitle is "" then set safeTitle to "Web Clip"

					set mdImported to false
					set htmlImported to false

					-- Download readable markdown (defuddle)
					-- This is the primary record — gets full pipeline treatment
					try
						set mdFile to workDir & "/" & safeTitle & ".md"
						do shell script envPrefix & "defuddle parse " & quoted form of recURL & " --markdown --output " & quoted form of mdFile

						-- Check content quality: strip images, links, and bare URLs, count remaining words.
						-- Pages with only embeds/images (e.g. Reddit image posts) produce garbage AI enrichment.
						set wordCount to (do shell script "sed -E 's/!\\[[^]]*\\]\\([^)]*\\)//g; s/\\[[^]]*\\]\\([^)]*\\)//g; s/https?:\\/\\/[^ ]*//g' " & quoted form of mdFile & " | wc -w | tr -d ' '") as integer

						if wordCount ≥ 20 then
							set mdRecord to import mdFile to targetGroup
							set URL of mdRecord to recURL
							add custom meta data 1 for "NeedsProcessing" to mdRecord
							-- Lock the name so AI enrichment doesn't rename it —
							-- keeping all three records (bookmark, markdown, HTML)
							-- aligned on the same title from the browser.
							add custom meta data 1 for "NameLocked" to mdRecord
							-- Pre-set flags for rules that skip web clips (via WebClipSource
							-- criteria), so Archive: Processed Items can still pick it up.
							add custom meta data 1 for "TasksExtracted" to mdRecord
							add custom meta data 1 for "DailyNotesProcessed" to mdRecord
							set mdImported to true
						else
							log message "Extract: Web Content — skipping markdown, only " & wordCount & " words of text content" info recName
						end if
					on error errMsg
						log message "Extract: Web Content — defuddle failed: " & errMsg info recName
					end try

					-- Download faithful HTML snapshot (monolith)
					-- Imported directly to 99_ARCHIVE — no AI enrichment needed
					-- -I: isolate (block network requests when opened)
					-- -j: strip JavaScript
					-- -F: strip web fonts  -v: strip video  -a: strip audio
					try
						set htmlFile to workDir & "/" & safeTitle & ".html"
						do shell script envPrefix & "monolith " & quoted form of recURL & " -I -j -F -v -a -o " & quoted form of htmlFile & " 2>&1"

						-- Compress embedded images using Python and sips
						set pyScript to "import sys, re, base64, subprocess, tempfile, os
html_file = sys.argv[1]
try:
    with open(html_file, 'r', encoding='utf-8') as f:
        content = f.read()
except Exception:
    sys.exit(0)

def process_image(match):
    original = match.group(0)
    mime_type = match.group(1)
    b64_data = match.group(2)
    if len(b64_data) < 10000 or 'svg' in mime_type:
        return original
    temp_path = None
    out_path = None
    try:
        img_data = base64.b64decode(b64_data)
        fd, temp_path = tempfile.mkstemp(suffix='.img')
        out_path = temp_path + '.jpeg'
        with os.fdopen(fd, 'wb') as f:
            f.write(img_data)
        subprocess.run(['sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', '60', '-Z', '1024', temp_path, '--out', out_path], capture_output=True, check=True, timeout=10)
        with open(out_path, 'rb') as f:
            new_img_data = f.read()
        new_b64 = base64.b64encode(new_img_data).decode('utf-8')
        new_str = 'data:image/jpeg;base64,' + new_b64
        if len(new_str) < len(original):
            return new_str
        return original
    except Exception:
        return original
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        if out_path and os.path.exists(out_path):
            os.remove(out_path)

pattern = re.compile(r'data:image/([^;]+);base64,([A-Za-z0-9+/=]+)')
new_content = pattern.sub(process_image, content)
with open(html_file, 'w', encoding='utf-8') as f:
    f.write(new_content)"

						do shell script envPrefix & "python3 -c " & quoted form of pyScript & " " & quoted form of htmlFile

						-- Only import if file has content
						set htmlSize to (do shell script "wc -c < " & quoted form of htmlFile & " | tr -d ' '") as integer
						if htmlSize > 0 then
							set htmlRecord to import htmlFile to archiveGroup
							set URL of htmlRecord to recURL
							set tags of htmlRecord to {}
							set htmlImported to true
						end if
					on error errMsg
						log message "Extract: Web Content — monolith failed: " & errMsg info recName
					end try

					-- Clean up temp directory
					do shell script "rm -rf " & quoted form of workDir

					if mdImported or htmlImported then
						-- Rename the bookmark to match the sanitized title used
						-- for the derived files, so all three share the same name.
						add custom meta data 1 for "NameLocked" to theRecord
						set name of theRecord to safeTitle

						-- Cross-link all records via item link custom metadata
						set bookmarkLink to "x-devonthink-item://" & (uuid of theRecord)
						if mdImported then
							set mdLink to "x-devonthink-item://" & (uuid of mdRecord)
							add custom meta data bookmarkLink for "WebClipSource" to mdRecord
							add custom meta data mdLink for "WebClipMarkdown" to theRecord
						end if
						if htmlImported then
							set htmlLink to "x-devonthink-item://" & (uuid of htmlRecord)
							add custom meta data bookmarkLink for "WebClipSource" to htmlRecord
							add custom meta data htmlLink for "WebClipSnapshot" to theRecord
						end if

						-- Fast-track the bookmark: set all flags so it archives
						add custom meta data 1 for "Recognized" to theRecord
						add custom meta data 1 for "Commented" to theRecord
						add custom meta data 1 for "AIEnriched" to theRecord
						add custom meta data 1 for "TasksExtracted" to theRecord
						add custom meta data 1 for "DailyNotesProcessed" to theRecord
					else
						-- Both failed — let bookmark continue through pipeline as-is
						log message "Extract: Web Content — all downloads failed, passing through" info recName
						add custom meta data 1 for "Recognized" to theRecord
						add custom meta data 1 for "Commented" to theRecord
					end if

				on error errMsg number errNum
					log message "Extract: Web Content — error: " & errMsg & " (" & errNum & ")" info recName
					try
						do shell script "rm -rf " & quoted form of workDir
					end try
					-- Set flags so the bookmark doesn't get stuck
					add custom meta data 1 for "Recognized" to theRecord
					add custom meta data 1 for "Commented" to theRecord
				end try
			end if
		end repeat
	end tell
end performSmartRule
