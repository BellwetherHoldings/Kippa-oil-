"""Tests for chokepoint realized-impact damping (the July 2026 fix)."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.intelligence.geopolitical import strait_status as ss


def _valid(**over):
    base = dict(
        strait_status="partially_open",
        change_from_previous="more_open",
        estimated_shipping_capacity_percent=35,
        commercial_shipping="limited",
        military_risk="extreme",
        insurance_risk="high",
        expected_supply_impact="neutral",
        confidence=0.6,
        summary="test",
        sources=["https://example.com/a"],
    )
    base.update(over)
    return base


# -- schema ------------------------------------------------------------------

def test_valid_classification_passes():
    assert ss.validate_status(_valid()) == []


def test_missing_field_rejected():
    obj = _valid()
    del obj["military_risk"]
    with pytest.raises(ss.StatusValidationError, match="military_risk"):
        ss.validate_status(obj)


def test_bad_enum_rejected():
    with pytest.raises(ss.StatusValidationError, match="strait_status"):
        ss.validate_status(_valid(strait_status="ajar"))


def test_capacity_out_of_range_rejected():
    with pytest.raises(ss.StatusValidationError, match="capacity"):
        ss.validate_status(_valid(estimated_shipping_capacity_percent=140))


def test_unsourced_classification_rejected():
    """Invariant: no unverified data may drive a score."""
    with pytest.raises(ss.StatusValidationError, match="source"):
        ss.validate_status(_valid(sources=[]))


def test_capacity_contradicting_label_warns_not_fails():
    warns = ss.validate_status(
        _valid(strait_status="fully_open",
               estimated_shipping_capacity_percent=10))
    assert warns and "outside" in warns[0]


# -- damping math ------------------------------------------------------------

def test_full_capacity_removes_the_claim():
    """100% shipping capacity => the chokepoint claim contributes nothing."""
    f = ss.capacity_factor(
        _valid(estimated_shipping_capacity_percent=100, confidence=1.0))
    assert f == pytest.approx(0.0)


def test_zero_capacity_leaves_claim_intact():
    f = ss.capacity_factor(
        _valid(estimated_shipping_capacity_percent=0, confidence=1.0))
    assert f == pytest.approx(1.0)


def test_low_confidence_pulls_toward_undamped():
    """An unsure observation must move the score less than a confident one."""
    sure = ss.capacity_factor(
        _valid(estimated_shipping_capacity_percent=50, confidence=1.0))
    unsure = ss.capacity_factor(
        _valid(estimated_shipping_capacity_percent=50, confidence=0.2))
    assert sure < unsure < 1.0


def test_no_observation_does_not_damp():
    assert ss.capacity_factor(None) == 1.0


def test_inventory_build_discounts_the_claim():
    """Crude building against a 'supply crisis' is evidence it isn't biting."""
    assert ss.inventory_factor(0.93) < 1.0
    assert ss.inventory_factor(2.0) < ss.inventory_factor(0.5)


def test_inventory_draw_does_not_inflate():
    """Draws leave the score alone — they must never amplify past the claim."""
    assert ss.inventory_factor(-1.5) == 1.0
    assert ss.inventory_factor(None) == 1.0


def test_inventory_discount_is_capped():
    assert ss.inventory_factor(99.0) == pytest.approx(
        1.0 - ss.MAX_INVENTORY_DISCOUNT)


def test_combined_factor_and_explainability():
    factor, detail = ss.realized_impact_factor(_valid(), 0.93)
    assert 0.0 < factor < 1.0
    # the breakdown must show WHICH check did the work (no black boxes)
    assert detail["capacity_factor"] and detail["inventory_factor"]
    assert detail["capacity_percent"] == 35
    assert detail["combined_factor"] == pytest.approx(factor, abs=1e-3)


def test_july_regression_headline_extreme_but_oil_flowing():
    """The exact failure this was built for.

    Rhetoric maxed (extreme military + insurance risk) while 35% of capacity
    still flowed and inventories BUILT. The claim must be damped well below
    its headline value.
    """
    obs = _valid(strait_status="partially_open",
                 estimated_shipping_capacity_percent=35,
                 military_risk="extreme", insurance_risk="extreme",
                 confidence=0.6)
    factor, _ = ss.realized_impact_factor(obs, surprise_z=0.93)
    assert factor < 0.7, "damping too weak to have caught the July divergence"


# -- registry round-trip -----------------------------------------------------

def test_ingest_stamps_and_appends(tmp_path):
    reg = tmp_path / "events.json"
    reg.write_text(json.dumps({"events": [], "chokepoint_status": []}))
    ss.ingest(_valid(), observed_date="2026-07-19", path=reg)
    ss.ingest(_valid(estimated_shipping_capacity_percent=60),
              observed_date="2026-07-20", path=reg)

    obs = ss.observations(path=reg)
    assert len(obs) == 2                       # history preserved, not replaced
    assert obs[0]["observed_date"] == "2026-07-19"
    latest = ss.latest_status(path=reg)
    assert latest["estimated_shipping_capacity_percent"] == 60
    assert latest["chokepoint"] == "strait_of_hormuz"
    assert latest["ingested_via"] == "analyst_classification"


# -- engine integration ------------------------------------------------------

def _registry(tmp_path, capacity=None, confidence=1.0):
    """Registry with one maxed-out chokepoint event, optional status obs."""
    reg = {
        "events": [{
            "id": "cp", "category": "chokepoint_disruption",
            "chokepoint": "strait_of_hormuz", "status": "active",
            "severity": 10, "oil_supply_impact": 10, "confidence": 1.0,
            "last_update": "2026-07-26", "description": "test",
            "sources": ["https://example.com/x"],
        }],
        "chokepoint_status": [],
    }
    if capacity is not None:
        reg["chokepoint_status"].append({
            **_valid(estimated_shipping_capacity_percent=capacity,
                     confidence=confidence),
            "chokepoint": "strait_of_hormuz",
            "observed_date": "2026-07-26",
        })
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "events.json"
    p.write_text(json.dumps(reg))
    return p


def test_engine_damps_when_oil_still_flows(tmp_path, monkeypatch):
    """The headline is identical; only realized capacity differs."""
    from src.intelligence.geopolitical.engine import (
        GeopoliticalIntelligenceEngine as Geo,
    )
    import src.intelligence.geopolitical.engine as geo_mod
    monkeypatch.setattr(geo_mod, "_inventory_surprise_z", lambda: None)

    blocked = Geo(events_path=_registry(tmp_path / "a", capacity=0)).run()
    (tmp_path / "b").mkdir(parents=True, exist_ok=True)
    flowing = Geo(events_path=_registry(tmp_path / "b", capacity=80)).run()

    assert blocked.ok and flowing.ok
    assert flowing.data["risk_score"] < blocked.data["risk_score"], (
        "an event whose oil is still flowing must score lower than a real block")


def test_engine_warns_and_leaves_claim_undamped_without_status(tmp_path):
    from src.intelligence.geopolitical.engine import (
        GeopoliticalIntelligenceEngine as Geo,
    )
    (tmp_path / "c").mkdir(parents=True, exist_ok=True)
    res = Geo(events_path=_registry(tmp_path / "c", capacity=None)).run()
    assert res.ok
    assert any("No shipping-status observation" in w for w in res.warnings)
    assert res.data["realized_impact"]["strait_of_hormuz"]["combined_factor"] == 1.0
