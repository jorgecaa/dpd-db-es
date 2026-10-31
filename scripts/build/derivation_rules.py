#!/usr/bin/env python3
"""Induce the productive derivation rules from the dictionary's own constructions.

Every derived headword carries its formation in the `construction` column (`dhamma + *ika`,
`na > a + kaṇṭa + aka`), and that is the ground truth this script harvests. A triple is
kept when the element before the suffix is a word the dictionary knows:

    dhamma + *ika   ->  dhamma  (stem `dhamm`)   ->  dhammika   (stem `dhammik`)
    dhamma + *tā    ->  dhamma  (stem `dhamm`)   ->  dhammatā   (stem `dhammat`)

Each triple becomes a rule keyed by (suffix, last character of the base's stem) and
mapping (what is appended to the base's stem, the inflection patterns observed for it):

    ('ika', 'm') : 'ik'  for patterns {a adj, a masc, a nt}

The patterns are the constraint that makes inversion precise: a form is only read as a
derivation when the paradigm ending stripped from it belongs to a pattern the rule was
actually observed with. The base's own stem is not listed — it comes from the lexicon at
analysis time, which is what lets a *novel* stem be derived from a known word.

Writes `shared_data/derivation_rules.tsv` in canonical Pāḷi sort order.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tools.pali_sort_key import pali_sort_key
from tools.paths import ProjectPaths
from tools.printer import printer as pr

# Mirrors `DpdHeadword.lemma_clean` and `exporter/webapp/preloads.py::clean_lemma`.
_SENSE_NUMBER = re.compile(r" \d.*$")

# Stem markers: `!` marks an inflected-form entry, `*` an irregular stem whose ending is
# the whole form; neither can take part in a productive derivation.
_STEM_MARKERS = re.compile(r"[!*]")

# An element that is a root, or that carries a phonetic-change annotation, is not a word
# the derivation can be anchored on.
_NOT_A_WORD = re.compile(r"[>\[\]()]|^√")


@dataclass
class GlobalVars:
    pth: ProjectPaths = field(default_factory=ProjectPaths)
    # Headwords whose derivations must not be learned from. The held-out evaluation in
    # temp/derivation_eval.py sets this to keep the rules free of its test entries.
    exclude_ids: set[int] = field(default_factory=set)
    output_path: Path | None = None
    lemma_stems: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    rules: dict[tuple[str, str, str, str], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    suffixes: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)


def load_lexicon(g: GlobalVars) -> None:
    """Map every lemma to the stems the dictionary records for it."""
    connection = sqlite3.connect(f"file:{g.pth.dpd_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute("select lemma_1, stem from dpd_headwords").fetchall()
    finally:
        connection.close()

    for lemma_1, stem in rows:
        if not stem or stem in ("-", "*") or "!" in stem:
            continue
        g.lemma_stems[_SENSE_NUMBER.sub("", lemma_1)].add(_STEM_MARKERS.sub("", stem))

    pr.green_tmr("lemmas with a usable stem")
    pr.yes(len(g.lemma_stems))


def harvest(g: GlobalVars) -> None:
    """Turn every construction into (suffix, base stem, derived stem) rules."""
    connection = sqlite3.connect(f"file:{g.pth.dpd_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select id, construction, suffix, derivative, stem, pattern "
            "from dpd_headwords "
            "where suffix != '' and construction != '' "
            "and stem not in ('-', '*') and pattern != ''"
        ).fetchall()
    finally:
        connection.close()

    pr.green_tmr("headwords with a suffix and a pattern")
    pr.yes(len(rows))

    for headword_id, construction, suffix, _derivative, stem, pattern in rows:
        if headword_id in g.exclude_ids:
            g.skipped["held out"] += 1
            continue
        clean_suffix = suffix.lstrip("*")
        found = False

        # A construction can have several lines: the second shows the surface step of a
        # secondary derivation (`na > a + √kaṅkh + ā + ī` / `akaṅkhā + ī`).
        for line in construction.split("\n"):
            elements = [element.strip() for element in line.split(" + ")]
            if len(elements) < 2:
                continue
            base = elements[-2].lstrip("*").strip()
            if not base or _NOT_A_WORD.search(base):
                g.skipped["base is not a word"] += 1
                continue
            base_stems = g.lemma_stems.get(base)
            if not base_stems:
                g.skipped["base is not in the dictionary"] += 1
                continue

            for base_stem in base_stems:
                # The base stem can sit behind a prefix (`na > a + kaṇṭa + aka`), so look
                # for it anywhere and keep everything appended after it.
                position = stem.rfind(base_stem)
                if position < 0 or position + len(base_stem) >= len(stem):
                    continue
                added = stem[position + len(base_stem) :]
                g.rules[(clean_suffix, base_stem[-1], added, pattern)] += 1
                found = True

        if found:
            g.suffixes[clean_suffix] += 1
        else:
            g.skipped["no stem relation"] += 1

    pr.green_tmr("rules (suffix, base_last, added, pattern)")
    pr.yes(len(g.rules))


def save(g: GlobalVars) -> None:
    """Write the rules in canonical sort order."""
    lines = ["suffix\tbase_last\tadded\tpattern\tcount"]
    for (suffix, base_last, added, pattern), count in g.rules.items():
        lines.append(f"{suffix}\t{base_last}\t{added}\t{pattern}\t{count}")

    header, body = lines[0], lines[1:]
    body.sort(
        key=lambda line: (
            pali_sort_key(line.split("\t")[0]),  # suffix
            pali_sort_key(line.split("\t")[1]),  # base_last
            -int(line.split("\t")[4]),  # count, most frequent first
            pali_sort_key(line.split("\t")[2]),  # added
            pali_sort_key(line.split("\t")[3]),  # pattern
        )
    )

    target = g.output_path or g.pth.derivation_rules_path
    target.write_text("\n".join([header, *body]) + "\n", encoding="utf-8")
    pr.green_tmr("rules written")
    pr.yes(len(body))
    pr.green_tmr("suffixes covered")
    pr.yes(len(g.suffixes))


def main() -> None:
    pr.tic()
    pr.yellow_title("inducing derivation rules")
    g = GlobalVars()
    load_lexicon(g)
    harvest(g)
    save(g)
    for reason, count in g.skipped.most_common():
        pr.green_tmr(f"skipped: {reason}")
        pr.yes(count)
    pr.green_tmr("written to")
    pr.no(str(g.output_path or g.pth.derivation_rules_path))
    pr.toc()


if __name__ == "__main__":
    main()
