#!/usr/bin/env python3

"""Extract the bilingual sense glossary, and use it to translate what it can.

The existing translation is a list of senses separated by `;`, and it kept that
structure, so English and Spanish can be aligned sense by sense:

    meaning_1 (en)  "not hard; not rough"
    meaning_1 (es)  "no duro; no áspero"
                     └ not hard → no duro
                       not rough → no áspero

That alignment turns 72,698 translated entries into a glossary of tens of
thousands of English senses with their agreed Spanish. Two outputs come from it:

1. `--glossary` writes the whole glossary to a TSV. It is a derived artifact,
   not versioned: it can always be rebuilt from the sidecar plus the db.
2. `--output` writes the entries this glossary can translate outright — words
   whose every sense is already in it, with a single agreed rendering. No
   judgement is involved, so these can be generated rather than written.

Usage:
    uv run python scripts/build/build_spanish_glossary.py
"""

import argparse
import collections
import re
import unicodedata
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.db_helpers import get_db_session
from db.models import (
    DpdHeadword,
    FamilyCompound,
    FamilyIdiom,
    FamilyRoot,
    FamilySet,
    FamilyWord,
)
from tools import spanish
from tools.paths import ProjectPaths
from tools.printer import printer as pr

GLOSSARY_TSV = Path("dpd_es_glossary.tsv")
COMPOUNDS_TSV = Path(__file__).parent / "compounds_es.tsv"
SENSES_TSV = Path(__file__).parent / "senses_es.tsv"
OVERRIDES_TSV = Path(__file__).parent / "glosario_es_overrides.tsv"
FIELDS = ("meaning_1", "meaning_lit", "meaning_2")


def senses(text: str) -> list[str]:
    """Split a meaning field into its `;`-separated senses."""
    return [sense.strip() for sense in text.split(";") if sense.strip()]


