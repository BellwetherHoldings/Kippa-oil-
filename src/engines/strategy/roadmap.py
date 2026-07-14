"""
Roadmap Engine — doc 018's future ideas as a tracked, queryable registry.

Reads config/roadmap.json (version-controlled) and publishes status
counts plus the actionable queue: what is blocked and by what, and what
is planned next.

Publishes: data/roadmap_status.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine

ROADMAP_PATH = _REPO_ROOT / "config" / "roadmap.json"
VALID_STATUSES = {"idea", "planned", "blocked", "in_progress", "done"}


class RoadmapEngine(Engine):
    name = "roadmap"
    version = "1.0"
    output_name = "roadmap_status"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        items = json.loads(ROADMAP_PATH.read_text())["items"]
        bad = [i["id"] for i in items if i["status"] not in VALID_STATUSES]
        if bad:
            raise ValueError(f"Invalid statuses on roadmap items: {bad}")
        for i in items:
            if i["status"] == "blocked" and not i.get("unblocked_by"):
                warnings.append(f"{i['id']} is blocked without naming "
                                f"what unblocks it")
        counts = Counter(i["status"] for i in items)
        data = {
            "as_of": date.today().isoformat(),
            "items_total": len(items),
            "by_status": dict(counts),
            "blocked": [{"id": i["id"], "title": i["title"],
                         "unblocked_by": i.get("unblocked_by")}
                        for i in items if i["status"] == "blocked"],
            "next_up": [{"id": i["id"], "title": i["title"]}
                        for i in items if i["status"] in
                        ("planned", "in_progress")],
            "items": items,
        }
        return data, ["config/roadmap.json (version-controlled registry)"]


def main() -> None:
    result = RoadmapEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"Roadmap (doc 018): {d['items_total']} items — {d['by_status']}")
    for b in d["blocked"]:
        print(f"  ⛔ {b['title']} (needs: {b['unblocked_by']})")
    for n in d["next_up"]:
        print(f"  → {n['title']}")
    print("  Published → data/roadmap_status.json")


if __name__ == "__main__":
    main()
