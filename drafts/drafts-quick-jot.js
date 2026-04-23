// Quick Jot → DEVONthink Daily Note
//
// Inserts the draft content into today's daily note as a timestamped
// bullet in the note body (before "## Today's Notes").
//
// macOS: modifies the daily note directly via AppleScript (instant)
// iOS:   creates a jot document (IsJot=1) → processed by smart rule on Mac
//
// Setup:
//   1. Create a new Drafts action with a single "Script" step
//   2. Paste this entire file as the script content
//   3. (iOS only) Install the "Process Jots" smart rule in DEVONthink

const macScript = `
on getDailyNote(dateStr)
	tell application id "DNtp"
		set g to get record at "/10_DAILY" in database "Lorebook"
		set hits to search ("name:\\"" & dateStr & "\\" kind:markdown") in g
		if (count of hits) > 0 then
			set r to item 1 of hits
			return (uuid of r) & "<<<SPLIT>>>" & (plain text of r)
		else
			return "NOT_FOUND"
		end if
	end tell
end getDailyNote

on updateDailyNote(uuidStr, newBody)
	tell application id "DNtp"
		set r to get record with uuid uuidStr
		set plain text of r to newBody
	end tell
end updateDailyNote

on createFallback(dateStr, lineText)
	tell application id "DNtp"
		set r to create record with {name:"Jot " & dateStr, type:markdown, plain text:lineText} in incoming group
		add custom meta data 1 for "IsJot" to r
	end tell
end createFallback
`;

(() => {
  const text = draft.content.trim();
  if (!text) {
    context.fail("Empty draft");
    return;
  }

  const now = new Date();
  let h = now.getHours();
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ampm = h >= 12 ? "pm" : "am";
  h = h % 12 || 12;
  const line = `- ${h}:${mm}${ampm}: ${text}`;

  const dateStr = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");

  if (device.systemName === "macOS") {
    macInsert(dateStr, line);
  } else {
    iosCreateJot(dateStr, line, text);
  }
})();

// ── macOS: direct AppleScript ──────────────────────────────────────

function macInsert(dateStr, line) {
  const script = AppleScript.create(macScript);

  if (!script.execute("getDailyNote", [dateStr])) {
    alert("GET ERROR:\n" + script.lastError);
    context.fail();
    return;
  }

  const result = script.lastResult;
  if (!result || result === "NOT_FOUND") {
    macFallback(dateStr, line);
    return;
  }

  const raw = result;
  const sep = raw.indexOf("<<<SPLIT>>>");
  const uuid = raw.substring(0, sep);
  const body = raw.substring(sep + 11).replace(/\r\n?/g, "\n");

  const newBody = insertBeforeTodaysNotes(body, line);

  if (!script.execute("updateDailyNote", [uuid, newBody])) {
    alert("WRITE ERROR:\n" + script.lastError);
    context.fail();
    return;
  }
}

function macFallback(dateStr, line) {
  const script = AppleScript.create(macScript);
  if (!script.execute("createFallback", [dateStr, line])) {
    alert("FALLBACK ERROR:\n" + script.lastError);
    context.fail();
    return;
  }
  app.displayInfoMessage("Daily note missing — saved to Inbox");
}

// ── iOS: create jot for smart-rule processing ──────────────────────
// DTTG's x-callback-url can't set custom metadata, so the smart rule
// matches on the "Jot YYYY-MM-DD" name prefix for iOS-created records.

function iosCreateJot(dateStr, line, rawText) {
  const cb = CallbackURL.create();
  cb.baseURL = "x-devonthink://x-callback-url/createmarkdown";
  cb.addParameter("title", "Jot " + dateStr);
  cb.addParameter("text", line);
  cb.waitForResponse = true;

  if (!cb.open()) {
    app.displayErrorMessage("Could not reach DEVONthink");
    context.fail();
  }
}

// ── Text insertion ─────────────────────────────────────────────────

function insertBeforeTodaysNotes(body, jotLine) {
  const lines = body.split("\n");
  const marker = "## Today's Notes";
  const emptyBullet = /^\s*[-*]\s*$/;
  const contentBullet = /^\s*[-*]\s+\S/;

  let h2 = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim() === marker) {
      h2 = i;
      break;
    }
  }

  if (h2 === -1) {
    lines.push("", jotLine);
    return lines.join("\n");
  }

  // Prefer inserting after the most recent content-carrying bullet
  let lastContent = -1;
  for (let i = h2 - 1; i >= 0; i--) {
    if (contentBullet.test(lines[i])) {
      lastContent = i;
      break;
    }
  }

  if (lastContent !== -1) {
    let insertAt = lastContent + 1;
    while (insertAt < h2 && /^[ \t]/.test(lines[insertAt])) {
      insertAt++;
    }
    lines.splice(insertAt, 0, jotLine);
    return lines.join("\n");
  }

  // No content bullets — replace an empty placeholder bullet from the template
  for (let i = h2 - 1; i >= 0; i--) {
    if (emptyBullet.test(lines[i])) {
      lines[i] = jotLine;
      return lines.join("\n");
    }
  }

  // Neither — insert with surrounding blank lines
  let ins = h2;
  while (ins > 0 && lines[ins - 1].trim() === "") ins--;
  lines.splice(ins, h2 - ins, "", jotLine, "");
  return lines.join("\n");
}
