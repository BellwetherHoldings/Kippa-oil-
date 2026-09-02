"""Tests for the macroeconomic intelligence engine (offline paths only)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.intelligence.macroeconomic.engine import INDICATORS, _clip


def test_indicator_weights_sum_to_one():
    assert sum(i["weight"] for i in INDICATORS.values()) == pytest.approx(1.0)


def test_dollar_index_is_inverted():
    # A rising dollar must push the oil signal down, not up.
    assert INDICATORS["dollar_index"]["invert"] is True
    assert INDICATORS["industrial_production"]["invert"] is False


def test_clip_bounds():
    assert _clip(3.7) == 1.0
    assert _clip(-2.2) == -1.0
    assert _clip(0.42) == 0.42
