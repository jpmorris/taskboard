#!/bin/bash
# Launch taskboard watch in a small floating kitty terminal

LOCKFILE="${XDG_RUNTIME_DIR:-/tmp}/taskboard-launch.pid"

# Suppress duplicate launches: if lockfile exists and the stored PID is alive, bail out
if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE")" 2>/dev/null; then
    echo "Startup suppressed: $LOCKFILE contains running pid, startup script is already running"
    exit 1
fi

# Remove lockfile on exit so future launches are not suppressed
trap 'rm -f "$LOCKFILE"' EXIT
echo $$ > "$LOCKFILE"

kitty \
  --title "TASKBOARD" \
  -o font_size=8 \
  -o initial_window_width=549 \
  -o initial_window_height=217 \
  -e taskboard watch
