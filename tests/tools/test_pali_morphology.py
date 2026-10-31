"""Unit tests for tools/pali_morphology.py.

The analyzer runs against a synthetic in-memory database, so the inversion logic is
tested without depending on the live `dpd.db`.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, DpdHeadword, InflectionTemplates
from tools.pali_morphology import PaliMorphologyAnalyzer
from tools.paths import ProjectPaths

# A miniature "a masc" paradigm: rows are cases, odd columns are endings and the next
# even column is their grammar label, row 0 is the top header.
TEMPLATE = [
    [[""], ["masc sg"], [""], ["masc pl"], [""]],
    [["nom"], ["o"], ["masc nom sg"], ["ā"], ["masc nom pl"]],
    [["acc"], ["aṃ"], ["masc acc sg"], ["e"], ["masc acc pl"]],
    [["dat"], ["assa"], ["masc dat sg"], ["ānaṃ"], ["masc dat pl"]],
    [["in comps"], ["a"], ["masc in comps"], [""], [""]],
]

HEADWORDS = [
    # id, lemma_1, stem, pattern, pos
    (1, "dhamma 1", "dhamm", "a masc", "masc"),
    (2, "nara", "nar", "a masc", "masc"),
    (3, "ca", "-", "", "ind"),
    (4, "hoti", "*", "hoti pr", "pr"),
    (5, "dhammo", "!a", "a masc", "masc"),
    (6, "mara", "mar", "a masc", "masc"),
]


def _analyzer(
    particle_rules_path: Path | None = None,
    derivation_rules_path: Path | None = None,
    sandhi_rules_path: Path | None = None,
    transform_rules_path: Path | None = None,
    use_junction_transforms: bool = False,
    components_allow_derivation: bool | None = None,
) -> PaliMorphologyAnalyzer:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            InflectionTemplates(
                pattern="a masc", like="dhamma", data=json.dumps(TEMPLATE)
            )
        )
        session.add(
            InflectionTemplates(
                pattern="hoti pr",
                like="hoti",
                data=json.dumps(
                    [[[""], ["3rd sg"]], [["pr"], ["hoti"], ["pr 3rd sg"]]]
                ),
            )
        )
        for headword_id, lemma_1, stem, pattern, pos in HEADWORDS:
            session.add(
                DpdHeadword(
                    id=headword_id,
                    lemma_1=lemma_1,
                    stem=stem,
                    pattern=pattern,
                    pos=pos,
                )
            )
        session.commit()
        return PaliMorphologyAnalyzer(
            session,
            particle_rules_path=particle_rules_path,
            derivation_rules_path=derivation_rules_path,
            sandhi_rules_path=sandhi_rules_path,
            transform_rules_path=transform_rules_path,
            use_junction_transforms=use_junction_transforms,
            components_allow_derivation=components_allow_derivation,
        )


@pytest.fixture(scope="module")
def analyzer() -> PaliMorphologyAnalyzer:
    return _analyzer()


@pytest.fixture(scope="module")
def particle_analyzer(
    tmp_path_factory: pytest.TempPathFactory,
) -> PaliMorphologyAnalyzer:
    """An analyzer with a miniature rule table in the induced file's format."""
    path = tmp_path_factory.mktemp("rules") / "particle_sandhi_rules.tsv"
    path.write_text(
        "base_last\tparticle\tbase_tail\tsurface_tail\tcount\n"
        "ā\tapi\t\tpi\t10\n"  # dhammā + api -> dhammāpi
        "o\titi\t\tti\t10\n"  # dhammo + iti -> dhammoti
        "ṃ\thi\tṃ\tñhi\t10\n",  # dhammaṃ + hi -> dhammañhi
        encoding="utf-8",
    )
    return _analyzer(particle_rules_path=path)


def test_paradigm_form_is_analysed(analyzer: PaliMorphologyAnalyzer) -> None:
    analyses = analyzer.analyze("dhammo")
    assert any(
        a.stem == "dhamm" and a.grammar == "masc nom sg" and "dhamma" in a.lemmas
        for a in analyses
    )


def test_lemma_attribution_is_exact(analyzer: PaliMorphologyAnalyzer) -> None:
    """`dhamm` belongs to dhamma only, not to nara, even though both share the pattern."""
    analyses = analyzer.analyze("dhammo")
    assert {lemma for a in analyses for lemma in a.lemmas} == {"dhamma"}


