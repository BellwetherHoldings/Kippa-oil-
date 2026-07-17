"""Tests for the intraday radar (offline statistics only)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.engines.analytics.intraday import (
    LEAN_THRESHOLD,
    MIN_STATE_SAMPLE,
    _streak_state,
    _wilson,
)


def test_wilson_interval_behaves():
    lo, hi = _wilson(50, 100)
    assert lo < 0.5 < hi
    lo2, hi2 = _wilson(500, 1000)
    assert (hi2 - lo2) < (hi - lo)          # more data → tighter interval
    assert _wilson(0, 0) == (0.0, 1.0)      # no data → no knowledge


def test_wilson_strong_edge_clears_half():
    lo, _ = _wilson(70, 100)
    assert lo > 0.5                          # 70% on n=100 is a real edge


def test_streak_states():
    dirs = [1, 1, 1, 1, -1, -1, 0, 1]
    assert _streak_state(dirs, 0) == "+1"
    assert _streak_state(dirs, 1) == "+2"
    assert _streak_state(dirs, 3) == "+3+"   # capped
    assert _streak_state(dirs, 5) == "-2"
    assert _streak_state(dirs, 6) == "flat"
    assert _streak_state(dirs, 7) == "+1"    # reset after flat


def test_lean_threshold_is_meaningfully_above_coin_flip():
    assert LEAN_THRESHOLD >= 0.55
    assert MIN_STATE_SAMPLE >= 30
