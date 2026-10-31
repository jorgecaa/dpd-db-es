#!/usr/bin/env python3

"""Search bold_definitions using vanilla find or regex."""

from typing import List

from rich import print

from db.db_helpers import get_db_session
from db.models import BoldDefinition
from tools.paths import ProjectPaths
from tools.search_deadline import install_regexp


class BoldDefinitionsSearchManager:
    """Search `bold_definitions`, owning one db session.

    The caller owns that session's lifetime. Short-lived callers (the webapp's
    `/bd_search`) should use the manager as a context manager so the connection
    is returned instead of waiting for garbage collection; long-lived callers
    (gui2 keeps one manager for the whole app) keep using it directly.

    Why it matters: each `/bd_search` used to leave its session open, and the
    measured churn was ~0.25 MiB per request (see
    `kamma/threads/20260918_webapp_memory/plan.md`, task 2.1).
    """

    def __init__(self) -> None:
        self.pth = ProjectPaths()
        self.session = get_db_session(self.pth.dpd_db_path)
        install_regexp(self.session)

    def close(self) -> None:
        """Release the session, and with it the connection (the engine is NullPool)."""
        self.session.close()

    def __enter__(self) -> "BoldDefinitionsSearchManager":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def search(
        self,
        search1: str,
        search2: str,
        option: str = "regex",
        limit: int | None = None,
    ) -> List[BoldDefinition]:
        """Main search method with options matching db_search_bd.

        Pass ``limit`` to cap the number of rows returned (e.g. for a GUI that
        cannot render an unbounded result set). Default ``None`` returns all rows.
        """
        if not search1 and not search2:
            return []

        query = self.session.query(BoldDefinition)

        if option == "starts_with":
            query = query.filter(
                BoldDefinition.bold.regexp_match(f"^{search1}")
            ).filter(BoldDefinition.commentary.regexp_match(search2))
        elif option == "regex":
            query = query.filter(BoldDefinition.bold.regexp_match(search1)).filter(
                BoldDefinition.commentary.regexp_match(search2)
            )
        elif option == "fuzzy":
            from exporter.webapp.toolkit import fuzzy_replace

            query = query.filter(
                BoldDefinition.bold.regexp_match(fuzzy_replace(search1))
            ).filter(BoldDefinition.commentary.regexp_match(fuzzy_replace(search2)))
        else:  # plain search
            query = query.filter(BoldDefinition.bold.ilike(f"%{search1}%")).filter(
                BoldDefinition.commentary.ilike(f"%{search2}%")
            )

        if limit is not None:
            query = query.limit(limit)

        return query.all()


if __name__ == "__main__":
    searcher = BoldDefinitionsSearchManager()
    search1 = input("enter search1: ")
    search2 = input("enter search2: ")
    search_results = searcher.search(search1, search2)
    print(f"{len(search_results)} results found")
