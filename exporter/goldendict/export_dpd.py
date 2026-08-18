"""Compile HTML data for DpdHeadword."""

from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import TypedDict

import jinja2
import psutil

from minify_html import minify
from sqlalchemy.orm.session import Session
from sqlalchemy.sql import func

from db.models import (
    DpdHeadword,
    DpdRoot,
    FamilyCompound,
    FamilyIdiom,
    FamilyRoot,
    FamilySet,
    FamilyWord,
    SuttaInfo,
)
from tools.configger import config_test
from tools.goldendict_exporter import DictEntry
from tools.niggahitas import add_niggahitas
from tools.paths import ProjectPaths
from tools.printer import printer as pr
from tools.speech_marks import SpeechMarksDict
from tools.utils import (
    RenderedSizes,
    default_rendered_sizes,
    extract_body,
    squash_whitespaces,
    sum_rendered_sizes,
)
from exporter.jinja2_env import get_jinja2_env
from exporter.goldendict.data_classes import HeadwordData


DpdHeadwordDbRowItems = tuple[DpdHeadword, FamilyRoot, FamilyWord]


class DpdHeadwordDbParts(TypedDict):
    pali_word: DpdHeadword
    pali_root: DpdRoot
    family_root: FamilyRoot
    family_word: FamilyWord
    family_compounds: list[FamilyCompound]
    family_idioms: list[FamilyIdiom]
    family_set: list[FamilySet]
    sutta_info: SuttaInfo


class DpdHeadwordRenderDataBase(TypedDict):
    speech_marks: SpeechMarksDict
    cf_set: set[str]
    idioms_set: set[str]
    show_id: bool
    show_tbw: bool


class DpdHeadwordRenderData(DpdHeadwordRenderDataBase):
    pth: ProjectPaths
    jinja_env: jinja2.Environment


def render_pali_word_dpd_html(
    db_parts: DpdHeadwordDbParts,
    render_data: DpdHeadwordRenderData,
) -> tuple[DictEntry, RenderedSizes]:
    rd = render_data
    size_dict = default_rendered_sizes()

    i: DpdHeadword = db_parts["pali_word"]
    rt: DpdRoot = db_parts["pali_root"]
    fr: FamilyRoot = db_parts["family_root"]
    fw: FamilyWord = db_parts["family_word"]
    fc: list[FamilyCompound] = db_parts["family_compounds"]
    fi: list[FamilyIdiom] = db_parts["family_idioms"]
    fs: list[FamilySet] = db_parts["family_set"]
    su: SuttaInfo = db_parts["sutta_info"]

    pth = rd["pth"]
    jinja_env = rd["jinja_env"]
    speech_marks = rd["speech_marks"]

    # Use ViewModel
    data = HeadwordData(
        i=i,
        rt=rt,
        fr=fr,
        fw=fw,
        fc=fc,
        fi=fi,
        fs=fs,
        su=su,
        pth=pth,
        jinja_env=jinja_env,
        cf_set=rd["cf_set"],
        idioms_set=rd["idioms_set"],
        show_id=rd["show_id"],
    )

    template = jinja_env.get_template("dpd_headword.jinja")
    html = template.render(d=data)

    # Re-calculate parts for parity
    header = data.header
    body = extract_body(html)

    final_html = squash_whitespaces(header) + minify(body)

    size_dict["dpd_header"] += len(header)
    size_dict["dpd_summary"] += len(data.meaning_combo_html)

    # synonyms
    synonyms: list[str] = add_niggahitas(i.inflections_list_all)

    # Apostrophe contractions for any synonym that is a speech-mark key. No
    # contraction is itself a key, so a single pass over the base list finds
    # them all (the old code appended to the list it was iterating).
    contractions = [
        contraction
        for synonym in synonyms
        if synonym in speech_marks
        for contraction in speech_marks[synonym]
        if "'" in contraction
    ]
    synonyms += contractions

    synonyms += i.inflections_sinhala_list
    synonyms += i.inflections_devanagari_list
    synonyms += i.inflections_thai_list
    synonyms += i.family_set_list
    synonyms += [str(i.id)]

    if i.needs_sutta_info_button:
        synonyms += i.su.sutta_codes_list  # type: ignore[union-attr]

    size_dict["dpd_synonyms"] += len(str(synonyms))

    res = DictEntry(
        word=i.lemma_1,
        definition_html=final_html,
        definition_plain="",
        synonyms=synonyms,
    )

    return (res, size_dict)


_WORKER_RENDER_DATA: DpdHeadwordRenderData | None = None


