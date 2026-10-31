"""Query the index of words that are attested with a given preceding word.

The morphology analyser answers "is this form licensed?". This answers a different and
often stronger question: "is this word, next to the word in front of it, actually in the
texts?". Pāḷi is full of pairs that only make sense together — the preceding word's ending
is what elides or assimilates at the junction — so the neighbour is real evidence and not
decoration.

The data comes from `shared_data/variant_context_index.tsv`
(`scripts/build/variant_context_index.py`), which harvests it from two places: the variant
readings of the four editions and the example sentences of the headwords.

    index = PaliContextIndex()
    index.neighbours("viññāṇa")                 # ('evaṃ', 'iti', 'uparujjhati')
    index.is_attested("viññāṇa", "evaṃ")        # True
    index.attested_among("viññana", "evaṃ", candidates)   # the ones that fit the neighbour
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tools.paths import ProjectPaths


@dataclass(frozen=True)
class Attestation:
    """One attested sighting of a word behind a given preceding word."""

    word: str
    before: str
    source: str
    provenance: str

    def __str__(self) -> str:
        origin = f" ({self.provenance})" if self.provenance else ""
        return f"{self.before} {self.word} — {self.source}{origin}"


@dataclass
class PaliContextIndex:
    """Word -> the block of lines that record its attested preceding words.

    The file is written in canonical order, word first, so only the byte offset of each
    word's block is held in memory (~95k entries). Reading the whole 167k rows into dicts
    of sets costs 134 MB, which is a lot to carry for a fallback that fires on a miss;
    offsets cost a fraction of that and each query reads a few kilobytes.
    """

    path: Path | None = None
    _offsets: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.load()

    @property
    def _target(self) -> Path:
        return self.path or ProjectPaths().variant_context_index_path

    def load(self) -> None:
        """Record where each word's block starts. One pass, offsets only.

        Read as bytes with the offsets counted by hand: `tell()` on a text handle disables
        the read-ahead buffer and turned this scan into ten seconds.
        """
        target = self._target
        if not target.exists():
            return

        with target.open("rb") as handle:
            offset = len(handle.readline())  # the header's bytes
            for raw in handle:
                word = raw.split(b"\t", 1)[0].decode("utf-8")
                if word and word not in self._offsets:
                    self._offsets[word] = offset
                offset += len(raw)

    @property
    def word_count(self) -> int:
        return len(self._offsets)

    def _block(self, word: str) -> list[list[str]]:
        """Every row filed under `word`."""
        offset = self._offsets.get(word)
        if offset is None:
            return []

        rows: list[list[str]] = []
        with self._target.open("rb") as handle:
            handle.seek(offset)
            for raw in handle:
                parts = raw.decode("utf-8").rstrip("\n").split("\t")
                if len(parts) != 4 or parts[0] != word:
                    break
                rows.append(parts)
        return rows

    def neighbours(self, word: str) -> tuple[str, ...]:
        """Every word attested immediately before `word`, in Pāḷi sort order."""
        from tools.pali_sort_key import pali_list_sorter

        return tuple(pali_list_sorter([row[1] for row in self._block(word)]))

    def readings(self, word: str, before: str) -> tuple[Attestation, ...]:
        """Where `before word` is attested, and in which source."""
        return tuple(
            Attestation(word=word, before=before, source=row[2], provenance=row[3])
            for row in self._block(word)
            if row[1] == before
        )

    def is_attested(self, word: str, before: str) -> bool:
        return any(row[1] == before for row in self._block(word))

    def attested_among(
        self, word: str, before: str, candidates: list[str] | tuple[str, ...]
    ) -> list[str]:
        """Which of `candidates` the texts attest behind this same neighbour.

        This is what turns an orthographic suggestion into a corpus-backed correction: the
        misspelling has no neighbour at all, while one of its near spellings does.
        """
        return [
            candidate
            for candidate in candidates
            if candidate != word and self.is_attested(candidate, before)
        ]
