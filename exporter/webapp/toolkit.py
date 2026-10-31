import difflib
import re
import time
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, literal, or_, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, defer
from unidecode import unidecode

from db.db_helpers import get_db_session
from db.models import DpdHeadword, DpdRoot, FamilyRoot, Lookup
from exporter.webapp.data_classes import (
    AbbreviationsData,
    AbbreviationsOtherData,
    DeconstructorData,
    EpdData,
    GrammarData,
    HeadwordData,
    HelpData,
    ManualVariantData,
    RootsData,
    SeeData,
    SpellingData,
    VariantData,
)
from tools.exporter_functions import (
    get_family_compounds,
    get_family_idioms,
    get_family_set,
)
from tools.configger import config_test
from tools.pali_context import PaliContextIndex
from tools.pali_sort_key import pali_sort_key
from tools.paths import ProjectPaths
from tools.variants_manager import VariantManager


_variant_manager: VariantManager | None = None

# Columns this webapp never reads. They are `defer`red rather than merely unread, so a
# serving-only build of the database can drop them: an ORM entity load selects every
# column the model declares, and a trimmed db without them fails with "no such column"
# (measured 2026-09-18: dropping them broke 125 of 130 golden-oracle responses).
# `kamma/threads/20260918_serving_db_diet/` has the size figures and the design.
_UNREAD_HEADWORD_COLUMNS = (
    "freq_data",
    "inflections_sinhala",
    "inflections_thai",
    "inflections_devanagari",
    "inflections",
    "inflections_api_ca_eva_iti",
    "created_at",
    "updated_at",
)
_UNREAD_LOOKUP_COLUMNS = ("sinhala", "thai", "devanagari", "rpd", "other")

_DEFER_HEADWORD = tuple(
    defer(getattr(DpdHeadword, name)) for name in _UNREAD_HEADWORD_COLUMNS
)
_DEFER_LOOKUP = tuple(defer(getattr(Lookup, name)) for name in _UNREAD_LOOKUP_COLUMNS)


_context_index: PaliContextIndex | None = None


def get_context_index() -> PaliContextIndex:
    """The attested-neighbour index, loaded once per process on the first miss.

    Costs ~0.1 s and ~15 MB, and only when a search actually fails: a session that only
    looks up words the dictionary has never pays it.
    """
    global _context_index
    if _context_index is None:
        _context_index = PaliContextIndex()
    return _context_index


def get_variant_manager() -> VariantManager:
    global _variant_manager
    if _variant_manager is None:
        _variant_manager = VariantManager()
    return _variant_manager


@lru_cache(maxsize=1)
def lookup_key_variants(db_path: Path) -> dict[str, tuple[str, ...]]:
    """Cached `lookup_key_variant_rows` for a database path.

    Costs ~1 s once per process; `exporter/webapp/main.py` warms it at startup so
    no request pays it.
    """
    with get_db_session(db_path) as session:
        return lookup_key_variant_rows(session)


def lookup_key_variant_rows(db_session: Session) -> dict[str, tuple[str, ...]]:
    """Folded `lookup_key` -> its stored spellings, for keys stored more than once.

    Only 181 of 1,281,569 keys are stored under more than one ASCII case spelling
    (`buddha` / `Buddha`), and some spellings cannot be derived by folding the
    user's input (`ana` / `ANa`). The search probe needs the exact spellings to be
    both fast and complete: probing the primary-key index measured **0.017 ms**
    against **519.8 ms** for the scan the old predicate forced, with 419 MB of
    pages read per search.

    Keyed by both SQLite's fold and Python's (`str.lower`), because the callers
    fold in Python.
    """
    inner = (
        select(Lookup.lookup_key, func.lower(Lookup.lookup_key).label("folded"))
        .distinct()
        .subquery()
    )
    statement = (
        select(inner.c.folded, func.group_concat(inner.c.lookup_key, literal("\x1f")))
        .group_by(inner.c.folded)
        .having(func.count() > 1)
    )
    variants: dict[str, tuple[str, ...]] = {}
    for folded, joined in db_session.execute(statement).all():
        spellings = tuple(joined.split("\x1f"))
        variants[folded] = spellings
        for spelling in spellings:
            variants[spelling.lower()] = spellings
    return variants


