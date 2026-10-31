"""Unit tests for tools/pali_verdict.py.

The morphological half runs against a synthetic in-memory database; the corpus half is
stubbed, so the verdict logic is tested without the live `dpd.db`.
"""

import importlib.util
import json
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, DpdHeadword, InflectionTemplates, Lookup
from tools.pali_context import PaliContextIndex
from tools.pali_morphology import PaliMorphologyAnalyzer
from tools.pali_verdict import PaliVerdict, Verdict

# The syllable layer comes from `abugida`, an optional library the verdict works without
# (it then says it cannot judge the shape). These tests pin what that layer decides, so
# they only mean something where it is installed.
needs_abugida = pytest.mark.skipif(
    importlib.util.find_spec("abugida") is None,
    reason="needs the optional `abugida` library (the syllable layer)",
)

TEMPLATE = [
    [[""], ["masc sg"], [""], ["masc pl"], [""]],
    [["nom"], ["o"], ["masc nom sg"], ["ā"], ["masc nom pl"]],
    [["acc"], ["aṃ"], ["masc acc sg"], ["e"], ["masc acc pl"]],
    [["voc"], ["a"], ["masc voc sg"], ["ā"], ["masc voc pl"]],
]

# id, lemma_1, stem, pattern, pos, construction — the last is what the morphological
# test reads: `viññāṇa` is `vi + √ñā + aṇa`, so its `ā` and `ṇ` are derived, not spelled.
HEADWORDS = [
    (1, "dhamma 1", "dhamm", "a masc", "masc", "√dham + a"),
    (2, "viññāṇa 1", "viññāṇ", "a masc", "masc", "vi + √ñā + aṇa"),
    (3, "ca", "-", "", "ind", ""),
    # The stem `viññ` is what the junk derivation anchors on (`viññ` + `anā` -> `viññana`),
    # the same shape the live database produces.
    (4, "viññissati", "viññ", "issati fut", "fut", ""),
    # For the gemination bridge: typed `vinnanakhandha`, two letters away.
    (5, "viññāṇakkhandha", "viññāṇakkhandh", "a masc", "masc", "viññāṇa + khandha"),
    # Ends in a consonant, which the syllable layer rejects; the dictionary knows it.
    (6, "etad", "eta", "a masc", "pron", ""),
    # The word the notation guard reads `adukkham` as.
    (7, "adukkha", "adukkh", "a masc", "adj", "na + dukkha"),
    # `suciṃ + dhara`, printed `sucin'dharā` in TH13: the niggahita the dictionary writes
    # `n` and an edition may write `ṃ`. The construction is the live database's own.
    (8, "sucindhara", "sucindhar", "a masc", "adj", "suciṃ + dhara"),
]


