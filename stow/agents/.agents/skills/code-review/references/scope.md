# Select the reviewed version

Run commands at the repository root. Start with `git status --short` and confirm the user's scope. Do not print secrets or generated credential-bearing files while collecting evidence. Quote refs and paths, keep paths after `--`, and use NUL-delimited filename output when parsing names programmatically.

Resolve each supplied revision to a commit with `git rev-parse --verify --end-of-options "${ref}^{commit}"`. Capture the target SHA as `head` and the baseline SHA as `base`. For staged or WIP reviews in a repository with commits, pin the current HEAD as `head`. Use those immutable SHAs in later commands, not moving branch names. For a branch or PR review, find merge bases with `git merge-base --all "$base" "$head"` and capture the single result as `merge_base`. Multiple merge bases need an explicit comparison choice. Capture the commit list with `git log --oneline "$base..$head"`.

## Diff recipes

The commands below review all tracked paths. Append the agreed paths after the final `--` for a file-scoped review. If unrelated changes share a file, retain the user's hunk selection rather than assuming a path filter excludes them.

| Mode | Command |
| --- | --- |
| Branch or PR | `git diff --no-ext-diff --no-textconv "$merge_base" "$head" --` |
| Exact endpoints | `git diff --no-ext-diff --no-textconv "$base" "$head" --` |
| Staged only | `git diff --no-ext-diff --no-textconv --cached "$head" --` |
| Unstaged only | `git diff --no-ext-diff --no-textconv --` |
| All uncommitted tracked changes | `git diff --no-ext-diff --no-textconv "$head" --` |
| Since baseline including work in progress | `git diff --no-ext-diff --no-textconv "$merge_base" --` |

A single non-merge commit uses its parent as `base` and that commit as `head`, with the exact-endpoints recipe. For a root commit, use `git diff-tree --root --no-commit-id --no-ext-diff --no-textconv -p "$head" --`. For a merge commit, ask which parent or comparison the user wants; do not assume the first parent.

A bare "review my changes" uses the conversation's identified change. Without that context, all uncommitted changes are a reasonable scope only when the user clearly means the whole dirty tree. If several tasks or sessions share the tree, clarify. If the tree is clean, ask for the branch, commit, or files rather than falling back to `HEAD~1`.

WIP means the current net contents relative to the selected baseline, including staged and unstaged changes. Unstaged-only compares the working tree against the index. Staged-only compares the index against HEAD. Do not interchange them. In a repository without HEAD, use `git diff --no-ext-diff --no-textconv --cached --` for staged changes; for all WIP, inventory staged and untracked paths and review their current contents as additions, omitting paths absent from the working tree. Do not pretend HEAD resolves.

## Inventory and content

Use the selected diff's `--name-status -z` form to inventory tracked changes. Do not filter out deletions or renamed source paths. For WIP or explicitly requested untracked scope, also use `git ls-files --others --exclude-standard -z --` with the same path filter. An empty tracked diff does not mean an empty review if in-scope untracked files exist. Treat their complete contents as additions. A staged-only or committed review excludes untracked and unrelated unstaged files.

Read content from the selected version:

- For committed targets, use `git show "$head:$path"` and `git show "$base:$path"`, substituting `merge_base` for the branch/PR baseline.
- For staged targets, use `git show ":$path"` and `git show "$head:$path"` when the baseline path exists. For unstaged-only reviews, the old version is the index and the new version is the working-tree file.
- For WIP, read the working-tree file and the selected baseline blob. For new paths there is no old blob. For deletions, inspect the removed version and its surviving callers. For renames, use the old and new paths on their respective sides.

Apply the same version selection to relevant callers, tests, standards, and spec files when they differ across revisions. Active agent permission instructions remain authoritative regardless of the target revision. Report binary, unavailable, or otherwise uninspectable content as a coverage gap rather than silently skipping it.

For a remote PR, obtain its repository, base/head SHAs, description, and changed files through read-only tools. Fetch missing history if permitted by the active instructions, but do not check out the PR over the user's dirty tree. Review the PR's selected blobs rather than the current local branch. If exact evidence remains unavailable, report the limitation.

## Consistency

Capture the selected diff and inventory, plus content hashes for in-scope untracked files. Recompute them before aggregation to detect concurrent changes, including edits that leave `git status` unchanged. Recheck the index for staged reviews and relevant context files when they affected a finding. Immutable committed SHAs remain the report's target even if the branch later moves; identify that movement explicitly. Do not mutate the repository to freeze it or store private snapshots in tracked files.
