# TailorKey config notes (Glove80 + Go60)

Working reference for editing the TailorKey JSON exports in this folder:

- `Glove80 TailorKey v5.2³ Bilateral - macOS.json` — 80-key MoErgo Glove80, with a trackball mounted above the right thumb cluster
- `Go60 TailorKey v4.2m⁶ macOS Bilateral.json` — 60-key MoErgo Go60
- `upstream/` — unmodified TailorKey reference downloads, kept in sync with the version label of each working file. Used by the [sync workflow](#sync-workflow-for-new-tailorkey-releases) to diff future upstream releases against. Don't edit these.

Both files come from the same TailorKey layout family by `@moosy` (sites.google.com/view/tailorkey) and share most conventions. This doc covers what isn't obvious from reading the JSON.

## Shared concepts

### JSON structure

Each file is a single JSON object. `"layers"` is an array of layer arrays. Each layer array is a fixed number of key objects, indexed from 0 (Glove80: 80 keys/layer, Go60: 60 keys/layer). Both files have 20 layers.

A typical key object looks like:

```json
{ "value": "&kp", "params": [{ "value": "RGUI" }] }
```

Some keys use custom hold-tap/macro behaviors specific to TailorKey: `&HRM_*_v1B_TKZ` (home-row mod hold-taps), `&thumb_v2_TKZ` (thumb hold-tap), `&space_v3_TKZ` (space hold-tap), `&magic` (system/magic shortcuts), `&lower` (Glove80 only — Lower-layer toggle).

### Home-row mods

Both keyboards use the same HRM scheme on Layer 0 (`HRM_macOS`):

- Left:  `A=LCTRL`, `S=LALT`, `D=LGUI`, `F=LSHFT`
- Right: `J=RSHFT`, `K=RGUI`, `L=LALT`, `;=RCTRL`

Bound via `&HRM_left_pinky_v1B_TKZ`, `&HRM_left_ring_v1B_TKZ`, `&HRM_left_middy_v1B_TKZ`, `&HRM_left_index_v1B_TKZ` and the mirrored right-side variants. Each takes two params: the modifier keycode and the alpha.

### Bilateral mod naming gotcha

This layout enforces "bilateral combos": modifiers and alphas must be triggered from opposite hands. The trick is in the keycode chosen for the mod side:

- `L` key's HRM uses `LALT`, NOT `RALT`, even though `L` is on the right hand. Using the *left* Alt code ensures it only combines with right-hand alphas (preventing same-hand conflicts with the other right-side HRMs).
- Glove80's right thumb cluster has a Control key bound to `LCTRL` for the same reason.

**When searching for "right Control" in either file, look for `LCTRL` on right-hand positions, not `RCTRL`.** Only the `;` home-row mod uses `RCTRL`.

### Layer 1 (Typing) is mostly fall-through

In both files, Layer 1 only overrides the home-row alphas (positions for A/S/D/F/J/K/L/;) and `&magic`, switching them to plain `&kp` to disable HRM during fast bursts. Everything else (including thumb cluster) is `&trans`, so **edits to thumb cluster keys on Layer 0 propagate through Layer 1** automatically. No need to mirror Layer 0 thumb changes onto Layer 1.

### Mouse emulation

Mouse clicks are bound via `&mkp` with `LCLK` / `MCLK` / `RCLK`. Wheel/movement bindings are `&mwh` / `&mmv` (used on the Mouse, MouseSlow, MouseFast, MouseWarp layers).

For `&mkp` to actually emit clicks, the firmware needs HID pointing reports enabled:

- **Glove80**: `HID_POINTING = "y"` is set in this user's `config_parameters` ✓
- **Go60**: `config_parameters` is empty. The Go60 supports an integrated Cirque touchpad (note `layout_parameters.cirque_touch_sensitivity`), which provides HID pointing via the touchpad sensor. If you want `&mkp` on a Go60 without the Cirque, you would need to add `HID_POINTING = "y"` under Advanced Configuration in the TailorKey editor before flashing.

**After flashing any change that adds/removes HID pointing or modifies HID descriptors, unpair the keyboard from every host and re-pair**, or input may not register. This is called out in each layout's own `notes` field.

### Workflow tips

- Don't trust line numbers in this README or in past edits. Layer offsets shift as soon as anything is edited. Find positions by counting key objects within the target layer's array (a Python one-liner is the easiest way: `json.load(...)['layers'][LAYER][POS]`).
- The `&kp` → `&mkp` swap is the canonical pattern for converting a modifier/alpha into a mouse click. Both take one param (a keycode for `&kp`, a click-code like `LCLK`/`MCLK`/`RCLK` for `&mkp`).
- Validate JSON after edits: `python3 -c "import json; json.load(open('<file>'))"`.
- `decoration.background` is the per-key color shown in the TailorKey web editor. Safe to leave as-is when swapping bindings — the color will simply stay wherever it was last set.

---

## Glove80 specifics

### Layer order

| Index | Name        | Index | Name       |
|-------|-------------|-------|------------|
| 0     | HRM_macOS   | 10    | RightPinky |
| 1     | Typing      | 11    | Gaming     |
| 2     | Autoshift   | 12    | Cursor     |
| 3     | LeftPinky   | 13    | Symbol     |
| 4     | LeftRingy   | 14    | Mouse      |
| 5     | LeftMiddy   | 15    | MouseSlow  |
| 6     | LeftIndex   | 16    | MouseFast  |
| 7     | RightIndex  | 17    | MouseWarp  |
| 8     | RightMiddy  | 18    | Lower      |
| 9     | RightRingy  | 19    | Magic      |

### Thumb cluster positions (Layer 0)

**Left thumb cluster:**

| Pos | Binding (stock)         | Physical role |
|-----|-------------------------|---------------|
| 52  | `&kp LSHFT`             | Back row, outer — Shift |
| 53  | `&kp LGUI`              | Back row, middle — CMD |
| 54  | `&lower`                | Back row, inner — Lower-layer toggle |
| 69  | `&thumb_v2_TKZ 12 BSPC` | Front row, outer — Backspace |
| 70  | `&kp DEL`               | Front row, middle — Delete |
| 71  | `&kp LALT`              | Front row, inner — Option |

**Right thumb cluster:**

| Pos | Binding (stock)         | Physical role |
|-----|-------------------------|---------------|
| 55  | `&kp LCTRL` ⚠️           | Back row, inner — Control (uses `LCTRL` per bilateral rule) |
| 56  | `&kp RGUI`              | Back row, middle — CMD |
| 57  | `&kp RSHFT`             | Back row, outer — Shift |
| 72  | `&kp RALT`              | Front row, inner — Option |
| 73  | `&thumb_v2_TKZ 14 RET`  | Front row, middle — Enter |
| 74  | `&space_v3_TKZ 13 SPACE`| Front row, outer — Space |

### Customizations vs. upstream v5.2³

Four tweaks layered on stock TailorKey. The unmodified reference sits at `upstream/Glove80 TailorKey v5.2³ Bilateral - macOS.json` so this inventory can be regenerated by diff.

#### Tweak 1: mouse buttons on thumb clusters

Layer 0. Both thumb clusters repurposed for the trackball above the right cluster, so either thumb can click without a hand swap.

| Pos | Cluster | Was         | Now         | Role         |
|-----|---------|-------------|-------------|--------------|
| 52  | Left    | `&kp LSHFT` | `&mkp LCLK` | Left click   |
| 53  | Left    | `&kp LGUI`  | `&mkp RCLK` | Right click  |
| 55  | Right   | `&kp LCTRL` | `&mkp LCLK` | Left click   |
| 56  | Right   | `&kp RGUI`  | `&mkp RCLK` | Right click  |
| 72  | Right   | `&kp RALT`  | `&mkp MCLK` | Middle click |

Modifier access still flows through home-row mods on both hands (left: `A`=Ctrl, `S`=Option, `D`=CMD, `F`=Shift; right: `J`=Shift, `K`=CMD, `L`=Option, `;`=Ctrl). `RSHFT` at pos 57 stays intact, and pos 54 (`&lower`) on the left is untouched.

#### Tweak 2: top-row F-keys for external display DDC

Layers 0 and 18. External-display brightness and volume route through Karabiner + `m1ddc`. The constraint is that goku (the EDN-to-JSON compiler for `stow/karabiner/.config/karabiner.edn`) does not accept `consumer_key_code` in `:from`, so the keyboard has to emit raw F-keys for goku to match.

Layer 0 (HRM_macOS) top row, positions 2–7:

| Pos | Upstream | Yours    | Notes                                              |
|-----|----------|----------|----------------------------------------------------|
| 2   | `F3`     | `C_PREV` | Media transport, not routed through Karabiner.     |
| 3   | `F4`     | `C_PP`   | Media transport.                                   |
| 4   | `F5`     | `C_NEXT` | Media transport.                                   |
| 5   | `F6`     | `C_MUTE` | System mute. Kept as consumer code (m1ddc has no `chg mute`). |
| 6   | `F7`     | `F11`    | Karabiner routes to `m1ddc chg volume -10`.        |
| 7   | `F8`     | `F12`    | Karabiner routes to `m1ddc chg volume +10`.        |

Positions 0 and 1 already send `F1` / `F2` upstream. Karabiner routes both to `m1ddc chg luminance -10` / `+10`.

Layer 18 (Lower) top row, all positions remapped to `F1`–`F10` to recover access to the F-row that the base layer's media keys displace:

| Pos | Upstream      | Yours |
|-----|---------------|-------|
| 0   | `C_BRI_DN`    | `F1`  |
| 1   | `C_BRI_UP`    | `F2`  |
| 2   | `C_PREV`      | `F3`  |
| 3   | `C_NEXT`      | `F4`  |
| 4   | `C_PP`        | `F5`  |
| 5   | `C_MUTE`      | `F6`  |
| 6   | `C_VOL_DN`    | `F7`  |
| 7   | `C_VOL_UP`    | `F8`  |
| 8   | `&none`       | `F9`  |
| 9   | `PAUSE_BREAK` | `F10` |

End-to-end behavior:

- **Docked mode** (external display active): pos 0 emits `F1`. Karabiner sees `:f1` and runs `/opt/homebrew/bin/m1ddc chg luminance -10` plus an `:f1` passthrough that macOS no-ops on an external keyboard. Net effect: external monitor brightness goes down. Same pattern for `F2`, `F11`, `F12`.
- **Portable mode** (built-in MacBook keyboard): Apple's firmware converts `F1`/`F2`/`F11`/`F12` to consumer codes (`display_brightness_decrement` etc.) before Karabiner sees them. The DDC rules don't match, and native macOS brightness and volume continue to work as before.

Paired files outside this directory:

- `Brewfile` — `brew "m1ddc"`
- `stow/karabiner/.config/karabiner.edn` — `:des "External display DDC via media keys (m1ddc)..."` block matching `:f1`, `:f2`, `:f11`, `:f12`

#### Tweak 3: added Hyper combo

One combo beyond stock TailorKey, in the `combos` array:

```
right_hyper_ACSG_v1_TKZ
  binding:      &sk LA(LC(LS(LGUI)))    (sticky Alt+Ctrl+Shift+Cmd)
  keyPositions: [57, 74]                (RSHFT thumb back + Space thumb front)
  layers:       [0, 2]
```

Parallels the stock `right_meh_ACS_v1_TKZ` (Meh = Alt+Ctrl+Shift, on RGUI + Enter). Hyper is the standard macOS power-user modifier, paired with rules in Karabiner / AeroSpace / Hammerspoon elsewhere in this repo.

#### Tweak 4: `left_alt_tab_switcher_v1_TKZ` combo positions

`keyPositions` shifted from upstream `[52, 71]` to `[54, 71]`. Position 52 is now `&mkp LCLK` (tweak 1), so the original chord would have been left-click + LAlt. New trigger is `&lower` + LAlt.

#### Latent gotcha

`right_meh_ACS_v1_TKZ` still uses `keyPositions: [56, 73]`. Position 56 is now `&mkp RCLK` (tweak 1), so the combo chord is "right-click + Enter." Unlikely to fire by accident, but worth knowing if Meh ever triggers unexpectedly.

---

## Go60 specifics

### Layer order

Note the order differs from Glove80 in places (Right* layers are reversed, and there's a `Keypad` layer in slot 12 instead of `Cursor`):

| Index | Name        | Index | Name       |
|-------|-------------|-------|------------|
| 0     | HRM_macOS   | 10    | RightIndex |
| 1     | Typing      | 11    | Cursor     |
| 2     | Autoshift   | 12    | Keypad     |
| 3     | LeftPinky   | 13    | Symbol     |
| 4     | LeftRingy   | 14    | Mouse      |
| 5     | LeftMiddy   | 15    | MouseSlow  |
| 6     | LeftIndex   | 16    | MouseFast  |
| 7     | RightPinky  | 17    | MouseWarp  |
| 8     | RightRingy  | 18    | Gaming     |
| 9     | RightMiddy  | 19    | Magic      |

There's no `Lower` layer on the Go60. Position 47 in Layer 0 is `&layer(12)` — toggles into the Keypad layer.

### Full Layer 0 position map

The Go60 packs the entire layout into 60 positions in row-major order. No interleaved thumb keys in the alpha rows (unlike Glove80).

| Pos range | Row                     | Keys |
|-----------|-------------------------|------|
| 0–11      | Number row              | `= 1 2 3 4 5 6 7 8 9 0 -` |
| 12–23     | Top alpha row           | `Tab Q W E R T Y U I O P \` |
| 24–35     | Home row (with HRM)     | `Esc A* S* D* F* G H J* K* L* ;* '`  (`*` = HRM hold-tap) |
| 36–47     | Bottom alpha row        | `&magic Z X C V B N M , . / &layer(12)` |
| 48–53     | Nav row                 | `Home Left Right Up Down End` |
| 54–59     | Thumb cluster           | see below |

### Thumb cluster positions (Layer 0)

**Left thumb cluster:**

| Pos | Binding (stock)         | Physical role |
|-----|-------------------------|---------------|
| 54  | `&thumb_v2_TKZ 11 BSPC` | Backspace |
| 55  | `&thumb_v2_TKZ 12 DEL`  | Delete |
| 56  | `&kp LSHFT`             | Shift |

**Right thumb cluster:**

| Pos | Binding (stock)         | Physical role |
|-----|-------------------------|---------------|
| 57  | `&kp RALT`              | Option |
| 58  | `&thumb_v2_TKZ 14 RET`  | Enter |
| 59  | `&space_v3_TKZ 13 SPACE`| Space |

The Go60's right thumb cluster has no Control or CMD key — those modifiers live only on the home row. So the bilateral-mod gotcha that bites on Glove80 (`LCTRL` on the right thumb) doesn't appear on the Go60 thumb cluster; it only applies to the `L` home-row mod.

### Screenshot decoration notes

The default-layout reference (`Go60 Default TailorKey Layout.png`) shows large "SCROLL" and "MOVE" circular icons in the middle of each half. **These are not keys** — they're visual indicators for the Cirque touchpad area (in the layout editor preview) showing what the touchpad does on the Mouse layer.

### No customizations yet

The Go60 JSON currently matches stock TailorKey v4.2m⁶ for macOS (no edits applied).

---

## Sync workflow for new TailorKey releases

When a new TailorKey version ships at sites.google.com/view/tailorkey:

1. Download the new JSON.
2. Place it under `tailorkey/upstream/<board> TailorKey <version> ... .json` alongside the existing reference.
3. Diff the new upstream against the previously stored upstream to see what TailorKey actually changed:

   ```bash
   python3 - <<'PY'
   import json
   old = json.load(open('tailorkey/upstream/Glove80 TailorKey v5.2³ Bilateral - macOS.json'))
   new = json.load(open('tailorkey/upstream/Glove80 TailorKey vNEW Bilateral - macOS.json'))
   strip = lambda k: {kk: vv for kk, vv in k.items() if kk != 'decoration'}
   for li, (ol, nl) in enumerate(zip(old['layers'], new['layers'])):
       for pi, (ok, nk) in enumerate(zip(ol, nl)):
           if json.dumps(strip(ok), sort_keys=True) != json.dumps(strip(nk), sort_keys=True):
               print(f"L{li} pos {pi}: {ok} -> {nk}")
   for f in ('combos', 'holdTaps', 'macros', 'custom_devicetree', 'config_parameters'):
       if json.dumps(old.get(f), sort_keys=True) != json.dumps(new.get(f), sort_keys=True):
           print(f"{f} differs (inspect manually)")
   PY
   ```

4. Apply only the upstream deltas to the working file. Each tweak in the inventory above is well-localized, so reapplying them by hand is cheap unless upstream's changes happen to collide with them.
5. Update the working file's `title` and `notes` header to the new version label.
6. Rename the working file to match (`Glove80 TailorKey vNEW Bilateral - macOS.json`).
7. Update the file references at the top of this README and bump the version label in the customizations heading.
8. Validate JSON: `python3 -c "import json; json.load(open('<file>'))"`.
9. Either delete the previous `upstream/` reference for that board (single-version baseline) or keep both (multi-version history for diffing). Track only one baseline at a time per board if you want commit diffs to stay readable.

The same recipe applies to the Go60. To diff in the other direction (your working file vs. the matching upstream baseline) and regenerate the tweak inventory, swap the two paths in the snippet above.
