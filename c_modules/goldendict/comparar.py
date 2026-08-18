"""Compara la salida del piloto en C con la referencia de Python.

    uv run python c_modules/goldendict/comparar.py <dir_referencia> <dir_c>

Da tres medidas, de más exigente a más indulgente:

1. **idénticas** — byte a byte, que es el objetivo y lo único que cuenta como
   paridad;
2. **parecido medio** — similitud de secuencia, sensible al orden, útil para
   ver si un cambio acerca o aleja;
3. **cobertura de texto** — qué fracción del texto visible de la referencia
   aparece también en el piloto, insensible al orden y al marcado, que es la
   medida que sirve mientras una sección todavía no está en su sitio.

Sale con código 1 si algo difiere, para poder encadenarlo en un Makefile.
"""

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from tools.printer import printer as pr

ETIQUETA = re.compile(r"<[^>]+>")
ESPACIOS = re.compile(r"\s+")
SEPARADORES = re.compile(r"[\s;,|]+")


def texto_visible(html: str) -> str:
    return ESPACIOS.sub(" ", ETIQUETA.sub(" ", html)).strip()


def fragmentos(texto: str) -> set[str]:
    return {t for t in SEPARADORES.split(texto.lower()) if len(t) > 1}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    ref_dir, c_dir = Path(sys.argv[1]), Path(sys.argv[2])
    indice = json.loads((ref_dir / "indice.json").read_text(encoding="utf-8"))

    identicas = 0
    parecidos: list[float] = []
    coberturas: list[tuple[str, float]] = []
    sin_generar: list[str] = []
    primera_diferencia: tuple[str, str, str] | None = None

    for entrada in indice:
        hid = entrada["id"]
        c_file = c_dir / f"{hid}.html"
        if not c_file.exists():
            sin_generar.append(f"{hid} ({entrada['lemma_1']})")
            continue
        ref = (ref_dir / f"{hid}.html").read_text(encoding="utf-8")
        got = c_file.read_text(encoding="utf-8")

        if ref == got:
            identicas += 1
        elif primera_diferencia is None:
            for tag, i1, i2, j1, j2 in SequenceMatcher(None, ref, got).get_opcodes():
                if tag != "equal":
                    primera_diferencia = (
                        entrada["lemma_1"],
                        repr(ref[max(0, i1 - 50) : i2 + 25]),
                        repr(got[max(0, j1 - 50) : j2 + 25]),
                    )
                    break

        parecidos.append(SequenceMatcher(None, ref, got).ratio())
        f_ref = fragmentos(texto_visible(ref))
        f_got = fragmentos(texto_visible(got))
        cobertura = len(f_ref & f_got) / len(f_ref) if f_ref else 1.0
        coberturas.append((entrada["lemma_1"], cobertura))

    n = len(parecidos)
    if not n:
        pr.red("no se comparó ninguna entrada")
        return 1

    pr.green_title("paridad con el exportador de Python")
    pr.summary("comparadas", f"{n} de {len(indice)}")
    pr.summary("idénticas", f"{identicas}/{n} ({100 * identicas / n:.1f} %)")
    pr.summary("parecido medio", f"{100 * sum(parecidos) / n:.1f} %")
    media_cobertura = sum(c for _, c in coberturas) / n
    pr.summary("cobertura de texto", f"{100 * media_cobertura:.1f} %")

    if sin_generar:
        pr.red(f"el piloto no generó {len(sin_generar)}: {', '.join(sin_generar[:5])}")
    if identicas < n:
        peores = sorted(coberturas, key=lambda x: x[1])[:5]
        pr.amber(
            "peor cobertura: "
            + ", ".join(f"{lema} {100 * c:.0f} %" for lema, c in peores)
        )
        if primera_diferencia:
            lema, esperado, obtenido = primera_diferencia
            pr.amber(f"primera diferencia, en {lema}:")
            pr.white(f"  referencia: {esperado}")
            pr.white(f"  piloto:     {obtenido}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
