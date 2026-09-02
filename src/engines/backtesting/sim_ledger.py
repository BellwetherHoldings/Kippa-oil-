"""
Simulated Trade Ledger — what the signal WOULD have done, marked to real prices.

Governed by docs/010_Backtesting.md. Doc 010 invariant 6 requires that
backtesting stay independent from production decision-making, so these
trades live in data/sim_trades.json and NEVER touch data/trades.json.
The live paper record stays clean; this file is a research artifact.

Mechanics, deliberately simple and stated so they can be argued with:

  - One position at a time, sized at the strategy engine's own size_pct.
  - Entry when the composite clears the |gate| and no position is open.
  - Exit when the composite crosses back through zero, flips sign, or the
    holding-period cap is hit — the same invalidation logic the live
    strategy publishes, applied mechanically.
  - Fills at the recorded WTI mark, plus a configurable slippage in
    dollars per barrel. Zero-slippage backtests flatter themselves; the
    default is deliberately non-zero.

The ledger is rebuilt from data/signal_log.jsonl on every run, so it is
reproducible (invariant 1) and never accumulates drift from partial
updates.

Usage:
    python src/cli/oil.py backtest sim [slippage_per_bbl]

Publishes:
    data/sim_trades.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine
from src.engines.backtesting.signal_log import load_rows

SIM_TRADES = DATA_DIR / "sim_trades.json"

GATE = 0.20                # |composite| to open — matches live invalidation
DEFAULT_SLIPPAGE = 0.03    # $/bbl each way; CL tick is $0.01
MAX_HOLD_HOURS = 24 * 28   # 28d, matching the strategy engine's horizon
MIN_ROWS = 4               # below this a "simulation" is just anecdote
NOTIONAL_BBL = 1000        # 1 CL lot = 1,000 bbl


class SimLedgerEngine(Engine):
    name = "sim_ledger"
    version = "1.0"
    output_name = "sim_trades"

    def __init__(self, slippage: float = DEFAULT_SLIPPAGE,
                 min_rows: int = MIN_ROWS, gate: float = GATE) -> None:
        super().__init__()
        self.slippage = float(slippage)
        # The gate is a parameter so it can be swept, but a gate chosen by
        # sweeping THIS log would be fitted to nine days. Gate selection
        # belongs to the 678-week replay panel; see gate_sweep.py.
        self.gate = float(gate)
        # Guard against drawing conclusions from a near-empty log. Lowered
        # only by tests, which need short deterministic fixtures.
        self.min_rows = int(min_rows)

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        from datetime import datetime

        rows = [r for r in load_rows()
                if r.get("composite") is not None and r.get("wti")]
        if len(rows) < self.min_rows:
            raise ValueError(
                f"signal log has {len(rows)} usable rows — need at least "
                f"{self.min_rows} to "
                "simulate. Let the watch loop run and capture signals first."
            )

        rows.sort(key=lambda r: r["at"])
        trades: list[dict[str, Any]] = []
        open_pos: dict[str, Any] | None = None

        def _dt(s: str) -> Any:
            return datetime.fromisoformat(s)

        for r in rows:
            score = float(r["composite"])
            px = float(r["wti"])
            when = r["at"]

            if open_pos is None:
                if abs(score) >= self.gate:
                    side = "long" if score > 0 else "short"
                    fill = px + self.slippage if side == "long" \
                        else px - self.slippage
                    open_pos = {
                        "side": side,
                        "entry_at": when,
                        "entry_mark": round(px, 2),
                        "entry_fill": round(fill, 4),
                        "entry_score": round(score, 3),
                        "size_pct": r.get("strategy_size_pct"),
                        "entry_confidence": r.get("confidence_score"),
                        "entry_label": r.get("label"),
                    }
                continue

            # position open — test the exits
            side = open_pos["side"]
            held_h = (_dt(when) - _dt(open_pos["entry_at"])).total_seconds() / 3600
            crossed_zero = (side == "long" and score <= 0) or \
                           (side == "short" and score >= 0)
            timed_out = held_h >= MAX_HOLD_HOURS

            if crossed_zero or timed_out:
                fill = px - self.slippage if side == "long" \
                    else px + self.slippage
                move = (fill - open_pos["entry_fill"]) if side == "long" \
                    else (open_pos["entry_fill"] - fill)
                trades.append({
                    **open_pos,
                    "exit_at": when,
                    "exit_mark": round(px, 2),
                    "exit_fill": round(fill, 4),
                    "exit_score": round(score, 3),
                    "exit_reason": "score_crossed_zero" if crossed_zero
                                   else "max_hold",
                    "held_hours": round(held_h, 2),
                    "move_per_bbl": round(move, 4),
                    "return_pct": round(move / open_pos["entry_fill"], 5),
                    "pnl_per_lot_usd": round(move * NOTIONAL_BBL, 2),
                })
                open_pos = None

        # mark any still-open position, clearly flagged
        marked_open = None
        if open_pos is not None:
            last = rows[-1]
            px = float(last["wti"])
            side = open_pos["side"]
            move = (px - open_pos["entry_fill"]) if side == "long" \
                else (open_pos["entry_fill"] - px)
            marked_open = {
                **open_pos,
                "status": "open",
                "mark": round(px, 2),
                "mark_at": last["at"],
                "unrealized_per_bbl": round(move, 4),
                "unrealized_per_lot_usd": round(move * NOTIONAL_BBL, 2),
            }

        summary = self._summarise(trades)
        if trades:
            warnings.append(
                f"{len(trades)} simulated trades from "
                f"{summary['span_days']} days of signal log. These are NOT "
                "independent observations if they fall inside one market "
                "regime — read alongside data/replay_report.json, which "
                "spans many."
            )
        warnings.append(
            f"slippage {self.slippage:+.3f}/bbl each way is an assumption, "
            "not a measurement. Real fills in a 68%-vol tape will be worse."
        )

        data = {
            "gate": self.gate,
            "slippage_per_bbl": self.slippage,
            "max_hold_hours": MAX_HOLD_HOURS,
            "notional_bbl_per_lot": NOTIONAL_BBL,
            "signal_rows_used": len(rows),
            "summary": summary,
            "open_position": marked_open,
            "trades": trades,
            "independence_note": (
                "Simulated off the live signal log. Trades within one regime "
                "are correlated draws, not independent samples. Doc 010 "
                "invariant 6: this ledger is research only and never feeds "
                "production decisions. data/trades.json is untouched."
            ),
        }
        evidence = [
            f"{len(rows)} signal-log rows",
            f"{len(trades)} closed simulated trades",
            f"gate |{self.gate}|, slippage {self.slippage}/bbl",
        ]
        return data, evidence

    @staticmethod
    def _summarise(trades: list[dict[str, Any]]) -> dict[str, Any]:
        if not trades:
            return {"closed": 0, "note": "no closed simulated trades yet",
                    "span_days": 0}
        import statistics as st
        from datetime import datetime

        rets = [t["return_pct"] for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r < 0]
        gw, gl = sum(wins), abs(sum(losses))

        eq, peak, mdd = 1.0, 1.0, 0.0
        for r in rets:
            eq *= (1 + r)
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)

        first = datetime.fromisoformat(trades[0]["entry_at"])
        last = datetime.fromisoformat(trades[-1]["exit_at"])

        # concentration: how much of total P&L is the single best trade?
        pnls = [t["pnl_per_lot_usd"] for t in trades]
        total = sum(pnls)
        top = max(pnls) if pnls else 0
        conc = round(top / total, 3) if total > 0 else None

        return {
            "closed": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "hit_rate": round(len(wins) / len(trades), 4),
            "mean_return": round(st.mean(rets), 5),
            "median_return": round(st.median(rets), 5),
            "total_pnl_per_lot_usd": round(total, 2),
            "profit_factor": round(gw / gl, 3) if gl > 0 else None,
            "max_drawdown": round(mdd, 5),
            "best_trade_pnl": round(top, 2),
            "top_trade_share_of_pnl": conc,
            "mean_hold_hours": round(st.mean(
                [t["held_hours"] for t in trades]), 1),
            "span_days": round((last - first).total_seconds() / 86400, 1),
        }

    def verify_output(self, data: dict[str, Any]) -> None:
        if data["signal_rows_used"] < self.min_rows:
            raise ValueError("too few signal rows to simulate")


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    slip = float(args[0]) if args else DEFAULT_SLIPPAGE
    result = SimLedgerEngine(slippage=slip).run()
    if not result.ok:
        print(f"sim ledger failed: {result.error}", file=sys.stderr)
        raise SystemExit(1)

    d = result.data
    s = d["summary"]
    print("\nSimulated Trade Ledger (research only — trades.json untouched)")
    print("=" * 66)
    print(f"  {d['signal_rows_used']} signal rows · gate |{d['gate']}| · "
          f"slippage {d['slippage_per_bbl']}/bbl each way")

    if s.get("closed"):
        print(f"\n  closed {s['closed']}  ({s['wins']}W / {s['losses']}L, "
              f"hit {s['hit_rate']:.1%})  over {s['span_days']}d")
        print(f"  mean {s['mean_return']:+.4%} · median "
              f"{s['median_return']:+.4%} · mean hold {s['mean_hold_hours']}h")
        pf = f"{s['profit_factor']:.2f}" if s.get("profit_factor") else "n/a"
        print(f"  P&L/lot ${s['total_pnl_per_lot_usd']:+,.0f} · PF {pf} · "
              f"maxDD {s['max_drawdown']:+.1%}")
        if s.get("top_trade_share_of_pnl") is not None:
            print(f"  concentration: best trade is "
                  f"{s['top_trade_share_of_pnl']:.0%} of total P&L")
    else:
        print(f"\n  {s.get('note')}")

    if d.get("open_position"):
        o = d["open_position"]
        print(f"\n  OPEN {o['side']} from {o['entry_fill']} "
              f"· mark {o['mark']} · unrealized "
              f"${o['unrealized_per_lot_usd']:+,.0f}/lot")

    for w in result.warnings:
        print(f"\n  ⚠ {w}")
    print(f"\n  Published → data/{SimLedgerEngine.output_name}.json")


if __name__ == "__main__":
    main()
