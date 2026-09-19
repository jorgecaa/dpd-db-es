import re
from collections import defaultdict

from sqlalchemy.orm import Session
from unidecode import unidecode

from db.db_helpers import make_roots_count_dict as make_roots_count_dict
from db.models import DpdHeadword, Lookup
from tools.pali_sort_key import pali_list_sorter


def clean_lemma(lemma_1: str) -> str:
    """Strip the sense number from a lemma: `dhamma 1.01` -> `dhamma`.

    Mirrors `db/models.py::DpdHeadword.lemma_clean` exactly, and a parity test
    keeps the two in step. Keeping it here is what lets the preloads query one
    column instead of materialising whole rows: loading all 89,280 `DpdHeadword`
    entities twice cost ~541 MiB of anonymous RSS that the allocator never gave
    back (see `kamma/threads/20260918_webapp_memory/spec.md`).
    """
    return re.sub(r" \d.*$", "", lemma_1)


def make_headwords_clean_set(db_session: Session) -> set[str]:
    """Make a set of Pāḷi headwords and English meanings."""

    # Columns only: `lemma_clean` derives from `lemma_1`, and row loads are what
    # make this expensive.
    lemma_rows = db_session.query(DpdHeadword.lemma_1).all()
    headwords_clean_set = {clean_lemma(lemma_1) for (lemma_1,) in lemma_rows}

    # add all english meanings
    # lookup_key is a primary key (column), so this is efficient
    lookup_results = db_session.query(Lookup.lookup_key).filter(Lookup.epd != "").all()
    headwords_clean_set.update(lookup_key for (lookup_key,) in lookup_results)

    return headwords_clean_set


def make_ascii_to_unicode_dict(db_session: Session) -> dict[str, list[str]]:
    """ASCII Key: Unicode Value."""

    lemma_rows = db_session.query(DpdHeadword.lemma_1, DpdHeadword.lemma_2).all()

    headwords_clean_set: set[str] = set()
    for lemma_1, lemma_2 in lemma_rows:
        headwords_clean_set.add(clean_lemma(lemma_1))
        headwords_clean_set.add(lemma_2)
    headwords_sorted_list = pali_list_sorter(headwords_clean_set)

    ascii_to_unicode_dict = defaultdict(list)
    for headword in headwords_sorted_list:
        headword_ascii = unidecode(headword)
        if (
            headword_ascii != headword
            and headword not in ascii_to_unicode_dict[headword_ascii]
        ):
            ascii_to_unicode_dict[headword_ascii].append(headword)

    return ascii_to_unicode_dict
