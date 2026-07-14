"""
Automation Runner — sequential workflows with retries and audit logging.

Governed by docs/013_Automation.md. Executes named workflows from
config/workflows.json. Each step retries on failure (bounded), failures
are isolated per the workflow's on_failure policy, and every step
execution is appended to logs/automation_audit.jsonl (invariant: every
automated action must be logged). Scheduling is external by design —
point cron (or a hosted Routine) at:

    python src/cli/oil.py auto run weekly_full_run

Usage:
    python src/engines/automation/runner.py <workflow>
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, LOG_DIR

WORKFLOWS_PATH = _REPO_ROOT / "config" / "workflows.json"
AUDIT_LOG = LOG_DIR / "automation_audit.jsonl"
RETRY_DELAY_S = 2


# -- step registry -----------------------------------------------------------

def _step_data_pull() -> None:
    from src.data import eia_client
    eia_client.main()


def _step_inventory_surprise() -> None:
    import pandas as pd
    from src.engines.scoring.inventory_surprise import (
        INPUT_PATH, OUTPUT_PATH, compute_surprise,
    )
    df = compute_surprise(pd.read_csv(INPUT_PATH))
    df.to_csv(OUTPUT_PATH, index=False)


def _engine_step(import_path: str, cls_name: str) -> Callable[[], None]:
    def step() -> None:
        module = __import__(import_path, fromlist=[cls_name])
        result = getattr(module, cls_name)().run()
        if not result.ok:
            raise RuntimeError(result.error)
    return step


STEPS: dict[str, Callable[[], None]] = {
    "data_pull": _step_data_pull,
    "data_quality": _engine_step("src.data.quality", "DataQualityEngine"),
    "inventory_surprise": _step_inventory_surprise,
    "composite_signal": _engine_step(
        "src.engines.scoring.composite_signal", "CompositeSignalEngine"),
    "risk_assessment": _engine_step(
        "src.engines.risk.engine", "RiskEngine"),
    "signal_confidence": _engine_step(
        "src.engines.confidence.engine", "ConfidenceEngine"),
    "backtest": _engine_step(
        "src.engines.backtesting.engine", "BacktestingEngine"),
    "forecast": _engine_step(
        "src.engines.forecast.engine", "ForecastEngine"),
    "simulation": _engine_step(
        "src.engines.simulation.engine", "SimulationEngine"),
    "strategy": _engine_step(
        "src.engines.strategy.engine", "StrategyEngine"),
    "monitoring": _engine_step(
        "src.monitoring.engine", "MonitoringEngine"),
}


def _audit(record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def run_workflow(name: str) -> dict[str, Any]:
    config = json.loads(WORKFLOWS_PATH.read_text())["workflows"]
    if name not in config:
        raise ValueError(
            f"Unknown workflow '{name}'. Available: {sorted(config)}")
    wf = config[name]
    unknown = [s for s in wf["steps"] if s not in STEPS]
    if unknown:
        raise ValueError(f"Workflow '{name}' has unknown steps: {unknown}")

    started = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []

    for step_name in wf["steps"]:
        attempts = 0
        status, error = "success", None
        t0 = time.perf_counter()
        while True:
            attempts += 1
            try:
                STEPS[step_name]()
                status, error = "success", None
                break
            except Exception as exc:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
                if attempts > wf["max_retries"]:
                    break
                time.sleep(RETRY_DELAY_S)

        record = {
            "workflow": name,
            "step": step_name,
            "status": status,
            "attempts": attempts,
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "error": error,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _audit(record)
        results.append(record)

        if status == "failed" and wf["on_failure"] == "abort":
            break

    summary = {
        "workflow": name,
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps_total": len(results),
        "steps_failed": sum(1 for r in results if r["status"] == "failed"),
        "steps": results,
    }
    _audit({"workflow": name, "event": "summary", **{
        k: v for k, v in summary.items() if k != "steps"}})
    (DATA_DIR / "last_workflow_run.json").write_text(
        json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        raise SystemExit("usage: runner.py <workflow>  "
                         "(see config/workflows.json)")
    summary = run_workflow(argv[0])
    print(f"\nWorkflow '{summary['workflow']}': "
          f"{summary['steps_total'] - summary['steps_failed']}/"
          f"{summary['steps_total']} steps succeeded")
    for r in summary["steps"]:
        mark = "✓" if r["status"] == "success" else "✗"
        extra = f" — {r['error']}" if r["error"] else ""
        print(f"  {mark} {r['step']} ({r['attempts']} attempt(s), "
              f"{r['duration_ms']}ms){extra}")


if __name__ == "__main__":
    main()
