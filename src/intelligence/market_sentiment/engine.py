"""
Market Sentiment Intelligence Engine — institutional positioning from CFTC.

Governed by docs/021_Market_Sentiment_Intelligence.md. Reads the CFTC
Commitments of Traders (legacy, futures-only) public dataset for NYMEX
WTI crude and scores speculative positioning: net non-commercial length
as a share of open interest, standardized against 3 years of history.
Institutional positioning is the strongest free sentiment proxy available
(futures positioning intelligence, doc 021).

No API key required — publicreporting.cftc.gov is a public Socrata API.

Usage:
    python src/intelligence/market_sentiment/engine.py

Publishes:
    data/market_sentiment.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine

COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
NYMEX_WTI_CODE = "067651"     # CRUDE OIL, LIGHT SWEET — NYMEX
HISTORY_WEEKS = 156
FULL_SCORE_Z = 2.0            # |z| of net positioning that maps to ±1


class MarketSentimentEngine(Engine):
    name = "market_sentiment"
    version = "1.0"
    output_name = "market_sentiment"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        resp = requests.get(
            COT_URL,
            params={
                "$where": f"cftc_contract_market_code='{NYMEX_WTI_CODE}'",
                "$order": "report_date_as_yyyy_mm_dd ASC",
                "$limit": 5000,
                "$select": ",".join([
                    "report_date_as_yyyy_mm_dd",
                    "noncomm_positions_long_all",
                    "noncomm_positions_short_all",
                    "open_interest_all",
                ]),
            },
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise ValueError("CFTC returned no rows for NYMEX WTI.")

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
        for col in ("noncomm_positions_long_all",
                    "noncomm_positions_short_all", "open_interest_all"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = (
            df.dropna()
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True)
            .tail(HISTORY_WEEKS)
        )
        if len(df) < 52:
            raise ValueError(f"Only {len(df)} weeks of COT history — need ≥52.")

        df["net_noncomm"] = (
            df["noncomm_positions_long_all"] - df["noncomm_positions_short_all"]
        )
        df["net_pct_oi"] = df["net_noncomm"] / df["open_interest_all"]

        latest = df.iloc[-1]
        hist = df["net_pct_oi"]
        z = float((latest["net_pct_oi"] - hist.mean()) / hist.std())
        signal = max(-1.0, min(1.0, z / FULL_SCORE_Z))

        change_4w = float(
            latest["net_noncomm"] - df.iloc[-5]["net_noncomm"]
        ) if len(df) >= 5 else 0.0

        report_date = latest["date"].date()
        staleness = (date.today() - report_date).days
        if staleness > 12:
            warnings.append(
                f"Latest COT report is {staleness} days old — CFTC publishes "
                f"Friday for the prior Tuesday; check for a release gap."
            )

        if signal > 0.2:
            label = "bullish"
        elif signal < -0.2:
            label = "bearish"
        else:
            label = "neutral"

        data = {
            "sentiment_signal": round(signal, 3),
            "sentiment_label": label,
            "as_of": report_date.isoformat(),
            "staleness_days": staleness,
            "net_noncommercial_contracts": int(latest["net_noncomm"]),
            "net_pct_open_interest": round(float(latest["net_pct_oi"]), 4),
            "positioning_zscore_3y": round(z, 2),
            "net_change_4w_contracts": int(change_4w),
            "open_interest": int(latest["open_interest_all"]),
            "weeks_of_history": len(df),
        }
        evidence = [
            f"CFTC Commitments of Traders (legacy futures-only), NYMEX WTI "
            f"code {NYMEX_WTI_CODE}, report dated {report_date} "
            f"({len(df)} weekly reports) — {COT_URL}"
        ]
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not -1.0 <= data["sentiment_signal"] <= 1.0:
            raise ValueError(f"signal out of bounds: {data['sentiment_signal']}")


def main() -> None:
    result = MarketSentimentEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Market Sentiment — Institutional Positioning (CFTC COT)")
    print("=" * 60)
    print(f"  Sentiment:   {d['sentiment_signal']:+.2f}  "
          f"({d['sentiment_label'].upper()})")
    print(f"  As of {d['as_of']} ({d['staleness_days']}d old)")
    print(f"  Net non-commercial: {d['net_noncommercial_contracts']:+,} contracts "
          f"({d['net_pct_open_interest']:+.1%} of OI)")
    print(f"  3y positioning z:   {d['positioning_zscore_3y']:+.2f}")
    print(f"  4w change:          {d['net_change_4w_contracts']:+,} contracts")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/market_sentiment.json")


if __name__ == "__main__":
    main()
