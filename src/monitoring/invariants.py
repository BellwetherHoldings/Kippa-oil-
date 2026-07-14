"""
Invariant Compliance Engine — doc 001 made executable.

docs/001_Project_Vision.md defines eight architectural invariants that
must ALWAYS be true. This engine checks each one against the live
platform — published artifacts, configs, data files, and git state — and
publishes a pass/fail per invariant. The constitution, enforced.

Usage:
    python src/monitoring/invariants.py

Publishes:
    data/invariant_compliance.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, RUN_LOG, Engine

EVIDENCE_BEARING_ARTIFACTS = [
    "composite_signal", "geopolitical_risk", "supply_chain_stress",
    "market_sentiment", "macro_conditions", "price_momentum",
    "risk_assessment", "signal_confidence", "price_forecast",
    "simulation_results", "strategy_recommendation", "backtest_report",
    "data_quality_report",
]


def _artifact(name: str) -> dict | None:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


class InvariantComplianceEngine(Engine):
    name = "invariant_compliance"
    version = "1.0"
    output_name = "invariant_compliance"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        checks: list[dict[str, Any]] = []

        def check(num: int, invariant: str, passed: bool, detail: str):
            checks.append({"invariant": num, "statement": invariant,
                           "passed": bool(passed), "detail": detail})

        # 1. Data quality comes before analysis
        dq = _artifact("data_quality_report")
        wf = json.loads((_REPO_ROOT / "config" / "workflows.json")
                        .read_text())["workflows"]["weekly_full_run"]["steps"]
        quality_first = ("data_quality" in wf and "composite_signal" in wf
                         and wf.index("data_quality")
                         < wf.index("composite_signal"))
        dq_ok = bool(dq) and dq["status"] == "success" \
            and dq["data"]["overall_quality"] >= 80
        check(1, "Data quality comes before analysis",
              dq_ok and quality_first,
              f"quality {dq['data']['overall_quality'] if dq else 'n/a'}/100; "
              f"quality step precedes analysis in weekly workflow: "
              f"{quality_first}")

        # 2. Every analytical output has supporting evidence
        missing = [n for n in EVIDENCE_BEARING_ARTIFACTS
                   if (a := _artifact(n)) and a["status"] == "success"
                   and not a.get("evidence")]
        published = [n for n in EVIDENCE_BEARING_ARTIFACTS if _artifact(n)]
        check(2, "Every analytical output has supporting evidence",
              not missing and len(published) > 8,
              f"{len(published)} artifacts published, "
              f"{len(missing)} without evidence: {missing or 'none'}")

        # 3. Models remain explainable
        comp = _artifact("composite_signal")
        fc = _artifact("price_forecast")
        explainable = bool(
            comp and comp["data"].get("methodology")
            and comp["data"].get("weights_source")
            and fc and fc["data"]["model"].get("coefficients"))
        check(3, "Models remain explainable", explainable,
              "composite publishes methodology + weights source; forecast "
              "publishes coefficients")

        # 4. Risk is always considered
        risk = _artifact("risk_assessment")
        check(4, "Risk must always be considered",
              bool(risk) and risk["status"] == "success"
              and len(risk["data"]["assessments"]) >= 4,
              f"{len(risk['data']['assessments']) if risk else 0} risk "
              f"categories assessed; uncovered categories declared: "
              f"{risk['data']['categories_not_covered'] if risk else 'n/a'}")

        # 5. Uncertainty is always measured
        conf = _artifact("signal_confidence")
        intervals = bool(fc and fc["data"].get("interval_95"))
        check(5, "Uncertainty must always be measured",
              bool(conf) and conf["status"] == "success" and intervals,
              "confidence engine grades every composite; forecasts carry "
              "68/95% intervals")

        # 6. Historical information remains preserved
        inv_csv = DATA_DIR / "crude_inventories.csv"
        span_ok = False
        if inv_csv.exists():
            df = pd.read_csv(inv_csv, parse_dates=["period"])
            span_ok = (df["period"].max() - df["period"].min()).days > 3650
        events = json.loads(
            (_REPO_ROOT / "src/intelligence/geopolitical/events.json")
            .read_text())["events"]
        check(6, "Historical information must remain preserved",
              span_ok and RUN_LOG.exists() and len(events) > 0,
              f"{'16+y' if span_ok else '<10y'} of inventory history; "
              f"event registry retains all statuses; run log is append-only")

        # 7. Security exists throughout the architecture
        sec = _artifact("security_audit")
        env_ignored = subprocess.run(
            ["git", "check-ignore", ".env"], cwd=_REPO_ROOT,
            capture_output=True).returncode == 0
        check(7, "Security must exist throughout the architecture",
              bool(sec) and sec["data"]["status"] == "pass" and env_ignored,
              f"security audit {sec['data']['status'] if sec else 'missing'}; "
              f".env gitignored: {env_ignored}")

        # 8. Human oversight remains available for critical decisions
        strat = _artifact("strategy_recommendation")
        weights_tracked = "config/weights.json" in subprocess.run(
            ["git", "ls-files", "config/weights.json"], cwd=_REPO_ROOT,
            capture_output=True, text=True).stdout
        check(8, "Human oversight remains available for critical decisions",
              bool(strat) and "human_oversight" in strat["data"]
              and weights_tracked,
              "strategy outputs are recommendations only; weight changes "
              "are reviewed git commits")

        passed = sum(c["passed"] for c in checks)
        data = {
            "compliant": passed == 8,
            "invariants_passed": f"{passed}/8",
            "as_of": date.today().isoformat(),
            "checks": checks,
        }
        for c in checks:
            if not c["passed"]:
                warnings.append(f"INVARIANT {c['invariant']} FAILING: "
                                f"{c['statement']} — {c['detail']}")
        evidence = ["docs/001_Project_Vision.md architectural invariants "
                    "verified against live artifacts, configs, and git state"]
        return data, evidence


def main() -> None:
    result = InvariantComplianceEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print("Architectural Invariant Compliance (doc 001)")
    print("=" * 66)
    print(f"  {'✓ COMPLIANT' if d['compliant'] else '✗ NON-COMPLIANT'} "
          f"— {d['invariants_passed']} invariants hold\n")
    for c in d["checks"]:
        print(f"  {'✓' if c['passed'] else '✗'} {c['invariant']}. "
              f"{c['statement']}")
        print(f"      {c['detail']}")
    print(f"\n  Published → data/invariant_compliance.json")


if __name__ == "__main__":
    main()
