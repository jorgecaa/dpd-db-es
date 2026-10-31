"""Decide whether a word is Pāḷi, and say on what ground — in either direction.

Three sources, each for its own thing, and only one of them can say no:

    the dictionary      the paradigm layer, the citation forms, the indeclinables
    the corpus          the lookup index (CST, BJT, SYA, MST), plus any curated list
                        added later — today the PTS Saṃyutta Nikāya IV
    the syllables       `abugida`'s phonotactics (Geiger §5, Oberlies §§15-16)

Before the syllables judge the spelling, the niggahita is read the way another edition
writes it: `suciṃdhara` is `sucindhara` (the dictionary's own construction is
`suciṃ + dhara`) and `acchariyañ` is `acchariyaṃ`. The reverse holds too — `kimsambhava`
is `kiṃsambhava`, because `s` has no nasal of its class — and both are the same difference:
a convention, not a rule of the language. Judging the first as a rule called 661 of 1.688
real PTS spellings unknown; the second recovers one of the 490 that remain (`samyojanaṃ`
for `saṃyojanaṃ`), because what is left is the vocabulary of a fifth witness and not its
notation.

The answer is one: **Pāḷi** or **not Pāḷi**. Under it, the ground it stands on:

    known       the dictionary analyses it
    attested    a corpus has it, and nothing analyses it
    variant     a known word in another spelling or notation
    possible    nothing has it
    no_word     the spelling cannot be derived from the word's own derivation
    not_pali    the syllable structure is not Pāḷi

The first three answer yes; the last three answer no. A syllable rejection decides only
when the dictionary and the corpus are both silent, because the layer judges the spelling
and is not always right about a word it has never seen: `etad` ends in a consonant and is
the `nt nom sg` of `eta`, and `ahud eva kaṅkhā` is `ahu` with the enclitic `d`.

Each verdict also carries a decimal, `Verdict.score`, which puts the six grounds on one
order so a page of words can be sorted: 1,00 known, 0,90 attested, 0,75 variant, 0,50
possible, 0,25 no_word, 0,00 not_pali. It is an **ordinal** score, not a probability — the
same caveat as `possible` in prose — and it depends on the basis alone, so the fast path and
`--deep` cannot disagree about it. `-b` prints it first, one line per word.

**What it does not decide.** `possible` is not "exists": a Pāḷi word outside the sources, on
a stem the dictionary does not have, looks exactly like it. And `no_word` is a statement
about the spelling's derivation, not about the word's meaning in context — the derivation is
checked letter by letter against the word's own `construction`, and meaning is not.

Use it from the console:

    uv run python tools/pali_verdict.py viññana adukkham
    uv run python tools/pali_verdict.py -b ananuyuñjanto ivā mnih
    uv run python tools/pali_verdict.py -f pagina.txt --notation pts
    cat pagina.txt | uv run python tools/pali_verdict.py -f - -b
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unidecode import unidecode

# The repository's own packages are imported absolutely, so this file has to be run with
# the project root on the path. Run from inside `tools/`, the failure is a bare
# `No module named 'db'`, which says nothing about what to do; this says it.
try:
    from tools.pali_alphabet import pali_alphabet
    from tools.paths import ProjectPaths
except ModuleNotFoundError as exc:
    if (exc.name or "").split(".")[0] not in {"db", "tools"}:
        raise  # a genuine missing dependency is not a wrong working directory
    raise SystemExit(
        "run this from the project root, in the project's environment:\n"
        "    uv run python tools/pali_verdict.py añña\n"
        "    just verdict añña\n"
        "(from inside `tools/`, use `uv run python -m tools.pali_verdict añña`)"
    ) from exc

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tools.pali_context import PaliContextIndex
    from tools.pali_morphology import Analysis, PaliMorphologyAnalyzer


def _orm() -> tuple[Any, Any]:
    """The ORM classes, imported the first time they are needed.

    SQLAlchemy and the 60-column model cost 1,2 s and 64 MB to import, which is more than
    the whole budget of a console answer. The yes/no never needs them: the index answers
    most words, and it is read with `sqlite3` when a session was not handed in.
    """
    from db.models import DpdHeadword, Lookup

    return DpdHeadword, Lookup


#: The `lookup` columns the verdict reads, in the order `CorpusRow` takes them. Only some
#: of them are evidence that the index carries the word; see `_is_entry`.
_CORPUS_COLUMNS = (
    "headwords",
    "grammar",
    "deconstructor",
    "roots",
    "epd",
    "variant",
    "spelling",
    "see",
    "help",
    "abbrev",
    "abbrev_other",
)


@dataclass(frozen=True)
class CorpusRow:
    """What the index holds for a key — the fields the verdict reads."""

    headwords: str = ""
    grammar: str = ""
    deconstructor: str = ""
    roots: str = ""
    epd: str = ""
    variant: str = ""
    spelling: str = ""
    see: str = ""
    help: str = ""
    abbrev: str = ""
    abbrev_other: str = ""


def _is_entry(row: CorpusRow) -> bool:
    """Does the index carry the word itself, or only a list that points at it?

    `spelling`, `see` and `help` are built from correction and help lists, and
    `db/lookup/spelling_mistakes.py` inserts a row for every mistake in
    `shared_data/deconstructor/spelling_mistakes.tsv`. A row carrying only `spelling` is
    therefore the blacklist quoting the word back at itself, not the corpus carrying it:
    counting those rows made the verdict call 518 of the 543 known misspellings "Pāḷi — a
    corpus has it", and 492 of those rows existed for no other reason. `epd` is the English
    index (79.596 keys pointing at Pāḷi words), so it is not evidence either.
    """
    return any(
        (
            row.headwords,
            row.grammar,
            row.deconstructor,
            row.roots,
            row.variant,
            row.abbrev,
            row.abbrev_other,
        )
    )


def sqlite_corpus(db_path: Path) -> Callable[[str], CorpusRow | None]:
    """Read the index directly, without the ORM: 0,03 s instead of 1,2 s."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    columns = ", ".join(_CORPUS_COLUMNS)

    def reader(word: str) -> CorpusRow | None:
        row = connection.execute(
            f"select {columns} from lookup where lookup_key = ?", (word,)
        ).fetchone()
        return CorpusRow(*row) if row else None

    return reader