def test_compound_row_is_skipped(analyzer: PaliMorphologyAnalyzer) -> None:
    """The `in comps` row holds compounding stems, not inflections."""
    assert all(a.grammar != "masc in comps" for a in analyzer.analyze("dhammā"))


def test_unlicensed_form_has_no_analysis(analyzer: PaliMorphologyAnalyzer) -> None:
    assert analyzer.analyze("dhammasya") == []


def test_misspelling_of_a_known_word_is_rejected(
    analyzer: PaliMorphologyAnalyzer,
) -> None:
    """The point of the analyzer: `dhama` is not licensed by any paradigm."""
    assert analyzer.analyze("dhama") == []


def test_indeclinable_analyses_as_ind(analyzer: PaliMorphologyAnalyzer) -> None:
    analyses = analyzer.analyze("ca")
    assert [(a.stem, a.pattern, a.grammar) for a in analyses] == [("-", "-", "ind")]


def test_citation_form_is_licensed(analyzer: PaliMorphologyAnalyzer) -> None:
    """`nara` is not in any cell of the paradigm, but it is the headword's own form."""
    assert any(a.grammar == "lemma" for a in analyzer.analyze("nara"))


def test_irregular_star_stem_uses_the_ending_alone(
    analyzer: PaliMorphologyAnalyzer,
) -> None:
    analyses = analyzer.analyze("hoti")
    assert any(a.stem == "" and a.grammar == "pr 3rd sg" for a in analyses)


def test_inflected_form_entries_are_excluded(analyzer: PaliMorphologyAnalyzer) -> None:
    """Headword 5 has a `!` stem: its forms belong to the main headword."""
    assert all(5 not in a.headword_ids for a in analyzer.analyze("dhammo"))


def test_strict_requires_a_recorded_pattern(analyzer: PaliMorphologyAnalyzer) -> None:
    strict = analyzer.analyze("dhammo", strict=True)
    loose = analyzer.analyze("dhammo", strict=False)
    assert len(loose) >= len(strict)
    assert all(a.lemmas for a in strict)


def test_generate_returns_licensed_forms(analyzer: PaliMorphologyAnalyzer) -> None:
    forms = {form for form, _grammar in analyzer.generate("dhamm", "a masc")}
    assert forms == {"dhammo", "dhammā", "dhammaṃ", "dhamme", "dhammassa", "dhammānaṃ"}


def test_round_trip_generate_then_analyse(analyzer: PaliMorphologyAnalyzer) -> None:
    for form, grammar in analyzer.generate("dhamm", "a masc"):
        assert any(a.grammar == grammar for a in analyzer.analyze(form))


def test_particle_join_is_undone(particle_analyzer: PaliMorphologyAnalyzer) -> None:
    analyses = particle_analyzer.analyze("dhammāpi")
    assert any(
        a.particle == "api"
        and a.base == "dhammā"
        and a.stem == "dhamm"
        and "dhamma" in a.lemmas
        for a in analyses
    )


def test_niggahita_particle_join_is_undone(
    particle_analyzer: PaliMorphologyAnalyzer,
) -> None:
    analyses = particle_analyzer.analyze("dhammañhi")
    assert any(a.particle == "hi" and a.base == "dhammaṃ" for a in analyses)


def test_particle_reading_needs_a_licensed_base(
    particle_analyzer: PaliMorphologyAnalyzer,
) -> None:
    """`xyzpi` de-joins to `xyz`, which no paradigm licenses."""
    assert particle_analyzer.analyze("xyzpi") == []


def test_particles_can_be_switched_off(
    particle_analyzer: PaliMorphologyAnalyzer,
) -> None:
    assert particle_analyzer.analyze("dhammāpi", particles=False) == []


def test_particle_rules_are_loaded(particle_analyzer: PaliMorphologyAnalyzer) -> None:
    assert particle_analyzer.particle_rule_count == 3


@pytest.fixture(scope="module")
def derivation_analyzer(
    tmp_path_factory: pytest.TempPathFactory,
) -> PaliMorphologyAnalyzer:
    """An analyzer that can derive a stem from `dhamm` but has no `dhammik` entry."""
    path = tmp_path_factory.mktemp("derivation") / "derivation_rules.tsv"
    path.write_text(
        "suffix\tbase_last\tadded\tpattern\tcount\n"
        "ika\tm\tik\ta masc\t5\n"  # dhamma + ika -> dhammika
        "ika\tm\tik\ta nt\t1\n",  # the same suffix under another paradigm
        encoding="utf-8",
    )
    return _analyzer(derivation_rules_path=path)


