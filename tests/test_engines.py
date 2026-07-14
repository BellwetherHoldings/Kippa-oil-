"""Tests for the engine framework, geopolitical engine, and composite signal."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.engines.base import Engine
from src.engines.scoring.composite_signal import (
    _clip,
    _freshness_confidence,
    _label,
)
from src.intelligence.geopolitical.engine import (
    GeopoliticalIntelligenceEngine,
    _risk_level,
)


# ---------------------------------------------------------------------------
# Engine framework (doc 005 lifecycle)
# ---------------------------------------------------------------------------

class _GoodEngine(Engine):
    name = "test_good"

    def execute(self, inputs, warnings):
        return {"answer": 42}, ["test evidence"]


class _BrokenEngine(Engine):
    name = "test_broken"

    def execute(self, inputs, warnings):
        raise RuntimeError("boom")


def test_engine_success_lifecycle():
    result = _GoodEngine().run()
    assert result.ok
    assert result.data == {"answer": 42}
    assert result.evidence == ["test evidence"]
    assert result.error is None
    assert result.duration_ms >= 0


def test_engine_failure_is_isolated():
    result = _BrokenEngine().run()          # must not raise
    assert not result.ok
    assert "boom" in result.error
    assert result.data == {}


def test_engine_rejects_empty_output():
    class Empty(Engine):
        name = "test_empty"

        def execute(self, inputs, warnings):
            return {}, []

    result = Empty().run()
    assert not result.ok


# ---------------------------------------------------------------------------
# Geopolitical engine (doc 019)
# ---------------------------------------------------------------------------

def _write_registry(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "events.json"
    p.write_text(json.dumps({"events": events}))
    return p


def _event(**overrides) -> dict:
    base = {
        "id": "evt-1",
        "category": "armed_conflict",
        "region": "Test",
        "countries": [],
        "chokepoint": None,
        "description": "test event",
        "start_date": "2026-01-01",
        "last_update": "2026-07-14",
        "status": "active",
        "severity": 5,
        "oil_supply_impact": 5,
        "confidence": 1.0,
        "sources": ["https://example.com/evidence"],
    }
    return {**base, **overrides}


def test_geo_score_bounds_and_level(tmp_path):
    path = _write_registry(tmp_path, [_event()])
    result = GeopoliticalIntelligenceEngine(events_path=path).run()
    assert result.ok
    assert 0 <= result.data["risk_score"] <= 100
    assert result.data["risk_level"] == _risk_level(result.data["risk_score"])


def test_geo_unsourced_events_are_excluded(tmp_path):
    path = _write_registry(
        tmp_path,
        [_event(), _event(id="evt-2", severity=10, oil_supply_impact=10, sources=[])],
    )
    result = GeopoliticalIntelligenceEngine(events_path=path).run()
    assert result.ok
    ids = [c["id"] for c in result.data["contributions"]]
    assert "evt-2" not in ids                      # unverified data never scores
    assert any("evt-2" in w for w in result.warnings)


def test_geo_resolved_events_do_not_score(tmp_path):
    path = _write_registry(
        tmp_path,
        [_event(), _event(id="evt-old", status="resolved",
                          severity=10, oil_supply_impact=10)],
    )
    result = GeopoliticalIntelligenceEngine(events_path=path).run()
    ids = [c["id"] for c in result.data["contributions"]]
    assert "evt-old" not in ids


def test_geo_chokepoint_amplifies_score(tmp_path):
    plain = GeopoliticalIntelligenceEngine(
        events_path=_write_registry(tmp_path, [_event()])
    ).run()
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    choked = GeopoliticalIntelligenceEngine(
        events_path=_write_registry(tmp2, [_event(chokepoint="strait_of_hormuz")])
    ).run()
    assert choked.data["risk_score"] > plain.data["risk_score"]
    assert choked.data["chokepoints_disrupted"] == ["strait_of_hormuz"]


# ---------------------------------------------------------------------------
# Composite signal (docs 006 + 008)
# ---------------------------------------------------------------------------

def test_freshness_confidence_decays_and_floors():
    assert _freshness_confidence("geopolitical_risk", 0) == 1.0
    assert _freshness_confidence("geopolitical_risk", 2) == 1.0
    mid = _freshness_confidence("geopolitical_risk", 12)
    assert 0.2 < mid < 1.0
    assert _freshness_confidence("geopolitical_risk", 500) == 0.2  # floor


def test_labels_cover_the_range():
    assert _label(-0.9) == "strong bearish"
    assert _label(-0.3) == "bearish"
    assert _label(0.0) == "neutral"
    assert _label(0.4) == "bullish"
    assert _label(0.9) == "strong bullish"


def test_clip():
    assert _clip(5.0) == 1.0
    assert _clip(-5.0) == -1.0
    assert _clip(0.3) == 0.3