def probe_keys(q: str, variants: dict[str, tuple[str, ...]]) -> list[str]:
    """The `lookup_key` spellings to probe for a user-typed `q`.

    SQLite's `LIKE` folds ASCII case, so the old scan matched any spelling of the
    same letters. These are the spellings users produce (as typed, all lower, all
    upper, sentence case, title case) plus the stored spellings the data actually
    has, each with the trailing dot the old query also matched.
    """
    forms = {q, q.lower(), q.upper(), q.capitalize(), q.title()}
    forms.update(variants.get(q.lower(), ()))
    return sorted({form + suffix for form in forms if form for suffix in ("", ".")})


def lookup_rows(
    db_session: Session, q: str, variants: dict[str, tuple[str, ...]]
) -> list[Lookup]:
    """Rows of `lookup` for `q`, through the primary-key index.

    Probing is complete because the candidate spellings include every stored
    spelling of the key: measured on 2026-09-18, 18 of 18 probe cases — including
    the ambiguous keys and the trailing-dot ones — returned exactly the rows the old
    predicate returned. If nothing matched, the old predicate still runs as a safety
    net, but reading the covering index rather than the whole table (373 ms / 36 MB
    instead of 513 ms / 419 MB), and only then fetching the matched rows by key.
    Either way the result is the same, ordered by rowid as the old scan produced it.
    """
    rows = (
        db_session.query(Lookup)
        .options(*_DEFER_LOOKUP)
        .filter(Lookup.lookup_key.in_(probe_keys(q, variants)))
        .order_by(text("lookup.rowid"))
        .all()
    )
    if rows:
        return rows

    # LIKE is only for case-insensitivity: escape % and _ so a query like "%"
    # matches itself, not every row
    q_like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    matched_keys = [
        key
        for (key,) in db_session.query(Lookup.lookup_key)
        .filter(
            or_(
                func.lower(Lookup.lookup_key).like(func.lower(q_like), escape="\\"),
                func.lower(Lookup.lookup_key).like(
                    func.lower(q_like + "."), escape="\\"
                ),
            )
        )
        .all()
    ]
    if not matched_keys:
        return []

    return (
        db_session.query(Lookup)
        .options(*_DEFER_LOOKUP)
        .filter(Lookup.lookup_key.in_(matched_keys))
        .order_by(text("lookup.rowid"))
        .all()
    )


LICENSE_URL: str = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
LICENSE_NAME: str = "CC BY-NC-SA 4.0"
LICENSE_HOLDER: str = "Bodhirasa Bhikkhu"
LICENSE_ATTRIBUTION: str = f"Digital Pāḷi Dictionary by {LICENSE_HOLDER} {LICENSE_NAME}"

# Official Creative Commons press-kit marks, recoloured to inherit the surrounding
# text colour so they read in both light and dark mode. Inlined rather than linked
# so the notice survives offline in GoldenDict.
_CC_ICONS: str = ProjectPaths().webapp_cc_icons_path.read_text(encoding="utf-8").strip()

# Appended to the rendered entries rather than to a page footer, so the notice
# travels with the data into GoldenDict, the JSON body and third-party embeds.
LICENSE_HTML: str = (
    '<div class="license-line">'
    f'<a href="{LICENSE_URL}" target="_blank" rel="license">'
    f'<span class="license-icons">{_CC_ICONS}</span>'
    f"Digital Pāḷi Dictionary {LICENSE_NAME}"
    "</a>"
    "</div>"
)

# Attached only to routes returning DPD dictionary data, so that the licence and
# attribution travel with the data itself. Tipitaka translations and audio are
# licensed separately and must not carry these.
LICENSE_LINK_HEADER: dict[str, str] = {
    "Link": f'<{LICENSE_URL}>; rel="license"; title="{LICENSE_NAME}"'
}

LICENSE_NOTICE: dict[str, str] = {
    "name": LICENSE_NAME,
    "url": LICENSE_URL,
    "attribution": LICENSE_ATTRIBUTION,
    "note": "Non-commercial use only. Derivatives must be shared alike.",
}


