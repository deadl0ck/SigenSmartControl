#!/usr/bin/env bash
set -euo pipefail

# Installs a user-level systemd timer that refreshes handoff snapshots.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
SERVICE_NAME="sigen-handoff-snapshot.service"
TIMER_NAME="sigen-handoff-snapshot.timer"

mkdir -p "$USER_SYSTEMD_DIR"

cat > "$USER_SYSTEMD_DIR/$SERVICE_NAME" <<EOF
[Unit]
Description=Update Sigen session handoff snapshot

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
ExecStart=/usr/bin/env bash ${REPO_ROOT}/scripts/update_handoff_snapshot.sh
EOF

cat > "$USER_SYSTEMD_DIR/$TIMER_NAME" <<EOF
[Unit]
Description=Periodic Sigen session handoff snapshot update

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true
Unit=${SERVICE_NAME}

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER_NAME"

# Generate first snapshot immediately.
/usr/bin/env bash "${REPO_ROOT}/scripts/update_handoff_snapshot.sh"

echo "Installed and started ${TIMER_NAME}"
echo "Check status with: systemctl --user status ${TIMER_NAME}"
