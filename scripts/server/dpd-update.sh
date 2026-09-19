#!/usr/bin/env bash
# dpd-update.sh — keep dpd.db current from the latest DPD release.
#
# Host-side companion of `dpd-update.service` / `dpd-update.timer`: it reads the
# installed release from the db itself, asks GitHub for the newest one, and only
# then downloads, validates and installs it, respecting the RAM lifecycle of
# `dpd-webapp.service` (`deploy/preload-db.sh` / `deploy/unload-db.sh`).
#
# Install (root-owned, outside the repo — this file is the tracked source):
#
#     sudo cp scripts/server/dpd-update.sh /usr/local/bin/dpd-update.sh
#     sudo chmod +x /usr/local/bin/dpd-update.sh
#
# Modes:
#     dpd-update.sh              update if a newer release exists (default)
#     dpd-update.sh --check      report installed vs published, change nothing
#     dpd-update.sh --dry-run    also validate the asset URL, change nothing
#     dpd-update.sh --force      install even if the tag is not newer
#
# Fixed 2026-09-18 (kamma/threads/20260918_webapp_memory, task 4.2):
#   * the release asset is `dpd.db.tar.xz`; the old `.tar.bz2` name 404s
#   * the GitHub API call retries instead of dying on a transient DNS failure
#   * a downgrade guard: a tag older than the installed one no longer clobbers it
set -euo pipefail

REPO="digitalpalidictionary/dpd-db"
ASSET_NAME="dpd.db.tar.xz"
# Overridable so the script can be exercised without touching the real release
# (a local file:// response, a mirror, or a deliberately unreachable API host).
ASSET_URL="${DPD_ASSET_URL:-https://github.com/${REPO}/releases/latest/download/${ASSET_NAME}}"
API_URL="${DPD_API_URL:-https://api.github.com/repos/${REPO}/releases/latest}"
RETRY_DELAY="${DPD_RETRY_DELAY:-10}"

REPO_DIR="${DPD_REPO_DIR:-/home/jorge/Code/dpd-db}"
DB_DISK="${REPO_DIR}/dpd.db"          # canonical path (may be a symlink into RAM)
DB_BACKUP="${REPO_DIR}/dpd.db.disk"   # backup used by preload/unload
DB_RAM="/dev/shm/dpd.db"
DB_OWNER="${DPD_DB_OWNER:-jorge:jorge}"

SERVICE="${DPD_SERVICE:-dpd-webapp.service}"
WORKDIR="$(mktemp -d /tmp/dpd-update.XXXXXX)"

MODE="update"
FORCE="no"
for arg in "$@"; do
    case "$arg" in
        --check)   MODE="check" ;;
        --dry-run) MODE="dry-run" ;;
        --force)   FORCE="yes" ;;
        *) echo "[dpd-update] ERROR: unknown option: $arg" >&2; exit 2 ;;
    esac
done

log()  { echo "[dpd-update] $*"; }
die()  { echo "[dpd-update] ERROR: $*" >&2; exit 1; }
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

# ── 1. Installed version (read from the db in use) ───────────────────────────
read_version() {  # $1 = path to a dpd.db
    sqlite3 "$1" "SELECT value FROM db_info WHERE key='dpd_release_version';" 2>/dev/null || true
}

CURRENT_DB="$DB_DISK"
if [[ -L "$DB_DISK" && -f "$DB_RAM" ]]; then
    CURRENT_DB="$DB_RAM"
elif [[ ! -e "$DB_DISK" && -f "$DB_BACKUP" ]]; then
    CURRENT_DB="$DB_BACKUP"
fi

INSTALLED="$(read_version "$CURRENT_DB")"
[[ -n "$INSTALLED" ]] || INSTALLED="(none)"
log "installed: $INSTALLED"

# ── 2. Latest published version (retrying the API) ───────────────────────────
API_JSON=""
for attempt in 1 2 3 4 5; do
    if API_JSON="$(curl -fsSL --retry 2 --retry-delay 3 "$API_URL" 2>/dev/null)"; then
        break
    fi
    log "GitHub API attempt ${attempt}/5 failed; retrying in ${RETRY_DELAY} s"
    sleep "$RETRY_DELAY"
