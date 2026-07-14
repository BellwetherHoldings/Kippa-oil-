"""
API Status Engine — doc 022's surface as a published artifact.

Verifies the API app loads, counts its versioned routes, and confirms
auth and rate limiting are configured — without needing the server up.

Publishes: data/api_status.json
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine


class APIStatusEngine(Engine):
    name = "api_status"
    version = "1.0"
    output_name = "api_status"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        from src.api.app import (
            ADMIN_RESOURCES, RATE_LIMIT_PER_MIN, RESOURCES, app, _keys,
        )
        routes = [r.path for r in app.routes
                  if getattr(r, "path", "").startswith("/api/v1")]
        keys = _keys()
        if not keys:
            warnings.append("PLATFORM_API_KEYS not configured — API would "
                            "refuse all data requests (503).")
        data = {
            "as_of": date.today().isoformat(),
            "version_prefix": "/api/v1",
            "routes": sorted(routes),
            "resources_served": len(RESOURCES),
            "admin_only_resources": sorted(ADMIN_RESOURCES),
            "auth": "X-API-Key with roles (readonly/admin)",
            "keys_configured": len(keys),
            "rate_limit_per_min": RATE_LIMIT_PER_MIN,
            "serve": "oil api serve → 127.0.0.1:8000 "
                     "(docs at /api/v1/docs)",
        }
        return data, ["src/api/app.py route table and auth config"]


def main() -> None:
    result = APIStatusEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"API (doc 022): {d['resources_served']} resources under "
          f"/api/v1, {d['keys_configured']} key(s), "
          f"{d['rate_limit_per_min']}/min rate limit")
    print("  Published → data/api_status.json")


if __name__ == "__main__":
    main()
