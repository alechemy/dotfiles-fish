# Append to Daily Note

## Drafts Action

Run Shortcut: Append to Daily Note
  Template: [[draft]]
  Wait for response: OFF

---

## Shortcut

Receives: Text (Share Sheet)

Date — Current Date
Format Date
  Format: Custom — `yyyy-MM-dd`

Match
  Pattern: `^[-*]\s`
  In: Shortcut Input
If Matches has no value
  Text: `- [Shortcut Input]`
Otherwise
  Text: `[Shortcut Input]`
End If

Set Variable: NewBullet = If Result

Search (DTTG)
  for: `name:[Formatted Date]`
  in group: Lorebook: 10_DAILY

If Items has any value
  Get DEVONthink item with UUID
  Get the content of item Item as file

  If File contains `## Today's Notes`
    Split Text
      Text: File
      By: Custom
      Separator: `## Today's Notes`
    Get Item from List
      Item: First Item       ← BeforePart (everything before the header)
    Get Item from List
      Item: Last Item        ← AfterPart (everything after the header)
    Text: (use actual Return keypresses, not \n)
      `[BeforePart][NewBullet]`↵
      `- `↵
      ↵
      `## Today's Notes[AfterPart]`
  Otherwise                  ← fresh note with no Today's Notes section yet
    Text:
      `[File]`↵
      `[NewBullet]`
  End If

  Update Item with Markdown document content
    Content: If Result
    Markdown Update Mode: Update (Options are "Update"/"Append"/"Insert")
Otherwise
  Create Document (DTTG)
    Title: `Draft intended for [Formatted Date]`
    Content: `[NewBullet]`
    Type: Markdown doc
    Location: Global Inbox
End If