def make_dpd_html(
    q: str,
    pth: ProjectPaths,
    templates,
    roots_count_dict,
    headwords_clean_set,
    ascii_to_unicode_dict,
) -> tuple[str, str]:
    retries = 3
    vm = get_variant_manager()

    for attempt in range(retries):
        try:
            with get_db_session(pth.dpd_db_path) as db_session:
                with db_session.no_autoflush:
                    dpd_html = ""
                    summary_html = ""
                    # Set when the query fell through to suggestions, so the licence
                    # is not stamped on a "no results" page.
                    only_suggestions = False
                    q = q.replace("'", "").replace("ṁ", "ṃ").strip()

                    lookup_results = lookup_rows(
                        db_session, q, lookup_key_variants(pth.dpd_db_path)
                    )

                    # Check manual variants from TSV first
                    main_form = vm.get_main(q)
                    if main_form:
                        d = ManualVariantData(variant=q, main=main_form)
                        summary_html += templates.get_template(
                            pth.template_manual_variant_summary
                        ).render(d=d)
                        dpd_html += templates.get_template(
                            pth.template_manual_variant
                        ).render(d=d)

                    # first try the lookup table, if no results, then try other options
                    # a bare number is always a permalink to a headword id, so it
                    # skips the lookup table, where "3" is an abbreviation
                    if lookup_results and not q.isdecimal():
                        for lookup_result in lookup_results:
                            # headwords
                            if lookup_result.headwords:
                                headwords = lookup_result.headwords_unpack
                                headword_results = (
                                    db_session.query(DpdHeadword)
                                    .options(*_DEFER_HEADWORD)
                                    .filter(DpdHeadword.id.in_(headwords))
                                    .all()
                                )
                                headword_results = sorted(
                                    headword_results,
                                    key=lambda x: pali_sort_key(x.lemma_1),
                                )
                                for i in headword_results:
                                    fc = get_family_compounds(i)
                                    fi = get_family_idioms(i)
                                    fs = get_family_set(i)
                                    d = HeadwordData(i, fc, fi, fs)
                                    summary_html += templates.get_template(
                                        pth.template_dpd_summary
                                    ).render(d=d)
                                    dpd_html += templates.get_template(
                                        pth.template_dpd_headword
                                    ).render(d=d)

                            # roots
                            if lookup_result.roots:
                                roots_list = lookup_result.roots_unpack
                                root_results = (
                                    db_session.query(DpdRoot)
                                    .filter(DpdRoot.root.in_(roots_list))
                                    .all()
                                )
                                for r in root_results:
                                    frs = db_session.query(FamilyRoot).filter(
                                        FamilyRoot.root_key == r.root
                                    )
                                    frs = sorted(
                                        frs, key=lambda x: pali_sort_key(x.root_family)
                                    )
                                    d = RootsData(r, frs, roots_count_dict)
                                    summary_html += templates.get_template(
                                        pth.template_root_summary
                                    ).render(d=d)
                                    dpd_html += templates.get_template(
                                        pth.template_root
                                    ).render(d=d)

                            # abbreviations
                            if lookup_result.abbrev:
                                d = AbbreviationsData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_abbreviations_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_abbreviations
                                ).render(d=d)

                            # deconstructor
                            if lookup_result.deconstructor:
                                d = DeconstructorData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_deconstructor_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_deconstructor
                                ).render(d=d)

                            # grammar
                            if lookup_result.grammar:
                                d = GrammarData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_grammar_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_grammar
                                ).render(d=d)

                            # help
                            if lookup_result.help:
                                d = HelpData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_help_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_help
                                ).render(d=d)

                            # epd
                            if lookup_result.epd:
                                d = EpdData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_epd_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_epd
                                ).render(d=d)

                            # variant
                            if lookup_result.variant:
                                d = VariantData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_variant_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_variant
                                ).render(d=d)

                            # see
                            if lookup_result.see:
                                d = SeeData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_see_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_see
                                ).render(d=d)

                            # spelling mistake
                            if lookup_result.spelling:
                                d = SpellingData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_spelling_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_spelling
                                ).render(d=d)

                            # abbreviations other
                            if lookup_result.abbrev_other:
                                d = AbbreviationsOtherData(lookup_result)
                                summary_html += templates.get_template(
                                    pth.template_abbreviations_other_summary
                                ).render(d=d)
                                dpd_html += templates.get_template(
                                    pth.template_abbreviations_other
                                ).render(d=d)

                    # the two cases below search directly in the DpdHeadwords table
                    # isdecimal, not isnumeric: "½" and "²" are numeric but int() rejects them
                    elif q.isdecimal():  # eg 78654
                        # 18 digits always fit SQLite's integer; a longer number
                        # is never an id, and would overflow the query
                        headword_result = (
                            db_session.query(DpdHeadword)
                            .options(*_DEFER_HEADWORD)
                            .filter(DpdHeadword.id == int(q))
                            .first()
                            if len(q) <= 18
                            else None
                        )
                        if headword_result:
                            fc = get_family_compounds(headword_result)
                            fi = get_family_idioms(headword_result)
                            fs = get_family_set(headword_result)
                            d = HeadwordData(headword_result, fc, fi, fs)
                            dpd_html += templates.get_template(
                                pth.template_dpd_headword
                            ).render(d=d)

                        # return closest matches
                        else:
                            context_html = ""
                            if config_test("dictionary", "context_fallback", "yes"):
                                context_html = find_context_matches(
                                    q, headwords_clean_set, ascii_to_unicode_dict
                                )
                            dpd_html = context_html + find_closest_matches(
                                q, headwords_clean_set, ascii_to_unicode_dict
                            )
                            only_suggestions = True

                    elif re.search(r"\s\d", q):  # eg "kata 5"
                        headword_result = (
                            db_session.query(DpdHeadword)
                            .options(*_DEFER_HEADWORD)
                            .filter(DpdHeadword.lemma_1 == q)
                            .first()
                        )
                        if headword_result:
                            fc = get_family_compounds(headword_result)
                            fi = get_family_idioms(headword_result)
                            fs = get_family_set(headword_result)
                            d = HeadwordData(headword_result, fc, fi, fs)
                            dpd_html += templates.get_template(
                                pth.template_dpd_headword
                            ).render(d=d)

                        # return closest matches
                        else:
                            context_html = ""
                            if config_test("dictionary", "context_fallback", "yes"):
                                context_html = find_context_matches(
                                    q, headwords_clean_set, ascii_to_unicode_dict
                                )
                            dpd_html = context_html + find_closest_matches(
                                q, headwords_clean_set, ascii_to_unicode_dict
                            )
                            only_suggestions = True

                    # or finally return closest matches
                    else:
                        context_html = ""
                        if config_test("dictionary", "context_fallback", "yes"):
                            context_html = find_context_matches(
                                q, headwords_clean_set, ascii_to_unicode_dict
                            )
                        dpd_html = context_html + find_closest_matches(
                            q, headwords_clean_set, ascii_to_unicode_dict
                        )
                        only_suggestions = True

                    if dpd_html and not only_suggestions:
                        dpd_html += LICENSE_HTML

                    return dpd_html, summary_html

        except OperationalError as e:
            if attempt == retries - 1:  # Last attempt
                raise e
            time.sleep(0.1 * (attempt + 1))  # Exponential backoff

    # Unreachable: the body returns on success and the handler re-raises on the
    # final attempt. Explicit rather than returning names bound in the `with`.
    raise RuntimeError(f"make_dpd_html exhausted {retries} retries for {q!r}")