@pytest.fixture
def verdict(tmp_path, monkeypatch: pytest.MonkeyPatch) -> PaliVerdict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        InflectionTemplates(pattern="a masc", like="dhamma", data=json.dumps(TEMPLATE))
    )
    for headword_id, lemma_1, stem, pattern, pos, construction in HEADWORDS:
        session.add(
            DpdHeadword(
                id=headword_id,
                lemma_1=lemma_1,
                stem=stem,
                pattern=pattern,
                pos=pos,
                construction=construction,
            )
        )
    # `dhamma` is in the corpus; `viññāṇa` is not, so the two halves can be told apart.
    session.add(Lookup(lookup_key="dhamma", headwords="[1]"))
    # The index carries the English dictionary too: this key is `fear` -> `āsaṅkā`, and
    # finding it must not read as finding `fear` in the Pāḷi corpus.
    session.add(Lookup(lookup_key="fear", epd='[["āsaṅkā", "fem", "doubt"]]'))
    session.add(Lookup(lookup_key="adukkhaṃ", headwords="[7]"))
    # Every citation form is an index key — measured: 89.587 of 89.587 — so this row is what
    # the real database has, and what saves `etad` from the syllable layer.
    session.add(Lookup(lookup_key="etad", headwords="[6]"))
    # `sucindhara` and its two niggahita spellings share the same entry: `saṃghaṃ` and
    # `saṅghaṃ` are both in the index so the candidate choice has to be deterministic.
    session.add(Lookup(lookup_key="sucindhara", headwords="[8]"))
    session.add(Lookup(lookup_key="saṃghaṃ", headwords="[8]"))
    session.add(Lookup(lookup_key="saṅghaṃ", headwords="[8]"))
    # A row the spelling-mistakes sync inserts: nothing but the correction in it. It is the
    # blacklist quoting the word back, not the corpus carrying it.
    session.add(Lookup(lookup_key="pariyāya", spelling='["pariyaya"]'))
    # A row that only records where an edition prints the word: that *is* the corpus.
    session.add(
        Lookup(
            lookup_key="byākarissāmī",
            variant='{"CST": {"DN3": [["tathā byākarissāmī", "( ) etthantare"]]}}',
        )
    )
    # The other direction of the notation rule: `kiṃsambhava` is the dictionary's spelling of
    # `kimsambhava`, and `taṃhā` is in the index so the `h` guard has something to refuse.
    session.add(Lookup(lookup_key="kiṃsambhava", headwords="[8]"))
    session.add(Lookup(lookup_key="taṃhā", headwords="[8]"))
    # `naṃa` is not a spelling the language has, and it is here to prove the medial rule does
    # not manufacture it out of `nama`.
    session.add(Lookup(lookup_key="naṃa", headwords="[8]"))
    session.commit()

    derivation_path = tmp_path / "derivation_rules.tsv"
    derivation_path.write_text(
        "suffix\tbase_last\tadded\tpattern\tcount\nanā\tñ\tan\ta masc\t5\n",
        encoding="utf-8",
    )

    context_path = tmp_path / "context.tsv"
    context_path.write_text(
        "word\tbefore\tsource\tprovenance\nviññāṇa\tevaṃ\texample\tsuttaṃ\n",
        encoding="utf-8",
    )

    extra_path = tmp_path / "extra_wordlist.tsv"
    extra_path.write_text("word\tsource\nsn4only\tpts_sn4\n", encoding="utf-8")
    built = PaliVerdict(
        session,
        extra_wordlist_path=extra_path,
        sources_path=tmp_path / "no_such_sidecar.db",
    )
    built.analyzer = PaliMorphologyAnalyzer(
        session, derivation_rules_path=derivation_path
    )
    built.context = PaliContextIndex(path=context_path)
    return built


def test_an_attested_word_belongs(verdict: PaliVerdict) -> None:
    result = verdict.check("dhamma")
    assert result.is_pali
    assert result.basis == "known"
    assert result.of_kind("attested")


def test_a_licensed_form_of_a_known_word_belongs(verdict: PaliVerdict) -> None:
    result = verdict.check("dhammo")
    assert result.is_pali
    assert result.basis == "known"
    assert "masc nom sg" in str(result.of_kind("inflected")[0])


def test_an_indeclinable_belongs(verdict: PaliVerdict) -> None:
    result = verdict.check("ca")
    assert result.is_pali
    assert result.basis == "known"


def test_a_spelling_that_drops_derived_letters_is_not_a_word(
    verdict: PaliVerdict,
) -> None:
    """`viññana` writes `a` for the root's `ā` and `n` for the suffix's `ṇ`.

    The letters it drops are the ones the derivation supplies (`vi + √ñā + aṇa`), so the
    spelling cannot be built from the root: it is not a variant of a word, it is not a word.
    """
    result = verdict.check("viññana")
    assert not result.is_pali
    assert result.basis == "no_word"
    assert result.corrections == ("viññāṇa",)
    detail = str(result.of_kind("derivation")[0])
    assert "√ñā" in detail and "aṇa" in detail


