#!/bin/bash
set -euo pipefail

install_hook=0
hunk_only=0
if [[ ${1:-} == --install-pi-hook ]]; then
  install_hook=1
  shift
elif [[ ${1:-} == --hunk-only ]]; then
  hunk_only=1
  shift
fi
if (($#)); then
  echo "Usage: $0 [--install-pi-hook | --hunk-only]" >&2
  exit 2
fi

DOTFILES=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if ((!hunk_only)); then
  config=${CMUX_GHOSTTY_CONFIG:-"$HOME/Library/Application Support/com.cmuxterm.app/config.ghostty"}
  if [[ -L $config ]]; then
    echo "Refusing to replace symlinked cmux Ghostty config: $config" >&2
    exit 1
  fi
  python3 - "$config" <<'PY'
from datetime import datetime
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)
original = path.read_text()
managed = {
    "command = /usr/bin/env -u HERDR_AGENT -u HERDR_ENV -u HERDR_PANE_ID -u HERDR_PROCESS_DETECTION /opt/homebrew/bin/fish -l",
    "command = /opt/homebrew/bin/fish -l -c 'exec herdr'",
    "command = /usr/bin/env -u HERDR_ENV -u HERDR_PANE_ID -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID -u HERDR_SOCKET_PATH -u HERDR_BIN_PATH /opt/homebrew/bin/fish -l -c 'exec /opt/homebrew/bin/herdr'",
}
lines = original.splitlines()
commands = [line.strip() for line in lines if line.lstrip().startswith("command =")]
unknown = [line for line in commands if line not in managed]
if unknown:
    raise SystemExit(f"Refusing to remove an unrecognized cmux command override in {path}")
remaining = [line for line in lines if line.strip() not in managed]
active = [line for line in remaining if line.strip() and not line.lstrip().startswith("#")]
if active:
    raise SystemExit(f"Refusing to remove a cmux config containing unrelated settings: {path}")
stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
backup = path.with_name(f"{path.name}.backup-{stamp}")
backup.write_bytes(path.read_bytes())
os.chmod(backup, 0o600)
path.unlink()
PY
fi

extension=${CMUX_HUNK_EXTENSION:-"${XDG_CONFIG_HOME:-$HOME/.config}/hunk/extensions/worktrunk-feedback.ts"}
if [[ -L $extension ]]; then
  echo "Refusing to replace symlinked Hunk extension: $extension" >&2
  exit 1
fi
mkdir -p "$(dirname "$extension")"
python3 - "$DOTFILES/scripts/cmux/worktrunk-feedback.ts" "$extension" <<'PY'
from datetime import datetime
from pathlib import Path
import os
import sys

source, target = map(Path, sys.argv[1:])
contents = source.read_bytes()
if target.exists() and target.read_bytes() == contents:
    raise SystemExit(0)
if target.exists():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = target.with_name(f"{target.name}.backup-{stamp}")
    backup.write_bytes(target.read_bytes())
    os.chmod(backup, 0o600)
temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
try:
    temporary.write_bytes(contents)
    os.chmod(temporary, 0o600)
    temporary.replace(target)
finally:
    temporary.unlink(missing_ok=True)
PY

if ((install_hook)); then
  cmux hooks setup pi --yes
fi
