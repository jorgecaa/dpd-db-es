"""Tests for scripts/build/grammar_to_json.py.

The script writes the grammar data twice: the JSON dump for processing (85 MB of
``{word, pos, grammar, components}`` objects) and ``grammar_by_word_json.js`` for
the dictionary, where every form maps to a packed string of indices into two shared
tables (14.1 MiB, 55 ms to parse, against 36.7 MiB / 357 ms for plain triples).

The JS file is generated and gitignored, so nothing else in the suite would notice
if its shape changed — the dictionary loads it by the same name the header links, and
a rename or an off-by-one table index would only surface in a built dictionary. These
tests pin the encoding round-trip, and pin the one thing written twice in two
languages: the grammar-string split, which must agree with
``exporter/webapp/data_classes.py`` and with ``grammar_by_word_template.js``.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from exporter.webapp.data_classes import GrammarData
from scripts.build.grammar_to_json import (
    ANALYSES_VAR_NAME,
    JS_VAR_NAME,
    WORDS_VAR_NAME,
    build_by_word,
    parse_grammar,
    write_js,
    write_json,
)

FIXTURE_PATH = Path(__file__).parent / "test_grammar_to_json_fixtures.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
JS_TEMPLATE = REPO_ROOT / "exporter/goldendict/javascript/grammar_by_word_template.js"
JS_DATA = REPO_ROOT / "exporter/goldendict/javascript/grammar_by_word_json.js"
JSON_DUMP = REPO_ROOT / "shared_data/grammar/grammar_by_word.json"
NODE = shutil.which("node")

# every shape the split has to survive: the live data is all three-component
# strings, the rest are the edges of the two languages' split rules
GRAMMAR_STRINGS = [
    "masc dat sg",
    "nt nom pl",
    "reflx aor 1st sg",
    "reflx pres 3rd pl",
    "reflx",
    "in comps",
    "masc nom sg extra",
    "",
]


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _webapp_components(grammar: str) -> list[str]:
    """The webapp's split, called on a bare instance (it reads no attributes)."""
    data = GrammarData.__new__(GrammarData)
    [(_, _, components)] = data._process_grammar([["w 1", "pos", grammar]])
    return components


class TestParseGrammar:
    def test_plain_grammar_splits_on_whitespace(self):
        assert parse_grammar("masc dat sg") == ["masc", "dat", "sg"]

    def test_reflx_keeps_its_first_two_tokens_together(self):
        assert parse_grammar("reflx aor 1st sg") == ["reflx aor", "1st", "sg"]

    def test_lone_reflx_is_kept_whole(self):
        assert parse_grammar("reflx") == ["reflx"]

    def test_in_comps_is_kept_whole(self):
        assert parse_grammar("in comps") == ["in comps"]

    def test_empty_grammar_has_no_components(self):
        assert parse_grammar("") == []


class TestBuildByWord:
    def test_counts_and_keys(self):
        fixture = _fixture()
        by_word, analyses = build_by_word(fixture["rows"])
        assert analyses == fixture["expected_analyses"]
        assert list(by_word) == [row[0] for row in fixture["rows"]]

    def test_triples_survive_the_round_trip(self):
        fixture = _fixture()
        by_word, _ = build_by_word(fixture["rows"])
        for key, triples in fixture["expected_triples"].items():
            assert [
                (item["word"], item["pos"], item["grammar"]) for item in by_word[key]
            ] == [(item["word"], item["pos"], item["grammar"]) for item in triples]

    def test_components_use_the_webapp_split(self):
        fixture = _fixture()
        by_word, _ = build_by_word(fixture["rows"])
        reflx_row = by_word["abbaha"][1]
        assert reflx_row["grammar"] == "reflx aor 1st sg"
        assert reflx_row["components"] == ["reflx aor", "1st", "sg"]


class TestWriteJson:
    def test_is_valid_json_one_form_per_line(self, tmp_path: Path):
        by_word, _ = build_by_word(_fixture()["rows"])
        path = tmp_path / "grammar_by_word.json"
        write_json(path, by_word, indent=None)
        content = path.read_text(encoding="utf-8")

        assert content.startswith("{\n")
        assert content.endswith("}\n")
        assert len(content.splitlines()) == len(by_word) + 2
        assert json.loads(content) == by_word

    def test_creates_missing_parent_directories(self, tmp_path: Path):
        path = tmp_path / "nested" / "grammar.json"
        write_json(path, {}, indent=None)
        assert path.exists()


