"""The `_combos` memo in `tools/spanish.py` must stay bounded.

It memoises `lemma_1 -> overlaid meaning_combo` while translating the family tables
stored in `dpd.db`. It is a pure cache, so evicting can only cost a re-query; before
task 2.3 it grew with every lemma ever seen, bounded only by the 89,280 headwords.

See `kamma/threads/20260918_webapp_memory/plan.md`, task 2.3.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
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
