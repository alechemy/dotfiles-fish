# agent-reader Pi/JSON overlay

`recall` needs Pi session discovery plus normalized JSON commands from two
reviewed `agent-reader` commits that are not reachable from the public remote.
The remote must not be changed by dotfiles setup, so this directory carries a
temporary downstream source overlay instead of vendoring the complete project.

Pinned provenance:

- upstream: `https://github.com/alechemy/agent-reader.git`
- base: `09080db090f0741707652535fd8be8b8df429e4c`
- reviewed local commits: `429868b9c7bc900162e6833fda2db61a1749031a`
  and `6105b5ce4c3aa377dfcdd5181e2be9b44e1e0de4`
- overlay SHA-256:
  `8313edf83407011ddeda34259db1be6ba31f05d8146a3e11bb7715c1eea31415`
- patched Git tree: `15cab576ed4cbbf1700ff0fbe21ecc3c2671ace5`

The overlay also fixes the session-index contract after those commits: table
output defaults to 20 sessions, JSON defaults to all, `--limit 0` means all,
negative limits fail, and positive limits slice normally. It carries only
matching public source, documentation, and synthetic schema-faithful
fixtures/tests. It contains no real transcript, credential, session ID, path,
prompt, or generated state.

`scripts/install-agent-reader.sh` checks the exact base, overlay and constraint
digests, changed-path allowlist, patched tree, whitespace, and 50 synthetic
tests. It builds with uv-managed Python 3.12.13, pinned runtime and Hatchling
build constraints, writes a wheel and digest manifest into a versioned private
staging directory, then atomically moves it to
`~/.local/share/dotfiles-tools/`. Installing from that stable wheel path keeps
uv's tool receipt and later `uv tool upgrade` valid. Setup probes only `--help`;
it never lists or reads private transcripts.

The source, runtime dependencies, build dependencies, and Python version are
pinned. The Homebrew-provided uv version and platform-specific wheel bytes
remain outside a fully hermetic artifact boundary, so the manifest verifies the
local build rather than claiming one universal wheel digest.

Remove this overlay and pin a reviewed public commit once an upstream ref
contains the same capabilities and tests. The upstream project is MIT licensed;
its license remains in the exact base checkout used for every build.