def test_a_possible_word_is_not_claimed_to_exist(verdict: PaliVerdict) -> None:
    """Pāḷi letters, a Pāḷi shape, and no source that knows it: `possible`, not `known`."""
    result = verdict.check("gagaga", with_compound=False)
    assert not result.is_pali
    assert result.basis == "possible"
    assert result.of_kind("not-in-corpus")
    assert result.of_kind("not-licensed")


@needs_abugida
def test_a_word_with_letters_pali_does_not_have(verdict: PaliVerdict) -> None:
    """`x` and `z` are not in the Pāḷi alphabet, so this is a categorical negative."""
    result = verdict.check("xyzzy", with_compound=False)
    assert not result.is_pali
    assert result.basis == "not_pali"


def test_a_derivation_alone_does_not_make_a_word(verdict: PaliVerdict) -> None:
    """`viññ + anā` licences a shape; the shape is not a word the derivation can build."""
    result = verdict.check("viññana")
    assert result.basis == "no_word"
    assert not result.is_pali
    assert result.of_kind("derived")


def test_a_lost_gemination_is_still_not_a_word(verdict: PaliVerdict) -> None:
    """`viññāṇakkhandha` typed without diacritics: the correction is named, the word is not."""
    result = verdict.check("vinnanakhandha", with_compound=False)
    assert not result.is_pali
    assert result.basis == "no_word"
    assert "viññāṇakkhandha" in result.corrections


def test_the_derivation_reason_names_what_was_lost(
    verdict: PaliVerdict,
) -> None:
    detail = str(verdict.check("viññana", with_compound=False).of_kind("derivation")[0])
    assert "√ñā" in detail and "aṇa" in detail


@needs_abugida
def test_a_difference_the_derivation_does_not_supply_is_a_spelling(
    verdict: PaliVerdict,
) -> None:
    """`dhamma` + a niggahita written `m`: no derivation supplies `ṃ`, so it is notation."""
    result = verdict.check("dhammam", with_compound=False)
    assert result.basis == "variant"
    assert result.is_pali


def test_explain_mentions_the_verdict_and_the_reason(verdict: PaliVerdict) -> None:
    text = verdict.check("dhamma").explain()
    assert "dhamma: Pāḷi — the dictionary analyses it" in text
    assert "corpus index" in text


@needs_abugida
def test_a_word_in_another_script_does_not_belong_to_pali(verdict: PaliVerdict) -> None:
    """A Mon word is not Pāḷi: it does not even tokenise as Pāḷi letters."""
    result = verdict.check("မည်", with_compound=False)
    assert not result.is_pali
    assert result.basis == "not_pali"
    assert result.of_kind("syllables")


def test_a_possible_word_is_not_claimed_to_be_impossible(verdict: PaliVerdict) -> None:
    """A shape Pāḷi could spell, which no source knows: possible, not condemned."""
    text = verdict.check("gagaga", with_compound=False).explain()
    assert "not Pāḷi — nothing has it" in text
    assert "nothing here rules the word out" in text


@needs_abugida
def test_an_impossible_onset_is_not_pali(verdict: PaliVerdict) -> None:
    """`kl` is not a Pāḷi onset: the syllable layer, not the alphabet, catches this."""
    result = verdict.check("kloṇ", with_compound=False)
    assert result.basis == "not_pali"
    assert result.of_kind("syllables")


def test_the_niggahita_of_another_notation_is_read(verdict: PaliVerdict) -> None:
    """`adukkham` ends in a consonant: PTS spelling of `adukkhaṃ`, not a broken word.

    The syllable layer rejects it, and the guard finds the same word in the dictionary's
    notation. `adukkhaṃ` is known here, so the answer is yes.
    """
    result = verdict.check("adukkham", with_compound=False)
    assert result.is_pali
    assert result.basis == "variant"
    assert result.of_kind("notation")


