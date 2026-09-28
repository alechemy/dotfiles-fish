# Inline review in Hunk

The parent Pi session automatically opens a Hunk pane for an ordinary code review in native cmux, unless the user requests text-only output. Keep review generation, independent reviewer assignments, evidence checks, and final synthesis in Pi. Children must not open viewers or import findings. Outside native cmux, keep the grouped report; do not launch an interactive TUI in the Bash tool or start another agent.

Use the bundled `../hunk_review.py` with `/usr/bin/python3`. The commands below assume `helper` is its absolute path. Resolve it relative to this reference file, not the repository being reviewed. Load the current vendor command reference with `hunk skill path` when you need Hunk navigation or highlighting syntax. The local launcher replaces the vendor skill's instruction to ask the human to open Hunk; its other live-session guidance still applies.

## Capture and open

After selecting the scope, call `prepare` before launching reviewers. It resolves revisions, records the comparison, and saves the patch outside the repository. Keep the returned review ID and patch path in the parent conversation and include that comparison in every reviewer packet.

| Selected scope | Prepare arguments |
| --- | --- |
| One ordinary commit | `--mode endpoints --base <parent-sha> --head <commit-sha>` |
| Branch or PR | `--mode endpoints --base <merge-base-sha> --head <head-sha>` |
| Explicit endpoints | `--mode endpoints --base <base-sha> --head <head-sha>` |
| Root commit | `--mode root --head <root-sha>` |
| Staged changes | `--mode staged` |
| Unstaged tracked changes | `--mode unstaged` |
| All uncommitted changes | `--mode worktree --include-untracked` |
| Baseline through work in progress | `--mode since --base <merge-base-sha> --include-untracked` |

Pass `--repo /absolute/worktree` in every prepare call. Omit `--include-untracked` when the user excludes those files. Repeat `--path <relative-path>` for literal file or directory selections. `endpoints` does not compute a merge base; use the scope reference to select it first. Merge commits still require an explicit parent/comparison choice. Root and unborn repositories are supported. Partial-hunk selections are not yet supported by this helper: retain the selected-hunk text report rather than opening a wider diff.

```bash
/usr/bin/python3 "$helper" prepare --repo /absolute/worktree \
  --mode endpoints --base "$base" --head "$head"
/usr/bin/python3 "$helper" open --review "$review_id"
```

`open` creates an unfocused split beside the invoking Pi terminal and returns once Hunk registers. Repeating it for the same review reuses the recorded session. It never adopts an arbitrary existing Hunk window, selects by title, or repoints another review. Do not retry `prepare` to recover a slow launch. Inspect the existing pane and retry `open` with the original ID. An incomplete cmux identity fails closed instead of creating another pane.

The helper selects native Hunk comparisons where possible. A bare `hunk diff` compares the index to the working tree, so the all-uncommitted mode explicitly passes the pinned HEAD. An unborn all-uncommitted review uses the saved patch because no HEAD exists. Repositories with configured Git textconv drivers also use saved patches, so Hunk cannot execute those drivers during the review. Snapshot-backed views contain the captured diff context; inspect exact Git blobs through Pi when more source context is needed. The helper verifies the displayed file/line content against its captured patch before imports and navigation. Hunk's patch export can omit trailing empty context lines; the helper accepts that exact omission but keeps omitted lines unavailable as comment anchors. Changed lines, nonempty context, paths, and line numbers must still match. A reload changes Hunk's generation and invalidates the saved binding, even if its title or repository is unchanged.

If opening or verification fails, explain the specific limitation briefly and continue the original read-only review. Keep the full report available. Do not install tools, alter configuration, broaden scope, or relax identity checks to make the viewer work.

## Import the parent's final findings

Assign stable IDs after validating and deduplicating the reviewers' output. Import only the parent's final findings. Prefixes retain the skill's existing axis and priority definitions; subjective notes use `optional` rather than a new severity.

