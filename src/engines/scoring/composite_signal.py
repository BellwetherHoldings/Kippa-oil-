"""
Composite Signal Engine — the platform's integrated market read.

Governed by docs/006_Scoring_System.md (weighting, normalization,
explainability) with freshness-weighted confidence per
docs/008_Confidence_System.md. Born from a real failure: on 2026-07-14
the inventory signal alone printed "bearish" off July-3 data while the
Strait of Hormuz had just re-closed. No single signal may ever again be
presented as the market view (anti-pattern: single-source dependence).

Components (signal ∈ [-1, +1], positive = bullish for prices):
    inventory_surprise   — weekly EIA surprise z-score, inverted
    price_momentum       — 5-day WTI return, scaled
    geopolitical_risk    — event-driven supply risk, mapped to [0, 1]

Each component's weight is multiplied by a freshness confidence factor:
stale inputs lose voice instead of silently lying.

Usage:
    python src/engines/scoring/composite_signal.py

Publishes:
    data/composite_signal.json
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

from src.engines.base import DATA_DIR, Engine
from src.engines.analytics.price_momentum import PriceMomentumEngine
from src.intelligence.geopolitical.engine import GeopoliticalIntelligenceEngine

SURPRISE_PATH = DATA_DIR / "inventory_surprise.csv"

# Base weights (doc 006: weighting framework). Geopolitical carries the
# most weight while a chokepoint is disrupted; these are v1 priors to be
# re-estimated by the Wave 3 backtester.
WEIGHTS = {
    "geopolitical_risk": 0.30,
    "inventory_surprise": 0.20,
    "price_momentum": 0.20,
    "supply_chain_stress": 0.15,
    "market_sentiment": 0.15,
}

# Freshness model (doc 008 lite): each component has an expected update
# cadence; confidence decays linearly to the floor once data outruns it.
EXPECTED_LAG_DAYS = {
    "geopolitical_risk": 2,
    "inventory_surprise": 12,   # weekly series + EIA publication lag
    "price_momentum": 4,
    "supply_chain_stress": 12,  # weekly EIA series
    "market_sentiment": 12,     # COT published Friday for prior Tuesday
}
CONFIDENCE_FLOOR = 0.2
DECAY_DAYS = 21                 # days past expected lag to reach the floor

LABELS = (
    (-0.60, "strong bearish"),
    (-0.20, "bearish"),
    (0.20, "neutral"),
    (0.60, "bullish"),
    (2.00, "strong bullish"),
)


def _freshness_confidence(component: str, staleness_days: int) -> float:
    overdue = max(0, staleness_days - EXPECTED_LAG_DAYS[component])
    conf = 1.0 - overdue / DECAY_DAYS
    return round(max(CONFIDENCE_FLOOR, min(1.0, conf)), 2)


def _label(score: float) -> str:
    for threshold, label in LABELS:
        if score <= threshold:
            return label
    return "strong bullish"


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class CompositeSignalEngine(Engine):
    name = "composite_signal"
    version = "1.0"
    output_name = "composite_signal"

    def validate_input(self, inputs: dict[str, Any]) -> None:
        if not SURPRISE_PATH.exists():
            raise ValueError(
                f"{SURPRISE_PATH} not found. Run the inventory pipeline first."
            )

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        today = date.today()
        components: list[dict[str, Any]] = []
        evidence: list[str] = []

        # -- inventory surprise (weekly, stale by design) -------------------
        surprise = pd.read_csv(SURPRISE_PATH, parse_dates=["period"])
        latest = surprise.dropna(subset=["surprise_z"]).iloc[-1]
        inv_stale = (today - latest["period"].date()).days
        inv_signal = _clip(-float(latest["surprise_z"]) / 3.0)
        components.append({
            "component": "inventory_surprise",
            "signal": round(inv_signal, 3),
            "as_of": latest["period"].date().isoformat(),
            "staleness_days": inv_stale,
            "confidence": _freshness_confidence("inventory_surprise", inv_stale),
            "detail": f"surprise_z={latest['surprise_z']:+.2f} "
                      f"(surprise {latest['surprise']:+,.0f} kbbl vs seasonal)",
        })
        evidence.append(
            f"EIA weekly inventories through {latest['period'].date()} "
            f"(data/inventory_surprise.csv)"
        )

        # -- price momentum --------------------------------------------------
        momo = PriceMomentumEngine().run()
        if momo.ok:
            d = momo.data
            momo_signal = _clip(d["return_5d"] / 0.10)  # ±10% weekly = full score
            components.append({
                "component": "price_momentum",
                "signal": round(momo_signal, 3),
                "as_of": d["last_date"],
                "staleness_days": d["staleness_days"],
                "confidence": _freshness_confidence(
                    "price_momentum", d["staleness_days"]),
                "detail": f"5d {d['return_5d']:+.2%}, 20d {d['return_20d']:+.2%}, "
                          f"WTI ${d['last_close']:.2f}",
            })
            evidence.extend(momo.evidence)
            warnings.extend(momo.warnings)
        else:
            warnings.append(f"price_momentum failed and was excluded: {momo.error}")

        # -- geopolitical risk -----------------------------------------------
        geo = GeopoliticalIntelligenceEngine().run()
        if geo.ok:
            d = geo.data
            geo_stale = min(
                c["days_since_update"] for c in d["contributions"]
            )
            geo_signal = _clip(d["risk_score"] / 100.0, 0.0, 1.0)
            components.append({
                "component": "geopolitical_risk",
                "signal": round(geo_signal, 3),
                "as_of": d["as_of"],
                "staleness_days": geo_stale,
                "confidence": _freshness_confidence("geopolitical_risk", geo_stale),
                "detail": f"risk {d['risk_score']}/100 ({d['risk_level']}), "
                          f"chokepoints: {', '.join(d['chokepoints_disrupted']) or 'none'}",
            })
            evidence.extend(geo.evidence)
            warnings.extend(geo.warnings)
        else:
            warnings.append(f"geopolitical_risk failed and was excluded: {geo.error}")

        # -- supply chain stress ----------------------------------------------
        from src.intelligence.supply_chain.engine import (
            SupplyChainIntelligenceEngine,
        )
        supply = SupplyChainIntelligenceEngine().run()
        if supply.ok:
            d = supply.data
            supply_signal = _clip(d["stress_score"] / 100.0, 0.0, 1.0)
            components.append({
                "component": "supply_chain_stress",
                "signal": round(supply_signal, 3),
                "as_of": d["as_of"],
                "staleness_days": d["staleness_days"],
                "confidence": _freshness_confidence(
                    "supply_chain_stress", d["staleness_days"]),
                "detail": f"stress {d['stress_score']}/100 ({d['stress_level']}), "
                          f"{d['metrics_scored']} metrics",
            })
            evidence.extend(supply.evidence)
            warnings.extend(supply.warnings)
        else:
            warnings.append(
                f"supply_chain_stress failed and was excluded: {supply.error}")

        # -- market sentiment (CFTC positioning) --------------------------------
        from src.intelligence.market_sentiment.engine import MarketSentimentEngine
        senti = MarketSentimentEngine().run()
        if senti.ok:
            d = senti.data
            components.append({
                "component": "market_sentiment",
                "signal": d["sentiment_signal"],
                "as_of": d["as_of"],
                "staleness_days": d["staleness_days"],
                "confidence": _freshness_confidence(
                    "market_sentiment", d["staleness_days"]),
                "detail": f"net spec {d['net_noncommercial_contracts']:+,} "
                          f"contracts, 3y z {d['positioning_zscore_3y']:+.2f}",
            })
            evidence.extend(senti.evidence)
            warnings.extend(senti.warnings)
        else:
            warnings.append(
                f"market_sentiment failed and was excluded: {senti.error}")

        if len(components) < 2:
            raise ValueError(
                "Fewer than two components available — refusing to emit a "
                "single-signal verdict (anti-pattern)."
            )

        # -- aggregate (doc 006: weighted, normalized, explainable) -----------
        total_w = sum(
            WEIGHTS[c["component"]] * c["confidence"] for c in components
        )
        score = sum(
            WEIGHTS[c["component"]] * c["confidence"] * c["signal"]
            for c in components
        ) / total_w

        for c in components:
            c["weight"] = WEIGHTS[c["component"]]
            c["effective_weight"] = round(
                WEIGHTS[c["component"]] * c["confidence"] / total_w, 3
            )

        components.sort(key=lambda c: c["effective_weight"], reverse=True)

        data = {
            "composite_score": round(float(score), 3),
            "label": _label(float(score)),
            "as_of": today.isoformat(),
            "components": components,
            "methodology": (
                "score = Σ(weight × freshness_confidence × signal) / "
                "Σ(weight × freshness_confidence); signals normalized to "
                "[-1,+1], positive = bullish"
            ),
        }
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not -1.0 <= data["composite_score"] <= 1.0:
            raise ValueError(f"composite out of bounds: {data['composite_score']}")


def main() -> None:
    result = CompositeSignalEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Kippa Composite Market Signal")
    print("=" * 66)
    print(f"  COMPOSITE: {d['composite_score']:+.2f}  →  {d['label'].upper()}")
    print(f"  As of {d['as_of']}\n")
    print(f"  {'component':<20} {'signal':>7} {'conf':>5} {'eff.wt':>7}  as-of")
    print(f"  {'-'*62}")
    for c in d["components"]:
        print(f"  {c['component']:<20} {c['signal']:>+7.2f} "
              f"{c['confidence']:>5.2f} {c['effective_weight']:>6.1%}  "
              f"{c['as_of']} ({c['staleness_days']}d)")
        print(f"      {c['detail']}")
    if result.warnings:
        print()
        for w in result.warnings:
            print(f"  ⚠ {w}")
    print(f"\n  Published → data/composite_signal.json")


if __name__ == "__main__":
    main()
