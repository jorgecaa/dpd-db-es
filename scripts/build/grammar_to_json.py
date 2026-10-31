#!/usr/bin/env python3
"""Dump every grammatical analysis of every inflected form to JSON and JS.

The webapp's **grammar** panel is built from `lookup.grammar`, a JSON column holding
`[lemma, pos, grammar]` triples per inflected form. This writes the same data as a
single mapping — form -> its analyses — in two forms:

- `shared_data/grammar/grammar_by_word.json` — one form per line, no indentation
  inside a line, still valid JSON, so `grep '^"buddha"'` works and `json.load` works.
- `exporter/goldendict/javascript/grammar_by_word_json.js` — the same mapping for the
  dictionary, which ships it as a resource (`exporter/goldendict/templates/dpd_header.jinja`
  loads it, `grammar_by_word_template.js` decodes and renders it, `main.js` calls it
  from the grammar panel).

  The JS is not plain triples: every form maps to a packed string of indices into two
  shared tables (64 162 words, 721 `[pos, grammar]` pairs), and the template decodes
  only the form it is about to show. Measured 2026-09-20 with node 22 / V8, parsing
  the file in a fresh context:

  | encoding                          | size     | parse  |
  |-----------------------------------|----------|--------|
  | `[word, pos, grammar]` triples     | 36.7 MiB | 325 ms |
  | the same as indexed arrays         | 15.6 MiB | 331 ms |
  | packed index string, per form      | 13.8 MiB |  56 ms |

  So the cost is the 924 205 inner arrays, not the bytes — 15.6 MiB of arrays parses
  as slowly as 36.7 MiB of triples. The dictionary parses this file on every lookup
  (the whole header is 12.1 MiB / 22 ms today), which is why the packed form won.

    uv run python scripts/build/grammar_to_json.py
    uv run python scripts/build/grammar_to_json.py --out /tmp/grammar.json --no-js
    uv run python scripts/build/grammar_to_json.py --limit 100 --js-out /tmp/g.js

Output is generated, not versioned (`shared_data/grammar` and the `*_json.js` data
files are gitignored). `components` is the grammar string split exactly as the webapp
splits it for its table (`reflx …` and `in comps` are kept whole, the rest is
whitespace-split); the webapp pads that to three cells, this dump does not, so a
processor sees what is there.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from db.db_helpers import get_db_session
from db.models import Lookup
from tools.paths import ProjectPaths
from tools.printer import printer as pr

JS_VAR_NAME = "grammar_by_word_json"
WORDS_VAR_NAME = "grammar_by_word_words"
ANALYSES_VAR_NAME = "grammar_by_word_analyses"
MB = 1048576


def parse_grammar(grammar: str) -> list[str]:
    """Split a grammar string the way `exporter/webapp/data_classes.py` does."""
    if grammar.startswith("reflx"):
        parts = grammar.split()
        if len(parts) >= 2:
            return [f"{parts[0]} {parts[1]}", *parts[2:]]
        return [grammar]
    if grammar.startswith("in comps"):
        return [grammar]
    return grammar.split()


def build_by_word(
    rows: Iterable[tuple[str, str]],
) -> tuple[dict[str, list[dict[str, object]]], int]:
    """Turn `(lookup_key, grammar json)` rows into a form -> analyses mapping."""
    by_word: dict[str, list[dict[str, object]]] = {}
    analyses = 0
    for lookup_key, grammar_json in rows:
        items = json.loads(grammar_json)
        by_word[lookup_key] = [
            {
                "word": word,
                "pos": pos,
                "grammar": grammar,
                "components": parse_grammar(grammar),
            }
            for word, pos, grammar in items
        ]
        analyses += len(items)
    return by_word, analyses


def write_json(
    path: Path,
    by_word: Mapping[str, Sequence[Mapping[str, object]]],
    indent: int | None,
) -> None:
    """Write the mapping as JSON, one form per line unless `indent` is given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        if indent:
            json.dump(
                dict(by_word),
                handle,
                ensure_ascii=False,
                indent=indent,
                sort_keys=True,
            )
            return
        # One form per line: greppable and diffable, still one JSON document.
        handle.write("{\n")
        last = len(by_word) - 1
        for index, (lookup_key, items) in enumerate(by_word.items()):
            comma = "," if index < last else ""
            handle.write(f"{json.dumps(lookup_key, ensure_ascii=False)}:")
            handle.write(
                json.dumps(list(items), ensure_ascii=False, separators=(",", ":"))
            )
            handle.write(f"{comma}\n")
        handle.write("}\n")


