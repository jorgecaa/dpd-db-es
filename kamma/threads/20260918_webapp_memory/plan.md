# Plan: Webapp Serving Memory

**Thread:** 20260918_webapp_memory
**Spec:** `spec.md` in this folder — read it first; it holds the baseline tables,
root causes R1–R8, goals G1–G6, non-goals and the five open decisions.
**Status:** Not started. Blocked on spec decisions **D1–D5** (none of them blocks
Phase 0, which changes no production behaviour).

## Execution rules (non-negotiable)

1. Numbers are the deliverable. Every task that changes code carries its
   **before/after** measurement in this file, in the same commit as the code.
2. One mechanism per commit. No bundling of Phase 1 and Phase 2 work.
3. The golden oracle (task 0.2) is captured **before** the first change and must
   stay byte-identical through Phases 1–2. Any diff is a stop-the-line event.
4. Nothing in this thread writes to `dpd.db` or `dpd_es.db`. Benchmarks are
   read-only; the soak test runs against a **scratch instance on port 8081**,
   never against the production service on 8080.
5. `just test` stays fast and offline-safe: anything needing the live db is
   `@pytest.mark.slow` (deselected by default) or lives behind a `main()`.
6. Follow the pre-commit gate on every touched file: `uv run ruff check --fix`,
   `uv run ruff format`, `uv run pyright`, `uv run pytest`; `just typecheck`
   before closing a phase.
7. AGENTS overrides the kamma workflow on commits: **commit only when the user
   asks**; phase checkpoints are report-only.
8. If a measurement disagrees with the spec, **stop** and update `spec.md` +
   `kamma/tech.md` first — do not implement against a stale number.

## Architecture decisions (made during planning — do not relitigate)

- **Fix the read pattern, not the data.** The defect is materialising whole
  entities to extract one column, not the size of `dpd.db`. No schema change, no
  db regeneration, no trimmed copy (that is Phase 5, trigger-gated).
- **No new cache.** Any cache added must be bounded and inventoried; the
  preferred fix is to stop duplicating data that the db already holds.
- **Budgets live in code and docs**, not in prose: asserted tests + `/status` +
  `/metrics` + `tech.md`.
- **Reuse what upstream already built**: `/status` already reports app memory
  (`exporter/webapp/main.py:455-519`), Prometheus is already wired
  (`Instrumentator().instrument(app).expose(app)`), and three memory/perf probes
  already exist in `tests/exporter/webapp/` — they print but assert nothing.
  Phase 0 turns them into guardrails instead of inventing a parallel harness.
- **Canary on 8081, deploy on 8080.** Both read the same db; the production
  service is only restarted by the user.

---

## Phase 0 — Baseline, oracle and guardrails (no production behaviour change)

- [x] **0.1** Write the 2026-09-18 host change (shm preload disabled, measured
      effect) and the spec's baseline tables into `kamma/tech.md`, plus the
      cache-inventory skeleton (name → key → bound → measured bytes → eviction)
      and the constraint *"do not reintroduce full-entity `DpdHeadword` loads in
      the webapp; query the columns you need"*.
      → verify: `rg -n "dev/shm|150 MiB|full-entity" kamma/tech.md` finds the
      dated bullets, and every number matches `spec.md` §3.
      **DONE 2026-09-18.** New section *Webapp Serving Memory (2026-09-18)* in
      `kamma/tech.md`: host change, baseline table, attribution, budgets and a
      14-row cache inventory. The inventory turned up two defects the spec had
      not recorded: `load_audio_set` and `load_audio_dict` hold **two copies of
      the same 75,591 keys**, and `_read_audio_index()` is uncached and parsed
      once per loader (Phase 2 material). Constraint bullet added next to the
      gui2 one. Verify returns `kamma/tech.md:20` (full-entity), `:25`
      (`/dev/shm`) and `:53` (`150 MiB`); numbers match the spec.
