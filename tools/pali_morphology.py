"""Morphological analysis and generation of Pāḷi word forms.

Four layers, each one analysed only when the previous cannot account for the form:

1. the paradigm, inverted from the `inflection_templates` grids, plus the headword's own
   citation form and the indeclinables (`dhammassa`, `nara`, `ca`);
2. particle joins, undoing the sandhi that attaches `api`, `ca`, `eva`, `iti`, `iva`, `hi`
   (`vihāresūti` = `vihāresu` + `iti`);
3. productive derivation, reading a stem the lexicon does not have as a known stem plus a
   derivational suffix (`dhammikassa` = `dhamma` + `ika`);
4. compounds, splitting a form into analysable components joined by licensed sandhi
   (`byākaraṇanayena` = `byākaraṇa` + `nayena`), in `analyze_compound`.

Layers 2-4 raise coverage and lower precision, so `analyze` exposes a switch for each: the
"is this word correct?" question wants the narrow answer, reading real text wants all four.

A fifth, induced set of junction transformations (gemination and assimilation, e.g.
`siddha` + `ṭhena` -> `siddhaṭṭhena`) is loaded but OFF by default: on 900 compound
headwords it buys 0.7 points of reachable decompositions for 3.3x the running time. It no
longer *costs* anything, so it is there for callers who want maximum recall. See
`use_junction_transforms` and `scripts/build/junction_transform_rules.py`.

The search deepens by number of components: every two-part reading is enumerated before
any three-part one, and it only stops on a surplus rather than as soon as the answer list
is full. That costs about twice the time of a shallow search and is worth it: on a fixed
1.000-word sample it lifts the share of words whose decomposition matches the deconstructor
exactly from 59.2% to 61.1% and cuts the unreachable ones from 20.6% to 18.1%. For bulk
work where that trade is wrong, `analyze_compound(word, max_segments=3)` is the cheap
setting.

Every template is a table of endings (`db/inflections/inflection_templates.xlsx`, imported
by `db/inflections/create_inflection_templates.py`). Odd columns hold the endings of a
paradigm, the following even column holds the grammar label of that cell, and the first
column is the row header (case, tense, person...). A form is licensed by a template when
the form ends with one of its endings and the remainder is a stem the dictionary knows.

The stem lexicon is what limits this phase: a form whose stem is not in `dpd_headwords`
cannot be analysed, because the derivational rules that would license a novel stem (which
suffix after which root, with which retroflexion) are not encoded anywhere yet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from sqlalchemy.orm import Session

from db.models import DpdHeadword, InflectionTemplates
from tools.paths import ProjectPaths

# `!` marks an entry that is itself an inflected form, `*` marks an irregular stem that
# contributes nothing (the ending is the whole form). Mirrors
# `db/inflections/generate_inflection_tables.py`, which does re.sub(r"\!|\*", "", stem).
_STEM_MARKERS = re.compile(r"[!*]")

# Mirrors `DpdHeadword.lemma_clean` and `exporter/webapp/preloads.py::clean_lemma`.
_SENSE_NUMBER = re.compile(r" \d.*$")

# The row header of the "in comps" row: its forms are compounding stems, not inflections.
_COMPOUND_ROW = "in comps"

# Parts of speech that cannot stand as a compound member: a root, a suffix or a letter is
# not a word a compound is built from. The deconstructor excludes these too
# (`go_modules/deconstructor/importer/unmatched.go`), but it also excludes `prefix` and
# `sandhi`, and those two *are* members in the dictionary's own convention (`na > a +
# kaṇṭa + aka`): dropping them measured 81.3% -> 55.7% on the editorial metric, while
# these seven give 62.2% agreement with the deconstructor (61.2% with no exclusion at all),
# 57.0% on the editorial metric and 28% less time. The same form still resolves when it is
# the query itself; it just cannot be a piece of a compound.
_COMPONENT_EXCLUDED_POS = frozenset(
    {"abbrev", "cs", "idiom", "letter", "root", "suffix", "ve"}
)

# Shortest stem a derivation may start from. A one-letter base turns the suffix table into
# a licence for anything: `ñ` + `anā` reads `ñana` as a word, which is how `viññana` ends
# up decomposing as `vi` + `ñana`.
_MIN_DERIVATION_BASE = 3

# Whether a compound component may be licensed by the derivation layer. It is off by
# default because a derived form the dictionary does not have becomes a component of a
# further split, and the split is then built on a guess. Measured on 500 corpus words and
# 600 compound headwords: allowing it gives 63.8% agreement with the deconstructor and
# 81.0% on the editorial metric at 53 ms/word; refusing it gives 71.4% and 78.7% at
# 27 ms/word. Components are dictionary forms first; the derivation layer is for whole
# queries, not for pieces.
_COMPONENT_DERIVATION = False

# Per-substring ceiling on the segmentations kept, so one very compound-heavy word
# cannot make the search explode.
_MAX_SEGMENTATIONS = 16


def _compound_rank(
    compound: "CompoundAnalysis",
) -> tuple[int, int, int, tuple[str, ...]]:
    """Rank readings: fewest components, then fewest one-letter components, then lightest
    sandhi rules. A one-letter component is licensed (`a-`, `na-`, `su-`) but it is also
    how a mis-split looks, so it is demoted rather than forbidden."""
    return (
        len(compound.segments),
        sum(1 for segment in compound.segments if len(segment.text) < 2),
        compound.weight,
        compound.parts,
    )


@dataclass(frozen=True)
class Analysis:
    """One licensed reading of a word form.

    Three things can be going on at once, and the reading records each of them:

    * `particle` — the form is a base joined to one of the six common particles by sandhi
      (`vihāresūti` = `vihāresu` + `iti`), and `base` is the recovered base.
    * `suffix` — the stem is not itself in the lexicon but is derived from one that is
      (`dhammikassa` = `dhammika`, which is `dhamma` + `ika`), and `derivation_base` is the
      stem it was derived from.
    * otherwise the form is a paradigm form or the citation form of a headword.

    `stem`, `pattern` and `grammar` always describe the form's own inflection.
    """

    stem: str
    pattern: str
    grammar: str
    lemmas: tuple[str, ...]
    headword_ids: tuple[int, ...]
    particle: str = ""
    base: str = ""
    suffix: str = ""
    derivation_base: str = ""

    def __str__(self) -> str:
        lemmas = ", ".join(self.lemmas) if self.lemmas else "?"
        origin = []
        if self.particle:
            origin.append(f"{self.base} + {self.particle}")
        if self.suffix:
            origin.append(f"{self.derivation_base} + {self.suffix}")
        origin.append(f"{self.stem}+{self.pattern}")
        return f"{' -> '.join(origin)} -> {self.grammar} [{lemmas}]"


@dataclass(frozen=True)
class Segment:
    """One component of a compound, with the sandhi rule that joins it to the previous.

    `rule` is the `index` of the rule in `shared_data/deconstructor/sandhi_rules.tsv`, or
    0 when the two components meet unchanged. `analyses` is empty when the component is
    itself a compound, in which case `children` holds its own segments.
    """

    text: str
    analyses: tuple[Analysis, ...] = ()
    children: tuple["Segment", ...] = ()
    rule: int = 0
    weight: int = 0

    @property
    def lemmas(self) -> tuple[str, ...]:
        lemmas: set[str] = set()
        for analysis in self.analyses:
            lemmas.update(analysis.lemmas)
        for child in self.children:
            lemmas.update(child.lemmas)
        return tuple(sorted(lemmas))

    def readings(self) -> str:
        if self.children:
            return " + ".join(child.text for child in self.children)
        return "; ".join(str(analysis) for analysis in self.analyses[:3])


@dataclass(frozen=True)
class CompoundAnalysis:
    """A compound read as a sequence of components.

    The components are kept flat, as the deconstructor writes them (`a + b + c`), rather
    than as a binary tree: that is the shape both the dictionary's `compound_construction`
    and the GoldenDict deconstructor entries use.
    """

    word: str
    segments: tuple[Segment, ...]
    weight: int = 0

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(segment.text for segment in self.segments)

    @property
    def lemmas(self) -> tuple[str, ...]:
        lemmas: set[str] = set()
        for segment in self.segments:
            lemmas.update(segment.lemmas)
        return tuple(sorted(lemmas))

    def __str__(self) -> str:
        lines = [self.word]
        for index, segment in enumerate(self.segments):
            marker = "└─" if index == len(self.segments) - 1 else "├─"
            rule = f"  (rule {segment.rule})" if segment.rule else ""
            lines.append(f"   {marker} {segment.text:<20}{rule}")
            for line in segment.readings().split("; "):
                if line:
                    lines.append(f"        {line}")
        return "\n".join(lines)


@dataclass
class _StemEntry:
    """Everything the lexicon knows about one (stem, pattern) pair.

    Keyed by the pair, not by the stem alone: an irregular stem is stored as `*` and
    cleans to the empty string, so every `*` headword would otherwise share one entry and
    the lemmas of `hoti`, `nadī`, `ta`... would all be attributed to each other's forms.
    """

    lemmas: set[str] = field(default_factory=set)
    headword_ids: set[int] = field(default_factory=set)
    pos: set[str] = field(default_factory=set)


@dataclass
class _LemmaEntry:
    """A headword's citation form, which is itself a licensed form.

    `db/inflections/generate_inflection_tables.py` prepends `lemma_clean` to every
    inflection list, so the citation form belongs to the dictionary's own inventory even
    though no template cell contains it (e.g. `ravanta`, stem `rav`, pattern `anta prp`
    whose cells are `anto`, `aṃ`, `antā`...). Indeclinables, which have no paradigm at
    all, are recorded here too.
    """

    lemmas: set[str] = field(default_factory=set)
    headword_ids: set[int] = field(default_factory=set)
    pos: set[str] = field(default_factory=set)
    indeclinable: bool = False


class PaliMorphologyAnalyzer:
    """Analyse and generate Pāḷi forms from the inflection templates.

    The template grids and the stem lexicon are loaded once, from plain columns rather
    than ORM entities: materialising all ~89k `DpdHeadword` objects costs hundreds of MiB
    for five fields (see `kamma/threads/20260918_webapp_memory/spec.md`).
    """

    def __init__(
        self,
        db_session: Session,
        particle_rules_path: Path | None = None,
        derivation_rules_path: Path | None = None,
        sandhi_rules_path: Path | None = None,
        transform_rules_path: Path | None = None,
        use_junction_transforms: bool = False,
        components_allow_derivation: bool | None = None,
        exclude_headword_ids: set[int] | None = None,
    ) -> None:
        # Headwords to leave out of the lexicon. The held-out evaluation uses this to ask
        # whether a form can still be analysed when its own entry is unknown.
        self._exclude_ids: set[int] = exclude_headword_ids or set()
        self._ending_index: dict[str, list[tuple[str, str]]] = {}
        self._pattern_forms: dict[str, list[tuple[str, str]]] = {}
        self._lexicon: dict[tuple[str, str], _StemEntry] = {}
        self._stem_patterns: dict[str, set[str]] = {}
        self._lemma_forms: dict[str, _LemmaEntry] = {}
        self._component_excluded_ids: set[int] = set()
        self._particle_rules: list[tuple[str, str, str]] = []
        self._derivation_index: dict[str, list[tuple[str, str, str]]] = {}
        self._sandhi_index: dict[tuple[str, str], list[tuple[int, str, str, int]]] = {}
        self._transform_index: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
        self._max_added_length: int = 0
        self._max_ending_length: int = 0

        self._load_templates(db_session)
        self._load_lexicon(db_session)

        paths = ProjectPaths()
        particle_path = (
            particle_rules_path
            if particle_rules_path is not None
            else paths.particle_sandhi_rules_path
        )
        if particle_path.exists():
            self._load_particle_rules(particle_path)

        derivation_path = (
            derivation_rules_path
            if derivation_rules_path is not None
            else paths.derivation_rules_path
        )
        if derivation_path.exists():
            self._load_derivation_rules(derivation_path)

        sandhi_path = (
            sandhi_rules_path
            if sandhi_rules_path is not None
            else paths.sandhi_rules_path
        )
        if sandhi_path.exists():
            self._load_sandhi_rules(sandhi_path)

        transform_path = (
            transform_rules_path
            if transform_rules_path is not None
            else paths.junction_transform_rules_path
        )
        # Measured on 900 compound headwords: +0.7 points of reachable decompositions for
        # 3.3x the running time. Worth having, not worth defaulting to.
        self._use_transforms = use_junction_transforms
        self._component_derivation = (
            _COMPONENT_DERIVATION
            if components_allow_derivation is None
            else components_allow_derivation
        )
        if self._use_transforms and transform_path.exists():
            self._load_junction_transforms(transform_path)

    # ------------------------------------------------------------------ loading

    def _load_templates(self, db_session: Session) -> None:
        """Index every ending of every template by ending and by pattern."""
        rows = db_session.query(
            InflectionTemplates.pattern, InflectionTemplates.data
        ).all()

        for pattern, data in rows:
            table: list[list[list[str]]] = json.loads(data)
            # Row 0 is the top header (sg / pl, 1st / 2nd / 3rd...).
            for row in table[1:]:
                if not row or not row[0] or row[0][0] == _COMPOUND_ROW:
                    continue
                # Odd columns are forms, the next even column is their grammar label.
                for column in range(1, len(row) - 1, 2):
                    label_cell = row[column + 1]
                    if not label_cell or not label_cell[0]:
                        continue
                    grammar = label_cell[0]
                    for ending in row[column]:
                        if not ending:
                            continue
                        self._ending_index.setdefault(ending, []).append(
                            (pattern, grammar)
                        )
                        self._pattern_forms.setdefault(pattern, []).append(
                            (ending, grammar)
                        )

        self._max_ending_length = (
            max(len(ending) for ending in self._ending_index)
            if self._ending_index
            else 0
        )

    def _load_lexicon(self, db_session: Session) -> None:
        """Build the stem lexicon and the citation-form index from the headword table."""
        rows = db_session.query(
            DpdHeadword.id,
            DpdHeadword.lemma_1,
            DpdHeadword.stem,
            DpdHeadword.pattern,
            DpdHeadword.pos,
        ).all()

        for headword_id, lemma_1, stem, pattern, pos in rows:
            # Entries whose stem carries `!` are inflected forms themselves; their forms
            # belong to the main headword (same rule as db/grammar/grammar_to_lookup.py).
            if "!" in stem or headword_id in self._exclude_ids:
                continue

            if pos in _COMPONENT_EXCLUDED_POS:
                self._component_excluded_ids.add(headword_id)

            lemma = _SENSE_NUMBER.sub("", lemma_1)
            lemma_entry = self._lemma_forms.setdefault(lemma, _LemmaEntry())
            lemma_entry.lemmas.add(lemma)
            lemma_entry.headword_ids.add(headword_id)
            lemma_entry.pos.add(pos)
            if stem == "-":
                lemma_entry.indeclinable = True

            if not pattern:
                continue
            clean_stem = _STEM_MARKERS.sub("", stem)
            entry = self._lexicon.setdefault((clean_stem, pattern), _StemEntry())
            entry.lemmas.add(lemma)
            entry.headword_ids.add(headword_id)
            entry.pos.add(pos)
            self._stem_patterns.setdefault(clean_stem, set()).add(pattern)

    def _load_particle_rules(self, path: Path) -> None:
        """Load the induced (base_tail, surface_tail) joins, keyed by particle.

        Written by `scripts/build/particle_sandhi_rules.py`. The base's last character is
        part of the file's key but not needed here: a join is inverted by string matching,
        and a wrong de-join is discarded because the recovered base has to be a licensed
        form anyway.
        """
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            _base_last, particle, base_tail, surface_tail, _count = parts
            if not surface_tail:
                continue
            self._particle_rules.append((particle, base_tail, surface_tail))

    @property
    def particle_rule_count(self) -> int:
        return len(self._particle_rules)

    def _load_derivation_rules(self, path: Path) -> None:
        """Index the induced derivations by the string they append to the base's stem.

        Written by `scripts/build/derivation_rules.py`. Each row is
        (suffix, base_last, added, pattern, count): a stem ending in `added`, whose base
        ends in `base_last`, inflects with `pattern` and carries the derivational `suffix`.
        """
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            suffix, base_last, added, pattern, _count = parts
            if not added:
                continue
            self._derivation_index.setdefault(added, []).append(
                (suffix, base_last, pattern)
            )

        self._max_added_length = (
            max(len(added) for added in self._derivation_index)
            if self._derivation_index
            else 0
        )

    @property
    def derivation_rule_count(self) -> int:
        return sum(len(v) for v in self._derivation_index.values())

    def _load_sandhi_rules(self, path: Path) -> None:
        """Index the deconstructor's sandhi rules by the rune pair they apply to.

        The semantics are the ones `go_modules/deconstructor/splitters/split_2words.go`
        implements: where the surface shows `...A B...` at the junction, the left word ends
        with `ch1` replacing `A`, and the right word starts with `ch2` replacing `A B`.
        `tadapi` splits as `tada` + `api` through the rule (a, p) -> ch1 `a`, ch2 `ap`.
        """
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            index, ch_a, ch_b, ch1, ch2, _eg, weight = parts[:7]
            if not ch_a or not ch_b:
                continue
            self._sandhi_index.setdefault((ch_a[0], ch_b[0]), []).append(
                (int(index), ch1, ch2, int(weight or 0))
            )

    @property
    def sandhi_rule_count(self) -> int:
        return sum(len(v) for v in self._sandhi_index.values())

    def _load_junction_transforms(self, path: Path) -> None:
        """Load the induced transformations, keyed the same way as the curated rules.

        Written by `scripts/build/junction_transform_rules.py`: the gemination and
        assimilation cases (`siddha` + `ṭhena` -> `siddhaṭṭhena`) the hand-written table
        does not carry. They are kept apart from the curated rules and only consulted when
        no curated rule licenses a split, so they can add coverage without displacing the
        established readings.
        """
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            ch_a, ch_b, ch1, ch2, weight = parts[:5]
            if not ch_a or not ch_b:
                continue
            self._transform_index.setdefault((ch_a[0], ch_b[0]), []).append(
                (ch1, ch2, int(weight or 0))
            )

    @property
    def transform_rule_count(self) -> int:
        return sum(len(v) for v in self._transform_index.values())

    def _junctions(
        self, word: str, position: int, transforms: bool = False
    ) -> Iterator[tuple[str, str, int, int]]:
        """Yield (left, right, rule, weight) for every licensed split at one junction.

        `transforms` adds the induced gemination/assimilation rules, which callers only
        want when the curated table has nothing for this junction.
        """
        # The surface itself: both halves are the words unchanged. The deconstructor
        # scores a plain junction 2, and a rule scores its own weight, lower being better.
        yield word[: position + 1], word[position + 1 :], 0, 2

        key = (word[position], word[position + 1])

        for index, ch1, ch2, weight in self._sandhi_index.get(key, ()):
            left = word[:position] + ch1
            right = ch2 + word[position + 2 :]
            if left and right:
                yield left, right, index, weight

        if transforms:
            for ch1, ch2, weight in self._transform_index.get(key, ()):
                left = word[:position] + ch1
                right = ch2 + word[position + 2 :]
                if left and right:
                    yield left, right, 0, weight

    # ---------------------------------------------------------------- properties

    @property
    def ending_count(self) -> int:
        return sum(len(v) for v in self._ending_index.values())

    @property
    def pattern_count(self) -> int:
        return len(self._pattern_forms)

    @property
    def stem_count(self) -> int:
        return len(self._stem_patterns)

    @property
    def lemma_count(self) -> int:
        return len(self._lemma_forms)

    # ------------------------------------------------------------------ analysis

    def analyze(
        self,
        word: str,
        strict: bool = True,
        particles: bool = True,
        derivation: bool = True,
    ) -> list[Analysis]:
        """Return every licensed reading of `word`.

        `strict` requires the ending's pattern to be one the stem is recorded with, which
        is what the build pipeline does. With `strict=False` any pattern whose ending
        matches is accepted, which finds readings the dictionary's own pattern assignment
        does not cover; those readings carry no lemmas, because no headword records that
        (stem, pattern) pair.

        With `particles=True` the form is also tried as a base joined to one of the six
        common particles (`vihāresūti` = `vihāresu` + `iti`). With `derivation=True` a stem
        the lexicon does not have is tried as a known stem plus a derivational suffix
        (`dhammikassa` = `dhamma` + `ika`), which is what lets novel stems be analysed.
        """
        results = self._analyze_stem(word, strict, derivation)
        if particles and self._particle_rules:
            results.extend(self._analyze_particles(word, strict, derivation))

        unique: list[Analysis] = []
        seen: set[tuple[str, str, str, str, str, str]] = set()
        for analysis in results:
            key = (
                analysis.stem,
                analysis.pattern,
                analysis.grammar,
                analysis.particle,
                analysis.base,
                analysis.derivation_base + analysis.suffix,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(analysis)
        return unique

    def _ending_candidates(self, word: str) -> Iterator[tuple[str, str, str]]:
        """Yield (stem, pattern, grammar) for every template ending the word ends with."""
        for length in range(min(len(word), self._max_ending_length), 0, -1):
            candidates = self._ending_index.get(word[-length:])
            if not candidates:
                continue
            stem = word[:-length]
            for pattern, grammar in candidates:
                yield stem, pattern, grammar

    def _analyze_stem(
        self, word: str, strict: bool, derivation: bool = True
    ) -> list[Analysis]:
        """The readings of the form itself: paradigm, citation form, and derivation."""
        results = self._analyze_core(word, strict)
        if derivation and self._derivation_index:
            results.extend(self._analyze_derivation(word))
        return results

    def _analyze_core(self, word: str, strict: bool) -> list[Analysis]:
        """The readings of `word` itself: its paradigm, or its citation form."""
        results: list[Analysis] = []
        seen: set[tuple[str, str, str]] = set()

        # The citation form of a headword is itself a licensed form, and for an
        # indeclinable it is the only one.
        lemma_entry = self._lemma_forms.get(word)
        if lemma_entry is not None:
            if lemma_entry.indeclinable:
                results.append(
                    Analysis(
                        stem="-",
                        pattern="-",
                        grammar="ind",
                        lemmas=tuple(sorted(lemma_entry.lemmas)),
                        headword_ids=tuple(sorted(lemma_entry.headword_ids)),
                    )
                )
            else:
                results.append(
                    Analysis(
                        stem=word,
                        pattern="-",
                        grammar="lemma",
                        lemmas=tuple(sorted(lemma_entry.lemmas)),
                        headword_ids=tuple(sorted(lemma_entry.headword_ids)),
                    )
                )

        for stem, pattern, grammar in self._ending_candidates(word):
            recorded_patterns = self._stem_patterns.get(stem)
            if not recorded_patterns:
                continue
            if strict and pattern not in recorded_patterns:
                continue
            key = (stem, pattern, grammar)
            if key in seen:
                continue
            seen.add(key)
            entry = self._lexicon.get((stem, pattern))
            results.append(
                Analysis(
                    stem=stem,
                    pattern=pattern,
                    grammar=grammar,
                    lemmas=tuple(sorted(entry.lemmas)) if entry else (),
                    headword_ids=tuple(sorted(entry.headword_ids)) if entry else (),
                )
            )

        return results

    def _analyze_derivation(self, word: str) -> list[Analysis]:
        """Read the form as a stem derived from a known stem plus a suffix.

        The rule's recorded pattern has to be the one the stripped ending belongs to, and
        the recovered base has to be a stem the lexicon knows ending in the character the
        rule was learned with. Those two constraints are what keep the rule table's noise
        out: without them any string could be "derived" from any shorter one.
        """
        results: list[Analysis] = []
        seen: set[tuple[str, str, str, str]] = set()

        for stem, pattern, grammar in self._ending_candidates(word):
            for length in range(min(len(stem), self._max_added_length), 0, -1):
                rules = self._derivation_index.get(stem[-length:])
                if not rules:
                    continue
                base = stem[:-length]
                if len(base) < _MIN_DERIVATION_BASE:
                    continue
                for suffix, base_last, rule_pattern in rules:
                    if rule_pattern != pattern or not base.endswith(base_last):
                        continue
                    base_patterns = self._stem_patterns.get(base)
                    if not base_patterns:
                        continue
                    key = (stem, pattern, grammar, suffix)
                    if key in seen:
                        continue
                    lemmas: set[str] = set()
                    headword_ids: set[int] = set()
                    for base_pattern in base_patterns:
                        entry = self._lexicon.get((base, base_pattern))
                        if entry is not None:
                            lemmas |= entry.lemmas
                            headword_ids |= entry.headword_ids
                    if not lemmas:
                        continue
                    seen.add(key)
                    results.append(
                        Analysis(
                            stem=stem,
                            pattern=pattern,
                            grammar=grammar,
                            lemmas=tuple(sorted(lemmas)),
                            headword_ids=tuple(sorted(headword_ids)),
                            suffix=suffix,
                            derivation_base=base,
                        )
                    )

        return results

    def _analyze_particles(
        self, word: str, strict: bool, derivation: bool = True
    ) -> list[Analysis]:
        """Undo every induced particle join whose surface the word ends with.

        A de-join is only kept when the recovered base is itself a licensed form, which
        is what keeps the rule table's noise out of the results.
        """
        results: list[Analysis] = []
        for particle, base_tail, surface_tail in self._particle_rules:
            if not word.endswith(surface_tail):
                continue
            base = word[: len(word) - len(surface_tail)] + base_tail
            if not base or base == word:
                continue
            for analysis in self._analyze_stem(base, strict, derivation):
                results.append(replace(analysis, particle=particle, base=base))
        return results

    def analyze_lemmas(self, word: str, strict: bool = True) -> set[str]:
        """Convenience wrapper returning just the lemmas behind every reading."""
        lemmas: set[str] = set()
        for analysis in self.analyze(word, strict=strict):
            lemmas.update(analysis.lemmas)
        return lemmas

    # ---------------------------------------------------------------- compounds

    def analyze_compound(
        self,
        word: str,
        max_segments: int = 6,
        limit: int = 12,
        min_segment_length: int = 1,
        expand: bool = False,
    ) -> list[CompoundAnalysis]:
        """Read `word` as a compound of at most `max_segments` components.

        Every component has to be analysable on its own (by the paradigm, citation-form or
        derivation layers) and every junction has to be licensed by a sandhi rule. The
        segmentations are ranked the way the deconstructor ranks its matches: fewest
        components first, then the lightest sandhi rules (a plain junction scores 2).

        `min_segment_length` can prune single-letter components, but the default keeps
        them: the privative `a-` and `na-` really are single-letter members. `expand` adds
        each top reading's fully decomposed
        counterpart after the flat ones, rather than instead of them: both granularities
        are legitimate and which is right depends on the question.
        """
        if len(word) < 4 or not self._sandhi_index:
            return []

        memo: dict[tuple[str, int], list[tuple[Segment, ...]]] = {}
        analyses: dict[str, list[Analysis]] = {}
        budget = max(2, min(max_segments, len(word) // min_segment_length))

        # Iterative deepening on the number of components: every two-part reading is
        # enumerated before any three-part one, so the cheap linguistically-simple answer
        # is never crowded out by a deeply cut alternative.
        results: list[CompoundAnalysis] = []
        seen: set[tuple[str, ...]] = set()
        for parts in range(2, budget + 1):
            for segments in self._partition(
                word, parts, memo, analyses, min_segment_length
            ):
                key = tuple(segment.text for segment in segments)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    CompoundAnalysis(
                        word=word,
                        segments=segments,
                        weight=sum(segment.weight for segment in segments),
                    )
                )
            # Deepening stops on a surplus, not on the quota: stopping as soon as the
            # two-part readings fill the list is what hid the three-part answer.
            if len(results) >= limit * 4:
                break

        results.sort(key=lambda c: (len(c.segments), c.weight, c.parts))
        top = results[:limit]
        if not expand:
            return top

        # `expand` adds each top reading's fully decomposed counterpart after the flat
        # ones, rather than replacing them.
        seen_parts = {compound.parts for compound in top}
        decomposed: list[CompoundAnalysis] = []
        for compound in top:
            segments = self._expand(
                compound.segments, budget, memo, analyses, min_segment_length
            )
            parts = tuple(segment.text for segment in segments)
            if parts in seen_parts:
                continue
            seen_parts.add(parts)
            decomposed.append(
                CompoundAnalysis(
                    word=word,
                    segments=segments,
                    weight=sum(segment.weight for segment in segments),
                )
            )
        decomposed.sort(key=lambda c: (len(c.segments), c.weight, c.parts))
        return top + decomposed[:limit]

    def _is_component(self, analysis: Analysis) -> bool:
        """Whether a reading may stand as a compound member.

        A reading is rejected when every headword behind it is an entry that is not a word
        (a root, a prefix, a letter, a sandhi entry...). The same form still resolves when
        it is the query itself; it just cannot be one of the pieces of a compound.
        """
        if not analysis.headword_ids:
            return True
        return any(
            headword_id not in self._component_excluded_ids
            for headword_id in analysis.headword_ids
        )

    def _analyze_cached(
        self, word: str, analyses: dict[str, list[Analysis]]
    ) -> list[Analysis]:
        """`_analyze_stem` behind a per-search cache: substrings recur constantly."""
        cached = analyses.get(word)
        if cached is None:
            cached = self._analyze_stem(word, True, self._component_derivation)
            analyses[word] = cached
        return cached

    def _partition(
        self,
        word: str,
        parts: int,
        memo: dict[tuple[str, int], list[tuple[Segment, ...]]],
        analyses: dict[str, list[Analysis]],
        min_segment_length: int,
    ) -> list[tuple[Segment, ...]]:
        """Every way to cut `word` into exactly `parts` analysable components."""
        key = (word, parts)
        if key in memo:
            return memo[key]

        results: list[tuple[Segment, ...]] = []

        if parts == 1:
            found = [
                analysis
                for analysis in self._analyze_cached(word, analyses)
                if self._is_component(analysis)
            ]
            if found:
                results.append((Segment(text=word, analyses=tuple(found)),))
            memo[key] = results
            return results

        for position in range(len(word) - 1):
            if (
                position + 1 < min_segment_length
                or len(word) - position - 1 < min_segment_length
            ):
                continue
            # Curated rules first, then the induced gemination/assimilation ones. The
            # induced rules carry the heaviest weight, so they add reachability without
            # displacing an established reading of the same shape.
            for transforms in (False, self._use_transforms):
                for left, right, rule, weight in self._junctions(
                    word, position, transforms=transforms
                ):
                    if (
                        len(left) < min_segment_length
                        or len(right) < min_segment_length
                    ):
                        continue
                    head = [
                        analysis
                        for analysis in self._analyze_cached(left, analyses)
                        if self._is_component(analysis)
                    ]
                    if not head:
                        continue
                    for tail in self._partition(
                        right, parts - 1, memo, analyses, min_segment_length
                    ):
                        results.append(
                            (
                                Segment(text=left, analyses=tuple(head)),
                                replace(tail[0], rule=rule, weight=weight),
                                *tail[1:],
                            )
                        )
        # Within one (word, parts) key every segmentation has the same number of
        # components, so ranking by component count is a no-op here: the beam keeps the
        # lightest rules. Deeper cuts live under their own key and cannot be evicted by
        # shallower ones.
        results.sort(
            key=lambda segments: (
                sum(segment.weight for segment in segments),
                tuple(segment.text for segment in segments),
            )
        )
        memo[key] = results[:_MAX_SEGMENTATIONS]
        return memo[key]

    def _expand(
        self,
        segments: tuple[Segment, ...],
        budget: int,
        memo: dict[tuple[str, int], list[tuple[Segment, ...]]],
        analyses: dict[str, list[Analysis]],
        min_segment_length: int,
        depth: int = 2,
    ) -> tuple[Segment, ...]:
        """Re-segment every component that is itself a compound, recursively."""
        if depth <= 0:
            return segments

        expanded: list[Segment] = []
        for segment in segments:
            finer: tuple[Segment, ...] | None = None
            for parts in range(2, budget + 1):
                candidates = self._partition(
                    segment.text, parts, memo, analyses, min_segment_length
                )
                if candidates:
                    finer = candidates[0]
                    break
            if finer is None:
                expanded.append(segment)
                continue
            nested = self._expand(
                finer, budget, memo, analyses, min_segment_length, depth - 1
            )
            head = replace(nested[0], rule=segment.rule, weight=segment.weight)
            expanded.extend((head, *nested[1:]))
        return tuple(expanded)

    # ---------------------------------------------------------------- generation

    def generate(self, stem: str, pattern: str) -> list[tuple[str, str]]:
        """Return every (form, grammar) the template licenses for stem + pattern."""
        clean_stem = _STEM_MARKERS.sub("", stem)
        return [
            (f"{clean_stem}{ending}", grammar)
            for ending, grammar in self._pattern_forms.get(pattern, [])
        ]

    def patterns(self) -> list[str]:
        return sorted(self._pattern_forms)

    def lexicon_pairs(self) -> list[tuple[str, str]]:
        """Every (stem, pattern) pair the dictionary records, for bulk generation."""
        return list(self._lexicon)

    def known_patterns(self, stem: str) -> set[str]:
        return set(self._stem_patterns.get(stem, set()))


def _main() -> None:
    """Report every licensed reading of the words given on the command line."""
    import sys

    from db.db_helpers import get_db_session
    from tools.paths import ProjectPaths

    analyzer = PaliMorphologyAnalyzer(get_db_session(ProjectPaths().dpd_db_path))
    for word in sys.argv[1:]:
        analyses = analyzer.analyze(word)
        if analyses:
            print(f"{word}: {len(analyses)} readings")
            for analysis in analyses:
                print(f"  {analysis}")
            continue
        compounds = analyzer.analyze_compound(word)
        if compounds:
            print(f"{word}: {len(compounds)} compound readings")
            for compound in compounds[:3]:
                print(compound)
            continue
        print(f"{word}: no analysis")


if __name__ == "__main__":
    _main()
