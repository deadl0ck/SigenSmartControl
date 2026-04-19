#!/bin/bash
set -euo pipefail

cd /home/martin/git/SigenSmartControl

LOCK_FILE=".monitor.pid"
LOG_FILE="monitor.log"

running_pids="$(pgrep -f "python main.py" || true)"
if [[ -n "$running_pids" ]]; then
	echo "Monitor already running. Existing PID(s):"
	echo "$running_pids"
	echo "Refusing to start a second instance."
	exit 1
fi

if [[ -f "$LOCK_FILE" ]]; then
	echo "Removing stale PID file: $LOCK_FILE"
	rm -f "$LOCK_FILE"
fi

source .venv/bin/activate
nohup python main.py >> "$LOG_FILE" 2>&1 &
new_pid="$!"
echo "$new_pid" > "$LOCK_FILE"

echo "Monitor started. PID: $new_pid"
echo "PID file: $LOCK_FILE"
echo "View logs with: tail -f $LOG_FILE"
