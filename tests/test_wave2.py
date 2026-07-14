"""Tests for Wave 2 engines: supply chain, sentiment, risk, confidence.

Network-dependent paths are not exercised here; these tests cover scoring
math, artifact plumbing, and classification tables using temp artifacts.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.engines.confidence.engine as confidence_mod
import src.engines.risk.engine as risk_mod
from src.engines.confidence.engine import ConfidenceEngine, _tier
from src.engines.risk.engine import RiskEngine, _risk_level
from src.intelligence.supply_chain.engine import METRICS, _stress_level


# ---------------------------------------------------------------------------
# Helpers — minimal published artifacts
# ---------------------------------------------------------------------------

def _publish(tmp_path: Path, name: str, data: dict, status: str = "success"):
    (tmp_path / f"{name}.json").write_text(json.dumps({
        "engine": name, "status": status, "run_id": "test", "version": "1.0",
        "started_utc": "", "finished_utc": "", "duration_ms": 0,
        "data": data, "evidence": [f"test evidence for {name}"],
        "warnings": [], "error": None,
    }))


def _standard_artifacts(tmp_path: Path):
    _publish(tmp_path, "geopolitical_risk", {
        "risk_score": 80.0, "risk_level": "severe",
        "chokepoints_disrupted": ["strait_of_hormuz"],
    })
    _publish(tmp_path, "supply_chain_stress", {
        "stress_score": 60.0, "stress_level": "elevated",
        "components": [{"metric": "spr_level", "percentile_3y": 0.05}],
    })
    _publish(tmp_path, "price_momentum", {
        "volatility_20d_annualized": 0.30, "return_20d": -0.10,
        "spike_flag": False,
    })
    _publish(tmp_path, "composite_signal", {
        "composite_score": 0.4, "label": "bullish", "as_of": "2026-07-14",
        "components": [
            {"component": "geopolitical_risk", "signal": 1.0,
             "confidence": 1.0, "effective_weight": 0.4, "staleness_days": 0},
            {"component": "inventory_surprise", "signal": -0.1,
             "confidence": 0.8, "effective_weight": 0.3, "staleness_days": 11},
            {"component": "price_momentum", "signal": 0.2,
             "confidence": 0.6, "effective_weight": 0.3, "staleness_days": 8},
        ],
    })


# ---------------------------------------------------------------------------
# Supply chain
# ---------------------------------------------------------------------------

def test_supply_metric_weights_sum_to_one():
    assert sum(m["weight"] for m in METRICS.values()) == pytest.approx(1.0)


def test_stress_levels():
    assert _stress_level(10) == "low"
    assert _stress_level(45) == "elevated"
    assert _stress_level(95) == "critical"


# ---------------------------------------------------------------------------
# Risk engine
# ---------------------------------------------------------------------------

def test_risk_aggregates_categories(tmp_path, monkeypatch):
    _standard_artifacts(tmp_path)
    monkeypatch.setattr(risk_mod, "DATA_DIR", tmp_path)
    result = RiskEngine().run()
    assert result.ok
    d = result.data
    assert 0 <= d["overall_risk_score"] <= 100
    cats = [a["category"] for a in d["assessments"]]
    assert set(cats) >= {"geopolitical", "supply", "market", "model_data"}
    # sorted worst-first, and worst risk dominates the blend
    scores = [a["score"] for a in d["assessments"]]
    assert scores == sorted(scores, reverse=True)
    assert d["overall_risk_score"] >= sum(scores) / len(scores)
    assert d["overall_risk_level"] == _risk_level(d["overall_risk_score"])


def test_risk_fails_without_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_mod, "DATA_DIR", tmp_path)
    result = RiskEngine().run()
    assert not result.ok      # refuses to assess from nothing


def test_risk_reports_uncovered_categories(tmp_path, monkeypatch):
    _standard_artifacts(tmp_path)
    monkeypatch.setattr(risk_mod, "DATA_DIR", tmp_path)
    result = RiskEngine().run()
    assert "cybersecurity" in result.data["categories_not_covered"]


# ---------------------------------------------------------------------------
# Confidence engine
# ---------------------------------------------------------------------------

def test_confidence_grades_composite(tmp_path, monkeypatch):
    _standard_artifacts(tmp_path)
    monkeypatch.setattr(confidence_mod, "DATA_DIR", tmp_path)
    result = ConfidenceEngine().run()
    assert result.ok
    d = result.data
    assert 0 <= d["confidence_score"] <= 100
    assert d["confidence_tier"] == _tier(d["confidence_score"])
    factors = {f["factor"] for f in d["factors"]}
    assert factors == {"data_quality", "agreement", "uncertainty", "coverage"}


def test_confidence_agreement_reflects_disagreement(tmp_path, monkeypatch):
    _standard_artifacts(tmp_path)
    monkeypatch.setattr(confidence_mod, "DATA_DIR", tmp_path)
    result = ConfidenceEngine().run()
    agreement = next(f for f in result.data["factors"]
                     if f["factor"] == "agreement")
    # geo (1.0 bullish, w .4) + momentum (0.2 bullish, w .3) agree with the
    # bullish composite; inventory (-0.1, w .3) does not → 0.7
    assert agreement["score"] == pytest.approx(0.7)


def test_confidence_requires_composite(tmp_path, monkeypatch):
    monkeypatch.setattr(confidence_mod, "DATA_DIR", tmp_path)
    result = ConfidenceEngine().run()
    assert not result.ok


def test_tier_table():
    assert _tier(95) == "very_high"
    assert _tier(80) == "high"
    assert _tier(65) == "moderate"
    assert _tier(45) == "low"
    assert _tier(10) == "very_low"
