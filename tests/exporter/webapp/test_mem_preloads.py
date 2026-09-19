"""Memory guardrails for the webapp startup preloads.

The measurement runs in a subprocess so it starts from a clean allocator:
in-process, the arenas freed by an earlier measurement mask the next one — which
is exactly the retention this thread is about.

The ceiling is a ratchet (see `kamma/threads/20260918_webapp_memory/plan.md`):
**200 MiB**, the G1 budget, now that the preloads query columns — measured
83.6 MiB on 2026-09-18, down from 290.1 MiB with whole-row loads, so a revert to
the old pattern fails here too. It can also be overridden so the negative test can
prove this guard is able to fail:

    DPD_WEBAPP_MEM_CEILING_MIB=10 uv run pytest -m slow \
        tests/exporter/webapp/test_mem_preloads.py -q

Manual harness (prints the same numbers the tests assert):

    PYTHONPATH=. .venv/bin/python tests/exporter/webapp/test_mem_preloads.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from db.db_helpers import get_db_session
from exporter.webapp.preloads import (
    make_ascii_to_unicode_dict,
    make_headwords_clean_set,
    make_roots_count_dict,
)
from tools.cache_load import load_audio_dict
from tools.paths import ProjectPaths

RSS_CEILING_MIB = float(os.environ.get("DPD_WEBAPP_MEM_CEILING_MIB", "200"))
ENTITY_OVER_COLUMN_RATIO = 3.0

# Runs in a fresh interpreter: base RSS, each preload's cost, and the price of a
# single-column query against the price of materialising whole rows.
_MEASURE_SNIPPET = """
import ctypes, gc, json, os, time
import psutil
from sqlalchemy.orm import defer
from db.db_helpers import get_db_session
from db.models import DpdHeadword
from exporter.webapp.preloads import (
    make_ascii_to_unicode_dict, make_headwords_clean_set, make_roots_count_dict,
)
from tools.paths import ProjectPaths

libc = ctypes.CDLL("libc.so.6")

def rss():
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024

def trim():
    gc.collect()
    libc.malloc_trim(0)

out = {}
session = get_db_session(ProjectPaths().dpd_db_path)
out["base"] = round(rss(), 1)

t = time.time()
make_roots_count_dict(session)
out["roots_seconds"] = round(time.time() - t, 2)
out["roots_delta"] = round(rss() - out["base"], 1)

t = time.time()
make_headwords_clean_set(session)
out["set_seconds"] = round(time.time() - t, 2)

t = time.time()
make_ascii_to_unicode_dict(session)
out["ascii_seconds"] = round(time.time() - t, 2)
out["after_preloads"] = round(rss(), 1)

trim()
before = rss()
session.query(DpdHeadword.lemma_1).all()
out["column_delta"] = round(rss() - before, 1)

trim()
before = rss()
(
    session.query(DpdHeadword)
    .options(
        defer(DpdHeadword.inflections_html),
        defer(DpdHeadword.freq_html),
        defer(DpdHeadword.inflections_sinhala),
        defer(DpdHeadword.inflections_devanagari),
        defer(DpdHeadword.inflections_thai),
        defer(DpdHeadword.freq_data),
    )
    .all()
)
out["entity_delta"] = round(rss() - before, 1)

print("MEASUREMENT " + json.dumps(out))
"""


def get_mem() -> float:
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024


def measure(func, name: str, session) -> object:
    mem_before = get_mem()
    start = time.time()
    res = func(session)
    duration = time.time() - start
    mem_after = get_mem()
    print(
        f"{name}: {duration:.2f}s, Memory Delta: {mem_after - mem_before:.2f} MB, Total: {mem_after:.2f} MB"
    )
    return res


@pytest.fixture(scope="module")
def measurement() -> dict[str, float]:
    """Run the isolated measurement once per session and parse its numbers."""
    result = subprocess.run(
        [sys.executable, "-c", _MEASURE_SNIPPET],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(f"measurement subprocess failed:\n{result.stderr[-2000:]}")
    line = next(
        (
            ln
            for ln in reversed(result.stdout.splitlines())
            if ln.startswith("MEASUREMENT ")
        ),
        None,
    )
    if line is None:
        pytest.fail(f"no measurement line in output:\n{result.stdout[-2000:]}")
    return json.loads(line.removeprefix("MEASUREMENT "))


@pytest.mark.slow
def test_preloads_resident_memory_within_ceiling(measurement: dict[str, float]) -> None:
    """The startup preloads must fit the ratcheted RSS ceiling."""
    total = measurement["after_preloads"]
    print(
        f"base={measurement['base']} MiB total_after_preloads={total} MiB "
        f"ceiling={RSS_CEILING_MIB} MiB"
    )
    assert total <= RSS_CEILING_MIB, (
        f"startup preloads hold {total} MiB, ceiling is {RSS_CEILING_MIB} MiB — "
        "see kamma/threads/20260918_webapp_memory/plan.md (Phase 1)"
    )


@pytest.mark.slow
def test_entity_load_costs_much_more_than_columns(
    measurement: dict[str, float],
) -> None:
    """Whole-row loads must stay far more expensive than single-column ones.

    This is the mechanism behind the fix: if a future change makes the column
    query as costly as the entity query, the premise of Phase 1 no longer holds
    and the numbers have to be re-derived before touching anything.
    """
    column = measurement["column_delta"]
    entity = measurement["entity_delta"]
    print(f"column_delta={column} MiB entity_delta={entity} MiB")
    assert entity >= ENTITY_OVER_COLUMN_RATIO * column, (
        f"entity load cost {entity} MiB vs column load {column} MiB — expected at "
        f"least {ENTITY_OVER_COLUMN_RATIO}x; re-derive the Phase 1 numbers"
    )


def main() -> None:
    session = get_db_session(ProjectPaths().dpd_db_path)
    print(f"Initial Memory: {get_mem():.2f} MB")
    measure(make_roots_count_dict, "make_roots_count_dict", session)
    measure(make_headwords_clean_set, "make_headwords_clean_set", session)
    measure(make_ascii_to_unicode_dict, "make_ascii_to_unicode_dict", session)
    measure(lambda s: load_audio_dict(), "load_audio_dict", session)


if __name__ == "__main__":
    main()
