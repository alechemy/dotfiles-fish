# Inline review in tuicr

The parent Pi session automatically opens a tuicr pane for an ordinary code review in native cmux, unless the user requests text-only output. Keep independent reviewers, evidence checks, and synthesis in Pi. Children must not open viewers or import findings. Outside native cmux, retain the grouped text report. Never launch the interactive TUI in the Bash tool.

Use the bundled `../tuicr_review.py` with `/usr/bin/python3`. Resolve its absolute path relative to this reference, not the repository being reviewed. The commands below call that path `$helper`. This replaces the Hunk integration in this skill; there is no alternate viewer mode.

## Capture and open

After selecting the scope, call `prepare` before launching reviewers. It pins source revisions and saves the patch outside the repository. Keep the returned review ID, source comparison, and patch path in the parent conversation and every reviewer packet.

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

Pass `--repo /absolute/worktree` to every prepare call. Repeat `--path <relative-path>` for literal file or directory selections. Omit `--include-untracked` when the user excludes those files. Select the merge base or merge-commit parent through [scope.md](scope.md); the helper does not choose one. Root and unborn repositories are supported. Partial-hunk selections remain text-only. Never broaden the comparison to fit the viewer.

```bash
/usr/bin/python3 "$helper" prepare --repo /absolute/worktree \
  --mode endpoints --base "$base" --head "$head"
/usr/bin/python3 "$helper" open --review "$review_id"
```

`prepare` builds a private Git repository containing only the changed paths' baseline blobs and the selected patch, represented by two synthetic commits. It writes no source index, refs, commits, or working files. The snapshot has no remote, checkout, source hooks, filters, ignore files, or shared object dependency. Its commit IDs are viewer implementation details, not the reviewed source revisions. Inspect callers, tests, and full repository context through the original repository and pinned source versions.

The snapshot gives staged, unstaged, path-filtered, and untracked comparisons the same tuicr launch contract. It also prevents `.tuicrignore` and Git textconv configuration from silently changing the reviewed scope. Binary files, deletions, and renames stay in the inventory. The viewer uses tuicr's Git CLI backend to preserve rename representation. Its extra `Commit Message` entry describes the synthetic snapshot, not the original commit message.

`open` creates an unfocused split beside the invoking Pi terminal. It binds the persisted session to the private snapshot, exact process, and returned cmux surface. Repeating it reuses that binding. It never adopts another viewer or selects by title, newest timestamp, or a source-repository slug. A slow launch must be retried with the original review ID. An uncertain cmux response fails closed instead of creating a second pane.

If capture, opening, or verification fails, briefly state the limitation and continue the complete text review. Do not skip an axis, install tools, modify configuration, or relax identity checks during an ordinary review.

## Import final findings

Assign stable IDs after validating and deduplicating the reviewers' output. Import only the parent's final findings. Use the existing axes and P0/P1/P2 priorities; subjective notes use `optional`.

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

`scope` is `line`, `file`, or `review`. File findings have a path but no side or line fields. Review findings have no location fields. Line findings require an explicit old/new side and inclusive start/end lines. Use the renamed destination path; an unambiguous old path is also accepted. `reference` is optional.

```bash
/usr/bin/python3 "$helper" import --review "$review_id" --input /private/path/findings.json
```

Use `--input -` for JSON on stdin. Never put finding text in shell arguments or parse the free-form report. The helper sends each comment through official `tuicr review add --input -` with an explicit `Pi review <id>` author. It never rewrites tuicr's session JSON.

Native range, file, and review comments are supported. Only ranges present on the specified patch side become inline notes. `inline` lists line findings, `attached` lists all delivered findings, and `report_only` lists findings that could not be anchored. Keep the complete report-only findings in Pi. Never fabricate an anchor.

Repeated identical imports reconcile saved comments without duplicating them. Removed or edited comments are not recreated, and changed finding text requires a new ID. Pending writes and individual receipts survive partial failure. A write with an uncertain outcome can be reconciled if its matching comment appears, but cannot be repeated blindly. Human comments are never deleted.

## Discuss findings and read feedback

For "show R1" or "explain R1," verify the saved finding first:

```bash
/usr/bin/python3 "$helper" show --review "$review_id" --finding R1
```

Answer in Pi and direct the user to `:summary`, select R1, then Enter in that review pane. tuicr has no equivalent to Hunk's navigation or expression-highlighting API. The helper returns the exact surface and comment identities but sends no keystrokes and does not claim to have moved the cursor.

```bash
/usr/bin/python3 "$helper" status --review "$review_id"
/usr/bin/python3 "$helper" comments --review "$review_id"
```

`comments` reads saved feedback from this review and excludes its imported agent notes. It works after the pane closes if the session remains saved and the source comparison is still current. It does not clear comments, submit them as a new Pi turn, or authorize fixes. Read it when the user asks about their review feedback. Ctrl+Shift+F belongs to the separate Hunk Worktrunk integration and does not apply to these panes.

A different comparison gets a new review ID. After the old pane and process have closed, `open --review "$review_id" --reopen` reopens the same still-current snapshot and resumes its persisted comments. It does not reimport removed notes. tuicr deletes empty sessions on exit; if the saved session is gone, prepare a new review. Preserve unresolved conclusions before switching comparisons.

## Storage, verification, and limits

Records live under `~/.local/state/pi-code-review/<id>/`, inside mode-0700 directories. Patches and helper records are mode 0600. Each review has an isolated tuicr HOME and config, so its comments, manifest, and process registry stay inside that private directory. The config enables comment refresh, disables update checks and diff watching, and preserves whitespace. Nothing is stowed, uploaded, or automatically expired. Close the pane and preserve conclusions before removing a specific review directory.

The verified binary is Homebrew tuicr 0.27.0, upstream tag `v0.27.0`, commit `9175dc95b97a0a7dd290d43d38385745a7fb7d40`, with cmux 0.64.25. Official CLI commands own all comment writes. Because the public CLI lacks process and comparison details, the helper also reads the persisted session 1.3 and active-registry 1.0 contracts. It rejects a different binary version until those checks are revalidated. Homebrew owns upgrades; do not use tuicr's self-updater.

Before and after imports, the helper checks source freshness, snapshot HEAD, session identity, commit range, complete saved file inventory, process start time, and cmux ownership. tuicr does not expose its live rendered patch, transient filters, or an atomic generation precondition. These checks bind comments to immutable snapshot paths and lines; they cannot certify the current cursor, visible filters, or immediate redraw. A simultaneous target switch can race a write. The post-check reports failure and keeps receipts rather than claiming atomic exclusion or retrying blindly.

The patch and JSON input limit is 32 MiB. Invalid UTF-8 patches, unsupported snapshots, unavailable history, or failed comparisons retain the full text report. The helper makes no model calls, launches no Pi session, and publishes no remote review. A user-triggered `:submit`, including a remote draft, still requires current-turn permission; this workflow supplies no forge remote or authentication setup.

Official references: [CLI](https://github.com/agavra/tuicr/blob/v0.27.0/docs/CLI.md), [review CLI](https://github.com/agavra/tuicr/blob/v0.27.0/docs/REVIEW_CLI.md), and [configuration](https://github.com/agavra/tuicr/blob/v0.27.0/docs/CONFIG.md).
