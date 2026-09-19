"""Tests for `exporter/webapp/memory.py`, the numbers behind `/status` and `/metrics`.

Everything runs off fake `/proc` and cgroup trees, so the parsing, the budget
resolution and the health thresholds are pinned without depending on the host's
kernel or on this machine's cgroup layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from exporter.webapp import memory

PROC_STATUS = """Name:\tuvicorn
VmRSS:\t  151744 kB
RssAnon:\t 127496 kB
RssFile:\t  24248 kB
"""

MEMORY_STAT = """anon 140353536
file 303104
slab 359640
"""


def _fake(tmp_path: Path, *, limit: str | None = "max") -> tuple[Path, Path, Path]:
    proc_status = tmp_path / "status"
    proc_status.write_text(PROC_STATUS, encoding="utf-8")
    cgroup = tmp_path / "cgroup" / "system.slice" / "dpd-webapp.service"
    cgroup.mkdir(parents=True)
    (cgroup / "memory.current").write_text(str(142229504), encoding="utf-8")
    (cgroup / "memory.peak").write_text(str(149811200), encoding="utf-8")
    if limit is not None:
        (cgroup / "memory.max").write_text(limit, encoding="utf-8")
    (cgroup / "memory.stat").write_text(MEMORY_STAT, encoding="utf-8")
    (cgroup / "cgroup.procs").write_text("111\n222\n", encoding="utf-8")
    proc_cgroup = tmp_path / "cgroup-list"
    proc_cgroup.write_text("0::/system.slice/dpd-webapp.service\n", encoding="utf-8")
    return proc_status, proc_cgroup, tmp_path / "cgroup"


def test_reads_the_process_numbers(tmp_path: Path) -> None:
    proc_status, proc_cgroup, root = _fake(tmp_path)
    facts = memory.read_memory_facts(proc_status, proc_cgroup, root)
    assert facts.rss_mib == pytest.approx(148.2, abs=0.1)
    assert facts.anon_mib == pytest.approx(124.5, abs=0.1)
    assert facts.file_mib == pytest.approx(23.7, abs=0.1)
    assert facts.processes == 2


def test_reads_the_cgroup_numbers(tmp_path: Path) -> None:
    proc_status, proc_cgroup, root = _fake(tmp_path)
    facts = memory.read_memory_facts(proc_status, proc_cgroup, root)
    assert facts.cgroup_current_mib == pytest.approx(135.6, abs=0.1)
    assert facts.cgroup_peak_mib == pytest.approx(142.9, abs=0.1)


def test_unlimited_cgroup_falls_back_to_the_documented_budget(tmp_path: Path) -> None:
    proc_status, proc_cgroup, root = _fake(tmp_path, limit="max")
    facts = memory.read_memory_facts(proc_status, proc_cgroup, root)
    assert facts.limit_mib is None
    assert facts.budget_mib == memory.DOCUMENTED_STEADY_BUDGET_MIB
    assert facts.budget_source == "documented steady-state budget"


def test_a_real_cgroup_limit_becomes_the_budget(tmp_path: Path) -> None:
    proc_status, proc_cgroup, root = _fake(tmp_path, limit=str(1024 * 1048576))
    facts = memory.read_memory_facts(proc_status, proc_cgroup, root)
    assert facts.limit_mib == pytest.approx(1024.0)
    assert facts.budget_mib == pytest.approx(1024.0)
    assert facts.budget_source == "cgroup memory.max"
    # 140353536 B of cgroup `anon` (133.9 MiB) against a 1 GiB limit
    assert facts.percent_of_budget == pytest.approx(13.1, abs=0.2)


def test_missing_proc_files_degrade_instead_of_raising(tmp_path: Path) -> None:
    facts = memory.read_memory_facts(
        tmp_path / "absent", tmp_path / "absent", tmp_path / "absent"
    )
    assert facts.rss_mib is None
    assert facts.cgroup_current_mib is None
    assert facts.processes is None
    assert facts.budget_mib == memory.DOCUMENTED_STEADY_BUDGET_MIB
    assert facts.percent_of_budget == 0.0
    assert facts.health == "green"


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (0.0, "green"),
        (99.9, "green"),
        (100.0, "orange"),
        (149.9, "orange"),
        (150.0, "red"),
    ],
)
def test_health_thresholds(percent: float, expected: str) -> None:
    assert memory.health_for(percent) == expected


def test_cache_facts_covers_the_published_inventory() -> None:
    facts = memory.cache_facts({"history": 3})
    expected = {
        "audio_dict",
        "audio_set",
        "cf_set",
        "idioms_set",
        "sutta_info_set",
        "tpr_codes_set",
        "sidecar_meanings",
        "sidecar_roots",
        "sidecar_sanskrit",
        "sidecar_notes",
        "sidecar_sutta_summaries",
        "sidecar_ui",
        "combos",
        "inflection_html",
        "history",
    }
    assert expected <= set(facts)
    assert all(isinstance(size, int) for size in facts.values())
    assert facts["history"] == 3


def test_publishers_set_the_gauges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        memory,
        "read_memory_facts",
        lambda: memory.MemoryFacts(
            rss_mib=148.2,
            anon_mib=124.5,
            file_mib=23.7,
            cgroup_current_mib=135.6,
            cgroup_peak_mib=142.9,
            limit_mib=None,
            processes=1,
            budget_mib=260.0,
            budget_source="documented steady-state budget",
            percent_of_budget=47.9,
            health="green",
        ),
    )
    monkeypatch.setattr(memory, "cache_facts", lambda extra=None: {"combos": 7})

    memory.publish_memory_gauges()
    memory.publish_cache_gauges()

    assert REGISTRY.get_sample_value("dpd_webapp_anon_mib") == pytest.approx(124.5)
    assert REGISTRY.get_sample_value("dpd_webapp_memory_budget_mib") == pytest.approx(
        260.0
    )
    assert REGISTRY.get_sample_value(
        "dpd_webapp_cache_entries", {"cache": "combos"}
    ) == pytest.approx(7)
