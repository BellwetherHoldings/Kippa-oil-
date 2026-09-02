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
    chokepoint_capacity  — realized shipping capacity through the strait

Each component's weight is multiplied by a freshness confidence factor:
stale inputs lose voice instead of silently lying.

Usage:
    python src/engines/scoring/composite_signal.py

Publishes:
    data/composite_signal.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine, clip as _clip
from src.engines.analytics.price_momentum import PriceMomentumEngine
from src.intelligence.geopolitical.engine import GeopoliticalIntelligenceEngine

SURPRISE_PATH = DATA_DIR / "inventory_surprise.csv"

# Weights come from config/weights.json — a version-controlled, evidence-
# backed file (doc 006: weighting needs evidence; changes are reviewed
# commits). The constants below are only the fallback if the config is
# absent.
WEIGHTS_CONFIG = _REPO_ROOT / "config" / "weights.json"

# How much a capacity TREND (more_open / more_restrictive) tilts the
# chokepoint signal beyond its level. Deliberately modest: direction is a
# leading hint, not the measurement.
CAPACITY_TREND_TILT = 0.15

_DEFAULT_WEIGHTS = {
    "geopolitical_risk": 0.28,
    "inventory_surprise": 0.18,
    "price_momentum": 0.18,
    "chokepoint_capacity": 0.00,   # config/weights.json is the real source
    "supply_chain_stress": 0.14,
    "market_sentiment": 0.12,
    "macro_conditions": 0.10,
}


def _load_weights() -> tuple[dict[str, float], str]:
    """Loaded per-run (not at import) so a bad config fails the engine's
    validate stage instead of breaking every module that imports this one."""
    if WEIGHTS_CONFIG.exists():
        import json
        cfg = json.loads(WEIGHTS_CONFIG.read_text())
        w = cfg["weights"]
        total = sum(w.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"config/weights.json weights sum to {total}, not 1.0")
        return w, f"config/weights.json v{cfg['version']}"
    return _DEFAULT_WEIGHTS, "built-in defaults (no config/weights.json)"