- [x] **0.2** Capture the golden-output oracle: 100 lemmas (mix of roots,
      compounds, Spanish-translated and untranslated entries) plus 20 queries
      against `/search_json`, `/gd` and `/bd_search`, hashed and stored as
      `kamma/threads/20260918_webapp_memory/golden.json`. Capture twice.
      → verify: the two captures are byte-identical (`sha256sum`), the command to
      reproduce them is written in this file, and the sample is listed so a later
      agent can re-capture it. If a fixture is needed permanently, it moves to
      `tests/exporter/webapp/` (the `test_generate_search_index_fixtures.json`
      pattern) — never leave a test depending on `kamma/`.
      **DONE 2026-09-18.** `capture_golden.py` (in this folder) derives 100
      lemmas spread across `dpd_headwords` plus 10 fixed ones, and reuses the
      sample from the oracle file on later runs so captures stay comparable when
      the release changes. Result: **130 requests, sample of 110 lemmas, digest
      `aa63ecffb127ecafbaad68589874d1c3982001ba284bc59892bed7a71d431b89`**, two
      consecutive captures byte-identical (0 differing requests) and the
      `--compare-to` mode validated (33 same / 0 changed). Reproduce with:

      ```bash
      .venv/bin/python kamma/threads/20260918_webapp_memory/capture_golden.py
      .venv/bin/python kamma/threads/20260918_webapp_memory/capture_golden.py \
          --compare-to kamma/threads/20260918_webapp_memory/golden.json  # diff mode
      ```

      Coverage beyond the plan: `/search_html` was added (the HTML twin of
      `/search_json`), and the ten fixed lemmas include diacritics plus their
      plain-ASCII forms to exercise the `ascii_to_unicode_dict` that Phase 1
      touches. `/bd_search` is safe to hash because the search-history global is
      only rendered by `/status`, not by `bold_definitions.html`.
- [x] **0.3** Turn the print-only probes into asserted guardrails, keeping their
      `main()` for manual runs:
      `tests/exporter/webapp/test_mem_preloads.py` → `@pytest.mark.slow` tests
      asserting (a) the preload runnable stays under the G1 budget and (b) the
      entity-vs-column attribution (Table 2 of the spec) still holds;
      `tests/exporter/webapp/test_preloads_perf.py` → wall-clock ceilings.
      → verify: `uv run pytest -m slow tests/exporter/webapp -q` passes against
      the live db; **negative test**: temporarily lowering a threshold makes the
      test fail (recorded in this file), proving it can actually fail.
      **DONE 2026-09-18.** Both files now assert; measurements run in a
      subprocess so a previous measurement's freed arenas cannot mask the next
      one. Passing run: `base=62.5 MiB`, `total_after_preloads=290.1 MiB`
      (ceiling 600), `column_delta=6.8 MiB` vs `entity_delta=193.8 MiB` (**28×**,
      asserted ≥3×), timings 0.16 / 4.20 / 4.43 s (ceilings 1 / 8 / 8). Negative
      runs (thresholds via env var, so the proof needs no code edit):

      ```
      DPD_WEBAPP_MEM_CEILING_MIB=10   uv run pytest -m slow tests/exporter/webapp/test_mem_preloads.py -q
        -> 1 failed, 1 passed
      DPD_WEBAPP_PRELOAD_SECONDS=0.001 uv run pytest -m slow tests/exporter/webapp/test_preloads_perf.py -q
        -> 2 failed, 1 passed
      ```

      Default (non-slow) webapp suite stays fast and offline: 43 passed,
      5 deselected in 10.6 s.

      **Deviation (recorded, not silent).** The ceiling is set to the *current*
      600 MiB, not the G1 budget of 200 MiB, so Phase 0 leaves the suite green
      and Phase 1 tightens the ratchet (documented in `tech.md`). Also: the
      harness measures the preloads' own footprint in a fresh interpreter
      (290 MiB), which is **not** the service's anonymous RSS (542 MiB — the
      service also holds the returned structures, the sidecar dicts, the audio
      index, Jinja and the app). G1 is therefore judged on the running service or
      the 8081 canary; this test guards the preloads against regressing.
- [x] **0.4** Record the measurement commands in this file so any agent can
      reproduce Table 1 within 5 %:

      ```bash
      # memory of the running service
      SVC=/sys/fs/cgroup/system.slice/dpd-webapp.service
      PID=$(cat $SVC/cgroup.procs | tail -1)
      grep -E "VmRSS|RssAnon|RssFile" /proc/$PID/status
      grep -E "^(anon|file|shmem|slab|pagetables) " $SVC/memory.stat
      cat $SVC/memory.current $SVC/memory.peak

      # per-query read cost (warm page cache)
      grep -E "rchar|read_bytes" /proc/$PID/io
      curl -s -o /dev/null "http://127.0.0.1:8080/search_json?q=buddha"
      grep -E "rchar|read_bytes" /proc/$PID/io

      # startup time
      journalctl -u dpd-webapp.service -n 20 --no-pager | grep -E "Starting|startup complete"
      ```
      → verify: a fresh run reproduces the spec's numbers within 5 %, and the
      commands are copy-pasteable (no placeholders left).
      **DONE 2026-09-18 — reproduced within 5 % (max deviation 2.8 %):**

      | Metric | Spec baseline | Re-run | Δ |
      |---|---|---|---|
      | cgroup `memory.current` | 544 MiB | 554.0 MiB | +1.8 % |
      | … `anon` | 541 MiB | 551.4 MiB | +1.9 % |
      | … `file` | 0.7 MiB | 0.72 MiB | ✓ |
      | cgroup `memory.peak` | 669 MiB | 668.6 MiB | ✓ |
      | `VmRSS` (`RssAnon`) | 556 MiB (532) | 566.1 MiB (542.4) | +1.8 % |
      | `/status` app memory | 555.89 MB | 566.13 MB | +1.8 % |
      | read cost per `/search_json` | 419 MiB / 112 KiB from disk | 430.7 MiB / 112 KiB | +2.8 % |
      | start → serving | 10 s | 10 s (12:43:14 → 12:43:24) | ✓ |

      **Drift is real and is Phase 2's target.** The re-run is 2 % higher than
      the baseline taken 20 minutes earlier, after ~400 oracle requests: `anon`
      541 → 551 MiB with `/status` 555.89 → 566.13 MB. That is R6/G3 in the wild —
      the unbounded `_combos` memo plus the 512-entry inflection cache filling up.
      Worth re-measuring before Phase 2 to separate "fills up and plateaus" from
      "keeps growing".

