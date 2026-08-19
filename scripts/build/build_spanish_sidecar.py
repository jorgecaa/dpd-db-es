#!/usr/bin/env python3

"""Build the Spanish sidecar database read by `tools/spanish.py`.

The source is `dpd_ebts_es.js` from the dpd-dictionary-es project, whose values
are rendered strings in the shape produced by `exporter/tbw/tbw_exporter.py`:

    "adj. <b>libre de duda; despojado de incertidumbre</b> [na + √kaṅkh + a]"
     ────  ──────────────────────────────────────────────  ─────────────────
     pos   meaning_1 in bold, or meaning_2 unbolded         construction

This script parses that back into the three separate fields and writes them to
`dpd_es.db`, keyed by `lemma_1`. The `pos` and the construction are stored too,
untranslated: they are the fingerprint that tells whether the lemma still means
what it meant when the translation was made.

Usage:
    uv run python scripts/build/build_spanish_sidecar.py --source path/to/dpd_ebts_es.js
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

from tools import spanish
from tools.printer import printer as pr
from tools.spanish import (
    COLUMNS,
    FIELDS,
    FINGERPRINT,
    constructions_agree,
    ROOT_COLUMNS,
    ROOT_TABLE,
    SANSKRIT_COLUMN,
    SANSKRIT_TABLE,
    GENERATED,
    ORIGIN,
    SIDECAR_PATH,
    STORED,
    TABLE,
    UI_COLUMN,
    UI_TABLE,
    NOTES_COLUMN,
    NOTES_TABLE,
)

SANSKRIT_TSV = Path(__file__).parent / "sanskrit_meanings_es.tsv"
CORRECCIONES_TSV = Path(__file__).parent / "material_es_correcciones.tsv"
UI_TSV = Path(__file__).parent / "webapp_ui_es.tsv"
NOTES_TSV = Path(__file__).parent / "notes_es.tsv"
COMPOUNDS_TSV = Path(__file__).parent / "compounds_es.tsv"
GENERATED_TSV = Path(__file__).parent / "generado_es.tsv"

POS_PATTERN = re.compile(r"^(?P<pos>[^.]{1,20})\.\s+")
BOLD_PATTERN = re.compile(r"^<b>(?P<meaning>.*?)</b>(?P<rest>.*)$", re.DOTALL)
LITERAL_PATTERN = re.compile(r";\s*lit\.\s*(?P<literal>.+)$", re.DOTALL)
ROOT_PATTERN = re.compile(
    r"'(?P<root>[^']+)':\s*\{en:\s*'(?P<en>(?:[^'\\]|\\.)*)',"
    r"\s*es:\s*'(?P<es>(?:[^'\\]|\\.)*)'\}"
)


def parse_entry(rendered: str) -> dict[str, str]:
    """Split one rendered definition into its fields plus the drift fingerprint.

    `pos` and the construction summary are kept as they stood when the entry was
    translated, so `tools/spanish.py` can drop a translation whose lemma has
    since been reassigned to another sense.
    """
    pos_match = POS_PATTERN.match(rendered)
    pos = pos_match.group("pos") if pos_match else ""
    text = POS_PATTERN.sub("", rendered, count=1).strip()

    # split off the trailing construction, e.g. " [na + √kaṅkh + a]"
    construction = ""
    if text.endswith("]"):
        opening = text.rfind(" [")
        if opening != -1:
            construction = text[opening + 2 : -1]
            text = text[:opening].strip()

    meaning_1 = ""
    meaning_2 = ""
    meaning_lit = ""

    bold = BOLD_PATTERN.match(text)
    if bold:
        # the literal sits after </b>, so meaning_1 needs no further cleaning
        meaning_1 = bold.group("meaning").strip()
        literal = LITERAL_PATTERN.search(bold.group("rest"))
        if literal:
            meaning_lit = literal.group("literal").strip()
    else:
        meaning_2 = text
        literal = LITERAL_PATTERN.search(meaning_2)
        if literal:
            meaning_lit = literal.group("literal").strip()
            meaning_2 = LITERAL_PATTERN.sub("", meaning_2).strip()

    return {
        "meaning_1": meaning_1,
        "meaning_lit": meaning_lit,
        "meaning_2": meaning_2,
        "pos": pos,
        "construction": construction,
    }


def read_roots(source_path: Path) -> dict[str, dict[str, str]]:
    """Read `dpd_roots.js`, whose entries are javascript object literals:

        '√dukkh': {en: 'be painful', es: 'ser doloroso'},

    The English is kept as the fingerprint: a root whose meaning has since been
    reworded upstream will no longer match, and keeps its English.
    """
    text = source_path.read_text(encoding="utf-8")
    return {
        match.group("root"): {
            "root_meaning": match.group("es").replace("\\'", "'"),
            "root_meaning_en": match.group("en").replace("\\'", "'"),
        }
        for match in ROOT_PATTERN.finditer(text)
    }


def read_sanskrit(
    tsv_path: Path, roots: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Build the English→Spanish map for Sanskrit root glosses.

    Two sources, in this order of precedence:
    1. the root translations already in `dpd_roots.js`, reused wherever a
       Sanskrit gloss reads exactly like a Pāḷi one (`be worth` → `valer`);
    2. `sanskrit_meanings_es.tsv`, this project's own translations of the
       glosses that appear nowhere in the Spanish material.
    """
    phrases = {
        fields["root_meaning_en"]: fields["root_meaning"]
        for fields in roots.values()
        if fields["root_meaning_en"]
    }
    if tsv_path.exists():
        for line in tsv_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            english, _, spanish_text = line.partition("\t")
            phrases.setdefault(english, spanish_text)
    return {
        english: {SANSKRIT_COLUMN: spanish_text}
        for english, spanish_text in phrases.items()
    }