def bare(text: str) -> str:
    """Strip accents, case, punctuation and plural -s, to spot mere variants."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    letters = "".join(c for c in stripped if c.isalnum() or c == " ")
    return " ".join(word.rstrip("s") for word in letters.split())


def is_reordered(source: list[str], target: list[str]) -> bool:
    """True when the translation moved a sense, which breaks positional pairing.

    Only reorderings anchored on a verbatim repeat — a proper name kept as is —
    can be seen this way; an unanchored one is invisible and stays unnoticed.
    """
    positions = {sense: index for index, sense in enumerate(source)}
    return any(
        sense in positions and positions[sense] != index
        for index, sense in enumerate(target)
    )


def build_glossary(
    db_session: Session, sidecar: dict[str, dict[str, str]]
) -> tuple[dict[tuple[str, str], str], dict[str, str], dict[str, int]]:
    """Map each (English sense, part of speech) to its agreed Spanish.

    The part of speech belongs in the key: the same English gloss is rendered
    differently depending on it, and legitimately so — `eating` is *comiendo*
    under `prp`, *que come* under `adj` and *comer* under `nt`. Keying on the
    sense alone turns that grammar into spurious disagreement.
    """
    counts: dict[tuple[str, str], collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    stats = collections.Counter()
    for headword in db_session.execute(select(DpdHeadword)).scalars():
        stored = sidecar.get(headword.lemma_1)
        # generated entries are not authority: letting them in would make the
        # next batch cite this project's own unreviewed wording as precedent
        if not stored or stored.get(spanish.ORIGIN) == spanish.GENERATED:
            continue
        with spanish.suppressed():
            english = {field: getattr(headword, field) for field in FIELDS}
            pos = headword.pos
        for field in FIELDS:
            source, target = senses(english[field]), senses(stored[field])
            if not source or len(source) != len(target):
                continue
            if is_reordered(source, target):
                stats["reordenados"] += 1
                continue
            for one, other in zip(source, target):
                counts[(one.lower(), pos)][other] += 1

    # How often each Spanish wording is used anywhere in the translation. This
    # is what settles a split: the house style is whatever the corpus already
    # does, so a tie goes to the wording it uses elsewhere.
    corpus = collections.Counter()
    for options in counts.values():
        corpus.update(options)

    glossary: dict[tuple[str, str], str] = {}
    for key, options in counts.items():
        ranked = sorted(
            options.items(), key=lambda item: (item[1], corpus[item[0]]), reverse=True
        )
        best, count = ranked[0]
        if len(options) == 1:
            stats["acordadas"] += 1
        elif len({bare(option) for option in options}) == 1:
            stats["cosmeticas"] += 1
        elif count / sum(options.values()) >= 0.9:
            stats["dominantes"] += 1
        elif count > ranked[1][1] or corpus[best] > corpus[ranked[1][0]]:
            # split, settled by the corpus so the term stays homogeneous
            stats["homogeneizadas"] += 1
        else:
            stats["sin_resolver"] += 1
            continue
        glossary[key] = best

    # A sense whose Spanish never varies across parts of speech does not need
    # the part of speech at all. Keeping that fallback matters: keying strictly
    # on (sense, pos) is more precise but far less applicable to new words,
    # because a compound rarely shares the part of speech of the entry the
    # sense was translated under.
    across: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for (english, _), options in counts.items():
        across[english].update(options)
    fallback = {
        english: max(options.items(), key=lambda item: (item[1], corpus[item[0]]))[0]
        for english, options in across.items()
    }
    stats["reserva"] = len(fallback)
    return glossary, fallback, dict(stats)


def read_senses(tsv_path: Path) -> dict[str, str]:
    """Read this project's own translations of senses absent from the material.

    They are keyed by the English sense alone: these are compound glosses that
    the Spanish translation never covered, so there is no corpus usage to key
    them against. Their wording follows the glossary wherever a term recurs.
    """
    senses_es: dict[str, str] = {}
    if not tsv_path.exists():
        return senses_es
    for line in tsv_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        english, _, spanish_text = line.partition("\t")
        if spanish_text:
            senses_es[english.lower()] = spanish_text
    return senses_es


def read_overrides(tsv_path: Path) -> dict[tuple[str, str], str]:
    """Read the hand-made corrections to the glossary, keyed by sense and pos.

    The glossary is derived from the published translation, so it inherits its
    mistakes: a wording the corpus settled wrongly becomes the agreed rendering
    for every word that reuses that sense. These corrections are applied on top,
    and each one carries its reason in the file itself.
    """
    overrides: dict[tuple[str, str], str] = {}
    if not tsv_path.exists():
        return overrides
    for line in tsv_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        english, _, rest = line.partition("\t")
        pos, _, spanish_text = rest.partition("\t")
        if spanish_text:
            overrides[(english.lower(), pos)] = spanish_text
    return overrides


FAMILY_TABLES = (FamilyCompound, FamilyRoot, FamilyWord, FamilySet, FamilyIdiom)


def family_lemmas(db_session: Session) -> set[str]:
    """Every lemma listed in any family table.

    These are the words a reader meets while browsing a family, so they are
    the ones worth having in Spanish even when the published translation —
    which only covers the early canon — never reached them.
    """
    lemmas: set[str] = set()
    for table in FAMILY_TABLES:
        for family in db_session.execute(select(table)).scalars():
            for match in re.finditer(spanish.FAMILY_ROW, family.html):
                lemmas.add(spanish.unsuperscript(match.group("lemma")))
    return lemmas


def translate_fully_covered(
    db_session: Session,
    lemmas: set[str],
    glossary: dict[tuple[str, str], str],
    fallback: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Translate the words whose every sense is in the glossary."""
    translated: dict[str, dict[str, str]] = {}
    headwords = (
        db_session.execute(select(DpdHeadword).where(DpdHeadword.lemma_1.in_(lemmas)))
        .scalars()
        .all()
    )
    for headword in sorted(headwords, key=lambda hw: hw.lemma_1):
        lemma = headword.lemma_1
        with spanish.suppressed():
            english = {field: getattr(headword, field) for field in FIELDS}
            fingerprint = (headword.pos, headword.construction_summary)

        fields: dict[str, str] = {}
        covered = True
        for field in FIELDS:
            parts = senses(english[field])
            if not parts:
                fields[field] = ""
                continue
            spanish_parts = [
                glossary.get((part.lower(), headword.pos)) or fallback.get(part.lower())
                for part in parts
            ]
            if any(part is None for part in spanish_parts):
                covered = False
                break
            # Two English senses can land on the same Spanish wording — near
            # synonyms the glossary settled the same way — and the entry would
            # then say it twice. Keep the first, in order.
            fields[field] = "; ".join(
                dict.fromkeys(part for part in spanish_parts if part)
            )

        if covered and any(fields.values()):
            fields["pos"], fields["construction"] = fingerprint
            translated[lemma] = fields
    return translated


