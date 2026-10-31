"""Unit tests for tools/pali_context.py.

The index runs against a synthetic TSV, so the lookup logic is tested without the live
`shared_data/variant_context_index.tsv`.
"""

from pathlib import Path

import pytest

from tools.pali_context import PaliContextIndex

# word, before, source, provenance — written in the canonical order the real file uses.
ROWS = [
    ("dhamma", "ariya", "example", "suttaṃ"),
    ("dhamma", "saddha", "variant", "CST/BV"),
    ("dhammo", "ayaṃ", "example", "suttaṃ"),
    ("viññāṇa", "evaṃ", "example", "mahāpuṇṇamasuttaṃ"),
    ("viññāṇa", "iti", "example", "suttaṃ"),
]


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> PaliContextIndex:
    path = tmp_path_factory.mktemp("context") / "variant_context_index.tsv"
    body = "\n".join("\t".join(row) for row in ROWS)
    path.write_text(f"word\tbefore\tsource\tprovenance\n{body}\n", encoding="utf-8")
    return PaliContextIndex(path=path)


def test_offsets_are_recorded(index: PaliContextIndex) -> None:
    assert index.word_count == 3


def test_neighbours_are_returned_sorted(index: PaliContextIndex) -> None:
    assert index.neighbours("dhamma") == ("ariya", "saddha")


def test_neighbours_of_an_unknown_word_are_empty(index: PaliContextIndex) -> None:
    assert index.neighbours("viññana") == ()


def test_attested_pair(index: PaliContextIndex) -> None:
    assert index.is_attested("viññāṇa", "evaṃ")
    assert not index.is_attested("viññāṇa", "ariya")


def test_readings_carry_their_source(index: PaliContextIndex) -> None:
    readings = index.readings("dhamma", "saddha")
    assert len(readings) == 1
    assert readings[0].source == "variant"
    assert readings[0].provenance == "CST/BV"
    assert "saddha dhamma" in str(readings[0])


def test_attested_among_picks_the_correction(index: PaliContextIndex) -> None:
    """The misspelling has no neighbour; its near spelling does."""
    candidates = ["viññāṇa", "viññāṇaṃ", "viññāna", "viññana"]
    assert index.attested_among("viñana", "evaṃ", candidates) == ["viññāṇa"]


def test_attested_among_excludes_the_word_itself(index: PaliContextIndex) -> None:
    assert index.attested_among("viññāṇa", "evaṃ", ["viññāṇa"]) == []


def test_attested_among_without_a_neighbour_match(index: PaliContextIndex) -> None:
    assert index.attested_among("viñana", "saddha", ["viññāṇa"]) == []


def test_missing_file_is_empty(tmp_path: Path) -> None:
    index = PaliContextIndex(path=tmp_path / "absent.tsv")
    assert index.word_count == 0
    assert index.neighbours("dhamma") == ()
    assert index.is_attested("dhamma", "ariya") is False
