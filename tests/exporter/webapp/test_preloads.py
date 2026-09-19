"""Parity and mechanism tests for `exporter/webapp/preloads.py`.

Three jobs:

1. `make_roots_count_dict` stays interchangeable with the goldendict helper
   (frozen fixture, unchanged).
2. The webapp's column-only preloads produce **exactly** what the old whole-row
   implementation produced, because that is what the golden-output oracle was
   captured against. `clean_lemma` is the one derivation that now lives in two
   places (`db/models.py::DpdHeadword.lemma_clean` and here), so a row-by-row
   test keeps them identical.
3. The preloads keep selecting **columns, not rows**: reverting to
   `query(DpdHeadword)` would re-introduce ~541 MiB of anonymous RSS that the
   allocator never returns. See
   `kamma/threads/20260918_webapp_memory/spec.md` (R1/R2) for the measurement.
"""

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from unidecode import unidecode

from db.models import Base, DpdHeadword, Lookup
from exporter.goldendict.helpers import make_roots_count_dict as goldendict_make
from exporter.webapp.preloads import (
    clean_lemma,
    make_ascii_to_unicode_dict,
    make_headwords_clean_set,
    make_roots_count_dict as webapp_make,
)
from tools.pali_sort_key import pali_list_sorter

FIXTURE_PATH = (
    Path(__file__).parent.parent / "goldendict" / "test_helpers_fixtures.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

# (lemma_1, lemma_2) chosen for the derivation's edge cases: numbered senses,
# digits that are not a sense number, diacritics on both columns, and empty
# strings. `lemma_clean` is `re.sub(r" \d.*$", "", lemma_1)`.
LEMMAS: list[tuple[str, str]] = [
    ("dhamma 1.01", ""),
    ("dhamma 2", ""),
    ("a 1.1", ""),
    ("buddha", "buddhā"),
    ("kamma2", ""),
    ("saṅgha", ""),
    ("nibbāna", ""),
    ("jhāna", "jhānā"),
    ("paṭiccasamuppāda", ""),
    ("ṭhāna", ""),  # two forms sharing one ASCII key, to lock the group order
    ("thāna", ""),
    ("", ""),
]

LOOKUP_ROWS: list[tuple[str, str]] = [
    ("buddha", "Buddha; Awakened One"),
    ("dhamma", "teaching"),
    ("sangha", ""),  # no English meaning -> excluded from the clean set
]


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        DpdHeadword(id=r["id"], lemma_1=r["lemma_1"], root_key=r["root_key"])
        for r in FIXTURE["rows"]
    )
    session.commit()
    return session


def _build_preload_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        DpdHeadword(id=i, lemma_1=lemma_1, lemma_2=lemma_2)
        for i, (lemma_1, lemma_2) in enumerate(LEMMAS, start=1)
    )
    session.add_all(Lookup(lookup_key=key, epd=epd) for key, epd in LOOKUP_ROWS)
    session.commit()
    return session


def _capture_statements(session: Session) -> list[str]:
    """Record every SQL statement the session executes from now on."""
    engine: Engine = session.get_bind()  # type: ignore[assignment]
    statements: list[str] = []

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    return statements


def _expected_clean_set(session: Session) -> set[str]:
    """The OLD behaviour: whole rows, `lemma_clean` from the model property."""
    clean = {row.lemma_clean for row in session.query(DpdHeadword).all()}
    clean.update(
        key
        for (key,) in session.query(Lookup.lookup_key).filter(Lookup.epd != "").all()
    )
    return clean


def _expected_ascii_dict(session: Session) -> dict[str, list[str]]:
    """The OLD behaviour: whole rows, `lemma_clean` from the model property."""
    pool: set[str] = set()
    for row in session.query(DpdHeadword).all():
        pool.add(row.lemma_clean)
        pool.add(row.lemma_2)
    out: dict[str, list[str]] = defaultdict(list)
    for headword in pali_list_sorter(pool):
        ascii_form = unidecode(headword)
        if ascii_form != headword and headword not in out[ascii_form]:
            out[ascii_form].append(headword)
    return out


