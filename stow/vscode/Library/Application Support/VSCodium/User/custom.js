// Loaded by be5invis.vscode-custom-css. Toggles body.ws-suppress-deprecated
// when the active workspace matches a project that needs the legacy-Material
// strikethrough suppressed. Relies on `window.title: "${rootName}"`, so
// document.title is just the workspace folder name.
//
// document.title is set by VS Code well after this script runs and there's no
// DOM event for it, so we poll. The cost is a string check every 500ms.
(function () {
  // ffs-console is opened at src/main/webapp, so root name resolves to "webapp".
  const MATCH_WORKSPACES = ["webapp"];

  let last = null;
  const apply = () => {
    if (!document.body) return;
    const hit = MATCH_WORKSPACES.some((name) => (document.title || "").includes(name));
    if (hit !== last) {
      document.body.classList.toggle("ws-suppress-deprecated", hit);
      last = hit;
    }
  };

  apply();
  setInterval(apply, 500);
})();
