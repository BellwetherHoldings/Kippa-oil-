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

Governed by docs/004_Data_Layer.md. The API key is loaded from .env
(gitignored) and is never logged, printed, or included in error messages.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

load_dotenv(REPO_ROOT / ".env")

EIA_INVENTORY_URL = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
EIA_SPOT_PRICE_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"

INVENTORY_SERIES = "WCESTUS1"  # U.S. commercial crude stocks, excl. SPR (kbbl)
WTI_SPOT_SERIES = "RWTC"       # Cushing, OK WTI spot price FOB ($/bbl)

MAX_ROWS_PER_CALL = 5000       # EIA v2 API page size cap
REQUEST_TIMEOUT = 30           # seconds
MAX_PAGES = 200                # hard stop against runaway pagination

_RETRY = Retry(
    total=4,
    backoff_factor=2,          # 2s, 4s, 8s, 16s
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),
)


def _api_key() -> str:
    key = os.getenv("EIA_API_KEY")
    if not key:
        raise RuntimeError(
            "EIA_API_KEY not found. Add it to your .env file in the repo root."
        )
    return key


def _session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=_RETRY))
    return session


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def _paginate_eia(url: str, params: dict) -> list[dict]:
    """Fetch every page of an EIA v2 endpoint, returning the combined rows.

    Errors are re-raised with the request URL stripped so the API key
    (a query parameter) never appears in tracebacks or logs.
    """
    key = _api_key()
    all_rows: list[dict] = []
    offset = 0

    with _session() as session:
        for _ in range(MAX_PAGES):
            page_params = {
                **params,
                "api_key": key,
                "offset": offset,
                "length": MAX_ROWS_PER_CALL,
            }
            try:
                resp = session.get(url, params=page_params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                status = getattr(exc.response, "status_code", "n/a")
                raise RuntimeError(
                    f"EIA API request failed (endpoint: {url}, "
                    f"status: {status}, offset: {offset})."
                ) from None  # drop original exception: its URL contains the key

            payload = resp.json()
            response = payload.get("response")
            if not isinstance(response, dict):
                raise RuntimeError(
                    f"Unexpected EIA API payload shape at offset {offset}: "
                    f"missing 'response' object."
                )

            rows = response.get("data", [])
            if not rows:
                break

            all_rows.extend(rows)
            offset += len(rows)

            total = int(response.get("total", 0))
            if offset >= total:
                break
        else:
            raise RuntimeError(
                f"Pagination exceeded {MAX_PAGES} pages — aborting as a "
                f"safeguard. Check the query parameters."
            )

    return all_rows


def _to_frame(
    rows: list[dict],
    date_col: str,
    value_col: str,
    label: str,
) -> pd.DataFrame:
    """Convert raw EIA rows to a clean two-column frame, sorted and deduped."""
    if not rows:
        raise RuntimeError(f"No {label} data returned from EIA.")

    df = pd.DataFrame(rows)[["period", "value"]]
    df.columns = [date_col, value_col]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")

    df = (
        df.dropna(subset=[value_col])
        .drop_duplicates(subset=[date_col], keep="last")
        .sort_values(date_col)
        .reset_index(drop=True)
    )
    if df.empty:
        raise RuntimeError(f"All {label} rows were empty or non-numeric.")
    return df


# ---------------------------------------------------------------------------
# EIA: generic series fetch (v1-style series IDs via the v2 passthrough)
# ---------------------------------------------------------------------------

def get_series(series_id: str, start: str = "2010-01-01") -> pd.DataFrame:
    """
    Fetch any EIA series by its v1-style ID (e.g. 'PET.WPULEUS3.W') via the
    v2 /seriesid/ passthrough. Returns columns: period, value (sorted asc).
    """
    rows = _paginate_eia(
        f"https://api.eia.gov/v2/seriesid/{series_id}",
        {"start": start},
    )
    if not rows:
        raise RuntimeError(f"No data returned for EIA series {series_id}.")

    df = pd.DataFrame(rows)[["period", "value"]]
    df["period"] = pd.to_datetime(df["period"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return (
        df.dropna(subset=["value"])
        .drop_duplicates(subset=["period"], keep="last")
        .sort_values("period")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# EIA: weekly crude inventories
# ---------------------------------------------------------------------------

def get_crude_inventories(start: str = "2010-01-01") -> pd.DataFrame:
    """
    Fetch weekly U.S. commercial crude inventories (ex-SPR) from EIA v2 API.
    Series WCESTUS1 — thousand barrels.

    Returns columns: period, value, weekly_change.
    """
    rows = _paginate_eia(
        EIA_INVENTORY_URL,
        {
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": INVENTORY_SERIES,
            "start": start,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        },
    )
    df = _to_frame(rows, "period", "value", "inventory")
    df["weekly_change"] = df["value"].diff()
    return df


# ---------------------------------------------------------------------------
# EIA: WTI spot prices
# ---------------------------------------------------------------------------

def get_wti_prices(start: str = "2010-01-01") -> pd.DataFrame:
    """
    Fetch WTI Cushing spot price FOB (daily) from EIA v2 API.
    Series RWTC — dollars per barrel.

    Returns columns: date, close.
    """
    rows = _paginate_eia(
        EIA_SPOT_PRICE_URL,
        {
            "frequency": "daily",
            "data[0]": "value",
            "facets[series][]": WTI_SPOT_SERIES,
            "start": start,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        },
    )
    return _to_frame(rows, "date", "close", "WTI price")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

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
