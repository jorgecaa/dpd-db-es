"""Tests for the attested-context fallback in exporter/webapp/toolkit.py.

The index runs against a synthetic TSV and the preloads are passed inline, so the fallback
is tested without a database or a running server.
"""

from pathlib import Path

import pytest

from exporter.webapp import toolkit
from tools.pali_context import PaliContextIndex

CANDIDATES = {"viññāṇa"}
FOLDED = {"vinnana": ["viññāṇa"]}


@pytest.fixture
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PaliContextIndex:
    path = tmp_path / "variant_context_index.tsv"
    path.write_text(
        "word\tbefore\tsource\tprovenance\n"
        "viññāṇa\tevaṃ\texample\tmahāpuṇṇamasuttaṃ\n"
        "viññāṇa\titi\texample\tvīmaṃsakasuttaṃ\n",
        encoding="utf-8",
    )
    built = PaliContextIndex(path=path)
    monkeypatch.setattr(toolkit, "_context_index", built)
    return built


def test_a_pair_with_no_neighbour_says_nothing(index: PaliContextIndex) -> None:
    assert toolkit.find_context_matches("viññana", CANDIDATES, FOLDED) == ""


def test_the_correction_is_reported_with_its_source(index: PaliContextIndex) -> None:
    html = toolkit.find_context_matches("evaṃ viññana", CANDIDATES, FOLDED)
    assert "viññāṇa" in html
    assert "mahāpuṇṇamasuttaṃ" in html
    assert "not found" in html


def test_an_attested_pair_is_reported_as_such(index: PaliContextIndex) -> None:
    html = toolkit.find_context_matches("evaṃ viññāṇa", CANDIDATES, FOLDED)
    assert "attested in the texts" in html


def test_a_neighbour_that_fits_nothing_says_nothing(index: PaliContextIndex) -> None:
    assert toolkit.find_context_matches("saddha viññana", CANDIDATES, FOLDED) == ""


def test_the_folded_index_is_consulted_with_a_folded_query() -> None:
    """`viññana` folds to `vinnana`, which is what finds `viññāṇa`."""
    candidates = toolkit._orthographic_candidates("viññana", set(), FOLDED)
    assert candidates == ["viññāṇa"]
