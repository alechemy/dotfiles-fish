# Ploopy Knob — custom QMK firmware

The [Ploopy Knob](https://ploopyco.github.io/knob/) (RP2040 + AS5600 magnetic
angle encoder) ships with QMK whose scroll path branches on
`detected_host_os()`: Windows/Linux get high-resolution proportional scrolling,
everything else gets notched (one tick per `TICK_COUNT` sensor counts). On this
Mac that OS detection is unreliable — it intermittently reports Windows/Linux —
so the knob flips into the high-res branch, and combined with macOS's own scroll
smoothing that produces sudden runaway sensitivity.

`keymap.c` drops the OS branch so scrolling is **always notched**, and lowers
`TICK_COUNT` for finer granularity. `upstream/keymap.c` is the pristine
`keymaps/default/keymap.c` from the build below, kept so the diff is obvious.

Changes vs upstream:

- Removed the `detected_host_os()` branch — always the notched path.
- `POINTING_DEVICE_AS5600_TICK_COUNT = 32` (stock is 128) → ~2.8°/detent, ~128 detents/rev.

Host-side companions (both tracked in this repo), needed for a good feel on macOS:

- **LinearMouse** — 1 line/detent, no acceleration, scoped to the knob
  (VID `0x5043` / PID `0x63C3`). Seed: `stow/linearmouse/_seed/`.
- **Ghostty** — `mouse-scroll-multiplier = discrete:1` (a terminal otherwise
  applies its default ×3 discrete multiplier on top). In `stow/ghostty/`.

## Build

The knob keyboard lives only in Ploopy's QMK fork, branch `ploopyco/knob` (not
mainline QMK):

```sh
git clone --branch ploopyco/knob --single-branch \
  https://github.com/ploopyco/qmk_firmware.git ~/Developer/qmk_firmware
cd ~/Developer/qmk_firmware
git submodule update --init \
  lib/chibios lib/chibios-contrib lib/pico-sdk lib/printf lib/lufa lib/vusb
```

Init those submodules specifically — a recursive init also pulls
`chibios-contrib/ext/mcux-sdk` and `pico-sdk/lib/tinyusb`, which the RP2040 build
doesn't use (and `lvgl`/`googletest`, also unused).

**Toolchain gotcha:** Homebrew's `arm-none-eabi-gcc` is compiler-only — it has no
newlib, so `stdint.h` fails to resolve. Use Arm's official toolchain (bundles
newlib) instead. Download the `darwin-arm64` `.tar.xz` from developer.arm.com
(same URL as the `gcc-arm-embedded` cask, with `.pkg` → `.tar.xz`), extract it,
and put its `bin` first on `PATH`:

```sh
export PATH="$HOME/Developer/arm-gnu-toolchain/bin:$PATH"
```

Install the qmk CLI via uv (`uv tool install qmk`) — the Homebrew `qmk` formula
depends on `osx-cross/arm`'s GCC 8, which source-builds on Apple Silicon.

```sh
cp firmware/ploopy-knob/keymap.c \
  ~/Developer/qmk_firmware/keyboards/ploopyco/knob/keymaps/default/keymap.c
qmk compile -kb ploopyco/knob/rev1_001 -km default
```

Produces `ploopyco_knob_rev1_001_default.uf2` in the qmk_firmware root.

## Flash

1. Unplug the knob.
2. Remove the base screws and lift off the top to expose the PCB.
3. Bridge the two BOOTSEL vias (gold-plated holes) with something metal while
   plugging USB back in. It mounts as an `RPI-RP2` drive.
4. Drop the `.uf2` onto `RPI-RP2`; it flashes and reboots on its own.

## Tuning

- **Scroll speed** → LinearMouse lines-per-detent (no reflash).
- **Detent granularity** → `TICK_COUNT` in `keymap.c` (lower = finer; needs a reflash).
