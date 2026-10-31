#!/usr/bin/env python3
"""Induce the junction transformations the hand-written sandhi table does not have.

`indasiddhaṭṭhena` is `inda` + `siddha` + `ṭhena`: the surface doubles the `ṭ` at the
junction. `dukhāvaha` is `dukkha` + `āvaha`: `kkh` collapses to `kh` and the `a` before
`ā` drops. `mahaddhana` is `mahā` + `dhana`: `ā` shortens and the `d` doubles. None of
these are in `shared_data/deconstructor/sandhi_rules.tsv`, which is why a form that needs
one cannot be split at all.

The deconstructor's own two-part splits are the training set: wherever joining the two
components does not reproduce the word, the difference *is* the transformation. Each one
is reduced to the same (chA, chB) -> (ch1, ch2) shape the Go splitter applies:

    word1 = word[:x] + ch1        the left word ends here
    word2 = ch2 + word[x+2:]      the right word starts here

The junction is found by anchoring on the right component's common suffix with the word,
which is what makes gemination (`siddha` + `ṭhena` -> `siddhaṭṭhena`) come out as
(ch1 `a`, ch2 ``) rather than as a spurious mismatch.

Components that differ from the dictionary word *internally* (`sati` surfacing as `sacca`
in `muṭṭhasacca`) are not junction transformations and are not harvested here: they have
no common suffix to anchor on and are dropped by construction.

Writes `shared_data/junction_transform_rules.tsv` in canonical Pāḷi sort order.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tools.pali_sort_key import pali_sort_key
from tools.paths import ProjectPaths
from tools.printer import printer as pr

# Deconstructor splits carry a trailing rule annotation, e.g. "vihāresu + iti [rule]".
_RULE_ANNOTATION = re.compile(r" \[.+")

# Mirrors `DpdHeadword.lemma_clean`.
_SENSE_NUMBER = re.compile(r" \d.*$")

# An element that is a root, or that carries a phonetic-change annotation, is not a word
# the junction can be anchored on.
_NOT_A_WORD = re.compile(r"[>\[\]()]|^√")

MIN_COUNT = 3

# The hand-written table's rules weigh 2-4; an induced rule is scored at the heavy end so
# that a split needing one never outranks a split the curated table already explains.
WEIGHT = 4


@dataclass
class GlobalVars:
    pth: ProjectPaths = field(default_factory=ProjectPaths)
    output_path: Path | None = None
    rules: dict[tuple[str, str, str, str], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    examples: dict[tuple[str, str, str, str], str] = field(default_factory=dict)
    skipped: Counter[str] = field(default_factory=Counter)


def common_suffix_length(left: str, right: str) -> int:
    length = 0
    while (
        length < len(left)
        and length < len(right)
        and left[len(left) - 1 - length] == right[len(right) - 1 - length]
    ):
        length += 1
    return length


def harvest(g: GlobalVars) -> None:
    """Turn every two-part split that changes letters into a junction rule."""
    connection = sqlite3.connect(f"file:{g.pth.dpd_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select lookup_key, deconstructor from lookup where deconstructor != ''"
        ).fetchall()
    finally:
        connection.close()

    pr.green_tmr("lookup keys with deconstructions")
    pr.yes(len(rows))

    for word, raw in rows:
        try:
            splits: list[str] = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for split in splits:
            clean = _RULE_ANNOTATION.sub("", split)
            parts = clean.split(" + ")
            if len(parts) != 2:
                continue
            left, right = parts
            if not left or not right or left + right == word:
                continue
            induce(g, word, left, right)

    pr.green_tmr("distinct transformations")
    pr.yes(len(g.rules))


def induce(g: GlobalVars, word: str, left: str, right: str) -> bool:
    """Reduce one (word, left, right) observation to a junction rule."""
    # Anchor on the right component: its tail fixes where the junction is.
    suffix = common_suffix_length(right, word)
    if suffix == 0 or suffix > len(right):
        g.skipped["no common suffix"] += 1
        return False
    position = len(word) - suffix - 2
    if position < 0:
        g.skipped["junction outside the word"] += 1
        return False
    if word[:position] != left[:position]:
        g.skipped["left side disagrees before the junction"] += 1
        return False

    ch_a = word[position]
    ch_b = word[position + 1]
    ch1 = left[position:]
    ch2 = right[: len(right) - suffix]
    key = (ch_a, ch_b, ch1, ch2)
    g.rules[key] += 1
    g.examples.setdefault(key, word)
    return True


def harvest_constructions(g: GlobalVars) -> None:
    """Learn from the dictionary's own `construction` column.

    The deconstructor never processes a word the dictionary already has, so a formation
    that only ever occurs inside a headword (`mahaddhana` = `mahā` + `dhana`) is invisible
    to it. The editorial construction is that missing half of the training set.
    """
    connection = sqlite3.connect(f"file:{g.pth.dpd_db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select lemma_1, construction from dpd_headwords where construction != ''"
        ).fetchall()
    finally:
        connection.close()

    pr.green_tmr("headwords with a construction")
    pr.yes(len(rows))

    for lemma_1, construction in rows:
        line = construction.split("\n")[0]
        elements = [element.strip().lstrip("*") for element in line.split(" + ")]
        if len(elements) != 2:
            g.skipped["construction is not a two-part formation"] += 1
            continue
        left, right = elements
        if _NOT_A_WORD.search(left) or _NOT_A_WORD.search(right):
            g.skipped["construction element is not a word"] += 1
            continue
        word = _SENSE_NUMBER.sub("", lemma_1)
        if left + right == word:
            continue
        induce(g, word, left, right)

    pr.green_tmr("distinct transformations after constructions")
    pr.yes(len(g.rules))


def save(g: GlobalVars) -> None:
    """Write the rules kept, in canonical sort order."""
    kept = {key: count for key, count in g.rules.items() if count >= MIN_COUNT}
    lines = ["chA\tchB\tch1\tch2\tweight\teg\tcount"]
    for (ch_a, ch_b, ch1, ch2), count in kept.items():
        example = g.examples[(ch_a, ch_b, ch1, ch2)]
        lines.append(f"{ch_a}\t{ch_b}\t{ch1}\t{ch2}\t{WEIGHT}\t{example}\t{count}")

    header, body = lines[0], lines[1:]
    body.sort(
        key=lambda line: (
            pali_sort_key(line.split("\t")[0]),
            pali_sort_key(line.split("\t")[1]),
            -int(line.split("\t")[6]),
            pali_sort_key(line.split("\t")[2]),
            pali_sort_key(line.split("\t")[3]),
        )
    )

    target = g.output_path or g.pth.junction_transform_rules_path
    target.write_text("\n".join([header, *body]) + "\n", encoding="utf-8")
    pr.green_tmr("rules kept")
    pr.yes(len(body))
    pr.green_tmr("distinct transformations dropped below MIN_COUNT")
    pr.yes(len(g.rules) - len(kept))


def main() -> None:
    pr.tic()
    pr.yellow_title("inducing junction transformations")
    g = GlobalVars()
    harvest(g)
    harvest_constructions(g)
    save(g)
    for reason, count in g.skipped.most_common():
        pr.green_tmr(f"skipped: {reason}")
        pr.yes(count)
    pr.green_tmr("written to")
    pr.no(str(g.output_path or g.pth.junction_transform_rules_path))
    pr.toc()


if __name__ == "__main__":
    main()
