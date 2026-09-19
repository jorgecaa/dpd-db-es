"""The webapp must never select the columns a serving-db build drops.

`kamma/threads/20260918_serving_db_diet/` measured what an ORM entity load costs here:
dropping the eleven columns no code reads still broke **125 of 130** golden-oracle
responses, because SQLAlchemy selects every column the model declares. Fixing that
meant `defer`ring them at the display loads and `load_only`ing the family lookup; with
that in place a 32.6 %-smaller database serves the same bytes.

This test is the lock. It renders real pages against the live db and fails if a
droppable column comes back into a SELECT, or if a deferred attribute starts being
read one row at a time (a lazy load means the column *is* being read, and a trimmed db
would raise `no such column` instead of paying that query).

Marked `slow`: it needs the live db and the real templates.
"""

from __future__ import annotations

import pytest
from fastapi.templating import Jinja2Templates
from sqlalchemy import event

from db.db_helpers import get_db_session, make_roots_count_dict
from exporter.webapp.preloads import (
    make_ascii_to_unicode_dict,
    make_headwords_clean_set,
)
from exporter.webapp.toolkit import (
    _UNREAD_HEADWORD_COLUMNS,
    _UNREAD_LOOKUP_COLUMNS,
    lookup_key_variants,
    make_dpd_html,
)
from tools import spanish
from tools.paths import ProjectPaths

# One term per display path: a lookup hit, a numeric id, and a numbered lemma.
TERMS = ("buddha", "67047", "a 1.1")


def _statements(term: str, pth: ProjectPaths, templates: Jinja2Templates) -> list[str]:
    session = get_db_session(pth.dpd_db_path)
    roots_count_dict = make_roots_count_dict(session)
    clean_set = make_headwords_clean_set(session)
    ascii_dict = make_ascii_to_unicode_dict(session)
    lookup_key_variants(pth.dpd_db_path)

    collected: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        collected.append(" ".join(statement.split()))

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        make_dpd_html(term, pth, templates, roots_count_dict, clean_set, ascii_dict)
    finally:
        event.remove(engine, "before_cursor_execute", _record)
        session.close()
    return collected


@pytest.fixture(scope="module")
def rendered() -> dict[str, list[str]]:
    pth = ProjectPaths()
    templates = Jinja2Templates(directory=str(pth.webapp_templates_dir))
    spanish.translate_templates(templates)
    templates.env.filters["inflexion_es"] = spanish.translate_inflection_html
    templates.env.globals["show_tbw"] = True
    return {term: _statements(term, pth, templates) for term in TERMS}


@pytest.mark.slow
def test_pages_do_not_select_the_droppable_columns(
    rendered: dict[str, list[str]],
) -> None:
    for term, statements in rendered.items():
        leaked = sorted(
            {
                column
                for statement in statements
                for column in _UNREAD_HEADWORD_COLUMNS
                if f"dpd_headwords.{column} AS" in statement
            }
            | {
                column
                for statement in statements
                for column in _UNREAD_LOOKUP_COLUMNS
                if f"lookup.{column} AS" in statement
            }
        )
        assert not leaked, f"{term}: {leaked} would not exist in a serving-db build"


@pytest.mark.slow
def test_pages_do_not_lazy_load_deferred_columns(
    rendered: dict[str, list[str]],
) -> None:
    """A single-column SELECT by primary key is a deferred attribute being read."""
    for term, statements in rendered.items():
        lazy = [
            statement
            for statement in statements
            if statement.count("dpd_headwords.") == 1
            and "WHERE dpd_headwords.id = " in statement
        ]
        assert not lazy, f"{term}: {lazy[:3]}"
