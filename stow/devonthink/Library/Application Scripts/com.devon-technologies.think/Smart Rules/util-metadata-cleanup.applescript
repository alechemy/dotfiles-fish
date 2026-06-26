-- Util: Metadata Cleanup
--
-- Catch-all hourly cleanup rule for trivial metadata inconsistencies that
-- can accumulate over time (manual edits, cross-device sync, retired
-- flags, etc.). Each case is a self-contained handler that re-checks its
-- own preconditions; the rule's DT-level criteria are the explicit union
-- of currently-handled cases so DT pre-filters to just the records that
-- need work and yields zero in normal state.
--
-- Adding a new case: (1) add an OR group to the DT criteria covering the
-- new case's preconditions; (2) add a `my cleanupX(theRecord)` call in
-- performSmartRule; (3) implement the handler. The handler should
-- re-check preconditions defensively so it's safe even if DT yields a
-- record that matches a different case's criteria.
--
-- Current cleanups:
--   - cleanupSkipVsNeeds: enforce that SkipSingleFile and NeedsSingleFile
--     are never both 1 simultaneously. Skip wins (cleared on the Needs
--     side). To force-capture a previously-skipped bookmark, either
--     clear SkipSingleFile first or use the on-demand rule's selection
--     mode (which bypasses both flags).
--
-- DEVONthink smart rule configuration:
--   Search In:  Lorebook (entire database)
--   Criteria:   Kind is Bookmark
--               SkipSingleFile is On
--               NeedsSingleFile is On
--   Trigger:    Hourly
--   Action:     Execute Script → External → this file

on performSmartRule(theRecords)
  -- [follower-guard] only the DEVONthink pipeline driver mutates documents (see should-run-dt-driver)
  try
    do shell script "$HOME/.local/bin/should-run-dt-driver"
  on error
    return
  end try
	tell application id "DNtp"
		repeat with theRecord in theRecords
			try
				my cleanupSkipVsNeeds(theRecord)
				-- future cleanups go here, one handler call per case
			end try
		end repeat
	end tell
end performSmartRule

-- If both SkipSingleFile=1 and NeedsSingleFile=1, clear NeedsSingleFile.
-- Skip is the user's "off switch" and wins by design.
on cleanupSkipVsNeeds(theRecord)
	tell application id "DNtp"
		try
			set skipVal to (get custom meta data for "SkipSingleFile" from theRecord)
			set needsVal to (get custom meta data for "NeedsSingleFile" from theRecord)
			if skipVal is 1 and needsVal is 1 then
				add custom meta data 0 for "NeedsSingleFile" to theRecord
				my pipelineLog("Util: Metadata Cleanup", "INFO", "cleared NeedsSingleFile (SkipSingleFile=1)", name of theRecord as string, uuid of theRecord)
			end if
		end try
	end tell
end cleanupSkipVsNeeds

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
