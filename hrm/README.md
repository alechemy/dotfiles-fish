# HRM.app keyboard-aware toggle

Software home row mods (**HRM.app**, `com.wontaeyang.HRM`, menu-bar only) should
run **only when the built-in MacBook keyboard is the active board**. The Glove80
(used over USB at the desk) and the Go60 (used over Bluetooth while travelling)
both do home row mods in firmware, so running HRM.app on top of either
double-applies the behavior. This setup quits HRM.app whenever a firmware board
is attached and relaunches it when only the built-in keyboard remains.

This replaces the old approach of quitting/launching HRM.app on AC power
connect/disconnect, which used "docked == Glove80" as a lossy proxy and had no
concept of the Go60-over-Bluetooth case at all.

## Architecture

One idempotent **reconciler** is the sole authority on the desired state; every
trigger is just a dumb "something changed, re-check" nudge. Add a trigger, miss a
trigger, fire a trigger twice — the reconciler always converges on the right
state because it derives truth fresh from device presence each run.

```
Glove80 USB attach/detach ─┐
Go60 Bluetooth conn/disc  ─┼─► hrm-reconcile ─► HRM.app on/off
wake / login              ─┘    (reads ioreg; pkill or open)
```

### The brain (tracked)

`~/.local/bin/hrm-reconcile` — stowed from `stow/bin/.local/bin/hrm-reconcile`.
Reads `ioreg -r -c IOHIDInterface` for a Glove80/Go60 match (both transports
surface there), then `pkill -x HRM` or `open -gja HRM`. Apple-signed binaries
only, no AppleEvents, so it is TCC-free regardless of what invokes it.

Run it by hand any time to force-correct the state:

```sh
hrm-reconcile
```

### The triggers (GUI, not auto-stowed — rebuild manually per the steps below)

Keyboard Maestro and Shortcuts ride supported, vendor-maintained device triggers
(more robust than scraping `log stream`), and KM already holds stable
Accessibility/Automation grants. Their config lives in the respective GUIs; the
steps to recreate it are below.

#### 1. Keyboard Maestro macro — USB (Glove80) + wake + login

Create one macro, e.g. **"HRM: reconcile"**, in an always-available group:

- **Triggers** (add all to the single macro):
  - _This USB Device_ → **Glove80 Left** → **is attached**
  - _This USB Device_ → **Glove80 Left** → **is detached**
  - _At system wake_
  - _At engine launch_ (covers login / reboot / cold start)
- **Action:** _Execute a Shell Script_ → **ignore results** →
  ```sh
  "$HOME/.local/bin/hrm-reconcile"
  ```

The Glove80 is a split board and enumerates as **two** USB devices. Use the
**Left** half — that's the one cabled to the host, so its attach/detach tracks
docking. The Right entry is redundant (the reconciler re-checks everything
regardless of which event fires).

| Name (as KM lists it) | Vendor | idVendor:idProduct |
| --------------------- | ------ | ------------------ |
| **Glove80 Left**      | MoErgo | `0x16C0:0x310B`    |
| Glove80 Right         | MoErgo | `0x16C0:0x1100`    |

> KM's USB trigger is a popup of **currently-attached** devices, not a
> type-to-autocomplete field — plug the Glove80 in **before** adding the trigger
> or it won't be listed. To dump the exact names/IDs KM is reading:
> `system_profiler SPUSBDataType | grep -iE 'Glove|Product ID|Vendor ID'`.
> If KM keys off IDs rather than the name, match `0x16C0:0x310B`. As a last
> resort, "Any USB Device" for both attach and remove also works — the
> reconciler is idempotent, so spurious runs on unrelated devices are harmless.

#### 2. Shortcuts automation — Bluetooth (Go60)

macOS Shortcuts has no USB-attach trigger but does have a Bluetooth one. It can
only scope to "AirPods" or "Any Device" — that's fine here, because the body just
calls the reconciler, which re-derives truth from `ioreg`; no per-device check is
needed in the automation.

- New **Personal Automation** → **Bluetooth**
  - Device: **Any Device**
  - Check **both** _Is Connected_ **and** _Is Disconnected_
  - **Run Immediately** (not Run After Confirmation)
- Action: **Run Shell Script**
  ```sh
  "$HOME/.local/bin/hrm-reconcile"
  ```

Export it as `hrm.shortcut` into this folder once created (File → Export) so the
automation body is at least recorded in the repo.

> Fires on every Bluetooth connect/disconnect (AirPods, headphones, etc.), not
> just the Go60. Harmless — the reconciler is cheap and idempotent.

## First-time Go60 confirmation

The Go60 was not connected when this was built, so its exact HID Product string
is unverified. The first time you use the Go60, confirm it matches and update
`BOARDS` in `hrm-reconcile` if it reports as something other than `Go60`:

```sh
ioreg -r -c IOHIDInterface -d1 | grep -i go60
```

## Decommission the old automation

Delete the Shortcuts power automations that this replaces:

- "When Alec's MacBook Pro is connected to power" (Quit/Open HRM)
- "When Alec's MacBook Pro is disconnected from power" (Quit App and Open App)

## Debugging

The reconciler logs each decision to the unified log. Watch it fire live while
testing the triggers (use the absolute path — a shell `log` function may shadow
the binary, and `--level info` is required because the messages log at info
level):

```sh
/usr/bin/log stream --level info --predicate 'eventMessage CONTAINS "hrm-reconcile"'
```

Or review recent decisions:

```sh
/usr/bin/log show --last 1h --info --predicate 'eventMessage CONTAINS "hrm-reconcile"'
```