# --- make_roots_count_dict: unchanged frozen-fixture parity -------------------


def test_webapp_roots_count_matches_frozen_output() -> None:
    session = _build_session()
    assert webapp_make(session) == FIXTURE["expected"]


def test_webapp_roots_count_matches_goldendict() -> None:
    session = _build_session()
    assert webapp_make(session) == goldendict_make(session)


def test_webapp_roots_count_empty_db() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    assert webapp_make(session) == {}


# --- the derivation and the preloads themselves ------------------------------


def test_clean_lemma_matches_model_property() -> None:
    """`clean_lemma` must stay identical to `DpdHeadword.lemma_clean`."""
    session = _build_preload_session()
    for row in session.query(DpdHeadword).all():
        assert clean_lemma(row.lemma_1) == row.lemma_clean


def test_clean_lemma_expected_values() -> None:
    assert clean_lemma("dhamma 1.01") == "dhamma"
    assert clean_lemma("dhamma 2") == "dhamma"
    assert clean_lemma("a 1.1") == "a"
    assert clean_lemma("kamma2") == "kamma2"  # a digit that is not a sense number
    assert clean_lemma("buddha") == "buddha"
    assert clean_lemma("") == ""


def test_headwords_clean_set_matches_entity_derivation() -> None:
    session = _build_preload_session()
    assert make_headwords_clean_set(session) == _expected_clean_set(session)


def test_headwords_clean_set_excludes_lookup_rows_without_english() -> None:
    session = _build_preload_session()
    clean = make_headwords_clean_set(session)
    assert "buddha" in clean and "dhamma" in clean
    assert "sangha" not in clean


def test_headwords_clean_set_empty_db() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert make_headwords_clean_set(Session(engine)) == set()


def test_ascii_to_unicode_dict_matches_entity_derivation() -> None:
    session = _build_preload_session()
    produced = make_ascii_to_unicode_dict(session)
    assert produced == _expected_ascii_dict(session)
    assert isinstance(produced, defaultdict)  # callers index missing keys


def test_ascii_to_unicode_dict_groups_forms_in_sort_order() -> None:
    """Frozen expectations: which forms group under one key, and in what order."""
    session = _build_preload_session()
    produced = make_ascii_to_unicode_dict(session)
    assert produced["buddha"] == ["buddhā"]  # ASCII lemmas map to themselves: skipped
    assert produced["sangha"] == ["saṅgha"]
    assert produced["jhana"] == ["jhāna", "jhānā"]
    assert produced["thana"] == ["ṭhāna", "thāna"]  # two forms, one ASCII key
    assert "kamma2" not in produced  # already ASCII


# --- mechanism lock: columns, never rows -------------------------------------


@pytest.mark.parametrize(
    ("func", "expected_columns"),
    [
        (make_headwords_clean_set, {"dpd_headwords.lemma_1"}),
        (
            make_ascii_to_unicode_dict,
            {"dpd_headwords.lemma_1", "dpd_headwords.lemma_2"},
        ),
    ],
)
def test_headword_queries_select_only_columns(
    func: Callable[[Session], Any], expected_columns: set[str]
) -> None:
    """The preloads must select columns, not whole `DpdHeadword` rows."""
    session = _build_preload_session()
    statements = _capture_statements(session)
    func(session)
    headword_statements = [s for s in statements if "FROM dpd_headwords" in s]
    assert len(headword_statements) == 1, headword_statements
    select_list = headword_statements[0].split("FROM")[0].removeprefix("SELECT").strip()
    # SQLAlchemy labels the columns ("dpd_headwords.lemma_1 AS dpd_headwords_lemma_1"),
    # so compare the source column of each selected expression.
    selected = {
        expression.strip().split(" AS ")[0].strip()
        for expression in select_list.split(",")
    }
    assert selected == expected_columns
