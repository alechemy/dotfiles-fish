/*
 * theme-toggle.js — companion script for Readable-Universal.css
 *
 * Adds a floating circular button in the top-right of the preview
 * that toggles between light and dark themes. The CSS responds via
 * [data-theme="light"] and [data-theme="dark"] selectors on <html>.
 *
 * Install: DEVONthink > Settings > Files > Markdown > JavaScript
 *          (same place for DEVONthink To Go on iOS)
 */
(function () {
  "use strict";

  // Guard against double-injection if the script runs twice
  if (document.querySelector(".theme-toggle")) return;

  // Resolve starting theme from system preference
  var prefersDark =
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  var currentTheme = prefersDark ? "dark" : "light";

  // Apply initial theme to <html> so CSS [data-theme] rules match
  document.documentElement.setAttribute("data-theme", currentTheme);

  // Single SVG icon (half-moon in circle)
  var ICON_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' +
    'width="18" height="18" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
    'aria-hidden="true">' +
    '<circle cx="12" cy="12" r="9"/>' +
    '<path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor"/>' +
    "</svg>";

  // Build the button
  var button = document.createElement("button");
  button.className = "theme-toggle";
  button.type = "button";
  button.setAttribute("aria-label", "Toggle light/dark theme");
  button.setAttribute("title", "Toggle theme");
  button.innerHTML = ICON_SVG;

  button.addEventListener("click", function () {
    currentTheme = currentTheme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", currentTheme);
  });

  // Append once the body exists. Custom JS in DEVONthink sometimes
  // runs before DOMContentLoaded, so handle both cases.
  function inject() {
    if (document.body) {
      document.body.appendChild(button);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
})();
