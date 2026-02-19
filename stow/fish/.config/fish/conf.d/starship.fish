#!/usr/bin/env fish
set -e STARSHIP_CONFIG

if command -q starship
  starship init fish | source
end
