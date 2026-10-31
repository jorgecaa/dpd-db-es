"""`spanish.instance_fields`: `vars()` keyed by column name, overlay columns included."""

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, DpdHeadword, DpdRoot
from gui2.corrections_manager import CorrectionsManager
from gui2.database_manager import DatabaseManager
from gui2.paths import Gui2Paths
from tools import spanish

if TYPE_CHECKING:
    from gui2.toolkit import ToolKit


def test_overlay_columns_come_back_under_their_column_names() -> None:
    word = DpdHeadword(
        id=1, lemma_1="assa 1", meaning_1="horse", meaning_lit="lit", meaning_2="steed"
    )
    fields = spanish.instance_fields(word)
    assert fields["meaning_1"] == "horse"
    assert fields["meaning_lit"] == "lit"
    assert fields["meaning_2"] == "steed"
    assert fields["lemma_1"] == "assa 1"
    assert not any(key.startswith("_") for key in fields)


def test_root_meaning_comes_back_under_its_column_name() -> None:
    root = DpdRoot(root="√gam", root_meaning="going")
    assert spanish.instance_fields(root)["root_meaning"] == "going"


def test_an_unmapped_object_behaves_like_the_old_filter() -> None:
    obj = SimpleNamespace(id=1, lemma_1="x", _private="hidden")
    assert spanish.instance_fields(obj) == {"id": 1, "lemma_1": "x"}


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db_session = sessionmaker(bind=engine)()
    db_session.add(DpdHeadword(id=1, lemma_1="assa 1", meaning_1="horse"))
    db_session.commit()
    yield db_session
    db_session.close()


def test_the_primary_user_saves_an_edited_meaning(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = object.__new__(DatabaseManager)
    manager.db_session = session
    manager._own_edits = None
    monkeypatch.setattr(manager, "mark_corpus_stale", lambda: None)
    monkeypatch.setattr(manager, "invalidate_relationship_detector", lambda: None)

    edited = DpdHeadword(id=1, lemma_1="assa 1", meaning_1="stallion")
    committed, error = manager.update_word_in_db(edited)

    assert committed, error
    session.expire_all()
    stored = session.get(DpdHeadword, 1)
    assert stored is not None
    with spanish.suppressed():
        assert stored.meaning_1 == "stallion"


def test_a_contributor_correction_keeps_the_meaning(tmp_path: Path) -> None:
    paths = Gui2Paths.for_user("alice", base_dir=tmp_path)
    paths.gui2_data_path.mkdir(parents=True, exist_ok=True)
    manager = CorrectionsManager(cast("ToolKit", SimpleNamespace(paths=paths)))

    manager.update_corrections(
        DpdHeadword(id=1, lemma_1="assa 1", meaning_1="stallion"), "comment"
    )

    entry = next(iter(manager.corrections_dict.values()))
    assert entry["meaning_1"] == "stallion"
