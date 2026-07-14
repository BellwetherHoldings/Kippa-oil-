"""
Risk Engine — platform-wide risk assessment.

Governed by docs/007_Risk_System.md. Consumes the published outputs of the
intelligence and analytics engines (data/*.json) and assesses risk per
category as probability × impact, aggregated into an overall risk score.
Categories without a live data source are reported as not-covered rather
than silently omitted (invariant: uncertainty must always be measured).

Usage:
    python src/engines/risk/engine.py
    (run `oil signal run` first so upstream artifacts exist)

Publishes:
    data/risk_assessment.json
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

# Doc 007 defines nine categories; regulatory remains the only one
# without a live data source.
UNCOVERED_CATEGORIES = ["regulatory"]

RISK_LEVELS = (
    (20, "low"),
    (40, "elevated"),
    (60, "high"),
    (80, "severe"),
    (101, "critical"),
)

MITIGATIONS = {
    "geopolitical": "Track de-escalation paths (Qatar talks); stress-test "
                    "portfolio against both closure-persists and reopening "
                    "scenarios before positioning.",
    "supply": "Monitor SPR drawdown rate and refinery maintenance schedules; "
              "a thin reserve buffer amplifies any further disruption.",
    "market": "Elevated volatility regime — size positions down and widen "
              "stops; headline-driven whipsaws are the dominant tape risk.",
    "model_data": "Refresh stale feeds before acting on composite output; "
                  "the weekly EIA cadence cannot see intra-week shocks.",
    "economic": "Watch dollar strength and industrial production momentum; "
                "a macro headwind can cap price upside even in a supply "
                "shock.",
    "demand": "Track industrial production and refinery runs; demand "
              "destruction above ~$100 WTI historically caps rallies.",
    "operational": "Resolve monitoring alerts before relying on outputs; "
                   "degraded pipelines produce stale or missing signals.",
    "cybersecurity": "Keep the security audit green: secrets in .env only, "
                     "rotate keys on exposure, review audit findings "
                     "before every push.",
}


def _risk_level(score: float) -> str:
    for threshold, label in RISK_LEVELS:
        if score < threshold:
            return label
    return "critical"


def _load(name: str) -> dict[str, Any] | None:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


class RiskEngine(Engine):
    name = "risk"
    version = "1.0"
    output_name = "risk_assessment"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        assessments: list[dict[str, Any]] = []
        evidence: list[str] = []

        # -- geopolitical risk -------------------------------------------------
        geo = _load("geopolitical_risk")
        if geo and geo.get("status") == "success":
            d = geo["data"]
            prob = min(1.0, d["risk_score"] / 100 + 0.1)
            impact = 0.9 if d["chokepoints_disrupted"] else 0.6
            assessments.append({
                "category": "geopolitical",
                "probability": round(prob, 2),
                "impact": round(impact, 2),
                "score": round(100 * prob * impact, 1),
                "basis": f"Geo risk {d['risk_score']}/100 ({d['risk_level']}); "
                         f"chokepoints: {', '.join(d['chokepoints_disrupted']) or 'none'}",
                "mitigation": MITIGATIONS["geopolitical"],
            })
            evidence.extend(geo.get("evidence", []))
        else:
            warnings.append("geopolitical artifact missing — run oil signal run.")

        # -- supply risk ---------------------------------------------------------
        supply = _load("supply_chain_stress")
        if supply and supply.get("status") == "success":
            d = supply["data"]
            prob = d["stress_score"] / 100
            spr = next((c for c in d["components"]
                        if c["metric"] == "spr_level"), None)
            impact = 0.8 if spr and spr["percentile_3y"] < 0.1 else 0.6
            assessments.append({
                "category": "supply",
                "probability": round(prob, 2),
                "impact": round(impact, 2),
                "score": round(100 * prob * impact, 1),
                "basis": f"Supply stress {d['stress_score']}/100 "
                         f"({d['stress_level']}); SPR at "
                         f"{spr['percentile_3y']:.0%} percentile" if spr else
                         f"Supply stress {d['stress_score']}/100",
                "mitigation": MITIGATIONS["supply"],
            })
            evidence.extend(supply.get("evidence", []))
        else:
            warnings.append("supply chain artifact missing — run oil signal run.")

        # -- market risk (volatility regime) --------------------------------------
        momo = _load("price_momentum")
        if momo and momo.get("status") == "success":
            d = momo["data"]
            vol = d["volatility_20d_annualized"]
            prob = max(0.0, min(1.0, (vol - 0.15) / 0.45))  # 15%→0, 60%→1
            impact = 0.7
            assessments.append({
                "category": "market",
                "probability": round(prob, 2),
                "impact": impact,
                "score": round(100 * prob * impact, 1),
                "basis": f"20d realized vol {vol:.0%} annualized; "
                         f"20d return {d['return_20d']:+.1%}"
                         + (" — spike flagged" if d["spike_flag"] else ""),
                "mitigation": MITIGATIONS["market"],
            })
            evidence.extend(momo.get("evidence", []))

        # -- economic risk (macro headwind for demand) -------------------------
        macro = _load("macro_conditions")
        if macro and macro.get("status") == "success":
            d = macro["data"]
            # bearish macro (signal → -1) = high economic risk to prices
            prob = round((1.0 - d["macro_signal"]) / 2.0, 2)
            assessments.append({
                "category": "economic",
                "probability": prob,
                "impact": 0.6,
                "score": round(100 * prob * 0.6, 1),
                "basis": f"Macro signal {d['macro_signal']:+.2f} "
                         f"({d['macro_label']}), "
                         f"{d['indicators_scored']} FRED indicators",
                "mitigation": MITIGATIONS["economic"],
            })
            evidence.extend(macro.get("evidence", []))

        # -- demand risk (industrial demand rolling over) -------------------------
        if macro and macro.get("status") == "success":
            indpro = next(
                (c for c in macro["data"]["components"]
                 if c["indicator"] == "industrial_production"), None)
            if indpro:
                prob = round((1.0 - indpro["signal"]) / 2.0, 2)
                assessments.append({
                    "category": "demand",
                    "probability": prob,
                    "impact": 0.7,
                    "score": round(100 * prob * 0.7, 1),
                    "basis": f"Industrial production signal "
                             f"{indpro['signal']:+.2f} ({indpro['detail']})",
                    "mitigation": MITIGATIONS["demand"],
                })

        # -- operational risk (our own pipeline health) ----------------------------
        mon = _load("monitoring_report")
        if mon and mon.get("status") == "success":
            health = mon["data"]["health"]
            prob = {"healthy": 0.1, "degraded": 0.5, "critical": 0.9}[health]
            assessments.append({
                "category": "operational",
                "probability": prob,
                "impact": 0.5,
                "score": round(100 * prob * 0.5, 1),
                "basis": f"Platform health {health} "
                         f"({len(mon['data']['alerts'])} alerts)",
                "mitigation": MITIGATIONS["operational"],
            })

        # -- cybersecurity risk (secrets hygiene) -----------------------------------
        sec = _load("security_audit")
        if sec and sec.get("status") == "success":
            audit_status = sec["data"]["status"]
            prob = 0.1 if audit_status == "pass" else 0.9
            assessments.append({
                "category": "cybersecurity",
                "probability": prob,
                "impact": 0.8,
                "score": round(100 * prob * 0.8, 1),
                "basis": f"Security audit {audit_status} "
                         f"({sec['data']['tracked_files_scanned']} files, "
                         f"{len(sec['data']['findings'])} findings)",
                "mitigation": MITIGATIONS["cybersecurity"],
            })

        # -- model/data risk (staleness of our own inputs) --------------------------
        comp = _load("composite_signal")
        if comp and comp.get("status") == "success":
            comps = comp["data"]["components"]
            avg_conf = sum(c["confidence"] for c in comps) / len(comps)
            prob = round(1.0 - avg_conf, 2)
            worst = min(comps, key=lambda c: c["confidence"])
            assessments.append({
                "category": "model_data",
                "probability": prob,
                "impact": 0.5,
                "score": round(100 * prob * 0.5, 1),
                "basis": f"Mean input freshness confidence {avg_conf:.2f}; "
                         f"weakest: {worst['component']} "
                         f"({worst['staleness_days']}d stale)",
                "mitigation": MITIGATIONS["model_data"],
            })

        if len(assessments) < 2:
            raise ValueError(
                "Fewer than two risk categories scoreable — run the signal "
                "pipeline first (oil signal run)."
            )

        assessments.sort(key=lambda a: a["score"], reverse=True)
        scores = [a["score"] for a in assessments]
        overall = round(0.6 * max(scores) + 0.4 * (sum(scores) / len(scores)), 1)

        data = {
            "overall_risk_score": overall,
            "overall_risk_level": _risk_level(overall),
            "as_of": date.today().isoformat(),
            "top_risk": assessments[0]["category"],
            "assessments": assessments,
            "categories_not_covered": UNCOVERED_CATEGORIES,
            "methodology": "per-category probability × impact; overall = "
                           "0.6·max + 0.4·mean (worst risk dominates)",
        }
        return data, sorted(set(evidence))

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not 0 <= data["overall_risk_score"] <= 100:
            raise ValueError(f"risk out of bounds: {data['overall_risk_score']}")


def main() -> None:
    result = RiskEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Risk Assessment")
    print("=" * 60)
    print(f"  OVERALL: {d['overall_risk_score']}/100  "
          f"({d['overall_risk_level'].upper()})  — top risk: {d['top_risk']}\n")
    for a in d["assessments"]:
        print(f"  [{a['score']:>5.1f}] {a['category']:<12} "
              f"p={a['probability']:.2f} × impact={a['impact']:.2f}")
        print(f"          {a['basis']}")
        print(f"          → {a['mitigation']}")
    print(f"\n  Not yet covered: {', '.join(d['categories_not_covered'])}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/risk_assessment.json")


if __name__ == "__main__":
    main()
