#!/usr/bin/env python3

"""Extrae de Apertium la categoría léxica de cada lema español.

El `.metadix` de apertium-spa declara cada entrada con el paradigma que sigue, y
el nombre del paradigma acaba en la categoría:

    <e lm="resplandor">   <i>resplandor</i><par n="abril__n"/></e>
    <e lm="destellante"><i>destellante</i><par n="aberrante__adj"/></e>

De ahí sale un mapa lema → categorías, que es lo que necesitamos: no la
categoría que una palabra tiene en una frase, sino las que puede tener.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).parent
ENTRADA = RAIZ / "spa.metadix"
SALIDA = RAIZ / "lexico_es.json"
PATRON = re.compile(r'<e\b[^>]*lm="([^"]+)"[^>]*>.*?par n="[^"]*?__([a-z]+)"')


def construir(texto: str) -> dict[str, list[str]]:
    """Mapa lema → categorías, en orden estable."""
    lexico: dict[str, set[str]] = defaultdict(set)
    for coincidencia in PATRON.finditer(texto):
        lexico[coincidencia.group(1).lower()].add(coincidencia.group(2))
    return {lema: sorted(cats) for lema, cats in sorted(lexico.items())}


def main() -> None:
    if not ENTRADA.exists():
        raise SystemExit(f"falta {ENTRADA}; ejecuta preparar.sh")
    lexico = construir(ENTRADA.read_text(encoding="utf-8"))
    SALIDA.write_text(
        json.dumps(lexico, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    print(f"{len(lexico)} lemas escritos en {SALIDA.name}")


if __name__ == "__main__":
    main()
