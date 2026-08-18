#!/usr/bin/env python3

"""Spanish overlay for the meaning fields of `DpdHeadword` and `DpdRoot`.

The translations live in a sidecar database (`dpd_es.db`) rather than in
`dpd.db`, because `dpd.db` is replaced wholesale by every release update and
would lose any added column.

The overlay is read-only and off by default. Switch it on in `config.ini`:

    [spanish]
    enabled = yes

With it on, reading `headword.meaning_1`, `.meaning_lit`, `.meaning_2` or
`root.root_meaning` returns the Spanish text when a translation exists, and the
English original when it does not. Writes and SQL-level queries are untouched:
they always go to the English columns.
"""

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from tools.configger import config_read

if TYPE_CHECKING:
    from db.models import DpdHeadword

SIDECAR_PATH = Path("dpd_es.db")
TABLE = "meanings_es"
FIELDS = ("meaning_1", "meaning_lit", "meaning_2")

# `lemma_1` is a stable key but not a stable meaning: a numbered sense can be
# reassigned to a different word between releases, which would silently attach
# an old translation to a new sense. `pos` and the construction summary as they
# stood when the translation was made are stored alongside it, and a lemma whose
# current values disagree is left untranslated.
FINGERPRINT = ("pos", "construction")
COLUMNS = FIELDS + FINGERPRINT

# Where an entry came from. "material" is the published Spanish translation;
# "generado" is this project's own. It is stated in the `Fuente` row of the
# grammar table, and it keeps generated wording out of the glossary that
# produces the next batch.
ORIGIN = "origin"
GENERATED = "generado"
STORED = COLUMNS + (ORIGIN,)

# Roots are a separate table with a separate source (`dpd_roots.js`), which does
# carry the English it was translated from — so their fingerprint is that English
# text itself: an exact comparison, no proxy needed.
ROOT_TABLE = "roots_es"
ROOT_COLUMNS = ("root_meaning", "root_meaning_en")

# Sanskrit root meanings are keyed by the English phrase itself, not by root:
# they are short glosses that repeat across roots, and the exact English text is
# both the key and the fingerprint.
SANSKRIT_TABLE = "sanskrit_es"
SANSKRIT_COLUMN = "meaning_es"

# Webapp interface strings, keyed by the exact English as it appears in the
# templates. Substitution happens when Jinja loads a template, so no template
# is edited and upstream can keep churning them freely; a string that changes
# upstream simply stops matching and stays English.
UI_TABLE = "ui_es"
UI_COLUMN = "text_es"

_enabled: bool | None = None
_translations: dict[str, dict[str, str]] | None = None
_roots: dict[str, dict[str, str]] | None = None
_sanskrit: dict[str, dict[str, str]] | None = None
_ui: dict[str, dict[str, str]] | None = None
# lemma_1 -> its overlaid meaning_combo, memoised across family tables and
# requests: the same lemmas recur in family after family, and re-querying
# them tripled page latency on words with many compound families
_combos: dict[str, str] = {}
_local = threading.local()


def is_enabled() -> bool:
    """Read the `[spanish] enabled` switch from `config.ini`, once."""
    global _enabled
    if _enabled is None:
        _enabled = config_read("spanish", "enabled", "no") == "yes"
    return _enabled


def is_active() -> bool:
    """True when the overlay should apply to this read."""
    return is_enabled() and not getattr(_local, "suppressed", False)


@contextmanager
def suppressed() -> Iterator[None]:
    """Return English inside this block, whatever the config says.

    Wrap anything that reads meanings in order to write them back out —
    the TSV backup above all, which must never receive translated text.
    """
    previous = getattr(_local, "suppressed", False)
    _local.suppressed = True
    try:
        yield
    finally:
        _local.suppressed = previous


