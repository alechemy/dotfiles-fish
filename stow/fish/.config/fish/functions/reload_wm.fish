function reload_wm -d "Reload window manager stack (Goku, AeroSpace, SketchyBar, JankyBorders, Hammerspoon)"
  set -l failed 0

  if type -q goku
    echo "→ Regenerating Karabiner config (goku)"
    goku; or set failed 1
  else
    echo "✗ goku not found in PATH"
    set failed 1
  end

  if type -q aerospace
    echo "→ Reloading AeroSpace config"
    aerospace reload-config; or set failed 1
  else
    echo "✗ aerospace not found in PATH"
    set failed 1
  end

  if type -q sketchybar
    echo "→ Reloading SketchyBar"
    sketchybar --reload; or set failed 1
  else
    echo "✗ sketchybar not found in PATH"
    set failed 1
  end

  if type -q borders
    echo "→ Restarting JankyBorders"
    killall borders 2>/dev/null
    borders &>/dev/null &
    disown
  else
    echo "✗ borders not found in PATH"
    set failed 1
  end

  echo "→ Reloading Hammerspoon"
  open -g "hammerspoon://reload" >/dev/null 2>&1; or begin
    echo "✗ Could not trigger Hammerspoon reload URL"
    set failed 1
  end

  if test $failed -eq 0
    echo "✓ Window manager stack reloaded"
  else
    echo "⚠ Reload completed with errors"
  end

  return $failed
end
