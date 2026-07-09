"""
Kippa Oil Intelligence Platform — EIA Data Client
Pulls weekly U.S. commercial crude inventories (ex-SPR) and WTI spot prices
from the EIA v2 API.

Setup:
    pip install requests pandas python-dotenv

    Create a .env file in the repo root containing:
        EIA_API_KEY=your_key_here

Usage:
    python src/data/eia_client.py

Outputs:
    data/crude_inventories.csv  (period, value, weekly_change)
    data/wti_prices.csv         (date, close)
"""

import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

EIA_API_KEY = os.getenv("EIA_API_KEY")
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

MAX_ROWS_PER_CALL = 5000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _paginate_eia(url: str, params: dict) -> list[dict]:
    if not EIA_API_KEY:
        raise RuntimeError(
            "EIA_API_KEY not found. Add it to your .env file in the repo root."
        )

    all_rows: list[dict] = []
    offset = 0

    while True:
        page_params = {**params, "api_key": EIA_API_KEY, "offset": offset, "length": MAX_ROWS_PER_CALL}
        resp = requests.get(url, params=page_params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        rows = payload.get("response", {}).get("data", [])
        if not rows:
            break

        all_rows.extend(rows)
        offset += len(rows)

        total = int(payload["response"].get("total", 0))
        if offset >= total:
            break

    return all_rows


# ---------------------------------------------------------------------------
# EIA: weekly crude inventories
# ---------------------------------------------------------------------------

def get_crude_inventories(start: str = "2010-01-01") -> pd.DataFrame:
    """
    Fetch weekly U.S. commercial crude inventories (ex-SPR) from EIA v2 API.
    Series WCESTUS1 — thousand barrels.
    """
    rows = _paginate_eia(
        "https://api.eia.gov/v2/petroleum/stoc/wstk/data/",
        {
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": "WCESTUS1",
            "start": start,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        },
    )

    if not rows:
        raise RuntimeError("No inventory data returned from EIA.")

    df = pd.DataFrame(rows)[["period", "value"]]
    df["period"] = pd.to_datetime(df["period"])
    df["value"] = pd.to_numeric(df["value"])
    df = df.sort_values("period").reset_index(drop=True)
    df["weekly_change"] = df["value"].diff()
    return df


# ---------------------------------------------------------------------------
# EIA: WTI spot prices
# ---------------------------------------------------------------------------

def get_wti_prices(start: str = "2010-01-01") -> pd.DataFrame:
    """
    Fetch WTI Cushing spot price FOB (daily) from EIA v2 API.
    Series RWTC — dollars per barrel.
    """
    rows = _paginate_eia(
        "https://api.eia.gov/v2/petroleum/pri/spt/data/",
        {
            "frequency": "daily",
            "data[0]": "value",
            "facets[series][]": "RWTC",
            "start": start,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        },
    )

    if not rows:
        raise RuntimeError("No WTI price data returned from EIA.")

    df = pd.DataFrame(rows)[["period", "value"]]
    df.columns = ["date", "close"]
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Pulling EIA weekly crude inventories (ex-SPR)...")
    inv = get_crude_inventories()
    inv_path = DATA_DIR / "crude_inventories.csv"
    inv.to_csv(inv_path, index=False)
    print(f"  {len(inv)} weekly observations → data/crude_inventories.csv")
    print(f"  Latest: {inv['period'].iloc[-1].date()} | "
          f"Level: {inv['value'].iloc[-1]:,.0f} kbbl | "
          f"Change: {inv['weekly_change'].iloc[-1]:+,.0f} kbbl")

    print("\nPulling WTI Cushing spot prices (RWTC)...")
    wti = get_wti_prices()
    wti_path = DATA_DIR / "wti_prices.csv"
    wti.to_csv(wti_path, index=False)
    print(f"  {len(wti)} daily closes → data/wti_prices.csv")
    print(f"  Latest: {wti['date'].iloc[-1].date()} | ${wti['close'].iloc[-1]:.2f}")

    print("\nDone. Next step: compute the 5-year-average inventory surprise signal.")


if __name__ == "__main__":
    main()
