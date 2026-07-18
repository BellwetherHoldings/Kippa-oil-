"""Tests for the CL futures market-hours guard (pure schedule logic)."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.data import market_hours

ET = ZoneInfo("America/New_York")


def _utc(y, mo, d, h, mi=0):
    """A UTC instant expressed from an ET wall-clock time."""
    return datetime(y, mo, d, h, mi, tzinfo=ET).astimezone(timezone.utc)


def test_weekend_is_closed():
    assert not market_hours.is_market_open(_utc(2026, 7, 18, 12))   # Sat
    assert not market_hours.is_market_open(_utc(2026, 7, 19, 12))   # Sun noon


def test_friday_close_and_sunday_open_boundaries():
    assert market_hours.is_market_open(_utc(2026, 7, 17, 16, 59))   # Fri 4:59
    assert not market_hours.is_market_open(_utc(2026, 7, 17, 17, 1))  # Fri 5:01
    assert not market_hours.is_market_open(_utc(2026, 7, 19, 17, 59))  # Sun 5:59
    assert market_hours.is_market_open(_utc(2026, 7, 19, 18, 1))    # Sun 6:01


def test_weekday_overnight_open_but_halt_closed():
    assert market_hours.is_market_open(_utc(2026, 7, 15, 3, 0))     # Wed 3 AM
    assert not market_hours.is_market_open(_utc(2026, 7, 15, 17, 30))  # Wed halt


def test_next_open_points_forward_when_closed():
    now = _utc(2026, 7, 18, 12)                # Saturday
    nxt = market_hours.next_open(now)
    assert nxt > now
    assert market_hours.is_market_open(nxt)
    # Sunday 6 PM ET == 22:00 UTC in July (EDT)
    assert nxt.astimezone(ET).weekday() == 6


def test_next_open_is_now_when_open():
    now = _utc(2026, 7, 15, 3, 0)              # Wed, trading
    assert market_hours.next_open(now) == now


def test_status_shape_when_closed():
    st = market_hours.status(_utc(2026, 7, 18, 12))
    assert st["open"] is False
    assert "next_open" in st and "hours_to_open" in st
