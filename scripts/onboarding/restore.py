#!/usr/bin/env python3

"""Bring a fresh clone back to a working Spanish dictionary, in one command.

`contributor_setup.py` gets a contributor as far as the English DPD: git,
submodules, `dpd.db` from the latest release, a `config.ini`. It knows nothing
about the Spanish overlay, so a clone left there serves English and gives no
sign that anything is missing.

This carries on from where that stops. It checks each piece, does only what is
absent, and ends by asking the dictionary itself whether it answers in Spanish —
because every step can succeed and the overlay still be off, which is the one
failure that looks like success.

What no clone can restore, and this says so rather than leaving it to be
discovered:

    dpd.db          2.1 GB, downloaded from the DPD's own releases
    config.ini      not in git; the Spanish switch lives in it
    the Pāḷi-English dictionaries consulted through `sdcv`, which are third
    party and live outside any repository — the translation pipeline runs
    without them, but blind

Usage:
    uv run python scripts/onboarding/restore.py [--skip-db]
"""

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.onboarding.contributor_setup import (
    download_database,
    extract_database,
    get_latest_db_release_url,
)
from tools.configger import config_read, config_update
from tools.paths import ProjectPaths
from tools.printer import printer as pr

SIDECAR = Path("dpd_es.db")
MATERIAL = Path("resources/dpd-dictionary-es/js/dpd_ebts_es.js")
CONFIG = Path("config.ini")

# A headword the generated translations reach, used as the closing question:
# if this reads Spanish the overlay is on, the sidecar is built and the db is
# the one the fingerprints were made against.
PROBE = "akatayogga"


def ensure_environment() -> bool:
    """Install the dependencies, or say what is missing to."""
    pr.green_tmr("dependencias")
    try:
        result = subprocess.run(
            ["uv", "sync"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        pr.no("falta uv")
        pr.red("instálalo desde https://astral.sh/uv y vuelve a ejecutar")
        return False
    if result.returncode != 0:
        pr.no("falló")
        pr.red(result.stderr.strip()[:400])
        return False
    pr.yes("listas")
    return True


def ensure_config() -> bool:
    """A `config.ini` with the Spanish overlay switched on.

    The file is gitignored, so a clone has none, and nothing else in the project
    turns the overlay on: a restore that skipped this would rebuild everything
    correctly and still serve English.
    """
    pr.green_tmr("config.ini")
    if not CONFIG.exists():
        subprocess.run(
            ["uv", "run", "python", "scripts/bash/initial_setup_run_once.py"],
            capture_output=True,
            text=True,
            check=False,
        )
    if not CONFIG.exists():
        pr.no("no se creó")
        return False
    if config_read("spanish", "enabled") != "yes":
        config_update("spanish", "enabled", "yes")
        pr.yes("overlay activado")
    else:
        pr.yes("ya estaba")
    return True


def ensure_database(paths: ProjectPaths, skip: bool) -> bool:
    """`dpd.db`, downloaded from the DPD's releases if it is not here."""
    pr.green_tmr("dpd.db")
    if paths.dpd_db_path.exists():
        pr.yes(f"{paths.dpd_db_path.stat().st_size // 1024 // 1024} MB")
        return True
    if skip:
        pr.no("ausente")
        pr.red("--skip-db se pidió, pero sin dpd.db no hay diccionario")
        return False
    url = get_latest_db_release_url()
    if url is None:
        pr.no("sin release")
        pr.red("no se pudo consultar la última release del DPD")
        return False
    pr.yes("descargando")
    archive = paths.dpd_db_path.parent / "dpd.db.tar.bz2"
    if not download_database(url, archive):
        return False
    return extract_database(archive, paths.dpd_db_path.parent)


def ensure_sidecar() -> bool:
    """`dpd_es.db`, rebuilt from the vendored material and this project's own."""
    pr.green_tmr("dpd_es.db")
    if not MATERIAL.exists():
        pr.no("sin material")
        pr.red(f"falta {MATERIAL}; el repositorio está incompleto")
        return False
    result = subprocess.run(
        ["uv", "run", "python", "scripts/build/build_spanish_sidecar.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not SIDECAR.exists():
        pr.no("falló")
        pr.red(result.stderr.strip()[:400])
        return False
    pr.yes(f"{SIDECAR.stat().st_size // 1024 // 1024} MB")
    return True


def verify(paths: ProjectPaths) -> bool:
    """Ask the dictionary, not the files, whether Spanish comes out.

    Everything above can report success while the reader still gets English: the
    overlay reads its switch once per process, the fingerprints compare against
    whichever `dpd.db` is present, and a sidecar can be built and never applied.
    """
    pr.green_tmr("comprobación")
    from db.db_helpers import get_db_session
    from db.models import DpdHeadword
    from tools import spanish

    spanish.reload()
    if not spanish.is_active():
        pr.no("overlay apagado")
        return False
    db_session = get_db_session(paths.dpd_db_path)
    headword = (
        db_session.query(DpdHeadword).filter(DpdHeadword.lemma_1 == PROBE).one_or_none()
    )
    if headword is None:
        pr.no("falta el lema")
        pr.red(f"{PROBE} no está en esta dpd.db; ¿es una versión distinta?")
        return False
    with spanish.suppressed():
        english = headword.meaning_1
    served = headword.meaning_1
    if served == english:
        pr.no("sale inglés")
        pr.red(f"{PROBE} sigue en inglés: «{english[:60]}»")
        return False
    pr.yes("en español")
    pr.summary(PROBE, served[:60])
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-db", action="store_true", help="fail instead of downloading dpd.db"
    )
    args = parser.parse_args()

    pr.tic()
    pr.yellow_title("restaurando el diccionario en español")
    paths = ProjectPaths()
    steps = (
        ensure_environment(),
        ensure_config(),
        ensure_database(paths, args.skip_db),
        ensure_sidecar(),
    )
    if not all(steps) or not verify(paths):
        pr.red("la restauración no está completa; mira el paso que falló")
        pr.toc()
        sys.exit(1)

    pr.green("el diccionario responde en español")
    pr.amber("fuera de todo repositorio, y por tanto no restaurado:")
    pr.amber("  los diccionarios pali-inglés que consulta sdcv (NCPED, PTS, Cone)")
    pr.amber("  sin ellos la línea de traducción funciona, pero sin glosas externas")
    pr.toc()


if __name__ == "__main__":
    main()