def _read_table(
    table: str, key: str, columns: tuple[str, ...]
) -> dict[str, dict[str, str]]:
    """Read one sidecar table keyed by `key`. A missing sidecar reads as empty."""
    rows: dict[str, dict[str, str]] = {}
    if not SIDECAR_PATH.exists():
        return rows
    connection = sqlite3.connect(f"file:{SIDECAR_PATH}?mode=ro", uri=True)
    try:
        selected = ", ".join(columns)
        for key_value, *values in connection.execute(
            f"select {key}, {selected} from {table}"
        ):
            rows[key_value] = dict(zip(columns, values))
    except sqlite3.OperationalError:
        # an older sidecar built before this table existed
        return {}
    finally:
        connection.close()
    return rows


def load() -> dict[str, dict[str, str]]:
    """Load the headword translations into memory, once."""
    global _translations
    if _translations is None:
        _translations = _read_table(TABLE, "lemma_1", STORED)
    return _translations


def load_roots() -> dict[str, dict[str, str]]:
    """Load the root translations into memory, once."""
    global _roots
    if _roots is None:
        _roots = _read_table(ROOT_TABLE, "root", ROOT_COLUMNS)
    return _roots


def load_sanskrit() -> dict[str, dict[str, str]]:
    """Load the Sanskrit gloss translations into memory, once."""
    global _sanskrit
    if _sanskrit is None:
        _sanskrit = _read_table(SANSKRIT_TABLE, "meaning_en", (SANSKRIT_COLUMN,))
    return _sanskrit


def load_ui() -> dict[str, dict[str, str]]:
    """Load the interface strings into memory, once."""
    global _ui
    if _ui is None:
        _ui = _read_table(UI_TABLE, "text_en", (UI_COLUMN,))
    return _ui


def reload() -> None:
    """Drop the cached config switch and translations."""
    global _enabled, _translations, _roots, _sanskrit, _ui
    _enabled = None
    _translations = None
    _roots = None
    _sanskrit = None
    _ui = None
    _combos.clear()


def fingerprint_matches(headword: "DpdHeadword", stored: dict[str, str]) -> bool:
    """True when the lemma still means what it meant when it was translated.

    The part of speech must always agree. The construction is only compared when
    both sides have one: an empty construction in the sidecar is a gap in the
    dictionary as it stood in 2025, not a statement about the word, and reading
    it as disagreement discards translations that are perfectly good. Measured on
    `family_set`, 2,499 of 2,908 discarded lemmas were of exactly this kind —
    names of suttas and vaggas whose construction the DPD has since filled in.

    What this gives up: if a construction was filled in *because* the entry was
    reinterpreted, that signal is now lost. How often that happens is unmeasured,
    and cannot be measured from here — the Spanish material does not keep the
    English it was translated from.

    Evaluated with the overlay suppressed, both to compare English against
    English and to keep `construction_summary` — which reads `meaning_1` — from
    re-entering this function.
    """
    with suppressed():
        if stored["pos"] != headword.pos:
            return False
        construction = headword.construction_summary
        if not stored["construction"] or not construction:
            return True
        return stored["construction"] == construction


def origin_of(headword: "DpdHeadword") -> str:
    """Where the Spanish shown for this headword comes from.

    Empty when nothing is shown in Spanish, whether because the overlay is off,
    the lemma is untranslated, or its fingerprint no longer matches.
    """
    if not is_active():
        return ""
    stored = load().get(headword.lemma_1)
    if stored is None or not fingerprint_matches(headword, stored):
        return ""
    if not any(stored.get(field) for field in FIELDS):
        return ""
    return stored.get(ORIGIN, "material")


def overlay(headword: "DpdHeadword", field: str, english: str) -> str:
    """Spanish text for this headword and field, falling back to English."""
    if not is_active():
        return english
    # never invent a meaning where the dictionary itself has none: an empty
    # field is data, and filling it would move every completeness count
    if not english:
        return english
    stored = load().get(headword.lemma_1)
    if stored is None or not fingerprint_matches(headword, stored):
        return english
    return stored.get(field, "") or english


def overlay_root(root: str, english: str) -> str:
    """Spanish meaning for this root, falling back to English.

    The stored English is compared against the current one, so a root whose
    meaning has been reworded or renumbered upstream keeps its English.
    """
    if not is_active() or not english:
        return english
    stored = load_roots().get(root)
    if stored is None or stored["root_meaning_en"] != english:
        return english
    return stored["root_meaning"] or english


