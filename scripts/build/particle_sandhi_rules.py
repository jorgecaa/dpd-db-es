#!/usr/bin/env python3
"""Induce the particle-sandhi rules from the deconstructor output.

When a word form is followed by one of the six common particles (`api`, `ca`, `eva`,
`iti`, `iva`, `hi`), the two are joined by sandhi: `vihāresu` + `iti` -> `vihāresūti`,
`upagataṃ` + `hi` -> `upagatañhi`. The deconstructor records those joins as two-part
splits in `lookup.deconstructor`, which is the ground truth this script harvests.

Each observation is reduced to a rule keyed by (last character of the base, particle) and
mapping (what remains of the base, what the surface shows there):

    vihāresu + iti -> vihāresūti   =>   ('u', 'iti') : ('u' -> 'ūti')
    upagataṃ + hi -> upagatañhi    =>   ('ṃ', 'hi')  : ('ṃ' -> 'ñhi')
    sahasā   + api -> sahasāpi     =>   ('ā', 'api') : ('' -> 'pi')

Rules observed fewer than `MIN_COUNT` times are dropped: the long tail is deconstructor
noise (a single `'antike' -> 'santikeca'` would otherwise become a "rule").

Writes `shared_data/deconstructor/particle_sandhi_rules.tsv` in canonical Pāḷi sort order.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from sqlite3 import Connection, connect

from tools.pali_sort_key import pali_sort_key
from tools.paths import ProjectPaths
from tools.printer import printer as pr

# The six particles, exactly as scripts/build/api_ca_eva_iti_iva_hi.py recognises them.
PARTICLE_RE = re.compile(r" \+ (api|ca|eva|iti|iva|hi)$")

# Deconstructor splits carry a trailing rule annotation, e.g. "vihāresu + iti [rule]".
RULE_RE = re.compile(r" \[.+")

MIN_COUNT = 3


@dataclass
class GlobalVars:
    pth: ProjectPaths = field(default_factory=ProjectPaths)
    triples: set[tuple[str, str, str]] = field(default_factory=set)
    rules: dict[tuple[str, str], Counter[tuple[str, str]]] = field(
        default_factory=lambda: defaultdict(Counter)
    )


def harvest(g: GlobalVars) -> None:
    """Collect (base, particle, surface) triples from the deconstructor column."""
    connection: Connection = connect(f"file:{g.pth.dpd_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select lookup_key, deconstructor from lookup where deconstructor != ''"
        ).fetchall()
    finally:
        connection.close()

    pr.green_tmr("rows with deconstructor")
    pr.yes(len(rows))

    for surface, raw in rows:
        try:
            splits: list[str] = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for split in splits:
            if split.count(" + ") != 1:
                continue
            clean = RULE_RE.sub("", split)
            match = PARTICLE_RE.search(clean)
            if match and match.start() > 0:
                g.triples.add((clean[: match.start()], match.group(1), surface))

    pr.green_tmr("triples (base, particle, surface)")
    pr.yes(len(g.triples))


def induce(g: GlobalVars) -> None:
    """Reduce every triple to a rule keyed by (base's last character, particle)."""
    for base, particle, surface in g.triples:
        common = 0
        while (
            common < len(base)
            and common < len(surface)
            and base[common] == surface[common]
        ):
            common += 1
        g.rules[(base[-1], particle)][(base[common:], surface[common:])] += 1


def save(g: GlobalVars) -> None:
    """Write the rules in canonical sort order, dropping the long tail."""
    lines = ["base_last\tparticle\tbase_tail\tsurface_tail\tcount"]
    kept = 0
    dropped = 0
    for (base_last, particle), counter in g.rules.items():
        for (base_tail, surface_tail), count in counter.items():
            if count < MIN_COUNT:
                dropped += 1
                continue
            lines.append(
                f"{base_last}\t{particle}\t{base_tail}\t{surface_tail}\t{count}"
            )
            kept += 1

    header, body = lines[0], lines[1:]
    body.sort(
        key=lambda line: (
            pali_sort_key(line.split("\t")[1]),  # particle
            pali_sort_key(line.split("\t")[0]),  # base_last
            -int(line.split("\t")[4]),  # count, most frequent first
            pali_sort_key(line.split("\t")[2]),  # base_tail
            pali_sort_key(line.split("\t")[3]),  # surface_tail
        )
    )

    g.pth.particle_sandhi_rules_path.write_text(
        "\n".join([header, *body]) + "\n", encoding="utf-8"
    )
    pr.green_tmr("rules kept")
    pr.yes(kept)
    pr.green_tmr("rules dropped (below MIN_COUNT)")
    pr.yes(dropped)


def main() -> None:
    pr.tic()
    pr.yellow_title("inducing particle sandhi rules")
    g = GlobalVars()
    harvest(g)
    induce(g)
    save(g)
    pr.green_tmr("written to")
    pr.no(str(g.pth.particle_sandhi_rules_path))
    pr.toc()


if __name__ == "__main__":
    main()