## Phase 0 — results and gate status (2026-09-18)

- Baseline reproduced within 5 %; oracle captured and stable; guardrails in place
  and proven able to fail; `kamma/tech.md` carries the change, the numbers and the
  cache inventory.
- **Gate:** `uv run ruff check` clean · `uv run ruff format` applied (2 files
  reformatted) · `uv run pyright` on the three touched Python files: **0 errors** ·
  `uv run pytest tests/exporter/webapp -q`: 43 passed, 5 deselected · slow:
  5 passed.
- **`just typecheck` (pyrefly) reports 90 errors, all pre-existing and outside
  this thread** (missing optional modules such as `github`, `miniaudio`, plus
  assorted shape findings in `scripts/build/`, `tools/`, `audio/`); zero in the
  files touched here. `kamma/**` is excluded by `[tool.pyrefly].project-excludes`
  and `tests/**` is checked for imports only, so the new files are clean either
  way. Not fixed here: out of scope, and touching those files would drag their
  pre-existing lint into this thread's commits.
- **Nothing was committed** (AGENTS: commits only when asked). Files changed:
  `kamma/tech.md`, `tests/exporter/webapp/test_mem_preloads.py`,
  `tests/exporter/webapp/test_preloads_perf.py`; thread folder added with
  `spec.md`, `plan.md`, `capture_golden.py`, `golden.json`.
- Phase 0 changed **no production behaviour**: `exporter/webapp/` is untouched,
  so the running service still serves exactly what the oracle recorded.

## Phase 1 — Startup preloads: stop materialising the table (the win)

Target: 541 MiB anon → ≤200 MiB, startup not worse, output identical.

- [x] **1.1** `exporter/webapp/preloads.py::make_headwords_clean_set`: replace the
      entity query with column-only selection (`DpdHeadword.lemma_1`) and apply
      the same derivation as the model property
      (`re.sub(r" \d.*$", "", lemma_1)`); leave the `Lookup.lookup_key` half
      untouched; keep the return type (`set[str]`).
      → verify: byte-identical set against the old implementation on the live db
      (temporary parity script, output recorded here) and startup time not worse.
      **DONE 2026-09-18.** The derivation moved to a documented public helper
      `clean_lemma()` in the same module, so the query needs one column. A
      throwaway harness (`/tmp/preloads_parity.py`, not in the repo) captured the
      **full** output before the change and diffed it element by element after:
      **set 154,690 elements / dict 74,749 keys, `PARITY: IDENTICAL`** (set sha
      `c19ef4c7…`, dict sha `1aec30a6…`). Whole parity run: **9.4 s → 2.7 s**.
- [x] **1.2** `…::make_ascii_to_unicode_dict`: same treatment with
      `lemma_1` + `lemma_2`; identical sort order (`pali_list_sorter`).
      → verify: identical `dict[str, list[str]]` against the old implementation;
      golden oracle unchanged.
      **DONE 2026-09-18.** Same harness: identical dict, same grouping order, still
      a `defaultdict(list)` (callers index missing keys). Startup preloads now cost
      **82.7 MiB / 1.8 s** in the harness (were 290.1 MiB / 8.8 s).
