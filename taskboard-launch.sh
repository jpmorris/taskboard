#!/bin/bash
# Launch taskboard watch in a small floating terminal
# Uses terminator with a custom profile for small font

exec terminator \
  --title "TASKBOARD" \
  --profile taskboard \
  --geometry 600x300 \
  -e "taskboard watch"
