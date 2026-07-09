"""
Kippa Oil Intelligence Platform — Inventory Surprise Signal
Computes the weekly crude inventory "surprise" vs. the 5-year seasonal average,
then normalizes it to a trailing z-score.

Logic:
    expected  = average weekly_change for the same calendar week, prior 5 years
    surprise  = actual weekly_change - expected
    z-score   = surprise standardized over a trailing 52-week window

Interpretation:
    Negative surprise (bigger draw / smaller build than seasonal norm) = bullish
    Positive surprise (bigger build / smaller draw than seasonal norm) = bearish

Setup:
    Requires data/crude_inventories.csv (run src/data/eia_client.py first)

Usage:
    python src/engines/scoring/inventory_surprise.py

Outputs:
    data/inventory_surprise.csv
    Prints top 10 bullish and top 10 bearish surprises for sanity checking.

Governed by docs/006_Scoring_System.md — every score is traceable to its
inputs: expected_change and surprise are preserved alongside the z-score.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"

INPUT_PATH = DATA_DIR / "crude_inventories.csv"
OUTPUT_PATH = DATA_DIR / "inventory_surprise.csv"

LOOKBACK_YEARS = 5      # years used for the seasonal expectation
ZSCORE_WINDOW = 52      # trailing weeks for z-score normalization
ZSCORE_MIN_PERIODS = 26  # minimum observations before a z-score is emitted

REQUIRED_COLUMNS = {"period", "weekly_change"}


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def compute_surprise(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add expected_change, surprise, and surprise_z columns.

    Expected change for a given week = mean of weekly_change for the same
    ISO calendar week across the prior LOOKBACK_YEARS years. Duplicate
    (year, week) entries (ISO week 53 quirks) are averaged before use.
    """
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")

    df = df.copy()
    df["period"] = pd.to_datetime(df["period"])
    df = df.sort_values("period").reset_index(drop=True)

    iso = df["period"].dt.isocalendar()
    iso_week = iso.week.astype(int)
    iso_year = iso.year.astype(int)

    # Mean weekly_change per (iso_year, iso_week); dict lookup keeps the
    # seasonal-expectation pass O(n) instead of an O(n^2) index scan.
    seasonal: dict[tuple[int, int], float] = (
        df["weekly_change"]
        .groupby([iso_year, iso_week])
        .mean()
        .to_dict()
    )

    def expected_for(year: int, week: int) -> float | None:
        vals = [
            v
            for back in range(1, LOOKBACK_YEARS + 1)
            if (v := seasonal.get((year - back, week))) is not None
            and pd.notna(v)
        ]
        return sum(vals) / len(vals) if vals else None

    df["expected_change"] = [
        expected_for(y, w) for y, w in zip(iso_year, iso_week)
    ]
    df["surprise"] = df["weekly_change"] - df["expected_change"]

    # Trailing z-score: how extreme is this surprise vs. the past year?
    roll = df["surprise"].rolling(ZSCORE_WINDOW, min_periods=ZSCORE_MIN_PERIODS)
    df["surprise_z"] = (df["surprise"] - roll.mean()) / roll.std()

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"{INPUT_PATH} not found. Run src/data/eia_client.py first."
        )

    df = pd.read_csv(INPUT_PATH)
    df = compute_surprise(df)

    # Keep only rows where the signal is fully defined
    valid = df.dropna(subset=["surprise_z"]).copy()

    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Saved {len(df)} rows to {OUTPUT_PATH}")
    print(f"({len(valid)} rows have a complete surprise z-score)\n")

    fmt_cols = ["period", "weekly_change", "expected_change", "surprise", "surprise_z"]

    print("=" * 70)
    print("TOP 10 BULLISH SURPRISES (biggest draws vs. seasonal norm)")
    print("=" * 70)
    bullish = valid.nsmallest(10, "surprise_z")[fmt_cols]
    print(bullish.to_string(index=False))

    print()
    print("=" * 70)
    print("TOP 10 BEARISH SURPRISES (biggest builds vs. seasonal norm)")
    print("=" * 70)
    bearish = valid.nlargest(10, "surprise_z")[fmt_cols]
    print(bearish.to_string(index=False))

    print("\nSanity check: do these dates line up with real market events?")
    print("(e.g., 2020 COVID builds, 2021-22 reopening/SPR-era draws)")
    print("If yes, the signal is computing correctly. Next: event-study backtest.")


if __name__ == "__main__":
    main()
