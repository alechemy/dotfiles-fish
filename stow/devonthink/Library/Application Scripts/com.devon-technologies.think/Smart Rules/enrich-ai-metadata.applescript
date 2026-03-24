on performSmartRule(theRecords)
  tell application id "DNtp"
    set maxWaitSeconds to 300 -- 5 minutes
    set theRole to "You are a document cataloguing assistant that responds only in JSON."
    set theInstructions to "Based on this document, respond with ONLY a JSON object containing the following keys:" & linefeed & linefeed & ¬
      "- \"title\": A concise, descriptive document title in English. Do NOT include a date prefix or suffix (e.g. do not append '2/20/26' or '2026-02-20' to the title) — dates are handled separately via the \"eventDate\" field below. If the filename or heading contains a date (e.g. \"March 17 Round Table\", \"2/20/26 Meeting Notes\"), strip the date portion and use only the descriptive remainder as the title (e.g. \"Round Table\", \"Meeting Notes\") — capture the date in eventDate instead. If the document already has a clear, descriptive title — whether from its filename (e.g. \"Sony A95K Television User Manual.pdf\") or from a heading/title within its content (e.g. an article headline or a title page) — preserve that title as-is (after removing any date portion) rather than rephrasing or summarizing it. Only generate a new title when the existing name is generic or non-descriptive (e.g. \"Untitled\", \"IMG_0042\", \"Document 1\", \"Notebook-7\")." & linefeed & ¬
      "- \"eventDate\": A date string in strict yyyy-mm-dd format (e.g. \"2025-03-14\"), or an empty string. Set this ONLY for documents where a specific date is intrinsic to their meaning — i.e. knowing WHEN matters for understanding or filing the document. Examples: a receipt, a restaurant bill, meeting notes, a phone call log, a journal entry, an appointment, a bank statement for a specific day, a conversation log. The date may come from a date in the document's filename or title (e.g. \"March 17 Round Table\" → \"2026-03-17\"), OR from an explicit date in the document's content, OR from relative time references like 'today' or 'this week' resolved against the file dates provided below, OR — for inherently event-tied document types only (e.g. receipts, journal entries, meeting notes) when the content contains no date — from the file creation date. When a date appears without an explicit year and the previous year would be more plausible given the document's creation date, assume the previous year. Do NOT set it for documents that span a period, are reference material, or are not event-anchored — e.g. a W-2 (covers a full tax year), an annual report, a manual, a lease, a bookmark, a contract with a term, a reference document, a technical note, a study note, a how-to guide, notes explaining a system or process, a brainstorm or design document. A note is NOT event-anchored merely because it was written on a specific day — the content itself must be about a specific dated event or occurrence. Do NOT fall back to file creation date for reference-style documents (technical notes, study notes, explainers, etc.). Do NOT construct or infer a date from a referenced period (e.g. do not return 2024-01-01 or 2024-12-31 for a document covering tax year 2024). Return \"\" when the document is not anchored to a single specific event or date." & linefeed & ¬
      "- \"type\": A single Title-Cased label for the document type (e.g. \"Receipt\", \"Invoice\", \"Meeting Notes\", \"Article\", \"Letter\", \"Manual\", \"Handwritten Note\", \"Contract\")." & linefeed & ¬
      "- \"tags\": An array of 1–3 concise, singular, Title-Cased organizational tags that describe the theme or primary topic of the document. Prefer selecting from the existing database tags listed at the end of this prompt when an applicable tag exists; only create a new tag when no existing tag is a reasonable fit. Do not duplicate the \"type\" value here." & linefeed & ¬
      "- \"summary\": A 1–2 sentence plain-English summary of the document's content." & linefeed & ¬
      "- \"lowConfidence\": A boolean (true or false). Set to true only if the document content is too unclear, ambiguous, or incomplete to produce a reliable title and summary. Otherwise false."

    -- Collect existing tags from the database so the LLM prefers reuse over creating near-duplicates
    if (count of theRecords) > 0 then
      try
        set db to database of (item 1 of theRecords)
        set tagGroups to tag groups of db
        set tagNames to {}
        repeat with tg in tagGroups
          set end of tagNames to name of tg
        end repeat
        if (count of tagNames) > 0 then
          set tid2 to AppleScript's text item delimiters
          set AppleScript's text item delimiters to ", "
          set existingTagString to tagNames as text
          set AppleScript's text item delimiters to tid2
          set theInstructions to theInstructions & linefeed & linefeed & "Existing tags in this database (prefer these over creating new tags): " & existingTagString
        end if
      end try
    end if

    repeat with theRecord in theRecords
      set recName to name of theRecord

      -- Safeguard: pull from pipeline if too many consecutive errors
      set _skipRecord to false
      try
        set currentErrors to (get custom meta data for "ErrorCount" from theRecord)
        if currentErrors is not missing value and currentErrors is not "" and currentErrors ≥ 10 then
          log message "Enrich AI Metadata: ErrorCount=" & currentErrors & " exceeds limit, removing from pipeline" info recName
          add custom meta data 1 for "AIEnriched" to theRecord
          add custom meta data 0 for "NeedsProcessing" to theRecord
          set _skipRecord to true
        end if
      end try

      if not _skipRecord then

      -- Stamp first-attempt time so we can enforce a timeout
      set enrichStart to (get custom meta data for "EnrichStartedAt" from theRecord)
      if enrichStart is missing value or enrichStart is "" then
        add custom meta data (current date) for "EnrichStartedAt" to theRecord
      end if

      -- Determine if we should filter text before passing to AI
      set isHandwritten to (get custom meta data for "Handwritten" from theRecord)
      set recType to (type of theRecord) as string

      set useFilteredText to false
      set docText to ""

      if isHandwritten is 1 then
        set docText to comment of theRecord
        set useFilteredText to true
        set theMode to "text"
      else if recType is "markdown" or recType is "txt" or recType is "rtf" then
        set docText to plain text of theRecord
        set useFilteredText to true
        set theMode to "text"
      else
        set theMode to "auto"
      end if

      set filteredText to ""
      if useFilteredText is true then
        set pyScript to "import sys, re" & linefeed & ¬
            "text = sys.stdin.read()" & linefeed & ¬
            "lines = text.splitlines()" & linefeed & ¬
            "output_lines = []" & linefeed & ¬
            "skip_section = False" & linefeed & ¬
            "target_headers_re = re.compile(r'^\\s*#+\\s*(Daily Notes?|Today|Journal|Action Items|Todos|To-Dos|To Do|Tasks):?\\s*$', re.IGNORECASE)" & linefeed & ¬
            "header_re = re.compile(r'^\\s*#+\\s')" & linefeed & ¬
            "for line in lines:" & linefeed & ¬
            "    if target_headers_re.match(line):" & linefeed & ¬
            "        skip_section = True" & linefeed & ¬
            "        continue" & linefeed & ¬
            "    if skip_section:" & linefeed & ¬
            "        if header_re.match(line):" & linefeed & ¬
            "            skip_section = False" & linefeed & ¬
            "        else:" & linefeed & ¬
            "            continue" & linefeed & ¬
            "    output_lines.append(line)" & linefeed & ¬
            "print('\\n'.join(output_lines), end='')"

        set tmpPath to do shell script "mktemp /tmp/dt-enrich.XXXXXX"
        set fileRef to open for access (POSIX file tmpPath) with write permission
        write docText to fileRef as «class utf8»
        close access fileRef
        set filteredText to do shell script "/usr/bin/python3 -c " & quoted form of pyScript & " < " & quoted form of tmpPath
        do shell script "rm -f " & quoted form of tmpPath

        -- Fallback: if filtering removed all content (note contains ONLY tasks/journal entries),
        -- use the full text so enrichment can still produce a meaningful title and summary.
        if filteredText is "" then
          set filteredText to docText
        else
          set tmpCheck to do shell script "mktemp /tmp/dt-enrich.XXXXXX"
          set chkRef to open for access (POSIX file tmpCheck) with write permission
          write filteredText to chkRef as «class utf8»
          close access chkRef
          set filteredStripped to do shell script "/usr/bin/python3 -c 'import sys; print(sys.stdin.read().strip())' < " & quoted form of tmpCheck
          do shell script "rm -f " & quoted form of tmpCheck
          if filteredStripped is "" then set filteredText to docText
        end if
      end if


      try
        if useFilteredText is true then
          -- Include file dates so the model can use them for eventDate
          set recCreated to creation date of theRecord
          set recModified to modification date of theRecord
          set dateMetadata to "File created: " & (recCreated as «class isot» as string) & linefeed & "File modified: " & (recModified as «class isot» as string)
          set finalPrompt to theInstructions & linefeed & linefeed & "Record name: " & recName & linefeed & dateMetadata & linefeed & linefeed & "Document Content:" & linefeed & filteredText
          set jsonResult to get chat response for message finalPrompt ¬
            role theRole ¬
            mode theMode ¬
            thinking false ¬
            tool calls false ¬
            as "JSON"
        else
          set jsonResult to get chat response for message theInstructions ¬
            record theRecord ¬
            role theRole ¬
            mode theMode ¬
            thinking false ¬
            tool calls false ¬
            as "JSON"
        end if

        -- `as "JSON"` normally returns an AppleScript record, but some models
        -- wrap the response in an array ([{…}]).  Unwrap if needed.
        if class of jsonResult is list then
          if (count of jsonResult) > 0 then
            set jsonResult to item 1 of jsonResult
          end if
        end if

        set theTitle to ""
        try
          set theTitle to |title| of jsonResult
        end try

        try
          if theTitle is not "" then
            -- Clean up any prefix or trailing dates that AI might have included despite instructions
            set pyScript to "import os, re" & linefeed & ¬
              "t = os.environ.get('THE_TITLE', '')" & linefeed & ¬
              "t = re.sub(r'^\\s*\\d{1,4}\\s*[-/.]\\s*\\d{1,2}(?:\\s*[-/.]\\s*\\d{1,4})?\\s*[-_]*\\s*', '', t)" & linefeed & ¬
              "t = re.sub(r'(?i)^\\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2}(?:st|nd|rd|th)?(?:,?\\s+\\d{4})?\\s*[-_]*\\s*', '', t)" & linefeed & ¬
              "t = re.sub(r'\\s*[-_]*\\s*\\d{1,4}\\s*[-/.]\\s*\\d{1,2}(?:\\s*[-/.]\\s*\\d{1,4})?\\s*$', '', t)" & linefeed & ¬
              "t = re.sub(r'(?i)\\s*[-_]*\\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2}(?:st|nd|rd|th)?(?:,?\\s+\\d{4})?\\s*$', '', t)" & linefeed & ¬
              "print(t.strip(), end='')"
            set theTitle to do shell script "export THE_TITLE=" & quoted form of theTitle & " && /usr/bin/python3 -c " & quoted form of pyScript
          end if
        on error errCleanup
          -- Fallback to the original title if regex cleanup fails
        end try

        set theDate to ""
        try
          set theDate to |eventDate| of jsonResult
          -- AI may return "null" as a string or missing value
          if theDate is missing value then set theDate to ""
          if theDate is "null" then set theDate to ""
          -- Validate yyyy-mm-dd format (10 chars, dashes at positions 5 & 8)
          if theDate is not "" then
            if (count of theDate) is not 10 ¬
              or character 5 of theDate is not "-" ¬
              or character 8 of theDate is not "-" then
              set theDate to ""
            end if
          end if
        end try

        set theType to ""
        try
          set theType to |type| of jsonResult
        end try

        set tagList to {}
        try
          set tagList to |tags| of jsonResult
        end try

        set theSummary to ""
        try
          set theSummary to |summary| of jsonResult
        end try

        set isLowConfidence to false
        try
          set isLowConfidence to |lowConfidence| of jsonResult
        end try

        -- Prepend date to title if present
        if theDate is not "" and theTitle is not "" then
          set theTitle to theDate & " " & theTitle
        end if

        -- Apply title unless NameLocked
        if theTitle is not "" then
          set nameLocked to (get custom meta data for "NameLocked" from theRecord)
          if nameLocked is not 1 then
            -- Sanitize title: strip characters illegal in HFS+/APFS filenames
            set sanitized to theTitle
            set tid3 to AppleScript's text item delimiters
            repeat with badChar in {"/", ":"}
              set AppleScript's text item delimiters to badChar
              set sanitized to text items of sanitized
              set AppleScript's text item delimiters to " - "
              set sanitized to sanitized as text
            end repeat
            set AppleScript's text item delimiters to tid3
            -- Collapse duplicate " - " runs left by adjacent : or / replacements
            set tid4 to AppleScript's text item delimiters
            repeat
              if sanitized contains " -  - " then
                set AppleScript's text item delimiters to " -  - "
                set sanitized to text items of sanitized
                set AppleScript's text item delimiters to " - "
                set sanitized to sanitized as text
              else if sanitized contains " - - " then
                set AppleScript's text item delimiters to " - - "
                set sanitized to text items of sanitized
                set AppleScript's text item delimiters to " - "
                set sanitized to sanitized as text
              else
                exit repeat
              end if
            end repeat
            set AppleScript's text item delimiters to tid4
            -- Trim leading/trailing whitespace
            repeat while sanitized starts with " "
              set sanitized to text 2 thru -1 of sanitized
            end repeat
            repeat while sanitized ends with " "
              set sanitized to text 1 thru -2 of sanitized
            end repeat

            if sanitized is not "" then
              -- Set NameLocked BEFORE renaming so the on-rename guard rule
              -- (whose criteria require NameLocked is Off) won't match this rename.
              add custom meta data 1 for "NameLocked" to theRecord
              try
                -- Extract extension from the record's name (using recName instead of
                -- filename, which can return a non-text type after OCR transforms)
                set oldTID to AppleScript's text item delimiters
                set AppleScript's text item delimiters to "."
                set fnParts to text items of recName
                set AppleScript's text item delimiters to oldTID
                set newName to sanitized
                if (count of fnParts) > 1 then
                  set newName to sanitized & "." & (last item of fnParts)
                end if
                -- Snapshot current name so the user can revert if needed
                add custom meta data recName for "PreviousName" to theRecord
                set name of theRecord to newName
              on error errMsg
                -- Rename failed — log it but let tags/summary proceed
                log message "Enrich AI Metadata rename failed: " & errMsg info (name of theRecord)
              end try
            end if
          end if
        end if

        -- Apply tags (deduplicate against existing tags)
        if (count of tagList) > 0 then
          set existingTags to (get tags of theRecord)
          set newTags to {}
          repeat with aTag in tagList
            set tagAlreadyExists to false
            repeat with existingTag in existingTags
              if (existingTag as text) is (aTag as text) then
                set tagAlreadyExists to true
                exit repeat
              end if
            end repeat
            if not tagAlreadyExists then set end of newTags to (aTag as text)
          end repeat
          if (count of newTags) > 0 then
            set tags of theRecord to existingTags & newTags
          end if
        end if

        -- Apply summary
        if theSummary is not "" then
          add custom meta data theSummary for "summary" to theRecord
        end if

        -- Apply document type (force "Handwritten Note" for handwritten records)
        if isHandwritten is 1 then
          add custom meta data "Handwritten Note" for "DocumentType" to theRecord
        else if theType is not "" then
          add custom meta data theType for "DocumentType" to theRecord
        end if

        -- Apply event date as custom metadata (clear if no valid date)
        if theDate is not "" then
          add custom meta data theDate for "EventDate" to theRecord
        else
          add custom meta data "" for "EventDate" to theRecord
        end if

        -- Flag low-confidence results for manual review
        if isLowConfidence is true then
          add custom meta data 1 for "LowConfidence" to theRecord
        end if

        -- Success — advance the record
        add custom meta data 1 for "AIEnriched" to theRecord

        -- Strip import-automation tags so they don't pollute the tag pool
        try
          set currentTags to (get tags of theRecord)
          set cleanedTags to {}
          repeat with i from 1 to count of currentTags
            if (item i of currentTags) is not "Hazel-to-DT" then set end of cleanedTags to (item i of currentTags)
          end repeat
          set tags of theRecord to cleanedTags
        end try

        -- Propagate summary and tags to linked web clip records (bookmark + HTML snapshot).
        -- Web clip markdown records have WebClipSource pointing to the original bookmark,
        -- and the bookmark has WebClipSnapshot pointing to the HTML archive.
        try
          set clipSource to (get custom meta data for "WebClipSource" from theRecord)
          if clipSource is not missing value and clipSource is not "" then
            set bookmarkUUID to do shell script "echo " & quoted form of clipSource & " | sed 's|x-devonthink-item://||'"
            set bookmarkRecord to get record with uuid bookmarkUUID
            if bookmarkRecord is not missing value then
              if theSummary is not "" then add custom meta data theSummary for "summary" to bookmarkRecord
              if (count of tagList) > 0 then set tags of bookmarkRecord to tags of theRecord
              -- Follow the chain to the HTML snapshot
              set snapshotLink to (get custom meta data for "WebClipSnapshot" from bookmarkRecord)
              if snapshotLink is not missing value and snapshotLink is not "" then
                set snapshotUUID to do shell script "echo " & quoted form of snapshotLink & " | sed 's|x-devonthink-item://||'"
                set snapshotRecord to get record with uuid snapshotUUID
                if snapshotRecord is not missing value then
                  if theSummary is not "" then add custom meta data theSummary for "summary" to snapshotRecord
                  if (count of tagList) > 0 then set tags of snapshotRecord to tags of theRecord
                end if
              end if
            end if
          end if
        end try

      on error errMsg
        -- Check if we've been retrying too long
        set enrichStart to (get custom meta data for "EnrichStartedAt" from theRecord)
        if enrichStart is not missing value and enrichStart is not "" then
          set elapsed to (current date) - enrichStart
          if elapsed > maxWaitSeconds then
            log message "Enrich AI Metadata: timed out after " & elapsed & "s, advancing without enrichment" info recName
            add custom meta data 1 for "AIEnriched" to theRecord
            try
              set currentErrors to (get custom meta data for "ErrorCount" from theRecord)
              if currentErrors is missing value or currentErrors is "" then set currentErrors to 0
              add custom meta data (currentErrors + 1) for "ErrorCount" to theRecord
            end try
          else
            log message "Enrich AI Metadata: enrichment failed (" & elapsed & "s elapsed), will retry next poll: " & errMsg info recName
          end if
        else
          log message "Enrich AI Metadata: enrichment failed, will retry next poll: " & errMsg info recName
        end if
      end try
      end if
    end repeat
  end tell
end performSmartRule
