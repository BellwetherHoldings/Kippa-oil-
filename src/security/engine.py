"""
Security Engine — doc 014's audit through the standard lifecycle.

Runs the secrets-hygiene audit (audit.py) as a platform engine so the
result is logged, evidenced, and consumable by the risk engine's
cybersecurity category and the API.

Publishes: data/security_audit.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine
from src.security.audit import run_audit


class SecurityEngine(Engine):
    name = "security"
    version = "1.0"
    output_name = "security_audit"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        report = run_audit()
        for f in report["findings"]:
            warnings.append(f"[{f['severity']}] {f['target']}: {f['finding']}")
        data = {"as_of": date.today().isoformat(), **report}
        return data, [f"git ls-files scan of "
                      f"{report['tracked_files_scanned']} tracked files"]


def main() -> None:
    result = SecurityEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"Security (doc 014): audit {d['status'].upper()} — "
          f"{d['tracked_files_scanned']} files, "
          f"{len(d['findings'])} findings")
    print("  Published → data/security_audit.json")
    if d["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
