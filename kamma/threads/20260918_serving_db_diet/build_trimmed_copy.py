#!/usr/bin/env python3
"""Build a throwaway serving-db candidate: copy, drop columns, VACUUM.

`AGENTS.md` (Performance Work) requires benchmarking the claimed mechanism on a
throwaway copy before writing the optimisation spec. This is that benchmark: it
measures what dropping the columns the webapp never reads actually saves on disk.

    PYTHONPATH=. .venv/bin/python kamma/threads/20260918_serving_db_diet/build_trimmed_copy.py \
        --source dpd.db --dest /tmp/dpd_trim.db

The canonical `dpd.db` is only ever opened read-only; every change happens in the
copy. Dropping a column makes SQLite rewrite the table, and VACUUM compacts the
freelist those rewrites leave behind, so both steps are attributed separately.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path

# Columns no code path in exporter/webapp/ touches (verified by grep over the
# webapp's modules and templates, and by the runtime audit in audit_columns.py).
DROPPABLE: dict[str, tuple[str, ...]] = {
    "dpd_headwords": (
        "freq_data",  # renders into freq_html at build time; only tools/synonym_variant.py reads it
        "inflections_sinhala",
        "inflections_thai",
        "inflections_devanagari",
        "inflections",  # the webapp renders inflections_html, never this
        "inflections_api_ca_eva_iti",
        "created_at",
        "updated_at",
    ),
    "lookup": (
        "sinhala",
        "thai",
        "devanagari",
        "rpd",
    ),
}


def mib(path: Path) -> float:
    return path.stat().st_size / 1048576


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("dpd.db"))
    parser.add_argument("--dest", type=Path, default=Path("/tmp/dpd_trim.db"))
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args()

    start = time.perf_counter()
    shutil.copy2(args.source, args.dest)
    print(f"copied   {mib(args.source):8.1f} MiB -> {args.dest}  ({time.perf_counter() - start:.1f} s)")

    with sqlite3.connect(args.dest) as conn:
        for table, columns in DROPPABLE.items():
            for column in columns:
                before = time.perf_counter()
                conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
                print(f"  dropped {table}.{column:26} ({time.perf_counter() - before:5.1f} s)")
        conn.commit()

    after_drops = mib(args.dest)
    print(f"after drops: {after_drops:.1f} MiB (freelist not yet reclaimed)")

    if not args.no_vacuum:
        vacuum_start = time.perf_counter()
        with sqlite3.connect(args.dest) as conn:
            conn.execute("VACUUM")
        print(f"after VACUUM: {mib(args.dest):.1f} MiB ({time.perf_counter() - vacuum_start:.1f} s)")

    total = time.perf_counter() - start
    saved = mib(args.source) - mib(args.dest)
    print(
        f"\nsaved {saved:.1f} MiB of {mib(args.source):.1f} MiB "
        f"({saved / mib(args.source) * 100:.1f} %) in {total:.1f} s total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
