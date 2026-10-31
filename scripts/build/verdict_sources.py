"""Build the two lookups the verdict needs faster than the ORM can serve them.

    uv run python scripts/build/verdict_sources.py

It writes `shared_data/verdict_sources.db`, a small SQLite file with two indexed tables:

    folded(folded, loose, spelling, attested)   spelling -> the ASCII fold that reaches it
    derivation(lemma, construction)             lemma    -> the morphology it is built from
    headword(spelling, lemma)                   spelling -> the lemma it belongs to

Both hold what is already in `dpd.db`, and the point is that they hold it **ready**:

- **The fold.** Correcting a spelling needs every lemma and every inflected form folded to
  ASCII. Built in memory it is 458.665 `unidecode` calls, about 4 s and 70 MB — for one
  question. Indexed, the same question is a lookup.
- **The derivation.** The analyser reads `dpd_headwords.stem` and `pattern` through the ORM:
  2,38 s and 252 MB to build 89.587 objects, measured. The same rows through `sqlite3` are
  0,32 s and 34 MB, and the verdict only ever wants the `construction` of one lemma.

`db/backup_tsv/` is not the source of truth and neither is this: both are built from the
live `dpd.db`, which stays the one thing that is hand-edited. Regenerate after any change
to the headwords — the folded spellings and the constructions change with them.

The `attested` column is 1 when the spelling is a form the corpus carries with a headword
behind it, and 0 when it is only a lemma. It is what puts `viññāṇakkhandha` before a bare
citation form when both fold to the same string.
"""

from __future__ import annotations

import re
import sqlite3

from unidecode import unidecode

from tools.paths import ProjectPaths
from tools.pali_verdict import loose_fold
from tools.printer import printer as pr

#: Mirrors `DpdHeadword.lemma_clean`: the sense number is not part of the word.
_SENSE_NUMBER = re.compile(r" \d.*$")

_TABLES = (
    "create table folded (folded text not null, loose text not null, "
    "spelling text not null, attested integer not null)",
    "create table derivation (lemma text not null, construction text not null)",
    "create table headword (spelling text not null, lemma text not null)",
)


def build(paths: ProjectPaths) -> tuple[int, int, int]:
    """(folded rows, derivations, spelling->lemma links). Reads `dpd.db`."""
    source = sqlite3.connect(f"file:{paths.dpd_db_path}?mode=ro&immutable=1", uri=True)
    destination = paths.verdict_sources_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    sidecar = sqlite3.connect(destination)
    for statement in _TABLES:
        sidecar.execute(statement)

    seen: dict[tuple[str, str], tuple[str, int]] = {}
    for (lemma,) in source.execute("select lemma_1 from dpd_headwords"):
        word = _SENSE_NUMBER.sub("", lemma)
        if " " not in word and word:
            seen[(unidecode(word).lower(), loose_fold(word))] = (word, 0)

    for (key,) in source.execute(
        "select lookup_key from lookup where headwords is not null and headwords != ''"
    ):
        if " " in key or not key or any(c.isupper() for c in key):
            continue
        seen[(unidecode(key).lower(), loose_fold(key))] = (key, 1)

    sidecar.executemany(
        "insert into folded (folded, loose, spelling, attested) values (?, ?, ?, ?)",
        [
            (folded, loose, spelling, attested)
            for (folded, loose), (spelling, attested) in seen.items()
        ],
    )

    rows = [
        (lemma, construction or "")
        for lemma, construction in source.execute(
            "select lemma_1, construction from dpd_headwords"
        )
    ]
    sidecar.executemany(
        "insert into derivation (lemma, construction) values (?, ?)", rows
    )

    # The link the fast path was missing: a spelling reaches its lemma without the ORM, so
    # `jotipālo` finds `joti + pāla` and the verdict can say its spelling is not derivable.
    headwords = [
        (spelling, lemma)
        for spelling, lemma in source.execute(
            "select l.lookup_key, h.lemma_1 from lookup l "
            "join json_each(l.headwords) j join dpd_headwords h on h.id = j.value "
            "where l.headwords is not null and l.headwords != ''"
        )
    ]
    sidecar.executemany(
        "insert into headword (spelling, lemma) values (?, ?)", headwords
    )

    sidecar.execute("create index folded_key on folded (folded)")
    sidecar.execute("create index folded_loose on folded (loose)")
    sidecar.execute("create index derivation_lemma on derivation (lemma)")
    sidecar.execute("create index headword_spelling on headword (spelling)")
    sidecar.commit()
    sidecar.execute("vacuum")
    sidecar.close()
    source.close()
    return len(seen), len(rows), len(headwords)


def main() -> None:
    paths = ProjectPaths()
    pr.green_title("building the verdict's sidecar")
    folded, derivations, links = build(paths)
    pr.summary("folded spellings", str(folded))
    pr.summary("derivations", str(derivations))
    pr.summary("spelling -> lemma", str(links))
    size = paths.verdict_sources_path.stat().st_size / 1e6
    pr.summary("written", f"{paths.verdict_sources_path} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
