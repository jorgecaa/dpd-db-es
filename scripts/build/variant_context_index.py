#!/usr/bin/env python3
"""Index the attested word that precedes each word, from the two sources the database has.

**Variant readings** (`lookup.variant`). Per lookup key, the readings of the four editions
that differ from the main text, each with the phrase it was found in. In the overwhelming
majority of cases that phrase is `<word before> <the key>`:

    tattha kā      'kā tattha (sī.)'      CST
    hi kā          'te – sīmu, syā'       BJT

The preceding word is what makes the variant — it is its ending that elides or assimilates
at the junction — so the column is a by-product that happens to be a context index. It is
narrow, though: only words that have editorial variants carry it, which is 4.6% of the
lookup keys and none of the everyday vocabulary (`dhamma` and `viññāṇa` among them).

**Examples** (`dpd_headwords.example_1`/`example_2`). ~52.000 headwords carry one or two
sentences from the canon with the headword emboldened, and the word before the bold span is
the attested neighbour. This covers 79.9% of them and does include the everyday words, so
the two sources are complementary.

Writes `shared_data/variant_context_index.tsv` in canonical Pāḷi sort order, columns
`word`, `before`, `source`, `provenance`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tools.pali_sort_key import pali_sort_key
from tools.paths import ProjectPaths
from tools.printer import printer as pr

# Punctuation that trails a word inside a phrase: `buddho,` `kā.` `dhammaṃ;`
_TRAILING = re.compile(r"[.,;:!?'\")\]}]+$")
_LEADING = re.compile(r"^[(\[{'\"]+")

# A variant note can span lines (`na anattamano na āhatacitto ... – syā`); written raw it
# would break the row count of the TSV. Any run of whitespace becomes one space.
_WHITESPACE = re.compile(r"\s+")

# Mirrors `DpdHeadword.lemma_clean` and `exporter/webapp/preloads.py::clean_lemma`.
_SENSE_NUMBER = re.compile(r" \d.*$")


@dataclass
class GlobalVars:
    pth: ProjectPaths = field(default_factory=ProjectPaths)
    output_path: Path | None = None
    # (word, before, source, provenance)
    rows: set[tuple[str, str, str, str]] = field(default_factory=set)
    skipped: Counter[str] = field(default_factory=Counter)


def clean(token: str) -> str:
    """Strip the punctuation a word carries inside a phrase."""
    return _TRAILING.sub("", _LEADING.sub("", token))


def word_before(example: str) -> str | None:
    """The word preceding the bold span of an example sentence, if there is one."""
    position = example.find("<b>")
    if position < 0:
        return None
    tokens = [clean(token) for token in example[:position].split()]
    tokens = [token for token in tokens if token]
    return tokens[-1] if tokens else None


def harvest(g: GlobalVars) -> None:
    connection = sqlite3.connect(f"file:{g.pth.dpd_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select lookup_key, variant from lookup where variant != ''"
        ).fetchall()
    finally:
        connection.close()

    pr.green_tmr("lookup keys with variant readings")
    pr.yes(len(rows))

    for word, raw in rows:
        try:
            editions: dict[str, dict[str, list[list[str]]]] = json.loads(raw)
        except (TypeError, ValueError):
            g.skipped["unparseable"] += 1
            continue

        for edition, books in editions.items():
            for book, readings in books.items():
                for reading in readings:
                    if not reading:
                        continue
                    phrase, *rest = reading
                    note = _WHITESPACE.sub(" ", rest[0] if rest else "").strip()
                    tokens = [clean(token) for token in phrase.split()]
                    tokens = [token for token in tokens if token]
                    if len(tokens) < 2:
                        g.skipped["no neighbouring word"] += 1
                        continue
                    if tokens[-1] != word:
                        g.skipped["the key is not the last word"] += 1
                        continue
                    g.rows.add(
                        (
                            word,
                            tokens[-2],
                            "variant",
                            f"{edition}/{book} {note}".strip(),
                        )
                    )

    pr.green_tmr("context rows")
    pr.yes(len(g.rows))


def harvest_examples(g: GlobalVars) -> None:
    """Take the neighbour from the sentence each headword is illustrated with."""
    connection = sqlite3.connect(f"file:{g.pth.dpd_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select lemma_1, example_1, example_2, sutta_1, sutta_2 from dpd_headwords "
            "where example_1 != '' or example_2 != ''"
        ).fetchall()
    finally:
        connection.close()

    pr.green_tmr("headwords with an example")
    pr.yes(len(rows))

    for lemma_1, example_1, example_2, sutta_1, sutta_2 in rows:
        word = _SENSE_NUMBER.sub("", lemma_1)
        for example, sutta in ((example_1, sutta_1), (example_2, sutta_2)):
            if not example or "<b>" not in example:
                continue
            before = word_before(example)
            if not before:
                g.skipped["example starts with the word"] += 1
                continue
            g.rows.add((word, before, "example", sutta or ""))

    pr.green_tmr("context rows after examples")
    pr.yes(len(g.rows))


def save(g: GlobalVars) -> None:
    lines = ["word\tbefore\tsource\tprovenance"]
    for row in g.rows:
        # Every field is sanitised, not just the note: a sutta name or a reading can carry
        # a line break too, and one embedded newline breaks the row count of the file.
        lines.append("\t".join(_WHITESPACE.sub(" ", field).strip() for field in row))

    header, body = lines[0], lines[1:]
    body.sort(
        key=lambda line: (
            pali_sort_key(line.split("\t")[0]),
            pali_sort_key(line.split("\t")[1]),
            line.split("\t")[2],
            line.split("\t")[3],
        )
    )

    target = g.output_path or g.pth.variant_context_index_path
    target.write_text("\n".join([header, *body]) + "\n", encoding="utf-8")
    pr.green_tmr("rows written")
    pr.yes(len(body))


def main() -> None:
    pr.tic()
    pr.yellow_title("indexing variant contexts")
    g = GlobalVars()
    harvest(g)
    harvest_examples(g)
    save(g)
    for reason, count in g.skipped.most_common():
        pr.green_tmr(f"skipped: {reason}")
        pr.yes(count)
    words = {word for word, _b, _s, _p in g.rows}
    befores = {before for _w, before, _s, _p in g.rows}
    pr.green_tmr("distinct words")
    pr.yes(len(words))
    pr.green_tmr("distinct preceding words")
    pr.yes(len(befores))
    pr.green_tmr("written to")
    pr.no(str(g.output_path or g.pth.variant_context_index_path))
    pr.toc()


if __name__ == "__main__":
    main()
