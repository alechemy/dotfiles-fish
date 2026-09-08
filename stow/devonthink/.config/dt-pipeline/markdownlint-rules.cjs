const literalTypes = new Set([
  "codeFenced", "codeIndented", "codeText", "htmlFlow", "htmlText",
  "resource", "reference", "definition", "autolink", "literalAutolink",
]);

module.exports = {
  names: ["DT001", "escape-prose-tildes"],
  description: "Escape single prose tildes for DEVONthink",
  tags: ["devonthink"],
  parser: "micromark",
  function(params, onError) {
    function visit(token) {
      if (literalTypes.has(token.type)) return;
      // Shortcut and collapsed labels are also reference identifiers.
      if ((token.type === "link" || token.type === "image") &&
          !token.children.some(child => child.type === "resource" ||
            (child.type === "reference" && child.text !== "[]"))) return;
      if (token.type === "data") {
        for (const match of token.text.matchAll(/~+/g)) {
          if (match[0].length % 2 === 0) continue;
          const column = token.startColumn + match.index + match[0].length - 1;
          onError({
            lineNumber: token.startLine,
            range: [column, 1],
            fixInfo: {editColumn: column, insertText: "\\"},
          });
        }
      }
      token.children.forEach(visit);
    }
    params.parsers.micromark.tokens.forEach(visit);
  },
};
