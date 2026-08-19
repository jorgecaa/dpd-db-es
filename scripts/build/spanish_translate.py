#!/usr/bin/env python3

"""Run the batches of pending headwords through translation and audit.

`spanish_pending.py` writes the batches, `spanish_review.py` sorts the results;
this is what happens in between, and it is two passes rather than one. Every
batch is first translated, then audited by a second request that never sees how
the first one analysed the word — the auditor is given the DPD entry and the
finished Spanish, and works out the morphology from scratch. Measured over 120
headwords, that blind analysis agreed with the translator's on 118 and caught
four wrong translations, three of which the translator had declared confident.

Both passes answer in the same closed vocabulary, and an answer that breaks the
contract is not accepted: it is retried, and a batch that keeps failing is left
untouched for the next run rather than written half-formed.

Resumable by design. A batch whose output already validates is skipped, so an
interrupted run costs nothing and a failed batch can be retried on its own.

On workers: `AIManager` keeps its rate-limit clock in a plain dict, so several
threads sharing one would race it. Each worker therefore builds its own manager,
which means N workers issue N times the requests per minute. Size `--workers` to
what the provider allows, not to the machine.

Usage:
    uv run python scripts/build/spanish_translate.py --batches <dir> [--workers 2]
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

from scripts.build.spanish_review import check_audit, check_translation
from tools.ai_manager import AIManager
from tools.printer import printer as pr

FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

TRANSLATOR_SYSTEM = """\
Trabajas con el método de Helmer Smith, el filólogo del Critical Pāli Dictionary \
y editor del Saddanīti: la forma antes que el sentido. Un participio no se \
traduce como adjetivo, un absolutivo no se traduce como agente, un causativo no \
se traduce como intransitivo. Antes de elegir una palabra española determinas qué \
es morfológicamente la palabra pali, y el español reproduce esa categoría."""

TRANSLATOR_PROMPT = """\
Traduce al español las acepciones inglesas de estas entradas del Digital Pāḷi \
Dictionary.

MÉTODO
1. Analiza la forma con `pos`, `gramatica` y `construccion`. El inglés del DPD es \
a veces ambiguo donde el pali no lo es: «moves away» puede ser causativo \
transitivo, «knowing» puede ser un absolutivo.
2. Localiza la palabra en los `ejemplos` (va marcada con <b>) y mira qué rige, de \
qué depende y con qué concuerda.
3. Consulta las `glosas_externas` sólo si la forma sigue en duda. No mandan sobre \
el DPD.
4. Elige el término según `terminologia_del_material`: son las equivalencias que \
ya usa la traducción española publicada de este mismo diccionario, con su número \
de apariciones. Si te apartas, declara la desviación con su motivo.
5. El español reproduce la categoría gramatical del pali, no la del inglés cuando \
difieran.

REGLAS
- Traducción, no paráfrasis: traduce la `acepcion_inglesa`, no la expliques, no la \
amplíes, no devuelvas inglés.
- Conserva el número y el orden de los sentidos separados por «; ».
- «(comm)» se escribe «(comentario)» y «(gram)» se escribe «(gramática)». Conserva \
los paréntesis aclaratorios, el «lit.» y los códigos de referencia tal cual.
- Deja en pali lo que el diccionario deja en pali: Vinaya, Dhamma, niggahīta, \
yojana, nombres propios y topónimos.
- Los títulos de sección van como «Capítulo 12 del Majjhima Nikāya (MN111-120)».
- Si no estás seguro, confianza «media» o «baja» con la razón. Un hueco señalado \
vale más que una invención.

SALIDA: sólo un array JSON, un objeto por entrada y en el mismo orden, sin texto \
alrededor y sin vallas de código. Cada objeto:

{{"lema": "<tal cual>", "meaning_1_es": "...", "meaning_lit_es": "<o vacío>",
 "confianza": "alta|media|baja",
 "forma": {{"categoria": "<una de: {categorias}>",
            "decidio_traduccion": true|false, "explicacion": "<obligatoria si true>"}},
 "terminologia": {{"seguida": true|false,
            "desviaciones": [{{"termino_ingles": "...", "material_dice": "...",
                              "elegido": "...", "motivo": "..."}}]}},
 "glosa_externa": {{"consultada": true|false, "decisiva": true|false, "cual": ""}},
 "duda": {{"hay": true|false, "tipo": "<una de: {tipos_duda}>", "detalle": "<obligatorio si hay>"}}}}

