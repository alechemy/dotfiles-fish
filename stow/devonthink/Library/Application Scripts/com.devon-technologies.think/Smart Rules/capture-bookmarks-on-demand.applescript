-- Capture Bookmarks Batch (On Demand)
--
-- Thin wrapper around ~/.local/bin/capture-bookmarks-batch.py. The batch
-- script queries DT itself for bookmarks with NeedsSingleFile=1 and drives
-- Chromium + SingleFile to capture each, then invokes
-- ~/.local/bin/ingest-singlefile-html.py to create the cross-linked
-- bookmark / HTML snapshot / markdown records.
--
-- theRecords is intentionally ignored: the script does its own lookup, so
-- triggering this rule on any selection (or from Tools → Apply Rules with
-- nothing specific selected) fires the full queue drain. No arguments need
-- to be passed in.
--
-- Runs in the background — the batch takes a while (per-URL browser
-- navigation, SingleFile save, defuddle, DT import). All progress is
-- written to ~/Library/Logs/singlefile-ingest.log.

on performSmartRule(theRecords)
	set batchPath to "/Users/alec/.local/bin/capture-bookmarks-batch.py"
	set logPath to (POSIX path of (path to home folder)) & "Library/Logs/singlefile-ingest.log"
	-- PATH must include mise shims (defuddle), Homebrew (fswatch, magick),
	-- and ~/.local/bin (capture-with-singlefile, ingester).
	set envSetup to "export PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.local/share/mise/bin:$HOME/.local/share/mise/shims:$PATH; "
	set cmd to envSetup & "nohup " & quoted form of batchPath & " >> " & quoted form of logPath & " 2>&1 &"
	do shell script cmd
	display notification "Capturing queued bookmarks in the background" with title "SingleFile Batch"
end performSmartRule
