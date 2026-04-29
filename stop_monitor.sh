#!/bin/bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

LOCK_FILE=".monitor.pid"

is_main_process() {
    local pid="$1"
    local cmdline
    cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    [[ -n "$cmdline" && "$cmdline" == *"python main.py"* ]]
}

stopped_any=false

if [[ -f "$LOCK_FILE" ]]; then
    pid_from_lock="$(cat "$LOCK_FILE" 2>/dev/null || true)"
    if [[ -n "${pid_from_lock}" ]] && is_main_process "$pid_from_lock"; then
        echo "Stopping monitor PID from lock: $pid_from_lock"
        kill "$pid_from_lock"
        stopped_any=true
    else
        echo "PID file exists but process is not running (or not main.py)."
    fi
fi

if [[ "$stopped_any" == false ]]; then
    running_pids="$(pgrep -f "python main.py" || true)"
    if [[ -n "$running_pids" ]]; then
        echo "Stopping monitor PID(s):"
        echo "$running_pids"
        while IFS= read -r pid; do
            [[ -n "$pid" ]] && kill "$pid"
        done <<< "$running_pids"
        stopped_any=true
    fi
fi

if [[ "$stopped_any" == true ]]; then
    rm -f "$LOCK_FILE"
    echo "Monitor stopped."
else
    rm -f "$LOCK_FILE"
    echo "No running monitor process found."
fi