def _read_js(path: Path) -> tuple[list[str], list[list[str]], dict[str, str]]:
    """Pull the three declarations back out of a written JS data file."""
    words_line, analyses_line, mapping = path.read_text(encoding="utf-8").split("\n", 2)
    assert words_line.startswith(f"var {WORDS_VAR_NAME} = ")
    assert analyses_line.startswith(f"var {ANALYSES_VAR_NAME} = ")
    assert mapping.startswith(f"var {JS_VAR_NAME} = {{\n")
    assert words_line.endswith(";") and analyses_line.endswith(";")
    return (
        json.loads(words_line[len(f"var {WORDS_VAR_NAME} = ") : -1]),
        json.loads(analyses_line[len(f"var {ANALYSES_VAR_NAME} = ") : -1]),
        json.loads(mapping[len(f"var {JS_VAR_NAME} = ") :]),
    )


def _decode(
    packed: str, words: list[str], analyses: list[list[str]]
) -> list[list[str]]:
    """Unpack one form the way grammarByWordRows does in the template."""
    return [
        [words[int(word_index)], *analyses[int(analysis_index)]]
        for word_index, analysis_index in (
            pair.split(",") for pair in packed.split(" ")
        )
    ]


class TestWriteJs:
    def test_tables_and_one_line_per_form(self, tmp_path: Path):
        by_word, _ = build_by_word(_fixture()["rows"])
        path = tmp_path / "grammar_by_word_json.js"
        write_js(path, by_word)
        words, analyses, mapping = _read_js(path)

        assert len(mapping) == len(by_word)
        # two table lines, the map's opening line, one line per form, the brace
        assert len(path.read_text(encoding="utf-8").splitlines()) == len(by_word) + 4
        # the tables hold each distinct value once
        assert len(words) == len(set(words))
        assert len(analyses) == len({tuple(a) for a in analyses})

    def test_every_form_decodes_back_to_its_triples(self, tmp_path: Path):
        by_word, _ = build_by_word(_fixture()["rows"])
        path = tmp_path / "grammar_by_word_json.js"
        write_js(path, by_word)
        words, analyses, mapping = _read_js(path)

        for form, packed in mapping.items():
            assert _decode(packed, words, analyses) == [
                [item["word"], item["pos"], item["grammar"]] for item in by_word[form]
            ]

    def test_creates_missing_parent_directories(self, tmp_path: Path):
        path = tmp_path / "nested" / "grammar_by_word_json.js"
        write_js(path, {})
        assert path.exists()
        assert path.read_text(encoding="utf-8").endswith("}\n")


class TestSplitParity:
    """grammar_by_word_template.js re-implements the split in JavaScript."""

    def test_every_split_matches_the_webapp(self):
        for grammar in GRAMMAR_STRINGS:
            components = parse_grammar(grammar)
            # the webapp pads to three cells, parse_grammar leaves the string as
            # it is, so the shared part is the prefix
            assert components == _webapp_components(grammar)[: len(components)]

    @pytest.mark.skipif(NODE is None, reason="node is not installed")
    def test_js_split_matches_python(self, tmp_path: Path):
        assert NODE is not None
        harness = tmp_path / "split.js"
        harness.write_text(
            JS_TEMPLATE.read_text(encoding="utf-8")
            + "\nconsole.log(JSON.stringify("
            + json.dumps(GRAMMAR_STRINGS)
            + ".map(splitGrammar)));\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [NODE, str(harness)], capture_output=True, text=True, check=True
        )
        js_splits = json.loads(result.stdout)

        assert js_splits == [_webapp_components(g) for g in GRAMMAR_STRINGS]


@pytest.mark.skipif(
    NODE is None or not JS_DATA.exists() or not JSON_DUMP.exists(),
    reason="needs node and the generated grammar artifacts",
)
def test_generated_js_decodes_to_the_json_dump(tmp_path: Path):
    """The dictionary file and the JSON dump must carry the same analyses.

    Both are gitignored build artifacts and neither is committed, so this runs
    only where they have been generated (`just grammar-json`).
    """
    assert NODE is not None
    harness = tmp_path / "decode.js"
    harness.write_text(
        "const fs = require('fs'), vm = require('vm');\n"
        "const ctx = {}; vm.createContext(ctx);\n"
        f"for (const f of [{json.dumps(str(JS_TEMPLATE))}, "
        f"{json.dumps(str(JS_DATA))}]) "
        "vm.runInContext(fs.readFileSync(f, 'utf8'), ctx);\n"
        f"const dump = JSON.parse(fs.readFileSync({json.dumps(str(JSON_DUMP))}, "
        "'utf8'));\n"
        "const forms = Object.keys(dump);\n"
        "const bad = forms.filter((f) => JSON.stringify(ctx.grammarByWordRows(f)) "
        "!== JSON.stringify(dump[f].map((x) => [x.word, x.pos, x.grammar])));\n"
        "console.log(JSON.stringify({forms: forms.length, bad: bad.slice(0, 5)}));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, "--max-old-space-size=4096", str(harness)],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)

    assert report["forms"] > 0
    assert report["bad"] == []
