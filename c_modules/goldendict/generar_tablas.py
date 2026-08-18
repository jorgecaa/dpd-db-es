"""Genera las tablas de datos que el piloto en C necesita compiladas dentro.

Escribe dos cabeceras:

``ipa_tabla.h``
    IASTPali -> IPA. `DpdHeadword.lemma_ipa` llama a aksharamukha, que no es
    portable a C, pero la conversión resulta ser un mapeo por unidades con
    coincidencia más larga primero: dígrafos consonante+h, vocal+ṃ, y letras
    sueltas. Verificado al 100 % sobre 20 000 lemas al azar.

``trad_tabla.h``
    patrón -> terminación, para `make_lemma_trad_clean`.

Uso desde la raíz del proyecto:
    uv run python c_modules/goldendict/generar_tablas.py c_modules/goldendict
"""

import random
import re
import sys
from pathlib import Path

from aksharamukha import transliterate

from db.db_helpers import get_db_session
from db.models import DpdHeadword
from tools.lemma_traditional import lemma_trad_dict
from tools.paths import ProjectPaths
from tools.printer import printer as pr

CONSONANTES = "kgcjtdpbṭḍ"
VOCALES = "aāiīuūeo"
MUESTRA_VERIFICACION = 20_000


def unidades_de(alfabeto: list[str]) -> list[str]:
    """Las unidades del mapeo, de más larga a más corta."""
    digrafos = [c + "h" for c in CONSONANTES]
    digrafos += [c.upper() + "h" for c in CONSONANTES]
    nasales = [v + "ṃ" for v in VOCALES]
    return nasales + digrafos + alfabeto


def transliterar_con(mapa: dict[str, str], lemma: str) -> str | None:
    """Aplica el mapa por coincidencia más larga; None si algo no encaja."""
    salida: list[str] = []
    i = 0
    while i < len(lemma):
        if i + 1 < len(lemma) and lemma[i : i + 2] in mapa:
            salida.append(mapa[lemma[i : i + 2]])
            i += 2
        elif lemma[i] in mapa:
            salida.append(mapa[lemma[i]])
            i += 1
        else:
            return None
    return "".join(salida)


def escribir_cabecera(
    ruta: Path, nombre: str, pares: list[tuple[str, str]], comentario: str
) -> None:
    lineas = [f"/* {comentario} */", f"static const char *{nombre}[][2] = {{"]
    lineas += [f'    {{"{clave}", "{valor}"}},' for clave, valor in pares]
    lineas += ["    {NULL, NULL}", "};", ""]
    ruta.write_text("\n".join(lineas), encoding="utf-8")


def main() -> None:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    pth = ProjectPaths()
    db = get_db_session(pth.dpd_db_path)

    pr.green_title("generando tablas para el piloto en C")

    lemas = [re.sub(r" \d.*$", "", fila[0]) for fila in db.query(DpdHeadword.lemma_1)]
    alfabeto = sorted({c for lemma in lemas for c in lemma})

    pr.green_tmr("construyendo el mapa IPA")
    mapa = {
        unidad: str(transliterate.process("IASTPali", "IPA", unidad))
        for unidad in unidades_de(alfabeto)
    }
    pr.yes(str(len(mapa)))

    pr.green_tmr("verificando contra aksharamukha")
    random.seed(11)
    muestra = random.sample(lemas, min(MUESTRA_VERIFICACION, len(lemas)))
    fallos = [
        lemma
        for lemma in muestra
        if lemma
        and transliterar_con(mapa, lemma)
        != str(transliterate.process("IASTPali", "IPA", lemma))
    ]
    if fallos:
        pr.no(f"{len(fallos)} fallos")
        pr.red(f"el mapeo no reproduce aksharamukha, p.ej. {fallos[:3]}")
        sys.exit(1)
    pr.yes(f"{len(muestra)} ok")

    # unidades más largas primero, para que la coincidencia en C sea correcta
    orden = sorted(mapa, key=lambda u: (-len(u.encode()), u))
    escribir_cabecera(
        destino / "ipa_tabla.h",
        "IPA_TABLA",
        [(u, mapa[u]) for u in orden if u.strip()],
        "GENERADO por generar_tablas.py desde aksharamukha (IASTPali -> IPA)."
        " Unidades largas primero. No editar.",
    )
    escribir_cabecera(
        destino / "trad_tabla.h",
        "TRAD_TABLA",
        sorted(lemma_trad_dict.items()),
        "GENERADO por generar_tablas.py desde tools/lemma_traditional.py:"
        " patrón -> terminación. No editar.",
    )
    pr.summary("ipa_tabla.h", str(len(orden)))
    pr.summary("trad_tabla.h", str(len(lemma_trad_dict)))


if __name__ == "__main__":
    main()
