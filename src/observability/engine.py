"""
Observability Engine — doc 012's telemetry as a published artifact.

Wraps the run-log aggregation (report.py) in the standard engine
lifecycle so telemetry itself is versioned, evidenced, and queryable via
the API like every other output.

Publishes: data/observability_report.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines import base as _base
from src.engines.base import Engine
from src.observability.report import build_report


class ObservabilityEngine(Engine):
    name = "observability"
    version = "1.0"
    output_name = "observability_report"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        stats = build_report()
        failing = [e for e, s in stats.items()
                   if s["last_status"] == "failed"
                   and not e.startswith("test_")]
        for e in failing:
            warnings.append(f"{e}: most recent run failed")
        data = {
            "as_of": date.today().isoformat(),
            "engines_tracked": len(stats),
            "total_runs": sum(s["runs"] for s in stats.values()),
            "engines_failing": failing,
            "per_engine": stats,
        }
        return data, [f"{_base.RUN_LOG} structured run log"]


def main() -> None:
    result = ObservabilityEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"Observability (doc 012): {d['engines_tracked']} engines, "
          f"{d['total_runs']} runs logged, "
          f"{len(d['engines_failing'])} currently failing")
    print("  Published → data/observability_report.json "
          "(detail: oil obs report)")


if __name__ == "__main__":
    main()
