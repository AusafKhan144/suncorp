#!/usr/bin/env bash
# Run motors.py only if it isn't already running.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-/home/wmc/anaconda3/bin/python}"
TARGET="$SCRIPT_DIR/motors.py"

# Post a status snapshot to the Google Chat webhook every tick (whether or not a
# scrape is already running), so progress is visible while away. Never fatal.
"$PY" "$SCRIPT_DIR/monitor_status.py" || true

# pgrep -f matches the full command line; exclude this wrapper's own PID.
if pgrep -f "[m]otors.py" >/dev/null; then
    echo "motors.py is already running — skipping."
    exit 0
fi

echo "Starting motors.py ..."
exec "$PY" "$TARGET"
