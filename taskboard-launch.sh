#!/bin/bash
# Launch taskboard watch in a small floating kitty terminal

exec kitty \
  --title "TASKBOARD" \
  -o font_size=8 \
  -o initial_window_width=549 \
  -o initial_window_height=217 \
  -e taskboard watch
