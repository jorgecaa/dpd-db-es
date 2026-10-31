#!/usr/bin/env python3
"""Build `dpd-serve.db`: `dpd.db` minus the columns the webapp never reads.

Dropping them takes the database from 2180.7 to 1470.1 MiB (32.6 %, measured
2026-09-18) and the webapp serves it byte-identically, but only because every
request-path load either `defer`s these columns or selects its own explicitly — an
ORM entity load selects every column the model declares, so a trimmed db without that
work returns `no such column` instead of a page.

    uv run python scripts/build/build_serving_db.py --source dpd.db --dest dpd-serve.db

The drop list is imported from `exporter/webapp/toolkit.py`, which is also what
`defer`s them at the query sites: one list, so the build can never drop a column the
running code still selects. See `kamma/threads/20260918_serving_db_diet/`.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path

from exporter.webapp.toolkit import (
    _UNREAD_HEADWORD_COLUMNS,
    _UNREAD_LOOKUP_COLUMNS,
)

DROPPABLE: dict[str, tuple[str, ...]] = {
    "dpd_headwords": _UNREAD_HEADWORD_COLUMNS,
    "lookup": _UNREAD_LOOKUP_COLUMNS,
}

# Where the scratch copy goes. Each `DROP COLUMN` rewrites its whole table, so the build
# is I/O-bound and the filesystem the scratch lands on dominates everything else:
# measured 2026-09-19, 15.9 s with the scratch on tmpfs against 530 s with it on this
# machine's (95 % full) disk — and the two resulting databases are byte-identical. So the
# scratch goes to a tmpfs when one has room; the destination is still written where asked.
SCRATCH_CANDIDATES = (Path("/dev/shm"), Path("/tmp"))


def mib(path: Path) -> float:
    return path.stat().st_size / 1048576


def tmpfs_scratch(needed_bytes: int) -> Path | None:
    """A tmpfs mount of `SCRATCH_CANDIDATES` with room for the scratch copy."""
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in mounts:
        parts = line.split()
        if len(parts) < 3 or parts[2] != "tmpfs":
            continue
        mount = Path(parts[1].replace("\\040", " "))
        if mount not in SCRATCH_CANDIDATES:
            continue
        try:
            if shutil.disk_usage(mount).free > needed_bytes:
                return mount
        except OSError:
            continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("dpd.db"))
    parser.add_argument("--dest", type=Path, default=Path("dpd-serve.db"))
    parser.add_argument("--no-vacuum", action="store_true")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="where to put the scratch copy (default: a tmpfs with room, else --dest's directory)",
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"source database not found: {args.source}")
    if args.dest.resolve() == args.source.resolve():
        raise SystemExit(
            "--dest must differ from --source: this build never edits dpd.db"
        )

    start = time.perf_counter()
    scratch = args.work_dir or tmpfs_scratch(int(args.source.stat().st_size * 1.2))
    if scratch is not None:
        work = scratch / f"{args.dest.name}.work"
    else:
        work = args.dest.with_name(args.dest.name + ".work")
    work.unlink(missing_ok=True)
    args.dest.unlink(missing_ok=True)
    shutil.copy2(args.source, work)
    print(f"copied   {mib(args.source):8.1f} MiB -> {work}")

    # A scratch copy that is rebuilt on demand does not need durability, and its journal
    # is not free: with WAL on, each `ALTER TABLE … DROP COLUMN` rewrites the table into
    # the -wal file, which on a spinning disk left a 1.5 GB journal and turned a ~60 s
    # build into one that had not finished in 10 minutes. `journal_mode=OFF` writes the
    # rewrite in place instead.
    with sqlite3.connect(work) as conn:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        existing = {
            table: {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            for table in DROPPABLE
        }
        for table, columns in DROPPABLE.items():
            missing = [column for column in columns if column not in existing[table]]
            if missing:
                raise SystemExit(
                    f"{table}: columns not in the source database: {missing}"
                )
            for column in columns:
                conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        conn.commit()
    print(f"dropped  {sum(len(c) for c in DROPPABLE.values())} columns")

    # `VACUUM INTO` compacts straight into the destination, so the build never needs room
    # for two full databases plus a journal on the same filesystem.
    if args.no_vacuum:
        work.replace(args.dest)
    else:
        vacuum_start = time.perf_counter()
        with sqlite3.connect(work) as conn:
            conn.execute("VACUUM INTO ?", (str(args.dest),))
        print(
            f"vacuumed into {args.dest.name} in {time.perf_counter() - vacuum_start:.1f} s"
        )
        work.unlink()

    # The db is only usable if it is intact and still identifies its release: the webapp
    # reads `db_info.dpd_release_version` for search-index cache busting, and the host
    # updater compares it before installing anything.
    with sqlite3.connect(f"file:{args.dest}?mode=ro", uri=True) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = conn.execute(
            "SELECT value FROM db_info WHERE key='dpd_release_version'"
        ).fetchone()
    if integrity != "ok":
        raise SystemExit(f"integrity_check failed: {integrity}")
    if not version or not version[0]:
        raise SystemExit("the built database has no db_info.dpd_release_version")

    saved = mib(args.source) - mib(args.dest)
    print(
        f"\n{args.dest}: {mib(args.dest):.1f} MiB, saved {saved:.1f} MiB "
        f"({saved / mib(args.source) * 100:.1f} %), version {version[0]}, "
        f"built in {time.perf_counter() - start:.1f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
