#!/usr/bin/env python3
"""Which columns does the webapp actually read? (Phase 5 reconnaissance)

Runs a realistic request mix through the app in-process, captures every SQL
statement SQLAlchemy emits, and aggregates which columns of which table were
selected. Cross-referenced with the on-disk size per column, this is the evidence
a serving-db diet has to start from — `AGENTS.md` forbids speccing an optimisation
before re-deriving its numbers.

    PYTHONPATH=. .venv/bin/python kamma/threads/20260918_serving_db_diet/audit_columns.py \
        --out /tmp/usage_audit.json

It is read-only: no request writes to the database.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

from db.db_helpers import _get_cached_engine
from exporter.webapp.main import app
from tools.paths import ProjectPaths

SELECT_LIST = re.compile(r"^\s*SELECT\s+(?P<columns>.*?)\s+FROM\s+(?P<table>[\w\"]+)", re.S)


def workload(sample: list[str]) -> list[tuple[str, str]]:
    """The requests the audit exercises: word pages, searches, other tabs."""
    requests: list[tuple[str, str]] = []
    for lemma in sample:
        from urllib.parse import quote

        quoted = quote(lemma, safe="")
        requests.append((f"search_json {lemma}", f"/search_json?q={quoted}"))
        requests.append((f"gd {lemma}", f"/gd?search={quoted}"))
    requests += [
        ("search_html", "/search_html?q=buddha"),
        ("bd_search", "/bd_search?q1=dukkha&q2=suffering&option=starts_with"),
        ("status", "/status"),
        ("home", "/"),
        ("miss", "/search_json?q=zzzzz"),
    ]
    return requests


def column_sizes(db_path: Path) -> dict[str, dict[str, int]]:
    """Bytes held by each text column, per table."""
    sizes: dict[str, dict[str, int]] = {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            row = conn.execute(
                "SELECT "
                + ", ".join(
                    f'COALESCE(SUM(LENGTH("{column}")), 0)' for column in columns
                )
                + f' FROM "{table}"'
            ).fetchone()
            sizes[table] = dict(zip(columns, row, strict=True))
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("/tmp/usage_audit.json"))
    parser.add_argument("--sample-size", type=int, default=12)
    args = parser.parse_args()

    pth = ProjectPaths()
    engine: Engine = _get_cached_engine(pth.dpd_db_path)

    reads: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    statements: dict[str, int] = defaultdict(int)
    per_request: dict[str, int] = defaultdict(int)

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        match = SELECT_LIST.match(statement)
        statements["total"] += 1
        per_request[per_request["current"]] += 1
        if not match:
            return
        table = match.group("table").strip('"')
        for expression in match.group("columns").split(","):
            column = expression.strip().split(" AS ")[0].strip()
            if column.startswith(f"{table}."):
                reads[table][column.split(".", 1)[1]] += 1
            elif column == "*":
                reads[table]["*"] += 1

    event.listen(engine, "before_cursor_execute", _record)

    sample = [
        "buddha",
        "dhamma",
        "nibbāna",
        "saṅgha",
        "kamma",
        "jhāna",
        "anattā",
        "dukkha",
        "metta",
        "sati",
        "citta",
        "attha 1",
    ][: args.sample_size]

    client = TestClient(app)
    for label, path in workload(sample):
        per_request["current"] = label
        response = client.get(path)
        if response.status_code != 200:
            print(f"WARNING {label}: HTTP {response.status_code}")

    event.remove(engine, "before_cursor_execute", _record)

    sizes = column_sizes(pth.dpd_db_path)
    report = {
        "statements": statements["total"],
        "requests": len(workload(sample)),
        "per_request_statements": dict(per_request),
        "reads": {table: dict(columns) for table, columns in sorted(reads.items())},
        "sizes": sizes,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"{statements['total']} statements over {len(workload(sample))} requests -> {args.out}\n")
    print(f"{'table':22} {'column':26} {'MiB on disk':>12} {'reads':>7}")
    for table in sorted(sizes, key=lambda t: -sum(sizes[t].values())):
        table_total = sum(sizes[table].values())
        if table_total < 100_000:
            continue
        read_columns = reads.get(table, {})
        for column, nbytes in sorted(sizes[table].items(), key=lambda kv: -kv[1]):
            if nbytes < 1_000_000:
                continue
            count = read_columns.get(column, 0)
            if count == 0 and "*" in read_columns:
                count = -1  # read through an entity load (SELECT * of all columns)
            print(f"{table:22} {column:26} {nbytes / 1048576:12.1f} {count:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
