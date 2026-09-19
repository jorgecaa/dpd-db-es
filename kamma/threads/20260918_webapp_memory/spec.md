# Spec: Webapp Serving Memory — de-materialize the startup preloads, bound the caches, publish a budget

**Thread:** 20260918_webapp_memory
**Date:** 2026-09-18
**Status:** Proposed — awaiting approval of decisions D1–D5; no production code changed by this thread yet
**Related:** `kamma/tech.md` (constraints + cache inventory), `kamma/archive/20260710_gui2_perf_and_testing` (gui2, different surface), upstream probes that already exist in `tests/exporter/webapp/` (`test_mem_preloads.py`, `test_preloads_perf.py`, `test_preloads.py`, `test_webapp_observability.py`).

> **Self-contained.** This spec plus `plan.md` carry everything needed to execute.
> All numbers were measured on 2026-09-18 on the machine that serves the dictionary
> (`dpd-webapp.service`, **1** uvicorn worker, `dpd.db` v0.4.20260728, 2.13 GiB).
> Units are binary (MiB/GiB) unless stated.

## 1. Why this thread exists

Serving an 89,280-headword dictionary costs the webapp **541 MiB of anonymous
resident memory**, while the data it actually keeps afterwards is **~12 MiB**.
The gap is not the dataset and not the file cache: it is *how the process reads
it* — the whole `dpd_headwords` table turned into ORM entities **twice** at
startup, then discarded without the allocator ever returning the pages. The
process pays for work it threw away, for as long as it runs.

This is a pure efficiency defect: same output, same behaviour, ~5× the resident
memory it needs, no bound on any in-process cache, and a hardcoded budget in
`/status` that nothing enforces.

**Context — the other half of the footprint (out of scope here).** `dpd.db` is
2.13 GiB, and most of it is *derived data materialised at build time*:
`freq_html` 659 MB + `inflections_html` 251 MB of **stored rendered HTML**
(910 MB), ~190 MB of transliterations of the roman form duplicated between
`dpd_headwords` and `lookup`, a 419 MB `lookup` for 1.28 M inflected forms, and
`bold_definitions` at 181 MB for 369,147 commentarial definitions — while all the
human-written English text (`meaning_1`, `example_1`, `commentary`, …) is only
~20–25 MB. The release compresses 2.13 GiB to 161 MiB (~13.5:1), which is the
tell. Attacking that is Phase 5 (trigger-gated, separate thread); this thread
only stops the *process* from making copies of what it reads.

## 2. Host change already made (2026-09-18, context for this thread)

- The service used to copy `dpd.db` into `/dev/shm` on every start
  (`deploy/preload-db.sh` as `ExecStartPre`, ~37 s, 2.13 GiB of non-reclaimable
  memory) and switch `dpd.db` to a symlink into RAM.
- **Disabled that day**: `deploy/preload-db.sh` is now a documented no-op
  (`exit 0`); the original is kept as `deploy/preload-db.sh.orig`; `dpd.db` is a
  real file on disk again; the stale `/dev/shm` copy was removed.
  Measured effect: cgroup **3.52 GiB → 544 MiB**, start-to-serving
  **~56 s → 10 s**.
- The systemd unit was **not** edited (no root was available): it still calls the
  no-op script and keeps `ReadWritePaths=… /dev/shm` and `PrivateDevices=no`.
  Phase 4 covers the root-side cleanup.
- Revert is one `mv` + restart (documented in the script header).

## 3. Baseline (measured; commands in `plan.md` §0.4)

**Table 1 — where the memory is (idle, one worker)**

| Metric | Value | Source |
|---|---|---|
| cgroup `memory.current` | 544 MiB | `/sys/fs/cgroup/system.slice/dpd-webapp.service/memory.current` |
| … `anon` | 541 MiB | `…/memory.stat` |
| … `file` (page cache charged) | 0.7 MiB | `…/memory.stat` |
| … `shmem` | 0.3 MiB | `…/memory.stat` |
| cgroup `memory.peak` | 669 MiB | `…/memory.peak` |
| `VmRSS` | 556 MiB | `/proc/<pid>/status` |
| — `RssAnon` / `RssFile` | 532 / 24 MiB | idem |
| `/status` "App Memory" | 555.89 MB of a **hardcoded 4 GB** | `curl 127.0.0.1:8080/status` |
| Startup (start → "Application startup complete") | 10 s | journal |
| Per-query read cost | **419 MiB read** per `/search_json`, 112 KiB from disk | `/proc/<pid>/io` `rchar`/`read_bytes` before/after |

**Table 2 — attribution (separate process, same code path, `VmRSS` deltas)**

| Step | RSS |
|---|---|
| interpreter + imports (SQLAlchemy, models, tools) | 61 MiB |
| `SELECT lemma_1` for all 89,280 rows | **86 MiB** |
| full ORM entities (what `preloads.py` does today) | **635 MiB** |
| after `del` + `malloc_trim(0)` | **73 MiB** |

**Table 3 — what the app actually keeps**

| Structure | Size | Note |
|---|---|---|
| `headwords_clean_set` | 154,690 strings | lemmas + `lookup_key` with `epd != ''` (79,242 rows) |
| `ascii_to_unicode_dict` | 74,749 keys | ascii → unicode forms |
| `load_audio_dict()` | 75,591 entries | from `audio/db/dpd_audio_index.tsv` |
| Spanish sidecar `meanings_es` | 86,002 rows | `dpd_es.db`, 11 MB on disk |
| **live data after trim** | **~12 MiB** | the rest of Table 1 is allocator retention |

## 4. Root causes (each with its carrier)

