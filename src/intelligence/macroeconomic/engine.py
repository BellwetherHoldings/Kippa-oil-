"""
Macroeconomic Intelligence Engine — demand-side macro conditions for oil.

Fills the macroeconomic slot in src/intelligence/ (referenced by docs 021
and 022 integration lists). Reads three FRED series and scores the macro
backdrop for oil demand on [-1, +1] (positive = bullish for prices):

    DTWEXBGS  Broad dollar index    — strong/rising dollar = bearish oil
    INDPRO    Industrial production — growth = physical demand
    T10YIE    10y breakeven CPI     — rising inflation expectations = bullish

Setup: FRED_API_KEY in .env (free key: https://fred.stlouisfed.org/docs/api/api_key.html)

Usage:
    python src/intelligence/macroeconomic/engine.py

Publishes:
    data/macro_conditions.json
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine

load_dotenv(_REPO_ROOT / ".env")

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

# indicator: (series, weight, direction of raw change for oil prices)
INDICATORS = {
    "dollar_index": {
        "series": "DTWEXBGS",
        "label": "Broad dollar index (3m change, 3y z)",
        "weight": 0.40,
        "invert": True,        # rising dollar = bearish oil
    },
    "industrial_production": {
        "series": "INDPRO",
        "label": "US industrial production (6m annualized growth)",
        "weight": 0.35,
        "invert": False,
    },
    "inflation_expectations": {
        "series": "T10YIE",
        "label": "10y breakeven inflation (3m change, 3y z)",
        "weight": 0.25,
        "invert": False,
    },
}

FULL_SCORE_Z = 2.0
INDPRO_FULL_GROWTH = 0.05     # ±5% annualized growth maps to ±1


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _fred_series(series_id: str, start: str = "2018-01-01") -> pd.DataFrame:
    key = os.getenv("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not found in .env.")
    try:
        resp = requests.get(
            FRED_URL,
            params={
                "series_id": series_id,
                "api_key": key,
                "file_type": "json",
                "observation_start": start,
                "sort_order": "asc",
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(exc.response, "status_code", "n/a")
        raise RuntimeError(
            f"FRED request failed (series: {series_id}, status: {status})."
        ) from None  # original URL carries the API key — never propagate
    obs = resp.json().get("observations", [])
    df = pd.DataFrame(obs)[["date", "value"]]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # '.' → NaN
    return df.dropna().sort_values("date").reset_index(drop=True)


class MacroeconomicIntelligenceEngine(Engine):
    name = "macroeconomic_intelligence"
    version = "1.0"
    output_name = "macro_conditions"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        today = date.today()
        components: list[dict[str, Any]] = []
        evidence: list[str] = []
        newest: date | None = None

        for key, spec in INDICATORS.items():
            try:
                df = _fred_series(spec["series"])
            except Exception as exc:
                warnings.append(f"{key} unavailable ({exc}) — excluded.")
                continue

            latest_date = df["date"].iloc[-1].date()
            newest = max(newest, latest_date) if newest else latest_date

            if key == "industrial_production":
                # monthly: 6m annualized growth mapped to ±1
                if len(df) < 8:
                    warnings.append(f"{key}: insufficient history — excluded.")
                    continue
                growth = (df["value"].iloc[-1] / df["value"].iloc[-7]) ** 2 - 1
                raw = float(growth)
                signal = _clip(raw / INDPRO_FULL_GROWTH)
                detail = f"6m annualized growth {raw:+.1%}"
            else:
                # daily: 3-month (63 obs) change, z vs 3y of rolling changes
                if len(df) < 400:
                    warnings.append(f"{key}: insufficient history — excluded.")
                    continue
                changes = df["value"].diff(63).dropna()
                latest_chg = float(changes.iloc[-1])
                z = float((latest_chg - changes.mean()) / changes.std())
                raw = z
                signal = _clip(z / FULL_SCORE_Z)
                detail = (f"3m change {latest_chg:+.2f} "
                          f"(z {z:+.2f} vs 3y), level {df['value'].iloc[-1]:.2f}")

            if spec["invert"]:
                signal = -signal

            components.append({
                "indicator": key,
                "label": spec["label"],
                "signal": round(signal, 3),
                "raw": round(raw, 4),
                "as_of": latest_date.isoformat(),
                "weight": spec["weight"],
                "detail": detail,
            })
            evidence.append(
                f"FRED {spec['series']} through {latest_date} "
                f"({len(df)} observations)"
            )

        if not components:
            raise ValueError("No macro indicators available.")

        total_w = sum(c["weight"] for c in components)
        macro_signal = round(
            sum(c["signal"] * c["weight"] for c in components) / total_w, 3
        )

        if macro_signal > 0.2:
            label = "tailwind"
        elif macro_signal < -0.2:
            label = "headwind"
        else:
            label = "neutral"

        staleness = (today - newest).days if newest else 999

        data = {
            "macro_signal": macro_signal,
            "macro_label": label,
            "as_of": newest.isoformat() if newest else None,
            "staleness_days": staleness,
            "indicators_scored": len(components),
            "components": sorted(
                components, key=lambda c: abs(c["signal"] * c["weight"]),
                reverse=True,
            ),
        }
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not -1.0 <= data["macro_signal"] <= 1.0:
            raise ValueError(f"macro_signal out of bounds: {data['macro_signal']}")


def main() -> None:
    result = MacroeconomicIntelligenceEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Macroeconomic Intelligence — Oil Demand Backdrop")
    print("=" * 60)
    print(f"  Macro signal: {d['macro_signal']:+.2f}  "
          f"({d['macro_label'].upper()})")
    print(f"  As of {d['as_of']} ({d['staleness_days']}d old)\n")
    for c in d["components"]:
        print(f"  [{c['signal']:+.2f}] {c['label']}")
        print(f"          {c['detail']} (as of {c['as_of']})")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/macro_conditions.json")


if __name__ == "__main__":
    main()
