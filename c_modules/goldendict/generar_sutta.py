"""Compila el bloque ``sutta_info`` de la plantilla a C.

Ese bloque son 900 de las 1288 líneas de ``dpd_headword.jinja`` y 162
construcciones de control: transcribirlo a mano sería una fuente inagotable de
divergencias. En vez de eso se lee la plantilla, se le aplica la **misma**
traducción al español que usa el exportador (``translate_template_source``, el
mismo punto de entrada que engancha ``exporter/jinja2_env.py``) y se traduce el
subconjunto de Jinja que el bloque emplea — ``if``/``elif``/``else``/``endif``
y ``{{ expr }}`` — a un cuerpo de función en C.

Los espacios importan: el entorno usa ``trim_blocks`` y ``lstrip_blocks``, así
que aquí se reproducen los dos antes de trocear.

Ante cualquier expresión o condición fuera del subconjunto **falla**, en lugar
de adivinar: un error de generación se ve, un HTML silenciosamente distinto no.

Uso desde la raíz del proyecto:
    uv run python c_modules/goldendict/generar_sutta.py c_modules/goldendict/sutta_gen.c
"""

import json
import re
import sys
from pathlib import Path

# privadas de db/models.py a propósito: son la fuente de verdad de los
# anclas de s.4nt.org, y duplicarlas aquí las dejaría desincronizadas
from db.models import _S4NT_ANCHOR_OVERRIDES, _S4NT_KN_BOOKS
from tools.paths import ProjectPaths
from tools.printer import printer as pr
from tools.spanish import load_ui, translate_template_source

INICIO = '{% if d.i.needs_sutta_info_button %}\n<div id="sutta_info_'

# Propiedades calculadas del modelo SuttaInfo: las resuelve sutta.c, no salen
# de una columna.
CALCULADAS = frozenset(
    {
        "has_tpr",
        "tpp_org",
        "sc_card_link",
        "sc_pali_link",
        "sc_eng_link",
        "sc_vagga_link",
        "sc_github",
        "sc_express_link",
        "dhamma_gift",
        "tbw",
        "tbw_legacy",
        "sc_voice_link",
        "s_4nt_link",
        "bjt_github_link",
        "bjt_tipitaka_lk_link",
        "bjt_open_tipitaka_lk_link",
        "dv_exists",
        "dv_parallels_exists",
    }
)

# Columnas cuyo nombre lleva diacríticos y no vale como identificador C.
RENOMBRES = {
    "bjt_piṭaka": "bjt_pitaka",
    "bjt_nikāya": "bjt_nikaya",
    "dv_āgamas_parallels": "dv_agamas_parallels",
    "dv_partial_parallels_nā": "dv_partial_parallels_na",
}

BOOLEANAS = ("is_vagga", "is_samyutta", "is_nipata", "is_pannasaka")


class Compilador:
    """Traduce el bloque a sentencias C, anotando qué campos usa."""

    def __init__(self) -> None:
        self.campos: set[str] = set()
        self.lineas: list[str] = []
        self.nivel = 1

    # --- expresiones ---

    def campo(self, nombre: str) -> str:
        self.campos.add(nombre)
        return "su->" + RENOMBRES.get(nombre, nombre)

    def expresion(self, expr: str) -> str:
        expr = expr.strip()
        simple = re.fullmatch(r"d\.su\.(\w+)", expr, re.UNICODE)
        if simple:
            return self.campo(simple.group(1))
        minus = re.fullmatch(r"d\.su\.(\w+)\.lower\(\)", expr, re.UNICODE)
        if minus:
            return f"minusculas({self.campo(minus.group(1))})"
        if expr == "d.su.bjt_open_tipitaka_lk_link.replace('latn', 'deva')":
            self.campo("bjt_open_tipitaka_lk_link")
            return 'reemplaza(su->bjt_open_tipitaka_lk_link, "latn", "deva")'
        directas = {
            "d.i.id": "id_str",
            "d.i.lemma_1_": "lemma_",
            "d.i.lemma_link": "lemma_link_str",
            "d.today": "hoy",
        }
        if expr in directas:
            return directas[expr]
        sys.exit(f"expresión fuera del subconjunto: {expr!r}")

    def condicion(self, cond: str) -> str:
        piezas: list[str] = []
        for tok in re.findall(r"\(|\)|[\w.()]+|\S", cond, re.UNICODE):
            if tok == "not":
                piezas.append("!")
            elif tok == "and":
                piezas.append("&&")
            elif tok == "or":
                piezas.append("||")
            elif tok in ("(", ")"):
                piezas.append(tok)
            elif tok in BOOLEANAS:
                piezas.append(f"su_{tok}(su)")
            elif tok == "show_tbw":
                # jinja_env.globals["show_tbw"], puesto desde config.ini
                piezas.append("SHOW_TBW")
            elif tok == "d.i.needs_sutta_info_button":
                # la decide quien llama; dentro del bloque siempre es cierta
                piezas.append("1")
            elif tok.startswith("d.su."):
                piezas.append(f"vacio_no({self.campo(tok[5:])})")
            else:
                sys.exit(f"condición fuera del subconjunto: {tok!r}")
        return " ".join(piezas)

    # --- emisión ---

    def emitir(self, codigo: str) -> None:
        self.lineas.append("    " * self.nivel + codigo)

    def literal(self, texto: str) -> None:
        if not texto:
            return
        escapado = texto.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        self.emitir(f'buf_add(b, "{escapado}");')

    def etiqueta(self, tag: str) -> None:
        if tag.startswith("if "):
            self.emitir(f"if ({self.condicion(tag[3:])}) {{")
            self.nivel += 1
        elif tag.startswith("elif "):
            self.nivel -= 1
            self.emitir(f"}} else if ({self.condicion(tag[5:])}) {{")
            self.nivel += 1
        elif tag == "else":
            self.nivel -= 1
            self.emitir("} else {")
            self.nivel += 1
        elif tag == "endif":
            self.nivel -= 1
            self.emitir("}")
        else:
            sys.exit(f"etiqueta fuera del subconjunto: {tag!r}")

    def compilar(self, bloque: str) -> None:
        patron = re.compile(r"\{%-?\s*(.*?)\s*-?%\}|\{\{\s*(.*?)\s*\}\}", re.S)
        pos = 0
        for m in patron.finditer(bloque):
            self.literal(bloque[pos : m.start()])
            pos = m.end()
            if m.group(1) is not None:
                self.etiqueta(m.group(1))
            else:
                self.emitir(f"buf_add(b, {self.expresion(m.group(2))});")
        self.literal(bloque[pos:])


