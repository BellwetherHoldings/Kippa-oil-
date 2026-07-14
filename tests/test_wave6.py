"""Tests for Wave 6: anomalies, full risk coverage, sensitivity, horizons, RBAC."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.engines.risk.engine as risk_mod
import src.monitoring.engine as monitoring_mod
from src.engines.risk.engine import RiskEngine, UNCOVERED_CATEGORIES
from src.monitoring.engine import MonitoringEngine


# ---------------------------------------------------------------------------
# Monitoring anomaly detection
# ---------------------------------------------------------------------------

def test_anomaly_detected_on_absurd_inventory_change(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(monitoring_mod, "RUN_LOG", tmp_path / "none.jsonl")
    periods = pd.date_range("2024-01-05", periods=100, freq="W-FRI")
    changes = [1000.0] * 99 + [500_000.0]          # 500M bbl swing = broken
    pd.DataFrame({"period": periods, "value": 400_000,
                  "weekly_change": changes}).to_csv(
        tmp_path / "crude_inventories.csv", index=False)
    result = MonitoringEngine().run()
    assert any("outlier" in a["message"] for a in result.data["alerts"])


def test_no_anomaly_on_normal_data(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(monitoring_mod, "RUN_LOG", tmp_path / "none.jsonl")
    periods = pd.date_range("2024-01-05", periods=100, freq="W-FRI")
    pd.DataFrame({"period": periods, "value": 400_000,
                  "weekly_change": [(-1) ** i * 2000 for i in range(100)]}
                 ).to_csv(tmp_path / "crude_inventories.csv", index=False)
    result = MonitoringEngine().run()
    assert not any("outlier" in a["message"] for a in result.data["alerts"])


# ---------------------------------------------------------------------------
# Risk: 8 of 9 categories
# ---------------------------------------------------------------------------

def _publish(tmp_path, name, data):
    (tmp_path / f"{name}.json").write_text(json.dumps({
        "engine": name, "status": "success", "run_id": "t", "version": "1.0",
        "started_utc": "", "finished_utc": "", "duration_ms": 0,
        "data": data, "evidence": [], "warnings": [], "error": None,
    }))


def test_risk_covers_eight_categories(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_mod, "DATA_DIR", tmp_path)
    _publish(tmp_path, "geopolitical_risk", {
        "risk_score": 80.0, "risk_level": "severe",
        "chokepoints_disrupted": ["strait_of_hormuz"]})
    _publish(tmp_path, "supply_chain_stress", {
        "stress_score": 60.0, "stress_level": "elevated",
        "components": [{"metric": "spr_level", "percentile_3y": 0.05}]})
    _publish(tmp_path, "price_momentum", {
        "volatility_20d_annualized": 0.30, "return_20d": -0.1,
        "spike_flag": False})
    _publish(tmp_path, "composite_signal", {
        "composite_score": 0.3, "label": "bullish",
        "components": [{"component": "x", "confidence": 0.9,
                        "staleness_days": 1}]})
    _publish(tmp_path, "macro_conditions", {
        "macro_signal": -0.2, "macro_label": "headwind",
        "indicators_scored": 3,
        "components": [{"indicator": "industrial_production",
                        "signal": -0.4, "detail": "6m growth -2%"}]})
    _publish(tmp_path, "monitoring_report", {
        "health": "degraded", "alerts": [1, 2]})
    _publish(tmp_path, "security_audit", {
        "status": "pass", "tracked_files_scanned": 80, "findings": []})

    result = RiskEngine().run()
    assert result.ok
    cats = {a["category"] for a in result.data["assessments"]}
    assert cats == {"geopolitical", "supply", "market", "economic",
                    "demand", "operational", "cybersecurity", "model_data"}
    assert UNCOVERED_CATEGORIES == ["regulatory"]


# ---------------------------------------------------------------------------
# Simulation sensitivity & forecast horizons (published artifacts)
# ---------------------------------------------------------------------------

def test_simulation_publishes_sensitivity():
    art = json.loads((REPO_ROOT / "data" / "simulation_results.json")
                     .read_text())
    sens = art["data"]["sensitivity"]
    assert sens, "sensitivity section missing"
    for entry in sens:
        assert entry["dominant_assumption"] in entry["parameters"]


def test_forecast_publishes_both_horizons():
    art = json.loads((REPO_ROOT / "data" / "price_forecast.json").read_text())
    horizons = art["data"]["horizons"]
    assert set(horizons) == {"fwd_ret_1w", "fwd_ret_4w"}
    for h in horizons.values():
        lo, hi = h["interval_95"]
        assert lo <= h["point_forecast"] <= hi


# ---------------------------------------------------------------------------
# API RBAC
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import src.api.app as api
    monkeypatch.setenv("PLATFORM_API_KEYS", "reader-key,admin-key:admin")
    monkeypatch.setattr(api, "API_AUDIT_LOG", tmp_path / "api_audit.jsonl")
    api._rate.clear()
    return TestClient(api.app)


def test_readonly_key_denied_admin_resource(client):
    r = client.get("/api/v1/monitoring", headers={"X-API-Key": "reader-key"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_admin_key_allowed_admin_resource(client):
    r = client.get("/api/v1/monitoring", headers={"X-API-Key": "admin-key"})
    assert r.status_code in (200, 404)      # 404 only if never published
    assert r.status_code != 403


def test_api_audit_log_records_fingerprint_not_key(client, tmp_path):
    client.get("/api/v1/resources", headers={"X-API-Key": "reader-key"})
    log = (tmp_path / "api_audit.jsonl").read_text()
    assert "reader-key" not in log          # never the raw key
    assert "key_fingerprint" in log
