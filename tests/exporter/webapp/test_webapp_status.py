from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from exporter.webapp.main import app


def test_status_endpoint() -> None:
    """The dashboard renders the memory facts and the budget, not a hardcoded limit."""
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    assert "DPD Webapp Status" in response.text
    # memory facts (added 2026-09-18, kamma/threads/20260918_webapp_memory task 3.1)
    assert "anonymous" in response.text
    assert "budget" in response.text
    assert "cgroup Memory" in response.text
    assert "In-process caches" in response.text
    assert "4GB" not in response.text  # the hardcoded limit is gone
    print("PASS: /status dashboard is live and readable.")


def test_metrics_endpoint_exposes_the_memory_gauges() -> None:
    """A scrape must carry the serving-memory series, not only the default set."""
    client = TestClient(app)
    client.get("/")  # the publishers run after each request
    response = client.get("/metrics")
    assert response.status_code == 200
    for series in (
        "dpd_webapp_anon_mib",
        "dpd_webapp_cgroup_peak_mib",
        "dpd_webapp_memory_budget_mib",
        "dpd_webapp_cache_entries",
    ):
        assert series in response.text, series
    assert REGISTRY.get_sample_value("dpd_webapp_memory_budget_mib") is not None
    print("PASS: /metrics exposes the serving-memory gauges.")
