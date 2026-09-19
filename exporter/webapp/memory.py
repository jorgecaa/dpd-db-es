"""Serving-memory facts for the webapp's `/status` dashboard and `/metrics` gauges.

One module owns the numbers so the dashboard, the metrics and the tests cannot
drift apart, and so the budget lives in one place instead of being a literal in a
route. Everything here is read-only, and degrades to `None` (or the documented
budget) when `/proc` or cgroup v2 is not available, which is what makes it testable
off a Linux host and in a container.

Budgets come from `kamma/tech.md` and are measured facts, not guesses: 124 MiB anon
idle and 238.8 MiB at steady state on 2026-09-18, after the preloads stopped
materialising whole rows and the search stopped scanning `lookup`. The health
thresholds are therefore relative to the budget rather than to a hardcoded 4 GB:
green inside the budget, orange up to 1.5x, red beyond.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from prometheus_client import Gauge

#: Anonymous RSS a freshly started worker is expected to stay under.
DOCUMENTED_IDLE_BUDGET_MIB = 200.0
#: Anonymous RSS a worker settles at under diverse traffic (measured, 1,000-request soak).
DOCUMENTED_STEADY_BUDGET_MIB = 260.0

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_STATUS = Path("/proc/self/status")
PROC_CGROUP = Path("/proc/self/cgroup")

MEMORY_GAUGES: dict[str, Gauge] = {
    "rss_mib": Gauge("dpd_webapp_rss_mib", "Resident set size of the webapp process"),
    "anon_mib": Gauge("dpd_webapp_anon_mib", "Anonymous (non-reclaimable) memory"),
    "file_mib": Gauge("dpd_webapp_file_mib", "File-backed memory (shared page cache)"),
    "cgroup_current_mib": Gauge(
        "dpd_webapp_cgroup_current_mib", "cgroup memory.current"
    ),
    "cgroup_peak_mib": Gauge("dpd_webapp_cgroup_peak_mib", "cgroup memory.peak"),
    "budget_mib": Gauge(
        "dpd_webapp_memory_budget_mib", "Budget the health colour is relative to"
    ),
    "percent_of_budget": Gauge(
        "dpd_webapp_memory_percent_of_budget", "Used share of the budget"
    ),
}

CACHE_GAUGE = Gauge(
    "dpd_webapp_cache_entries",
    "Entries held by an in-process cache",
    ["cache"],
)


@dataclass(frozen=True)
class MemoryFacts:
    """Everything the dashboard and the gauges need, already formatted in MiB."""

    rss_mib: float | None
    anon_mib: float | None
    file_mib: float | None
    cgroup_current_mib: float | None
    cgroup_peak_mib: float | None
    limit_mib: float | None
    processes: int | None
    budget_mib: float
    budget_source: str
    percent_of_budget: float
    health: str


def _read_first_float(path: Path, keys: tuple[str, ...]) -> float | None:
    """The first matching `key: <number>` in a status-style file, in MiB."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        for key in keys:
            if line.startswith(f"{key}:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) / 1024
    return None


def cgroup_dir(
    proc_cgroup: Path = PROC_CGROUP, cgroup_root: Path = CGROUP_ROOT
) -> Path | None:
    """This process's cgroup v2 directory, or None when there is no cgroup v2."""
    try:
        content = proc_cgroup.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        parts = line.split("::", 1)
        if len(parts) == 2:
            candidate = cgroup_root / parts[1].strip().lstrip("/")
            if candidate.is_dir():
                return candidate
    return None


def _read_cgroup_number(directory: Path | None, name: str) -> float | None:
    if directory is None:
        return None
    try:
        raw = (directory / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw) / 1048576
    except ValueError:
        return None


def _read_cgroup_stat(directory: Path | None, key: str) -> float | None:
    if directory is None:
        return None
    try:
        lines = (directory / "memory.stat").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[0] == key:
            try:
                return int(parts[1]) / 1048576
            except ValueError:
                return None
    return None


def _read_process_count(directory: Path | None) -> int | None:
    if directory is None:
        return None
    try:
        lines = (directory / "cgroup.procs").read_text(encoding="utf-8").split()
    except OSError:
        return None
    return len(lines)


def health_for(percent: float) -> str:
    """Budget-relative health: inside it, up to 1.5x, beyond."""
    if percent >= 150:
        return "red"
    if percent >= 100:
        return "orange"
    return "green"


def read_memory_facts(
    proc_status: Path = PROC_STATUS,
    proc_cgroup: Path = PROC_CGROUP,
    cgroup_root: Path = CGROUP_ROOT,
) -> MemoryFacts:
    """Read the process and cgroup numbers, resolving the budget."""
    directory = cgroup_dir(proc_cgroup, cgroup_root)
    rss = _read_first_float(proc_status, ("VmRSS",))
    anon = _read_first_float(proc_status, ("RssAnon",))
    file_backed = _read_first_float(proc_status, ("RssFile",))
    current = _read_cgroup_number(directory, "memory.current")
    peak = _read_cgroup_number(directory, "memory.peak")
    limit = _read_cgroup_number(directory, "memory.max")

    # The dashboard counts anonymous memory: file-backed pages are the shared page
    # cache, which the kernel reclaims on demand and which no budget should chase.
    used = _read_cgroup_stat(directory, "anon") or anon or rss
    if limit is not None:
        budget, source = limit, "cgroup memory.max"
    else:
        budget, source = DOCUMENTED_STEADY_BUDGET_MIB, "documented steady-state budget"
    percent = (used / budget * 100) if (used is not None and budget) else 0.0

    return MemoryFacts(
        rss_mib=rss,
        anon_mib=anon,
        file_mib=file_backed,
        cgroup_current_mib=current,
        cgroup_peak_mib=peak,
        limit_mib=limit,
        processes=_read_process_count(directory),
        budget_mib=budget,
        budget_source=source,
        percent_of_budget=percent,
        health=health_for(percent),
    )


def cache_facts(extra: dict[str, int] | None = None) -> dict[str, int]:
    """Entries held by every bounded in-process cache (see the tech.md inventory)."""
    from tools import spanish
    from tools.cache_load import (
        load_audio_dict,
        load_audio_set,
        load_cf_set,
        load_idioms_set,
        load_sutta_info_set,
        load_tpr_codes_set,
    )

    facts: dict[str, int] = {
        "audio_dict": int(load_audio_dict.cache_info().currsize),
        "audio_set": int(load_audio_set.cache_info().currsize),
        "cf_set": int(load_cf_set.cache_info().currsize),
        "idioms_set": int(load_idioms_set.cache_info().currsize),
        "sutta_info_set": int(load_sutta_info_set.cache_info().currsize),
        "tpr_codes_set": int(load_tpr_codes_set.cache_info().currsize),
    }
    facts.update(spanish.cache_sizes())
    if extra:
        facts.update(extra)
    return facts


def publish_memory_gauges() -> None:
    """Refresh the `/metrics` gauges. Called after every request."""
    facts = read_memory_facts()
    MEMORY_GAUGES["rss_mib"].set(facts.rss_mib or 0)
    MEMORY_GAUGES["anon_mib"].set(facts.anon_mib or 0)
    MEMORY_GAUGES["file_mib"].set(facts.file_mib or 0)
    MEMORY_GAUGES["cgroup_current_mib"].set(facts.cgroup_current_mib or 0)
    MEMORY_GAUGES["cgroup_peak_mib"].set(facts.cgroup_peak_mib or 0)
    MEMORY_GAUGES["budget_mib"].set(facts.budget_mib)
    MEMORY_GAUGES["percent_of_budget"].set(facts.percent_of_budget)


def publish_cache_gauges(extra: dict[str, int] | None = None) -> None:
    """Refresh the per-cache entry-count gauges. Called after every request."""
    for name, size in cache_facts(extra).items():
        CACHE_GAUGE.labels(cache=name).set(size)
