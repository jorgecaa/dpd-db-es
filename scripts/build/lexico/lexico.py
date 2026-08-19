#!/usr/bin/env python3

"""Analizador morfológico del español, para revisar nuestras traducciones.

Dos preguntas, dos herramientas, y no son intercambiables:

    ¿esta palabra existe?          →  hunspell + diccionario de LibreOffice
    ¿puede ser sustantivo?         →  léxico de Apertium

La segunda es la que detecta un adjetivo puesto donde el inglés nombra una
acción («destellante» por «destello»), y responde `None` cuando el lema no está
en el léxico: eso es «no lo sé», no «no es sustantivo».

Uso:
    ./analisis/.venv/bin/python analisis/lexico.py destellante consonante accion
"""

import json
import subprocess
import sys
from functools import cache
from pathlib import Path

RAIZ = Path(__file__).parent
LEXICO = RAIZ / "lexico_es.json"
DICCIONARIO = RAIZ / "es"  # hunspell añade .dic y .aff
ACENTOS = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "n": "ñ"}


@cache
def _lexico() -> dict[str, list[str]]:
    if not LEXICO.exists():
        raise SystemExit(f"falta {LEXICO.name}; ejecuta analisis/preparar.sh")
    return json.loads(LEXICO.read_text(encoding="utf-8"))


def categorias(palabra: str) -> list[str]:
    """Categorías que el léxico atribuye al lema, vacío si no lo conoce."""
    return _lexico().get(palabra.lower(), [])


def puede_ser_sustantivo(palabra: str) -> bool | None:
    """True, False, o None cuando la palabra no está en el léxico."""
    cats = categorias(palabra)
    return "n" in cats if cats else None


def desconocidas(palabras: list[str]) -> set[str]:
    """Las que hunspell no reconoce como español."""
    if not palabras:
        return set()
    salida = subprocess.run(
        ["hunspell", "-d", str(DICCIONARIO), "-l"],
        input="\n".join(palabras),
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return set(salida.split())


def falta_tilde(palabra: str) -> str | None:
    """La misma palabra con una tilde o eñe, si así sí existe y sin ella no.

    Es lo que cazó «accion», «murio» y «ensenanza» en el corpus publicado. Solo
    afirma cuando hay una única variante válida: «practica» y «práctica» son
    ambas palabras, y ahí no hay nada que decidir sin contexto.
    """
    if palabra not in desconocidas([palabra]):
        return None
    variantes = [
        palabra[:i] + ACENTOS[letra] + palabra[i + 1 :]
        for i, letra in enumerate(palabra)
        if letra in ACENTOS
    ]
    malas = desconocidas(variantes)
    validas = [v for v in variantes if v not in malas]
    return validas[0] if len(validas) == 1 else None


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for palabra in sys.argv[1:]:
        cats = categorias(palabra) or ["no está en el léxico"]
        existe = "sí" if palabra not in desconocidas([palabra]) else "no"
        linea = f"{palabra:16s} existe: {existe:3s} categorías: {', '.join(cats)}"
        correccion = falta_tilde(palabra)
        if correccion:
            linea += f"  → ¿{correccion}?"
        print(linea)


if __name__ == "__main__":
    main()