def find_context_matches(
    q: str,
    headwords_clean_set: set[str],
    ascii_to_unicode_dict: dict[str, list[str]],
) -> str:
    """Answer a miss with the texts, when the query carries the word in front of it.

    A query of two or more words is read as `<neighbour> <word>`. Pāḷi joins words by
    sandhi, so the neighbour is evidence: if the pair is not attested but one of the near
    spellings is, the correction is not a guess by edit distance but a reading the
    editions actually carry. Returns "" when there is nothing to say.
    """
    tokens = [token for token in q.split() if token]
    if len(tokens) < 2:
        return ""
    word, before = tokens[-1], tokens[-2]

    index = get_context_index()
    if not index.word_count:
        return ""

    attested = index.attested_among(
        word,
        before,
        _orthographic_candidates(word, headwords_clean_set, ascii_to_unicode_dict),
    )

    if index.is_attested(word, before):
        headlines = index.readings(word, before)
        detail = "; ".join(str(headline) for headline in headlines[:3])
        return (
            f'<h3 class="dpd">attested in the texts:</h3><p>{before} <b>{word}</b> '
            f"— {detail}</p>"
        )

    if attested:
        rows = []
        for candidate in attested[:5]:
            headlines = index.readings(candidate, before)
            origin = headlines[0].provenance if headlines else ""
            source = headlines[0].source if headlines else ""
            note = f" ({source}{': ' + origin if origin else ''})" if source else ""
            rows.append(f"<b>{candidate}</b>{note}")
        return (
            f'<h3 class="dpd">not found, but the texts read:</h3><p>{before} '
            f"{', '.join(rows)}</p>"
        )

    return ""


