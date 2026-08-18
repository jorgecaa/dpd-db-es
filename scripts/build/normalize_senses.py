#!/usr/bin/env python3

"""Normalise this project's translations to Spanish orthographic norms.

Nine translators working in parallel each settled capitalisation on their own,
because the brief never mentioned it. What came out is English habit carried
into Spanish: title case, which Spanish does not use. It is «La cesta de la
conducta», not «La Cesta de la Conducta»; «los cuatro grandes reyes», not «los
Cuatro Grandes Reyes»; «un despierto», not «un Despierto».

An earlier version of this script settled the question by corpus frequency, the
way terminology was settled. That was wrong, and measurably so: the published
translation writes *Libro*, *Sutta* and *Pāḷi* with capitals, all three against
the norm — divisions of a work, generic common nouns and language names are
lower case in Spanish. Following it would have introduced some 57 errors to fix
15. The corpus is a machine translation, not an orthographic authority.

So the list below is explicit and short, and every line is arguable on its own.
Two candidates were deliberately left out: `Ven.` (abbreviation of *Venerable*)
and `Palma Christi` (traditional Latin name), which keep their capitals.

Usage:
    uv run python scripts/build/normalize_senses.py [--dry-run]
"""

import argparse
import re
from pathlib import Path

from tools.printer import printer as pr

SENSES_TSV = Path(__file__).parent / "senses_es.tsv"
COMPOUNDS_TSV = Path(__file__).parent / "compounds_es.tsv"
WORD = re.compile(r"[\wāīūṅñṭḍṇḷṃśṣĀĪŪṄÑṬḌṆḶṂŚṢ]+")

REPLACEMENTS = {
    # title case does not exist in Spanish: only the first word takes a capital
    "Cesta": "cesta",
    "Conducta": "conducta",
    "Cuatro": "cuatro",
    "Grandes": "grandes",
    "Reyes": "reyes",
    "Treinta": "treinta",
    "Tres": "tres",
    "Fenómenos": "fenómenos",
    "Comentario": "comentario",
    "Tesoros": "tesoros",
    # epithets are lower case in Spanish
    "Despierto": "despierto",
    "Iluminado": "iluminado",
    # not a biblical context
    "Escrituras": "escrituras",
    # common noun, like «uposatha»
    "Kathina": "kathina",
    # botanical convention: genus capitalised, species not
    "Latifolia": "latifolia",
    # mid-sentence, inside the parenthesis
    "Precedido": "precedido",
    # Divisions of a work, generic common nouns and language names are lower
    # case in Spanish; the corpus capitalises all three. Only a gloss that opens
    # with them keeps the capital, which the position check below handles.
    "Libro": "libro",
    "Sutta": "sutta",
    "Pāḷi": "pāḷi",
    # Spanish exonym. «Himalayas» is deliberately left as it is: Spanish says
    # «el Himalaya», but the plural is kept here for teaching purposes, so that
    # a reader coming from the English sees the same shape. (Swapping the noun
    # alone would in any case give «en los Himalaya», with the article no longer
    # agreeing — the fix is to the phrase, not to a word.)
    "Babylon": "Babilonia",
    # both forms are correct Spanish; this is the one the corpus uses
    "jainas": "jainistas",
    # proper name
    "mahāvīra": "Mahāvīra",
    # the Pāḷi word for awakening, and the tree named after it, are common
    # nouns: «árbol bodhi». Capitalised only as a person's name — Bhikkhu Bodhi
    # — which PERSONAL_NAME below keeps safe.
    "Bodhi": "bodhi",
    "Bo": "bo",
    # Spanish does not use the English reverential capital: «su iluminación»
    "Su": "su",
}

# A capital that follows one of these is somebody's name, not the common noun.
PERSONAL_NAME = ("Bhikkhu", "Ven.", "Ven", "venerable", "Venerable", "monje")

# In «Vinaya, Sutta y Abhidhamma» the three are the names of the three baskets,
# listed in parallel. Lower-casing only the one that happens to be in the map
# would leave two capitals and one lower case in the same enumeration, which
# reads worse than the anglicism it fixes.
KEEP_IN_LIST = ("Vinaya,", "Vinaya", "Abhidhamma")

# «Sutta» standing on its own is the name of the basket and keeps its capital;
# it goes lower case only when it trails the name of a particular discourse
# («el Satipaṭṭhāna sutta»). These two are the first kind: the word opens the
# title of a work, so the capital belongs to the name, not to the common noun.
PHRASES = {
    "sutta Nipāta": "Sutta Nipāta",
    "sutta Piṭaka": "Sutta Piṭaka",
    "vinaya Piṭaka": "Vinaya Piṭaka",
    "libro Piṭaka": "Libro Piṭaka",
}


# «Tres tesoros» is a designation, not the title of a work, so it takes no
# capital even at the head of the gloss. Titles like «La cesta de la conducta»
# do keep theirs, which is why this is a list and not a rule.
START_LOWER = {"Tres"}


def normalise(spanish: str) -> tuple[str, list[str]]:
    """Rewrite one translation, returning it and what changed."""
    changes: list[str] = []

    def replace(match: "re.Match[str]") -> str:
        word = match.group(0)
        target = REPLACEMENTS.get(word)
        if target is None:
            return word
        preceding = spanish[: match.start()].rstrip().rsplit(" ", 1)
        if preceding and preceding[-1] in PERSONAL_NAME:
            return word
        if preceding and preceding[-1] in KEEP_IN_LIST:
            return word
        # the first word of a line keeps its capital, unless it heads a
        # designation rather than a title
        if match.start() == 0 and target[0].islower() and word not in START_LOWER:
            return word
        changes.append(f"{word} → {target}")
        return target

    fixed = WORD.sub(replace, spanish)
    for wrong, right in PHRASES.items():
        if wrong in fixed:
            fixed = fixed.replace(wrong, right)
            changes.append(f"{wrong} → {right}")
    return fixed, changes


def main() -> None:
    pr.tic()
    pr.yellow_title("normalising to spanish norms")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--senses", type=Path, default=SENSES_TSV)
    parser.add_argument("--compounds", type=Path, default=COMPOUNDS_TSV)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # `senses_es.tsv` is what this project wrote; `compounds_es.tsv` is what it
    # publishes, and part of that wording is quoted from the corpus. The quote
    # is normalised too — an entry served under our own name carries our
    # orthography — while the corpus's own entries are never touched.
    for path, campos in ((args.senses, (1,)), (args.compounds, (1, 2, 3))):
        if not path.exists():
            continue
        pr.green_tmr(path.name)
        lines, touched = [], []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                lines.append(line)
                continue
            columnas = line.split("\t")
            for i in campos:
                if i < len(columnas):
                    fixed, changes = normalise(columnas[i])
                    if changes:
                        touched.append((columnas[i], fixed))
                        columnas[i] = fixed
            lines.append("\t".join(columnas))
        pr.yes(len(touched))
        for before, after in touched[:8]:
            pr.white(f"  {before[:70]}")
            pr.green(f"  {after[:70]}")
        if not args.dry_run:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pr.green_title("dry run, nothing written" if args.dry_run else "escrito")
    pr.toc()


if __name__ == "__main__":
    main()
