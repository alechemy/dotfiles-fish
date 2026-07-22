# HRM.app keyboard-aware toggle

Software home row mods (**HRM.app**, `com.wontaeyang.HRM`, menu-bar only) should
run **only when the built-in MacBook keyboard is the active board**. The Glove80
(used over USB at the desk) and the Go60 (used over Bluetooth while travelling)
both do home row mods in firmware, so running HRM.app on top of either
double-applies the behavior. This setup quits HRM.app whenever a firmware board
is attached and relaunches it when only the built-in keyboard remains.

## Architecture

One idempotent **reconciler** is the sole authority on the desired state; the
**watcher** is a dumb "something changed, re-check" nudge. Miss an event, get a
spurious one, fire twice — the reconciler always converges on the right state
because it derives truth fresh from device presence each run.

```
any IOHIDInterface        ┌──────────────┐
first-match/termination ─►│ hrm-watcher  │─► hrm-reconcile ─► HRM.app on/off
(USB and Bluetooth alike) │ (launchd,    │    (reads ioreg;
                          │  IOKit notif)│     pkill or open)
                          └──────────────┘
```

### The brain

`~/.local/bin/hrm-reconcile` — stowed from `stow/bin/.local/bin/hrm-reconcile`.
Reads `ioreg -r -c IOHIDInterface` for a Glove80/Go60 match (both transports
surface there; the Go60's Bluetooth Product string is a confirmed match), then
`pkill -x HRM` or `open -gja HRM`. Apple-signed binaries only, no AppleEvents,
so it is TCC-free regardless of what invokes it.

Run it by hand any time to force-correct the state:

```sh
hrm-reconcile
```

### The trigger

`com.user.hrm-watcher` (KeepAlive launch agent, `stow/hrm-watcher/`) runs
`~/.local/bin/hrm-watcher` under `/usr/bin/python3`. It subscribes to IOKit
matching notifications (`IOServiceAddMatchingNotification`, first-match +
termination) on the `IOHIDInterface` class — the same event source `ioreg`
reads — and calls the reconciler on each event, debounced 1 s to coalesce the
burst of interfaces one board enumerates. Between events it blocks in its
CFRunLoop: no polling, battery-clean, same shape as `lock-watcher`.

Why this beats per-transport triggers (the previous Keyboard Maestro USB macro
+ Shortcuts Bluetooth automation design):

- **One mechanism, both transports.** A HID interface arriving is a HID
  interface arriving, whether over USB (Glove80) or Bluetooth (Go60).
- **No wake race.** A wake-time trigger fires before Bluetooth re-pairs and
  sees only the built-in keyboard; the IOKit notification fires when the board
  actually (re-)enumerates.
- **Tracked and auto-stowed.** No GUI-only automation that can silently not
  exist (the Shortcuts half of the old design was never created — the Go60
  side had no trigger at all).

It also runs the reconciler once at startup, which covers login/reboot. On
machines without `/Applications/HRM.app` it exits 0 and the agent stays
dormant (`KeepAlive.SuccessfulExit=false`).

TCC-free end to end: no AppleEvents, no protected folders, and IOKit device
*matching* notifications need no Input Monitoring grant (the watcher never
opens the devices).

## Decommission the old triggers

- Delete the Keyboard Maestro macro **"Run HRM Reconciler"** (Glove80 USB
  attach/detach + wake + engine-launch triggers). Harmless if kept — the
  reconciler is idempotent — but the watcher makes it fully redundant.
- The Shortcuts Bluetooth automation from the old design was never created;
  nothing to remove. The old power-connect/disconnect automations are already
  gone.

## Debugging

The watcher logs to `/tmp/hrm-watcher.log` (startup, arming, any failure to
spawn the reconciler). The reconciler logs each decision to the unified log.
Watch decisions live (use the absolute path — a shell `log` function may
shadow the binary, and `--level info` is required because the messages log at
info level):

```sh
/usr/bin/log stream --level info --predicate 'eventMessage CONTAINS "hrm-reconcile"'
```

Or review recent decisions:

```sh
/usr/bin/log show --last 1h --info --predicate 'eventMessage CONTAINS "hrm-reconcile"'
```