# Every letter the Pāḷi alphabet is written with. A word using anything else is not Pāḷi,
# whatever else is true of it.
#
# `ṁ` and `ŋ` are the other niggahīta spellings. `tools/pali_alphabet` has only `ṃ`, the
# form the dictionary is normalised to, so a check built on it alone called `arahaṁ` — which
# the repo's own Pāḷi chanting books write 4.633 times — "not Pāḷi letters". The repository
# already names them: `db_tests/single/test_allowable_characters.py` has
# `niggahitas = ["ṁ", "ŋ"]`.
_PALI_CHARACTERS = frozenset(
    character for letter in pali_alphabet for character in letter
) | {"ṁ", "ŋ"}

# The syllable layer, from `abugida` (Geiger §5, Oberlies §§15-16; SPEC.md §8). It is the
# library's own measured implementation, not a re-derivation: an inventory of clusters
# induced from the corpus confused the system with its accidents — it accepted `mnih`
# (because `mn` occurs at compound junctions) and rejected real forms (`khalvapi`,
# `virodyapi`) whose clusters no lemma happens to contain.
_is_possible_syllables: Callable[[str], bool] | None
try:
    # pyrefly: ignore[missing-import]  # optional local library; absent is handled
    from abugida.phonotactics import (  # pyright: ignore[reportMissingImports]  # optional local library; absent is handled
        is_possible as _possible_syllables,
    )

    _is_possible_syllables = _possible_syllables
except ImportError:  # without it the verdict says so instead of guessing
    _is_possible_syllables = None

#: A word as the console reader understands it: letters and their diacritics.
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# The niggahita spellings are notational variants of one phoneme.
_NIGGAHITA = str.maketrans({"ṁ": "ṃ", "ŋ": "ṃ"})

# Which nasal consonant the language writes before which consonant. A nasal that matches the
# consonant after it is the ordinary assimilation (`sucindhara`, `sambhavati`); one that does
# not is a niggahita somebody wrote with the wrong letter (`kimsambhava`, where `s` has no
# nasal of its class and the dictionary writes `kiṃsambhava`).
_CLASS_NASAL = {
    "k": "ṅ",
    "kh": "ṅ",
    "g": "ṅ",
    "gh": "ṅ",
    "c": "ñ",
    "ch": "ñ",
    "j": "ñ",
    "jh": "ñ",
    "ṭ": "ṇ",
    "ṭh": "ṇ",
    "ḍ": "ṇ",
    "ḍh": "ṇ",
    "t": "n",
    "th": "n",
    "d": "n",
    "dh": "n",
    "p": "m",
    "ph": "m",
    "b": "m",
    "bh": "m",
}

#: Consonants written with two letters, so the nasal before them is read from both.
_CONSONANT_DIGRAPHS = frozenset(name for name in _CLASS_NASAL if len(name) == 2)

#: The nasals an edition might have written for the niggahita.
_NASALS = frozenset({"m", "n", "ñ", "ṅ", "ṇ", "ṉ"})

#: The vowels. The niggahita is written before a consonant or at the end of a word, never
#: before one of these, so a nasal between two vowels is the consonant and not a notation
#: variant (`nama` must not be read as `naṃa`).
_VOWELS = frozenset("aāiīuūeo")

#: The grounds that answer yes: a source has the word, in one spelling or another.
_PALI_BASES = frozenset({"known", "attested", "variant"})

# The syllable rules are stated for one notation: the one the dictionary is written in,
# where the niggahita is `ṃ` and a word ends in a vowel or `ṃ`. Judging another notation
# with them is what manufactures false negatives — 623 of 3.179 real Pāḷi words from the
# repository's own chanting books (`adukkham`, `ahesun`, `amhākam`) are PTS spelling, not
# errors, and the syllable layer rejects every one of them for ending in a consonant.
# `foureditions/pali_norm.py` is the measured translator between notations (it even needs
# the PTS volume: its marks differ per volume). Until that is available here, a caller with
# another notation has to say so, and the shape is then not judged at all.
_SYLLABLE_NOTATIONS = frozenset({"dpd"})

# Mirrors `DpdHeadword.lemma_clean` and `exporter/webapp/preloads.py::clean_lemma`.
_SENSE_NUMBER = re.compile(r" \d.*$")

# `viññāṇakkhandha` typed as `vinnanakhandha` differs by diacritics *and* by a lost
# gemination, and folding diacritics alone does not bridge that. Collapsing doubled letters
# does: both ends come out as `vinanakhandha`.
_DOUBLED = re.compile(r"(.)\1+")

# The bases, strongest first. A verdict reports the strongest one that holds.
_STRENGTH = (
    "attested",
    "inflected",
    "citation",
    "indeclinable",
    "derived",
    "compound-only",
    "none",
)


@dataclass(frozen=True)
class Reason:
    """One piece of evidence, and what it is evidence of."""

    kind: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}"


#: The decimal each ground is worth. Ordinal, not probabilistic — see `Verdict.score`.
_SCORES: dict[str, float] = {
    "known": 1.00,
    "attested": 0.90,
    "variant": 0.75,
    "possible": 0.50,
    "no_word": 0.25,
    "not_pali": 0.00,
}


