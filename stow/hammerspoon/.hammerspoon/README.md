# Hammerspoon Config

Hyper key (CapsLock via Karabiner) app switcher with AeroSpace tiling integration.

## How It Works

`init.lua` binds hyper+key combos to `openOrHideApp()` (defined in `apps/init.lua`), which toggles apps with three behaviors:

- **Not running** — launches the app
- **Running, not focused** — focuses the app's window (or creates a new one)
- **Running, focused** — "hides" by parking the window to AeroSpace workspace `H`

Parking to a dedicated workspace (rather than using macOS `app:hide()`) keeps AeroSpace's tiling tree consistent, so hidden windows stay hidden across workspace switches.

## App Config Options

```lua
["T"] = {
  name = "Ghostty",                        -- display name (used as fallback ID)
  bundleID = "com.mitchellh.ghostty",      -- preferred app identifier
  newWindowMenuItem = {"File", "New Window"}, -- menu path to create a new window
  summonHere = true,                       -- see below
  triggerOnRelease = false,                -- release modifier keys before launching
}
```

### `summonHere`

When `true`, the app follows you:
- **Unparking:** window moves to your *current* workspace (not where it was hidden from)
- **No window here:** creates a new window on the current workspace instead of switching to another

When `false` (default), the app stays put — unparking returns it to its original workspace, and focusing switches you there.

## AeroSpace Compatibility

- **Keep `automatically-unhide-macos-hidden-apps = false`** in `.aerospace.toml`
- **`on-window-detected` rules** for apps you hide/show should use `if.during-aerospace-startup = true`. Without this guard, the rule fires when a window is unparked from workspace H and can fight the unpark destination.
- Workspace `H` is created/destroyed on demand and doesn't need to be in `persistent-workspaces`.

## Tuning

Three timing values in `apps/init.lua` (marked `TUNABLE`):

| Delay | Default | Purpose |
|-------|---------|---------|
| Post-park workspace reassertion | 0.05s | Re-focuses the workspace after parking its last window |
| Position restoration | 0.15s | Waits for AeroSpace to tile the unparked window before repositioning. Increase to 0.2–0.3 if windows land in the wrong spot |
| New window polling | 20 x 0.05s | Polls for a newly created window to appear. Increase retries or interval if hiding right after creation is unreliable |
