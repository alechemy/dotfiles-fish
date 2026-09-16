#!/bin/sh

# Nightly runnability sync: analyze new/changed library audio into the feature
# store, then write RUNNABILITY tags. NAS-side rips (riptag remote mode) have
# no on-arrival scoring hook, so this run is what scores them.

"$HOME/.local/bin/should-run-background-job" || exit 0
LIBRARY_ROOT=$(/usr/bin/python3 "$HOME/.local/bin/_music_nas.py" get local_library_root) || exit 1
[ -d "$LIBRARY_ROOT" ] || exit 0

# uv invoked by absolute path: launchd's PATH has no /opt/homebrew/bin, and the
# lint forbids resolving runnability.py's uv shebang from a launchd shell.
UV=/opt/homebrew/bin/uv
if [ ! -x "$UV" ]; then
  echo "runnability-sync: uv not found at $UV" >&2
  exit 1
fi

echo "=== runnability-sync $(date '+%Y-%m-%d %H:%M:%S') ==="
"$UV" run --script "$HOME/.local/bin/runnability.py" analyze || exit 1
exec "$UV" run --script "$HOME/.local/bin/runnability.py" write
