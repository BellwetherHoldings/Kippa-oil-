"""Tests for Wave 3: feature panel, backtesting, forecast, simulation, strategy."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.engines.strategy.engine as strategy_mod
from src.engines.backtesting.engine import _verdict
from src.engines.strategy.engine import SIZE_BY_TIER, StrategyEngine


# ---------------------------------------------------------------------------
# Backtesting verdict thresholds
# ---------------------------------------------------------------------------

def test_verdict_thresholds():
    assert _verdict(0.15) == "predictive"
    assert _verdict(-0.12) == "predictive"     # magnitude, not sign
    assert _verdict(0.05) == "weak"
    assert _verdict(0.01) == "not predictive"


# ---------------------------------------------------------------------------
# Feature panel has no lookahead
# ---------------------------------------------------------------------------

def test_panel_entry_dates_never_precede_period():
    panel = pd.read_csv(REPO_ROOT / "data" / "feature_panel.csv",
                        parse_dates=["period", "entry_date"])
    assert (panel["entry_date"] >= panel["period"]).all()


def test_panel_has_required_columns():
    panel = pd.read_csv(REPO_ROOT / "data" / "feature_panel.csv")
    for col in ("surprise_z", "momentum_5d", "vol_20d",
                "fwd_ret_1w", "fwd_ret_4w"):
        assert col in panel.columns


# ---------------------------------------------------------------------------
# Simulation scenario definitions
# ---------------------------------------------------------------------------

def test_scenarios_are_well_formed():
    scenarios = json.loads(
        (REPO_ROOT / "simulations" / "scenarios.json").read_text()
    )["scenarios"]
    ids = [s["id"] for s in scenarios]
    assert "baseline" in ids
    assert len(ids) == len(set(ids))
    for s in scenarios:
        assert s["vol_mult"] > 0
        assert s["rationale"], f"{s['id']} missing rationale"


# ---------------------------------------------------------------------------
# Strategy engine
# ---------------------------------------------------------------------------

def _publish(tmp_path: Path, name: str, data: dict):
    (tmp_path / f"{name}.json").write_text(json.dumps({
        "engine": name, "status": "success", "run_id": f"test-{name}",
        "version": "1.0", "started_utc": "", "finished_utc": "",
        "duration_ms": 0, "data": data, "evidence": [], "warnings": [],
        "error": None,
    }))


@pytest.fixture
def strategy_artifacts(tmp_path, monkeypatch):
    _publish(tmp_path, "composite_signal", {
        "composite_score": 0.4, "label": "bullish",
        "components": [{}, {}, {}],
    })
    _publish(tmp_path, "risk_assessment", {
        "overall_risk_score": 70.0, "overall_risk_level": "severe",
        "top_risk": "geopolitical",
        "assessments": [
            {"category": "geopolitical", "score": 90.0, "mitigation": "m1"},
            {"category": "supply", "score": 50.0, "mitigation": "m2"},
        ],
    })
    _publish(tmp_path, "signal_confidence", {
        "confidence_score": 80.0, "confidence_tier": "high",
        "interpretation": "act",
    })
    monkeypatch.setattr(strategy_mod, "DATA_DIR", tmp_path)
    return tmp_path


def test_strategy_long_when_bullish(strategy_artifacts):
    result = StrategyEngine().run()
    assert result.ok
    s = result.data["stance"]
    assert s["direction"] == "long"
    # severe risk must halve the size: high tier (0.75) × min(1, .4/.6) × 0.5
    assert s["suggested_size_0_1"] == pytest.approx(
        0.75 * (0.4 / 0.6) * 0.5, abs=0.01)
    assert result.data["invalidation_triggers"]
    assert result.data["human_oversight"]


def test_strategy_flat_on_weak_composite(strategy_artifacts, tmp_path):
    _publish(tmp_path, "composite_signal", {
        "composite_score": 0.05, "label": "neutral", "components": [{}],
    })
    result = StrategyEngine().run()
    assert result.data["stance"]["direction"] == "flat"


def test_strategy_refuses_without_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(strategy_mod, "DATA_DIR", tmp_path)
    result = StrategyEngine().run()
    assert not result.ok


def test_size_tiers_monotonic():
    order = ["very_low", "low", "moderate", "high", "very_high"]
    sizes = [SIZE_BY_TIER[t] for t in order]
    assert sizes == sorted(sizes)
