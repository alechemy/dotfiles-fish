-- Smart rule: mark new 00_INBOX arrivals as NeedsProcessing=1
--
-- Why: records that land in 00_INBOX via the global-inbox → Sweep
-- rule path already get NeedsProcessing set by the sweep. Records that
-- arrive directly (Drafts URL scheme with destination=<00_INBOX UUID>,
-- or any other direct-create path) bypass the sweep and would otherwise
-- sit untouched by the enrichment pipeline. This rule closes that gap.
--
-- DEVONthink smart rule configuration:
--   Search In:   00_INBOX
--   Events:      On Import  (add "On Move" if you also want to catch
--                            records moved in from elsewhere)
--   Conditions:  Custom Metadata → NeedsProcessing is empty
--                (redundant with the check below but filters the script
--                run cost so we don't fire on already-primed records)
--   Perform the following actions:
--     Execute Script → External → this file

on performSmartRule(theRecords)
  tell application id "DNtp"
    repeat with theRecord in theRecords
      set current to (get custom meta data for "NeedsProcessing" from theRecord)
      if current is missing value or current is "" then
        add custom meta data 1 for "NeedsProcessing" to theRecord
      end if
    end repeat
  end tell
end performSmartRule
