#!/usr/bin/env python3

"""Turn the review queue into a spreadsheet a person can actually read.

`revisar_es.tsv` is what the pipeline needs: one line per headword, the reason
crammed into a field. Deciding 187 entries from that means reading concatenated
sentences in a column too narrow to show them, which is how a queue stops being
looked at.

This writes the same queue as `.xlsx`, laid out for the eye: the English beside
the proposal beside the auditor's correction, the verdict as a colour, and the
grammar the entry hangs on where it can be seen. Sorted so the ones the auditor
called wrong come first — those need a decision, the doubtful ones need an
opinion.

What is deliberately left out, on the grounds that the reader knows English and
Pāḷi: no gloss of the English, no transliteration, no restating what the lemma
means. What stays is what a decision needs — what the DPD says, what we propose,
what the auditor would put instead, and why.

Usage:
    uv run python scripts/build/spanish_review_sheet.py [--out temp/revision_es.xlsx]
"""

import argparse
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from db.db_helpers import get_db_session
from db.models import DpdHeadword
from scripts.build.spanish_review import (
    canonical,
    check_audit,
    check_translation,
    compatible,
)
from tools import spanish
from tools.paths import ProjectPaths
from tools.printer import printer as pr

REGISTRO = Path("scripts/build/generado_es_registro.jsonl")
SALIDA = Path("temp/revision_es.xlsx")

# Wrong first: those need a decision. Doubtful next: those need an opinion. The
# rest are here only because translator and auditor read the grammar differently.
ORDEN = {"incorrecta": 0, "dudosa": 1, "correcta": 2}

COLUMNAS = (
    ("lema", 20),
    ("gram", 16),
    ("inglés del DPD", 46),
    ("literal inglés", 26),
    ("propuesta", 46),
    ("literal propuesto", 26),
    ("veredicto", 12),
    ("qué falla", 18),
    ("por qué", 52),
    ("el auditor pondría", 46),
    ("forma: traductor / auditor", 26),
)

COLORES = {
    "incorrecta": PatternFill("solid", fgColor="F8CBAD"),
    "dudosa": PatternFill("solid", fgColor="FFE699"),
    "correcta": PatternFill("solid", fgColor="D9E1F2"),
}


def ingles_de(headword: DpdHeadword) -> tuple[str, str]:
    """The DPD's own gloss and literal, read past the overlay."""
    with spanish.suppressed():
        return (headword.meaning_1 or "").strip(), (headword.meaning_lit or "").strip()


def motivos(fuente: dict[str, Any], registro: dict[str, Any]) -> tuple[list[str], bool]:
    """Why this entry is in the queue, and whether the two readings disagree.

    Recomputed from the record rather than parsed back out of the TSV, so the
    sheet and the queue cannot drift apart.
    """
    traduccion, auditoria = registro["traduccion"], registro["auditoria"]
    fallos = check_translation(fuente, traduccion) + check_audit(auditoria)
    declarada = canonical((traduccion.get("forma") or {}).get("categoria", ""))
    auditada = canonical(auditoria.get("categoria_segun_auditor", ""))
    discrepan = not compatible(declarada, auditada)
    return fallos, discrepan


