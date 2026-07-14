"""
Strategy Engine — decision support from the full intelligence stack.

Governed by docs/009_Strategy_System.md. Consumes the published outputs
of the composite, risk, confidence, forecast, and simulation engines and
produces a positioning recommendation: direction, conviction, sizing,
scenario asymmetry, invalidation triggers, and the top risks — always
with the reasoning attached. Decision support, not auto-execution:
human oversight stays in the loop for critical decisions (invariant #8).

Usage:
    python src/engines/strategy/engine.py
    (run `oil signal run` first so upstream artifacts exist)

Publishes:
    data/strategy_recommendation.json
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

SIZE_BY_TIER = {
    "very_high": 1.0,
    "high": 0.75,
    "moderate": 0.5,
    "low": 0.25,
    "very_low": 0.0,
}


def _load(name: str) -> dict[str, Any] | None:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return None
    art = json.loads(path.read_text())
    return art if art.get("status") == "success" else None


class StrategyEngine(Engine):
    name = "strategy"
    version = "1.0"
    output_name = "strategy_recommendation"

    def validate_input(self, inputs: dict[str, Any]) -> None:
        missing = [n for n in ("composite_signal", "risk_assessment",
                               "signal_confidence")
                   if not (DATA_DIR / f"{n}.json").exists()]
        if missing:
            raise ValueError(
                f"Missing upstream artifacts: {missing} — run oil signal run."
            )

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        comp = _load("composite_signal")
        risk = _load("risk_assessment")
        conf = _load("signal_confidence")
        if not (comp and risk and conf):
            raise ValueError("Upstream artifacts exist but latest runs failed.")

        cd, rd, fd = comp["data"], risk["data"], conf["data"]
        forecast = _load("price_forecast")
        sim = _load("simulation_results")

        score = cd["composite_score"]
        direction = ("long" if score > 0.2 else
                     "short" if score < -0.2 else "flat")
        tier = fd["confidence_tier"]
        conviction = round(abs(score) * fd["confidence_score"] / 100, 3)
        base_size = SIZE_BY_TIER[tier] * min(1.0, abs(score) / 0.6)

        # risk overlay: severe/critical platform risk halves size again
        risk_mult = 0.5 if rd["overall_risk_level"] in ("severe", "critical") \
            else 1.0
        size = round(base_size * risk_mult, 2)

        reasoning = [
            f"Composite {score:+.2f} ({cd['label']}) from "
            f"{len(cd['components'])} freshness-weighted components.",
            f"Confidence {fd['confidence_score']}/100 ({tier}) → "
            f"guidance: {fd['interpretation']}.",
            f"Platform risk {rd['overall_risk_score']}/100 "
            f"({rd['overall_risk_level']}, top: {rd['top_risk']}) → "
            f"size multiplier ×{risk_mult}.",
        ]

        asymmetry = None
        if sim:
            scen = {s["id"]: s for s in sim["data"]["scenarios"]}
            up = scen.get("hormuz_escalation")
            down = scen.get("hormuz_resolution")
            if up and down:
                spot = sim["data"]["anchor_price"]
                upside = up["terminal_percentiles"]["p50"] / spot - 1
                downside = down["terminal_percentiles"]["p50"] / spot - 1
                asymmetry = {
                    "escalation_median": round(upside, 3),
                    "resolution_median": round(downside, 3),
                    "read": "asymmetric to the upside"
                            if upside + downside > 0 else
                            "asymmetric to the downside",
                }
                reasoning.append(
                    f"Scenario medians: escalation {upside:+.0%} vs "
                    f"resolution {downside:+.0%} → {asymmetry['read']}.")

        if forecast:
            fc = forecast["data"]
            reasoning.append(
                f"Fundamentals baseline: ${fc['point_forecast']} in "
                f"{fc['horizon_days']}d (95%: ${fc['interval_95'][0]}–"
                f"${fc['interval_95'][1]}), regime-blind — see its warnings.")

        invalidation = [
            "Composite flips sign or drops below |0.2|.",
            "Hormuz reopens (watch us-iran-qatar-talks-2026 in the event "
            "registry) — the reopening-glut analog argues for fast de-risking.",
            f"Confidence tier falls below 'low' "
            f"(currently {tier}).",
        ]

        top_risks = [
            {"category": a["category"], "score": a["score"],
             "mitigation": a["mitigation"]}
            for a in rd["assessments"][:3]
        ]

        data = {
            "as_of": date.today().isoformat(),
            "stance": {
                "direction": direction,
                "conviction_0_1": conviction,
                "suggested_size_0_1": size,
                "horizon_days": forecast["data"]["horizon_days"]
                if forecast else 28,
            },
            "reasoning": reasoning,
            "scenario_asymmetry": asymmetry,
            "invalidation_triggers": invalidation,
            "top_risks": top_risks,
            "human_oversight": "Recommendation only — no automated "
                               "execution. Review reasoning and risks "
                               "before acting.",
        }
        evidence = [
            f"composite run {comp['run_id']}",
            f"risk run {risk['run_id']}",
            f"confidence run {conf['run_id']}",
        ]
        if forecast:
            evidence.append(f"forecast run {forecast['run_id']}")
        if sim:
            evidence.append(f"simulation run {sim['run_id']}")
        return data, evidence


def main() -> None:
    result = StrategyEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    s = d["stance"]
    print("Strategy Recommendation")
    print("=" * 62)
    print(f"  STANCE: {s['direction'].upper()}  |  conviction "
          f"{s['conviction_0_1']:.2f}  |  size {s['suggested_size_0_1']:.0%} "
          f"of normal  |  horizon {s['horizon_days']}d\n")
    print("  Reasoning:")
    for r in d["reasoning"]:
        print(f"    • {r}")
    print("\n  Invalidation triggers:")
    for t in d["invalidation_triggers"]:
        print(f"    ✗ {t}")
    print("\n  Top risks:")
    for r in d["top_risks"]:
        print(f"    [{r['score']:.0f}] {r['category']}: {r['mitigation']}")
    print(f"\n  {d['human_oversight']}")
    print(f"\n  Published → data/strategy_recommendation.json")


if __name__ == "__main__":
    main()
