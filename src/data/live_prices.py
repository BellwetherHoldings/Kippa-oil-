"""
Live WTI prices — CL=F front-month futures via Yahoo's public chart API.

Closes the platform's 8-day price lag (roadmap: live-price-feed). No API
key required. EIA RWTC (Cushing spot) remains the canonical historical
series for backtesting; this feed supplies the fresh tail for momentum,
simulation anchoring, and forecasting.

load_price_history() returns the spliced series: EIA history up to where
the live series begins, then live CL=F. The two track within ~$1; the
single splice-point return distortion is negligible for the rolling
statistics that consume it, and every row carries its source column.

Usage:
    python src/data/live_prices.py     (writes data/wti_live.csv)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/CL=F"
LIVE_PATH = DATA_DIR / "wti_live.csv"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_live_wti(range_: str = "2y") -> pd.DataFrame:
    """Daily CL=F closes incl. today's session. Columns: date, close."""
    resp = requests.get(
        CHART_URL,
        params={"range": range_, "interval": "1d"},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]

    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    df = pd.DataFrame({
        "date": [datetime.fromtimestamp(t, tz=timezone.utc).date()
                 for t in stamps],
        "close": closes,
    })
    df["date"] = pd.to_datetime(df["date"])
    df = (
        df.dropna()
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    if df.empty:
        raise RuntimeError("Yahoo chart API returned no usable closes.")
    return df


def save_live_prices() -> pd.DataFrame:
    df = fetch_live_wti()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(LIVE_PATH, index=False)
    return df


def load_price_history() -> pd.DataFrame:
    """EIA spot history spliced with the live CL=F tail.

    Columns: date, close, source ('RWTC' | 'CL=F'). Degrades to EIA-only
    when the live file is missing, empty, or unreadable — a corrupt tail
    must never black out the whole price stack. EIA rows NEWER than the
    live window are kept (a stale live file cannot hide fresher EIA data).
    """
    eia = pd.read_csv(DATA_DIR / "wti_prices.csv", parse_dates=["date"])
    eia["source"] = "RWTC"
    if not LIVE_PATH.exists():
        return eia

    try:
        live = pd.read_csv(LIVE_PATH, parse_dates=["date"]).dropna(
            subset=["date", "close"])
    except (ValueError, KeyError, OSError, pd.errors.ParserError):
        live = pd.DataFrame()
    if live.empty:
        return eia

    live["source"] = "CL=F"
    spliced = pd.concat([
        eia[eia["date"] < live["date"].min()],   # history before the tail
        live,                                     # the live tail
        eia[eia["date"] > live["date"].max()],   # EIA fresher than the tail
    ])
    return spliced.sort_values("date").reset_index(drop=True)


def main() -> None:
    df = save_live_prices()
    last = df.iloc[-1]
    print(f"Live WTI (CL=F via Yahoo): {len(df)} daily closes → "
          f"data/wti_live.csv")
    print(f"  Latest: {last['date'].date()} | ${last['close']:.2f}")


if __name__ == "__main__":
    main()