Vocabulario cerrado: nunca inventes un valor; si nada encaja, «otro». Ningún texto \
con saltos de línea.

ENTRADAS:
{entradas}"""

AUDITOR_SYSTEM = """\
Eres auditor filológico y trabajas con el método de Helmer Smith. Tu trabajo no es \
traducir: es intentar refutar traducciones ya hechas. Partes de la sospecha de que \
la traducción puede estar mal; si no encuentras defecto lo dices, pero búscalo."""

AUDITOR_PROMPT = """\
Audita estas traducciones al español del Digital Pāḷi Dictionary. No se te dice qué \
análisis morfológico hizo quien tradujo: haz el tuyo desde cero, que es la prueba.

QUÉ COMPRUEBAS
1. La categoría morfológica, con `pos`, `gramatica`, `construccion` y el uso en el \
ejemplo.
2. Si el español reproduce esa categoría. Un absolutivo pide «habiendo…», un \
causativo pide transitivo, un participio de presente pasivo pide «siendo…». Que el \
inglés del DPD sea ambiguo no excusa un español que cambie la categoría.
3. Fidelidad: ¿traduce la `acepcion_inglesa` o la parafrasea, la explica o la \
amplía? ¿Conserva el número y el orden de los sentidos? ¿Mantiene «(comentario)», \
«(gramática)», los paréntesis aclaratorios y los códigos de referencia?
4. El literal, con el mismo criterio.

SALIDA: sólo un array JSON, un objeto por caso y en el mismo orden, sin texto \
alrededor y sin vallas de código. Cada objeto:

{{"lema": "<tal cual>", "categoria_segun_auditor": "<una de: {categorias}>",
 "reproduce_la_forma": true|false, "fiel_a_la_acepcion": true|false,
 "veredicto": "correcta|dudosa|incorrecta",
 "problema": {{"hay": true|false, "tipo": "<una de: {tipos_problema}>",
               "detalle": "<obligatorio si hay>"}},
 "correccion_propuesta": "<obligatoria si es incorrecta, si no vacía>"}}

Vocabulario cerrado: nunca inventes un valor; si nada encaja, «otro».

