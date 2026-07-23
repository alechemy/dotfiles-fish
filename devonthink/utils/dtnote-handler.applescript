-- DTNote.app — dtnote:// URL-scheme handler.
--
-- Built by scripts/build-dtnote-handler.sh into ~/Applications/DTNote.app
-- (CFBundleURLSchemes: dtnote, LSUIElement so no Dock flash). The briefing's
-- create-on-click event links route here; all real work — lookup by
-- LinkedEvent key, create-if-missing, open in DEVONthink — lives in
-- dtnote-open.py, which talks to DT through entity-dt-bridge.js under
-- /usr/bin/osascript. This applet therefore sends no AppleEvents of its own,
-- so rebuilding it (which rotates its ad-hoc code signature) never costs an
-- Automation grant.

on open location theURL
	try
		do shell script "/usr/bin/python3 $HOME/.local/bin/dtnote-open.py " & quoted form of theURL
	on error errMsg
		display notification errMsg with title "DTNote"
	end try
end open location
