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
- gui2's db load is fast-path since 2026-09-19: `initialize_db()` only populates the lookup sets and the `all_decon_no_headwords` DbInfo cache (db-loaded ~0.8 s); the corpus and the synonym/phonetic detector load lazily via `load_corpus()` / `get_relationship_detector()` (warm-up pre-builds the detector). Anything that writes `lookup.headwords` or `lookup.deconstructor` MUST go through `sync_lookup_column` — it deletes the cache in-session so invalidation commits atomically with the data change; a bypass writer leaves a stale cache until the next full build (`scripts/build/decon_cache_update.py` runs at the end of generate_components). Adding columns to gui2's corpus consumers does not require touching this — the deferred HTML columns are proven unread, everything else is always loaded.
- Flet 1.0.1 (pinned, `flet[all]==1.0.1`; migrated from 0.28.3 on 2026-09-18, bumped from 1.0.0 on 2026-09-27) exposes no cursor-position/selection API on `ft.TextField` — putting the caret at the end of a pre-filled field relies on focusing after the value is set, not a real cursor setter. There are also no per-dialog keyboard events: dialog-scoped keys (e.g. Enter = submit) must hook `page.on_keyboard_event` with a save/restore wrapper (see the EG popup in `gui2/pass2_add_view.py`).
- Flet 1.0 awaits a **sync** event handler inline on the asyncio event loop (`flet/controls/base_control.py`), unlike 0.x which ran it in a thread pool. A sync handler that loops over a long job therefore blocks every queued UI patch until it returns, and no other event (a Stop click) can be dispatched. Long-running handlers must be `async def`, with blocking work in `asyncio.to_thread` and an `await asyncio.sleep(0)` after each batch of UI updates to flush the patch. Fixed this way in the tests tab and the pass2 auto tab; `gui2/pass1_auto_controller.py` still has the old shape.
- Flet 1.0's silent traps, all of which fail with no error and a normal-looking screen — the migration thread's archive has the full BR-1..BR-29 list. The four that bite hardest: **`Control.focus()` is a coroutine**, so call `request_focus()` from `gui2/ui_utils.py` rather than `.focus()`; **`TextField.error_text` was renamed to `error` while `Dropdown` kept `error_text`**, and assigning the wrong one writes a dead instance attribute that never reaches the UI; **sync handlers now run on the event loop** where 0.28 gave them a worker thread, so any blocking work freezes the window and must go through `asyncio.to_thread` or `page.run_thread`; and **an explicit `border=` overrides the theme-resolved focus and error states**, so a field that states its border must derive its own red (the `Dpd*` classes do this in `before_update()`). Assigning an unknown attribute on a 1.0 control is silently accepted, which is why all of these are invisible rather than loud.
- `gui2/**` is excluded from **both** type checkers (`[tool.pyright].exclude` and `[tool.pyrefly].project-excludes` in `pyproject.toml`), so `uv run pyright gui2/<file>` reports "0 errors" having analysed zero files. Check `--outputjson`'s `filesAnalyzed` before treating a gui2 pyright result as evidence. Added 2026-09-18.
- Flet 1.0 hover events deliver a **boolean** in `Event.data`, where 0.28 sent the strings `"true"`/`"false"`. An `e.data == "true"` guard therefore returns early on every hover and silently disables the handler — several such comparisons survived the migration (`gui2/compound_type_tab_view.py`, `gui2/roots_tab_view.py`, and the delete-hover handler in `gui2/pass2_add_view.py` still carry them). Added 2026-09-18.
- `gui2/build/site-packages/` holds a **vendored copy of Flet 0.28** as a build artifact. It is gitignored, so a default `rg` skips it — but `grep -r` does not, and a sweep for removed 1.0 API across `gui2/` that forgets to exclude `build/` returns dozens of hits and reads as a failed migration. Every guard script in the migration archive excludes it. Added 2026-09-18.
- Anki family decks (Family Root, Family Word, Family Compound, Root Matrix) share `exporter/anki/anki_updater.py::make_new_family_note`, but Root Matrix needs its own Anki note type ("DPD Root Matrix") for its `table.root_matrix` CSS — note type selection is keyed off deck name via `family_note_type()`. Any new family deck needing dedicated styling must be added to `FAMILY_DECK_NOTE_TYPES`, and its templates/CSS added to `exporter/anki/templates/` + `template_pusher.py::NOTE_TYPE_FILES` so the note type stays backed up and re-pushable.
- Two type checkers run, with different jobs. **pyright** is the per-file commit gate (`.pre-commit-config.yaml`, `basic` mode, config in `[tool.pyright]`). **pyrefly** is the repo-wide sweep — `just typecheck`, enforced by `.github/workflows/typecheck.yml` on push to `main` and every PR, config in `[tool.pyrefly]`. The hook only ever sees staged files, so pyrefly is what catches a change that breaks a caller elsewhere. pyright wins on disagreement (97.8% vs 87.8% typing-spec conformance); treat a pyrefly-only complaint as a checker limitation and suppress it narrowly with `# pyrefly: ignore` plus a reason. A pyrefly fix is not finished until `uv run pyright <file>` is also clean — satisfying one regularly breaks the other. Added 2026-07-27.
- Dependencies: `uv add` / `uv remove` sync with the default groups only and uninstall the whole `tools` group. Pass `--no-sync`, then run `uv sync --all-groups`. `just deps` runs deptry (config in `[tool.deptry]`, which treats `tools` as a non-dev group); it exits 1 while any finding remains. Several open lower bounds (sqlalchemy, filelock, ruff) mean a blanket `uv lock --upgrade` jumps major versions — upgrade with `--upgrade-package`. Added 2026-09-27.
- `tools/proofreader.py` and gui2's `ProofreaderManager` (in `gui2/toolkit.py`) both read/write `tools/proofreader.tsv`. Any file touched by both a standalone script and gui2 needs the same pattern: a `filelock.FileLock` guarding a reload-fresh-from-disk-then-mutate-then-save sequence — a lock alone isn't enough, since holding a stale in-memory copy across the lock still loses updates.
- `[tool.pyright].exclude` in `pyproject.toml` includes `"tests"` repo-wide, so `uv run pyright tests/some_file.py` silently reports `0 files analyzed` / "0 errors" even with an explicit file path passed — that is not evidence the file is clean, just that pyright skipped it. To actually check a test file, run `uv run pyright --project /dev/null <file>` (bypasses the exclude) or read `--outputjson`'s `filesAnalyzed` count to confirm a normal run wasn't a no-op. Added 2026-08-21.
- The `resources/tpr_downloads` submodule (bksubhuti's TPR app download catalog) is migrating from `https://github.com/bksubhuti/tpr_downloads/raw/master/...` links to `https://cdn.jsdelivr.net/gh/bksubhuti/tpr_downloads@master/...` links, because GitHub raw downloads are blocked in Myanmar while the jsdelivr CDN mirror is not. The new TPR app reads a new file, `download_source_files/download_list_2.json`; older TPR versions still read the original `download_source_files/download_list.json` — both need to keep receiving DPD's release/beta entry during the transition (`exporter/tpr/tpr_exporter.py::copy_zip_to_tpr_downloads` writes both, matching entries by URL suffix rather than list index since upstream reorders that list without warning). Added 2026-08-21. The daily beta zip is opt-in: on a non-uposatha day `tpr_exporter.py` now exits before loading the db unless `[tpr] make_beta = yes` in config.ini — the version (release/beta) is decided at the top of `main()`, and the whole data pipeline exists only to feed the zip. Updated 2026-10-25.

- The four OpenAI-compatible AI providers (OpenRouter, NVIDIA, DeepSeek, Z.ai) are one module, `tools/ai_openai_compat.py`, driven by the `PROVIDER_SPECS` table; the separate `ai_open_router.py`, `ai_nvidia.py`, `ai_deepseek_manager.py` and `ai_zai_manager.py` are gone. Transport is plain `requests` — the `openai` SDK was removed as a dependency, since those two files were its only importers repo-wide and it was carrying a single ordinary POST. Its default two automatic retries were also delaying `AIManager`'s own fallback on 429/5xx. Provider connection details deliberately live in code, not in `tools/ai_models.json`: that file is hot-reloadable from a button in three gui2 views, but providers are constructed once at `AIManager.__init__` and never rebuilt. The four APIs report errors in three different shapes — nested `error.message` (OpenRouter/DeepSeek/Z.ai, with `code` an int, a string, or absent), RFC 7807 `{type,title,status,detail}` with no `error` key (NVIDIA, on 410), and plain non-JSON text (NVIDIA, on 404) — so `_error_detail()` must keep all four fallback branches. Added 2026-08-25.
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

**Shrinking the db is not free — the ORM blocks it (measured 2026-09-18).** Dropping
the 11 columns the webapp never reads (`freq_data`, the transliteration columns,
`created_at`/`updated_at`, `lookup.sinhala`/`thai`/`devanagari`/`rpd`) and vacuuming
takes `dpd.db` from 2180.7 to **1470.1 MiB (32.6 % smaller) in 67 s** — but serving
from that copy fails: **125 of 130 golden-oracle responses became
`Internal Server Error`**, `sqlite3.OperationalError: no such column: lookup.rpd`.
SQLAlchemy selects every column the model declares, so a column cannot leave the db
while `db/models.py` still declares it. Any diet therefore starts with `load_only` in
the webapp's entity loads, not with the db. Design and evidence:
`kamma/threads/20260918_serving_db_diet/` (`spec.md`: options A/B/C, and why
`deferred=True` across the project was rejected).

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
- Offers of help (time, skills, resources, services) come in through the Google
  Form on the contributing overview page of the docs website; its response
  sheet is the record. This is separate from the contribution website (hosted
  gui2, where approved people add Pāḷi entries). Added 2026-10-22.
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
- `CITATION.cff` and `docs/how_to_cite.md` are GENERATED — `tools/version.py` writes the
  first, `tools/docs_update_how_to_cite.py` the second, both from the release version so
  the citation can never name a stale version. Never hand-edit either. The Zenodo concept
  DOI (`10.5281/zenodo.22979413`) is the `DOI` constant in `tools/version.py` — it always
  resolves to the newest release, so it is fixed, not looked up — and flows from there into
  the citation string, `db_info`, the CFF, the docs page, the GoldenDict `cite` entry,
  the PDF front matter and the Flutter app's How to Cite card, in local and CI builds
  alike. The Zenodo record's own metadata comes from the static `.zenodo.json`, which
  overrides `CITATION.cff` on Zenodo; it has no version field, and a test keeps it in
  step with `AUTHOR`/`ABSTRACT`/`KEYWORDS`. Added 2026-09-06, DOI made a constant
  2026-09-30.
- `README.md` and `CONTRIBUTING.md`
- `conductor/product.md` and `conductor/tech-stack.md`
- `docs/technical/` for database and project structure documentation
- `db/`, `gui2/`, `exporter/`, `tests/`, and `scripts/` as the main implementation areas
- `resources/flet-docs/` for Flet reference material
- Slob export in CI relies on `PyICU` and ICU system libraries

- `dhamma.gift` is a single-page app: every path returns HTTP 200 with byte-identical HTML, so a status code proves nothing about whether a sutta link resolves. Verify links against the site's own endpoint, `https://dhamma.gift/api/text/<sc_code lowercased>`, which returns the sutta's JSON (title plus segments) or a 404 with an "Unknown sutta id" error. Sutta links DPD generates use the short path form `https://dhamma.gift/<sc_code lowercased>`. Added 2026-09-14.

- `resources/other-dictionaries/dictionaries/buddhist_wiki` ("Buddhist Wiki", `buddhist-wiki.zip` — never "Wikipedia" in the product name, per the Wikimedia trademark policy; CC BY-SA needs a licence link on every entry) gets its article list (category walk) from the enwiki SQL dumps, and its article text and redirects (`prop=redirects`) from the action API. CI rebuilds every dictionary from committed sources only, so the fetched text and redirects ship as the committed `buddhist_wiki.tar.zst` (step 6 of `run_overnight.sh`); without it the CI build fails (2026-10-05): the list is capped at depth 5 of Category:Buddhism (~24k articles, ~100 MB over the API, about an hour at one batch of 20 every ~3 s), while the pages-articles dump is 25 GB on a ~0.4 MB/s line. The dump extractor stays as the fallback for a much larger list. Pin every dump URL to one dated run (`enwiki/<YYYYMMDD>/`), never `latest` — `latest` moves on the 1st/20th of the month and `curl -C -` would splice two files. Since MediaWiki 1.45, `categorylinks.cl_target_id` is a `linktarget.lt_id`, not a page id; the category walk needs the `linktarget` dump. ElementTree finds namespaced dump tags only with `{*}tag`. Added 2026-10-04.

- `exporter/epitaka/epitaka_exporter.py` (gated by `[exporter] make_epitaka`, local only) copies `exporter/share/dpd-mobile.db` into the ePitaka app's `dpd-mobile.db` at `[epitaka] db_path` (here `~/.local/share/com.dn.epitaka/dpd-mobile.db`; the app id is `com.dn.epitaka`). The app switched from its own `dpd-dictionary.db` to the mobile database (branch `add/dpd-mobile-integration`, deletes the old file once the new one opens), so the old rebuild-and-prebuild exporter was removed 2026-10-15. Install goes through SQLite's backup API, never a file copy, because the file is in WAL mode; the backup waits forever on another process's write lock, so the script first closes a running ePitaka (psutil, process name `epitaka`; skips the install if it does not exit in 30 s) and reopens it afterwards with `gtk-launch epitaka`. It runs after the mobile exporter in `scripts/bash/makedict.py`, so every export installs a fresh copy.

- Contributor-server env vars (set per user by `scripts/server_contrib/add_contributor.sh`, read in `gui2/`): `DPD_GUI2_USERNAME`, `DPD_GUI2_ROLE=contributor-server`, `DPD_GUI2_DATA_DIR` (per-user files outside the repo), `DPD_GUI2_TABS` (extra tabs), plus Flet's `FLET_FORCE_WEB_SERVER=true` and `FLET_SERVER_PORT`. Start the server with `uv run python gui2/main.py`, never `flet run` (it starts in `gui2/.flet/storage/data` and breaks relative paths). Flet 1.0.1 keeps a closed session for up to an hour when its send loop raises on a closing socket, so the one-session guard lets the newest page take over. Added 2026-10-30.

## What The Output Looks Like
The project outputs updated dictionary data, a generated and tested SQLite database, and export artifacts in multiple formats for downstream apps, websites, offline dictionaries, and related release channels.
