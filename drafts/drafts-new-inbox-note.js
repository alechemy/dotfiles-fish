// New Note → DEVONthink Inbox
//
// Sends the draft to the DEVONthink inbox as a Markdown record.
// Decides whether the first line is a sensible title:
//   - Starts with `#`               → use as title, keep body intact
//   - Short line (≤ 80 chars) that
//     is the only line or is
//     followed by a blank line      → promote to H1, use as title
//   - Otherwise (blob of text)      → generic timestamped name,
//                                     body untouched
//
// Setup:
//   1. Create a new Drafts action with a single "Script" step
//   2. Paste this entire file as the script content

const TITLE_MAX_LEN = 80;
const INBOX_GROUP_UUID = "E618E3D8-DB98-4822-B577-7673F8F647CF";

(() => {
  const text = draft.content.trim();
  if (!text) {
    context.fail("Empty draft");
    return;
  }

  const { title, body } = resolveTitleAndBody(text);

  const cb = CallbackURL.create();
  cb.baseURL = device.systemName === "macOS"
    ? "x-devonthink://createMarkdown"
    : "x-devonthink://x-callback-url/createmarkdown";
  cb.addParameter("title", title);
  cb.addParameter("text", body);
  cb.addParameter("destination", INBOX_GROUP_UUID);
  cb.waitForResponse = false;

  if (!cb.open()) {
    app.displayErrorMessage("Could not reach DEVONthink");
    context.fail();
  }
})();

// ── Title resolution ───────────────────────────────────────────────

function resolveTitleAndBody(text) {
  const lines = text.split("\n");
  const first = lines[0];

  const headerMatch = first.match(/^#+\s+(.+)$/);
  if (headerMatch) {
    return { title: headerMatch[1].trim(), body: text };
  }

  const onlyLine = lines.length === 1;
  const blankAfter = lines.length > 1 && lines[1].trim() === "";
  if (first.length <= TITLE_MAX_LEN && (onlyLine || blankAfter)) {
    const body = "# " + first + (onlyLine ? "" : "\n" + lines.slice(1).join("\n"));
    return { title: first.trim(), body };
  }

  return { title: genericTitle(), body: text };
}

function genericTitle() {
  const now = new Date();
  const date = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");

  let h = now.getHours();
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;

  return `New Markdown Note ${date} at ${h}.${mm}.${ss}${ampm}`;
}
