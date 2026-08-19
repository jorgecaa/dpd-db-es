#!/usr/bin/env python3

"""Check translated batches against the contract and the independent audit.

Two things arrive here for every headword: a translation, and a verdict on that
translation from an auditor who analysed the word from scratch without seeing
how the translator had analysed it. Both are structured records with a closed
vocabulary, which is what makes them countable rather than readable.

The audit is required, not optional, and the reason is measured. Over 120
translations checked blind, the translator's own confidence did not predict
error: three of the four wrong translations were declared `alta`, and eight of
the twelve declared `media` were fine. Filtering on self-assessment would have
passed the wrong ones through and wasted review on the right ones. So a batch
with no audit is refused, and the queue is built from the auditor's verdict.

Agreement on the morphology was near total — 118 of 120 identical, 2 compatible,
no real disagreement — so a disagreement here is worth a human's attention on
its own, whatever the verdict says.

Output:
    <out>/aceptadas.tsv    lemma + fields, ready for the sidecar as `generado`
    <out>/revisar.tsv      what a person has to decide, with the reason

Usage:
    uv run python scripts/build/spanish_review.py --batches <dir> --out <dir>
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from tools.printer import printer as pr
from tools.spanish import normalise_markers

CATEGORIES = frozenset(
    """sustantivo sustantivo_accion adjetivo adjetivo_posesivo participio_pasado
    participio_presente_activo participio_presente_pasivo participio_futuro_pasivo
    absolutivo infinitivo presente aoristo futuro optativo imperativo causativo
    pasivo adverbio indeclinable numeral nombre_propio titulo_de_seccion otro""".split()
)
CONFIDENCE = frozenset({"alta", "media", "baja"})
VERDICTS = frozenset({"correcta", "dudosa", "incorrecta"})

# Categories that describe the same form from a different angle. Counting these
# as disagreement would bury the real ones.
COMPATIBLE = (
    {"causativo", "presente"},
    {"adjetivo", "adjetivo_posesivo"},
    {"sustantivo", "sustantivo_accion"},
    {"sustantivo", "nombre_propio"},
    {"sustantivo", "titulo_de_seccion"},
    {"participio_pasado", "adjetivo"},
    {"participio_presente_activo", "adjetivo"},
    {"participio_futuro_pasivo", "adjetivo"},
    {"pasivo", "presente"},
    {"indeclinable", "adverbio"},
)

# English function words surviving into the Spanish mean it was never translated.
ENGLISH = frozenset(
    """the of and from with which who that not was were been having being made
    belonging associated connected without this these those into""".split()
)


def canonical(value: object) -> str:
    """A closed-vocabulary value with the model's stray whitespace removed.

    Measured over 2,085 translations, fifteen were refused for writing
    `indec linable` — the right word with a space inside it. That is a slip in
    transcription, not a category the vocabulary lacks, and rejecting it wastes a
    retry on something no retry fixes. Anything that is still not in the
    vocabulary after this is a genuine invention and stays refused.
    """
    return "".join(str(value).split()) if value is not None else ""


def senses(text: str) -> int:
    return len([part for part in text.split(";") if part.strip()])


def compatible(one: str, other: str) -> bool:
    return one == other or any({one, other} <= group for group in COMPATIBLE)


def check_translation(source: dict[str, Any], record: dict[str, Any]) -> list[str]:
    """Everything wrong with one translation that can be found without judgement."""
    faults: list[str] = []
    if record.get("lema") != source["lema"]:
        faults.append(f"lema descuadrado: {record.get('lema')!r}")
    spanish_text = (record.get("meaning_1_es") or "").strip()
    english = source["acepcion_inglesa"]
    if not spanish_text:
        return [*faults, "traducción vacía"]

    surviving = {
        word.strip(".,;:()").lower() for word in spanish_text.split()
    } & ENGLISH
    if surviving:
        faults.append(f"palabras inglesas: {', '.join(sorted(surviving))}")
    if senses(spanish_text) != senses(english):
        faults.append(f"sentidos {senses(english)} → {senses(spanish_text)}")
    for marker, expected in (("(comm)", "(comentario)"), ("(gram)", "(gramática)")):
        if marker in english.lower() and expected not in spanish_text.lower():
            faults.append(f"pierde el marcador {marker}")
    if source["literal_ingles"] and not (record.get("meaning_lit_es") or "").strip():
        faults.append("falta el literal")

    if record.get("confianza") not in CONFIDENCE:
        faults.append(f"confianza inválida: {record.get('confianza')!r}")
    form = record.get("forma") or {}
    if canonical(form.get("categoria")) not in CATEGORIES:
        faults.append(f"categoría fuera del vocabulario: {form.get('categoria')!r}")
    if form.get("decidio_traduccion") and not (form.get("explicacion") or "").strip():
        faults.append("decidió la traducción sin explicarlo")
    terminology = record.get("terminologia") or {}
    if terminology.get("seguida") is False and not terminology.get("desviaciones"):
        faults.append("se aparta del material sin declarar la desviación")
    return faults


def check_audit(record: dict[str, Any]) -> list[str]:
    """Everything wrong with one audit record."""
    faults: list[str] = []
    verdict = record.get("veredicto")
    if verdict not in VERDICTS:
        faults.append(f"veredicto inválido: {verdict!r}")
    if canonical(record.get("categoria_segun_auditor")) not in CATEGORIES:
        faults.append(
            f"categoría fuera del vocabulario: {record.get('categoria_segun_auditor')!r}"
        )
    problem = record.get("problema") or {}
    if verdict != "correcta" and not problem.get("hay"):
        faults.append("no la da por correcta y no declara problema")
    if problem.get("hay") and not (problem.get("detalle") or "").strip():
        faults.append("problema sin detalle")
    if (
        verdict == "incorrecta"
        and not (record.get("correccion_propuesta") or "").strip()
    ):
        faults.append("la da por incorrecta sin proponer corrección")
    return faults


def load_batch(path: Path) -> tuple[list[dict], list[dict], list[dict]] | None:
    """A batch is its source, its translation and its audit, or it is nothing.

    The `.out` and `.audit` files sit beside the source under the same stem, so a
    batch that was translated but never audited is visibly incomplete rather than
    silently half-processed.
    """
    translated, audited = path.with_suffix(".out.json"), path.with_suffix(".audit.json")
    if not translated.exists():
        pr.amber(f"{path.name}: sin traducir")
        return None
    if not audited.exists():
        pr.red(
            f"{path.name}: SIN AUDITAR — la auditoría no es opcional, se descarta la tanda"
        )
        return None
    source = json.loads(path.read_text(encoding="utf-8"))
    output = json.loads(translated.read_text(encoding="utf-8"))
    audit = json.loads(audited.read_text(encoding="utf-8"))
    if not len(source) == len(output) == len(audit):
        pr.red(
            f"{path.name}: {len(source)} entradas, {len(output)} traducidas, {len(audit)} auditadas"
        )
        return None
    return source, output, audit


def review(batch_dir: Path) -> tuple[list[dict], list[dict], Counter]:
    """Sort every audited translation into accepted or needing a person."""
    accepted: list[dict] = []
    queued: list[dict] = []
    tally: Counter = Counter()
    # the glob would otherwise match the `.out.json` and `.audit.json` it writes
    sources = sorted(
        path
        for path in batch_dir.glob("pendientes_*.json")
        if not path.name.endswith((".out.json", ".audit.json"))
    )
    for path in sources:
        loaded = load_batch(path)
        if loaded is None:
            tally["tandas descartadas"] += 1
            continue
        for source, record, verdict in zip(*loaded):
            tally["revisadas"] += 1
            faults = check_translation(source, record) + check_audit(verdict)
            declared = canonical((record.get("forma") or {}).get("categoria", ""))
            audited_category = canonical(verdict.get("categoria_segun_auditor", ""))
            disagrees = not compatible(declared, audited_category)
            if disagrees:
                tally["discrepan en la categoría"] += 1

            reasons = []
            if faults:
                reasons.append("incumple el contrato: " + "; ".join(faults))
            if verdict.get("veredicto") != "correcta":
                problem = verdict.get("problema") or {}
                reasons.append(
                    f"el auditor la da por {verdict.get('veredicto')} "
                    f"({problem.get('tipo')}): {problem.get('detalle')}"
                )
            if disagrees:
                reasons.append(
                    f"morfología: traductor «{declared}», auditor «{audited_category}»"
                )

            if reasons:
                tally["a revisar"] += 1
                queued.append(
                    {
                        "lema": source["lema"],
                        "ingles": source["acepcion_inglesa"],
                        "propuesta": record.get("meaning_1_es", ""),
                        "correccion_del_auditor": verdict.get(
                            "correccion_propuesta", ""
                        ),
                        "motivo": " | ".join(reasons),
                    }
                )
                continue

            tally["aceptadas"] += 1
            accepted.append(
                {
                    "lema": source["lema"],
                    "meaning_1": normalise_markers(record["meaning_1_es"]),
                    "meaning_lit": normalise_markers(record.get("meaning_lit_es", "")),
                    "meaning_2": "",
                    "pos": source["pos"],
                    "construction": source["construccion"],
                }
            )
    return accepted, queued, tally


def flatten(value: object) -> str:
    """A field with nothing in it that could break the row it lives in.

    The contract forbids newlines and a record carrying one is refused, but a
    field can still arrive with one from a model that wrote the text before the
    rule bound it. Left alone it splits one record across two lines and the file
    parses into more rows than there are translations: measured, two of 1,988.
    """
    return " ".join(str(value).split())


def write_tsv(rows: list[dict], path: Path, columns: list[str], header: str) -> None:
    """One row per record, sorted by lemma so a regeneration diffs cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [header, "# " + "\t".join(columns)]
    for row in sorted(rows, key=lambda r: r["lema"]):
        lines.append("\t".join(flatten(row.get(column, "")) for column in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batches", type=Path, required=True, help="directory of batches"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the results"
    )
    args = parser.parse_args()

    pr.tic()
    pr.yellow_title("revisión de las traducciones generadas")
    accepted, queued, tally = review(args.batches)

    for key in (
        "revisadas",
        "aceptadas",
        "a revisar",
        "discrepan en la categoría",
        "tandas descartadas",
    ):
        if tally[key]:
            pr.summary(key, tally[key])

    write_tsv(
        accepted,
        args.out / "aceptadas.tsv",
        ["lema", "meaning_1", "meaning_lit", "meaning_2", "pos", "construction"],
        "# Traducciones generadas por este proyecto y confirmadas por auditoría independiente.",
    )
    write_tsv(
        queued,
        args.out / "revisar.tsv",
        ["lema", "ingles", "propuesta", "correccion_del_auditor", "motivo"],
        "# Cola de revisión humana. El motivo viene del auditor, no de la confianza del traductor.",
    )
    pr.toc()


if __name__ == "__main__":
    main()
