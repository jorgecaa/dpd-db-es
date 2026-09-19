# Tech Notes

## Tools & Platforms
This project is primarily a Python 3.13 codebase managed with `uv`. It uses SQLite as the main database and SQLAlchemy for database access and modeling. It also includes Go modules for performance-sensitive tasks, Flet for the contributor GUI, FastAPI and Uvicorn for web-facing services, and multiple exporter pipelines for web, mobile, desktop dictionary, and document outputs. Some gui2 AI workflows now also use locally authenticated CLI wrappers such as `codex` and `claude` through the shared AI manager. The `exporter/wxt_extension/` cross-browser extension (TypeScript, WXT) has its own Vitest + happy-dom unit suite (`npm test`), separate from the Python `pytest` suite.

## Who This Is For
These notes are for internal contributors and editors maintaining dictionary data, the database build process, tests, and release exports.

## Constraints
- Work needs to fit the existing Python 3.13, `uv`, SQLite, and SQLAlchemy stack.
- GUI-based data entry, database generation, testing, and exporter flows all need to stay reliable for contributors.
- Project conventions require modern type hints, `pathlib.Path` for file paths, and avoiding `sys.path` import hacks.
- The repository already contains multiple output targets, so changes should preserve compatibility with downstream exporters and integrations.
- This is an existing Git-managed project, so setup should work with the current repository structure and conventions rather than replacing them.
- `db/db_helpers.get_db_session()` reuses one cached Engine per db path (NullPool, fork-PID guard); gui2 derives all full-table headword data from `DatabaseManager.load_corpus()` and marks it stale via `mark_corpus_stale()` after any headword write — don't reintroduce per-call engines or direct `.all()` loads of `DpdHeadword` in gui2.
- Flet 0.28.3 (pinned, `flet[all]==0.28.3`) exposes no cursor-position/selection API on `ft.TextField` — putting the caret at the end of a pre-filled field relies on calling `.focus()` after the value is set, not a real cursor setter. There are also no per-dialog keyboard events: dialog-scoped keys (e.g. Enter = submit) must hook `page.on_keyboard_event` with a save/restore wrapper (see the EG popup in `gui2/pass2_add_view.py`).
- Anki family decks (Family Root, Family Word, Family Compound, Root Matrix) share `exporter/anki/anki_updater.py::make_new_family_note`, but Root Matrix needs its own Anki note type ("DPD Root Matrix") for its `table.root_matrix` CSS — note type selection is keyed off deck name via `family_note_type()`. Any new family deck needing dedicated styling must be added to `FAMILY_DECK_NOTE_TYPES`, and its templates/CSS added to `exporter/anki/templates/` + `template_pusher.py::NOTE_TYPE_FILES` so the note type stays backed up and re-pushable.
- Two type checkers run, with different jobs. **pyright** is the per-file commit gate (`.pre-commit-config.yaml`, `basic` mode, config in `[tool.pyright]`). **pyrefly** is the repo-wide sweep — `just typecheck`, enforced by `.github/workflows/typecheck.yml` on push to `main` and every PR, config in `[tool.pyrefly]`. The hook only ever sees staged files, so pyrefly is what catches a change that breaks a caller elsewhere. pyright wins on disagreement (97.8% vs 87.8% typing-spec conformance); treat a pyrefly-only complaint as a checker limitation and suppress it narrowly with `# pyrefly: ignore` plus a reason. A pyrefly fix is not finished until `uv run pyright <file>` is also clean — satisfying one regularly breaks the other. Added 2026-07-27.
- `tools/proofreader.py` and gui2's `ProofreaderManager` (in `gui2/toolkit.py`) both read/write `tools/proofreader.tsv`. Any file touched by both a standalone script and gui2 needs the same pattern: a `filelock.FileLock` guarding a reload-fresh-from-disk-then-mutate-then-save sequence — a lock alone isn't enough, since holding a stale in-memory copy across the lock still loses updates.
- The webapp's startup preloads must query **columns, not full-entity rows**. `exporter/webapp/preloads.py` used to load all 89,280 `DpdHeadword` entities twice just to derive one string per row (`lemma_clean` from `lemma_1`), and that alone was ~541 MiB of anonymous RSS the allocator never returned to the kernel. Rule: never reintroduce a full-entity `DpdHeadword` load under `exporter/webapp/` — select the columns you need (`db.query(DpdHeadword.lemma_1)`), and keep every in-process cache bounded and listed in the inventory below. Guarded by memory-budget and statement-shape tests under `tests/exporter/webapp/`. Added 2026-09-18.

## Webapp Serving Memory (2026-09-18)

**Host change.** Until 2026-09-18 `dpd-webapp.service` ran `deploy/preload-db.sh`
as `ExecStartPre`: it copied the 2.13 GiB `dpd.db` to `/dev/shm/dpd.db` (~37 s)
and switched `dpd.db` to a symlink into RAM — 2.13 GiB of memory the kernel could
not reclaim. The script is now a documented no-op (the original is kept as
`deploy/preload-db.sh.orig`; revert = one `mv` + restart) and the service serves
from disk, backed by the kernel page cache. Measured effect: cgroup current
**3.52 GiB → 544 MiB**, start-to-serving **~56 s → 10 s**. The systemd unit still
calls the no-op script because no root was available to edit it; the exact
cleanup is a task in `kamma/threads/20260918_webapp_memory/plan.md` (Phase 4).