def write_tsv(rows: list[tuple[str, ...]], path: Path, header: str) -> None:
    """Write rows to a TSV, sorted, with a comment header."""
    lines = [header] + ["\t".join(row) for row in sorted(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    pr.tic()
    pr.yellow_title("building spanish glossary")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glossary", type=Path, default=GLOSSARY_TSV)
    parser.add_argument("--output", type=Path, default=COMPOUNDS_TSV)
    parser.add_argument("--senses", type=Path, default=SENSES_TSV)
    parser.add_argument("--overrides", type=Path, default=OVERRIDES_TSV)
    args = parser.parse_args()

    pth = ProjectPaths()
    db_session = get_db_session(pth.dpd_db_path)
    sidecar = spanish.load()
    if not sidecar:
        pr.red("no sidecar found, run build_spanish_sidecar.py first")
        return

    pr.green_tmr("aligning senses")
    glossary, fallback, stats = build_glossary(db_session, sidecar)
    pr.yes(len(glossary))
    pr.summary("acepciones usables", len(glossary))
    for name in (
        "acordadas",
        "cosmeticas",
        "dominantes",
        "homogeneizadas",
        "sin_resolver",
        "reordenados",
        "reserva",
    ):
        pr.summary(name, stats.get(name, 0))

    overrides = read_overrides(args.overrides)
    if overrides:
        corrected = sum(1 for key in overrides if glossary.get(key) != overrides[key])
        glossary.update(overrides)
        pr.summary("correcciones manuales", corrected)

    pr.green_tmr("writing glossary")
    write_tsv(
        [
            (english, pos, spanish_text)
            for (english, pos), spanish_text in glossary.items()
        ],
        args.glossary,
        "# acepción inglesa\tcategoría\tacepción española — derivado, regenerable",
    )
    pr.yes(len(glossary))

    own = read_senses(args.senses)
    if own:
        # this project's own translations, never overriding the material's
        for english, spanish_text in own.items():
            fallback.setdefault(english, spanish_text)
        pr.summary("acepciones propias", len(own))

    pr.green_tmr("reading family tables")
    lemmas = family_lemmas(db_session)
    # A lemma is pending unless a human translated it. Its own previous output
    # does not count: the sidecar is rebuilt from this file, so treating a
    # generated entry as already done would leave nothing to translate on the
    # second run and blank the file it was built from.
    pending = {
        lemma
        for lemma in lemmas
        if lemma not in sidecar
        or sidecar[lemma].get(spanish.ORIGIN) == spanish.GENERATED
    }
    pr.yes(len(pending))

    pr.green_tmr("translating what the glossary covers")
    translated = translate_fully_covered(db_session, pending, glossary, fallback)
    pr.yes(len(translated))

    write_tsv(
        [
            (lemma, *(fields[column] for column in spanish.COLUMNS))
            for lemma, fields in translated.items()
        ],
        args.output,
        "# lemma_1\t"
        + "\t".join(spanish.COLUMNS)
        + " — generado por reuso del glosario, sin criterio humano",
    )
    pr.summary("lemas pendientes", len(pending))
    pr.summary("traducidos por reuso", len(translated))
    pr.toc()


if __name__ == "__main__":
    main()