def _orthographic_candidates(
    word: str,
    headwords_clean_set: set[str],
    ascii_to_unicode_dict: dict[str, list[str]],
) -> list[str]:
    """The same suggestions the orthographic fallback would offer, as a plain list.

    The diacritic-insensitive index is keyed by the *folded* word, so the lookup has to
    fold the query first: `viññana` and `viññāṇa` both fold to `vinnana`, which is what
    makes the index find a correction that edit distance ranks below longer words.
    """
    # .get, not []: indexing a defaultdict would store an entry per distinct miss
    candidates = list(ascii_to_unicode_dict.get(unidecode(word), []))
    candidates.extend(
        item
        for item in difflib.get_close_matches(
            word, headwords_clean_set, n=10, cutoff=0.7
        )
        if item not in candidates
    )
    return candidates


def find_closest_matches(
    q,
    headwords_clean_set,
    ascii_to_unicode_dict,
) -> str:
    # Keyed by the folded word: consulted with the raw query this index never matched a
    # query that carried diacritics, which is most of them. .get, not []: indexing a
    # defaultdict would store an entry per distinct miss.
    ascii_matches = ascii_to_unicode_dict.get(unidecode(q), [])
    closest_headword_matches = difflib.get_close_matches(
        q, headwords_clean_set, n=10, cutoff=0.7
    )

    combined_list = []
    combined_list.extend(ascii_matches)
    combined_list.extend(
        [item for item in closest_headword_matches if item not in ascii_matches]
    )

    string = '<h3 class="dpd">No results found. '
    if combined_list:
        string += "The closest matches are:</h3><br>"
        string += "<p>"
        string += ", ".join(combined_list)
        string += "</p>"
    else:
        string += "</h3>"

    return string


def fuzzy_replace(string: str) -> str:
    string = re.sub("aa|aā|āa|a|ā", "(a|ā|aa|aā|āa)", string)
    string = re.sub("ii|iī|īi|i|ī", "(i|ī|ii|iī|īi)", string)
    string = re.sub("uu|uū|ūu|u|ū", "(u|ū|uu|uū|ūu)", string)
    string = re.sub("kkh|kk|kh|k", "(k|kk|kh|kkh)", string)
    string = re.sub("ggh|gg|gh|g", "(g|gh|gg|ggh)", string)
    string = re.sub("ṅṅ|ññ|ṇṇ|nn|ṅ|ñ|ṇ|n|ṃ", "(ṅ|ñ|ṇ|n|ṅṅ|ññ|ṇṇ|nn|ṃ)", string)
    string = re.sub("cch|cc|ch|c", "(c|ch|cc|cch)", string)
    string = re.sub("jjh|jj|jh|j", "(j|jh|jj|jjh)", string)
    string = re.sub("ṭṭh|tth|ṭṭ|tt|ṭh|th|ṭ|t", "(ṭ|ṭh|ṭṭ|ṭṭh|t|tt|th|tth)", string)
    string = re.sub("ḍḍh|ddh|ḍḍ|dd|ḍh|dh|ḍ|d", "(ḍ|ḍh|ḍḍ|ḍḍh|d|dh|dd|ddh)", string)
    string = re.sub("pph|pp|ph|p", "(p|ph|pp|pph)", string)
    string = re.sub("bbh|bb|bh|b", "(b|bh|bb|bbh)", string)
    string = re.sub("mm|m|ṃ", "(m|mm|ṃ)", string)
    string = re.sub("yy|y", "(y|yy)", string)
    string = re.sub("rr|r", "(r|rr)", string)
    string = re.sub("ll|l|ḷ", "(l|ll|ḷ)", string)
    string = re.sub("vv|v", "(v|vv)", string)
    string = re.sub("ss|s", "(s|ss)", string)
    return string
