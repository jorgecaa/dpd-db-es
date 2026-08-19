#!/usr/bin/env python3

"""Fold a reviewed sheet back into the generated translations.

`spanish_review_sheet.py` hands a person 187 decisions; this is what happens
when they come back. Rows still undecided are left in the queue, so a review
done in sittings loses nothing and repeating this is safe.

The vocabulary is the reviewer's own, not the dropdown's — a sheet is edited by
a person, and insisting they use the exact phrase a script prefers is how a
review comes back unusable:

    propuesta            what we proposed stands
    error                what they wrote in `tu texto` replaces it
    literal propuesto    the meaning becomes the literal we proposed

That last one is a real change of meaning, not a wording fix: `sakkanāmaka`
stops reading «nombre de un demonio» and reads «llamado Sakka». It is what the
auditor argued for in each of those rows — that the sense must follow the form —
and it is applied only where the reviewer said so.

Reading the file needs care. Once LibreOffice or Excel has saved it, openpyxl
refuses it: the styles carry an `extLst` and the validations an attribute it
does not know. Both are stripped from a copy in memory, and the reviewer's file
is never touched.

Usage:
    uv run python scripts/build/spanish_apply_review.py [--dry-run]
"""

import argparse
import io
import re
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from db.db_helpers import get_db_session
from db.models import DpdHeadword
from scripts.build.spanish_review import flatten
from tools import spanish
from tools.paths import ProjectPaths
from tools.printer import printer as pr

HOJA = Path("scripts/build/revision_es.xlsx")
GENERADO = Path("scripts/build/generado_es.tsv")
COLUMNAS = ("lema", "meaning_1", "meaning_lit", "meaning_2", "pos", "construction")

ACEPTA = "propuesta"
CORRIGE = "error"
LITERAL = "literal propuesto"

# También se admite el vocabulario del desplegable, por si alguien lo usa.
SINONIMOS = {
    "acepto la propuesta": ACEPTA,
    "acepto la del auditor": CORRIGE,
    "escribo otra": CORRIGE,
}

EXTLST = re.compile(rb"<extLst>.*?</extLst>", re.DOTALL)
VALIDACIONES = re.compile(
    rb"<dataValidations.*?</dataValidations>|<dataValidation[^>]*/>", re.DOTALL
)


def abrir(hoja: Path):
    """La hoja, saneada de lo que openpyxl no sabe leer de un archivo de Excel."""
    limpio = io.BytesIO()
    with zipfile.ZipFile(hoja) as origen:
        with zipfile.ZipFile(limpio, "w", zipfile.ZIP_DEFLATED) as copia:
            for nombre in origen.namelist():
                datos = origen.read(nombre)
                if nombre == "xl/styles.xml":
                    datos = EXTLST.sub(b"", datos)
                elif nombre.startswith("xl/worksheets/"):
                    datos = VALIDACIONES.sub(b"", datos)
                copia.writestr(nombre, datos)
    limpio.seek(0)
    return load_workbook(limpio)


def leer_decisiones(hoja: Path) -> tuple[list[dict[str, str]], int, list[str]]:
    """Lo decidido, lo que sigue pendiente y lo que no se entiende."""
    libro = abrir(hoja)
    pagina = libro["revisión"]
    cabecera = [celda.value for celda in pagina[1]]
    indice = {nombre: cabecera.index(nombre) for nombre in cabecera if nombre}

    decisiones: list[dict[str, str]] = []
    pendientes = 0
    ilegibles: list[str] = []
    for fila in pagina.iter_rows(min_row=2, values_only=True):

        def campo(nombre: str) -> str:
            # Una celda vacía llega como None, y `flatten` la convertiría en la
            # cadena «None»: sin esto, una fila sin decidir se cuenta como una
            # decisión ilegible y el aviso tapa las que sí lo son.
            posicion = indice.get(nombre)
            if posicion is None or fila[posicion] is None:
                return ""
            return flatten(fila[posicion])

        etiqueta = campo("tu decisión").lower()
        etiqueta = SINONIMOS.get(etiqueta, etiqueta)
        lema = campo("lema")
        if not etiqueta:
            pendientes += 1
            continue
        if etiqueta not in (ACEPTA, CORRIGE, LITERAL):
            ilegibles.append(f"{lema}: «{etiqueta}»")
            continue
        decisiones.append(
            {
                "lema": lema,
                "decision": etiqueta,
                "texto": campo("tu texto"),
                "propuesta": campo("propuesta"),
                "literal": campo("literal propuesto"),
            }
        )
    return decisiones, pendientes, ilegibles