**Baseline after the changes** (1 uvicorn worker, `dpd.db` v0.4.20260728):

| Metric | Before | After Phase 1 | After Phase 2 (2026-09-18) |
|---|---|---|---|
| cgroup `memory.current` / `memory.peak` | 544 MiB / 669 MiB | 136 / 143 MiB | 136 / 143 MiB |
| `VmRSS` (`RssAnon` / `RssFile`) | 556 MiB (532 / 24) | 148 MiB (124 / 24) | **148 MiB (124 / 24)** |
| `/status` app memory, idle | 555.89 MB | 148.07 MB | **148.23 MB** |
| start → serving | 10 s | 3 s | **4 s** (variants map warm-up) |
| `/search_json` median | ~536 ms | ~536 ms | **9.6–15.3 ms** |
| pages read per search (hit) | 419 MB | 419 MB | **0.03 MiB** |
| `/bd_search` churn per 50 requests | +12.3 MiB | +12.3 MiB | **0.0 MiB** |

**Attribution — why it used to cost that much.** `exporter/webapp/preloads.py`
loaded all 89,280 `DpdHeadword` **entities** twice at startup to derive one string
per row. Measured in a separate process with the same code path: 61 MiB imports →
86 MiB with a single-column query → **635 MiB with entities** → **73 MiB after
`del` + `malloc_trim(0)`**. The fix (Phase 1, same day) replaced both passes with
column-only queries plus a `clean_lemma` helper that mirrors
`DpdHeadword.lemma_clean`; measured harness footprint **290.1 → 82.7 MiB**, preload
wall clock **8.8 → 1.8 s**, and the 130-request golden oracle stayed byte-identical
(`aa63ecff…`). Full evidence: `kamma/threads/20260918_webapp_memory/`.

**The search cost was an unindexed scan, not wide columns.** `Lookup.lookup_key.ilike(q)`
cannot use an index, so SQLite scanned all 1,281,569 `lookup` rows — **419 MB of
pages and 519.8 ms per search** to return one or two rows. `exporter/webapp/toolkit.py`
now probes the primary-key index (`probe_keys` + `lookup_rows`), using the exact
stored spellings of the 181 keys that have more than one case form, and keeps the old
predicate only as a safety net that reads the covering index (36 MB). 18 of 18
predicate-parity cases identical, 35× faster end to end.

**Under load it fills, then it is flat.** Soak of 1,000 mixed requests on a fresh
instance: anon goes 222 → **238.8 MiB** while the caches fill (first ~400 requests,
+7.5 %) and then **0.0 % drift** for the remaining 600, with `_combos` capped at
20,000 lemmas. Zero non-200 responses, 10.3 ms median per request.

**Budgets — ratchet only downwards, never raise one silently.**
- Anonymous RSS per worker: **≤200 MiB idle** (currently 124 MiB; the harness test
  asserts the ceiling and a revert to whole-row loads measures 290 MiB and fails it)
  and **≤260 MiB at steady state** under diverse traffic (currently 238.8 MiB).
- Start → first served request: **≤10 s** — currently 4 s.
- `/search_json` median: **≤50 ms** — currently 10-15 ms; the miss path (covering
  scan + difflib) stays ~370-500 ms and is the next candidate if it ever matters.
- Every new in-process cache must be bounded and added to the inventory below
  before it lands.
- **Unit guardrail (decided 2026-09-18, install pending root):**
  `MemoryHigh=512M` / `MemoryMax=1G` on `dpd-webapp.service` — ~2x the measured
  steady state (239 MiB) to throttle, ~4x to stop a runaway. The cleaned unit is
  `deploy/dpd-webapp.service`; it also drops the now-inert `/dev/shm` preload
  plumbing. If the webapp ever runs several workers, re-derive these numbers and
  this budget first.

**Where the numbers are published (2026-09-18, Phase 3).** `exporter/webapp/memory.py`
is the single owner of the facts, the budget and the health rule; `/status` renders
them and `/metrics` exposes `dpd_webapp_anon_mib`, `_rss_mib`, `_file_mib`,
`_cgroup_current_mib`, `_cgroup_peak_mib`, `_memory_budget_mib`,
`_memory_percent_of_budget` and `dpd_webapp_cache_entries{cache="…"}` per cache. The
budget is the cgroup `memory.max` when a limit exists, else the documented
steady-state budget above. **Deliberate threshold change:** health is now
budget-relative (green inside it, orange to 1.5x, red beyond) instead of a hardcoded
70 %/90 % of 4 GB, which no longer described anything; the response-time thresholds
(300 ms orange, 600 ms red) are unchanged. Publishing costs 0.39 ms per request
(measured in-process; indistinguishable end-to-end), and `prometheus-client` is now a
declared dependency because the instrumentator re-exports `Counter`, `Histogram` and
`Summary` but not `Gauge`.