def _worker_init(
    render_data: DpdHeadwordRenderDataBase,
    path: ProjectPaths,
) -> None:
    """Worker initializer: build the jinja env and full render data once per
    worker, reused across every batch that worker handles."""
    global _WORKER_RENDER_DATA
    jinja_env = get_jinja2_env("exporter/goldendict/templates")
    # pyrefly: ignore  # jinja2 leaves Environment.globals unannotated, so its
    # value type infers as the union of jinja's own defaults; extending it is
    # the documented usage.
    jinja_env.globals["show_tbw"] = render_data["show_tbw"]
    _WORKER_RENDER_DATA = DpdHeadwordRenderData(
        **render_data,
        pth=path,
        jinja_env=jinja_env,
    )


def _render_batch(
    batch: list[DpdHeadwordDbParts],
) -> list[tuple[DictEntry, RenderedSizes]]:
    """Render one batch of headwords in a worker. A worker exception propagates
    to the parent when the result is consumed, and a worker killed outright
    (e.g. OOM) surfaces as ``BrokenProcessPool`` — either way the build fails
    loudly rather than hanging."""
    assert _WORKER_RENDER_DATA is not None, "worker not initialised"
    return [render_pali_word_dpd_html(parts, _WORKER_RENDER_DATA) for parts in batch]


def _dedupe_keys(keys: list[str]) -> list[str]:
    """Distinct keys in first-occurrence order.

    Replicates the effect of the old ``FamilyX.field.in_(list)`` query, which
    returned each matching family once; the caller then sorted by first index
    in the list. Some headwords (e.g. long dvanda compounds) carry a repeated
    family name in ``family_compound_list``.
    """
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _lookup_family_compounds(
    pw: DpdHeadword, fc_map: dict[str, FamilyCompound]
) -> list[FamilyCompound]:
    if pw.family_compound:
        keys = _dedupe_keys(pw.family_compound_list)
    else:
        keys = [pw.lemma_clean]
    return [fc_map[k] for k in keys if k in fc_map]


def _lookup_family_idioms(
    pw: DpdHeadword, fi_map: dict[str, FamilyIdiom]
) -> list[FamilyIdiom]:
    if pw.family_idioms:
        keys = _dedupe_keys(pw.family_idioms_list)
    else:
        keys = [pw.lemma_clean]
    return [fi_map[k] for k in keys if k in fi_map]


def _lookup_family_set(
    pw: DpdHeadword, fs_map: dict[str, FamilySet]
) -> list[FamilySet]:
    return [fs_map[k] for k in _dedupe_keys(pw.family_set_list) if k in fs_map]


def _batches_of_size(
    items: list[DpdHeadwordDbParts], size: int
) -> list[list[DpdHeadwordDbParts]]:
    """Split into batches of a fixed ``size``.

    Deliberately not ``list_into_batches``, which takes a batch *count*: with a
    fixed count the batch size is the dataset size divided by the core count,
    so every worker's peak memory grows with the dictionary and has no ceiling.
    Fixing the size instead bounds a worker's memory to ``size`` headwords no
    matter how large the dictionary becomes.
    """
    return [items[i : i + size] for i in range(0, len(items), size)]


def _base_dpd_query(db_session: Session):
    """The joined DpdHeadword/FamilyRoot/FamilyWord query, without order or limit."""
    return (
        db_session.query(DpdHeadword, FamilyRoot, FamilyWord)
        .outerjoin(
            FamilyRoot, DpdHeadword.root_family_key == FamilyRoot.root_family_key
        )
        .outerjoin(FamilyWord, DpdHeadword.family_word == FamilyWord.word_family)
    )


def _iter_dpd_row_pages(
    db_session: Session,
    data_limit: int,
    page_size: int,
) -> Iterator[list[DpdHeadwordDbRowItems]]:
    """Yield pages of (DpdHeadword, FamilyRoot, FamilyWord) row tuples.

    Keyset pages ordered by ``lemma_1``, which is unique and carries the
    table's implicit unique index, so it is a stable paging key and no
    per-offset re-sort is needed. At most ``page_size`` rows are resident at
    once, and the entry order is identical to the old single-page default.

    The session is expunged after each page is consumed: without that the
    identity map keeps every loaded row alive for the whole run and paging
    frees nothing. Expunging is safe here because the caller finishes with a
    page (including its lazy-loaded ``rt``/``su`` relationships) before asking
    for the next one.
    """
    fetched = 0
    last_lemma: str | None = None
    while True:
        remaining = page_size
        if data_limit:
            remaining = min(page_size, data_limit - fetched)
            if remaining <= 0:
                return
        query = _base_dpd_query(db_session).order_by(DpdHeadword.lemma_1)
        if last_lemma is not None:
            query = query.filter(DpdHeadword.lemma_1 > last_lemma)
        rows = query.limit(remaining).all()
        if not rows:
            return
        last_lemma = rows[-1][0].lemma_1
        fetched += len(rows)
        yield [row._tuple() for row in rows]
        db_session.expunge_all()


