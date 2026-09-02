"""Unit tests for the inventory surprise signal (src/engines/scoring)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "engines" / "scoring"))

from inventory_surprise import LOOKBACK_YEARS, compute_surprise


def _weekly_frame(changes_by_year: dict[int, float], weeks: int = 52) -> pd.DataFrame:
    """Build a weekly series where every week of a year has the same change."""
    rows = []
    for year, change in changes_by_year.items():
        periods = pd.date_range(f"{year}-01-08", periods=weeks, freq="W-FRI")
        for p in periods:
            rows.append({"period": p, "weekly_change": change})
    return pd.DataFrame(rows)


def test_expected_change_is_mean_of_prior_years():
    # Years 2015-2019 have constant changes 10, 20, 30, 40, 50.
    # Every 2020 week that maps to the same ISO week should expect mean = 30.
    df = _weekly_frame({2015: 10, 2016: 20, 2017: 30, 2018: 40, 2019: 50, 2020: 0})
    out = compute_surprise(df)

    year_2020 = out[out["period"].dt.year == 2020]
    # Interior weeks (ISO boundary weeks can straddle years); all must be 30.
    interior = year_2020.iloc[5:-5]
    assert (interior["expected_change"] == 30.0).all()
    assert (interior["surprise"] == -30.0).all()


def test_first_years_have_no_expectation():
    df = _weekly_frame({2015: 10, 2016: 20})
    out = compute_surprise(df)
    first_year = out[out["period"].dt.year == 2015]
    assert first_year["expected_change"].isna().all()


def test_lookback_window_only_uses_prior_five_years():
    # 2014 has an extreme value that sits OUTSIDE the 5-year lookback of 2020.
    df = _weekly_frame(
        {2014: 99999, 2015: 10, 2016: 20, 2017: 30, 2018: 40, 2019: 50, 2020: 0}
    )
    out = compute_surprise(df)
    interior = out[out["period"].dt.year == 2020].iloc[5:-5]
    assert (interior["expected_change"] == 30.0).all(), (
        f"2014 leaked into the {LOOKBACK_YEARS}-year lookback"
    )


def test_missing_columns_raise():
    with pytest.raises(ValueError, match="missing required columns"):
        compute_surprise(pd.DataFrame({"period": ["2020-01-03"]}))


def test_output_preserves_input_rows_and_adds_signal_columns():
    df = _weekly_frame({2015: 10, 2016: 20, 2017: 30})
    out = compute_surprise(df)
    assert len(out) == len(df)
    for col in ("expected_change", "surprise", "surprise_z"):
        assert col in out.columns
