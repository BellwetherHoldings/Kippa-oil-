"""Tests for Wave 4: monitoring, observability, automation, security, API."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.monitoring.engine as monitoring_mod
from src.engines.automation.runner import STEPS, run_workflow
from src.monitoring.engine import MonitoringEngine
from src.security.audit import run_audit


# ---------------------------------------------------------------------------
# Security audit — must pass on the real repository
# ---------------------------------------------------------------------------

def test_security_audit_passes_on_repo():
    report = run_audit()
    assert report["status"] == "pass", report["findings"]
    assert report["tracked_files_scanned"] > 30


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

def test_monitoring_flags_missing_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(monitoring_mod, "RUN_LOG", tmp_path / "none.jsonl")
    result = MonitoringEngine().run()
    assert result.ok
    d = result.data
    assert d["health"] == "degraded"          # everything missing = high alerts
    assert d["alert_counts"]["high"] >= len(monitoring_mod.ARTIFACT_MAX_AGE)


def test_monitoring_flags_failed_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(monitoring_mod, "RUN_LOG", tmp_path / "none.jsonl")
    (tmp_path / "composite_signal.json").write_text(json.dumps({
        "status": "failed", "error": "boom",
        "finished_utc": "2026-07-14T00:00:00+00:00",
    }))
    result = MonitoringEngine().run()
    assert result.data["health"] == "critical"
    assert any(a["tier"] == "critical" for a in result.data["alerts"])


# ---------------------------------------------------------------------------
# Automation
# ---------------------------------------------------------------------------

def test_workflow_definitions_reference_known_steps():
    config = json.loads(
        (REPO_ROOT / "config" / "workflows.json").read_text())["workflows"]
    for name, wf in config.items():
        unknown = [s for s in wf["steps"] if s not in STEPS]
        assert not unknown, f"workflow {name} has unknown steps {unknown}"


def test_unknown_workflow_raises():
    with pytest.raises(ValueError, match="Unknown workflow"):
        run_workflow("does_not_exist")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import src.api.app as api
    import src.engines.base as base
    monkeypatch.setenv("PLATFORM_API_KEYS", "test-key-1,test-key-2")
    # serve the real published artifacts, not the isolated test dir
    monkeypatch.setattr(base, "DATA_DIR", REPO_ROOT / "data")
    monkeypatch.setattr(api, "API_AUDIT_LOG", tmp_path / "api_audit.jsonl")
    api._rate.clear()
    return TestClient(api.app)


def test_api_health_is_public(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["request_id"]
    assert body["timestamp"]


def test_api_rejects_missing_key(client):
    r = client.get("/api/v1/signal")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_api_accepts_valid_key(client):
    r = client.get("/api/v1/resources", headers={"X-API-Key": "test-key-2"})
    assert r.status_code == 200
    assert "signal" in r.json()["payload"]["resources"]


def test_api_serves_published_artifact(client):
    r = client.get("/api/v1/signal", headers={"X-API-Key": "test-key-1"})
    assert r.status_code == 200
    payload = r.json()["payload"]
    assert payload["engine"] == "composite_signal"
    assert "composite_score" in payload["data"]
    assert payload["evidence"]


def test_api_unknown_resource_404(client):
    r = client.get("/api/v1/nonsense", headers={"X-API-Key": "test-key-1"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_api_rate_limit(client, monkeypatch):
    import src.api.app as api
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MIN", 3)
    for _ in range(3):
        assert client.get("/api/v1/resources",
                          headers={"X-API-Key": "test-key-1"}).status_code == 200
    r = client.get("/api/v1/resources", headers={"X-API-Key": "test-key-1"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