def read_pairs(tsv_path: Path, column: str) -> dict[str, dict[str, str]]:
    """Read a two-column TSV of translations, ignoring comments and blanks."""
    pairs: dict[str, dict[str, str]] = {}
    if not tsv_path.exists():
        return pairs
    for line in tsv_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        english, _, spanish_text = line.partition("\t")
        if spanish_text:
            pairs[english] = {column: spanish_text}
    return pairs


def read_material_fixes(tsv_path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """Read the corrections applied on top of the published translation.

    The rule of this layer is to serve the material as it is; this file is the
    declared exception, and the reason travels with it in its own header. Keyed
    by lemma and field, because the material is stored one row per lemma.

    Each correction carries the text it expects to replace. Without it a
    rewording upstream would be overwritten in silence and we would never learn
    that their version now says something else — the same reasoning as the
    fingerprint that guards a drifting translation.
    """
    fixes: dict[tuple[str, str], tuple[str, str]] = {}
    if not tsv_path.exists():
        return fixes
    for line in tsv_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        lemma, field, spanish_text, fingerprint = parts
        if spanish_text and field in COLUMNS:
            fixes[(lemma, field)] = (spanish_text, fingerprint)
    return fixes


def read_entries(tsv_path: Path) -> dict[str, dict[str, str]]:
    """Read extra headword entries generated by `build_spanish_glossary.py`."""
    entries: dict[str, dict[str, str]] = {}
    if not tsv_path.exists():
        return entries
    for line in tsv_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        lemma_1, *values = line.split("\t")
        if len(values) == len(COLUMNS):
            entries[lemma_1] = dict(zip(COLUMNS, values))
    return entries


def merge_generated(
    entries: dict[str, dict[str, str]], extra: dict[str, dict[str, str]]
) -> tuple[int, int, int]:
    """Fold generated translations into the material, never overwriting it.

    A lemma the material does not have is added whole. A lemma it does have is
    only *completed*: a field the material left empty is filled, and a field it
    already wrote is left exactly as it stands, whatever the generated text says.

    Completing existing rows is what the Spanish material cannot do for itself.
    Its entries were rendered by `meaning_combo_html`, which falls back to
    `meaning_2` when a headword has no `meaning_1`, so a lemma translated before
    the DPD filled its `meaning_1` in has that field empty for good — measured,
    1,126 lemmas are in that position and show English. Adding whole lemmas only,
    as this did before, could never reach them.

    Where the two disagree about the lemma itself — a different part of speech, a
    construction that is not the same analysis — the material's fingerprint has
    gone stale, and `tools/spanish.py` is already refusing that whole row. Filling
    a hole in it would write a translation nobody can ever see: measured, 495 of
    1,988 landed exactly there. So a stale row is replaced outright by the
    generated one, carrying today's fingerprint.

    Replacing drops the material's other fields for that lemma, and that is the
    point rather than a cost: they are refused already, and keeping them under a
    refreshed fingerprint would put translations back on display that the drift
    check had deliberately taken down. Nothing is lost for good either way — the
    material is re-read from source on every build, so a lemma the DPD later
    changes back is judged afresh.

    The generated fields carry today's values because that is what they were
    translated from, which is why they may stamp the fingerprint at all.

    Returns how many lemmas were added, how many fields filled, how many rows
    replaced.
    """
    added = filled = replaced = 0
    for lemma, fields in extra.items():
        current = entries.get(lemma)
        if current is None:
            entries[lemma] = {**fields, ORIGIN: GENERATED}
            added += 1
            continue
        stale = current.get("pos", "") != fields.get(
            "pos", ""
        ) or not constructions_agree(
            current.get("construction", ""), fields.get("construction", "")
        )
        if stale and any(fields.get(column, "").strip() for column in FIELDS):
            entries[lemma] = {**fields, ORIGIN: GENERATED}
            replaced += 1
            continue
        for column in COLUMNS:
            if current.get(column, "").strip() or not fields.get(column, "").strip():
                continue
            if column in FINGERPRINT and any(
                current.get(f, "").strip() for f in FINGERPRINT
            ):
                continue
            current[column] = fields[column]
            filled += 1
    return added, filled, replaced


def read_source(source_path: Path) -> dict[str, str]:
    """Read the `let dpd_ebts_es = {...}` javascript file as a dict."""
    text = source_path.read_text(encoding="utf-8")
    start = text.index("{")
    return json.loads(text[start:].rstrip().rstrip(";"))


def write_sidecar(
    entries: dict[str, dict[str, str]],
    roots: dict[str, dict[str, str]],
    sanskrit: dict[str, dict[str, str]],
    notes: dict[str, dict[str, str]],
    ui: dict[str, dict[str, str]],
    output_path: Path,
) -> None:
    """Write every table to the sidecar database, replacing any previous one."""
    if output_path.exists():
        output_path.unlink()

    connection = sqlite3.connect(output_path)
    try:
        _write_table(connection, TABLE, "lemma_1", STORED, entries)
        _write_table(connection, ROOT_TABLE, "root", ROOT_COLUMNS, roots)
        _write_table(
            connection, SANSKRIT_TABLE, "meaning_en", (SANSKRIT_COLUMN,), sanskrit
        )
        _write_table(connection, NOTES_TABLE, "notes_en", (NOTES_COLUMN,), notes)
        _write_table(connection, UI_TABLE, "text_en", (UI_COLUMN,), ui)
        connection.commit()
    finally:
        connection.close()


def _write_table(
    connection: sqlite3.Connection,
    table: str,
    key: str,
    columns: tuple[str, ...],
    rows: dict[str, dict[str, str]],
) -> None:
    """Create one sidecar table and fill it."""
    definitions = ", ".join(f"{column} text not null default ''" for column in columns)
    connection.execute(f"create table {table} ({key} text primary key, {definitions})")
    placeholders = ", ".join("?" for _ in range(len(columns) + 1))
    connection.executemany(
        f"insert into {table} values ({placeholders})",
        [
            (key_value, *(fields[column] for column in columns))
            for key_value, fields in rows.items()
        ],
    )


def main() -> None:
    pr.tic()
    pr.yellow_title("building spanish sidecar")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, type=Path, help="path to dpd_ebts_es.js"
    )
    parser.add_argument("--roots", type=Path, help="path to dpd_roots.js")
    parser.add_argument("--sanskrit", type=Path, default=SANSKRIT_TSV)
    parser.add_argument("--notes", type=Path, default=NOTES_TSV)
    parser.add_argument("--ui", type=Path, default=UI_TSV)
    parser.add_argument("--compounds", type=Path, default=COMPOUNDS_TSV)
    parser.add_argument("--generated", type=Path, default=GENERATED_TSV)
    parser.add_argument("--correcciones", type=Path, default=CORRECCIONES_TSV)
    parser.add_argument("--output", type=Path, default=SIDECAR_PATH)
    args = parser.parse_args()

    pr.green_tmr("reading source")
    source = read_source(args.source)
    pr.yes(len(source))

    pr.green_tmr("parsing definitions")
    entries = {
        lemma_1: {**parse_entry(value), ORIGIN: "material"}
        for lemma_1, value in source.items()
    }
    pr.yes(len(entries))

    fixes = read_material_fixes(args.correcciones)
    if fixes:
        applied = 0
        stale: list[str] = []
        for (lemma, field), (value, fingerprint) in fixes.items():
            if lemma not in entries:
                stale.append(f"{lemma} ({field}): el lema ya no está")
            elif entries[lemma][field] != fingerprint:
                stale.append(f"{lemma} ({field}): ahora dice «{entries[lemma][field]}»")
            else:
                entries[lemma][field] = value
                applied += 1
        pr.summary("correcciones al material", applied)
        if stale:
            pr.red(f"{len(stale)} correcciones sin aplicar, el material cambió:")
            for detail in stale:
                pr.red(f"  {detail}")

    # generated by reusing the glossary, then translated for this project;
    # neither ever overrides a human translation
    for label, path in (
        ("compuestos por reuso", args.compounds),
        ("traducidas", args.generated),
    ):
        added, filled, replaced = merge_generated(entries, read_entries(path))
        if added:
            pr.summary(f"{label}, lemas nuevos", added)
        if filled:
            pr.summary(f"{label}, huecos rellenados", filled)
        if replaced:
            pr.summary(f"{label}, filas caducadas sustituidas", replaced)

    # after the corrections, so a fingerprint still compares against the text as
    # upstream wrote it, markers and all
    pr.green_tmr("unificando marcadores")
    unified = 0
    for fields in entries.values():
        for field in COLUMNS:
            normalised = spanish.normalise_markers(fields[field])
            if normalised != fields[field]:
                fields[field] = normalised
                unified += 1
    pr.yes(unified)

    # default to the roots file sitting beside the definitions
    roots_path = args.roots or args.source.parent / "dpd_roots.js"
    roots: dict[str, dict[str, str]] = {}
    if roots_path.exists():
        pr.green_tmr("reading roots")
        roots = read_roots(roots_path)
        pr.yes(len(roots))
    else:
        pr.red(f"no roots file at {roots_path}, skipping roots")

    pr.green_tmr("reading sanskrit glosses")
    sanskrit = read_sanskrit(args.sanskrit, roots)
    pr.yes(len(sanskrit))

    pr.green_tmr("reading notes")
    notes = read_pairs(args.notes, NOTES_COLUMN)
    pr.yes(len(notes))

    pr.green_tmr("reading interface strings")
    ui = read_pairs(args.ui, UI_COLUMN)
    pr.yes(len(ui))

    pr.green_tmr("writing sidecar")
    write_sidecar(entries, roots, sanskrit, notes, ui, args.output)
    pr.yes(len(entries) + len(roots) + len(sanskrit))

    checked = sum(1 for fields in entries.values() if fields["meaning_1"])
    pr.summary("meaning_1", checked)
    pr.summary("meaning_2", len(entries) - checked)
    pr.summary("meaning_lit", sum(1 for f in entries.values() if f["meaning_lit"]))
    pr.summary("roots", len(roots))
    pr.summary("sanskrit", len(sanskrit))
    pr.summary("notas", len(notes))
    pr.summary("interfaz", len(ui))
    pr.toc()


if __name__ == "__main__":
    main()
