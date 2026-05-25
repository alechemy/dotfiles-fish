// Loaded by be5invis.vscode-custom-css. Toggles body.ws-suppress-deprecated
// when the active workspace matches a project that needs the legacy-Material
// strikethrough suppressed. Relies on `window.title: "${rootName}"`, so
// document.title is just the workspace folder name.
//
// Subscribes to title mutations rather than polling. VS Code updates the
// window title by mutating the <title> element's text node, which fires a
// characterData mutation on its child text node — both observed here.
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

  // Observe the <title> directly when it exists; fall back to <head> with
  // subtree=true in case the script runs before <title> is in the DOM (which
  // shouldn't happen under vscode-custom-css's load order, but the fallback
  // costs nothing — apply() is just a string-includes check).
  const target = document.querySelector("title") || document.head;
  if (target) {
    new MutationObserver(apply).observe(target, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  }
})();