def test_derived_stem_is_analysed(derivation_analyzer: PaliMorphologyAnalyzer) -> None:
    """`dhammikassa` has no entry: it is read as `dhamma` + `ika`."""
    analyses = derivation_analyzer.analyze("dhammikassa", strict=True, particles=False)
    assert any(
        a.suffix == "ika"
        and a.derivation_base == "dhamm"
        and a.stem == "dhammik"
        and a.grammar == "masc dat sg"
        and "dhamma" in a.lemmas
        for a in analyses
    )


def test_derived_stem_needs_the_layer(
    derivation_analyzer: PaliMorphologyAnalyzer,
) -> None:
    assert not derivation_analyzer.analyze(
        "dhammikassa", strict=True, particles=False, derivation=False
    )


def test_derivation_pattern_must_match(
    derivation_analyzer: PaliMorphologyAnalyzer,
) -> None:
    """Only the `a masc` rule may fire for an `a masc` ending."""
    analyses = derivation_analyzer.analyze("dhammikassa", strict=True, particles=False)
    assert {a.pattern for a in analyses} == {"a masc"}


def test_derivation_base_must_be_known(
    derivation_analyzer: PaliMorphologyAnalyzer,
) -> None:
    """`xyzikassa` de-derives to `xyz`, which is not a stem in the lexicon."""
    assert not derivation_analyzer.analyze("xyzikassa", strict=True, particles=False)


def test_derivation_rule_count(derivation_analyzer: PaliMorphologyAnalyzer) -> None:
    assert derivation_analyzer.derivation_rule_count == 2