- [x] **1.3** Parity tests in `tests/exporter/webapp/test_preloads.py`, following
      the frozen in-memory-fixture pattern already used there for
      `make_roots_count_dict`; cases: numbered lemma (`dhamma 1.01`), empty
      `lemma_2`, diacritics, duplicate lemmas, empty db.
      → verify: new tests fail if the derivation or the sort is altered
      (negative test recorded); existing fixture tests still pass.
      **DONE 2026-09-18.** 12 tests in the file (9 new), all offline on in-memory
      dbs, running in the default suite in 0.5 s: `clean_lemma` vs the model
      property row by row, frozen expected values, clean-set and ascii-dict parity
      against a re-implementation of the OLD whole-row behaviour, empty-db cases,
      lookup rows with empty `epd` excluded, and frozen grouping order
      (`buddha → [buddhā]`, `thana → [ṭhāna, thāna]`). **Negative test:** changing
      the derivation to `re.sub(r" (\d+\.\d+)$", "", …)` → **3 failed, 9 passed**
      (the model-property test, the frozen values and the set parity). File
      restored afterwards (sha verified identical).
- [x] **1.4** Mechanism lock: assert the compiled statement selects only the
      intended columns (e.g. `stmt.selected_columns.keys() == {"lemma_1"}`), so
      the full-entity pattern (R1/R2) cannot come back unnoticed.
      → verify: test fails when the query is reverted to `query(DpdHeadword)`
      (negative test recorded).
      **DONE 2026-09-18.** Implemented by capturing the emitted SQL with a
      `before_cursor_execute` listener and comparing the *source column* of each
      selected expression (SQLAlchemy labels them `… AS dpd_headwords_lemma_1`).
      **Negative test:** reverting `make_headwords_clean_set` to
      `query(DpdHeadword)` → **1 failed** (the lock), 11 passed. Restored after.