**Cache inventory** (name → key → bound → carrier → eviction → notes):

| Cache | Key | Bound | Carrier | Eviction / notes |
|---|---|---|---|---|
| `roots_count_dict` | `root_key` | 1 (startup) | `exporter/webapp/main.py` | SQL aggregation, not entities |
| `headwords_clean_set` | — | 1 (startup) | idem | 154,690 strings, column-only query |
| `ascii_to_unicode_dict` | ascii form | 1 (startup) | idem | 74,749 keys |
| `lookup_key_variants` | folded key | **181 keys** | `exporter/webapp/toolkit.py` | `lru_cache(1)` per db path; warm at startup; drives the indexed search probe |
| `history_list` | (q1, q2, option) | **250** ✓ | `exporter/webapp/main.py` | slice on insert |
| `_engine_cache` | resolved db path | 1 per path | `db/db_helpers.py` | NullPool engines |
| `load_sutta_info_set` | — | 1 (`lru_cache`) | `tools/cache_load.py` | process-lifetime |
| `load_cf_set`, `load_idioms_set` | — | 1 each | idem | from `DbInfo` JSON |
| `load_audio_set`, `load_audio_dict` | lemma | 1 each | idem | **two copies of the same 75,591 keys** (candidate for Phase 5); `_read_audio_index()` uncached, runs once per loader |
| `load_tpr_codes_set` | — | 1 | idem | JSON file |
| sidecar `_translations`, `_roots`, `_sanskrit`, `_notes`, `_sutta_summaries`, `_ui` | lemma / English text | 1 each | `tools/spanish.py` | process-lifetime, from `dpd_es.db` (86,002 meaning rows) |
| `_combos` | lemma | **20,000** ✓ | idem | oldest-first trim **after** the substitution; capped in Phase 2 (was unbounded) |
| `_translate_inflection_html` | inflection HTML | **512** ✓ | idem | `lru_cache` |
| request sessions | — | 1 per request, closed | `exporter/webapp/main.py` | NullPool, `get_db()` context manager |
| `BoldDefinitionsSearchManager` | — | 1 per request, **closed** ✓ | idem, line 220 | used as a context manager since Phase 2; the session used to leak (+12.3 MiB per 50 requests) and a singleton was rejected because a long-lived session grows its identity map |

## Resources
- PDF export requires the `typst` CLI on PATH (>= 0.14 for
  `--no-pdf-tags`); CI installs the musl binary in the workflow. The
  exporter compiles in ~31 memory-bounded chunks (~2 GB per typst
  subprocess, ~3.5 GB total peak) and merges with pypdf — a single
  compile would need ~29 GB (typst holds ~2 MB per laid-out page).
  Body-level `#set par/page` rules in rendered typst data must stay
  on one line so chunk state replay captures them.
- Mobile DB export requires dictionary sources to be materialized first:
  `cd resources/other-dictionaries && uv run python scripts/prepare_sources.py`
  (decompresses tracked archives, builds MW and Apte fresh from Cologne). MW has
  a tracked `mwweb1.zip` fallback; Apte's `ap90web1.zip` is gitignored, so a
  fresh clone has no fallback and the build fails if Cologne is unreachable.
  `mobile_exporter.py` hard-fails if sources are missing.
- The mobile DB's `word_fuzzy`/`fuzzy_key` columns are generated by
  `_strip_diacritics_mobile` in `exporter/mobile/mobile_exporter.py`. The Flutter
  app reimplements this as `stripDiacritics` in `lib/utils/diacritics.dart` and
  the two **must** stay byte-identical — the app matches its computed key against
  the stored column, so any divergence silently breaks lookups. Verify parity
  across the whole built database, not with sample words.
- PTS page references in `sutta_info.dv_pts` come from the PTS↔CST concordance
  (https://github.com/jorgecaa/pts-vri-concordance, CC0), vendored as
  `db/suttas/pts_concordance.tsv` and regenerated on demand by
  `db/suttas/convert_pts_concordance.py` (reads the source XLSX; the build reads
  only the TSV). The DV catalogue no longer supplies PTS and `dv_pts` is no longer
  part of `SuttaInfo.dv_exists`, so the column name is a legacy misnomer — a rename
  to `pts_ref` is an open follow-up. The population step
  (`db/suttas/pts_concordance.py`) is deliberately unguarded: gating it on
  `table_rebuilt` makes it a no-op on every incremental run. The concordance's
  712 chapter-relative KN entries (`file:cN…`) are skipped and need the CST XML
  chapter structure to resolve. Added 2026-07-31.
- `README.md` and `CONTRIBUTING.md`
- `conductor/product.md` and `conductor/tech-stack.md`
- `docs/technical/` for database and project structure documentation
- `db/`, `gui2/`, `exporter/`, `tests/`, and `scripts/` as the main implementation areas
- `resources/flet-docs/` for Flet reference material
- Slob export in CI relies on `PyICU` and ICU system libraries

## What The Output Looks Like
The project outputs updated dictionary data, a generated and tested SQLite database, and export artifacts in multiple formats for downstream apps, websites, offline dictionaries, and related release channels.