def overlay_sanskrit(english: str) -> str:
    """Spanish for a Sanskrit root gloss, falling back to English.

    Keyed by the English phrase, so a reworded gloss simply misses and keeps
    its English — the same guarantee the other overlays get from a fingerprint.
    """
    if not is_active() or not english:
        return english
    stored = load_sanskrit().get(english)
    if stored is None:
        return english
    return stored[SANSKRIT_COLUMN] or english


# Family tables are stored in `dpd.db` as HTML rendered at build time, so their
# meanings never pass through the ORM. Every family type shares one row shape:
#     <tr><th>LEMMA</th><td><b>POS</b></td><td>MEANING</td>...</tr>
FAMILY_ROW = re.compile(
    r"(<tr><th>(?P<lemma>[^<]+)</th><td><b>[^<]*</b></td><td>)(?P<meaning>[^<]*)(</td>)"
)


# `superscripter_uni()` renders "dukkha 1" as "dukkha\u200a¹" in the stored HTML,
# so the lemma has to be turned back into a key before it can be looked up.
SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹·\u200a", "0123456789. ")


def unsuperscript(lemma: str) -> str:
    """Undo `superscripter_uni()` so a rendered lemma is a `lemma_1` again."""
    return lemma.translate(SUPERSCRIPTS)


def translate_family_html(html: str, db_session) -> str:
    """Replace the meaning cell of each family row with its Spanish.

    The replacement comes from the live headword, so it carries the same
    fingerprint check as any other read; a lemma that is missing, ambiguous or
    fails the check keeps the English that was baked into the HTML.
    """
    if not is_active() or not html:
        return html

    from db.models import DpdHeadword

    lemmas = {unsuperscript(m.group("lemma")) for m in FAMILY_ROW.finditer(html)}
    if not lemmas or db_session is None:
        return html

    missing = lemmas - _combos.keys()
    if missing:
        rows = (
            db_session.query(DpdHeadword).filter(DpdHeadword.lemma_1.in_(missing)).all()
        )
        _combos.update({row.lemma_1: row.meaning_combo for row in rows})
        # remember the misses too, so they are not queried again
        _combos.update({lemma: "" for lemma in missing - {row.lemma_1 for row in rows}})

    def replace(match: "re.Match[str]") -> str:
        combo = _combos.get(unsuperscript(match.group("lemma")))
        if not combo:
            return match.group(0)
        return f"{match.group(1)}{combo}{match.group(4)}"

    return FAMILY_ROW.sub(replace, html)


# El DPD marca con «(comm)» lo que viene del comentario, y la traducción
# publicada lo dejó de cuatro formas distintas —(com.), (comm), (com) y
# (comentario)—, mezcladas dentro de una misma ficha. Se unifican al construir
# el sidecar, que es donde se escribe el texto una sola vez.
MARCADORES = {
    "(comm)": "(comentario)",
    "(com.)": "(comentario)",
    "(com)": "(comentario)",
    "(gram)": "(gramática)",
    "(gram.)": "(gramática)",
}
MARCADOR = re.compile("|".join(re.escape(m) for m in MARCADORES), re.IGNORECASE)


def normalise_markers(text: str) -> str:
    """Unify the DPD markers that the translation left in several shapes."""
    if not text:
        return text
    return MARCADOR.sub(lambda m: MARCADORES[m.group(0).lower()], text)


def preload_combos(db_session) -> int:
    """Fill the meaning cache for every headword in one pass.

    The family translators query the db for the lemmas they have not seen yet,
    which is right for the webapp — a page touches a handful of families — and
    wrong for a full export, where the same work arrives split into thousands of
    small queries. Measured on the whole db: 89.050 lemmas in 8 seconds this
    way, against minutes family by family. Threads would not have helped; the
    cost was the shape of the querying, not the lack of parallelism.
    """
    if not is_active():
        return 0

    from db.models import DpdHeadword

    for headword in db_session.query(DpdHeadword).yield_per(2000):
        _combos[headword.lemma_1] = headword.meaning_combo
    return len(_combos)