def resolver(decision: dict[str, str]) -> tuple[str, str] | None:
    """La acepción y el literal que quedan, o None si la fila no da para tanto."""
    if decision["decision"] == ACEPTA:
        return decision["propuesta"], decision["literal"]
    if decision["decision"] == CORRIGE:
        return (decision["texto"], decision["literal"]) if decision["texto"] else None
    if decision["decision"] == LITERAL:
        return (
            (decision["literal"], decision["literal"]) if decision["literal"] else None
        )
    return None


def leer_generado(ruta: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Las traducciones ya aceptadas, con su cabecera intacta."""
    cabecera: list[str] = []
    filas: dict[str, list[str]] = {}
    if not ruta.exists():
        return cabecera, filas
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.startswith("#"):
            cabecera.append(linea)
        elif linea.strip():
            campos = linea.split("\t")
            filas[campos[0]] = campos
    return cabecera, filas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hoja", type=Path, default=HOJA)
    parser.add_argument("--generado", type=Path, default=GENERADO)
    parser.add_argument("--dry-run", action="store_true", help="no escribe nada")
    args = parser.parse_args()

    pr.tic()
    pr.yellow_title("aplicando la revisión")
    decisiones, pendientes, ilegibles = leer_decisiones(args.hoja)
    pr.summary("decididas", len(decisiones))
    if pendientes:
        pr.summary("sin decidir", pendientes)
    for problema in ilegibles:
        pr.red(f"decisión no reconocida — {problema}")

    db_session = get_db_session(ProjectPaths().dpd_db_path)
    lemas = {d["lema"] for d in decisiones}
    headwords = {
        h.lemma_1: h
        for h in db_session.query(DpdHeadword)
        .filter(DpdHeadword.lemma_1.in_(lemas))
        .all()
    }

    cabecera, filas = leer_generado(args.generado)
    nuevas = cambiadas = sin_texto = 0
    for decision in decisiones:
        resuelto = resolver(decision)
        if resuelto is None:
            sin_texto += 1
            pr.red(
                f"{decision['lema']}: «{decision['decision']}» sin texto que aplicar"
            )
            continue
        headword = headwords.get(decision["lema"])
        if headword is None:
            pr.red(f"{decision['lema']}: ya no está en dpd.db")
            continue
        acepcion, literal = resuelto
        with spanish.suppressed():
            construccion = headword.construction_summary
        fila = [decision["lema"], acepcion, literal, "", headword.pos, construccion]
        if decision["lema"] in filas:
            cambiadas += filas[decision["lema"]] != fila
        else:
            nuevas += 1
        filas[decision["lema"]] = fila

    pr.summary("altas", nuevas)
    if cambiadas:
        pr.summary("modificadas", cambiadas)
    if sin_texto:
        pr.summary("sin aplicar", sin_texto)

    if args.dry_run:
        pr.amber("--dry-run: no se ha escrito nada")
        pr.toc()
        return

    from tools.pali_sort_key import pali_sort_key

    lineas = cabecera or ["# " + "\t".join(COLUMNAS)]
    lineas += ["\t".join(filas[lema]) for lema in sorted(filas, key=pali_sort_key)]
    args.generado.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    pr.summary("traducciones en total", len(filas))
    pr.summary("escrito", str(args.generado))
    pr.toc()


if __name__ == "__main__":
    main()
