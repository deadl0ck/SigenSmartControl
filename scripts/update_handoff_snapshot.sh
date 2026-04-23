#!/usr/bin/env bash
set -euo pipefail

# Writes a compact project handoff snapshot for fast session recovery.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_FILE="$REPO_ROOT/docs/session-handoff-auto.md"
TMP_FILE="$(mktemp "${OUT_FILE}.tmp.XXXXXX")"

cleanup() {
  rm -f "$TMP_FILE"
}
trap cleanup EXIT

cd "$REPO_ROOT"

timestamp_local="$(date '+%Y-%m-%d %H:%M:%S %Z')"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
head_commit="$(git log -1 --pretty=format:'%h %s (%cr)' 2>/dev/null || echo 'none')"

{
  echo "# Session Handoff (Auto)"
  echo
  echo "_Last updated: ${timestamp_local}_"
  echo
  echo "## Snapshot"
  echo
  echo "- Branch: ${branch}"
  echo "- HEAD: ${head_commit}"
  echo
  echo "## Working Tree"
  echo
  if git diff --quiet --ignore-submodules HEAD -- 2>/dev/null; then
    echo "- Status: clean"
  else
    echo "- Status: dirty"
    echo ""
    echo "### Changed files"
    echo ""
    git status --short | sed 's/^/- /'
  fi
  echo
  echo "## Recent commits"
  echo
  git --no-pager log --oneline -5 | sed 's/^/- /'
  echo
  echo "## Suggested resume command"
  echo
  echo '```bash'
  echo "cd ${REPO_ROOT}"
  echo "git status"
  echo "cat docs/session-handoff.md"
  echo "cat docs/session-handoff-auto.md"
  echo '```'
} > "$TMP_FILE"

mv "$TMP_FILE" "$OUT_FILE"
trap - EXIT
