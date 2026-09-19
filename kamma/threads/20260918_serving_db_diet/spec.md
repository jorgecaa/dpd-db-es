# Spec: Serving-db diet — what the data side could give up, and what it costs

**Thread:** 20260918_serving_db_diet (Phase 5 of `20260918_webapp_memory`, deliberately separate)
**Date:** 2026-09-18
**Status:** Reconnaissance complete. **Recommendation: park it** — the trigger has not
fired and the obvious version of the diet is blocked by a measured fact (below).
**Artifacts:** `audit_columns.py` (runtime + static usage audit),
`build_trimmed_copy.py` (the throwaway-copy benchmark `AGENTS.md` demands).

## Why this thread exists at all

`dpd.db` is 2.13 GiB to serve 89,280 headwords. Most of it is materialised derived
data: 659 MB of `freq_html` + 251 MB of `inflections_html` (rendered HTML stored per
row), ~190 MB of transliterations duplicated between `dpd_headwords` and `lookup`,
419 MB of `lookup` for 1.28 M inflected forms and 181 MB of `bold_definitions` — while
the human-written English text (`meaning_1`, `example_1`, `commentary`, …) is only
~20-25 MB. The release compresses that to 161 MiB (~13.5:1), which is the tell.

## Trigger analysis (the gate the parent plan set)

The parent plan made this conditional on: the app not fitting the host's budget, the
release build being reworked anyway, or the webapp needing ≤1 GB.

**None has happened.** After Phases 1–3 the service runs at **124 MiB anonymous
idle**, **239 MiB at steady state**, **4 s** start-to-serving, inside a 260 MiB
budget and a 1 GiB cgroup guardrail. The diet would buy disk and page-cache
pressure, not the ability to run somewhere it currently cannot.

## Measured baseline

| Table | On disk | Notes |
|---|---|---|
| `dpd_headwords` | 1501 MB | 68 % of the db; `freq_html` 659 MB + `inflections_html` 251 MB are stored HTML |
| `lookup` | 419 MB + 36 MB index | 1,281,569 rows; `deconstructor` 54 MB, transliterations 85 MB |
| `bold_definitions` | 181 MB | 369,147 rows, only read by the `/bd_search` tab |
| `family_*` | ~35 MB | `html` (rendered) + `data` |
| everything else | ~10 MB | roots, sutta_info, templates, db_info |

**Runtime usage audit** (`audit_columns.py`, 30 requests through a TestClient with a
SQLAlchemy statement listener). The naive reading is "the webapp reads everything",
because a *whole-row* load selects every declared column. The static cross-check
(grep over `exporter/webapp/**` and the templates) separates what is genuinely used
from what is merely selected:

- never referenced by the webapp: `dpd_headwords.freq_data` (59 MB),
  `inflections_sinhala`/`inflections_thai`/`inflections_devanagari` (105 MB),
  `inflections` (23 MB), `inflections_api_ca_eva_iti` (3 MB), `created_at`/`updated_at`
  (3 MB), `lookup.sinhala`/`thai`/`devanagari` (85 MB), `lookup.rpd` (0 MB)
- genuinely used and *not* droppable: `freq_html`, `inflections_html`, `sanskrit`,
  the meaning/example/grammar columns, `lookup.lookup_key`/`headwords`/`epd`/
  `variant`/`grammar`/`deconstructor`/`abbrev*`

## The measurement that blocks the obvious plan

`build_trimmed_copy.py` copied the db to `/tmp` (tmpfs, so no pressure on a 94 %-full
`/home`), dropped those 11 never-referenced columns and ran `VACUUM`:

```
copied     2180.7 MiB -> /tmp/dpd_trim.db      (2.2 s)
11 columns dropped                             (45 s)
after VACUUM: 1470.1 MiB                       (23 s)
saved 710.6 MiB of 2180.7 MiB (32.6 %) in 67.0 s total
```

710 MiB for free — until the app is pointed at it. Served from the trimmed copy on
port 8083, the 130-request golden oracle reported **125 of 130 responses changed, all
to 21 bytes** (`Internal Server Error`). The traceback is unambiguous:

```
sqlite3.OperationalError: no such column: lookup.rpd
[SQL: SELECT lookup.lookup_key AS …, lookup.rpd AS lookup_rpd, … FROM lookup WHERE …]
```

**SQLAlchemy selects every column the model declares, whether or not any code reads
it.** `lookup_rows` (my own Phase 2 code) does `query(Lookup)`; `dpd_headwords`
entity loads do the same. So a column cannot be dropped from the db while the ORM
model still declares it — the "free" 32.6 % is not free at all.

The same run doubles as a negative control for the oracle: it is sensitive enough to
catch a db that serves *nothing*, and it was the reason the trim was caught in
minutes rather than in production.

**The block is cleared (2026-09-19).** After Phase 1 — `defer`ring the eight
`dpd_headwords` and five `lookup` columns nothing reads, at every request-path entity
load — the same trimmed copy (1470.1 MiB) serves the golden oracle **130/130
byte-identical**. The 710 MiB is claimable; the remaining work is turning that into a
build target (plan Phase 2).

## What the diet would actually require

