"""The `_combos` memo in `tools/spanish.py`, its bound, and its batched lookup.

It memoises `lemma_1 -> overlaid meaning_combo` while translating the family tables
stored in `dpd.db`. It is a pure cache, so evicting can only cost a re-query; before
task 2.3 it grew with every lemma ever seen, bounded only by the 89,280 headwords.

The lookup is batched below SQLite's parameter ceiling. Measured 2026-09-18: the build
this project runs allows 32,766 variables (40000 fails) and the biggest family table
renders 882 lemmas, so batching is a portability guard for older SQLite builds — whose
ceiling is 999 — not a fix for a live failure.

See `kamma/threads/20260918_webapp_memory/plan.md` (task 2.3) and
`kamma/threads/20260918_serving_db_diet/spec.md` (the addendum).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.models import Base, DpdHeadword
from tools import spanish


def _family_html(lemmas: list[str]) -> str:
    """A family table as it is stored in the db, one row per lemma."""
    rows = "".join(
        f"<tr><th>{lemma}</th><td><b>nt.</b></td><td>english meaning</td>"
        for lemma in lemmas
    )
    return f"<table>{rows}</table>"


def _session(lemmas: list[str]) -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        DpdHeadword(id=i, lemma_1=lemma, meaning_1=f"meaning of {lemma}")
        for i, lemma in enumerate(lemmas, start=1)
    )
    session.commit()
    return session


@pytest.fixture(autouse=True)
def overlay_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Enable the overlay without touching the sidecar db, and isolate the memo."""
    monkeypatch.setattr(spanish, "is_enabled", lambda: True)
    monkeypatch.setattr(spanish, "overlay", lambda headword, field, value: value)
    spanish._combos.clear()
    yield
    spanish._combos.clear()


def test_translates_from_the_live_headword_and_memoises() -> None:
    lemmas = ["dukkha", "dukkhanirodha"]
    session = _session(lemmas)
    produced = spanish.translate_family_html(_family_html(lemmas), session)
    assert "meaning of dukkha" in produced
    assert "meaning of dukkhanirodha" in produced
    assert "english meaning" not in produced
    assert set(spanish._combos) == set(lemmas)


def test_memo_stays_within_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spanish, "_COMBOS_MAX", 5)
    lemmas = [f"lemma{i}" for i in range(12)]
    session = _session(lemmas)
    spanish.translate_family_html(_family_html(lemmas), session)
    assert len(spanish._combos) <= 5


def test_memo_stays_within_the_cap_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spanish, "_COMBOS_MAX", 5)
    session = _session([f"lemma{i}" for i in range(20)])
    for start in range(0, 20, 3):
        batch = [f"lemma{i}" for i in range(start, start + 3)]
        spanish.translate_family_html(_family_html(batch), session)
    assert len(spanish._combos) <= 5


def test_capping_does_not_change_the_output(monkeypatch: pytest.MonkeyPatch) -> None:
    lemmas = [f"lemma{i}" for i in range(12)]
    session = _session(lemmas)
    html = _family_html(lemmas)

    monkeypatch.setattr(spanish, "_COMBOS_MAX", 100)
    uncapped = spanish.translate_family_html(html, session)
    spanish._combos.clear()

    monkeypatch.setattr(spanish, "_COMBOS_MAX", 3)
    capped = spanish.translate_family_html(html, session)

    assert capped == uncapped


# --- the batched `IN (...)` --------------------------------------------------


def _statements(session: Session) -> list[tuple[str, tuple[str, ...]]]:
    """Every statement from now on, with its parameters, whitespace collapsed."""
    engine: Engine = session.get_bind()  # type: ignore[assignment]
    collected: list[tuple[str, tuple[str, ...]]] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        collected.append((" ".join(statement.split()), tuple(parameters or ())))

    event.listen(engine, "before_cursor_execute", _record)
    return collected


def test_the_family_lookup_is_batched(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `IN (...)` per chunk, none bigger than the chunk size."""
    monkeypatch.setattr(spanish, "_IN_CHUNK", 3)
    lemmas = [f"lemma{i}" for i in range(10)]
    session = _session(lemmas)
    statements = _statements(session)

    spanish.translate_family_html(_family_html(lemmas), session)

    selects = [params for sql, params in statements if "FROM dpd_headwords" in sql]
    assert len(selects) == 4  # ceil(10 / 3)
    assert max(len(params) for params in selects) <= 3
    assert len(spanish._combos) == 10

    # No per-attribute lazy loads: naming a hybrid property in `load_only` is silently
    # ignored, which defers the real column and costs one SELECT per attribute per row.
    lazy = [
        sql
        for sql, _ in statements
        if sql.count("dpd_headwords.") == 1 and "WHERE dpd_headwords.id = " in sql
    ]
    assert not lazy, lazy


def test_batching_does_not_change_the_output(monkeypatch: pytest.MonkeyPatch) -> None:
    lemmas = [f"lemma{i}" for i in range(10)]
    session = _session(lemmas)
    html = _family_html(lemmas)

    monkeypatch.setattr(spanish, "_IN_CHUNK", 3)
    chunked = spanish.translate_family_html(html, session)
    spanish._combos.clear()

    monkeypatch.setattr(spanish, "_IN_CHUNK", 100)
    whole = spanish.translate_family_html(html, session)

    assert chunked == whole


def test_a_family_larger_than_the_old_parameter_limit_resolves() -> None:
    """1,200 lemmas in one table: over the 999 of old SQLite builds, under 32,766."""
    lemmas = [f"lemma{i}" for i in range(1200)]
    session = _session(lemmas)
    produced = spanish.translate_family_html(_family_html(lemmas), session)
    assert "english meaning" not in produced
    assert len(spanish._combos) == 1200