- **R1** `exporter/webapp/preloads.py::make_headwords_clean_set` loads every
  `DpdHeadword` entity (6 heavy columns deferred) to derive
  `lemma_clean = re.sub(r" \d.*$", "", lemma_1)` (`db/models.py:1373`) — one string
  per row.
- **R2** `…::make_ascii_to_unicode_dict` repeats the same full-entity pass; it only
  needs `lemma_clean` and `lemma_2`.
- **R3** Allocator retention: the entities are freed but glibc arenas are not
  returned to the kernel (proof: 635 → 73 MiB after `malloc_trim`). The cost
  outlives the work, so it is paid until the next restart.
- **R4** `db/db_helpers.py::make_roots_count_dict` is already a column aggregation
  (`root_key, count()`), i.e. correct — recorded so nobody "optimizes" it again.
- **R5** Per-request transients: `exporter/webapp/main.py:222` builds a
  `BoldDefinitionsSearchManager()` **per `/bd_search` request** (fresh session,
  regex over 369,147 `bold_definitions` rows); `/search_json` materialises entity
  rows at ~419 MiB read per query.
- **R6** Unbounded in-process cache: `tools/spanish.py::_combos` (lemma →
  overlaid `meaning_combo`, no cap, never released). `history_list` is capped at
  250 — that one is fine.
- **R7** Every extra uvicorn worker repeats R1–R6 (today: 1 worker).
- **R8** The budget in `/status` (`exporter/webapp/main.py:470`) is a hardcoded
  4 GB, not derived from the deployment, and nothing fails when the process grows.

## 5. Goals (acceptance criteria)

| # | Goal | Baseline | Target | Proof |
|---|---|---|---|---|
| G1 | Anonymous memory per worker, idle | 541 MiB | **≤200 MiB** (stretch 150) | cgroup `memory.stat` |
| G2 | Start → first served request | 10 s | **≤10 s** (no regression) | journal timestamps |
| G3 | Growth under load | unknown | **≤5 %** RSS drift over 1,000 mixed requests | soak harness |
| G4 | Cache bounds | 1 unbounded | every cache bounded + inventoried | `tech.md` inventory + test |
| G5 | Budget visibility | hardcoded, unenforced | visible in `/status` and `/metrics`, asserted in tests | test + canary |
| G6 | Latency | baseline p50/p95 | **no regression beyond noise** | workload benchmark |

## 6. Non-goals

- No change to the `dpd.db` schema, contents or the build pipeline; no db
  regeneration (it ripples into 15 exporters and gui2).
- No change to Spanish overlay semantics (read-only overlay, fingerprints,
  English fallback).
- **No new caching layer** — adding caches is the disease, not the cure; no
  Redis/memcached/external store.
- No optimisation of the *build/export* memory (20 GB builds, ~29 GB PDF) — that
  is a different thread.
- No further change to the deployed data path (the `/dev/shm` removal is done).
- Multi-worker scaling is explicitly deferred, not silently assumed.

## 7. Constraints (repo rules that shape the plan)

- `AGENTS.md` → *Performance Work*: before implementing each optimisation,
  re-derive its numbers from the evidence and benchmark the claimed mechanism;
  use a throwaway copy of `dpd.db` for anything that writes. This thread is
  read-only, so benchmarks run against the live db without writing.
- Pre-commit gate: `ruff check --fix` + `ruff format` + `pyright` on every touched
  file, plus `pytest`; repo-wide `just typecheck` (pyrefly) before finishing.
- CI runs **no** pytest (`CLAUDE.md`): local verification is the only gate, so the
  guardrails must be runnable by hand and cheap.
- kamma concurrency: many threads share one tree — never `git stash` /
  `checkout --` / `reset --hard`; stage explicit file lists; **commits only when
  the user asks** (AGENTS overrides the workflow's commit steps).
- `kamma/tech.md` is updated **before** implementation when tools, constraints or
  working assumptions change.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Derived sets feed search → silent behaviour drift | Golden-output oracle captured before any change (plan §0.2) + frozen-fixture parity tests in the style of `test_preloads.py` |
| Over-fitting to this machine | Report cgroup **and** `/proc` numbers; require the same direction in the separate-process harness |
| `malloc_trim` is glibc/Linux-only | Guarded (`sys.platform` + `hasattr`); measured separately; treated as mitigation, never as the fix |
| Untracked host scripts (`deploy/`) creating hidden coupling | Documented in `tech.md`; the thread's DoD does not depend on the host state |
| Regression creeping back (the exact R1 pattern) | Mechanism-lock test asserting the statement selects only the intended columns |

## 9. Rollout, canary, rollback

- **Canary**: run the changed code on a second port (8081) against the same db
  (read-only) and diff the golden oracle before touching the production service.
- **Deploy**: `systemctl restart dpd-webapp.service` (root; the user runs it),
  then compare `/status` before/after.
- **Rollback**: `git revert <commit>` + restart. Nothing in this thread writes to
  `dpd.db`, `dpd_es.db` or exporter outputs, so rollback cannot corrupt data.
- One mechanism per commit, each carrying its before/after numbers.

## 10. Decisions needed from the user

- **D1** Target for G1 per worker: **≤200 MiB** anon idle (from 541), stretch 150?
- **D2** Is multi-worker scaling in scope later (would need shared caches), or is
  one worker the design assumption?
- **D3** If app-side work fits the target host budget, do we still pursue the
  serving-db diet (plan Phase 5), or leave it as a documented trigger?
- **D4** Budgets enforced in CI (would require adding pytest to a workflow, which
  CI currently does not run) or local-only?
- **D5** Phase 4 needs root: do it now while it is fresh, or leave it documented?
