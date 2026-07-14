"""
Monitoring Engine — platform health, data freshness, and engine status.

Governed by docs/002_Monitoring.md. Checks every published artifact
against its expected update cadence, checks the raw data files, and scans
the engine run log for recent failures. Emits alerts on the doc 002
four-tier scale (critical / high / medium / low) and an overall health
verdict.

Usage:
    python src/monitoring/engine.py

Publishes:
    data/monitoring_report.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, RUN_LOG, Engine

# artifact → max acceptable age in days before a MEDIUM staleness alert
ARTIFACT_MAX_AGE = {
    "composite_signal": 2,
    "geopolitical_risk": 2,
    "risk_assessment": 2,
    "signal_confidence": 2,
    "macro_conditions": 2,
    "price_momentum": 2,
    "supply_chain_stress": 8,
    "market_sentiment": 8,
    "price_forecast": 8,
    "simulation_results": 8,
    "strategy_recommendation": 8,
    "data_quality_report": 8,
}

CSV_MAX_AGE = {
    "crude_inventories.csv": 9,     # weekly EIA release cadence
    "wti_prices.csv": 5,
    "inventory_surprise.csv": 9,
}

TIER_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class MonitoringEngine(Engine):
    name = "monitoring"
    version = "1.0"
    output_name = "monitoring_report"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        now = datetime.now(timezone.utc)
        alerts: list[dict[str, str]] = []
        checks = 0

        # -- artifacts: presence, last-run status, freshness --------------------
        for name, max_age in ARTIFACT_MAX_AGE.items():
            checks += 1
            path = DATA_DIR / f"{name}.json"
            if not path.exists():
                alerts.append({"tier": "high", "target": name,
                               "message": "artifact missing — engine has "
                                          "never published"})
                continue
            art = json.loads(path.read_text())
            if art.get("status") != "success":
                alerts.append({"tier": "critical", "target": name,
                               "message": f"last run FAILED: {art.get('error')}"})
                continue
            finished = datetime.fromisoformat(art["finished_utc"])
            age_days = (now - finished).total_seconds() / 86400
            if age_days > max_age:
                alerts.append({"tier": "medium", "target": name,
                               "message": f"stale: {age_days:.1f}d old "
                                          f"(max {max_age}d)"})
            if art.get("warnings"):
                alerts.append({"tier": "low", "target": name,
                               "message": f"{len(art['warnings'])} warning(s) "
                                          f"on last run"})

        # -- raw data files ------------------------------------------------------
        for fname, max_age in CSV_MAX_AGE.items():
            checks += 1
            path = DATA_DIR / fname
            if not path.exists():
                alerts.append({"tier": "high", "target": fname,
                               "message": "data file missing — run oil data pull"})
                continue
            age_days = (now.timestamp() - path.stat().st_mtime) / 86400
            if age_days > max_age:
                alerts.append({"tier": "medium", "target": fname,
                               "message": f"not refreshed for {age_days:.1f}d "
                                          f"(max {max_age}d)"})

        # -- anomaly detection (doc 002): is the newest data point sane? ---------
        import pandas as pd
        ANOMALY_Z = 4.0
        for fname, col, date_col in (
            ("crude_inventories.csv", "weekly_change", "period"),
            ("wti_prices.csv", "close", "date"),
        ):
            path = DATA_DIR / fname
            if not path.exists():
                continue
            checks += 1
            df = pd.read_csv(path)
            series = df[col].dropna()
            if len(series) < 60:
                continue
            if fname == "wti_prices.csv":
                series = series.pct_change().dropna()   # returns, not levels
            hist, latest = series.iloc[:-1], series.iloc[-1]
            z = (latest - hist.mean()) / hist.std()
            if abs(z) > ANOMALY_Z:
                alerts.append({
                    "tier": "high", "target": fname,
                    "message": f"latest {col} is a {z:+.1f}σ outlier vs "
                               f"history — verify the source before "
                               f"engines consume it",
                })

        # -- engine run log: failures in the last 100 runs ------------------------
        recent_failures = []
        if RUN_LOG.exists():
            checks += 1
            lines = RUN_LOG.read_text().strip().splitlines()[-100:]
            for line in lines:
                run = json.loads(line)
                if run["status"] == "failed":
                    recent_failures.append(
                        {"engine": run["engine"], "run_id": run["run_id"],
                         "error": (run.get("error") or "")[:120],
                         "at": run["finished_utc"]})
            # only alert on engines whose MOST RECENT run failed
            last_by_engine: dict[str, dict] = {}
            for line in lines:
                run = json.loads(line)
                last_by_engine[run["engine"]] = run
            for eng, run in last_by_engine.items():
                if run["status"] == "failed" and not eng.startswith("test_"):
                    alerts.append({"tier": "critical", "target": eng,
                                   "message": f"most recent run failed: "
                                              f"{(run.get('error') or '')[:120]}"})

        alerts.sort(key=lambda a: TIER_ORDER[a["tier"]])
        worst = alerts[0]["tier"] if alerts else None
        health = ("critical" if worst == "critical" else
                  "degraded" if worst in ("high", "medium") else
                  "healthy")

        data = {
            "health": health,
            "as_of": now.isoformat(timespec="seconds"),
            "checks_run": checks,
            "alerts": alerts,
            "alert_counts": {
                tier: sum(1 for a in alerts if a["tier"] == tier)
                for tier in TIER_ORDER
            },
            "recent_run_failures": recent_failures[-10:],
        }
        evidence = [f"{checks} checks across data/, artifacts, and "
                    f"logs/engine_runs.jsonl"]
        return data, evidence


def main() -> None:
    result = MonitoringEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    icon = {"healthy": "✓", "degraded": "△", "critical": "✗"}[d["health"]]
    print("Platform Monitoring")
    print("=" * 60)
    print(f"  {icon} HEALTH: {d['health'].upper()}  "
          f"({d['checks_run']} checks, {len(d['alerts'])} alerts)\n")
    for a in d["alerts"]:
        print(f"  [{a['tier'].upper():<8}] {a['target']}: {a['message']}")
    if not d["alerts"]:
        print("  No alerts — all systems within thresholds.")
    print(f"\n  Published → data/monitoring_report.json")


if __name__ == "__main__":
    main()
