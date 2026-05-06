-- Capture Bookmarks Batch (On Demand)
--
-- Thin wrapper around ~/.local/bin/capture-bookmarks-batch.py.
--
-- If invoked with records selected (e.g. from the smart rule's record list
-- or Tools → Apply Rules on a selection), passes those UUIDs to the batch
-- script which captures only those bookmarks (dedup against existing
-- WebClipSnapshots still applies).
--
-- If invoked with nothing selected, the batch script drains the full queue
-- of bookmarks with NeedsSingleFile=1.
--
-- Runs in the background — per-URL navigation + SingleFile save + defuddle
-- + DT import takes a while. Progress is written to
-- ~/Library/Logs/singlefile-ingest.log.

on performSmartRule(theRecords)
	set batchPath to "/Users/alec/.local/bin/capture-bookmarks-batch.py"
	set logPath to (POSIX path of (path to home folder)) & "Library/Logs/singlefile-ingest.log"
	-- PATH must include mise shims (defuddle), Homebrew (fswatch, magick),
	-- and ~/.local/bin (capture-with-singlefile, ingester).
	set envSetup to "export PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.local/share/mise/bin:$HOME/.local/share/mise/shims:$PATH; "

	set uuidArgs to ""
	try
		tell application id "DNtp"
			repeat with r in theRecords
				set uuidArgs to uuidArgs & " --uuid " & (uuid of r)
			end repeat
		end tell
	end try

	set cmd to envSetup & "nohup " & quoted form of batchPath & uuidArgs & " >> " & quoted form of logPath & " 2>&1 &"
	do shell script cmd
	if uuidArgs is "" then
		display notification "Draining NeedsSingleFile queue in the background" with title "SingleFile Batch"
	else
		display notification "Capturing selected bookmark(s) in the background" with title "SingleFile Batch"
	end if
end performSmartRule