CASOS:
{casos}"""

CATEGORIES = (
    "sustantivo, sustantivo_accion, adjetivo, adjetivo_posesivo, participio_pasado, "
    "participio_presente_activo, participio_presente_pasivo, participio_futuro_pasivo, "
    "absolutivo, infinitivo, presente, aoristo, futuro, optativo, imperativo, causativo, "
    "pasivo, adverbio, indeclinable, numeral, nombre_propio, titulo_de_seccion, otro"
)
DOUBT_TYPES = (
    "vacío, categoria_gramatical, sentido_doctrinal, identificacion_botanica, "
    "identificacion_zoologica, equivalente_castellano, referencia_textual, "
    "lectura_del_ejemplo, otro"
)
PROBLEM_TYPES = (
    "vacío, categoria_gramatical, sentido, numero_de_sentidos, terminologia, "
    "marcadores, referencia, literal, otro"
)

# What the auditor is allowed to see: the DPD entry and the finished Spanish.
# Never the declared category, the explanation or the confidence — that is what
# makes the second opinion independent rather than a rubber stamp.
AUDIT_FIELDS = (
    "lema",
    "pos",
    "gramatica",
    "construccion",
    "acepcion_inglesa",
    "literal_ingles",
    "ejemplos",
    "comentario",
    "glosas_externas",
)


def extract_json(answer: str) -> list[dict[str, Any]] | None:
    """The array in a model's answer, however it chose to wrap it."""
    fenced = FENCE.search(answer)
    text = fenced.group(1) if fenced else answer
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def audit_view(source: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """One case as the auditor sees it: the entry, plus the Spanish to judge."""
    case = {field: source[field] for field in AUDIT_FIELDS if field in source}
    case["traduccion_es"] = record.get("meaning_1_es", "")
    case["literal_es"] = record.get("meaning_lit_es", "")
    return case


def ask(
    manager: AIManager,
    system: str,
    prompt: str,
    attempts: int,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict] | None:
    """Ask until the answer is a JSON array, or give up and say so."""
    for attempt in range(1, attempts + 1):
        response = manager.request(
            prompt=prompt, prompt_sys=system, provider_preference=provider, model=model
        )
        if response.content:
            parsed = extract_json(response.content)
            if parsed is not None:
                return parsed
        pr.amber(f"  intento {attempt}/{attempts}: {response.status_message[:80]}")
    return None


def faults_in(
    sources: list[dict], records: list[dict] | None, auditing: bool
) -> list[str]:
    """Why a set of records cannot be accepted, empty when it can."""
    if records is None:
        return ["sin respuesta utilizable"]
    if len(records) != len(sources):
        return [f"{len(records)} registros para {len(sources)} entradas"]
    faults: list[str] = []
    for source, record in zip(sources, records):
        found = check_audit(record) if auditing else check_translation(source, record)
        if found:
            faults.append(f"{source['lema']}: {'; '.join(found)}")
    return faults


def run_pass(
    manager: AIManager,
    sources: list[dict],
    payload: list[dict],
    template: str,
    system: str,
    auditing: bool,
    attempts: int,
    provider: str | None,
    model: str | None,
) -> list[dict] | None:
    """One pass over one batch, refused unless every record keeps the contract."""
    prompt = template.format(
        categorias=CATEGORIES,
        tipos_duda=DOUBT_TYPES,
        tipos_problema=PROBLEM_TYPES,
        entradas=json.dumps(payload, ensure_ascii=False),
        casos=json.dumps(payload, ensure_ascii=False),
    )
    for attempt in range(1, attempts + 1):
        records = ask(manager, system, prompt, 1, provider, model)
        faults = faults_in(sources, records, auditing)
        if not faults:
            return records
        pr.amber(f"  pasada {attempt}/{attempts} incumple: {faults[0][:90]}")
    return None


def process(
    path: Path, attempts: int, lock: Lock, provider: str | None, model: str | None
) -> str:
    """Translate and audit one batch, skipping whatever is already done."""
    manager = AIManager()
    sources = json.loads(path.read_text(encoding="utf-8"))
    translated_path = path.with_suffix(".out.json")
    audited_path = path.with_suffix(".audit.json")

    if translated_path.exists():
        records = json.loads(translated_path.read_text(encoding="utf-8"))
    else:
        records = run_pass(
            manager,
            sources,
            sources,
            TRANSLATOR_PROMPT,
            TRANSLATOR_SYSTEM,
            False,
            attempts,
            provider,
            model,
        )
        if records is None:
            return f"{path.name}: sin traducción válida"
        translated_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    if audited_path.exists():
        return f"{path.name}: ya estaba"

    cases = [audit_view(source, record) for source, record in zip(sources, records)]
    verdicts = run_pass(
        manager,
        sources,
        cases,
        AUDITOR_PROMPT,
        AUDITOR_SYSTEM,
        True,
        attempts,
        provider,
        model,
    )
    if verdicts is None:
        return f"{path.name}: traducida, SIN auditar"
    audited_path.write_text(
        json.dumps(verdicts, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    with lock:
        pr.summary(
            path.name, sum(1 for v in verdicts if v.get("veredicto") == "correcta")
        )
    return f"{path.name}: hecha"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batches", type=Path, required=True, help="directory of batches"
    )
    parser.add_argument(
        "--workers", type=int, default=1, help="batches at once; see the docstring"
    )
    parser.add_argument(
        "--attempts", type=int, default=3, help="tries before giving a batch up"
    )
    parser.add_argument("--limit", type=int, help="stop after this many batches")
    parser.add_argument(
        "--provider", help="pin the requests to one provider, e.g. deepseek"
    )
    parser.add_argument(
        "--model", help="pin the requests to one model of that provider"
    )
    args = parser.parse_args()

    pr.tic()
    pr.yellow_title("traducción y auditoría de las tandas")
    batches = sorted(
        path
        for path in args.batches.glob("pendientes_*.json")
        if not path.name.endswith((".out.json", ".audit.json"))
    )
    pending = [path for path in batches if not path.with_suffix(".audit.json").exists()]
    pr.summary("tandas", len(batches))
    pr.summary("por hacer", len(pending))
    if args.limit:
        pending = pending[: args.limit]

    lock = Lock()
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(
                pool.map(
                    lambda path: process(
                        path, args.attempts, lock, args.provider, args.model
                    ),
                    pending,
                )
            )
    else:
        results = [
            process(path, args.attempts, lock, args.provider, args.model)
            for path in pending
        ]

    for line in results:
        if line.endswith("hecha") or line.endswith("ya estaba"):
            pr.green(line)
        else:
            pr.red(line)
    pr.toc()


if __name__ == "__main__":
    main()
