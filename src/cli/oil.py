"""
Oil Intelligence Platform CLI — oil <module> <command> [arg]

Governed by docs/016_CLI.md. Commands:

    oil data pull          Refresh EIA inventories + WTI prices
    oil data quality       Validation + lineage report for every dataset
    oil signal run         Full pipeline: data → engines → composite
                           → risk → confidence → forecast → sim → strategy
    oil signal show        Composite from existing data (no re-pull)
    oil geo status         Geopolitical risk assessment
    oil supply status      Supply chain stress assessment
    oil sentiment show     CFTC institutional positioning
    oil macro show         FRED macroeconomic conditions
    oil risk status        Platform risk assessment
    oil confidence show    Confidence grade of the latest composite
    oil backtest run       Historical validation of the signals
    oil forecast show      4-week WTI fundamentals forecast
    oil sim run            Monte Carlo scenarios (Hormuz overlays)
    oil strategy show      Positioning recommendation from the full stack
    oil monitor status     Platform health, freshness, alerts
    oil obs report         Engine telemetry from the run log
    oil auto run <name>    Run a workflow from config/workflows.json
    oil security audit     Secrets hygiene scan of tracked files
    oil deploy check       Release-readiness gate (audit + tests + env)
    oil api serve          Start the API on 127.0.0.1:8000

Usage:
    python src/cli/oil.py <module> <command> [arg]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _data_pull(arg=None):
    from src.data import eia_client
    eia_client.main()


def _data_quality(arg=None):
    from src.data import quality
    quality.main()


def _signal_show(arg=None):
    from src.engines.scoring import inventory_surprise
    inventory_surprise.main()
    print()
    from src.engines.scoring import composite_signal
    composite_signal.main()


def _signal_run(arg=None):
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
    print()
    _monitor_status()


def _geo_status(arg=None):
    from src.intelligence.geopolitical import engine
    engine.main()


def _supply_status(arg=None):
    from src.intelligence.supply_chain import engine
    engine.main()


def _sentiment_show(arg=None):
    from src.intelligence.market_sentiment import engine
    engine.main()


def _macro_show(arg=None):
    from src.intelligence.macroeconomic import engine
    engine.main()


def _risk_status(arg=None):
    from src.engines.risk import engine
    engine.main()


def _confidence_show(arg=None):
    from src.engines.confidence import engine
    engine.main()


def _backtest_run(arg=None):
    from src.engines.backtesting import engine
    engine.main()


def _forecast_show(arg=None):
    from src.engines.forecast import engine
    engine.main()


def _sim_run(arg=None):
    from src.engines.simulation import engine
    engine.main()


def _strategy_show(arg=None):
    from src.engines.strategy import engine
    engine.main()


def _monitor_status(arg=None):
    from src.monitoring import engine
    engine.main()


def _obs_report(arg=None):
    from src.observability import report
    report.main()


def _auto_run(arg=None):
    from src.engines.automation import runner
    runner.main([arg] if arg else [])


def _security_audit(arg=None):
    from src.security import audit
    audit.main()


def _deploy_check(arg=None):
    import os
    import subprocess

    from dotenv import load_dotenv

    from src.security.audit import run_audit

    load_dotenv(_REPO_ROOT / ".env")
    print("Deploy Check — release readiness")
    print("=" * 60)
    failures = 0

    for var in ("EIA_API_KEY", "FRED_API_KEY", "PLATFORM_API_KEYS"):
        ok = bool(os.getenv(var))
        print(f"  {'✓' if ok else '✗'} env: {var} "
              f"{'present' if ok else 'MISSING'}")
        failures += not ok

    audit_report = run_audit()
    ok = audit_report["status"] == "pass"
    print(f"  {'✓' if ok else '✗'} security audit: {audit_report['status']} "
          f"({audit_report['tracked_files_scanned']} files)")
    failures += not ok

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "tests/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "?"
    print(f"  {'✓' if proc.returncode == 0 else '✗'} tests: {last}")
    failures += proc.returncode != 0

    data_dir = _REPO_ROOT / "data"
    writable = os.access(data_dir, os.W_OK) if data_dir.exists() else False
    print(f"  {'✓' if writable else '✗'} data/ writable")
    failures += not writable

    print(f"\n  {'✓ READY' if failures == 0 else f'✗ NOT READY — {failures} gate(s) failed'}")
    if failures:
        raise SystemExit(1)


def _api_serve(arg=None):
    import uvicorn
    uvicorn.run("src.api.app:app", host="127.0.0.1", port=8000)


COMMANDS = {
    ("data", "pull"): _data_pull,
    ("data", "quality"): _data_quality,
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
    ("monitor", "status"): _monitor_status,
    ("obs", "report"): _obs_report,
    ("auto", "run"): _auto_run,
    ("security", "audit"): _security_audit,
    ("deploy", "check"): _deploy_check,
    ("api", "serve"): _api_serve,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oil",
        description="Oil Intelligence Platform CLI (docs/016_CLI.md)",
    )
    parser.add_argument("module")
    parser.add_argument("command")
    parser.add_argument("arg", nargs="?", default=None)
    args = parser.parse_args(argv)

    handler = COMMANDS.get((args.module, args.command))
    if handler is None:
        valid = "\n  ".join(f"oil {m} {c}" for m, c in sorted(COMMANDS))
        parser.error(f"unknown command '{args.module} {args.command}'. "
                     f"Valid:\n  {valid}")
    handler(args.arg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
