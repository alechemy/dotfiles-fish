# Private music NAS configuration

NAS topology belongs in `~/.config/music/nas.json`, not in Stow or generated
application settings. `MUSIC_NAS_CONFIG` selects a different file. The file
contains paths, host names, and account names, never passwords or tokens.

Restore this file privately from the old machine. Preserve an existing target.
Keep the parent directory mode 700 and the file mode 600. Do not put the real
file in this repository or copy its values into logs, examples, or task notes.
The [example](examples/music-nas.example.json) is fictional, not an installation
seed. Setup does not create or overwrite private NAS configuration.

Validate without network access or changes:

```sh
/usr/bin/python3 ~/.local/bin/_music_nas.py check
```

## Version 1

All fields below are required. Unknown fields, duplicate JSON keys, and other
versions fail validation.

| Field | Meaning |
| --- | --- |
| `version` | Integer `1`. |
| `mount.host` | SMB host, a DNS name or IP address. |
| `mount.user` | SMB login name. |
| `mount.shares` | Nonempty unique list of share names. |
| `mount.home_gateway` | Default-gateway IP that permits mount attempts. |
| `ssh_hosts` | Nonempty unique list of `user@host` destinations, in preference order. Bracket IPv6 addresses. |
| `local_library_root` | Absolute music library path on this Mac. |
| `remote.library_root` | The same library's absolute path on the NAS. |
| `remote.inbox` | Absolute NAS download inbox. |
| `remote.python` | Absolute NAS Python executable. |
| `remote.rip` | Absolute NAS streamrip executable. |
| `remote.streamrip_config` | Absolute NAS streamrip configuration path. The credentials stay on the NAS. |

Paths must be absolute, non-root, and have no `..` components. Spaces and shell
punctuation in paths are data, not commands. Values cannot contain control
characters or line separators. Host and user fields reject command syntax.
Scoped IPv6 addresses containing `%` are unsupported in SMB hosts, gateway
addresses, and SSH destinations. Unscoped IPv6 addresses remain valid.
Share names allow letters, digits, spaces, dots, underscores, and hyphens,
starting with a letter or digit. SMB URLs percent-encode share names.

## When configuration is needed

`_music_nas.py` uses only the standard library. Importing it does not read a file.
Python commands resolve defaults after argument parsing. `--help` does not need
NAS configuration.

These operations validate before their NAS probes, downloads, or writes:

- `riptag` and `riptag-worker.sh`, including resume and local mode.
- Top-hits download, assembly, adoption, redo, retag, run, and status.
- Import-album when `--library-root` is omitted.
- Music-doctor scan, filesystem stats, and fix when `--library-root` is omitted.
- Runnability analyze and write when `--library-root` is omitted.

Explicit library-root options work without NAS configuration. Top-hits chart,
resolve, and approve are independent of this topology. Music-doctor database-only
commands and runnability score/status also remain independent.

The mount agent logs and skips with status 0 when configuration is absent. It
fails before route or SMB probes when an existing file is malformed. With valid
configuration it keeps the gateway and reachability gates. Save SMB passwords
in the login Keychain through Finder, as before.

Runnability-sync keeps its power gate first. It then reads the private library
root with Apple-signed `/usr/bin/python3` and checks that directory before
running the existing explicit `uv` commands. Neither launchd plist embeds
private topology. The local streamrip credential template is unchanged.

## Getter and remote deployment

Shell and Fish consumers invoke the getter. They never source or evaluate its
output. A scalar prints one line; a list prints one item per line. Consumers
check the exit status before using values. For example:

```sh
/usr/bin/python3 ~/.local/bin/_music_nas.py get local_library_root
```

This prints private topology. Use it only when the value is needed, not for a
configuration dump. `map-path` converts a path under `local_library_root` to the
same relative path under `remote.library_root`. It rejects paths outside the
local root rather than replacing a string prefix.

Fish deploys `_music_nas.py` beside the worker and its tagging/organizer helpers.
`project-worker` writes a new mode-600 JSON file with only `version` and `remote`.
It will not overwrite an existing file. The wrapper sends this projection, not
the full private configuration and not streamrip credentials, into a private
per-invocation NAS directory. It sets mode 600 remotely and passes the projected
file path and configured bootstrap interpreter explicitly. `--worker` accepts
only that projected schema. Remote helper defaults refer to deployed siblings.

Ordered SSH failover is shared by the wrapper, worker permissions step, and
top-hits. Local permission repair maps paths through the configured roots and
quotes each remote path. Missing configuration does not trigger a fallback to
old machine constants.

`LOCAL_RIP`, `LOCAL_PYTHON`, and `STREAMRIP_DOWNLOADS` remain explicit local
worker overrides. Top-hits also honors `LOCAL_RIP`; its existing
`TOP_HITS_DOWNLOADS` override remains unchanged. Session ownership, recorded
host recovery, download-ID provenance, and current-file checks are unchanged.

## Installation boundary

The new helper must be stowed with the other bin files from the primary
checkout after integration. Do not run setup or Stow from a task worktree.
Restoring and validating private configuration is separate from mounting,
service activation, NAS deployment, or downloading music. None of those actions
is required to validate this migration.