def translate_family_rows(rows: list, db_session) -> list:
    """Same as `translate_family_html`, for the JSON payload of a family table.

    The webapp renders a family as HTML; GoldenDict ships the rows as JSON and
    paints them in the browser, so the English travels in `data` instead. Each
    row is `[lemma, pos, meaning, mark]`, and only the meaning changes.
    """
    if not is_active() or not rows:
        return rows

    from db.models import DpdHeadword

    lemmas = {row[0] for row in rows if len(row) > 2}
    missing = lemmas - _combos.keys()
    if missing and db_session is not None:
        found = (
            db_session.query(DpdHeadword).filter(DpdHeadword.lemma_1.in_(missing)).all()
        )
        _combos.update({row.lemma_1: row.meaning_combo for row in found})
        _combos.update({lemma: "" for lemma in missing - {r.lemma_1 for r in found}})

    return [
        [row[0], row[1], _combos.get(row[0]) or row[2], *row[3:]]
        if len(row) > 2
        else row
        for row in rows
    ]


# Everything inside these blocks is code, not prose: the status page ships a
# stylesheet and a theme script whose contents would otherwise look like text.
CODE_BLOCK = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
TEXT_NODE = re.compile(r">(?P<text>[^<>]*)<")
# a text node can interleave prose with Jinja tags; the prose between them is
# still translatable, so the node is split rather than skipped
JINJA_TAG = re.compile(r"(\{\{.*?\}\}|\{%.*?%\})", re.DOTALL)
# single quotes too: the inflection tables generated into the db write their
# tooltips as title='in comps'. Only an exact phrase is replaced, so the
# grammatical abbreviations that fill most of those tooltips —title='masc nom
# sg'— are left alone, which is what they should be: they read the same in
# Spanish.
TEXT_ATTRIBUTE = re.compile(
    r"(?P<head>(?:placeholder|title|alt|data-title)=(?P<quote>[\"']))"
    r"(?P<text>[^\"'{}]+)(?P=quote)"
)


def translate_template_source(source: str, phrases: dict[str, dict[str, str]]) -> str:
    """Replace known English strings in one Jinja template with their Spanish.

    Only text nodes and a few visible attributes are touched, never markup,
    Jinja expressions or the contents of `<script>`/`<style>`.
    """

    def translate(text: str) -> str | None:
        stored = phrases.get(" ".join(text.split()))
        return stored[UI_COLUMN] if stored else None

    def translate_segment(segment: str) -> str:
        spanish_text = translate(segment)
        if spanish_text is None:
            return segment
        # keep the original indentation and line breaks around the phrase
        leading = segment[: len(segment) - len(segment.lstrip())]
        trailing = segment[len(segment.rstrip()) :]
        # unless the Spanish opens with punctuation: word order changes between
        # the two languages, so a fragment that was «... characters.» in English
        # can end up as just «.», and it must not float away from the word
        # before it — «Velthuis .»
        if spanish_text[:1] in ".,;:!?)":
            leading = ""
        return f"{leading}{spanish_text}{trailing}"

    def replace_node(match: "re.Match[str]") -> str:
        pieces = JINJA_TAG.split(match.group("text"))
        # odd indices are the Jinja tags themselves, left untouched
        translated = [
            piece if index % 2 else translate_segment(piece)
            for index, piece in enumerate(pieces)
        ]
        return f">{''.join(translated)}<"

    def replace_attribute(match: "re.Match[str]") -> str:
        spanish_text = translate(match.group("text"))
        if spanish_text is None:
            return match.group(0)
        return f"{match.group('head')}{spanish_text}{match.group('quote')}"

    pieces: list[str] = []
    position = 0
    for block in CODE_BLOCK.finditer(source):
        prose = source[position : block.start()]
        pieces.append(
            TEXT_ATTRIBUTE.sub(replace_attribute, TEXT_NODE.sub(replace_node, prose))
        )
        pieces.append(block.group(0))
        position = block.end()
    tail = source[position:]
    pieces.append(
        TEXT_ATTRIBUTE.sub(replace_attribute, TEXT_NODE.sub(replace_node, tail))
    )
    return "".join(pieces)