def generate_dpd_html(
    db_session: Session,
    pth: ProjectPaths,
    speech_marks: SpeechMarksDict,
    cf_set: set[str],
    idioms_set: set[str],
    data_limit: int = 0,
) -> tuple[list[DictEntry], RenderedSizes]:
    pr.green_title("generating dpd html")

    show_id: bool = config_test("dictionary", "show_id", "yes")
    show_tbw: bool = config_test("dictionary", "show_tbw", "yes")

    pali_words_count = db_session.query(func.count(DpdHeadword.id)).scalar()

    if data_limit != 0:
        pali_words_count = data_limit

    # Both bounds are absolute, not a fraction of the dataset: the parent holds
    # at most one page of rows, and a worker at most one batch. Peak memory is
    # therefore flat in the size of the dictionary. A small machine gets the
    # tighter pair, but neither setting is unbounded.
    low_mem_threshold = 9 * 1024 * 1024 * 1024
    mem = psutil.virtual_memory()
    low_mem = mem.total < low_mem_threshold
    page_size = 2000 if low_mem else 5000
    batch_size = 125 if low_mem else 250

    num_logical_cores = psutil.cpu_count()
    if num_logical_cores is None:
        num_logical_cores = 1
    pr.green_title(f"running with {num_logical_cores} cores")

    render_data: DpdHeadwordRenderDataBase = {
        "speech_marks": speech_marks,
        "cf_set": cf_set,
        "idioms_set": idioms_set,
        "show_id": show_id,
        "show_tbw": show_tbw,
    }

    # Preload the family tables once instead of 3 queries per headword.
    fc_map: dict[str, FamilyCompound] = {
        x.compound_family: x for x in db_session.query(FamilyCompound).all()
    }
    fi_map: dict[str, FamilyIdiom] = {
        x.idiom: x for x in db_session.query(FamilyIdiom).all()
    }
    fs_map: dict[str, FamilySet] = {x.set: x for x in db_session.query(FamilySet).all()}

    def _add_parts(row: DpdHeadwordDbRowItems) -> DpdHeadwordDbParts:
        pw, fr, fw = row
        return {
            "pali_word": pw,
            "pali_root": pw.rt,
            "family_root": fr,
            "family_word": fw,
            "family_compounds": _lookup_family_compounds(pw, fc_map),
            "family_idioms": _lookup_family_idioms(pw, fi_map),
            "family_set": _lookup_family_set(pw, fs_map),
            "sutta_info": pw.su,  # type: ignore[typeddict-item]
        }

    dpd_data_list: list[DictEntry] = []
    rendered_sizes: list[RenderedSizes] = []

    # One persistent worker pool builds the jinja env + render data once each,
    # then renders every page's batches. A page is a bounded keyset slice and a
    # batch a fixed number of headwords, so neither the parent's nor a worker's
    # working set scales with the dictionary. Batches stay well finer than the
    # page so completions stream in and drive the progress counter live rather
    # than arriving in one burst at the end.
    report_every = 5000
    processed = 0
    reported = 0
    with ProcessPoolExecutor(
        max_workers=num_logical_cores,
        initializer=_worker_init,
        initargs=(render_data, pth),
    ) as pool:
        for page_rows in _iter_dpd_row_pages(db_session, data_limit, page_size):
            dpd_db_data = [_add_parts(row) for row in page_rows]

            batches: list[list[DpdHeadwordDbParts]] = _batches_of_size(
                dpd_db_data, batch_size
            )
            futures = {
                pool.submit(_render_batch, batch): len(batch) for batch in batches
            }

            for future in as_completed(futures):
                batch_result = future.result()
                for entry, size in batch_result:
                    dpd_data_list.append(entry)
                    rendered_sizes.append(size)

                processed += futures[future]
                if (
                    processed - reported >= report_every
                    or processed == pali_words_count
                ):
                    marker = batch_result[0][0].word if batch_result else ""
                    pr.counter(processed, pali_words_count, marker)
                    reported = processed

    total_sizes = sum_rendered_sizes(rendered_sizes)

    return dpd_data_list, total_sizes