done
[[ -n "$API_JSON" ]] || die "could not reach the GitHub API after 5 attempts"

LATEST="$(printf '%s' "$API_JSON" \
          | grep -m1 '"tag_name"' \
          | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
[[ -n "$LATEST" ]] || die "could not read the latest version from the API response"
log "published: $LATEST"

# In dry-run the point is to prove the plumbing, so the asset is checked even when
# the versions already match.
if [[ "$MODE" == "dry-run" ]]; then
    code="$(curl -sIL -o /dev/null -w '%{http_code}' --retry 2 "$ASSET_URL" || true)"
    [[ "$code" == "200" ]] || die "asset $ASSET_NAME is not downloadable (HTTP $code)"
    log "asset check: $ASSET_NAME -> HTTP $code (downloadable)"
fi

if [[ "$LATEST" == "$INSTALLED" ]]; then
    log "already at the latest version; nothing to do."
    exit 0
fi

# Downgrade guard: a tag that sorts *older* than what is installed means someone
# published a rollback — do not silently replace a newer db with an older one.
if [[ "$MODE" == "update" && "$FORCE" == "no" ]] && [[ "$INSTALLED" != "(none)" ]]; then
    newest="$(printf '%s\n%s\n' "$INSTALLED" "$LATEST" | sort -V | tail -1)"
    if [[ "$newest" != "$LATEST" ]]; then
        log "published version ($LATEST) is older than the installed one ($INSTALLED);"
        log "refusing to downgrade. Use --force to install it anyway."
        exit 0
    fi
fi

if [[ "$MODE" != "update" ]]; then
    log "mode $MODE: release $LATEST is available, asset would be $ASSET_URL"
    exit 0
fi

# ── 3. Download and unpack ───────────────────────────────────────────────────
log "downloading $ASSET_URL ..."
curl -fL --retry 3 --retry-delay 5 -o "$WORKDIR/$ASSET_NAME" "$ASSET_URL" \
    || die "download failed"

log "unpacking ..."
tar -xJf "$WORKDIR/$ASSET_NAME" -C "$WORKDIR" || die "unpacking failed"

NEW_DB="$(find "$WORKDIR" -name 'dpd.db' -type f | head -1)"
[[ -n "$NEW_DB" ]] || die "no dpd.db inside the archive"

# ── 4. Validate what was downloaded ──────────────────────────────────────────
log "validating SQLite integrity ..."
ok="$(sqlite3 "$NEW_DB" "PRAGMA integrity_check;" 2>/dev/null | head -1)"
[[ "$ok" == "ok" ]] || die "integrity_check failed: '$ok'"

NEW_VER="$(read_version "$NEW_DB")"
[[ -n "$NEW_VER" ]] || die "the downloaded db has no db_info.dpd_release_version"
log "downloaded version: $NEW_VER"

# ── 5. Install, respecting the webapp's RAM lifecycle ────────────────────────
WAS_ACTIVE=no
if systemctl is-active --quiet "$SERVICE"; then
    WAS_ACTIVE=yes
    log "stopping $SERVICE (unloads the RAM copy) ..."
    systemctl stop "$SERVICE"
fi

# A symlink left behind by a dirty stop is restored to a real file here.
if [[ -L "$DB_DISK" ]]; then
    rm -f "$DB_DISK"
    [[ -f "$DB_BACKUP" ]] && mv "$DB_BACKUP" "$DB_DISK" || true
fi
rm -f "$DB_RAM" "$DB_BACKUP"

if [[ -f "$DB_DISK" ]]; then
    mv -f "$DB_DISK" "${DB_DISK}.prev"
fi

log "installing the new db at $DB_DISK ..."
cp "$NEW_DB" "$DB_DISK"
chown "$DB_OWNER" "$DB_DISK"
chmod 644 "$DB_DISK"
rm -f "${DB_DISK}.prev"

if [[ "$WAS_ACTIVE" == "yes" ]]; then
    log "starting $SERVICE (reloads the db) ..."
    systemctl start "$SERVICE"
fi

log "update complete: $INSTALLED -> $NEW_VER"