def pack_by_word(
    by_word: Mapping[str, Sequence[Mapping[str, object]]],
) -> tuple[list[str], list[list[str]], dict[str, str]]:
    """Encode form -> analyses as indices into two shared tables.

    Returns `(words, analyses, packed)` where `packed[form]` is a string of
    `wordIndex,analysisIndex` pairs. See the module docstring for why: this is the
    only encoding measured that parses fast, because it allocates one string per
    form instead of one array per analysis.
    """
    words: dict[str, int] = {}
    analyses: dict[tuple[str, str], int] = {}
    packed: dict[str, str] = {}

    for form, items in by_word.items():
        pairs = []
        for item in items:
            word = str(item["word"])
            analysis = (str(item["pos"]), str(item["grammar"]))
            pairs.append(
                f"{words.setdefault(word, len(words))},"
                f"{analyses.setdefault(analysis, len(analyses))}"
            )
        packed[form] = " ".join(pairs)

    return list(words), [[pos, grammar] for pos, grammar in analyses], packed


def write_js(
    path: Path,
    by_word: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Write the mapping as JS variables for the dictionary.

    One form per line in the mapping, like the JSON dump, so the file still diffs
    and greps form by form; the two index tables are single lines above it.
    """
    words, analyses, packed = pack_by_word(by_word)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"var {WORDS_VAR_NAME} = ")
        handle.write(json.dumps(words, ensure_ascii=False, separators=(",", ":")))
        handle.write(";\n")
        handle.write(f"var {ANALYSES_VAR_NAME} = ")
        handle.write(json.dumps(analyses, ensure_ascii=False, separators=(",", ":")))
        handle.write(";\n")
        handle.write(f"var {JS_VAR_NAME} = {{\n")
        last = len(packed) - 1
        for index, (lookup_key, pairs) in enumerate(packed.items()):
            comma = "," if index < last else ""
            handle.write(f"{json.dumps(lookup_key, ensure_ascii=False)}:")
            handle.write(json.dumps(pairs, ensure_ascii=False))
            handle.write(f"{comma}\n")
        handle.write("}\n")


def main() -> int:
    pth = ProjectPaths()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=pth.grammar_by_word_json_path)
    parser.add_argument(
        "--js-out",
        type=Path,
        default=None,
        help=(
            "JS data file for the dictionary "
            f"(default {pth.grammar_by_word_json}); with --limit it is only "
            "written when this is given explicitly"
        ),
    )
    parser.add_argument("--no-js", action="store_true", help="skip the JS data file")
    parser.add_argument(
        "--indent", type=int, default=None, help="0 or more to pretty-print"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="only the first N forms (debugging)"
    )
    args = parser.parse_args()

    js_out: Path = args.js_out or pth.grammar_by_word_json
    # A truncated export must never reach the dictionary: --limit only writes JS
    # when the path is named explicitly.
    skip_js: bool = args.no_js or bool(args.limit and args.js_out is None)

    pr.tic()
    pr.yellow_title("exporting grammar by word")

    db_session = get_db_session(pth.dpd_db_path)
    pr.green_tmr("reading lookup.grammar")
    query = (
        db_session.query(Lookup.lookup_key, Lookup.grammar)
        .filter(Lookup.grammar != "", Lookup.grammar != "[]")
        .order_by(Lookup.lookup_key)
    )
    if args.limit:
        query = query.limit(args.limit)
    # unpacked into tuples because SQLAlchemy's Row is not a tuple to the type
    # checker, only at runtime
    rows: list[tuple[str, str]] = [
        (lookup_key, grammar) for lookup_key, grammar in query.all()
    ]
    pr.yes(len(rows))

    pr.green_tmr("building the mapping")
    by_word, analyses = build_by_word(rows)
    pr.yes(f"{analyses:,} analyses")

    pr.green_tmr("writing json")
    write_json(args.out, by_word, args.indent)
    pr.yes(f"{args.out.stat().st_size / MB:.1f} MB -> {args.out}")

    if skip_js:
        pr.amber(f"skipping js data file: {js_out}")
    else:
        pr.green_tmr("writing js")
        write_js(js_out, by_word)
        pr.yes(f"{js_out.stat().st_size / MB:.1f} MB -> {js_out}")

    db_session.close()
    pr.toc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