@dataclass(frozen=True)
class Verdict:
    word: str
    is_pali: bool
    basis: str
    reasons: tuple[Reason, ...] = ()
    corrections: tuple[str, ...] = ()

    @property
    def score(self) -> float:
        """Where the verdict stands on a decimal scale of evidence, 0,00 to 1,00.

        This is an **ordinal** score and not a probability: it orders the six grounds the
        verdict can stand on, and the gaps between them are a ranking choice, not a
        measurement. The order is the one the evidence itself has —

            1,00  the dictionary analyses the word
            0,90  a witness carries it, and nothing analyses it
            0,75  it is a known word in another spelling or notation
            0,50  nothing has it, and nothing rules it out either
            0,25  the spelling cannot be derived from the word it belongs to
            0,00  the shape is not Pāḷi

        — so a caller can sort a page of words by how much stands behind each one. It is
        deliberately a function of `basis` alone and nothing else: the fast path and the
        deep path reach the same basis, so the score cannot depend on which one answered.
        Turning it into a probability would need a labelled benchmark and a stated mix of
        real words to errors, because no prior is identifiable from the data alone; until
        there is one, a decimal that looks like a probability would be a lie with a
        decimal point.
        """
        return _SCORES.get(self.basis, 0.0)

    def of_kind(self, kind: str) -> tuple[Reason, ...]:
        return tuple(reason for reason in self.reasons if reason.kind == kind)

    def headline(self) -> str:
        """The answer in one line: Pāḷi or not, and on what ground."""
        grounds = {
            "known": "the dictionary analyses it",
            "attested": "a corpus has it",
            "variant": "a known word in another spelling or notation",
            "possible": "nothing has it",
            "no_word": "the spelling cannot be derived",
            "not_pali": "the syllable structure is not Pāḷi",
        }
        answer = "Pāḷi" if self.is_pali else "not Pāḷi"
        return f"{self.word}: {answer} — {grounds.get(self.basis, self.basis)}"

    def explain(self) -> str:
        """The answer and its ground, as text.

        The question is one: is this Pāḷi or not. Three of the grounds answer yes — the
        dictionary analyses it, the corpus has it, or it is a known word in another spelling
        — and three answer no: the syllable structure is impossible, the spelling cannot be
        derived, or nothing has it. `possible` is the last of those, and it keeps the one
        thing that must not be lost: nothing rules the word out. It is not a hedge, it is
        the difference between "this is wrong" and "this is not in what I have read".
        """
        lines = [f"[{self.score:.2f}] {self.headline()}"]
        for reason in self.reasons:
            lines.append(f"    {reason}")
        if self.corrections:
            lines.append(f"    licensed near spellings: {', '.join(self.corrections)}")
        if self.basis == "possible":
            lines.append(
                "    note: nothing here rules the word out — a Pāḷi word outside the four "
                "editions, built on a stem the dictionary does not have, looks exactly "
                "like this"
            )
        return "\n".join(lines)


