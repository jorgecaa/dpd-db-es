"""Vuelca el HTML del exportador de Python, como referencia de paridad del C.

Escribe un archivo por entrada, más ``indice.json`` e ``ids.txt`` para que el
piloto genere exactamente las mismas.

Por defecto elige una muestra deliberadamente variada — distintas partes del
discurso, con y sin ejemplos, con raíz, compuestos, suttas de cada libro —
porque una muestra consecutiva por lema no ejercita las ramas de la plantilla.

    uv run python c_modules/goldendict/referencia.py --salida /tmp/ref --n 300
    uv run python c_modules/goldendict/referencia.py --salida /tmp/ref --ids ids.txt

Sin ``--minificar`` la salida se deja sin minificar, que es lo útil mientras se
depura una sección: así las diferencias se leen. Con ``--minificar`` reproduce
la salida final exacta.
"""

import argparse
import json
from pathlib import Path
from typing import Any

import exporter.goldendict.export_dpd as export_dpd
from db.db_helpers import get_db_session
from db.models import (
    DpdHeadword,
    FamilyCompound,
    FamilyIdiom,
    FamilyRoot,
    FamilySet,
    FamilyWord,
    SuttaInfo,
)
from exporter.goldendict.export_dpd import (
    _lookup_family_compounds,
    _lookup_family_idioms,
    _lookup_family_set,
    render_pali_word_dpd_html,
)
from exporter.jinja2_env import get_jinja2_env
from tools.configger import config_test
from tools.paths import ProjectPaths
from tools.printer import printer as pr
from sqlalchemy.orm import Session


def ids_variados(db: Session, total: int) -> list[int]:
    """Una muestra que toca cada rama interesante de la plantilla."""
    perfiles = [
        DpdHeadword.pos == "prefix",
        (DpdHeadword.pos == "pr") & (DpdHeadword.example_1 != ""),
        (DpdHeadword.pos == "masc") & (DpdHeadword.root_key != ""),
        DpdHeadword.compound_type != "",
        DpdHeadword.example_1 == "",
        DpdHeadword.example_2 != "",
        DpdHeadword.commentary != "",
        DpdHeadword.pos == "ind",
    ]
    ids: list[int] = []
    for filtro in perfiles:
        filas = (
            db.query(DpdHeadword.id)
            .filter(filtro)
            .order_by(DpdHeadword.lemma_1)
            .limit(4)
            .all()
        )
        ids.extend(fila[0] for fila in filas)

    # suttas: hasta 8 por libro, que es donde vive el bloque sutta_info
    for (libro,) in db.query(SuttaInfo.book_code).distinct():
        filas = (
            db.query(DpdHeadword.id)
            .join(SuttaInfo, DpdHeadword.lemma_1 == SuttaInfo.dpd_sutta)
            .filter(SuttaInfo.book_code == libro)
            .order_by(DpdHeadword.id)
            .limit(8)
            .all()
        )
        ids.extend(fila[0] for fila in filas)

    vistos = set(ids)
    resto = db.query(DpdHeadword.id).order_by(DpdHeadword.lemma_1).limit(total * 2)
    for (rid,) in resto:
        if len(ids) >= total:
            break
        if rid not in vistos:
            ids.append(rid)
            vistos.add(rid)
    return ids[:total]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", required=True, type=Path)
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--ids", type=Path, help="lista de ids, uno por línea")
    parser.add_argument(
        "--minificar",
        action="store_true",
        help="minifica el cuerpo, como hace la exportación real",
    )
    args = parser.parse_args()

    if not args.minificar:
        # el piloto tiene el mismo interruptor; sin minificar las diferencias
        # se leen, que es lo que hace falta mientras se porta una sección
        # pyrefly: ignore  # sustituir minify por la identidad es el objetivo
        export_dpd.minify = lambda html: html

    pr.green_title("volcando la referencia de Python")
    args.salida.mkdir(parents=True, exist_ok=True)

    pth = ProjectPaths()
    db = get_db_session(pth.dpd_db_path)

    fc_map = {x.compound_family: x for x in db.query(FamilyCompound).all()}
    fi_map = {x.idiom: x for x in db.query(FamilyIdiom).all()}
    fs_map = {x.set: x for x in db.query(FamilySet).all()}

    if args.ids:
        ids = [int(linea) for linea in args.ids.read_text().split()]
    else:
        ids = ids_variados(db, args.n)

    jinja_env = get_jinja2_env("exporter/goldendict/templates")
    # el exportador real la pone en _worker_init; sin esto la fila TBW Legacy
    # sale distinta
    # pyrefly: ignore  # jinja2 deja Environment.globals sin anotar, igual que
    # en exporter/goldendict/export_dpd.py
    jinja_env.globals["show_tbw"] = config_test("dictionary", "show_tbw", "yes")
    render_data: dict[str, Any] = {
        "pth": pth,
        "jinja_env": jinja_env,
        "speech_marks": {},
        "cf_set": set(),
        "idioms_set": set(),
        "show_id": False,
    }

    indice: list[dict[str, Any]] = []
    for contador, hid in enumerate(ids, 1):
        fila = (
            db.query(DpdHeadword, FamilyRoot, FamilyWord)
            .outerjoin(
                FamilyRoot, DpdHeadword.root_family_key == FamilyRoot.root_family_key
            )
            .outerjoin(FamilyWord, DpdHeadword.family_word == FamilyWord.word_family)
            .filter(DpdHeadword.id == hid)
            .one_or_none()
        )
        if fila is None:
            pr.red(f"id {hid} no existe, se salta")
            continue
        pw, fr, fw = fila
        partes: dict[str, Any] = {
            "pali_word": pw,
            "pali_root": pw.rt,
            "family_root": fr,
            "family_word": fw,
            "family_compounds": _lookup_family_compounds(pw, fc_map),
            "family_idioms": _lookup_family_idioms(pw, fi_map),
            "family_set": _lookup_family_set(pw, fs_map),
            "sutta_info": pw.su,
        }
        entrada, _ = render_pali_word_dpd_html(partes, render_data)  # type: ignore[arg-type]
        (args.salida / f"{hid}.html").write_text(
            entrada.definition_html, encoding="utf-8"
        )
        indice.append(
            {
                "id": hid,
                "lemma_1": pw.lemma_1,
                "pos": pw.pos,
                "bytes": len(entrada.definition_html),
            }
        )
        if contador % 100 == 0:
            pr.counter(contador, len(ids), pw.lemma_1)

    (args.salida / "indice.json").write_text(
        json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (args.salida / "ids.txt").write_text(
        "\n".join(str(x["id"]) for x in indice) + "\n", encoding="utf-8"
    )
    total = sum(int(x["bytes"]) for x in indice)
    pr.summary("entradas", str(len(indice)))
    pr.summary("tamaño", f"{total / 1024:.1f} KB")
    pr.summary("minificado", "sí" if args.minificar else "no")


if __name__ == "__main__":
    main()
