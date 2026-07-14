"""
Confidence Engine — how much should the composite signal be trusted?

Governed by docs/008_Confidence_System.md. Scores four measurable factors
against the published composite artifact:

    data_quality    — mean freshness confidence of the composite's inputs
    agreement       — do the components point the same way? (weighted)
    uncertainty     — realized volatility regime (high vol = low confidence)
    coverage        — how many of the platform's signal engines contributed

and classifies the result on the doc 008 tier table (very_high ≥ 90 …
very_low < 40). A strong signal with low confidence is a research lead,
not a trade.

Usage:
    python src/engines/confidence/engine.py
    (run `oil signal run` first so upstream artifacts exist)

Publishes:
    data/signal_confidence.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine

FACTOR_WEIGHTS = {
    "data_quality": 0.30,
    "agreement": 0.25,
    "uncertainty": 0.25,
    "coverage": 0.20,
}

TIERS = (          # doc 008 classification table
    (90, "very_high"),
    (75, "high"),
    (60, "moderate"),
    (40, "low"),
    (0, "very_low"),
)

VOL_FLOOR = 0.15   # ≤15% annualized vol → full confidence
VOL_CEIL = 0.60    # ≥60% → zero
TARGET_COMPONENTS = 6   # geo, inventory, momentum, supply, sentiment, macro


def _tier(score: float) -> str:
    for threshold, label in TIERS:
        if score >= threshold:
            return label
    return "very_low"


class ConfidenceEngine(Engine):
    name = "confidence"
    version = "1.0"
    output_name = "signal_confidence"

    def validate_input(self, inputs: dict[str, Any]) -> None:
        if not (DATA_DIR / "composite_signal.json").exists():
            raise ValueError(
                "composite_signal.json not found — run oil signal run first."
            )

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        comp = json.loads((DATA_DIR / "composite_signal.json").read_text())
        if comp.get("status") != "success":
            raise ValueError("Latest composite run failed — nothing to grade.")

        cdata = comp["data"]
        components = cdata["components"]
        composite_score = cdata["composite_score"]

        factors: list[dict[str, Any]] = []

        # -- data quality: mean freshness confidence -------------------------
        avg_conf = sum(c["confidence"] for c in components) / len(components)
        factors.append({
            "factor": "data_quality",
            "score": round(avg_conf, 3),
            "detail": f"mean input freshness confidence across "
                      f"{len(components)} components",
        })

        # -- agreement: effective-weight share pointing with the composite ----
        if abs(composite_score) < 0.05:
            agreement = 0.5   # composite ~flat: agreement is undefined-ish
            detail = "composite near zero — directional agreement not meaningful"
        else:
            sign = 1 if composite_score > 0 else -1
            agreeing = sum(
                c["effective_weight"] for c in components
                if c["signal"] * sign > 0
            )
            agreement = agreeing
            detail = (f"{agreeing:.0%} of effective weight points "
                      f"{'bullish' if sign > 0 else 'bearish'} with the composite")
        factors.append({
            "factor": "agreement",
            "score": round(agreement, 3),
            "detail": detail,
        })

        # -- uncertainty: volatility regime ------------------------------------
        momo = json.loads((DATA_DIR / "price_momentum.json").read_text()) \
            if (DATA_DIR / "price_momentum.json").exists() else None
        if momo and momo.get("status") == "success":
            vol = momo["data"]["volatility_20d_annualized"]
            calm = 1.0 - (vol - VOL_FLOOR) / (VOL_CEIL - VOL_FLOOR)
            calm = max(0.0, min(1.0, calm))
            factors.append({
                "factor": "uncertainty",
                "score": round(calm, 3),
                "detail": f"20d realized vol {vol:.0%} annualized "
                          f"(≤{VOL_FLOOR:.0%} calm, ≥{VOL_CEIL:.0%} chaotic)",
            })
        else:
            warnings.append("price_momentum artifact missing — "
                            "uncertainty factor defaulted to 0.5.")
            factors.append({"factor": "uncertainty", "score": 0.5,
                            "detail": "no volatility data available"})

        # -- coverage -------------------------------------------------------------
        coverage = min(1.0, len(components) / TARGET_COMPONENTS)
        factors.append({
            "factor": "coverage",
            "score": round(coverage, 3),
            "detail": f"{len(components)}/{TARGET_COMPONENTS} signal engines "
                      f"contributed to the composite",
        })

        confidence = round(100 * sum(
            f["score"] * FACTOR_WEIGHTS[f["factor"]] for f in factors
        ), 1)

        data = {
            "confidence_score": confidence,
            "confidence_tier": _tier(confidence),
            "as_of": date.today().isoformat(),
            "graded_composite": {
                "score": composite_score,
                "label": cdata["label"],
                "as_of": cdata["as_of"],
            },
            "factors": factors,
            "interpretation": (
                "act" if confidence >= 75 else
                "act with reduced size" if confidence >= 60 else
                "treat as research lead, not a trade signal"
            ),
        }
        evidence = [f"data/composite_signal.json run "
                    f"{comp.get('run_id')} at {comp.get('finished_utc')}"]
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not 0 <= data["confidence_score"] <= 100:
            raise ValueError(f"confidence out of bounds: {data['confidence_score']}")


def main() -> None:
    result = ConfidenceEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    g = d["graded_composite"]
    print("Signal Confidence")
    print("=" * 60)
    print(f"  CONFIDENCE: {d['confidence_score']}/100  "
          f"({d['confidence_tier'].upper().replace('_', ' ')})")
    print(f"  Grading: composite {g['score']:+.2f} ({g['label']}) "
          f"as of {g['as_of']}")
    print(f"  Guidance: {d['interpretation']}\n")
    for f in d["factors"]:
        print(f"  [{f['score']:.2f}] {f['factor']:<13} {f['detail']}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/signal_confidence.json")


if __name__ == "__main__":
    main()
