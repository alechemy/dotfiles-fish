#!/usr/bin/python3
"""Add run accounting to the existing Homebrew schedule without replacing its updater."""

import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile


def wrap_homebrew(home, dry_run=False):
    label = "com.github.domt4.homebrew-autoupdate"
    path = home / "Library/LaunchAgents" / (label + ".plist")
    if not path.exists():
        return False
    with path.open("rb") as stream:
        data = plistlib.load(stream)
    original = str(home / "Library/Application Support" / label / "brew_autoupdate")
    arguments = ["/usr/bin/python3", str(home / ".local/bin/software_updates.py"), "homebrew"]
    if data.get("ProgramArguments") == arguments and "Program" not in data:
        return False
    if data.get("ProgramArguments") != [original] or data.get("Program", original) != original:
        raise ValueError("Homebrew job has an unexpected entrypoint; refusing to replace it.")
    if dry_run:
        return True
    data.pop("Program", None)
    data["ProgramArguments"] = arguments
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(data, stream)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def silence_native_notifications(home):
    path = home / "Library/Application Support/com.github.domt4.homebrew-autoupdate/brew_autoupdate"
    if not path.is_file():
        return
    text = path.read_text()
    pattern = r'(notify\.sh"? "\$status" "\$run_log" )(always|error|never)\b'
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError("Homebrew notifier configuration is unrecognized; refusing to change it.")
    if matches[0][1] == "never":
        return
    replacement = re.sub(pattern, r'\g<1>never', text)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(replacement)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    home = Path.home()
    label = "com.github.domt4.homebrew-autoupdate"
    result = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
    if re.search(r'"PID"\s*=\s*\d+', result.stdout):
        raise SystemExit("Homebrew is updating. Retry setup after it finishes.")
    wrap_homebrew(home, dry_run=True)
    silence_native_notifications(home)
    if wrap_homebrew(home):
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain + "/" + label],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "bootstrap", domain,
                        str(home / "Library/LaunchAgents" / (label + ".plist"))], check=True)
        print("Homebrew update accounting installed on the existing schedule.")


if __name__ == "__main__":
    main()