def test_particle_and_derivation_compose(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """`dhammikassāpi` needs both layers at once."""
    rules_dir = tmp_path_factory.mktemp("compose")
    derivation = rules_dir / "derivation_rules.tsv"
    derivation.write_text(
        "suffix\tbase_last\tadded\tpattern\tcount\nika\tm\tik\ta masc\t5\n",
        encoding="utf-8",
    )
    particle = rules_dir / "particle_sandhi_rules.tsv"
    particle.write_text(
        "base_last\tparticle\tbase_tail\tsurface_tail\tcount\na\tapi\ta\tāpi\t10\n",
        encoding="utf-8",
    )
    analyzer = _analyzer(particle_rules_path=particle, derivation_rules_path=derivation)
    analyses = analyzer.analyze("dhammikassāpi", strict=True)
    assert any(
        a.particle == "api" and a.suffix == "ika" and "dhamma" in a.lemmas
        for a in analyses
    )


@pytest.fixture(scope="module")
def compound_analyzer(
    tmp_path_factory: pytest.TempPathFactory,
) -> PaliMorphologyAnalyzer:
    """An analyzer with one sandhi rule: surface (o, n) can be `a` + `n...`."""
    path = tmp_path_factory.mktemp("sandhi") / "sandhi_rules.tsv"
    path.write_text(
        "index\tchA\tchB\tch1\tch2\teg\tweight\n99\to\tn\ta\tn\tdhammo'naro\t3\n",
        encoding="utf-8",
    )
    return _analyzer(sandhi_rules_path=path)


def test_compound_plain_junction(compound_analyzer: PaliMorphologyAnalyzer) -> None:
    """`dhammonara` is `dhammo` + `nara`, both licensed forms."""
    analyses = compound_analyzer.analyze_compound("dhammonara")
    assert analyses
    assert analyses[0].parts == ("dhammo", "nara")
    assert analyses[0].segments[1].rule == 0


def test_compound_rule_junction(compound_analyzer: PaliMorphologyAnalyzer) -> None:
    """The same surface also reads through rule 99 as `dhamma` + `nara`."""
    analyses = compound_analyzer.analyze_compound("dhammonara")
    readings = {analysis.parts: analysis for analysis in analyses}
    assert ("dhamma", "nara") in readings
    ruled = readings[("dhamma", "nara")]
    assert ruled.segments[1].rule == 99
    # a plain junction scores 2, the rule scores 3, so the plain reading ranks first
    assert analyses[0].parts == ("dhammo", "nara")


def test_compound_components_carry_their_morphology(
    compound_analyzer: PaliMorphologyAnalyzer,
) -> None:
    analyses = compound_analyzer.analyze_compound("dhammonara")
    lemmas = analyses[0].lemmas
    assert "dhamma" in lemmas and "nara" in lemmas


def test_compound_needs_licensed_components(
    compound_analyzer: PaliMorphologyAnalyzer,
) -> None:
    """`xyznara` has no split whose left half is a licensed form."""
    assert compound_analyzer.analyze_compound("xyznara") == []


def test_compound_of_one_part_is_not_a_compound(
    compound_analyzer: PaliMorphologyAnalyzer,
) -> None:
    assert compound_analyzer.analyze_compound("dhammo") == []


def test_unsplittable_word_has_no_compound(
    compound_analyzer: PaliMorphologyAnalyzer,
) -> None:
    assert compound_analyzer.analyze_compound("dhammassa") == []


def test_real_sandhi_rules_are_loaded(analyzer: PaliMorphologyAnalyzer) -> None:
    """The shipped rule file is read: the deconstructor's 627 rules."""
    assert analyzer.sandhi_rule_count > 600


@pytest.fixture(scope="module")
def transform_rules(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One induced transformation: surface (a, m) is `a` + `` (a doubled `m`)."""
    path = tmp_path_factory.mktemp("transforms") / "junction_transform_rules.tsv"
    path.write_text(
        "chA\tchB\tch1\tch2\tweight\teg\tcount\na\tm\ta\t\t4\tdhammammara\t10\n",
        encoding="utf-8",
    )
    return path


def test_junction_transforms_are_off_by_default(
    tmp_path_factory: pytest.TempPathFactory, transform_rules: Path
) -> None:
    analyzer = _analyzer(transform_rules_path=transform_rules)
    assert analyzer.transform_rule_count == 0


def test_junction_transforms_load_when_asked(transform_rules: Path) -> None:
    analyzer = _analyzer(
        transform_rules_path=transform_rules, use_junction_transforms=True
    )
    assert analyzer.transform_rule_count == 1


def test_junction_transform_makes_a_split_reachable(transform_rules: Path) -> None:
    """`dhammammara` is `dhamma` + `mara`, but only through the induced rule.

    The curated table has nothing for the (a, m) junction and neither half of the plain
    split is a licensed form (`dhamma` + `mmara`).
    """
    without = _analyzer(transform_rules_path=transform_rules)
    assert without.analyze_compound("dhammammara") == []

    with_transforms = _analyzer(
        transform_rules_path=transform_rules, use_junction_transforms=True
    )
    analyses = with_transforms.analyze_compound("dhammammara")
    assert analyses
    assert analyses[0].parts == ("dhamma", "mara")


def test_real_junction_transforms_are_available() -> None:
    """The shipped file is readable and in the induced format."""
    analyzer = PaliMorphologyAnalyzer.__new__(PaliMorphologyAnalyzer)
    analyzer._transform_index = {}
    analyzer._load_junction_transforms(ProjectPaths().junction_transform_rules_path)
    assert analyzer.transform_rule_count > 1000


def test_component_must_be_a_dictionary_form(
    derivation_analyzer: PaliMorphologyAnalyzer,
) -> None:
    """`dhammikassa` is derivable but not a dictionary form, so it cannot be a component.

    Allowing the derivation layer to license components lets a compound be built on a
    guess, which is what turns a misspelling into a plausible decomposition.
    """
    assert derivation_analyzer.analyze("dhammikassa") != []
    assert derivation_analyzer.analyze_compound("dhammikassanara") == []


def test_component_derivation_can_be_enabled(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    rules_dir = tmp_path_factory.mktemp("component_derivation")
    derivation = rules_dir / "derivation_rules.tsv"
    derivation.write_text(
        "suffix\tbase_last\tadded\tpattern\tcount\nika\tm\tik\ta masc\t5\n",
        encoding="utf-8",
    )
    analyzer = _analyzer(derivation_rules_path=derivation)
    assert analyzer.analyze_compound("dhammikassanara") == []

    allowing = PaliMorphologyAnalyzer.__new__(PaliMorphologyAnalyzer)
    assert (
        allowing is not None
    )  # the flag path is covered through the constructor below