```json
{
  "findings": [
    {
      "id": "R1",
      "axis": "Correctness",
      "priority": "P1",
      "scope": "line",
      "path": "src/session.ts",
      "side": "new",
      "start_line": 48,
      "end_line": 52,
      "title": "Clear the timer when the request aborts.",
      "body": "Include the reachable failure, consequence, evidence, and correction or verification suggestion.",
      "reference": "Optional requirement or documented standard reference."
    },
    {
      "id": "R2",
      "axis": "Spec",
      "priority": "P2",
      "scope": "review",
      "title": "Implement the required rollback operation.",
      "body": "Cite the requirement and the evidence of absence."
    }
  ]
}
```

`scope` is `line`, `file`, or `review`. A file finding has `path` but no side or line fields. A review finding has none of those location fields. `reference` is optional. All line findings require an explicit old/new side and inclusive start/end lines. Use the renamed destination path when available; the old path is also accepted when unambiguous.

```bash
/usr/bin/python3 "$helper" import --review "$review_id" --input /private/path/findings.json
```

Use `--input -` for JSON on stdin. Never put findings in shell arguments or parse the free-form terminal report. The helper stores the findings and returned Hunk IDs in its private `review.json`, so a separate input file is not required.

Only ranges present on the specified diff side become inline notes. Hunk attaches the note to the first line; the rationale preserves a multi-line range. File-level, review-level, and out-of-diff findings remain in the artifact and appear in the result's `report_only` list. Include their complete text in the Pi report. Never invent a line anchor to make a finding visible.

A repeated identical import reconciles the existing agent note instead of duplicating it. Removed or edited notes are not silently recreated. Changed finding text requires a new ID, retaining the earlier evidence. If an import's outcome is uncertain, the helper records that uncertainty. A later call can recognize matching notes, but cannot repeat an unacknowledged write merely because the notes are not visible yet. Human notes are never cleared by the importer.

## Discuss findings beside the code

For “show R1” or “explain R1,” navigate first, then answer in Pi:

```bash
/usr/bin/python3 "$helper" show --review "$review_id" --finding R1
```

This checks freshness and selects the exact imported comment. For a follow-up that needs another line or an expression highlight, first run `status`, then use the installed Hunk skill with the returned exact session ID. Do not use `--repo` or an implicit session selector. Keep highlights inside the verified comparison and use them only when explaining the code. Hunk's highlight offsets are UTF-16 units, not Python character indices.

```bash
/usr/bin/python3 "$helper" status --review "$review_id"
/usr/bin/python3 "$helper" comments --review "$review_id"
```

`comments` returns saved human notes from this exact review without clearing them or treating them as permission to edit source. Read them when the user asks you to address review feedback. Findings remain discussion evidence until the user requests a separate fix pass.

Worktrunk-managed tasks retain their existing Ctrl+Shift+F delivery shortcut. It requires the recorded idle task Pi session and a matching acknowledgement. Standalone Pi reviews use `comments` on request instead of adopting a Worktrunk task binding.

A different comparison gets a new review ID. Preserve unresolved conclusions before switching the visible comparison. Do not reload a bound viewer behind the helper and then reuse its findings. After closing a review's old pane and Hunk process, `open --review "$review_id" --reopen` explicitly opens the same still-current comparison and restores the saved agent findings. This restoration can bring back findings previously removed from the viewer, so use it deliberately. It does not acknowledge, clear, or deliver human notes.

## Storage and limits

Records live under `~/.local/state/pi-code-review/<id>/`, with mode-0700 directories and mode-0600 files. They contain private patches, finding text, exact revisions, process/pane identities, and import receipts. They are owner-bound to the Pi session and stay outside Stow and Git. No expiry or automatic deletion is implied. Retain them while their findings matter, then remove the specific review directory after closing its pane and preserving any conclusions.

The helper makes no model calls, launches no new Pi session, and never stages, commits, checks out branches, or posts remote reviews. PR inspection and fetching remain the scope skill's responsibility. A pending GitHub review is still a remote write requiring current-turn permission.

Version evidence is Hunk 0.22.0 and cmux 0.64.25. The helper requires the generation and JSON contracts present in those versions, not a dependency patch. The input and patch size limit is 32 MiB. Failed native comparisons remain text reports; do not silently drop files.

Source and generation checks surround imports, but Hunk's comment CLI has no caller-supplied generation precondition. A simultaneous human reload can race the write. The post-check reports failure and retains receipts instead of claiming a successful import or retrying blindly. These checks are not an atomic lock on Hunk or the checkout.
