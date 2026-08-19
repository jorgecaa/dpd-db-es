#!/usr/bin/env python3

"""Gather everything a translator needs for the headwords still showing English.

The Spanish overlay serves the material of dpd-dictionary-es and falls back to
English where it has nothing. What is left over is not one problem but four —
a lemma the material never had, a lemma whose fingerprint no longer matches, a
row translated only in `meaning_2`, and a row whose `meaning_2` the DPD has
since dropped — and all four end the same way for a reader: an English gloss in
a Spanish dictionary.

This writes one bundle per lemma with the evidence needed to translate it:

    the DPD entry          lemma, pos, grammar, construction, the gloss itself
    canonical usage        the examples with their suttas, and the commentary
    other dictionaries     NCPED, PTS, Cone, CPD — Pāḷi-English only
    the project's own      how dpd-dictionary-es already renders each term

The last one is the point. Terminological consistency is not a rule to be
invented for this batch: it is settled in the published material, and can be
read back out of it by aligning each translated sense with its English. Where
the two agree on sense count — 48,749 entries, 104,103 sense pairs — the
alignment is exact.

What is deliberately absent: the Spanish dictionaries and the Sanskrit ones.
Both are witnesses, for checking a finished translation, not sources to
translate from; Margaret Cone's rule for the Sanskrit, and confirmed for the
Spanish by measurement — the Pāḷi-Spanish dictionary covers a quarter of these
lemmas and carries one sense per lemma, so it cannot arbitrate a numbered sense.

Usage:
    uv run python scripts/build/spanish_pending.py --out-dir <dir> [--limit N]
"""

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from db.db_helpers import get_db_session
from db.models import DpdHeadword
from tools import spanish
from tools.paths import ProjectPaths
from tools.printer import printer as pr

# Sense-level pairs are only trustworthy when both sides split the same way, and
# short glosses are the ones that read as term equivalences rather than phrases.
TERM_MAX_WORDS = 3
TERM_MIN_LENGTH = 4
TERM_VARIANTS = 3

# Grammatical words carry no terminology and would match everything.
STOPWORDS = frozenset(
    "the of and with from that this into not for who was were been being having".split()
)

# Pāḷi-English only. The Sanskrit dictionaries in the same store are witnesses.
PALI_ENGLISH = ("Combined Pali", "Margaret Cone", "Critical")
GLOSS_MAX_CHARS = 420
GLOSS_MAX_COUNT = 4

SENSE_NUMBER = re.compile(r"\s+\d+(\.\d+)?$")
TAG = re.compile(r"<[^>]+>")


def bare_lemma(lemma: str) -> str:
    """`addhāna 3` is `addhāna` to any dictionary but this one."""
    return SENSE_NUMBER.sub("", lemma).strip()


def content_terms(gloss: str) -> set[str]:
    """The words of a gloss that can carry a terminological decision."""
    words = {
        word.lower().strip("(),;.")
        for word in gloss.split()
        if len(word) > TERM_MIN_LENGTH
    }
    return words - STOPWORDS


class Terminology:
    """How the published material already renders each English term.

    Built from the entries whose Spanish and English split into the same number
    of senses, which makes the pairing exact rather than guessed. Short glosses
    are kept apart from long ones and consulted first: `unskilful` → `inhábil`
    is a term equivalence, while a ten-word sense that happens to contain the
    word is context.
    """

    def __init__(self) -> None:
        self.short: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self.long: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self.pairs = 0

    def add(self, english: str, spanish_text: str) -> None:
        target = self.short if len(english.split()) <= TERM_MAX_WORDS else self.long
        self.pairs += 1
        for term in content_terms(english):
            target[term][spanish_text] += 1

    def lookup(self, gloss: str) -> dict[str, list[dict[str, Any]]]:
        found: dict[str, list[dict[str, Any]]] = {}
        for term in content_terms(gloss):
            renderings = self.short.get(term) or self.long.get(term)
            if renderings:
                found[term] = [
                    {"español": text, "veces": count}
                    for text, count in renderings.most_common(TERM_VARIANTS)
                ]
        return found


def classify(headword: DpdHeadword, stored: dict[str, str] | None) -> str | None:
    """Why this headword still shows English, or None when it does not.

    Evaluated through the overlay's own functions rather than by reading the
    sidecar directly, so this always agrees with what the dictionary serves.
    """
    if stored is None:
        return "sin fila"
    if not spanish.fingerprint_matches(headword, stored):
        return "huella distinta"
    if stored["meaning_1"].strip() or spanish.promoted_meaning_2(headword, stored):
        return None
    return "sin acepción principal"


def build_terminology(
    db_session, translations: dict[str, dict[str, str]]
) -> Terminology:
    """Read the terminology back out of the published material."""
    pr.green_tmr("alineando el material")
    terminology = Terminology()
    aligned = 0
    for headword in db_session.query(DpdHeadword).yield_per(2000):
        stored = translations.get(headword.lemma_1)
        if (
            stored is None
            or stored["origin"] != "material"
            or not stored["meaning_1"].strip()
        ):
            continue
        if not spanish.fingerprint_matches(headword, stored):
            continue
        with spanish.suppressed():
            english = (headword.meaning_1 or "").strip()
        english_senses = [part.strip() for part in english.split(";") if part.strip()]
        spanish_senses = [
            part.strip() for part in stored["meaning_1"].split(";") if part.strip()
        ]
        if not english_senses or len(english_senses) != len(spanish_senses):
            continue
        aligned += 1
        for pair in zip(english_senses, spanish_senses):
            terminology.add(*pair)
    pr.yes(terminology.pairs)
    pr.summary("entradas alineadas", aligned)
    return terminology


