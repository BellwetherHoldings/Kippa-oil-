"""
Engine Registry — doc 005's living map of every engine on the platform,
plus the full-system orchestrator.

REGISTRY maps all 22 architecture documents to their runnable engine,
artifact, and a headline extractor. `run_full_system()` executes the
entire platform in dependency order and renders the doc-by-doc board —
the single command that proves every document has a working counterpart.

The composite (006) runs the five intelligence/analytics engines
internally, so docs 019/020/021 and price momentum are verified from the
artifacts that run just published rather than re-fetching every source
twice.

Usage:
    python src/engines/registry.py          (registry audit only)
    oil system run                          (full system)
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines import base as _base
from src.engines.base import Engine

# doc → engine mapping. mode "run" executes the engine; "verify" confirms
# the artifact a prior step published (used where re-running would just
# re-fetch the same sources within the same system run).
REGISTRY: list[dict[str, Any]] = [
    {"doc": "001", "title": "Project Vision", "mode": "run",
     "module": "src.monitoring.invariants",
     "cls": "InvariantComplianceEngine", "artifact": "invariant_compliance",
     "headline": lambda d: f"{d['invariants_passed']} invariants hold"},
    {"doc": "002", "title": "Monitoring", "mode": "run",
     "module": "src.monitoring.engine", "cls": "MonitoringEngine",
     "artifact": "monitoring_report",
     "headline": lambda d: f"health {d['health']}, "
                           f"{len(d['alerts'])} alerts"},
    {"doc": "003", "title": "Repository Architecture", "mode": "run",
     "module": "src.monitoring.repo_architecture",
     "cls": "RepoArchitectureEngine", "artifact": "repo_architecture_report",
     "headline": lambda d: f"docs {d['architecture_documents']}, "
                           f"dirs {d['governed_directories']}"},
    {"doc": "004", "title": "Data Layer", "mode": "run",
     "module": "src.data.quality", "cls": "DataQualityEngine",
     "artifact": "data_quality_report",
     "headline": lambda d: f"quality {d['overall_quality']}/100 across "
                           f"{len(d['datasets'])} datasets"},
    {"doc": "005", "title": "Engine Architecture", "mode": "run",
     "module": "src.engines.registry", "cls": "EngineRegistryEngine",
     "artifact": "engine_registry",
     "headline": lambda d: f"{d['engines_registered']} engines "
                           f"lifecycle-compliant"},
    {"doc": "006", "title": "Scoring System", "mode": "run",
     "module": "src.engines.scoring.composite_signal",
     "cls": "CompositeSignalEngine", "artifact": "composite_signal",
     "headline": lambda d: f"composite {d['composite_score']:+.2f} "
                           f"({d['label']})"},
    {"doc": "007", "title": "Risk System", "mode": "run",
     "module": "src.engines.risk.engine", "cls": "RiskEngine",
     "artifact": "risk_assessment",
     "headline": lambda d: f"risk {d['overall_risk_score']}/100 "
                           f"({d['overall_risk_level']})"},
    {"doc": "008", "title": "Confidence System", "mode": "run",
     "module": "src.engines.confidence.engine", "cls": "ConfidenceEngine",
     "artifact": "signal_confidence",
     "headline": lambda d: f"confidence {d['confidence_score']}/100 "
                           f"({d['confidence_tier']})"},
    {"doc": "009", "title": "Strategy System", "mode": "run",
     "module": "src.engines.strategy.engine", "cls": "StrategyEngine",
     "artifact": "strategy_recommendation",
     "headline": lambda d: f"{d['stance']['direction']} @ "
                           f"{d['stance']['suggested_size_0_1']:.0%} size"},
    {"doc": "010", "title": "Backtesting", "mode": "run",
     "module": "src.engines.backtesting.engine", "cls": "BacktestingEngine",
     "artifact": "backtest_report",
     "headline": lambda d: f"{d['sample']['weeks']}w sample; inventory "
                           f"{d['signals'][0]['verdict_1w']}"},
    {"doc": "011", "title": "Simulation", "mode": "run",
     "module": "src.engines.simulation.engine", "cls": "SimulationEngine",
     "artifact": "simulation_results",
     "headline": lambda d: f"{len(d['scenarios'])} scenarios × "
                           f"{d['paths_per_scenario']} paths"},
    {"doc": "012", "title": "Observability", "mode": "run",
     "module": "src.observability.engine", "cls": "ObservabilityEngine",
     "artifact": "observability_report",
     "headline": lambda d: f"{d['engines_tracked']} engines, "
                           f"{d['total_runs']} runs logged"},
    {"doc": "013", "title": "Automation", "mode": "run",
     "module": "src.engines.automation.status",
     "cls": "AutomationStatusEngine", "artifact": "automation_status",
     "headline": lambda d: f"{d['workflows_defined']} workflows defined"},
    {"doc": "014", "title": "Security", "mode": "run",
     "module": "src.security.engine", "cls": "SecurityEngine",
     "artifact": "security_audit",
     "headline": lambda d: f"audit {d['status']}, "
                           f"{d['tracked_files_scanned']} files"},
    {"doc": "015", "title": "Deployment", "mode": "run",
     "module": "src.monitoring.deployment_readiness",
     "cls": "DeploymentReadinessEngine", "artifact": "deployment_readiness",
     "headline": lambda d: "ready" if d["ready"] else "NOT ready"},
    {"doc": "016", "title": "CLI", "mode": "run",
     "module": "src.cli.status", "cls": "CLIStatusEngine",
     "artifact": "cli_status",
     "headline": lambda d: f"{d['commands']} commands, "
                           f"{d['json_capable_commands']} json-capable"},
    {"doc": "017", "title": "Forecasting System", "mode": "run",
     "module": "src.engines.forecast.engine", "cls": "ForecastEngine",
     "artifact": "price_forecast",
     "headline": lambda d: f"4w point ${d['point_forecast']} "
                           f"(95%: {d['interval_95'][0]}–"
                           f"{d['interval_95'][1]})"},
    {"doc": "018", "title": "Future Ideas", "mode": "run",
     "module": "src.engines.strategy.roadmap", "cls": "RoadmapEngine",
     "artifact": "roadmap_status",
     "headline": lambda d: f"{d['items_total']} roadmap items "
                           f"({d['by_status']})"},
    {"doc": "019", "title": "Geopolitical Intelligence", "mode": "verify",
     "module": "src.intelligence.geopolitical.engine",
     "cls": "GeopoliticalIntelligenceEngine", "artifact": "geopolitical_risk",
     "headline": lambda d: f"risk {d['risk_score']}/100 "
                           f"({d['risk_level']})"},
    {"doc": "020", "title": "Supply Chain Intelligence", "mode": "verify",
     "module": "src.intelligence.supply_chain.engine",
     "cls": "SupplyChainIntelligenceEngine",
     "artifact": "supply_chain_stress",
     "headline": lambda d: f"stress {d['stress_score']}/100 "
                           f"({d['stress_level']})"},
    {"doc": "021", "title": "Market Sentiment Intelligence",
     "mode": "verify", "module": "src.intelligence.market_sentiment.engine",
     "cls": "MarketSentimentEngine", "artifact": "market_sentiment",
     "headline": lambda d: f"sentiment {d['sentiment_signal']:+.2f} "
                           f"({d['sentiment_label']})"},
    {"doc": "022", "title": "API System", "mode": "run",
     "module": "src.api.status", "cls": "APIStatusEngine",
     "artifact": "api_status",
     "headline": lambda d: f"{d['resources_served']} resources under "
                           f"/api/v1"},
]

# execution order: dependencies before consumers; board renders in doc order
EXECUTION_ORDER = ["004", "006", "019", "020", "021", "007", "008", "017",
                   "010", "011", "009", "014", "002", "012", "013", "015",
                   "016", "022", "003", "005", "018", "001"]


class EngineRegistryEngine(Engine):
    """Doc 005: verifies every registered engine implements the lifecycle."""

    name = "engine_registry"
    version = "1.0"
    output_name = "engine_registry"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        rows = []
        for entry in REGISTRY:
            if entry["cls"] == self.__class__.__name__:
                cls: type = self.__class__
            else:
                module = importlib.import_module(entry["module"])
                cls = getattr(module, entry["cls"])
            compliant = (isinstance(cls, type) and issubclass(cls, Engine)
                         and callable(getattr(cls, "execute", None)))
            if not compliant:
                warnings.append(f"{entry['cls']} is not lifecycle-compliant")
            rows.append({
                "doc": entry["doc"], "title": entry["title"],
                "engine": entry["cls"], "module": entry["module"],
                "artifact": entry["artifact"],
                "version": getattr(cls, "version", "?"),
                "lifecycle_compliant": compliant,
            })
        data = {
            "as_of": date.today().isoformat(),
            "engines_registered": len(rows),
            "docs_covered": f"{len({r['doc'] for r in rows})}/22",
            "engines": rows,
        }
        return data, ["static inspection of every registered engine class"]


def run_full_system(pull_data: bool = False) -> dict[str, Any]:
    """Run every doc's engine in dependency order; return the board."""
    if pull_data:
        from src.data import eia_client
        eia_client.main()
        import pandas as pd

        from src.engines.scoring.inventory_surprise import (
            INPUT_PATH, OUTPUT_PATH, compute_surprise,
        )
        compute_surprise(pd.read_csv(INPUT_PATH)).to_csv(
            OUTPUT_PATH, index=False)
        print()

    by_doc = {e["doc"]: e for e in REGISTRY}
    board: list[dict[str, Any]] = []

    for doc in EXECUTION_ORDER:
        entry = by_doc[doc]
        try:
            if entry["mode"] == "run":
                module = importlib.import_module(entry["module"])
                result = getattr(module, entry["cls"])().run()
                status = result.status
                data = result.data
                warns = len(result.warnings)
            else:  # verify: composite already ran it this cycle
                art = json.loads(
                    (_base.DATA_DIR / f"{entry['artifact']}.json")
                    .read_text())
                status = art["status"]
                data = art["data"]
                warns = len(art.get("warnings", []))
            headline = entry["headline"](data) if status == "success" \
                else "—"
        except Exception as exc:
            status, headline, warns = "failed", f"{type(exc).__name__}: {exc}", 0
        board.append({"doc": doc, "title": entry["title"],
                      "engine": entry["cls"], "mode": entry["mode"],
                      "status": status, "warnings": warns,
                      "headline": headline})

    board.sort(key=lambda r: r["doc"])
    ok = sum(1 for r in board if r["status"] == "success")
    summary = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "docs_green": f"{ok}/{len(board)}",
        "board": board,
    }
    _base.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (_base.DATA_DIR / "system_run.json").write_text(
        json.dumps(summary, indent=2))
    return summary


def print_board(summary: dict[str, Any]) -> None:
    print("\nOIL INTELLIGENCE PLATFORM — FULL SYSTEM RUN")
    print("=" * 74)
    print(f"  {summary['docs_green']} docs green @ {summary['as_of']}\n")
    for r in summary["board"]:
        mark = "✓" if r["status"] == "success" else "✗"
        warn = f" ({r['warnings']}⚠)" if r["warnings"] else ""
        print(f"  {mark} {r['doc']} {r['title']:<30} {r['headline']}{warn}")
    print("\n  Published → data/system_run.json")


def main() -> None:
    result = EngineRegistryEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"Engine Registry (doc 005): {d['engines_registered']} engines, "
          f"docs covered {d['docs_covered']}")
    print("  Published → data/engine_registry.json")


if __name__ == "__main__":
    main()
