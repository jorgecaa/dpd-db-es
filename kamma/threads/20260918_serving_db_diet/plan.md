# Plan: Serving-db diet

**Thread:** 20260918_serving_db_diet
**Spec:** `spec.md` in this folder — read it first, including the addendum with the
per-page attribution and the corrected parameter-limit finding.
**Status:** Phase 0 done. Phases 1–4 are ready to execute and **not started**; the
order below is by value per unit of risk, not by size of the prize.

## Why the order is what it is

- The 710 MiB trim is real, but it cannot be taken until **every entity load in the
  request path selects explicit columns**: dropping 11 unread columns and serving from
  the result broke **125 of 130** oracle responses (spec, "The measurement that blocks
  the obvious plan").
- Of the two call sites analysed in depth, only `translate_family_html` has any I/O
  value (~2 % of a page's reads); `lookup_rows` has none. Phase 1 is therefore
  **enabling** work: it costs ~10 small edits and buys the ability to shrink the db.
- The elephant — 13.9 MiB of stored family HTML read per page, 99 % of a page's read
  traffic — is the only lever big enough to matter. It is a bigger, template-shaped
  change, so it is Phase 3: after the cheap work has shown the oracle can police
  equivalence.

## Execution rules

1. The 130-request golden oracle (`kamma/threads/20260918_webapp_memory/golden.json`)
   gates every step. A changed response is a stop, not a judgement call.
2. All experiments on throwaway copies under `/tmp` (tmpfs, 16 GB); the canonical
   `dpd.db` is only ever opened read-only.
3. `db/models.py`'s contract for other consumers does not change, and neither does the
   canonical db. The diet is an extra serving artifact.
4. One mechanism per commit, each carrying its numbers, per `AGENTS.md`.
5. Every `load_only` uses the columns the templates actually read — no guessing: task
   1.1 produces the list, and the oracle plus a loud-failure test keep it honest.

## Phase 0 — Reconnaissance and the throwaway benchmark (DONE 2026-09-18)

- [x] **0.1** Size baseline per table and per column from the live db.
- [x] **0.2** Runtime usage audit (`audit_columns.py`) over 30 mixed requests,
      cross-checked against a static grep of the webapp's code and templates.
- [x] **0.3** Throwaway-copy benchmark (`build_trimmed_copy.py`): copy to `/tmp`, drop
      the 11 never-referenced columns, `VACUUM`. → **2180.7 → 1470.1 MiB, 710.6 MiB
      saved (32.6 %), 67 s of build time.**
- [x] **0.4** Serve the trimmed copy and run the oracle — the step that decides the
      shape of the work. → **125 of 130 responses became `Internal Server Error`**:
      `no such column: lookup.rpd`, because SQLAlchemy selects every column the model
      declares. Recorded in the spec as the finding that blocks the naive plan.
- [x] **0.5** Per-page attribution of the two call sites, plus the parameter-limit
      check. → member loads 283 KiB cold / 0 warm; family HTML 13,865 KiB (99 % of the
      page); SQLite ceiling here is 32,766, so the batching shipped as a portability
      guard, not a bug fix (spec addendum).

## The call-site inventory (Phase 1's working list, measured 2026-09-19)

Ten entity loads are reachable from a webapp request. Column sets marked *inventory*
need task 1.1 first (they feed templates); the rest are already known.

| # | Site | Loads | Columns needed | Effort |
|---|---|---|---|---|
| 1 | `tools/spanish.py` `translate_family_html` | `DpdHeadword` | **12 columns** (measured, not guessed): `lemma_1`, `_meaning_1`, `_meaning_2`, `_meaning_lit`, `pos`, `construction`, `origin`, `root_key`, `root_base`, `root_sign`, `family_root`, `family_word` — **done, 80/80 parity** | done |
| 2 | `exporter/webapp/toolkit.py` `lookup_rows` probe | `Lookup` | the five unread are `defer`red instead: `sinhala`, `thai`, `devanagari`, `rpd`, `other` | done |
| 3 | `toolkit.py` `lookup_rows` fallback | `Lookup` | same | done |
| 4 | `toolkit.py` ficha by ids | `DpdHeadword` | the eight droppable are `defer`red; everything else loads | done |
| 5 | `toolkit.py` ficha by id (numeric) | `DpdHeadword` | same | done |
| 6 | `toolkit.py` ficha by `lemma_1` | `DpdHeadword` | same | done |
| 7 | `toolkit.py` root panel | `DpdRoot` | not needed: `dpd_roots` columns are not in the drop list | skipped |
| 8 | `toolkit.py` root families | `FamilyRoot` | not needed, same reason | skipped |
| 9 | `tools/exporter_functions.py` family helpers | `FamilyCompound`, `FamilyIdiom`, `FamilySet` | not needed, same reason (`data` stays: `families_to_json.py` reads it) | skipped |
| 10 | `tools/cache_load.py`, `main.py` | `DbInfo` | not needed (23 rows) | skipped |

Not in the request path: `tools/spanish.py:549` (`preload_combos`) and `:570`
(`translate_family_rows`) belong to the offline family-JSON build, and
`exporter/webapp/generate_search_index.py` is a build script.

## Phase 1 — make every request-path load column-explicit (enabling work)

- [x] **1.1** Inventory the attributes of the **displayed headword** (sites 4–6).
      **DONE 2026-09-19, in the form that actually matters.** The template reads ~70
      attributes and each is a property chain, so instead of hand-listing a minimal set
      the display loads `defer` the eight columns a serving build drops and load
      everything else: the list that has to be right is the *dropped* one, and it is
      eight names long. `tests/exporter/webapp/test_serving_columns.py` renders one page
      per display path (lookup hit, numeric id, numbered lemma) against the live db and
      fails if any dropped column reappears in a SELECT **or** if a deferred attribute is
      read one row at a time. Both checks pass on all three paths.
- [x] **1.2** Site 1: `load_only` the family-member load. **DONE 2026-09-19 — with
      twelve columns, not six.** Two traps, both caught by measuring:
      `meaning_1`/`meaning_2`/`meaning_lit` are **hybrid properties** and `load_only`
      ignores those silently (the real column stays deferred → one lazy SELECT per
      attribute per row), and the overlay's fingerprint reaches
      `construction_summary` → `summarize_construction`, which also reads `origin`,
      `root_key`, `root_base`, `root_sign`, `family_root` and `family_word`. With the
      correct set: **1 statement instead of 167**, per 165-member table **4,832 →
      1,364 KiB (3.5×) and 15.0 → 8.8 ms**, per cold page **283 → 149 KiB**, `_combos`
      identical over 80/80 family tables.
      *Superseded claim:* an earlier "388 KiB, 12.6×" reading was an artifact of the
      incomplete set — those bytes were the lazy loads themselves.
- [x] **1.3** Sites 2–3: `defer` the five unread `Lookup` columns. **DONE 2026-09-19**
      (deferred rather than `load_only`: the row is 0.3 KiB, so there is nothing to win
      on bytes — the point is that a trimmed db can drop them). Locked by
      `test_a_hit_goes_through_the_index`.
- [x] **1.4** Site 9: family helpers. **NOT NEEDED for the diet** — `family_*` columns
      are not in the drop list, so nothing blocks them. Recorded rather than done.
- [x] **1.5** Sites 7–8: `DpdRoot` and `FamilyRoot`. **NOT NEEDED**, same reason; their
      columns are not droppable either.
- [x] **1.6** Sites 4–6: the display loads. **DONE 2026-09-19** via `defer` of the eight
      columns (`_DEFER_HEADWORD` in `toolkit.py`). Verified on all three paths: no
      deferred column in any SELECT, no lazy loads. The `raiseload`-style check the task
      asked for was replaced by the statement log, which catches the same regression
      without risking a hard failure on a column the template legitimately reads.
- [x] **1.7** Rebuild the trimmed copy and serve it. **DONE 2026-09-19 — it passes.**
      `build_trimmed_copy.py` → 2180.7 → **1470.1 MiB (710.6 MiB, 32.6 %) in 58 s**;
      served from `/tmp/dpdtrim` on port 8083 the golden oracle is **130/130
      byte-identical** (`aa63ecff…`), where before Phase 1 the same db returned 125
      `Internal Server Error`s. The 710 MiB is now claimable.
- [x] **1.8** Gate: `ruff check` clean · `ruff format` applied · `pyright` **0 errors**
      on the five touched files · `pytest tests/exporter/webapp
      tests/tools/test_spanish_combos.py`: **91 passed**, 7 deselected · `-m slow`: **7
      passed** · `just typecheck`: same 90 pre-existing errors, none in these files ·
      oracle identical on the canary **and** on production after deploy (startup 4 s).

## Phase 2 — a serving-db build target (the payoff of Phase 1)

- [ ] **2.1** Build step producing `dpd-serve.db`: copy, drop the proven-unread
      columns, `VACUUM`, keep `db_info.dpd_release_version`.
      → verify: deterministic (same size on two runs), `integrity_check` ok, version
      readable, build time recorded (baseline: 67 s for 710 MiB).
- [ ] **2.2** Point the webapp at it and validate. → verify: oracle identical; the
      1,000-request soak's memory curve unchanged; page-cache working set reported.
- [ ] **2.3** Decide whether it ships (spec D2) and record the decision with numbers.

## Phase 3 — the elephant: family tables rendered on demand

Baseline measured: a `dhamma` page reads **13,865 KiB** in 77 family-`html` queries to
serve 2.3 MB of JSON; everything else on the page is ~0.4 MiB.

- [ ] **3.1** Prototype: render one family table from its family row plus
      `load_only` headwords, and diff it against the stored `html` for a sample of
      families. → verify: differences listed and classified (whitespace/ordering vs
      content); content differences are a stop.
- [ ] **3.2** If (and only if) 3.1 is content-equivalent, switch the webapp's family
      rendering to the on-demand path and keep the stored `html` for the offline
      exporters (which have no page-cache problem).
      → verify: oracle identical; page reads recorded (expect ~0.4 MiB instead of
      13.9 MiB); latency not worse.
- [ ] **3.3** Trigger check before doing 3.2: only if page-cache pressure matters —
      anonymous memory is already 124 MiB idle, and the page cache is reclaimable.

## Phase 4 — `bold_definitions` as its own file (181 MB)

- [ ] **4.1** Move the table to `bold_definitions.db` with its own session, and
      validate `/bd_search` against the oracle's BD queries.
      → verify: BD responses byte-identical; main db ~181 MB smaller.

## Definition of done (applies to whichever phases are executed)

- [ ] Oracle byte-identical at every step, including served from the trimmed db.
- [ ] Size, read-volume and RAM numbers recorded here before and after each phase.
- [ ] Canonical `dpd.db` and `db/models.py`'s contract untouched; other consumers'
      needs (e.g. `families_to_json.py` reading `data`) still satisfied.
- [ ] Reviewed in a fresh session; committed in small commits when the user asks.

## Effort and sequencing

| Phase | Depends on | Effort | Prize |
|---|---|---|---|
| 1 | — | 3–5 h | enables the diet; ~2 % page reads on its own |
| 2 | 1 | 1–2 h | 710 MiB off the serving db |
| 3 | 1 (partly) | 1–2 days | 13.5 MiB less read per page |
| 4 | — | 2–3 h | 181 MB off the serving db |

## Open questions (spec D1–D3)

- **D1** Execute Phase 1 now (recommended if the diet is to happen at all), or keep the
  whole thread parked?
- **D2** `dpd-serve.db` as a release asset, or a local build target only?
- **D3** Phase 3 (on-demand family tables) is the biggest prize and the biggest change:
  schedule it, or leave it behind a page-cache-pressure trigger?
