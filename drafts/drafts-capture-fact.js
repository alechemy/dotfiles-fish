// Capture Person Fact → DEVONthink Entity Layer
//
// Sends the draft to the /20_ENTITIES/_Facts group in the Lorebook database
// as a Markdown record. entity-filing.py discovers it as a `fact` source,
// resolves the named person against the roster through the local LLM, and
// files the fact (auto-applied when the person is named clearly, otherwise a
// review proposal). Same code path on macOS and iOS — DEVONthink / DEVONthink
// To Go both accept `destination` as a group UUID, and the UUID is stable
// across sync.
//
// The body leads with an H1 equal to the record title so the global
// "Sync H1 and Filename" smart rule is a no-op (entity-filing strips it before
// extraction).
//
// Setup:
//   1. Run `~/.local/bin/dt-entity-bootstrap` once; it prints the _Facts
//      group UUID. Paste it into FACTS_GROUP_UUID below.
//   2. Create a new Drafts action "Capture Person Fact" with a single
//      "Script" step and paste this entire file as the script content.

const FACTS_GROUP_UUID = "PASTE-YOUR-_FACTS-GROUP-UUID-HERE";

(() => {
  const text = draft.content.trim();
  if (!text) {
    context.fail("Empty draft");
    return;
  }

  const title = factTitle();
  const isMac = device.systemName === "macOS";

  const cb = CallbackURL.create();
  cb.baseURL = isMac
    ? "x-devonthink://createMarkdown"
    : "x-devonthink://x-callback-url/createmarkdown";
  cb.addParameter("title", title);
  cb.addParameter("text", `# ${title}\n\n${text}`);
  cb.addParameter("destination", FACTS_GROUP_UUID);
  // DTTG's x-callback-url returns x-success only after the record is created,
  // so wait for it and treat a non-success as a failed capture; the Mac plain
  // scheme sends no callback, so there only dispatch is confirmable.
  cb.waitForResponse = !isMac;

  if (!cb.open()) {
    app.displayErrorMessage(isMac
      ? "Could not reach DEVONthink"
      : "DEVONthink didn't confirm the fact — check the _Facts group UUID");
    context.fail();
  }
})();

function factTitle() {
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

  return `Fact ${date} at ${h}.${mm}.${ss}${ampm}`;
}