# Freshness model (doc 008 lite): each component has an expected update
# cadence; confidence decays linearly to the floor once data outruns it.
EXPECTED_LAG_DAYS = {
    "geopolitical_risk": 2,
    "chokepoint_capacity": 2,    # shipping conditions move daily in a crisis
    "inventory_surprise": 12,   # weekly series + EIA publication lag
    "price_momentum": 4,
    "supply_chain_stress": 12,  # weekly EIA series
    "market_sentiment": 12,     # COT published Friday for prior Tuesday
    "macro_conditions": 6,      # daily FRED series with short lags
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



def _previous_component_set() -> set[str] | None:
    """
    Component set from the most recent signal-log row, or None if there is no
    usable history. Used to detect that the composite's basis changed between
    runs — see the comparability guard in execute().
    """
    path = _REPO_ROOT / "data" / "signal_log.jsonl"
    if not path.exists():
        return None
    last = None
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if last is None:
            return None
        comps = json.loads(last).get("components")
    except (OSError, ValueError):
        return None
    if isinstance(comps, dict):
        return set(comps)
    if isinstance(comps, list):
        out = {c.get("component") for c in comps if isinstance(c, dict)}
        return {c for c in out if c} or None
    return None


class CompositeSignalEngine(Engine):
    name = "composite_signal"
    version = "1.0"
    output_name = "composite_signal"

    # Components that must be present for the score to mean what the
    # historical series means. Missing any of these does not stop the run,
    # but it does mark the output not-comparable — see execute().
    BENCHMARK_COMPONENTS = ("inventory_surprise",)

    def validate_input(self, inputs: dict[str, Any]) -> None:
        # inventory_surprise used to hard-fail here while every other
        # component degraded with a warning. That asymmetry took the whole
        # composite — and confidence, strategy, forecasting, the Discord
        # embed and signal capture behind it — dark whenever EIA was
        # unreachable. It now degrades like the rest, and the output says
        # loudly that it is degraded rather than pretending otherwise.
        _load_weights()   # fail here, in the validate stage, on a bad config

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        today = date.today()
        weights, weights_source = _load_weights()
        components: list[dict[str, Any]] = []
        evidence: list[str] = []

        # -- inventory surprise (weekly, stale by design) -------------------
        if SURPRISE_PATH.exists():
            surprise = pd.read_csv(SURPRISE_PATH, parse_dates=["period"])
            rows = surprise.dropna(subset=["surprise_z"])
            if rows.empty:
                warnings.append(
                    "inventory_surprise excluded: no scored rows in "
                    f"{SURPRISE_PATH.name}."
                )
            else:
                latest = rows.iloc[-1]
                inv_stale = (today - latest["period"].date()).days
                inv_signal = _clip(-float(latest["surprise_z"]) / 3.0)
                components.append({
                    "component": "inventory_surprise",
                    "signal": round(inv_signal, 3),
                    "as_of": latest["period"].date().isoformat(),
                    "staleness_days": inv_stale,
                    "confidence": _freshness_confidence(
                        "inventory_surprise", inv_stale),
                    "detail": f"surprise_z={latest['surprise_z']:+.2f} "
                              f"(surprise {latest['surprise']:+,.0f} kbbl "
                              f"vs seasonal)",
                })
                evidence.append(
                    f"EIA weekly inventories through "
                    f"{latest['period'].date()} "
                    f"(data/inventory_surprise.csv)"
                )
        else:
            warnings.append(
                f"inventory_surprise excluded: {SURPRISE_PATH.name} not "
                f"found (EIA unreachable). The composite is NOT comparable "
                f"to scores that included it — see degraded_vs_benchmark."
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

        # -- chokepoint capacity (realized shipping, not rhetoric) -------------
        # Measures how much oil physically CANNOT move, from validated
        # observations of the strait itself. Separate from geopolitical_risk
        # on purpose: that engine scores how bad the situation is, this one
        # scores whether the barrels are actually stopping. In July 2026 those
        # diverged hard — headlines maxed, ~8.5M bbl/day still transiting —
        # and only this measure would have caught it.
        from src.intelligence.geopolitical import strait_status
        cap_obs = strait_status.latest_status("strait_of_hormuz")
        if cap_obs:
            cap_stale = (today - date.fromisoformat(
                cap_obs["observed_date"])).days
            cap_pct = float(cap_obs["estimated_shipping_capacity_percent"])
            # level: fraction of flow blocked, [0, 1] bullish-only
            disruption = 1.0 - cap_pct / 100.0
            # direction: the leading part. Capacity improving pulls the signal
            # down even while the level is still elevated (and vice versa) —
            # a reopening also unwinds accumulated premium (the "glut trade").
            tilt = {"more_open": -CAPACITY_TREND_TILT,
                    "more_restrictive": CAPACITY_TREND_TILT,
                    "no_change": 0.0}[cap_obs["change_from_previous"]]
            cap_signal = _clip(disruption + tilt)
            components.append({
                "component": "chokepoint_capacity",
                "signal": round(cap_signal, 3),
                "as_of": cap_obs["observed_date"],
                "staleness_days": cap_stale,
                "confidence": round(
                    _freshness_confidence("chokepoint_capacity", cap_stale)
                    * float(cap_obs.get("confidence", 1.0)), 2),
                "detail": f"{cap_pct:.0f}% capacity "
                          f"({cap_obs['strait_status']}, "
                          f"{cap_obs['change_from_previous']}), shipping "
                          f"{cap_obs['commercial_shipping']}",
            })
            evidence.extend(cap_obs.get("sources", []))
        else:
            warnings.append(
                "chokepoint_capacity excluded — no shipping-status "
                "observation on file (ingest one: oil geo strait <file.json>)")

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

        # -- macroeconomic conditions (FRED) --------------------------------
        from src.intelligence.macroeconomic.engine import (
            MacroeconomicIntelligenceEngine,
        )
        macro = MacroeconomicIntelligenceEngine().run()
        if macro.ok:
            d = macro.data
            components.append({
                "component": "macro_conditions",
                "signal": d["macro_signal"],
                "as_of": d["as_of"],
                "staleness_days": d["staleness_days"],
                "confidence": _freshness_confidence(
                    "macro_conditions", d["staleness_days"]),
                "detail": f"macro {d['macro_label']}, "
                          f"{d['indicators_scored']} FRED indicators",
            })
            evidence.extend(macro.evidence)
            warnings.extend(macro.warnings)
        else:
            warnings.append(
                f"macro_conditions failed and was excluded: {macro.error}")

        if len(components) < 2:
            raise ValueError(
                "Fewer than two components available — refusing to emit a "
                "single-signal verdict (anti-pattern)."
            )

        # -- aggregate (doc 006: weighted, normalized, explainable) -----------
        total_w = sum(
            weights[c["component"]] * c["confidence"] for c in components
        )
        score = sum(
            weights[c["component"]] * c["confidence"] * c["signal"]
            for c in components
        ) / total_w

        for c in components:
            c["weight"] = weights[c["component"]]
            c["effective_weight"] = round(
                weights[c["component"]] * c["confidence"] / total_w, 3
            )

        components.sort(key=lambda c: c["effective_weight"], reverse=True)

        # -- comparability guard (doc 006 explainability) ---------------------
        # A score built from a different component set is a different
        # statistic wearing the same name. Say so in the artifact itself so
        # no downstream reader — Discord embed, signal log, report table —
        # can silently compare across a composition change.
        present = {c["component"] for c in components}
        missing_benchmark = [
            c for c in self.BENCHMARK_COMPONENTS if c not in present
        ]
        excluded_weight = round(
            sum(weights[c] for c in missing_benchmark), 3
        )

        # The comment above promised that a composition change would be
        # announced. Until 2026-08-21 it was not: the check only looked at
        # BENCHMARK_COMPONENTS (inventory alone), so a component appearing or
        # vanishing anywhere else passed silently with comparable_to_history
        # still reading true. That bit the same day macro_conditions came
        # online and turned a 6-component composite into a 7-component one
        # mid-series — every row in data/signal_log.jsonl before that point is
        # a different statistic from every row after it.
        prev_set = _previous_component_set()
        composition_change = None
        if prev_set is not None and prev_set != present:
            added = sorted(present - prev_set)
            removed = sorted(prev_set - present)
            composition_change = {
                "added": added,
                "removed": removed,
                "previous_count": len(prev_set),
                "current_count": len(present),
            }
            bits = []
            if added:
                bits.append("added " + ", ".join(added))
            if removed:
                bits.append("removed " + ", ".join(removed))
            warnings.append(
                f"COMPOSITION CHANGE: {'; '.join(bits)} "
                f"({len(prev_set)} → {len(present)} components). Scores before "
                f"and after this run are different statistics sharing a name — "
                f"do not compare them, and treat any backtest or ledger "
                f"spanning this boundary as spanning a regime break."
            )

        data = {
            "composite_score": round(float(score), 3),
            "label": _label(float(score)),
            "as_of": today.isoformat(),
            "components_scored": len(components),
            "degraded_vs_benchmark": bool(missing_benchmark),
            "missing_benchmark_components": missing_benchmark,
            "excluded_nominal_weight": excluded_weight,
            "comparable_to_history": not missing_benchmark
                                     and composition_change is None,
            "composition_change": composition_change,
            "components": components,
            "methodology": (
                "score = Σ(weight × freshness_confidence × signal) / "
                "Σ(weight × freshness_confidence); signals normalized to "
                "[-1,+1], positive = bullish"
            ),
            "weights_source": weights_source,
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
