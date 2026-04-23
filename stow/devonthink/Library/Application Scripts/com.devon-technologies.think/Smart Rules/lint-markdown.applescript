-- Lint Markdown on Ingest
--
-- Applies `markdownlint --fix` plus house-style sed transforms to
-- incoming Markdown records. Non-Markdown records are skipped silently.
--
-- IMPORTANT — operates on DT's in-memory `plain text`, not on the
-- on-disk file. The earlier version did `sed -i` + `markdownlint --fix`
-- directly on `path of theRecord` and then called `synchronize record`,
-- which races with DT's buffered write of `set plain text of newRecord`
-- for programmatically-created records (e.g. from the summarize and
-- prose-check skills). If the rule fires before DT flushes, the disk
-- file is stale or empty, the sed pipeline runs on that, and
-- `synchronize record` then overwrites DT's authoritative in-memory
-- content with the stale/empty disk state — silently wiping the record.
-- Operating on `plain text` round-tripped through a temp file keeps DT
-- as the source of truth.
--
-- Intended to run as the first action in Extract: Native Text Bypass,
-- before the declarative flag-setting actions.

on performSmartRule(theRecords)
	tell application id "DNtp"
		repeat with theRecord in theRecords
			set recType to type of theRecord as string
			if recType is in {"markdown", "«constant ****mkdn»"} then
				try
					set currentText to plain text of theRecord
					if currentText is missing value then set currentText to ""
					if currentText is not "" then
						set tmpPath to do shell script "mktemp /tmp/dt-lint.XXXXXX.md"
						set fileRef to open for access (POSIX file tmpPath) with write permission
						set eof of fileRef to 0
						write currentText to fileRef as «class utf8»
						close access fileRef

						do shell script "$HOME/.local/bin/lint-markdown-file " & quoted form of tmpPath

						set newText to do shell script "cat " & quoted form of tmpPath without altering line endings
						do shell script "rm -f " & quoted form of tmpPath

						if newText is not currentText and newText is not "" then
							set plain text of theRecord to newText
						end if
					end if
				on error errMsg
					log message "Lint Markdown: failed for " & (name of theRecord) & ": " & errMsg
					try
						close access fileRef
					end try
				end try
			end if
		end repeat
	end tell
end performSmartRule