| Option | What it costs | Verdict |
|---|---|---|
| **A. `load_only` in the webapp** — every entity load in `exporter/webapp/` selects explicit columns, then the serving build drops the rest | a code change in ~6 query sites, validated by the oracle; fails loudly (not silently) if an unloaded column is touched | **the only sound route**; also cuts per-request reads on its own |
| **B. Serving-only models** (`ServeHeadword`, `ServeLookup`) mapped to the same tables with fewer columns | a parallel model set to keep in step with `db/models.py`, and a model swap in every webapp call path | sound but heavy; only worth it if A proves unmanageable |
| **C. `deferred=True` on the columns** | affects the *whole* project: every exporter and gui2 path would lazy-load those columns | **rejected** — it taxes consumers that need the data |

Option A is the only sound route, but see the addendum below for what it is actually
worth in speed terms.

## Addendum 2026-09-18 — where a word page's reads actually go

Attribution of a full `dhamma` render (statement listener, `rchar` per statement,
in-process), splitting the two call sites this thread cares about from the rest:

| Slice | Cold (memo empty) | Warm (second render) |
|---|---|---|
| `_combos` member loads (`WHERE dpd_headwords.lemma_1 IN …`) | 8 queries, **283 KiB**, 4.9 ms | **0 queries, 0 KiB** (memo hit) |
| family-table `html` blobs | 77 queries, **13,865 KiB**, 15.8 ms | 69 queries, 13,574 KiB |
| lookup probe | 1 query, 36 KiB | 1 query, 36 KiB |
| everything else | 6 queries, 157 KiB | 2 queries, 29 KiB |
| **page total** | **14,059 KiB**, 894 ms | 13,639 KiB, 106 ms |

Three conclusions that correct the sketch this thread started from:

1. **The `_combos` member load: fixed, and smaller than the first measurement
   suggested.** 283 KiB cold, **zero warm**, 4.9 ms. The fix needs **twelve** columns,
   not six — `load_only` silently ignores hybrid properties (`meaning_1`/`_meaning_2`/
   `meaning_lit`), and the overlay's fingerprint reaches `summarize_construction`, which
   also reads `origin`, `root_key`, `root_base`, `root_sign`, `family_root` and
   `family_word`. Measured with the correct set: **1 statement instead of 167**, per
   165-member table **4,832 → 1,364 KiB (3.5×) and 15.0 → 8.8 ms**, per cold page
   **283 → 149 KiB**, `_combos` identical over 80/80 family tables. An earlier reading
   of "388 KiB, 12.6×" was an artifact of the incomplete set: those bytes *were* the
   lazy loads. It is still only ~1 % of a page's reads; its value was unblocking the
   diet, and that is now done.
2. **`lookup_rows` has no measurable cost**: 2 rows, 64 KiB with the entity load
   against 60 KiB with `load_only` — one page either way, and `load_only` measured
   *slower* (1.16 ms vs 0.97 ms). It is a diet blocker only, never a hot spot.
3. **The elephant is the stored family HTML: 13.9 MiB read to render one page**, 99 %
   of its read traffic (`dhamma`'s JSON response is 2.3 MB). The 910 MB of stored HTML
   in the baseline is not only `freq_html`/`inflections_html` — the `family_*` tables'
   rendered member tables are a large part of it, and the webapp reads them in full on
   every page. Rendering those tables per request from the family rows plus
   `load_only` headwords would be ~0.4 MiB instead of 13.9 MiB, and is the only lever
   here big enough to matter.

**Parameter limit — claim corrected by measurement.** `family_set` HTMLs list up to
**882 distinct lemmas** (mean 41; `family_compound` up to 576, `family_root` 419,
`family_idiom` 261) and `translate_family_html` used to put all of them into one
`IN (…)`. This was first written up here as a latent failure close to SQLite's 999
variable ceiling. **Measured 2026-09-18, that ceiling does not apply to this build:**
sqlite 3.51.2 reports `MAX_VARIABLE_NUMBER=32766`, 5,000 parameters work and 40,000
fail — so 882 lemmas are **2.7 % of the real ceiling**, not 88 % of it. There was no
live bug. The lookup is now batched at 500 anyway (`tools/spanish.py::_IN_CHUNK`,
tests in `tests/tools/test_spanish_combos.py`): it costs three lines, is
behaviour-preserving, and covers the older builds where the ceiling *is* 999 and 882
lemmas would have been one family away from breaking.

## Non-goals

- Never changing the canonical `dpd.db`, `db/models.py`'s contract, or any exporter
  that consumes the full columns — the diet is an *extra* serving artifact.
- Not touching `dpd_es.db` or the overlay's semantics.
- Not re-doing Phases 1–3: startup and request-path memory are already solved.

## Recommendation

**Park it, with the design recorded.** The trigger has not fired, the app is inside
its budget, and the one version of the diet that looked free is blocked by the ORM's
whole-row behaviour. If the trigger ever fires, the order is: (1) `load_only` in the
webapp with the oracle as the gate, (2) a serving-db build target that drops what is
then provably unloaded, (3) `bold_definitions` as its own file. Phase 1 alone is
worth doing on its own merits if per-request read volume ever matters.

## Decisions needed from the user

- **D1** Park the thread (recommended) or execute Phase 1 (`load_only`) now on its own merits?
- **D2** If a serving db is ever built: a separate release asset, or only a local build target?
- **D3** Should the 710 MiB trim be re-tested after Phase 1 lands, to confirm the same columns become droppable?