@dataclass
class PaliVerdict:
    """The verdict, with its dependencies loaded on first use."""

    db_session: Session | None = None
    analyzer: PaliMorphologyAnalyzer | None = None
    context: PaliContextIndex | None = None
    corpus_reader: Callable[[str], CorpusRow | None] | None = None
    extra_wordlist_path: Path | None = None
    sources_path: Path | None = None
    _sidecar: sqlite3.Connection | None = field(default=None, repr=False, compare=False)
    _sidecar_tried: bool = field(default=False, repr=False, compare=False)
    _folded: dict[str, list[str]] = field(default_factory=dict)
    _loose: dict[str, list[str]] = field(default_factory=dict)
    _extra: set[str] | None = None

    def _extra_words(self) -> set[str]:
        """Curated PTS vocabulary from outside the four editions, read once.

        Today it holds the PTS Saṃyutta Nikāya IV (Feer, revised by Jackson 2014), 6.767
        words of which 1.976 are not in the dictionary's index at all. **For a word from
        that volume this is not independent corroboration**: the edition is both the
        question and part of the answer.
        """
        if self._extra is None:
            paths = ProjectPaths()
            path = self.extra_wordlist_path or paths.pts_sn4_wordlist_path
            words: set[str] = set()
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines()[1:]:
                    word = line.split("\t")[0].strip().lower()
                    if word:
                        words.add(word)
            # The edition's words include the editor's: 88 of the 7.459 are English words,
            # sigla and Roman numerals, marked by `foureditions`' own rule in this file. No
            # one asks a Pāḷi dictionary about `and`, but if they do, the answer must not be
            # that the Saṃyutta Nikāya has it.
            noise = path.with_name("pts_sn4_noise.tsv")
            if noise.exists():
                for line in noise.read_text(encoding="utf-8").splitlines()[1:]:
                    words.discard(line.split("\t")[0].strip().lower())
            self._extra = words
        return self._extra

    def _sources(self) -> sqlite3.Connection | None:
        """The precomputed lookups, opened once, or None when the file is not there.

        `shared_data/verdict_sources.db` holds the folded spellings and the derivations
        ready to be queried. Built in memory instead, the first is 4 s and 70 MB of
        `unidecode` and the second is the ORM at 2,38 s and 252 MB — measured. Without the
        file everything still works, by the slow road.
        """
        if self._sidecar is None and not self._sidecar_tried:
            self._sidecar_tried = True
            path = self.sources_path or ProjectPaths().verdict_sources_path
            if path.exists():
                self._sidecar = sqlite3.connect(
                    f"file:{path}?mode=ro&immutable=1", uri=True
                )
        return self._sidecar

    def _lookup_row(self, word: str) -> CorpusRow | None:
        """The index row for a key, from the reader if there is one and the ORM if not."""
        if self.corpus_reader is not None:
            return self.corpus_reader(word)
        if self.db_session is None:
            return None
        _headword, lookup = _orm()
        columns = [getattr(lookup, name) for name in _CORPUS_COLUMNS]
        row = self.db_session.query(*columns).filter(lookup.lookup_key == word).first()
        return CorpusRow(*row) if row else None

    def _analyzer(self) -> PaliMorphologyAnalyzer:
        if self.analyzer is None:
            if self.db_session is None:
                raise RuntimeError(
                    "the morphology layer needs a database session; this verdict was "
                    "built for the fast path, which answers from the index and the "
                    "syllables alone"
                )
            from tools.pali_morphology import PaliMorphologyAnalyzer

            self.analyzer = PaliMorphologyAnalyzer(self.db_session)
        return self.analyzer

    def _context(self) -> PaliContextIndex:
        if self.context is None:
            from tools.pali_context import PaliContextIndex

            self.context = PaliContextIndex()
        return self.context

    def _folded_index(self) -> dict[str, list[str]]:
        """Folded spelling -> the spellings that fold to it, built once when needed.

        Lemmas alone are not enough: `jotipalo` folds to nothing, because the form the texts
        carry is `jotipālo`, an inflected form of `jotipāla`. So the 458.665 corpus keys that
        have a headword behind them are in here too (1,6 s and about 70 MB, built only when a
        spelling has to be corrected).
        """
        if self.db_session is None:
            return {}
        if not self._folded:
            headword, lookup = _orm()
            seen: dict[str, set[str]] = {}
            for (lemma_1,) in self.db_session.query(headword.lemma_1).all():
                word = _SENSE_NUMBER.sub("", lemma_1)
                if " " not in word:
                    seen.setdefault(unidecode(word), set()).add(word)
            rows = self.db_session.query(lookup.lookup_key).filter(
                lookup.headwords != ""
            )
            for (key,) in rows:
                if " " not in key and not any(c.isupper() for c in key):
                    seen.setdefault(unidecode(key), set()).add(key)
            self._folded = {key: sorted(value) for key, value in seen.items()}
        return self._folded

    def _lemmas_of(self, spelling: str) -> list[str]:
        """The lemmas an inflected spelling belongs to, the ORM-free way first."""
        sidecar = self._sources()
        if sidecar is not None:
            rows = sidecar.execute(
                "select lemma from headword where spelling = ?", (spelling,)
            ).fetchall()
            if rows:
                return [lemma for (lemma,) in rows]
        row = self._lookup_row(spelling)
        if row is not None:
            return self._lemmas(row.headwords)
        return []

    def _lemma_of(self, spelling: str) -> str:
        """The lemma an inflected spelling belongs to, or the spelling itself."""
        lemmas = self._lemmas_of(spelling)
        return lemmas[0] if lemmas else spelling

    def _loose_index(self) -> dict[str, list[str]]:
        """Folded spelling with doubled letters collapsed, built from the same words."""
        if not self._loose:
            seen: dict[str, set[str]] = {}
            for words in self._folded_index().values():
                for word in words:
                    seen.setdefault(loose_fold(word), set()).add(word)
            self._loose = {key: sorted(value) for key, value in seen.items()}
        return self._loose

    def _matched_loosely(self, word: str) -> bool:
        """True when only the gemination-insensitive fold found the near spelling."""
        sidecar = self._sources()
        if sidecar is not None:
            exact = sidecar.execute(
                "select 1 from folded where folded = ? limit 1", (unidecode(word),)
            ).fetchone()
            loose = sidecar.execute(
                "select 1 from folded where loose = ? limit 1", (loose_fold(word),)
            ).fetchone()
            return not exact and bool(loose)
        return not self._folded_index().get(unidecode(word)) and bool(
            self._loose_index().get(loose_fold(word))
        )

    def check(
        self, word: str, with_compound: bool = True, notation: str = "dpd"
    ) -> Verdict:
        """Is this Pāḷi, and on what ground.

        Case is not part of the answer: every one of the 89.587 lemmas and every Pāḷi key in
        the index is lowercase, while the 20.585 keys that are not are English phrases. So
        the word is folded once here and the spelling the caller used is kept for the answer
        — `Jotipālo` and `jotipālo` are the same word, and `Jotipāla` against `jotipāla` is
        not a lost letter.
        """
        shown, word = word, word.lower()
        reasons: list[Reason] = []
        bases: set[str] = set()

        # Cheapest first, and only then the expensive layer. The order is not an
        # optimisation of the same computation: `lookup` carries every inflected form the
        # dictionary generates (1.282.512 keys, built from the inflections), so a word the
        # corpus does not have is not a form of a headword either. Only what is left after
        # these two questions is worth loading the analyser for — 2 s and 250 MB.
        reasons.extend(self._corpus_reasons(word, bases))
        reasons.extend(self._extra_corpus_reasons(word, bases))
        if "known" in bases or "attested" in bases:
            # A source has the word, and that settles it. The syllable layer judges the
            # spelling and it is not always right about a word it has never seen: `etad`
            # ends in a consonant and is the `nt nom sg` of `eta`, and `ahud eva kaṅkhā`
            # (SN IV 399) is `ahu` with the enclitic `d`. Whoever knows the word wins over
            # whoever judges the shape.
            return Verdict(
                word=shown,
                is_pali=True,
                basis="known" if "known" in bases else "attested",
                reasons=tuple(reasons),
            )

        # The niggahita is a convention before it is a rule: an edition that writes `n` for
        # `ṃ` has the same word, and the index has it one letter away. Asking here, before
        # the syllable layer judges the spelling, is what turns `suciṃdhara` into a
        # `variant` of `sucindhara` instead of a word nothing has.
        readings = self._notation_readings(word)
        if readings:
            reasons.append(
                Reason(
                    kind="notation",
                    detail=(
                        "the niggahīta written the way another edition writes it; the "
                        f"dictionary carries {', '.join(readings)}"
                    ),
                )
            )
            return Verdict(
                word=shown,
                is_pali=True,
                basis="variant",
                reasons=tuple(reasons),
                corrections=readings,
            )

        # The same convention the other way round: an edition writes a nasal consonant where
        # the dictionary writes the niggahita, which the language does whenever the next
        # consonant has no nasal of its own class (`kimsambhava`).
        medial = self._medial_nasal_readings(word)
        if medial:
            reasons.append(
                Reason(
                    kind="notation",
                    detail=(
                        "the niggahīta written as a nasal consonant before a consonant "
                        "with no nasal of its class; the dictionary carries "
                        f"{', '.join(medial)}"
                    ),
                )
            )
            return Verdict(
                word=shown,
                is_pali=True,
                basis="variant",
                reasons=tuple(reasons),
                corrections=medial,
            )

        rejected, syllable_reasons = self._syllable_check(word, notation)
        reasons.extend(syllable_reasons)
        if rejected:
            translated = self._niggahita_reading(word)
            if translated is None:
                # No near spelling is looked up here on purpose: the folded index holds
                # 458.665 forms, costs seconds and about 70 MB to build, and a word whose
                # syllables are not Pāḷi is not one letter away from being one. It is worth
                # building for `viññana`; it is not worth building for `kionl`.
                return Verdict(
                    word=shown,
                    is_pali=False,
                    basis="not_pali",
                    reasons=tuple(reasons),
                )
            inner = self.check(translated, with_compound=False)
            if inner.is_pali:
                return Verdict(
                    word=shown,
                    is_pali=True,
                    basis="variant",
                    reasons=(
                        Reason(
                            kind="notation",
                            detail=(
                                "ends in a consonant this notation writes for the "
                                f"niggahita; read as the dictionary writes it, "
                                f"{translated}, it is {inner.basis}"
                            ),
                        ),
                        *inner.reasons,
                    ),
                    corrections=(translated,),
                )
            # The notation is not what makes the word unknown: the syllable rejection is an
            # artefact of the spelling, so it is withdrawn and the word is judged on what is
            # left — which is nothing, if the other notation does not know it either. Not
            # Pāḷi, then, but for the reason that nothing has it, not that it is malformed.
            reasons.append(
                Reason(
                    kind="notation",
                    detail=(
                        "ends in a consonant this notation writes for the niggahita; read "
                        f"as the dictionary writes it, {translated}, it is {inner.basis} "
                        "there too"
                    ),
                )
            )
            bases.add("possible")

        reasons.extend(_cap(self._morphology_reasons(word, bases)))
        if "known" in bases:
            return Verdict(
                word=shown,
                is_pali=True,
                basis="known",
                reasons=tuple(reasons),
            )

        if with_compound:
            reasons.extend(self._compound_reasons(word, bases))

        # A spelling the texts do not carry is worth checking against the ones they do: in
        # ASCII, `viññana` and `viññāṇa` are the same string.
        corrections = self._corrections(word) if "attested" not in bases else ()
        if corrections:
            known = corrections[0]
            known_lemma = self._lemma_of(known)
            swapped = self._swapped_derivation(word, known, known_lemma)
            if swapped:
                # The spelling drops letters the word's own derivation supplies, so it
                # cannot be built from the root: `viññana` for `viññāṇa` writes `a` for the
                # root's `ā` and `n` for the suffix's `ṇ`. That is not a variant of a word,
                # it is a spelling with no morphology behind it.
                lemma_shown = _SENSE_NUMBER.sub("", known_lemma)
                reasons.append(
                    Reason(
                        kind="derivation",
                        detail=(
                            f"the derivation of {lemma_shown} is "
                            f"«{self._derivation_of(known_lemma)}», which supplies "
                            f"{' '.join(swapped)}; {word} cannot be built from it"
                        ),
                    )
                )
                return Verdict(
                    word=shown,
                    is_pali=False,
                    basis="no_word",
                    reasons=tuple(reasons),
                    corrections=corrections,
                )
            difference = self._difference(word, known)
            derivation = self._derivation_of(known_lemma)
            if not derivation:
                # There is nothing to check the difference against: a particle such as
                # `iva` records no derivation, so a long vowel in `ivā` is neither supplied
                # nor excluded by anything. The corpus does not carry the form either. Say
                # that, rather than calling it a spelling of a word and leaving it there.
                reasons.append(
                    Reason(
                        kind="near-spelling",
                        detail=(
                            f"differs from {known} in {difference}, and {known} records no "
                            "derivation to check that against; the corpus does not carry "
                            "this form either, so nothing here decides whether it is a "
                            "variant or an error"
                        ),
                    )
                )
                reasons.extend(self._letters_reasons(word) or self._why_not(word))
                return Verdict(
                    word=shown,
                    is_pali=False,
                    basis="possible",
                    reasons=tuple(reasons),
                    corrections=corrections,
                )
            reasons.append(
                Reason(
                    kind="variant",
                    detail=(
                        f"differs from {known} in {difference}, which its derivation "
                        f"«{derivation}» does not supply; the shape is Pāḷi and the letters "
                        "it changes are not the derived ones"
                    ),
                )
            )
            return Verdict(
                word=shown,
                is_pali=True,
                basis="variant",
                reasons=tuple(reasons),
                corrections=corrections,
            )

        # Only now, with the corpus silent, is it true that the corpus is silent — saying it
        # of an attested word was a contradiction in the same block of reasons.
        if "possible" not in bases:
            reasons.extend(self._letters_reasons(word) or self._why_not(word))
        if self.db_session is None:
            reasons.append(
                Reason(
                    kind="fast",
                    detail=(
                        "answered from the index, the syllables and the precomputed "
                        "derivations; the analyser was not loaded, so the paradigm and "
                        "compound readings are not shown — they add evidence, and the "
                        "verdict measured the same either way (315 of 315 words)"
                    ),
                )
            )
        return Verdict(
            word=shown,
            is_pali=False,
            basis="possible",
            reasons=tuple(reasons),
        )

    def _derivation_of(self, word: str) -> str:
        """The morphological explanation the dictionary records for a word."""
        sidecar = self._sources()
        if sidecar is not None:
            row = sidecar.execute(
                "select construction from derivation "
                "where lemma = ? or lemma like ? || ' %' limit 1",
                (word, word),
            ).fetchone()
            return (row[0] if row else "") or ""
        if self.db_session is None:
            return ""
        headword, _lookup = _orm()
        row = (
            self.db_session.query(headword.construction, headword.root_key)
            .filter((headword.lemma_1 == word) | (headword.lemma_1.like(f"{word} %")))
            .first()
        )
        if row is None:
            return ""
        return row[0] or ""

    def _derived_characters(self, word: str) -> set[str]:
        """The letters a word's own derivation supplies — root, prefix, suffix.

        These are the letters a spelling may not swap: `viññāṇa` is `vi + √ñā + aṇa`, so
        its `ā` comes from the root and its `ṇ` from the suffix, and a spelling that writes
        `a` and `n` cannot be built from them. A case ending is not in here — `ṃ` is not
        something `adukkha` is derived from, it is something it is inflected with.
        """
        explanation = self._derivation_of(word)
        if not explanation:
            return set()
        return {character for character in explanation if character.isalpha()}

    def _difference(self, word: str, known: str) -> str:
        """What this spelling does differently, letter by letter: `a for ā`, `+ k`.

        Naming the difference is the difference between a verdict and a shrug: "differs in
        diacritics" hides whether a long vowel, a retroflex or a whole letter is at stake.
        """
        import difflib

        matcher = difflib.SequenceMatcher(None, word, known)
        parts: list[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            theirs, ours = word[i1:i2], known[j1:j2]
            if tag == "replace":
                parts.append(f"{theirs or '∅'} for {ours}")
            elif tag == "insert":
                parts.append(f"missing {ours}")
            else:
                parts.append(f"extra {theirs}")
        return ", ".join(parts) or "nothing"

    def _swapped_derivation(
        self, word: str, known: str, lemma: str = ""
    ) -> tuple[str, ...] | None:
        """Letters of the known spelling this one loses that the lemma's derivation supplies."""
        import difflib

        matcher = difflib.SequenceMatcher(None, word, known)
        swapped: list[str] = []
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            # `replace` and `insert` are the blocks where the known word holds letters the
            # spelling does not reproduce (`delete` is the spelling carrying extra ones):
            # `viññana` reaches `viññāṇa` by inserting `āṇ`, which is exactly what it lost.
            if tag in ("replace", "insert"):
                swapped.extend(
                    character for character in known[j1:j2] if character.isalpha()
                )
        derived = self._derived_characters(lemma or known)
        lost = tuple(dict.fromkeys(c for c in swapped if c in derived))
        return lost or None

    def _notation_readings(self, word: str) -> tuple[str, ...]:
        """The spellings in the dictionary's notation that the index carries.

        The niggahita is written `ṃ` by the dictionary and `m`, `n`, `ṅ`, `ñ` or `ṉ` by the
        editions, and the choice is a convention rather than a rule of the language:
        `suciṃdhara` is `sucindhara` — the dictionary records the construction as
        `suciṃ + dhara` and the text prints `sucin'dharā` — and `acchariyañ` is
        `acchariyaṃ`. `nasal_variants` already computed the candidates; what was missing
        was asking the index about them before concluding that nothing has the word. It
        only fires when the spelling as written is not in the index, so it can turn a no
        into a yes but never the other way.

        Every candidate the index carries is returned, shortest first and alphabetical on
        ties, because more than one can be a real word: `saṃghañ` is both `saṃghaṃ` and
        `saṅghaṃ`, and naming only one of them would be arbitrary.
        """
        spelled = word.translate(_NIGGAHITA)
        candidates = [spelled] if spelled != word else []
        candidates.extend(sorted(nasal_variants(word), key=lambda c: (len(c), c)))
        readings: list[str] = []
        for candidate in candidates:
            row = self._lookup_row(candidate)
            if row is not None and _is_entry(row) and candidate not in readings:
                readings.append(candidate)
        return tuple(readings)

    def _medial_nasal_readings(self, word: str) -> tuple[str, ...]:
        """The same word with a medial class nasal read as the niggahita.

        The other direction of the same convention: an edition writes `m` where the
        dictionary writes `ṃ`, which happens whenever the next consonant has no nasal of
        its own class — `kimsambhava` for `kiṃsambhava`, because `s` has no class nasal.
        Where the written nasal *is* the class nasal of the consonant after it, it is the
        ordinary assimilation and not notation (`sucindhara`, `sambhavati`), and `h` is left
        alone because `taṇhā` and `brahmā` are licensed clusters.

        The candidate must be a spelling the index carries, so this cannot invent a word:
        it only names one. Measured on the PTS SN IV hold-out, where 490 of the 1.688 words
        remain unknown after the other notation rule, this recovers one (`samyojanaṃ` for
        `saṃyojanaṃ`) — the rest is vocabulary of a fifth witness, not notation. It earns
        its place less for that than for being the other half of the same convention, the
        one that makes `kimsambhava` a spelling of `kiṃsambhava`.
        """
        readings: list[str] = []
        for index, letter in enumerate(word):
            if letter not in _NASALS or index + 1 >= len(word):
                continue  # word-final: `nasal_variants` has already had these
            following = word[index + 1]
            if following in _VOWELS:
                continue  # a nasal between vowels is the consonant, not the niggahita
            if following in _NASALS or following == "h":
                continue  # a geminate, or `taṇhā`
            pair = word[index + 1 : index + 3]
            consonant = pair if pair in _CONSONANT_DIGRAPHS else following
            if _CLASS_NASAL.get(consonant) == letter:
                continue  # the ordinary assimilation, not notation
            candidate = word[:index] + "ṃ" + word[index + 1 :]
            row = self._lookup_row(candidate)
            if row is not None and _is_entry(row) and candidate not in readings:
                readings.append(candidate)
        return tuple(readings)

    def _niggahita_reading(self, word: str) -> str | None:
        """The same word with the final niggahita written `ṃ`, if that makes it possible.

        This is a spelling translation, not a rule of the language: it exists because the
        PTS and other conventions write the niggahita as `m`, `n`, `ṅ` or `ñ` where the
        dictionary writes `ṃ`, and judging those spellings with the syllable rules produced
        623 false "not Pāḷi" verdicts on real Pāḷi before it was measured.
        """
        if _is_possible_syllables is None:
            return None
        candidates = sorted(nasal_variants(word), key=lambda c: (len(c), c))
        for candidate in candidates:
            if _is_possible_syllables(candidate.translate(_NIGGAHITA)):
                return candidate
        return None

    # ------------------------------------------------------------------ evidence

    def _extra_corpus_reasons(self, word: str, bases: set[str]) -> list[Reason]:
        """Curated vocabulary from outside the four editions the dictionary is built from."""
        words = self._extra_words()
        matches = sorted(nasal_variants(word) & words)
        found = word if word in words else (matches[0] if matches else "")
        if not found:
            return []
        bases.add("attested")
        detail = (
            "in the PTS Saṃyutta Nikāya IV (Feer, revised by Jackson 2014), which is "
            "outside the four editions the dictionary is built from"
        )
        if found != word:
            detail += f" — as {found}, the niggahīta written its way"
        return [Reason(kind="attested", detail=detail)]

    def _corpus_reasons(self, word: str, bases: set[str]) -> list[Reason]:
        row = self._lookup_row(word)
        if row is None or not _is_entry(row):
            return []

        lemmas = self._lemmas_of(word)
        # A row with headwords or a grammatical analysis behind it is the dictionary
        # knowing the word; a row of the index alone only means the corpus carries the form.
        analysed = bool(lemmas) or bool(row.grammar)
        bases.add("known" if analysed else "attested")
        detail = "in the corpus index"
        if lemmas:
            detail += f", as {', '.join(lemmas[:4])}"
        if row.grammar:
            detail += " (with grammatical analysis of its forms)"
        if not analysed:
            detail += ", with nothing analysing it"
        return [Reason(kind="attested", detail=detail)]

    def _morphology_reasons(self, word: str, bases: set[str]) -> list[Reason]:
        if self.db_session is None:
            # The fast path has no analyser, and it does not need one: every inflected form
            # the dictionary generates is an index key (measured: 89.587 of 89.587 citation
            # forms, and the index is built from the inflections), so the corpus layer has
            # already answered whatever this one could.
            return []
        analyzer = self._analyzer()
        direct = analyzer.analyze(word, strict=True, particles=False, derivation=False)
        reasons = [
            Reason(kind=_kind_of(analysis), detail=_describe(analysis))
            for analysis in direct
        ]
        if direct:
            # A paradigm reading, a citation form or an indeclinable: the dictionary knows
            # the word, which on the four-level scale is `known`.
            bases.add("known")

        for analysis in analyzer.analyze(
            word, strict=True, particles=False, derivation=True
        ):
            if not analysis.suffix:
                continue
            reasons.append(
                Reason(
                    kind="derived",
                    detail=(
                        f"{analysis.derivation_base} + {analysis.suffix} -> "
                        f"{analysis.stem} + {analysis.pattern} ({analysis.grammar}); "
                        "a licensed formation, not an attested word"
                    ),
                )
            )

        for analysis in analyzer.analyze(
            word, strict=True, particles=True, derivation=False
        ):
            if not analysis.particle:
                continue
            reasons.append(
                Reason(
                    kind="particle",
                    detail=(
                        f"{analysis.base} + {analysis.particle} joined by sandhi -> "
                        f"{analysis.grammar}"
                    ),
                )
            )
        return reasons

    def _compound_reasons(self, word: str, bases: set[str]) -> list[Reason]:
        if self.db_session is None:
            return []  # the deconstructor is part of the analyser, and grants nothing
        compounds = self._analyzer().analyze_compound(word, limit=3)
        if not compounds:
            return []
        best = compounds[0]
        return [
            Reason(
                kind="compound",
                detail=(
                    f"reads as {' + '.join(best.parts)}; a decomposition is not evidence "
                    "of a word, because any string splits into dictionary words"
                ),
            )
        ]

    def _letters_reasons(self, word: str) -> list[Reason]:
        """Whether the word is written in Pāḷi letters at all.

        This is the one categorical negative the verdict can make: a word in Mon script, or
        in a romanisation that uses letters Pāḷi does not have, is not Pāḷi however it is
        spelled. Everything else is only "not recognised".
        """
        written = {character for character in word.lower() if character.isalpha()}
        outside = sorted(written - _PALI_CHARACTERS)
        if not outside:
            return []
        return [
            Reason(
                kind="not-pali-letters",
                detail=(
                    "written with letters outside the Pāḷi alphabet: "
                    + " ".join(outside)
                ),
            )
        ]

    def _syllable_check(
        self, word: str, notation: str = "dpd"
    ) -> tuple[bool | None, list[Reason]]:
        """Whether the syllable layer rejects the shape, and what to say about it.

        Three outcomes, and only the first is a claim: `True` rejects the shape, `False`
        accepts it, `None` means the layer could not judge — because the word is declared
        in another notation, or because `abugida` is not installed.

        A rejection is a statement about the *shape*, never about the word: `etad` ends in a
        consonant and is the `nt nom sg` of `eta` in the PTS spelling. That is why the
        caller only lets it decide when the dictionary and the corpus are silent too.
        """
        if notation not in _SYLLABLE_NOTATIONS:
            return None, [
                Reason(
                    kind="syllables",
                    detail=(
                        f"the word is declared as {notation!r} notation, and the syllable "
                        "rules are stated for the dictionary's; nothing is claimed about "
                        "the shape"
                    ),
                )
            ]
        if _is_possible_syllables is None:
            return None, [
                Reason(
                    kind="syllables",
                    detail=(
                        "no syllable check available (`abugida` is not installed), so "
                        "nothing is claimed about the shape"
                    ),
                )
            ]
        if _is_possible_syllables(word.translate(_NIGGAHITA)):
            return False, []
        return True, [
            Reason(
                kind="syllables",
                detail=(
                    "the syllable structure is not Pāḷi: an impossible cluster, or an "
                    "ending that is neither a vowel nor ṃ (Geiger §5, Oberlies §§15-16)"
                ),
            )
        ]

    def _why_not(self, word: str, shape_only: bool = False) -> list[Reason]:
        licensed = (
            "only the derivation layer licenses the shape, and a licensed shape is not an "
            "attested word"
            if shape_only
            else "no inflection template, citation form or indeclinable entry licenses it, "
            "and no derivation from a known stem produces it"
        )
        return [
            Reason(
                kind="not-in-corpus",
                detail=(
                    "not a key in the corpus index, so it does not occur in the CST, BJT, "
                    "SYA or MST texts"
                ),
            ),
            Reason(kind="not-licensed", detail=licensed),
        ]

    def _corrections(self, word: str) -> tuple[str, ...]:
        """Licensed headwords that fold to the same spelling, best first."""
        sidecar = self._sources()
        if sidecar is not None:
            rows = sidecar.execute(
                "select spelling from folded where folded = ? and spelling != ? "
                "order by attested desc, spelling",
                (unidecode(word), word),
            ).fetchall()
            if not rows:
                rows = sidecar.execute(
                    "select spelling from folded where loose = ? and spelling != ? "
                    "order by attested desc, spelling",
                    (loose_fold(word), word),
                ).fetchall()
            return tuple(spelling for (spelling,) in rows)
        if self.db_session is None:
            # Without the sidecar the folded index is the whole corpus in memory, seconds
            # and hundreds of megabytes, and the analyser filters it. No answer depends on
            # it: the near spellings are a courtesy, and an expensive one.
            return ()
        folded = self._folded_index().get(unidecode(word), [])
        if not folded:
            folded = self._loose_index().get(loose_fold(word), [])
        analyzer = self._analyzer()
        licensed = [
            candidate
            for candidate in folded
            if candidate != word
            and analyzer.analyze(
                candidate, strict=True, particles=False, derivation=False
            )
        ]
        if not licensed:
            return ()

        context = self._context()
        # A candidate the texts actually carry, behind some neighbour, comes first.
        return tuple(sorted(licensed, key=lambda c: (not context.neighbours(c), c)))

    def _lemmas(self, headword_ids: str) -> list[str]:
        if not headword_ids:
            return []
        import json

        try:
            ids = json.loads(headword_ids)
        except (TypeError, ValueError):
            return []
        if not ids or self.db_session is None:
            return []
        headword, _lookup = _orm()
        rows = (
            self.db_session.query(headword.lemma_1).filter(headword.id.in_(ids)).all()
        )
        return [lemma for (lemma,) in rows]


# Readings of one kind beyond this are summarised: a verdict is an explanation, and a
# dozen inflections of the same word explain nothing further.
_MAX_PER_KIND = 4


def _cap(reasons: list[Reason]) -> list[Reason]:
    """Keep the first few reasons of each kind, and say how many were left out."""
    kept: list[Reason] = []
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason.kind] = counts.get(reason.kind, 0) + 1
        if counts[reason.kind] <= _MAX_PER_KIND:
            kept.append(reason)
    for kind, total in counts.items():
        if total > _MAX_PER_KIND:
            kept.append(
                Reason(
                    kind=kind, detail=f"and {total - _MAX_PER_KIND} more of this kind"
                )
            )
    return kept


