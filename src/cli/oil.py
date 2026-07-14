"""
Oil Intelligence Platform CLI — oil <module> <command>

Governed by docs/016_CLI.md. v1 commands:

    oil data pull        Refresh EIA inventories + WTI prices
    oil signal run       Full pipeline: pull data → all engines → composite
                         → risk → confidence
    oil signal show      Composite from existing data (no re-pull)
    oil geo status       Geopolitical risk assessment
    oil supply status    Supply chain stress assessment
    oil sentiment show   CFTC institutional positioning
    oil macro show       FRED macroeconomic conditions
    oil risk status      Platform risk assessment
    oil confidence show  Confidence grade of the latest composite
    oil backtest run     Historical validation of the signals
    oil forecast show    4-week WTI fundamentals forecast
    oil sim run          Monte Carlo scenarios (Hormuz overlays)
    oil strategy show    Positioning recommendation from the full stack

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
    print()
    _risk_status()
    print()
    _confidence_show()
    print()
    _forecast_show()
    print()
    _sim_run()
    print()
    _strategy_show()


def _geo_status() -> None:
    from src.intelligence.geopolitical import engine
    engine.main()


def _supply_status() -> None:
    from src.intelligence.supply_chain import engine
    engine.main()


def _sentiment_show() -> None:
    from src.intelligence.market_sentiment import engine
    engine.main()


def _macro_show() -> None:
    from src.intelligence.macroeconomic import engine
    engine.main()


def _risk_status() -> None:
    from src.engines.risk import engine
    engine.main()


def _confidence_show() -> None:
    from src.engines.confidence import engine
    engine.main()


def _backtest_run() -> None:
    from src.engines.backtesting import engine
    engine.main()


def _forecast_show() -> None:
    from src.engines.forecast import engine
    engine.main()


def _sim_run() -> None:
    from src.engines.simulation import engine
    engine.main()


def _strategy_show() -> None:
    from src.engines.strategy import engine
    engine.main()


COMMANDS = {
    ("data", "pull"): _data_pull,
    ("signal", "run"): _signal_run,
    ("signal", "show"): _signal_show,
    ("geo", "status"): _geo_status,
    ("supply", "status"): _supply_status,
    ("sentiment", "show"): _sentiment_show,
    ("macro", "show"): _macro_show,
    ("risk", "status"): _risk_status,
    ("confidence", "show"): _confidence_show,
    ("backtest", "run"): _backtest_run,
    ("forecast", "show"): _forecast_show,
    ("sim", "run"): _sim_run,
    ("strategy", "show"): _strategy_show,
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
