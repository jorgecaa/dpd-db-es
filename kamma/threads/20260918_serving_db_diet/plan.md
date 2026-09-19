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

## The call-site inventory (Phase 1's working list, measured 2026-09-19)

Ten entity loads are reachable from a webapp request. Column sets marked *inventory*
need task 1.1 first (they feed templates); the rest are already known.

| # | Site | Loads | Columns needed | Effort |
|---|---|---|---|---|
| 1 | `tools/spanish.py:489` `translate_family_html` | `DpdHeadword` | `lemma_1`, `meaning_1`, `meaning_2`, `meaning_lit`, `pos`, `construction` — **parity proven, 80/80** | trivial |
| 2 | `exporter/webapp/toolkit.py:117` `lookup_rows` probe | `Lookup` | `lookup_key`, `headwords`, `roots`, `deconstructor`, `variant`, `see`, `spelling`, `grammar`, `help`, `abbrev`, `abbrev_other`, `epd` | trivial |
| 3 | `exporter/webapp/toolkit.py:140` `lookup_rows` fallback | `Lookup` | same 12 | trivial |
| 4 | `toolkit.py:188` ficha by ids | `DpdHeadword` | *inventory* — `HeadwordData` + `dpd_headword.html` | hard |
| 5 | `toolkit.py:325` ficha by id (numeric query) | `DpdHeadword` | same as 4 | hard |
| 6 | `toolkit.py:346` ficha by `lemma_1` (`kata 5`) | `DpdHeadword` | same as 4 | hard |
| 7 | `toolkit.py:212` root panel | `DpdRoot` | *inventory* — the roots template | medium |
| 8 | `toolkit.py:217` root families | `FamilyRoot` | `html` (+ `data`? unread by the webapp) | medium |
| 9 | `tools/exporter_functions.py:24,35,53,63,79` family helpers | `FamilyCompound`, `FamilyIdiom`, `FamilySet` | `html` + the key column; `data` is read only by `scripts/build/families_to_json.py` | easy |
| 10 | `tools/cache_load.py:41,54`, `main.py:74` | `DbInfo` | `key`, `value` | skip — 23 rows |

Not in the request path: `tools/spanish.py:549` (`preload_combos`) and `:570`
(`translate_family_rows`) belong to the offline family-JSON build, and
`exporter/webapp/generate_search_index.py` is a build script.

## Phase 1 — make every request-path load column-explicit (enabling work)

- [ ] **1.1** Inventory the attributes of the **displayed headword** (sites 4–6): walk
      `exporter/webapp/templates/dpd_headword.html` and every template it includes,
      plus `HeadwordData` and `make_grammar_line`, and list attribute → template.
      → verify: a table in this file with no unknown rows; each attribute traceable to
      a template line; `pos`/`construction` included if the overlay needs them.
- [ ] **1.2** Site 1: `load_only` the six columns. → verify: 80/80 `_combos` parity
      (already measured), oracle identical, per-page member-load reads recorded
      (baseline 283 KiB cold / 0 warm).
- [ ] **1.3** Sites 2–3: `load_only` the twelve columns. → verify: oracle identical and
      the statement log shows none of `sinhala`/`thai`/`devanagari`/`rpd`/`other`.
- [ ] **1.4** Site 9: family helpers. → verify: oracle identical; `data` no longer in
      the SELECT; `scripts/build/families_to_json.py` untouched and still runs.
- [ ] **1.5** Sites 7–8: `DpdRoot` and `FamilyRoot`. → verify: oracle identical.
- [ ] **1.6** Sites 4–6: the display loads. → verify: oracle identical **and** a
      negative test that accessing an unloaded attribute fails loudly rather than
      silently rendering nothing (the reason for a `raiseload`-style check).
- [ ] **1.7** Re-run Phase 0.3/0.4 on top of 1.1–1.6: rebuild the trimmed copy, serve
      it, run the oracle. → verify: 130/130 identical **and** the saving still
      ≥ ~700 MiB. If it fails, the traceback names the missed site — fix and repeat.
- [ ] **1.8** Gate: `ruff`, `ruff format`, `pyright`, `pytest` (plus `-m slow`),
      `just typecheck`, oracle on the canary **and** on production after deploy.

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