#: `ṃ` written as the nasal of the class the next consonant belongs to, and back.
_NASAL_CLASSES = (
    (r"ṃ(?=[kg])", "ṅ"),
    (r"ṃ(?=[cj])", "ñ"),
    (r"ṃ(?=[tdn])", "n"),
    (r"ṃ(?=[pbm])", "m"),
    (r"ṃ(?=[ṭḍṇ])", "ṇ"),
)


def nasal_variants(word: str) -> set[str]:
    """The same word with the niggahīta written as this or that edition writes it.

    Ported from `foureditions/revisa_pts._nasal`, whose measurement is why it is here:
    without it the PTS left out 3.496 of 40.000 raw fields (8,7 %), and 1.535 of those were
    nothing but this convention. It goes both ways — word-final `m n ñ ṅ` become `ṃ`, and a
    `ṃ` becomes the nasal of the class of the consonant after it — so it covers the medial
    cases (`cakkhusmim`, `saṃghañ`) that a rule looking only at the last letter misses.
    `ṉ` is added: the Jackson revision writes the niggahīta with it (`aniccaṉ`, SN IV 171).
    """
    base = re.sub(r"[mnñṅṉ]$", "ṃ", word)
    out: set[str] = set()
    for form in (word, base):
        out.add(form)
        for pattern, replacement in _NASAL_CLASSES:
            out.add(re.sub(pattern, replacement, form))
    out.discard(word)
    return out


