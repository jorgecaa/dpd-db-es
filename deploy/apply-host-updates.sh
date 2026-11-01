#!/usr/bin/env bash
# apply-host-updates.sh — install the 2026-09-18 host changes for the webapp.
#
# Needs root. It does the two things the memory thread could not do without a
# password (kamma/threads/20260918_webapp_memory, Phase 4):
#
#   1. the cleaned `dpd-webapp.service`: no /dev/shm preload plumbing, /dev/shm
#      removed from ReadWritePaths, PrivateDevices back to yes, and the memory
#      guardrail (MemoryHigh=512M / MemoryMax=1G)
#   2. the corrected `dpd-update.sh`: the release asset is `dpd.db.tar.xz` (the old
#      `.tar.bz2` 404s), the GitHub API call retries, and an older tag no longer
#      overwrites a newer db
#
# Usage:
#     sudo deploy/apply-host-updates.sh [--dry-run]
#
# Every file it overwrites is backed up next to the original with a timestamp, and
# the revert commands are printed at the end.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$REPO_DIR/deploy/dpd-webapp.service"
UNIT_DST="/etc/systemd/system/dpd-webapp.service"
UPDATER_SRC="$REPO_DIR/scripts/server/dpd-update.sh"
UPDATER_DST="/usr/local/bin/dpd-update.sh"
SERVICE="dpd-webapp.service"
STAMP="$(date +%Y%m%d-%H%M%S)"

DRY_RUN="no"
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="yes" ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

log() { echo "[apply-host-updates] $*"; }
die() { echo "[apply-host-updates] ERROR: $*" >&2; exit 1; }

[[ -f "$UNIT_SRC" ]] || die "missing $UNIT_SRC"
[[ -f "$UPDATER_SRC" ]] || die "missing $UPDATER_SRC"

if [[ "$DRY_RUN" == "yes" ]]; then
    log "dry run — these are the changes that would be applied:"
    diff -u "$UNIT_DST" "$UNIT_SRC" || true
    log "would back up: $UNIT_DST -> ${UNIT_DST}.bak-$STAMP"
    log "would install: $UNIT_SRC -> $UNIT_DST"
    log "would back up: $UPDATER_DST -> ${UPDATER_DST}.bak-$STAMP"
    log "would install: $UPDATER_SRC -> $UPDATER_DST (chmod +x)"
    log "would run: systemctl daemon-reload && systemctl restart $SERVICE"
    log "would verify: curl http://127.0.0.1:8080/ and $UPDATER_DST --check"
    exit 0
fi

[[ "$(id -u)" == "0" ]] || die "run me with sudo"

log "backing up and installing the unit ..."
cp -a "$UNIT_DST" "${UNIT_DST}.bak-$STAMP"
install -m 644 "$UNIT_SRC" "$UNIT_DST"

log "backing up and installing the updater ..."
cp -a "$UPDATER_DST" "${UPDATER_DST}.bak-$STAMP"
install -m 755 "$UPDATER_SRC" "$UPDATER_DST"

log "reloading systemd and restarting $SERVICE ..."
systemctl daemon-reload
systemctl restart "$SERVICE"

log "waiting for the webapp to answer ..."
for attempt in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8080/ || true)"
    if [[ "$code" == "200" ]]; then
        log "webapp answered HTTP 200 after ~$((attempt * 2))s"
        break
    fi
    sleep 2
done
[[ "${code:-}" == "200" ]] || die "the webapp did not answer 200; check: journalctl -u $SERVICE -n 50"

log "memory guardrail in effect:"
systemctl show "$SERVICE" -p MemoryHigh -p MemoryMax | sed 's/^/  /'

log "checking the updated release compared to the published one:"
"$UPDATER_DST" --check | sed 's/^/  /'

cat <<EOF

done. Revert with:
  sudo cp ${UNIT_DST}.bak-$STAMP $UNIT_DST
  sudo cp ${UPDATER_DST}.bak-$STAMP $UPDATER_DST
  sudo systemctl daemon-reload && sudo systemctl restart $SERVICE
EOF
