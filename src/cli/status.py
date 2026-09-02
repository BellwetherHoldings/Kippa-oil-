"""
CLI Status Engine — doc 016's surface as a published artifact.

Reports the command surface: modules, commands, and which support
machine-readable --json output.

Publishes: data/cli_status.json
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


class CLIStatusEngine(Engine):
    name = "cli_status"
    version = "1.0"
    output_name = "cli_status"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        from src.cli.oil import COMMANDS, JSON_ARTIFACTS
        modules = sorted({m for m, _ in COMMANDS})
        data = {
            "as_of": date.today().isoformat(),
            "pattern": "oil <module> <command> [arg] [--json]",
            "modules": modules,
            "commands": len(COMMANDS),
            "json_capable_commands": len(JSON_ARTIFACTS),
            "command_list": sorted(f"oil {m} {c}" for m, c in COMMANDS),
        }
        return data, ["src/cli/oil.py command registry"]


def main() -> None:
    result = CLIStatusEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print(f"CLI (doc 016): {d['commands']} commands across "
          f"{len(d['modules'])} modules, {d['json_capable_commands']} "
          f"with --json output")
    print("  Published → data/cli_status.json")


if __name__ == "__main__":
    main()
