"""
Deployment Readiness Engine — doc 015's release gate as an artifact.

Checks the non-test release gates: required environment keys, security
audit, environment config integrity, and a writable data tier. The test
gate stays in `oil deploy check` (running pytest inside an engine that
pytest itself tests would recurse).

Publishes: data/deployment_readiness.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine
from src.security.audit import run_audit

load_dotenv(_REPO_ROOT / ".env")

REQUIRED_ENV = ("EIA_API_KEY", "FRED_API_KEY", "PLATFORM_API_KEYS")


class DeploymentReadinessEngine(Engine):
    name = "deployment_readiness"
    version = "1.0"
    output_name = "deployment_readiness"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        gates: list[dict[str, Any]] = []

        for var in REQUIRED_ENV:
            gates.append({"gate": f"env:{var}",
                          "passed": bool(os.getenv(var))})

        audit = run_audit()
        gates.append({"gate": "security_audit",
                      "passed": audit["status"] == "pass"})

        env_cfg = _REPO_ROOT / "config" / "environments.json"
        cfg_ok = False
        if env_cfg.exists():
            cfg = json.loads(env_cfg.read_text()).get("environments", {})
            cfg_ok = {"development", "production"} <= set(cfg)
        gates.append({"gate": "config/environments.json", "passed": cfg_ok})

        gates.append({"gate": "data_tier_writable",
                      "passed": os.access(DATA_DIR, os.W_OK)})

        failed = [g["gate"] for g in gates if not g["passed"]]
        for g in failed:
            warnings.append(f"gate failed: {g}")

        data = {
            "ready": not failed,
            "as_of": date.today().isoformat(),
            "gates": gates,
            "test_gate": "run `oil deploy check` for the pytest gate "
                         "(kept out of this engine to avoid recursion)",
        }
        return data, [f"{len(gates)} release gates evaluated"]


def main() -> None:
    result = DeploymentReadinessEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"Deployment (doc 015): "
          f"{'✓ READY' if d['ready'] else '✗ NOT READY'} — "
          f"{sum(g['passed'] for g in d['gates'])}/{len(d['gates'])} gates")
    print("  Published → data/deployment_readiness.json")


if __name__ == "__main__":
    main()