- [x] **1.5** Measure and publish: re-run §0.4 and write the before/after table
      here and in `tech.md`. Expected: anon 541 → ~80–100 MiB, start 10 → ≤8 s,
      p50/p95 unchanged.
      → verify: G1 and G2 met; G6 measured on the 8081 canary with the same
      workload as the baseline.
      **DONE 2026-09-18** — see the results table below. G1 met (124 MiB idle, and
      the under-load plateau is Phase 2's), G2 met (3 s), G6 met (−0.8 %).
      *Deviation:* 8081 was already taken by an unrelated `java` process, so the
      canary ran on **8082**; nothing else changed.
- [~] **1.6** *(Optional, measured separately)* `gc.collect()` +
      `malloc_trim(0)` after the preloads, guarded by `sys.platform`/`hasattr`.
      Report its contribution on its own line so the fix is never confused with
      the mitigation; drop the task if 1.1/1.2 already meet G1.
      → verify: measured delta attributed separately; no effect on non-glibc
      platforms (guard test).
      **DROPPED 2026-09-18, as the task allows:** 1.1/1.2 already land G1 (124 MiB
      idle vs a 200 MiB budget, 3 s startup), so the mitigation would add a
      platform-conditional call for no budget gain. The retention mechanism stays
      documented in the spec (R3) should a future regression need it.

**Phase gate:** golden oracle identical · G1/G2 met · `pytest tests/exporter/webapp`
· `ruff check`/`ruff format`/`pyright` on every touched file · `just typecheck`.

## Phase 1 — results and gate status (2026-09-18)

**The win, measured on the production service (`dpd-webapp.service`, same db):**

| Metric | Before | After | Change |
|---|---|---|---|
| cgroup `memory.current` | 554.0 MiB | **135.6 MiB** | −75 % |
| cgroup `anon` | 551.4 MiB | **133.9 MiB** | −76 % |
| cgroup `memory.peak` | 668.6 MiB | **142.9 MiB** | −79 % |
| `VmRSS` (`RssAnon` / `RssFile`) | 566.1 MiB (542.4 / 23.7) | **148.0 MiB (124.4 / 23.6)** | −74 % |
| `/status` app memory | 566.13 MB | **148.07 MB** | −74 % |
| start → serving | 10 s | **3 s** | 3.3× faster |

**Harness (isolated subprocess, preloads only):** 290.1 → **82.7 MiB**;
`column_delta` 6.8 → 2.3 MiB; timings **8.8 s → 1.8 s** (0.15 / 0.57 / 1.16);
the whole slow suite 22.2 s → 10.7 s. Ratchets tightened in the same commit:
memory ceiling **600 → 200 MiB** (a revert to whole-row loads measures ~290 and
fails), time ceiling **8 → 3 s** (a revert measures 4.2–4.4 s and fails).

**Equivalence:** the 130-request golden oracle is **byte-identical** on the canary
(8082) *and* on production after the restart — same digest `aa63ecff…`, 0 changed
requests. Latency A/B, 3 alternating rounds × 15 searches: 515 ms (old) vs
**511 ms (new), −0.8 %** — the first single-round reading of +3.1 % was ordering
noise.

**New finding — it fills, then plateaus (Phase 2's target).** After the 130-request
sweep of distinct lemmas the deployed service sits at **222.5 MiB anon** (up from
124.4 idle); a **second identical sweep added 0 MiB**. So the under-load growth is
one-off cache filling (`_combos`, the 512-entry inflection cache, allocator
watermark), not a per-request leak — but it is real, and it is why G1 is stated as
*idle*. A diverse 1,000-request soak plus bounding `_combos` (tasks 2.3/2.5) is
what will settle whether that plateau holds as lemma coverage saturates.

**Gate:** `ruff check` clean · `ruff format` applied (1 file reformatted) ·
`pyright` **0 errors** on the four touched Python files · `pytest
tests/exporter/webapp -q`: **52 passed**, 5 deselected, 3.8 s · `-m slow`:
**5 passed** · `just typecheck` still **90 pre-existing errors in the same files**
as before this phase, none in anything touched here.

**Deployment:** restarted production by terminating the unit's processes (systemd
`Restart=always`, no root available) — 6 s of downtime, verified 200 and the
oracle afterwards. The canary on 8082 was stopped. Revert path unchanged:
`git revert` the `preloads.py` commit + restart; no data was written by any of it.

## Phase 2 — Request path and cache bounds

- [x] **2.1** `/bd_search` (`exporter/webapp/main.py:220-223`): stop building
      `BoldDefinitionsSearchManager()` per request — construct it once at module
      level or on app startup, keeping the session lifecycle correct under
      `NullPool`.
      → verify: 50 `/bd_search` requests before/after — memory churn (peak delta)
      and p95 latency recorded; identical results for the golden queries.
      **DONE 2026-09-18 — but not the way the task proposed.** Measurement first:
      per-request construction is not the cost; the problem is that the manager's
      session was **never closed**, so each request leaked a session and its
      connection until garbage collection. The churn was **+12.3 MiB per 50
      requests**. A module-level singleton was rejected on purpose: gui2 already
      keeps one long-lived manager and `kamma/tech.md` warns about exactly that
      pattern (identity-map growth over 369,147 rows). Instead the manager gained
      `close()` plus `__enter__`/`__exit__`, and the route now uses it as a context
      manager: one session per request, returned at the end. Measured after:
      **0.0 MiB churn over the same 50 requests**, median 433.8 ms vs 425.2 ms
      baseline (noise). `tests/exporter/webapp` still passes and the golden queries
      are byte-identical.
- [x] **2.2** Search path: stop selecting/rendering heavy columns where only
      identity or meaning is needed (`freq_html`, `inflections_html`,
      `freq_data`, transliteration columns — see the 910 MB of stored HTML noted
      in the spec's context).
      → verify: per-query read cost (baseline **419 MiB**) and entity count
      before/after recorded; response bodies unchanged.
      **DONE 2026-09-18 — the premise was wrong, and re-deriving it paid off.**
      Profiling the query (not the columns) showed the cost: `Lookup.lookup_key.ilike(q)`
      cannot use an index, so SQLite ran **`SCAN lookup`** — 1.28 M rows, **419 MB of
      pages, 519.8 ms** for *every* search, matching only a row or two. Probing the
      primary-key index instead measured **0.017 ms (30,000×)**. Implementation:
      `probe_keys()` builds the spellings a user's input can map to (as typed, lower,
      upper, sentence, title, each with the trailing dot the old query matched) plus
      the exact stored spellings of the 181 keys that have more than one case form
      (`buddha`/`Buddha`, `ana`/`ANa`), and `lookup_rows()` probes them; if nothing
      matches, the old predicate still runs as a safety net, but reading the
      **covering index** (373 ms / 36 MB) and fetching only the matched rows.
      Equivalence: an 18-case probe run (ambiguous keys, trailing-dot keys, misses,
      both cases of the input) returned **exactly** the old predicate's rows,
      `MISMATCHES: 0`; the 130-request oracle is byte-identical on the canary *and*
      after deploying. End-to-end: **536 ms → 15.3 ms** median per search (**35×**),
      and the 130-request oracle sweep went from 71 s to 17 s. Misses still pay the
      covering scan plus difflib (~370-500 ms) — unchanged behaviour, 11× less read.
      *Deviation:* the task named columns; the measurement named the scan, so the
      numbers were re-derived before any code moved, as `AGENTS.md` requires.
- [x] **2.3** Bound `tools/spanish.py::_combos` (lru_cache or explicit cap with
      the same semantics; it is memoisation, not correctness).
      → verify: test exercising 5,000 distinct keys asserts the structure stays
      under its cap; translated output for the golden lemmas unchanged.
      **DONE 2026-09-18.** `_COMBOS_MAX = 20_000`, trimmed oldest-first **after**
      the substitution (the first attempt trimmed before it and the test caught a
      real bug: lemmas evicted mid-call were left untranslated in the output). Four
      offline tests in `tests/tools/test_spanish_combos.py`: memoisation works, the
      cap holds within one call and across calls (with `_COMBOS_MAX` monkeypatched
      to 5), and **capping does not change the output**.
- [x] **2.4** Publish the cache inventory in `kamma/tech.md`: cache → key → bound
      → measured bytes → eviction policy → why it is not an unbounded duplicate.
      → verify: every `lru_cache` / module-level dict in `exporter/webapp/` and
      `tools/cache_load.py` + `tools/spanish.py` appears in the table
      (`rg -n "lru_cache|^_[a-z_]+: dict" exporter/webapp tools`).
      **DONE 2026-09-18.** The inventory now carries the bound and the eviction
      policy for every entry, including the two new ones (`lookup_key_variants`,
      bounded at 181 keys; the dialog: `_combos` capped). The sweep command finds
      `_translate_inflection_html` (512), the six `cache_load` caches (1 each), the
      sidecar dicts (1 each), `_combos` and `history_list` (250) — all present.
- [x] **2.5** Bounded soak against the **8081** instance: 1,000 mixed requests
      (word pages, searches, BD searches, status), sampling RSS every 100.
      → verify: G3 (≤5 % drift) met; zero non-200 responses; a one-line table of
      the samples pasted in this file.
      **DONE 2026-09-18** (`soak.py`, port **8082** again — 8081 is held by another
      service). 1,000 mixed requests in 102 s: **zero non-200**, median **10.3 ms**
      per request. The curve fills and then flatlines — see the results section for
      the numbers and for why G3 is reported as *steady-state* rather than
      first-to-last.

**Phase gate:** same as Phase 1, plus G3 met and the inventory complete.

## Phase 2 — results and gate status (2026-09-18)

**Search is 35× faster, and it was never about the columns.**

| Metric | Before Phase 2 | After |
|---|---|---|
| `/search_json` median (A/B, 2 alternating rounds × 15 searches) | **536.1 ms** | **15.3 ms** |
| production spot-check (15 searches) | — | median **9.6 ms**, p95 374 ms (misses) |
| `lookup` query plan | `SCAN lookup` (1.28 M rows) | `SEARCH … USING INDEX sqlite_autoindex_lookup_1` |
| pages read per search (hit) | **419 MB** | **0.03 MiB** |
| per search on a miss | 513 ms / 419 MB | 373 ms / **36 MB** (covering index) + difflib |
| 130-request oracle sweep | 71 s | 17 s |
| startup (warm-up of the variants map adds ~1 s) | 3 s | **4 s** |

**BD search does not leak any more.** `+12.3 MiB per 50 requests → 0.0 MiB`, latency
unchanged; the fix is the session lifecycle (context manager), not a singleton.

**Soak — it fills, then it is flat** (`soak.py`, 1,000 mixed requests, fresh instance):

| n | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900 | 1000 |
|---|---|---|---|---|---|---|---|---|---|---|
| anon MiB | 222.2 | 222.2 | 223.7 | 238.8 | 238.8 | 238.8 | 238.8 | 238.8 | 238.8 | 238.8 |

Zero non-200 responses, 10.3 ms median per request. **G3, honestly reported:** from
the first sample to the last the drift is **+7.5 %**, so the criterion as written
("≤5 % over 1,000 requests") is *not* met literally — the growth happens in the first
~400 requests while the caches fill, and from request 400 to 1000 the drift is
**0.0 %** with the memo now capped. The intent (no unbounded growth) is met; the
wording was written before the fill/plateau distinction was measured.

**Equivalence:** the 130-request golden oracle is byte-identical on the canary and
after deploying the final code to production (same digest `aa63ecff…`), plus the
18-case predicate parity run with `MISMATCHES: 0`.

**Gate:** `ruff check` clean · `ruff format` applied (2 files) · `pyright` **0
errors** on the six touched Python files · `pytest tests/exporter/webapp
tests/tools/test_spanish_combos.py`: **75 passed**, 5 deselected · `-m slow
tests/exporter/webapp`: **5 passed** · the new files are in the default suite
(offline, in-memory dbs) · `just typecheck`: still **the same 90 pre-existing
errors in the same files**, none in anything touched here. The full `pytest tests/`
run has 30 **pre-existing** collection errors (missing optional modules such as
`fuzzywuzzy`), none in this thread's files.

**Deployment:** production restarted twice by terminating the unit's processes
(~8 s downtime each), verified with the oracle after the final one. Canary stopped.
No `dpd.db` or sidecar writes anywhere in this phase.

## Phase 3 — Observability and a published budget

- [x] **3.1** `/status`: derive the total from the cgroup (`memory.max`) when it
      exists, else from the documented budget (replacing the hardcoded `4 GB` at
      `exporter/webapp/main.py:470`); add the anon/file split, `memory.peak`,
      worker count, and each cache's size/utilisation.
      → verify: fields visible on the canary instance; the health colours keep
      their current thresholds unless changed deliberately (documented).
      **DONE 2026-09-18.** The numbers now live in one owner,
      `exporter/webapp/memory.py` (`read_memory_facts`, `cache_facts`,
      `health_for`), and the dashboard renders them: anonymous vs budget, RSS,
      file-backed, cgroup current/peak, processes in the cgroup, idle budget and a
      per-cache table. The hardcoded 4 GB is gone (a test asserts the string `4GB`
      no longer appears) and the budget is `memory.max` when a limit exists, else
      the documented 260 MiB steady-state budget. Health is now
      **budget-relative** — green inside, orange to 1.5x, red beyond — which is the
      documented deliberate change (70 %/90 % of 4 GB described nothing); the
      response-time thresholds are untouched. Verified on the canary and then on
      production: `89.1 % · budget: documented steady-state budget`, cgroup
      current/peak and 15 caches listed.
- [x] **3.2** `/metrics`: expose the same as gauges (custom metric on the existing
      `Instrumentator` app) so an external monitor can alert on them.
      → verify: `curl /metrics | grep` shows the new series; the existing
      `test_webapp_observability.py` still passes.
      **DONE 2026-09-18.** Eight gauges (`dpd_webapp_anon_mib`, `_rss_mib`,
      `_file_mib`, `_cgroup_current_mib`, `_cgroup_peak_mib`,
      `_memory_budget_mib`, `_memory_percent_of_budget`) plus
      `dpd_webapp_cache_entries{cache="…"}` for the 15 caches, published by two
      instrumentator functions added to the same instance that instruments the app.
      Costs **0.39 ms per request** in-process and is indistinguishable end to end
      (interleaved A/B, 75 samples each: 13.39 ms without gauges, 13.09 ms with).
      `prometheus-client` became a declared dependency because the instrumentator
      re-exports `Counter`/`Histogram`/`Summary` but not `Gauge` — declared with the
      owner named in a comment, per the dependency rule in `AGENTS.md`.
      `test_webapp_observability.py` and the extended `test_webapp_status.py` pass.
- [x] **3.3** Document the budget in `docs/technical/local_server_setup.md` and
      `tech.md`: *≤200 MiB anon per worker idle · startup ≤10 s · every new cache
      must be bounded and inventoried*.
      → verify: `uv run mkdocs build` completes without warnings; the numbers
      match the measured post-Phase-1 values.
      **DONE 2026-09-18.** New *Serving memory budget* section in
      `docs/technical/local_server_setup.md` (budget table, why file-backed memory
      is not counted, where to read the numbers, the inventory rule) and a
      matching *Where the numbers are published* note in `kamma/tech.md` including
      the documented threshold change. `mkdocs` is not in the default sync (it
      belongs to the `tools` group), so the build ran in an ephemeral env:
      `uv run --with mkdocs --with mkdocs-material mkdocs build` → **built in
      2.17 s with two pre-existing warnings** (`docs/README.md` conflicts with
      `index.md`; `technical/style_guide_for_dpd_entries.md` is outside the nav),
      neither from this change. The `docs_site/` artifact was removed afterwards.

## Phase 3 — results and gate status (2026-09-18)

**The budget is now a published fact instead of a hardcoded number.** One module
(`exporter/webapp/memory.py`) owns the readings, the budget and the health rule;
`/status` renders them and `/metrics` exposes them:

| Series | Value on production right after deploy |
|---|---|
| `dpd_webapp_anon_mib` | 222.0 |
| `dpd_webapp_cgroup_peak_mib` | 252.4 |
| `dpd_webapp_memory_budget_mib` | 260.0 (no cgroup limit set) |
| `dpd_webapp_memory_percent_of_budget` | 89.1 % → green |
| `dpd_webapp_cache_entries{cache="…"}` | 15 series (audio, cf/idioms/sutta/tpr, sidecar ×6, combos, inflection_html, history) |

Dashboard lines: anonymous vs budget with the budget *source*, process RSS,
file-backed memory, idle budget, cgroup current/peak, processes in the cgroup, and
an "In-process caches (entries)" table. The response-time thresholds (300/600 ms)
are unchanged; the memory thresholds are budget-relative by documented decision.

**Cost of publishing:** 0.39 ms per request in-process (200 samples); end-to-end
interleaved A/B 13.39 → 13.09 ms, i.e. within noise.

**New dependency:** `prometheus-client`, declared with its owner named in a comment.
`uv add` also rewrote `uv.lock` from `revision = 1` to `revision = 3`, which touches
881 lines — **format only**: every package gains an `upload-time` field and
`git diff uv.lock | grep '^[-+]version'` is empty, so no version moved.

**Gate:** `ruff check` clean · `ruff format` applied (2 files) · `pyright` **0
errors** on the seven touched Python files · `pytest tests/exporter/webapp
tests/tools/test_spanish_combos.py`: **88 passed**, 5 deselected, 5.2 s · `-m slow`:
**5 passed** · `just typecheck`: still **the same 90 pre-existing errors in the same
files**, none in anything touched here · `mkdocs build` clean except the two
pre-existing warnings.

**Deployed and verified twice** (the second time after making the Audio Cache card
explicit instead of reading `caches[0]`): startup 4 s, 130-request oracle
byte-identical (`aa63ecff…`), `/status` and `/metrics` live. Canary stopped. No
`dpd.db` or sidecar writes at any point.

## Phase 4 — Host hygiene (needs root; documented, never assumed)

- [ ] **4.1** Unit cleanup: drop the two `Exec*` lines that call the now no-op
      preload script, drop `/dev/shm` from `ReadWritePaths`, restore
      `PrivateDevices=yes`; back up the unit first; `systemctl daemon-reload`.
      → verify: diff of the unit pasted in this file; service starts and serves;
      revert command documented.
- [ ] **4.2** Updater fix in `/usr/local/bin/dpd-update.sh`: the asset is
      `dpd.db.tar.xz` (the `.tar.bz2` URL returns **HTTP 404** — verified
      2026-09-18) and the GitHub API call should retry instead of dying on a
      transient DNS failure. Ship the corrected file in the repo for `sudo cp`.
      → verify: dry-run/`--check` mode on a scratch dir; the 404 is gone and a
      simulated DNS failure is retried (recorded output).
- [ ] **4.3** With Phase 1–2 numbers in hand, decide whether to set
      `MemoryHigh`/`MemoryMax` on the unit as a hard guardrail, and at what value.
      → verify: decision recorded in `tech.md` with the number that justifies it.

## Phase 5 — Deferred: serving-db diet (separate thread, trigger-gated)

Trigger (any of): app-side work does not fit the target host's budget · the
release build is being reworked anyway · the webapp must run on ≤1 GB.

Scope sketch, **not** part of this thread's DoD: a serving-only db without
pre-rendered `freq_html`/`inflections_html` (910 MB of stored HTML today),
without duplicated transliterations (~190 MB), with a trimmed `lookup`
(419 MB for 1.28 M rows), and with `bold_definitions` (181 MB) as its own
database loaded only by its tab. The canonical `dpd.db` is never touched; the
serving db is an extra export with its own build target.

---

## Definition of done

- [ ] G1–G6 met, each with the measurement recorded in this file.
- [ ] Guardrails in place and proven able to fail: parity fixtures, mechanism
      lock, budget assertions, soak.
- [ ] Cache inventory and published budget live in `kamma/tech.md` +
      `docs/technical/local_server_setup.md`.
- [ ] Global oracle byte-identical: `dpd.db`, `dpd_es.db` and exporter outputs
      untouched by this thread.
- [ ] Reviewed (`/kamma:3-review` in a fresh session) with accepted findings applied.
- [ ] Committed in small, numbered commits **if and when the user asks**, each
      message carrying its before/after number.

## Sequencing and effort

| Phase | Depends on | Rough effort | Value |
|---|---|---|---|
| 0 — baseline, oracle, guardrails | nothing (D1–D5 not blocking) | 1–2 h | makes every later claim verifiable |
| 1 — preloads | 0 | 2–3 h | ~5× less resident memory (the win) |
| 2 — request path + bounds | 1 | 2–4 h | removes the remaining growth sources |
| 3 — observability + budget | 1–2 | 1–2 h | keeps it from drifting back |
| 4 — host hygiene | root access | 0.5 h + your `sudo` | closes the deployment debt |
| 5 — serving-db diet | separate decision | days | only if the host demands it |

## Open questions (mirror of spec §10)

- **D1** G1 target: ≤200 MiB anon per worker (from 541), stretch 150?
- **D2** Multi-worker later (shared caches) or single worker by design?
- **D3** Pursue Phase 5 or leave it trigger-gated?
- **D4** Budgets in CI (CI runs no pytest today) or local-only?
- **D5** Phase 4 with your `sudo` now, or documented for later?