def loose_fold(word: str) -> str:
    """ASCII fold with doubled letters collapsed: the spelling a reader without a Pāḷi
    keyboard produces, which loses both the diacritics and the gemination."""
    return _DOUBLED.sub(r"\1", unidecode(word).lower())


def _kind_of(analysis: Analysis) -> str:
    if analysis.grammar == "ind":
        return "indeclinable"
    if analysis.grammar == "lemma":
        return "citation"
    return "inflected"


def _describe(analysis: Analysis) -> str:
    lemmas = ", ".join(analysis.lemmas) if analysis.lemmas else "?"
    if analysis.grammar == "ind":
        return f"indeclinable entry {lemmas}"
    if analysis.grammar == "lemma":
        return f"citation form of {lemmas}"
    return f"{analysis.stem} + {analysis.pattern} -> {analysis.grammar} of {lemmas}"


def _main() -> None:
    """Judge words given on the command line, in a file, or on stdin."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="pali_verdict",
        description="Is this Pāḷi, and on what ground.",
        epilog=(
            "`--notation pts` is for a spelling from the PTS or another edition: the "
            "syllable rules are stated for the dictionary's notation, and judging another "
            "one with them is what manufactures false negatives."
        ),
    )
    parser.add_argument("words", nargs="*", help="the words to judge")
    parser.add_argument(
        "-f",
        "--file",
        help="read the words from a text file, or from stdin with `-`",
    )
    parser.add_argument(
        "-n",
        "--notation",
        default="dpd",
        choices=("dpd", "pts", "vri"),
        help="the notation the words are written in (default: dpd)",
    )
    parser.add_argument(
        "-b",
        "--brief",
        action="store_true",
        help="one line per word, without the evidence",
    )
    parser.add_argument(
        "-c",
        "--no-compound",
        action="store_true",
        help="skip the compound layer: faster, and no compound grants Pāḷi anyway",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help=(
            "load the analyser for the paradigm and compound readings: about 5 s and "
            "450 MB more, and the verdict it returns is the same one (measured: 315 of "
            "315 words identical, basis included)"
        ),
    )
    args = parser.parse_args()

    words = list(args.words)
    if args.file:
        text = (
            sys.stdin.read()
            if args.file == "-"
            else Path(args.file).read_text(encoding="utf-8")
        )
        words.extend(_WORD.findall(text))
    if not words:
        parser.print_help()
        return

    paths = ProjectPaths()
    if args.deep:
        from db.db_helpers import get_db_session

        verdict = PaliVerdict(get_db_session(paths.dpd_db_path))
    else:
        # No ORM, no analyser: 0,03 s to open the index instead of 1,2 s, and the answer is
        # the same one — the analyser only ever adds grounds, never a yes.
        verdict = PaliVerdict(
            corpus_reader=sqlite_corpus(paths.dpd_db_path),
            extra_wordlist_path=paths.pts_sn4_wordlist_path,
        )
    seen: set[str] = set()
    for word in words:
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        result = verdict.check(
            word, with_compound=not args.no_compound, notation=args.notation
        )
        print(
            f"{result.score:.2f}\t{result.headline()}"
            if args.brief
            else result.explain()
        )
        if not args.brief:
            print()


if __name__ == "__main__":
    _main()
