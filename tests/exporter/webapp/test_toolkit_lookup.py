"""Tests for the indexed `lookup` probe in `exporter/webapp/toolkit.py`.

The old search predicate (`lower(lookup_key) LIKE lower(?)`) forced SQLite to scan
all 1,281,569 `lookup` rows — 419 MB of pages and ~520 ms per search. The probe
reproduces the same rows through the primary-key index (0.017 ms), and keeps the old
predicate only as a safety net for spellings no candidate list can guess.

Measurements and the equivalence run on the live db (18 of 18 cases identical) are
in `kamma/threads/20260918_webapp_memory/plan.md`, task 2.2.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, or_
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.models import Base, Lookup
from exporter.webapp.toolkit import (
    lookup_key_variant_rows,
    lookup_rows,
    probe_keys,
)

# Inserted in this order, so rowid order is predictable. They cover: a key stored
# twice with different case, a spelling no case-folding of the input produces
# (`ANa`), a trailing-dot key, an exotic internal capital (`iPhone`), and a
# two-word English phrase.
KEYS = ["buddha", "Buddha", "dhamma", "ana", "ANa", "kamma.", "iPhone", "I am"]


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(Lookup(lookup_key=key, headwords="1") for key in KEYS)
    session.commit()
    return session


def _old_predicate(session: Session, q: str) -> set[str]:
    """The historical query: a case-insensitive scan of the whole table."""
    rows = (
        session.query(Lookup.lookup_key)
        .filter(
            or_(
                func.lower(Lookup.lookup_key).like(func.lower(q)),
                func.lower(Lookup.lookup_key).like(func.lower(q + ".")),
            )
        )
        .all()
    )
    return {key for (key,) in rows}


def _statements(session: Session) -> list[str]:
    """Every statement executed from now on, with whitespace collapsed."""
    engine: Engine = session.get_bind()  # type: ignore[assignment]
    collected: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        collected.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _record)
    return collected


# --- the probe returns exactly what the scan returned ------------------------


@pytest.mark.parametrize(
    "term",
    [
        "buddha",
        "Buddha",
        "BUDDHA",
        "buddha.",
        "ana",
        "ANa",
        "I am",
        "i am",
        "kamma.",
        "iPhone",
        "zzzzz",
    ],
)
def test_probe_matches_the_old_predicate(term: str) -> None:
    session = _session()
    variants = lookup_key_variant_rows(session)
    produced = {row.lookup_key for row in lookup_rows(session, term, variants)}
    assert produced == _old_predicate(session, term)


def test_empty_input_matches_the_old_predicate() -> None:
    session = _session()
    variants = lookup_key_variant_rows(session)
    assert lookup_rows(session, "", variants) == []


def test_rowid_order_is_preserved() -> None:
    """The old scan returned rowid order; the probe must not reorder it."""
    session = _session()
    variants = lookup_key_variant_rows(session)
    rows = lookup_rows(session, "buddha", variants)
    assert [row.lookup_key for row in rows] == ["buddha", "Buddha"]


# --- completeness of the candidate spellings ---------------------------------


def test_variants_map_holds_both_spellings_under_both_folds() -> None:
    session = _session()
    variants = lookup_key_variant_rows(session)
    assert set(variants["buddha"]) == {"buddha", "Buddha"}
    assert set(variants["ana"]) == {"ana", "ANa"}


def test_probe_includes_spelling_that_case_folding_cannot_produce() -> None:
    session = _session()
    variants = lookup_key_variant_rows(session)
    # `ana`.upper() is ANA, `ana`.capitalize() is Ana: only the map knows ANa.
    assert "ANa" in probe_keys("ana", variants)


def test_probe_covers_the_trailing_dot_form() -> None:
    session = _session()
    variants = lookup_key_variant_rows(session)
    assert "kamma." in probe_keys("kamma", variants)


# --- mechanism lock ----------------------------------------------------------


def test_a_hit_goes_through_the_index() -> None:
    session = _session()
    variants = lookup_key_variant_rows(session)
    statements = _statements(session)
    lookup_rows(session, "buddha", variants)
    assert any("WHERE lookup.lookup_key IN (" in statement for statement in statements)
    assert not any("LIKE lower" in statement for statement in statements)

    # The columns no code reads are deferred, so a serving-db build can drop them
    # (`kamma/threads/20260918_serving_db_diet/`).
    unread = ("sinhala", "thai", "devanagari", "rpd", "other")
    leaked = sorted(
        {
            column
            for statement in statements
            for column in unread
            if f"lookup.{column} AS" in statement
        }
    )
    assert not leaked, leaked


def test_a_miss_falls_back_to_the_covering_index_scan() -> None:
    """The safety net reads only the key column, so SQLite can cover it."""
    session = _session()
    variants = lookup_key_variant_rows(session)
    statements = _statements(session)
    lookup_rows(session, "zzzzz", variants)
    scans = [statement for statement in statements if "LIKE lower" in statement]
    assert scans, statements
    assert all(
        "SELECT lookup.lookup_key AS lookup_lookup_key FROM lookup WHERE" in statement
        for statement in scans
    )
    assert not any("lookup.deconstructor" in statement for statement in scans)


def test_fallback_still_finds_a_spelling_no_candidate_can_guess() -> None:
    """`iPhone` is stored once, so it is not in the variants map."""
    session = _session()
    variants = lookup_key_variant_rows(session)
    assert "iPhone" not in variants
    rows = lookup_rows(session, "iphone", variants)
    assert [row.lookup_key for row in rows] == ["iPhone"]
