# Agent-owned test devices

Pi agents must acquire a device before starting a workflow that can boot it, including Expo, native run scripts, screenshots, and E2E tests. `agent_device` boots an existing iOS simulator by UDID or an Android emulator by AVD name and returns its exact target and lease. Pass that target to the build or test command so the framework cannot select a different device.

The helper refuses to acquire a running, unmanaged device. Shut down an old agent device explicitly before its first managed acquisition. Prefer dedicated agent simulators and AVDs. There is no automatic adoption of existing processes.

## Ownership and cleanup

Each acquisition creates a separate lease. Multiple sessions can share the same managed instance; releasing one lease never interrupts another valid owner. Release the lease when verification finishes, including failure and cancellation. Renew it before its 30-minute expiry when device use spans several tools or turns. An open Pi session does not renew device ownership automatically.

The Pi extension releases its session's ordinary leases on session exit, reload, or session replacement. It does not shut devices down at turn boundaries. If the extension is unavailable, use the command wrapper below rather than booting devices directly.

For user-requested manual QA, `agent_device`'s `hold` action retains the device for an agreed duration, defaulting to 60 minutes and capped at 120. A hold survives session exit and cannot be automatically renewed. `/agent-devices` or `agent-device status` shows the hold's lease token and expiry. `agent-device release <lease>` ends it early.

`~/.local/bin/agent-device` stores private runtime ownership in `~/.local/state/agent-devices/`. Records include the macOS boot ID, PID, and kernel process start time for both owners and device instances. A reused PID or manually rebooted device is not mistaken for the recorded process. Unregistered devices and replacement instances are preserved. Cleanup never erases simulator or AVD data.

## Long-running shell jobs

Wrap a foreground command to boot the device, keep it owned throughout the job, and release it on completion or failure:

```sh
agent-device run ios <simulator-UDID> -- <test-command>
agent-device run android <AVD-name> -- <test-command>
```

The command receives `AGENT_DEVICE_TARGET` and `AGENT_DEVICE_LEASE`. To expand the target inside the command, use a shell explicitly:

```sh
agent-device run ios <simulator-UDID> -- /bin/sh -c 'xcrun simctl spawn "$AGENT_DEVICE_TARGET" /usr/bin/true'
```

The wrapper renews its lease every five minutes while waiting for the command, without a polling thread. A pipe-gated launcher starts user work only after its process group and member identities are durably registered; loss of the wrapper before acknowledgement exits without running the command. Active group members remain protected even if the wrapper or command leader exits. A hung but live job must be cancelled explicitly; CPU activity is not a reliable indication that a job is useful. Do not daemonize a job or move workers out of its process group.

On completion or cancellation, the wrapper checks the group before reaping its leader, then allows up to ten seconds after SIGTERM and five seconds after SIGKILL for workers to exit. Signals require a still-matching recorded member; replacement or unconfirmed groups are not signalled. If cleanup cannot confirm that all workers exited, release and watchdog cleanup retain device protection until the group is empty. This conservative protection can also persist for an unconfirmed replacement group until it exits; inspect it rather than forcing device shutdown.

If an agent already owns the device through `agent_device`, the wrapper acquires a separate job lease. The agent must still release its original lease after verification.

## Watchdog and power use

`com.user.agent-device-watchdog` invokes `agent-device sweep` every five minutes as a low-priority launchd job. It is not a persistent daemon and has no `KeepAlive`, network traffic, desktop automation, or sleep-wake request.

With no state file, it exits before loading process identity APIs. With an empty registry, it reads local state and exits. With live leases, it checks only their kernel process identities, registered job-group members, and expiry; it does not enumerate devices or call `simctl` or `adb`. It skips a tick rather than waiting if an acquisition or shutdown holds the registry lock. Unchanged ownership does not rewrite state.

An owner that has exited gets a five-minute grace period from the first observed exit. Expired leases are removed even when Pi remains open. Last-owner release triggers immediate graceful shutdown; abandoned ownership is cleaned up on a watchdog tick. Shutdown failures remain registered for retry.

Cleanup intentionally runs on battery. Its short local check is cheaper than leaving an abandoned emulator running. launchd coalesces interval ticks across sleep rather than waking the machine for this job. The policy follows the battery-awareness guidance in [the architecture reference](dotfiles-reference.md#hardware-setup).

Startup is recorded before booting. If boot fails or the helper is killed before it can confirm the device process, the watchdog refuses to guess ownership of a running instance and reports `unconfirmed-startup-needs-inspection`. After explicitly shutting down that device, run `agent-device sweep` to remove the incomplete record. This preserves manually started devices during an ambiguous startup interruption.

## Installation and diagnostics

`setup.sh` builds and stows the LaunchAgent template, helper, and Pi extension, then loads the watchdog. Existing installations can apply the additions from the primary checkout:

```sh
scripts/build-launchd-plists.sh
cd stow
stow --restow --no-folding --target="$HOME" bin pi
cd ..
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.user.agent-device-watchdog.plist"
```

Run `/reload` in existing Pi sessions to load the tool and updated instructions. New sessions load them automatically. The watchdog's log is `~/Library/Logs/agent-device-watchdog.log`. Android startup logs under `~/Library/Logs/agent-devices/` are overwritten on each AVD's next managed boot.

Use `agent-device status` to inspect managed ownership and `agent-device sweep` for an immediate cleanup check. State, process identities, and logs are machine-local and must not be committed.
