"""
Feature panel builder — the shared weekly dataset for validation and
forecasting.

Aligns each weekly inventory observation with WTI prices at (or after)
the report date and computes forward returns, giving backtesting and
forecasting one consistent, leakage-free view of history.

Columns:
    period          inventory week
    surprise_z      inventory surprise z-score at that week
    entry_date      first price date on/after the period
    entry_price     WTI close on entry_date
    momentum_5d     trailing 5-session return at entry (known at entry)
    vol_20d         trailing 20-session realized vol at entry (annualized)
    fwd_ret_1w      forward 5-session return from entry
    fwd_ret_4w      forward 21-session return from entry

Usage:
    python src/engines/data_processing/features.py   (writes data/feature_panel.csv)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR

ANNUALIZATION = 252 ** 0.5

# memoized on source mtimes: backtesting and forecasting both need the
# panel in the same cycle — build it once, not twice
_CACHE: tuple[tuple[float, float], pd.DataFrame] | None = None


def build_weekly_panel() -> pd.DataFrame:
    global _CACHE
    key = ((DATA_DIR / "inventory_surprise.csv").stat().st_mtime,
           (DATA_DIR / "wti_prices.csv").stat().st_mtime)
    if _CACHE is not None and _CACHE[0] == key:
        return _CACHE[1].copy()

    surprise = pd.read_csv(DATA_DIR / "inventory_surprise.csv",
                           parse_dates=["period"])
    prices = pd.read_csv(DATA_DIR / "wti_prices.csv", parse_dates=["date"])
    prices = prices.sort_values("date").reset_index(drop=True)
    prices["ret_1d"] = prices["close"].pct_change()

    rows = []
    dates = prices["date"]
    for _, wk in surprise.dropna(subset=["surprise_z"]).iterrows():
        idx = dates.searchsorted(wk["period"])
        # need 20 sessions of history and 21 of future around the entry
        if idx < 21 or idx + 21 >= len(prices):
            continue
        entry = prices.iloc[idx]
        rows.append({
            "period": wk["period"],
            "surprise_z": wk["surprise_z"],
            "entry_date": entry["date"],
            "entry_price": entry["close"],
            "momentum_5d": entry["close"] / prices["close"].iloc[idx - 5] - 1,
            "vol_20d": prices["ret_1d"].iloc[idx - 19: idx + 1].std()
                       * ANNUALIZATION,
            "fwd_ret_1w": prices["close"].iloc[idx + 5] / entry["close"] - 1,
            "fwd_ret_4w": prices["close"].iloc[idx + 21] / entry["close"] - 1,
        })

    panel = pd.DataFrame(rows)
    if panel.empty:
        raise ValueError("Feature panel is empty — refresh the data first.")
    _CACHE = (key, panel)
    return panel.copy()


if __name__ == "__main__":
    panel = build_weekly_panel()
    out = DATA_DIR / "feature_panel.csv"
    panel.to_csv(out, index=False)
    print(f"{len(panel)} weekly rows → {out} "
          f"({panel['period'].min().date()} … {panel['period'].max().date()})")
