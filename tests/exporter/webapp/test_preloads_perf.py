"""Wall-clock guardrails for the webapp startup preloads.

Ceilings are ratchets around the post-fix baseline of 2026-09-18 (0.13 s / 0.55 s /
1.08 s). The 3 s ceiling on the two dictionary-sized ones is deliberate: a revert
to whole-row loads measured 4.2–4.4 s, so that regression fails here too. Both can
be overridden so the negative test can prove the guard is able to fail:

    DPD_WEBAPP_PRELOAD_SECONDS=0.001 uv run pytest -m slow \
        tests/exporter/webapp/test_preloads_perf.py -q

Manual harness (prints the same timings the tests assert):

    PYTHONPATH=. .venv/bin/python tests/exporter/webapp/test_preloads_perf.py
"""

import os
import time

import pytest

from db.db_helpers import get_db_session
from exporter.webapp.preloads import (
    make_ascii_to_unicode_dict,
    make_headwords_clean_set,
    make_roots_count_dict,
)
from tools.paths import ProjectPaths

TABLE_CEILING_SECONDS = float(os.environ.get("DPD_WEBAPP_PRELOAD_SECONDS", "3"))
ROOTS_CEILING_SECONDS = 1.0


def measure(func, name: str, session) -> object:
    start = time.time()
    res = func(session)
    print(f"{name}: {time.time() - start:.4f}s")
    return res


@pytest.fixture(scope="module")
def timings() -> dict[str, float]:
    """Time the three startup preloads once per session."""
    session = get_db_session(ProjectPaths().dpd_db_path)
    print("Measuring preloads...")
    out: dict[str, float] = {}
    for name, func in (
        ("make_roots_count_dict", make_roots_count_dict),
        ("make_headwords_clean_set", make_headwords_clean_set),
        ("make_ascii_to_unicode_dict", make_ascii_to_unicode_dict),
    ):
        start = time.time()
        func(session)
        out[name] = time.time() - start
    return out


@pytest.mark.slow
@pytest.mark.parametrize(
    ("name", "ceiling"),
    [
        ("make_roots_count_dict", ROOTS_CEILING_SECONDS),
        ("make_headwords_clean_set", TABLE_CEILING_SECONDS),
        ("make_ascii_to_unicode_dict", TABLE_CEILING_SECONDS),
    ],
)
def test_preload_within_time_budget(
    timings: dict[str, float], name: str, ceiling: float
) -> None:
    """Each preload must stay inside its time budget."""
    elapsed = timings[name]
    print(f"{name}: {elapsed:.2f}s (ceiling {ceiling:.2f}s)")
    assert elapsed <= ceiling, (
        f"{name} took {elapsed:.2f}s, ceiling is {ceiling:.2f}s — "
        "see kamma/threads/20260918_webapp_memory/plan.md"
    )


def main() -> None:
    session = get_db_session(ProjectPaths().dpd_db_path)
    print("Measuring preloads...")
    measure(make_roots_count_dict, "make_roots_count_dict", session)
    measure(make_headwords_clean_set, "make_headwords_clean_set", session)
    measure(make_ascii_to_unicode_dict, "make_ascii_to_unicode_dict", session)


if __name__ == "__main__":
    main()
