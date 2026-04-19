#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Restarting monitor..."
"$SCRIPT_DIR/stop_monitor.sh"
sleep 2
"$SCRIPT_DIR/start_monitor.sh"
