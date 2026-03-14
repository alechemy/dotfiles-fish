#!/usr/bin/env bash

if ! pgrep -xq "Feishin"; then
  open -a "Feishin"
else
  media-control toggle-play-pause
fi