def extraer_bloque(fuente: str) -> str:
    """Del {% if needs_sutta_info_button %} hasta su {% endif %} pareja."""
    inicio = fuente.index(INICIO)
    profundidad = 0
    for m in re.finditer(
        r"\{%-?\s*(if|elif|else|endif)\b.*?%\}", fuente[inicio:], re.S
    ):
        if m.group(1) == "if":
            profundidad += 1
        elif m.group(1) == "endif":
            profundidad -= 1
            if profundidad == 0:
                return fuente[inicio : inicio + m.end()]
    sys.exit("no se encontró el endif de cierre del bloque sutta_info")


def aplicar_espacios(bloque: str) -> str:
    """lstrip_blocks + trim_blocks, tal como los tiene el entorno real."""
    bloque = re.sub(r"(?m)^[ \t]+(?=\{%)", "", bloque)
    return bloque.replace("%}\n", "%}")


def escribir(destino: Path, comp: Compilador) -> None:
    codigos_tpr = sorted(
        json.loads(ProjectPaths().tpr_codes_json_path.read_text(encoding="utf-8"))
    )
    columnas = sorted(c for c in comp.campos if c not in CALCULADAS)
    calculadas = sorted(c for c in comp.campos if c in CALCULADAS)

    partes: list[str] = [
        "/* GENERADO por generar_sutta.py desde el bloque sutta_info de",
        " * dpd_headword.jinja, ya traducido al español. No editar a mano. */",
        "",
        "/* columnas de sutta_info: " + " ".join(columnas) + " */",
        "/* propiedades calculadas: " + " ".join(calculadas) + " */",
        "",
        "static const char *TPR_CODES[] = {",
    ]
    partes += [f'    "{c}",' for c in codigos_tpr]
    partes += ["    NULL", "};", "", "static const char *S4NT_OVERRIDES[][2] = {"]
    partes += [
        f'    {{"{k}", "{v}"}},' for k, v in sorted(_S4NT_ANCHOR_OVERRIDES.items())
    ]
    partes += ["    {NULL, NULL}", "};", "", "static const char *S4NT_KN[] = {"]
    partes += [f'    "{c}",' for c in sorted(_S4NT_KN_BOOKS)]
    partes += [
        "    NULL",
        "};",
        "",
        "static void emitir_sutta_info(Buffer *b, const Sutta *su,",
        "                              const char *id_str, const char *lemma_,",
        "                              const char *lemma_link_str, const char *hoy) {",
    ]
    partes += comp.lineas
    partes += ["}", ""]
    destino.write_text("\n".join(partes), encoding="utf-8")

    pr.summary("columnas", str(len(columnas)))
    pr.summary("calculadas", str(len(calculadas)))
    pr.summary("códigos tpr", str(len(codigos_tpr)))


def main() -> None:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sutta_gen.c")
    pr.green_title("compilando el bloque sutta_info a C")

    plantilla = Path("exporter/goldendict/templates/dpd_headword.jinja")
    fuente = translate_template_source(plantilla.read_text(encoding="utf-8"), load_ui())
    comp = Compilador()
    comp.compilar(aplicar_espacios(extraer_bloque(fuente)))
    escribir(destino, comp)
    pr.summary("escrito", str(destino))


if __name__ == "__main__":
    main()
