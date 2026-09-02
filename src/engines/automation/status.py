"""
Automation Status Engine — doc 013's audit trail as a published artifact.

Reads config/workflows.json and logs/automation_audit.jsonl to report
which workflows exist, when each last ran, and how it went.

Publishes: data/automation_status.json
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

from src.engines.automation.runner import AUDIT_LOG, WORKFLOWS_PATH
from src.engines.base import Engine


class AutomationStatusEngine(Engine):
    name = "automation_status"
    version = "1.0"
    output_name = "automation_status"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        config = json.loads(WORKFLOWS_PATH.read_text())["workflows"]

        last_runs: dict[str, dict] = {}
        if AUDIT_LOG.exists():
            for line in AUDIT_LOG.read_text().strip().splitlines():
                rec = json.loads(line)
                if rec.get("event") == "summary":
                    last_runs[rec["workflow"]] = rec

        workflows = []
        for name, wf in config.items():
            last = last_runs.get(name)
            if last is None:
                warnings.append(f"workflow '{name}' has never run")
            workflows.append({
                "workflow": name,
                "steps_configured": len(wf["steps"]),
                "on_failure": wf["on_failure"],
                "last_run": None if last is None else {
                    "finished_utc": last["finished_utc"],
                    "steps_failed": last["steps_failed"],
                    "steps_total": last["steps_total"],
                },
            })

        data = {
            "as_of": date.today().isoformat(),
            "workflows_defined": len(config),
            "workflows": workflows,
            "scheduling": "external by design — cron/Routine calls "
                          "`oil auto run <workflow>`",
        }
        return data, [f"{WORKFLOWS_PATH.name} + logs/automation_audit.jsonl"]


def main() -> None:
    result = AutomationStatusEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"Automation (doc 013): {d['workflows_defined']} workflows defined")
    for w in d["workflows"]:
        last = w["last_run"]
        state = (f"last run {last['finished_utc']}: "
                 f"{last['steps_total'] - last['steps_failed']}"
                 f"/{last['steps_total']} steps ok") if last else "never run"
        print(f"  {w['workflow']} ({w['steps_configured']} steps) — {state}")
    print("  Published → data/automation_status.json")


if __name__ == "__main__":
    main()