def recoger(db_session, registros: list[dict]) -> list[dict[str, Any]]:
    """The entries needing a person, with everything a decision needs beside them."""
    lemas = {r["lema"] for r in registros}
    headwords = {
        h.lemma_1: h
        for h in db_session.query(DpdHeadword)
        .filter(DpdHeadword.lemma_1.in_(lemas))
        .all()
    }
    filas = []
    for registro in registros:
        headword = headwords.get(registro["lema"])
        if headword is None:
            continue
        ingles, literal_en = ingles_de(headword)
        fuente = {
            "lema": registro["lema"],
            "acepcion_inglesa": ingles,
            "literal_ingles": literal_en,
            "pos": headword.pos,
        }
        fallos, discrepan = motivos(fuente, registro)
        traduccion, auditoria = registro["traduccion"], registro["auditoria"]
        veredicto = auditoria.get("veredicto", "")
        if veredicto == "correcta" and not fallos and not discrepan:
            continue

        problema = auditoria.get("problema") or {}
        porque = (problema.get("detalle") or "").strip()
        if fallos:
            porque = "; ".join(fallos) + (f" · {porque}" if porque else "")
        elif not porque and discrepan:
            # Sin esto la fila llega muda: el auditor aprobó la traducción y la
            # única objeción es que él y el traductor leyeron la forma distinto,
            # que es precisamente lo que hay que mirar aquí.
            porque = (
                "el auditor aprueba la traducción, pero él y el traductor leen "
                "la forma de manera distinta"
            )
        declarada = canonical((traduccion.get("forma") or {}).get("categoria", ""))
        auditada = canonical(auditoria.get("categoria_segun_auditor", ""))
        filas.append(
            {
                "lema": registro["lema"],
                "gram": headword.grammar,
                "inglés del DPD": ingles,
                "literal inglés": literal_en,
                "propuesta": traduccion.get("meaning_1_es", ""),
                "literal propuesto": traduccion.get("meaning_lit_es", ""),
                "veredicto": veredicto,
                "qué falla": problema.get("tipo")
                or ("contrato" if fallos else "morfología"),
                "por qué": porque,
                "el auditor pondría": auditoria.get("correccion_propuesta", ""),
                "forma: traductor / auditor": (
                    f"{declarada} / {auditada}" if discrepan else declarada
                ),
                "_orden": ORDEN.get(veredicto, 3),
            }
        )
    filas.sort(key=lambda f: (f["_orden"], f["lema"]))
    return filas


def escribir(filas: list[dict[str, Any]], destino: Path) -> None:
    """The sheet itself: frozen header, wrapped text, a colour per verdict."""
    libro = Workbook()
    hoja: Worksheet = libro.active  # pyright: ignore[reportAssignmentType]
    hoja.title = "revisión"

    hoja.append([nombre for nombre, _ in COLUMNAS])
    for celda in hoja[1]:
        celda.font = Font(bold=True)
        celda.alignment = Alignment(vertical="center")
    hoja.freeze_panes = "A2"

    for fila in filas:
        hoja.append([fila.get(nombre, "") for nombre, _ in COLUMNAS])
        color = COLORES.get(fila["veredicto"])
        if color:
            hoja.cell(row=hoja.max_row, column=7).fill = color

    for indice, (_, ancho) in enumerate(COLUMNAS, start=1):
        hoja.column_dimensions[get_column_letter(indice)].width = ancho
    for fila_celdas in hoja.iter_rows(min_row=2):
        for celda in fila_celdas:
            celda.alignment = Alignment(wrap_text=True, vertical="top")

    hoja.auto_filter.ref = hoja.dimensions
    destino.parent.mkdir(parents=True, exist_ok=True)
    libro.save(destino)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registro", type=Path, default=REGISTRO)
    parser.add_argument("--out", type=Path, default=SALIDA)
    args = parser.parse_args()

    pr.tic()
    pr.yellow_title("hoja de revisión")
    registros = [
        json.loads(linea)
        for linea in args.registro.read_text(encoding="utf-8").splitlines()
        if linea.strip()
    ]
    pr.summary("registros", len(registros))

    db_session = get_db_session(ProjectPaths().dpd_db_path)
    filas = recoger(db_session, registros)
    pr.summary("a revisar", len(filas))
    for veredicto in ("incorrecta", "dudosa", "correcta"):
        cuantas = sum(1 for f in filas if f["veredicto"] == veredicto)
        if cuantas:
            pr.summary(veredicto, cuantas)

    escribir(filas, args.out)
    pr.summary("escrito", str(args.out))
    pr.toc()


if __name__ == "__main__":
    main()