def read_glosses(lemmas: list[str], data_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Look every lemma up in the Pāḷi-English dictionaries, in one call.

    `sdcv` emits one JSON document per word rather than a single array, so the
    stream is decoded one document at a time. A missing dictionary store is not
    fatal: the bundles are simply built without external glosses.
    """
    if not data_dir.is_dir():
        pr.amber(f"sin diccionarios externos en {data_dir}")
        return {}
    pr.green_tmr("consultando diccionarios")
    try:
        result = subprocess.run(
            ["sdcv", "--data-dir", str(data_dir), "-n", "--json", *lemmas],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        pr.no("falló")
        pr.amber(f"sdcv no disponible: {error}")
        return {}

    decoder, position, entries = json.JSONDecoder(), 0, []
    text = result.stdout
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            break
        document, position = decoder.raw_decode(text, position)
        entries.extend(document)

    glosses: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for entry in entries:
        if not any(name in entry["dict"] for name in PALI_ENGLISH):
            continue
        clean = re.sub(r"\s+", " ", TAG.sub(" ", entry["definition"])).strip()
        glosses[entry["word"].lower()].append(
            {"diccionario": entry["dict"], "texto": clean[:GLOSS_MAX_CHARS]}
        )
    pr.yes(len(glosses))
    return {word: found[:GLOSS_MAX_COUNT] for word, found in glosses.items()}


def bundle(
    headword: DpdHeadword, english: str, reason: str, terminology: Terminology
) -> dict[str, Any]:
    """Everything known about one pending headword, as the translator sees it."""
    examples = [
        {
            "pali": text.strip(),
            "sutta": (sutta or "").strip(),
            "fuente": (source or "").strip(),
        }
        for text, sutta, source in (
            (headword.example_1, headword.sutta_1, headword.source_1),
            (headword.example_2, headword.sutta_2, headword.source_2),
        )
        if text.strip()
    ]
    with spanish.suppressed():
        literal = (headword.meaning_lit or "").strip()
    return {
        "lema": headword.lemma_1,
        "pos": headword.pos,
        "gramatica": headword.grammar,
        "construccion": headword.construction_summary,
        "acepcion_inglesa": english,
        "literal_ingles": literal,
        "ejemplos": examples,
        "comentario": headword.commentary.strip()[:600],
        "terminologia_del_material": terminology.lookup(english),
        "motivo": reason,
    }


def collect(
    db_session,
    translations: dict[str, dict[str, str]],
    dicts_dir: Path,
    limit: int | None,
) -> list[dict]:
    """Every headword the overlay still serves in English, with its evidence."""
    terminology = build_terminology(db_session, translations)
    pr.green_tmr("reuniendo pendientes")
    pending: list[tuple[DpdHeadword, str, str]] = []
    for headword in db_session.query(DpdHeadword).yield_per(2000):
        with spanish.suppressed():
            english = (headword.meaning_1 or "").strip()
        if not english:
            continue
        reason = classify(headword, translations.get(headword.lemma_1))
        if reason is None:
            continue
        pending.append((headword, english, reason))
        if limit and len(pending) >= limit:
            break
    pr.yes(len(pending))

    glosses = read_glosses([bare_lemma(h.lemma_1) for h, _, _ in pending], dicts_dir)
    bundles = []
    for headword, english, reason in pending:
        item = bundle(headword, english, reason, terminology)
        item["glosas_externas"] = glosses.get(bare_lemma(headword.lemma_1), [])
        bundles.append(item)
    return bundles


def write_batches(bundles: list[dict], out_dir: Path, per_batch: int) -> list[Path]:
    """Split the bundles into files of a size one translator can hold at once."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for number, start in enumerate(range(0, len(bundles), per_batch), start=1):
        path = out_dir / f"pendientes_{number:03d}.json"
        path.write_text(
            json.dumps(
                bundles[start : start + per_batch], ensure_ascii=False, indent=1
            ),
            encoding="utf-8",
        )
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path, required=True, help="where to write the batches"
    )
    parser.add_argument("--per-batch", type=int, default=30, help="headwords per batch")
    parser.add_argument("--limit", type=int, help="stop after this many headwords")
    parser.add_argument("--dicts", type=Path, help="stardict store for sdcv")
    args = parser.parse_args()

    pr.tic()
    pr.yellow_title("acepciones pendientes de traducir")
    if not spanish.is_enabled():
        pr.red("el overlay español está apagado en config.ini; no hay nada que medir")
        return

    paths = ProjectPaths()
    db_session = get_db_session(paths.dpd_db_path)
    translations = spanish.load()
    dicts_dir = args.dicts or paths.dpd_db_path.parent / "resources" / "stardict"
    bundles = collect(db_session, translations, dicts_dir, args.limit)

    reasons = Counter(item["motivo"] for item in bundles)
    for reason, count in reasons.most_common():
        pr.summary(reason, count)
    pr.summary("con ejemplos", sum(1 for i in bundles if i["ejemplos"]))
    pr.summary("con glosa externa", sum(1 for i in bundles if i["glosas_externas"]))
    pr.summary(
        "con terminología", sum(1 for i in bundles if i["terminologia_del_material"])
    )

    written = write_batches(bundles, args.out_dir, args.per_batch)
    pr.summary("tandas escritas", len(written))
    pr.toc()


if __name__ == "__main__":
    main()