def translate_environment(env) -> None:
    """Serve a Jinja environment in Spanish, without editing any template.

    Used by the webapp and by the exporters, which build their environments
    through `exporter/jinja2_env.py`. The inflection filter is registered here
    too: those tables carry their English inside `dpd.db`, so wherever a
    template renders one it needs the same treatment.
    """
    if not is_active():
        return

    phrases = load_ui()
    if not phrases:
        return

    from jinja2 import BaseLoader

    class SpanishLoader(BaseLoader):
        """Delegates to the real loader and translates what it returns."""

        def __init__(self, inner) -> None:
            self.inner = inner

        def get_source(self, environment, template):
            source, filename, uptodate = self.inner.get_source(environment, template)
            return translate_template_source(source, phrases), filename, uptodate

        def list_templates(self):
            return self.inner.list_templates()

    env.loader = SpanishLoader(env.loader)
    env.filters["inflexion_es"] = translate_inflection_html


def translate_templates(templates) -> None:
    """Serve a `Jinja2Templates` set in Spanish; see `translate_environment`."""
    translate_environment(templates.env)


INFLECTION_HEADING = re.compile(
    r"(?P<word><b>[^<]+</b>) is (?P<pattern><b>[^<]+</b>) "
    r"(?P<kind>declension|conjugation) \(like (?P<like><b>[^<]+</b>)\)"
)
ORDINAL = re.compile(r"\b(?P<number>[123])(?:st|nd|rd)\b")
KIND_ES = {"declension": "declinación", "conjugation": "conjugación"}


def translate_inflection_html(html: str) -> str:
    """Public entry: a no-op unless the overlay is on."""
    return _translate_inflection_html(html) if html and is_active() else html


@lru_cache(maxsize=512)
def _translate_inflection_html(html: str) -> str:
    """Put an inflection table into Spanish: heading, labels and persons.

    This one is neither template nor sidecar: the table is built when `dpd.db`
    is generated and stored in `inflections_html`, so the English is inside the
    data. The heading needs a rule of its own because Spanish moves the noun
    before its label — «es una declinación a adj», not «es a adj declinación» —
    which no phrase-by-phrase replacement can do. The rest goes through the
    ordinary interface table.
    """
    if not html:
        return html

    def heading(match: "re.Match[str]") -> str:
        kind = KIND_ES[match.group("kind")]
        return (
            f"{match.group('word')} es una {kind} {match.group('pattern')} "
            f"(como {match.group('like')})"
        )

    translated = INFLECTION_HEADING.sub(heading, html)
    translated = ORDINAL.sub(lambda m: f"{m.group('number')}ª", translated)
    phrases = load_ui()
    return translate_template_source(translated, phrases) if phrases else translated


def translate_static(static_dir: Path) -> Path:
    """Return a directory whose javascript has been translated, or the original.

    Part of the interface does not live in a template: the webapp's opening
    message is built inside `static/home.js` as a chunk of HTML. Rather than
    writing Spanish into that file — a patch that upstream would break on its
    next edit — the layer serves a translated copy from a scratch directory and
    leaves the original alone. A phrase that is not in the table stays English,
    so a reworded sentence degrades instead of failing.
    """
    if not is_active():
        return static_dir

    phrases = load_ui()
    if not phrases:
        return static_dir

    import shutil
    import tempfile

    destination = Path(tempfile.gettempdir()) / "dpd_es_static"
    shutil.copytree(static_dir, destination, dirs_exist_ok=True)
    for script in destination.glob("*.js"):
        source = script.read_text(encoding="utf-8")
        translated = translate_template_source(source, phrases)
        if translated != source:
            script.write_text(translated, encoding="utf-8")
    return destination
