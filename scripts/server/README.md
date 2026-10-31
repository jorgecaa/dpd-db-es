# Webapp host updater (fork only)

The rest of what lived here moved upstream: the contributor web server tooling is
now in `scripts/server_contrib/`, and the dpdict.net deploy script
(`update-dpd.sh`) in `scripts/server_dpdict/`.

## `dpd-update.sh` — the webapp host updater

Unrelated to `update-dpd.sh`: `dpd-update.sh` is the tracked source of the script
that `dpd-update.service`/`dpd-update.timer` run on the webapp host to keep
`dpd.db` current from the latest release (read installed version → ask GitHub →
download, validate, install, restart the service).

```bash
./scripts/server/dpd-update.sh --check      # report installed vs published
./scripts/server/dpd-update.sh --dry-run    # also validate the asset URL
./scripts/server/dpd-update.sh              # update (root, via the systemd unit)

sudo cp scripts/server/dpd-update.sh /usr/local/bin/dpd-update.sh
```

Fixed 2026-09-18: the asset is `dpd.db.tar.xz` (the old `.tar.bz2` name 404s), the
API call retries instead of dying on a transient DNS failure, and an older tag no
longer overwrites a newer installed db (`--force` overrides). `DPD_API_URL`,
`DPD_ASSET_URL`, `DPD_REPO_DIR`, `DPD_SERVICE` and `DPD_RETRY_DELAY` exist so the
script can be exercised without touching the real release.
