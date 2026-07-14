"""Tests for Wave 5: data quality, weights config, drift detection."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.data.quality as quality_mod
from src.data.quality import DataQualityEngine
from src.engines.scoring.composite_signal import WEIGHTS, WEIGHTS_SOURCE


# ---------------------------------------------------------------------------
# Data quality engine
# ---------------------------------------------------------------------------

def _write_inventories(tmp_path: Path, values, periods=None):
    periods = periods or pd.date_range("2024-01-05", periods=len(values),
                                       freq="W-FRI")
    pd.DataFrame({"period": periods, "value": values,
                  "weekly_change": [0] * len(values)}).to_csv(
        tmp_path / "crude_inventories.csv", index=False)


def test_quality_clean_dataset_scores_high(tmp_path, monkeypatch):
    monkeypatch.setattr(quality_mod, "DATA_DIR", tmp_path)
    _write_inventories(tmp_path, [400_000 + i for i in range(60)])
    result = DataQualityEngine().run()
    assert result.ok
    ds = next(d for d in result.data["datasets"]
              if d["dataset"] == "crude_inventories.csv")
    assert ds["quality_score"] >= 95
    assert ds["lineage"]["source"].startswith("EIA")


def test_quality_flags_out_of_range_and_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(quality_mod, "DATA_DIR", tmp_path)
    periods = list(pd.date_range("2024-01-05", periods=59, freq="W-FRI"))
    periods.append(periods[-1])                      # duplicate date
    values = [400_000] * 59 + [9_000_000]            # absurd value
    _write_inventories(tmp_path, values, periods)
    result = DataQualityEngine().run()
    ds = next(d for d in result.data["datasets"]
              if d["dataset"] == "crude_inventories.csv")
    assert ds["quality_score"] < 95
    assert any("duplicate" in i for i in ds["issues"])
    assert any("outside" in i for i in ds["issues"])


def test_quality_missing_file_scores_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(quality_mod, "DATA_DIR", tmp_path)
    result = DataQualityEngine().run()
    assert all(d["quality_score"] == 0 for d in result.data["datasets"])
    assert result.data["overall_quality"] == 0


# ---------------------------------------------------------------------------
# Evidence-based weights
# ---------------------------------------------------------------------------

def test_weights_load_from_config_and_sum_to_one():
    assert "config/weights.json" in WEIGHTS_SOURCE
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_weights_reflect_backtest_evidence():
    # backtest: inventory predictive, momentum not → weights must rank so
    assert WEIGHTS["inventory_surprise"] > WEIGHTS["price_momentum"]


def test_weights_config_carries_evidence_and_changelog():
    cfg = json.loads((REPO_ROOT / "config" / "weights.json").read_text())
    assert cfg["evidence"]["findings"]
    assert len(cfg["changelog"]) >= 2


# ---------------------------------------------------------------------------
# Drift detection fields
# ---------------------------------------------------------------------------

def test_backtest_reports_drift_and_weight_recommendation():
    art = json.loads((REPO_ROOT / "data" / "backtest_report.json").read_text())
    d = art["data"]
    assert set(d["drift"]) == {"inventory_surprise", "price_momentum_5d"}
    for entry in d["drift"].values():
        assert "ic_first_half" in entry and "drift_detected" in entry
    rec = d["weight_recommendation"]
    assert rec["inventory_share"] + rec["momentum_share"] == pytest.approx(1.0)
