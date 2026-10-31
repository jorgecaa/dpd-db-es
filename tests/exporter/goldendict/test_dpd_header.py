# -*- coding: utf-8 -*-
"""Tests for dpd_header.jinja — frequency block guard, JS wiring drift.

Fixtures are real ``dpd_headwords`` rows captured into
``test_dpd_header_fixtures.json``, including the rendered header HTML as a
golden master. ``cf_set`` / ``idioms_set`` membership is frozen from the
capture so the tests do not depend on live cache files.

The golden master is rendered inside ``spanish.suppressed()``: the fixtures were
captured with the Spanish overlay off, and with it on (``[spanish] enabled`` in
``config.ini``, a machine-local switch) the overlay rewrites ``meaning_1`` on
these detached rows and every ``meaning_1``-gated family button drops out.
"""

# ruff: noqa: E402

import json
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.modules.setdefault(
    "aksharamukha",
    types.SimpleNamespace(
        transliterate=types.SimpleNamespace(process=lambda *args, **kwargs: "")
    ),
)

from db.models import DpdHeadword
from exporter.jinja2_env import get_jinja2_env
from tools import spanish
from tools.paths import ProjectPaths

FIXTURES = json.loads(
    (Path(__file__).parent / "test_dpd_header_fixtures.json").read_text(
        encoding="utf-8"
    )
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_headword(fixture: dict) -> DpdHeadword:
    hw = DpdHeadword(**fixture["columns"])
    hw.cf_set = frozenset(fixture["cf_set_members"])
    hw.idioms_set = frozenset(fixture["idioms_set_members"])
    return hw


def _render(fixture: dict) -> str:
    env = get_jinja2_env("exporter/goldendict/templates")
    template = env.get_template("dpd_header.jinja")
    d = SimpleNamespace(i=_make_headword(fixture), date=FIXTURES["fixed_date"])
    with spanish.suppressed():
        return template.render(d=d)


class TestGoldenMaster:
    def test_root_compound_set_word_byte_identical(self):
        fixture = FIXTURES["golden_root_compound_set"]
        assert _render(fixture) == fixture["header_html"]

    def test_word_family_word_byte_identical(self):
        fixture = FIXTURES["golden_word_family"]
        assert _render(fixture) == fixture["header_html"]


class TestEmptyFreqData:
    """A freq-eligible pos with empty freq_data (a word added after the last
    frequency run) used to crash the render with
    ``TypeError: Object of type Undefined is not JSON serializable``.
    The guard now omits the frequency keys instead."""

    def test_renders_without_crash_and_omits_freq_keys(self):
        fixture = FIXTURES["empty_freq_data"]
        assert fixture["columns"]["freq_data"] == ""
        hw = _make_headword(fixture)
        assert hw.needs_frequency_button

        html = _render(fixture)
        assert "CstFreq" not in html
        assert "FreqHeading" not in html
        assert f'"id": {fixture["columns"]["id"]}' in html


class TestJsWiring:
    """The header loads JS by hand-written ``<link>``, the build ships JS from
    ``js_paths`` in exporter/goldendict/main.py. Nothing links the two lists, so
    a file added to one and forgotten in the other silently ships unused — or is
    loaded from a name that was never embedded."""

    def test_shipped_and_loaded_js_agree(self):
        main_source = (REPO_ROOT / "exporter/goldendict/main.py").read_text(
            encoding="utf-8"
        )
        block = re.search(r"js_paths=\[(?P<paths>.*?)\]", main_source, re.DOTALL)
        assert block, "js_paths block not found in exporter/goldendict/main.py"

        pth = ProjectPaths(REPO_ROOT, create_dirs=False)
        shipped = {
            getattr(pth, name).name
            for name in re.findall(r"g\.pth\.(\w+)", block["paths"])
        }

        header = (
            REPO_ROOT / "exporter/goldendict/templates/dpd_header.jinja"
        ).read_text(encoding="utf-8")
        loaded = set(re.findall(r'<link href="([^"]+\.js)" class="load_js"', header))

        assert shipped == loaded