@needs_abugida
def test_another_notation_does_not_earn_a_yes(verdict: PaliVerdict) -> None:
    """A PTS spelling of a word the dictionary does not know either is still not Pāḷi.

    The rejection is withdrawn — the shape is not what is wrong — but nothing has the word,
    so the answer is no, for the reason that nothing has it.
    """
    result = verdict.check("kusalan", with_compound=False)
    assert not result.is_pali
    assert result.basis in {"possible", "not_pali"}
    if result.basis == "possible":
        assert result.of_kind("notation")


def test_a_declared_foreign_notation_is_not_judged(verdict: PaliVerdict) -> None:
    """With the notation declared as another one, the shape is not judged at all."""
    result = verdict.check("mnih", with_compound=False, notation="pts")
    assert result.basis != "not_pali"
    assert "nothing is claimed about the shape" in str(result.of_kind("syllables")[0])


def test_the_dictionary_overrides_a_syllable_rejection(verdict: PaliVerdict) -> None:
    """`etad` ends in a consonant, so the syllable layer rejects it — and it is Pāḷi.

    Whoever knows the word wins over whoever judges the spelling.
    """
    result = verdict.check("etad", with_compound=False)
    assert result.is_pali
    assert result.basis == "known"


def test_an_attested_word_does_not_say_the_corpus_lacks_it(
    verdict: PaliVerdict,
) -> None:
    """A word the index has must not carry a reason saying the index does not have it."""
    result = verdict.check("viññāṇakkhandham", with_compound=False)
    if result.basis == "attested":
        assert not result.of_kind("not-in-corpus")


def test_an_english_key_is_not_corpus_attestation(verdict: PaliVerdict) -> None:
    """`lookup` carries the English dictionary too; finding `fear` there proves nothing."""
    result = verdict.check("fear", with_compound=False)
    assert result.basis != "attested"
    assert not result.of_kind("attested")


def test_a_difference_with_nothing_to_check_it_against_is_not_called_a_variant(
    verdict: PaliVerdict,
) -> None:
    """`cā` against `ca`, a particle with no derivation: the verdict must not decide.

    Naming it a spelling of `ca` would assert what nothing here can support, so it stays
    `possible` and says why it cannot be settled.
    """
    result = verdict.check("cā", with_compound=False)
    assert result.basis == "possible"
    detail = str(result.of_kind("near-spelling")[0])
    assert "ā for a" in detail
    assert "nothing here decides" in detail


def test_the_difference_is_named_letter_by_letter(verdict: PaliVerdict) -> None:
    """`ā for a` says what changed; `differs in diacritics` says nothing."""
    assert verdict._difference("cā", "ca") == "ā for a"
    assert verdict._difference("viññana", "viññāṇa") == "missing āṇ, extra na"


