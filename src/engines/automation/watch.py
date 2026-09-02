"""
Watch Mode — continuous operation: full cycle every N minutes → Discord.

Governed by docs/013_Automation.md. Each cycle: pull fresh data, rerun
the signal stack via the workflow runner (bounded retries, audit-logged),
and post the market read to Discord. Cycles are logged to
logs/watch.jsonl; a failed cycle posts a warning instead of going silent
(automation without monitoring is an anti-pattern).

Usage:
    python src/cli/oil.py watch run [minutes]    (default 30)

Runs until the process is stopped. For 24/7 operation host it on an
always-on machine; in an ephemeral environment it runs for the life of
the container.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.automation.runner import run_workflow
from src.engines.base import LOG_DIR

WATCH_LOG = LOG_DIR / "watch.jsonl"
DEFAULT_INTERVAL_MIN = 30


def _log(record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with WATCH_LOG.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def run_cycle(cycle: int) -> None:
    from src.engines.automation.notify import send_discord_update

    started = datetime.now(timezone.utc)

    # The intraday radar is meaningless when CL futures are dark (weekends,
    # nightly maintenance halt) — prices are frozen at the last close. Skip
    # the cycle: no Discord post, no wasted compute. A marker is still
    # logged so the keepalive sees a fresh heartbeat and leaves us alone.
    from src.data.market_hours import is_market_open, status
    if not is_market_open(started):
        st = status(started)
        _log({"cycle": cycle, "at": started.isoformat(timespec="seconds"),
              "market": "closed", "next_open": st.get("next_open")})
        print(f"[{started:%H:%M:%S}] cycle {cycle}: market closed — "
              f"skipping post (reopens {st.get('next_open')}, "
              f"~{st.get('hours_to_open')}h)")
        return

    summary = run_workflow("watch_cycle")

    # day-trade mode: refresh the intraday radar each cycle so the
    # Discord post carries a current 30m read
    import json as _json
    mode = _REPO_ROOT / "config" / "daytrade.json"
    if mode.exists() and _json.loads(mode.read_text()).get("enabled"):
        from src.engines.analytics.intraday import IntradayRadarEngine
        radar = IntradayRadarEngine().run()
        if not radar.ok:
            print(f"intraday radar failed: {radar.error}", file=sys.stderr)
    failed = summary["steps_failed"]

    from src.engines.base import load_artifact
    comp = load_artifact("composite_signal", require_success=True)
    label = comp["data"]["label"] if comp else "unknown"
    score = comp["data"]["composite_score"] if comp else None

    # full component-vector capture (doc 010): the composite scalar alone
    # cannot answer later which input carried a call. Never let a capture
    # failure break the cycle — the signal log is observation, not control.
    try:
        from src.engines.backtesting.signal_log import capture
        capture(trigger="watch_cycle")
    except Exception as exc:                              # noqa: BLE001
        print(f"signal log capture failed: {exc}", file=sys.stderr)

    # Re-simulate the trade ledger off the row we just captured. Deliberately
    # AFTER capture so it reflects this cycle, not the previous one, and
    # deliberately fail-open for the same reason as the capture above: this is
    # research observation, never control (doc 010 invariant 6).
    try:
        from src.engines.backtesting.sim_ledger import SimLedgerEngine
        SimLedgerEngine().run({})
    except Exception as exc:                              # noqa: BLE001
        print(f"sim ledger refresh failed: {exc}", file=sys.stderr)

    if failed == 0:
        send_discord_update()
    else:
        bad = [s["step"] for s in summary["steps"] if s["status"] == "failed"]
        send_discord_update(
            content=f"⚠️ Cycle completed with {failed} failed step(s): "
                    f"{', '.join(bad)} — figures below may be partially stale.")

    _log({"cycle": cycle, "at": started.isoformat(timespec="seconds"),
          "steps_failed": failed, "composite": score, "label": label})
    print(f"[{started:%H:%M:%S}] cycle {cycle}: composite "
          f"{score:+.2f} ({label}), {failed} failed steps" if score is not None
          else f"[{started:%H:%M:%S}] cycle {cycle}: composite unavailable")


def watch(interval_min: int = DEFAULT_INTERVAL_MIN) -> None:
    print(f"Kippa watch mode — full cycle every {interval_min} min, "
          f"posting to Discord. Ctrl-C to stop.")
    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle(cycle)
        except Exception as exc:                      # never die silently
            _log({"cycle": cycle,
                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "error": f"{type(exc).__name__}: {exc}"})
            print(f"cycle {cycle} failed: {exc}", file=sys.stderr)
        time.sleep(interval_min * 60)


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    interval = int(argv[0]) if argv else DEFAULT_INTERVAL_MIN
    if not 5 <= interval <= 720:
        raise SystemExit("Interval must be between 5 and 720 minutes.")
    watch(interval)


if __name__ == "__main__":
    main()
