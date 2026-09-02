"""
Observability Report — engine telemetry from the structured run log.

Governed by docs/012_Observability_System.md. Every Engine.run() appends
a structured record to logs/engine_runs.jsonl (metrics + logs pillars);
this module aggregates that telemetry into per-engine operational stats:
run counts, failure rates, latency, and last-run status.

Usage:
    python src/observability/report.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines import base as _base


def build_report() -> dict:
    run_log = _base.RUN_LOG
    if not run_log.exists():
        raise FileNotFoundError(f"{run_log} not found — no engine has run yet.")

    stats: dict[str, dict] = defaultdict(
        lambda: {"runs": 0, "failures": 0, "total_ms": 0,
                 "max_ms": 0, "last_status": None, "last_finished": None}
    )
    for line in run_log.read_text().strip().splitlines():
        run = json.loads(line)
        s = stats[run["engine"]]
        s["runs"] += 1
        s["failures"] += run["status"] == "failed"
        s["total_ms"] += run["duration_ms"]
        s["max_ms"] = max(s["max_ms"], run["duration_ms"])
        s["last_status"] = run["status"]
        s["last_finished"] = run["finished_utc"]

    return {
        engine: {
            "runs": s["runs"],
            "failure_rate": round(s["failures"] / s["runs"], 3),
            "avg_ms": int(s["total_ms"] / s["runs"]),
            "max_ms": s["max_ms"],
            "last_status": s["last_status"],
            "last_finished": s["last_finished"],
        }
        for engine, s in sorted(stats.items())
    }


def main() -> None:
    report = build_report()
    print("Engine Telemetry — logs/engine_runs.jsonl")
    print("=" * 72)
    print(f"  {'engine':<28} {'runs':>5} {'fail%':>6} {'avg ms':>8} "
          f"{'max ms':>8}  last")
    print(f"  {'-'*70}")
    for engine, s in report.items():
        print(f"  {engine:<28} {s['runs']:>5} {s['failure_rate']:>6.0%} "
              f"{s['avg_ms']:>8} {s['max_ms']:>8}  {s['last_status']} "
              f"@ {s['last_finished']}")


if __name__ == "__main__":
    main()
