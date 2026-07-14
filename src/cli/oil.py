"""
Oil Intelligence Platform CLI — oil <module> <command>

Governed by docs/016_CLI.md. v1 commands:

    oil data pull        Refresh EIA inventories + WTI prices
    oil signal run       Full pipeline: pull data → all engines → composite
    oil signal show      Composite from existing data (no re-pull)
    oil geo status       Geopolitical risk assessment

Usage:
    python src/cli/oil.py <module> <command>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _data_pull() -> None:
    from src.data import eia_client
    eia_client.main()


def _signal_show() -> None:
    from src.engines.scoring import inventory_surprise
    inventory_surprise.main()
    print()
    from src.engines.scoring import composite_signal
    composite_signal.main()


def _signal_run() -> None:
    _data_pull()
    print()
    _signal_show()


def _geo_status() -> None:
    from src.intelligence.geopolitical import engine
    engine.main()


COMMANDS = {
    ("data", "pull"): _data_pull,
    ("signal", "run"): _signal_run,
    ("signal", "show"): _signal_show,
    ("geo", "status"): _geo_status,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oil",
        description="Oil Intelligence Platform CLI (docs/016_CLI.md)",
    )
    parser.add_argument("module", help="data | signal | geo")
    parser.add_argument("command", help="pull | run | show | status")
    args = parser.parse_args(argv)

    handler = COMMANDS.get((args.module, args.command))
    if handler is None:
        valid = ", ".join(f"oil {m} {c}" for m, c in sorted(COMMANDS))
        parser.error(f"unknown command '{args.module} {args.command}'. "
                     f"Valid: {valid}")
    handler()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
