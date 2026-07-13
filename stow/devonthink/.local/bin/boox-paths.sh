# Boox device paths, sourced by boox-import-watcher.sh (the folder it watches)
# and dt-watchdog.sh (the stale-export check). The two must agree — a watchdog
# scanning a folder the watcher no longer imports from reports no stale exports
# for the same reason a healthy one doesn't, so the blind spot is silent.
#
# Dropbox mirrors each Boox under a folder named for the device model, so
# swapping the device is a one-line edit here.

BOOX_DEVICE="NoteMax"
BOOX_NOTEBOOKS_DIR="$HOME/Dropbox (Maestral)/onyx/$BOOX_DEVICE/Notebooks"
