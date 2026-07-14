"""
Supply Chain Intelligence Engine — U.S. physical supply stress scoring.

Governed by docs/020_Supply_Chain_Intelligence.md. Monitors four weekly
EIA series covering the physical system — refinery utilization, Strategic
Petroleum Reserve, domestic production, and crude imports — and scores
each against its own 3-year history to produce a 0-100 supply stress
score. High stress = tight physical supply = bullish price pressure.

Usage:
    python src/intelligence/supply_chain/engine.py

Publishes:
    data/supply_chain_stress.json
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

from src.data.eia_client import get_series
from src.engines.base import Engine

HISTORY_WEEKS = 156          # 3-year percentile window

# Each metric: EIA series, and whether a HIGH reading means MORE stress.
# Weights sum to 1 (doc 020, impact scoring).
METRICS = {
    "refinery_utilization": {
        "series": "PET.WPULEUS3.W",
        "label": "Refinery utilization (%)",
        "high_is_stress": True,     # running hot = no slack
        "weight": 0.30,
    },
    "spr_level": {
        "series": "PET.WCSSTUS1.W",
        "label": "Strategic Petroleum Reserve (kbbl)",
        "high_is_stress": False,    # low reserve = thin buffer
        "weight": 0.25,
    },
    "crude_production": {
        "series": "PET.WCRFPUS2.W",
        "label": "U.S. crude production (kbbl/d)",
        "high_is_stress": False,    # falling output = stress
        "weight": 0.25,
    },
    "crude_imports": {
        "series": "PET.WCRIMUS2.W",
        "label": "U.S. crude imports (kbbl/d)",
        "high_is_stress": False,    # falling imports = stress
        "weight": 0.20,
    },
}

STRESS_LEVELS = (
    (20, "low"),
    (40, "moderate"),
    (60, "elevated"),
    (80, "high"),
    (101, "critical"),
)


def _stress_level(score: float) -> str:
    for threshold, label in STRESS_LEVELS:
        if score < threshold:
            return label
    return "critical"


class SupplyChainIntelligenceEngine(Engine):
    name = "supply_chain_intelligence"
    version = "1.0"
    output_name = "supply_chain_stress"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        today = date.today()
        components: list[dict[str, Any]] = []
        evidence: list[str] = []
        total_stress = 0.0
        total_weight = 0.0
        newest: date | None = None

        for key, spec in METRICS.items():
            try:
                df = get_series(spec["series"], start="2018-01-01")
            except Exception as exc:
                warnings.append(f"{key} unavailable ({exc}) — excluded.")
                continue

            history = df.tail(HISTORY_WEEKS)
            latest = history.iloc[-1]
            latest_date = latest["period"].date()
            newest = max(newest, latest_date) if newest else latest_date

            # Percentile of the latest reading within its own 3y history
            pct = float((history["value"] <= latest["value"]).mean())
            stress = pct if spec["high_is_stress"] else 1.0 - pct

            change_4w = float(
                latest["value"] - history.iloc[-5]["value"]
            ) if len(history) >= 5 else 0.0

            components.append({
                "metric": key,
                "label": spec["label"],
                "latest_value": float(latest["value"]),
                "as_of": latest_date.isoformat(),
                "percentile_3y": round(pct, 3),
                "change_4w": round(change_4w, 1),
                "stress": round(stress, 3),
                "weight": spec["weight"],
            })
            evidence.append(
                f"EIA {spec['series']} through {latest_date} "
                f"({len(history)} weekly observations)"
            )
            total_stress += stress * spec["weight"]
            total_weight += spec["weight"]

        if total_weight < 0.5:
            raise ValueError(
                "Too few supply chain metrics available to score reliably."
            )

        stress_score = round(100 * total_stress / total_weight, 1)
        components.sort(key=lambda c: c["stress"] * c["weight"], reverse=True)

        staleness = (today - newest).days if newest else 999

        data = {
            "stress_score": stress_score,
            "stress_level": _stress_level(stress_score),
            "as_of": newest.isoformat() if newest else None,
            "staleness_days": staleness,
            "direction_for_prices": "bullish" if stress_score >= 50 else "neutral",
            "metrics_scored": len(components),
            "components": components,
        }
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not 0 <= data["stress_score"] <= 100:
            raise ValueError(f"stress_score out of bounds: {data['stress_score']}")


def main() -> None:
    result = SupplyChainIntelligenceEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Supply Chain Intelligence — U.S. Physical Supply Stress")
    print("=" * 60)
    print(f"  Stress score: {d['stress_score']}/100  ({d['stress_level'].upper()})")
    print(f"  As of {d['as_of']} ({d['staleness_days']}d old)\n")
    for c in d["components"]:
        print(f"  [{c['stress']:.2f}] {c['label']}")
        print(f"         latest {c['latest_value']:,.1f} | 3y percentile "
              f"{c['percentile_3y']:.0%} | 4w change {c['change_4w']:+,.1f}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/supply_chain_stress.json")


if __name__ == "__main__":
    main()