@needs_abugida
def test_a_word_with_impossible_syllables_never_loads_the_analyser(
    verdict: PaliVerdict,
) -> None:
    """The cheap questions come first: `kionl` is answered without the 2 s, 250 MB layer.

    Measured before the order was changed: 10,50 s and 546 MB for this one word. The
    analyser and the 458.665-form folded index were both being built to say no to a string
    whose syllables are not Pāḷi.
    """

    class Exploding:
        def analyze(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the analyser was loaded")

        analyze_compound = analyze

    verdict.analyzer = cast(Any, Exploding())
    result = verdict.check("kionl", with_compound=False)
    assert result.basis == "not_pali"
    assert not result.corrections


def test_a_spelling_correction_row_is_not_corpus_attestation(
    verdict: PaliVerdict,
) -> None:
    """`db/lookup/spelling_mistakes.py` inserts a row for every known misspelling.

    That row carries nothing but `spelling`, so counting it as attestation lets the
    blacklist answer the question it asks. Measured on the live database before the fix:
    518 of the 543 known misspellings came out "Pāḷi — a corpus has it", and 492 of the
    rows behind them held no evidence but the correction itself.
    """
    result = verdict.check("pariyāya", with_compound=False)
    assert not result.of_kind("attested")
    assert result.basis == "possible"


def test_a_variant_row_is_still_the_corpus_carrying_the_word(
    verdict: PaliVerdict,
) -> None:
    """`variant` records where an edition prints the word, so it is evidence."""
    result = verdict.check("byākarissāmī", with_compound=False)
    assert result.is_pali
    assert result.basis == "attested"
    assert result.of_kind("attested")


def test_the_niggahita_of_another_edition_is_the_same_word(
    verdict: PaliVerdict,
) -> None:
    """`suciṃdhara` writes the niggahita the dictionary assimilates: `suciṃ + dhara`.

    The dictionary carries `sucindhara`, so the answer is yes and the spelling it read is
    named — not `possible`, which is what it answered while the syllable layer came first.
    """
    result = verdict.check("suciṃdhara", with_compound=False)
    assert result.is_pali
    assert result.basis == "variant"
    assert result.corrections == ("sucindhara",)
    assert result.of_kind("notation")
    assert verdict.check("sucindhara").basis == "known"


def test_the_notation_reading_is_chosen_deterministically(
    verdict: PaliVerdict,
) -> None:
    """Equal-length candidates, one order: it must not come from the set.

    `sorted(nasal_variants(word), key=len)` left the ties to the set's iteration order, which
    `PYTHONHASHSEED` changes between runs. Both spellings are real words, so both are named.
    """
    assert verdict.check("saṃghañ", with_compound=False).corrections == (
        "saṃghaṃ",
        "saṅghaṃ",
    )


def test_a_class_nasal_written_for_the_niggahita_is_the_same_word(
    verdict: PaliVerdict,
) -> None:
    """`kimsambhava` writes `m` where `s` has no nasal of its own class: it is `kiṃsambhava`.

    The dictionary's spelling is `kiṃsambhava`; the edition wrote the niggahita as a nasal
    consonant. Nothing else could find it: the syllable layer passes the spelling as it
    stands, and the ASCII fold turns `ṃ` into `m` in both words only after the fact.
    """
    result = verdict.check("kimsambhava", with_compound=False)
    assert result.is_pali
    assert result.basis == "variant"
    assert result.corrections == ("kiṃsambhava",)
    assert result.of_kind("notation")


def test_a_licenced_cluster_is_not_rewritten(verdict: PaliVerdict) -> None:
    """`taṇhā` is a real cluster; `taṃhā` sitting in the index must not turn it into one."""
    result = verdict.check("taṇhā", with_compound=False)
    assert not result.is_pali
    assert result.corrections == ()


def test_a_nasal_between_vowels_is_not_the_niggahita(verdict: PaliVerdict) -> None:
    """`nama` has an `m`, not a niggahita, and `naṃa` in the index must not change that.

    The niggahita is written before a consonant or at the end of a word, so generating `ṃ`
    between vowels would let every word with an `m` claim a spelling that cannot exist.
    """
    assert verdict._medial_nasal_readings("nama") == ()


@needs_abugida
def test_the_score_puts_the_six_grounds_on_one_order(verdict: PaliVerdict) -> None:
    """1,00 at the dictionary, 0,00 at the impossible shape, one decimal per ground.

    It is an ordinal score, not a probability (see `Verdict.score`), and it is a function of
    the basis alone — the same number whichever path answered.
    """
    grounds = ["known", "attested", "variant", "possible", "no_word", "not_pali"]
    scores = [Verdict(word="x", is_pali=True, basis=basis).score for basis in grounds]
    assert scores == [1.00, 0.90, 0.75, 0.50, 0.25, 0.00]
    assert scores == sorted(scores, reverse=True)

    assert verdict.check("dhamma").score == 1.00
    assert verdict.check("suciṃdhara", with_compound=False).score == 0.75
    assert verdict.check("gagaga", with_compound=False).score == 0.50
    assert verdict.check("viññana").score == 0.25
    assert verdict.check("kionl").score == 0.00


def test_the_score_leads_the_explanation(verdict: PaliVerdict) -> None:
    """The decimal comes first, so a page of answers can be read down the column."""
    assert verdict.check("dhamma").explain().startswith("[1.00] dhamma: Pāḷi")
