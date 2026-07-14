"""
Price Momentum Engine — fast-tape analytics from WTI daily closes.

Catches what weekly inventory data cannot: spikes, trend, and volatility.
Reads data/wti_prices.csv (run src/data/eia_client.py first). Because EIA
spot data lags several days, staleness is measured and reported — a stale
momentum reading gets discounted downstream by the composite signal.

Usage:
    python src/engines/analytics/price_momentum.py

Publishes:
    data/price_momentum.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.live_prices import load_price_history
from src.engines.base import DATA_DIR, Engine

PRICES_PATH = DATA_DIR / "wti_prices.csv"

SPIKE_Z_THRESHOLD = 2.0        # |z| of latest daily return vs trailing year
TREND_THRESHOLD = 0.02         # ±2% over 5 sessions = directional
ANNUALIZATION = 252 ** 0.5


class PriceMomentumEngine(Engine):
    name = "price_momentum"
    version = "1.0"
    output_name = "price_momentum"

    def __init__(self, prices_path: Path = PRICES_PATH) -> None:
        self.prices_path = prices_path

    def validate_input(self, inputs: dict[str, Any]) -> None:
        if not self.prices_path.exists():
            raise ValueError(
                f"{self.prices_path} not found. Run src/data/eia_client.py first."
            )

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        df = load_price_history()          # EIA history + live CL=F tail
        if len(df) < 260:
            raise ValueError(f"Need ≥260 daily closes, got {len(df)}.")

        df = df.sort_values("date").reset_index(drop=True)
        price_source = df["source"].iloc[-1] if "source" in df else "RWTC"
        close = df["close"]
        returns = close.pct_change()

        last_date = df["date"].iloc[-1].date()
        staleness = (date.today() - last_date).days
        if staleness > 5:
            warnings.append(
                f"Price data is {staleness} days old (EIA spot lags; a live "
                f"feed is a Wave 2 upgrade). Momentum may miss recent moves."
            )

        ret_1d = returns.iloc[-1]
        ret_5d = close.iloc[-1] / close.iloc[-6] - 1
        ret_20d = close.iloc[-1] / close.iloc[-21] - 1

        trailing = returns.iloc[-252:]
        ret_z = (ret_1d - trailing.mean()) / trailing.std()
        vol_20d = returns.iloc[-20:].std() * ANNUALIZATION

        if ret_5d > TREND_THRESHOLD:
            direction = "bullish"
        elif ret_5d < -TREND_THRESHOLD:
            direction = "bearish"
        else:
            direction = "neutral"

        data = {
            "last_close": round(float(close.iloc[-1]), 2),
            "price_source": price_source,
            "last_date": last_date.isoformat(),
            "staleness_days": staleness,
            "return_1d": round(float(ret_1d), 4),
            "return_5d": round(float(ret_5d), 4),
            "return_20d": round(float(ret_20d), 4),
            "return_1d_zscore": round(float(ret_z), 2),
            "volatility_20d_annualized": round(float(vol_20d), 3),
            "spike_flag": bool(abs(ret_z) >= SPIKE_Z_THRESHOLD),
            "direction": direction,
        }
        evidence = [
            f"spliced price history through {last_date} "
            f"({len(df)} closes; tail source: {price_source} — "
            f"EIA RWTC + live CL=F via data/wti_live.csv)"
        ]
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if data["last_close"] <= 0:
            raise ValueError(f"Nonsensical close: {data['last_close']}")


def main() -> None:
    result = PriceMomentumEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Price Momentum — WTI")
    print("=" * 55)
    print(f"  Last close:  ${d['last_close']:.2f}  ({d['last_date']}, "
          f"{d['staleness_days']}d old)")
    print(f"  Returns:     1d {d['return_1d']:+.2%} | 5d {d['return_5d']:+.2%} "
          f"| 20d {d['return_20d']:+.2%}")
    print(f"  1d z-score:  {d['return_1d_zscore']:+.2f}"
          f"{'  ← SPIKE' if d['spike_flag'] else ''}")
    print(f"  20d vol:     {d['volatility_20d_annualized']:.0%} annualized")
    print(f"  Direction:   {d['direction'].upper()}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/price_momentum.json")


if __name__ == "__main__":
    main()
