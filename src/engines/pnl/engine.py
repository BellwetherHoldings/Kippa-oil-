"""
PnL Engine — the live track record of the Kippa signal.

Not one of the 22 architecture docs; this is the performance ledger that
turns "we haven't had a losing trade yet" into a measured, evidence-bearing
number (Project Vision invariant #2: every output has supporting evidence).

Reads data/trades.json, marks every open position to the platform's WTI
price feed, and publishes data/pnl_summary.json:
    - per-trade realized/unrealized P&L in $/bbl, %, and $ (when contracts set)
    - aggregate realized + open (unrealized) P&L
    - win/loss record and win rate on CLOSED trades
    - current directional exposure (net contracts / net side)

A NYMEX WTI (CL) contract is 1,000 barrels; dollar P&L uses that multiplier
when `contracts` is set, otherwise P&L is reported per barrel and in percent.

Usage:
    python src/cli/oil.py pnl show
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

CONTRACT_BBL = 1000          # CL contract size, barrels
LEDGER_NAME = "trades.json"


def _latest_mark() -> tuple[float, str]:
    """Latest WTI price and its date from the platform feed."""
    from src.data.live_prices import load_price_history
    hist = load_price_history()
    last = hist.dropna(subset=["close"]).iloc[-1]
    return float(last["close"]), str(last["date"])[:10]


def _trade_pnl(trade: dict[str, Any], mark: float) -> dict[str, Any]:
    """Compute one trade's P&L versus the mark (or its own exit if closed).

    Dollar P&L, in priority order: an explicit broker-reported
    realized_pnl_usd (closed trades imported from alerts); else
    units x point_value x price move; else CL contracts x 1000 bbl x move.
    Entry/exit prices may be absent on an imported trade that carries only
    its realized P&L — the price-derived fields then report null.
    """
    side = trade["side"]
    direction = 1.0 if side == "long" else -1.0
    closed = trade["status"] == "closed"
    entry = trade.get("entry_price")
    entry = float(entry) if entry is not None else None
    if closed:
        ex = trade.get("exit_price")
        ref = float(ex) if ex is not None else None
    else:
        ref = mark

    if entry is not None and ref is not None:
        per_bbl = (ref - entry) * direction
        pct = per_bbl / entry
    else:
        per_bbl = pct = None

    realized = trade.get("realized_pnl_usd")
    units, pv = trade.get("units"), trade.get("point_value")
    contracts = trade.get("contracts")
    if closed and realized is not None:
        dollars = float(realized)
    elif per_bbl is not None and units is not None and pv is not None:
        dollars = per_bbl * float(units) * float(pv)
    elif per_bbl is not None and contracts:
        dollars = per_bbl * contracts * CONTRACT_BBL
    else:
        dollars = None

    winner = (dollars > 0) if dollars is not None else (
        per_bbl > 0 if per_bbl is not None else False)

    return {
        "id": trade["id"],
        "side": side,
        "kind": trade.get("kind", "paper"),
        "status": trade["status"],
        "account": trade.get("account"),
        "entry_price": round(entry, 2) if entry is not None else None,
        "mark_or_exit": round(ref, 2) if ref is not None else None,
        "pnl_per_bbl": round(per_bbl, 2) if per_bbl is not None else None,
        "pnl_pct": round(pct, 4) if pct is not None else None,
        "pnl_usd": round(dollars, 2) if dollars is not None else None,
        "winner": winner,
        "notes": trade.get("notes", ""),
    }


class PnLEngine(Engine):
    name = "pnl"
    version = "1.0"
    output_name = "pnl_summary"

    def validate_input(self, inputs: dict[str, Any]) -> None:
        ledger = (inputs.get("data_dir") or DATA_DIR) / LEDGER_NAME
        if not ledger.exists():
            raise ValueError(f"trade ledger missing: {ledger}")
        book = json.loads(ledger.read_text())
        if not isinstance(book.get("trades"), list):
            raise ValueError("trades.json must contain a 'trades' list")

    def _accounts(self, book: dict[str, Any]) -> list[dict[str, Any]]:
        """Per-trader equity summary from the ledger's 'accounts' block."""
        out = []
        for a in book.get("accounts", []):
            dep = float(a.get("deposit") or 0)
            eq = float(a.get("current_equity") or 0)
            gain = eq - dep
            out.append({
                "id": a["id"],
                "owner": a.get("owner", a["id"]),
                "kind": a.get("kind", "paper"),
                "deposit": round(dep, 2),
                "current_equity": round(eq, 2),
                "gain_usd": round(gain, 2),
                "total_return_pct": round(gain / dep, 4) if dep else None,
                "multiple": round(eq / dep, 2) if dep else None,
                "week_return_pct": a.get("week_return_pct"),
                "as_of": a.get("as_of"),
                "note": a.get("note", ""),
            })
        return out

    def execute(self, inputs, warnings):
        data_dir = inputs.get("data_dir") or DATA_DIR
        book = json.loads((data_dir / LEDGER_NAME).read_text())
        trades = book["trades"]

        mark, mark_date = _latest_mark()
        rows = [_trade_pnl(t, mark) for t in trades]

        closed = [r for r in rows if r["status"] == "closed"]
        openp = [r for r in rows if r["status"] == "open"]
        wins = [r for r in closed if r["winner"]]
        losses = [r for r in closed if not r["winner"]]

        realized_usd = sum(r["pnl_usd"] for r in closed
                           if r["pnl_usd"] is not None)
        open_usd = sum(r["pnl_usd"] for r in openp
                       if r["pnl_usd"] is not None)
        # net directional exposure in contracts (long +, short -)
        net_contracts = sum(
            (1 if t["side"] == "long" else -1) * (t.get("contracts") or 0)
            for t in trades if t["status"] == "open")

        record = f"{len(wins)}-{len(losses)}"
        win_rate = (len(wins) / len(closed)) if closed else None

        accounts = self._accounts(book)

        data = {
            "as_of_mark": mark,
            "mark_date": mark_date,
            "account_mode": book.get("account", {}).get("mode", "paper"),
            "testing_until": book.get("account", {}).get("testing_until"),
            "accounts": accounts,
            "trades_total": len(rows),
            "open_count": len(openp),
            "closed_count": len(closed),
            "record_closed": record,
            "win_rate_closed": round(win_rate, 3) if win_rate is not None
            else None,
            "open_winners": sum(1 for r in openp if r["winner"]),
            "open_losers": sum(1 for r in openp if not r["winner"]),
            "realized_pnl_usd": round(realized_usd, 2),
            "open_pnl_usd": round(open_usd, 2),
            "net_open_contracts": net_contracts,
            "avg_open_pnl_pct": round(
                sum(r["pnl_pct"] for r in openp if r["pnl_pct"] is not None)
                / len(openp), 4) if openp else None,
            "trades": rows,
        }

        evidence = [
            f"Marked to platform WTI feed ${mark:.2f} ({mark_date}).",
            f"Closed record {record}"
            + (f" ({win_rate:.0%} win rate)." if win_rate is not None
               else " (no closed trades yet)."),
        ]
        for a in accounts:
            if a["total_return_pct"] is not None:
                evidence.append(
                    f"{a['owner']} ({a['kind']}): ${a['deposit']:,.0f} → "
                    f"${a['current_equity']:,.0f} "
                    f"({a['total_return_pct']:+.0%}, {a['multiple']}x).")
        for r in openp:
            evidence.append(
                f"{r['id']} {r['side']} @ ${r['entry_price']:.2f} → "
                f"${r['mark_or_exit']:.2f}: {r['pnl_pct']:+.2%}"
                + (f" (${r['pnl_usd']:+,.0f})" if r["pnl_usd"] is not None
                   else ""))
        if data["open_losers"]:
            warnings.append(f"{data['open_losers']} open position(s) underwater.")
        return data, evidence


def _fmt(data: dict[str, Any]) -> str:
    lines = [
        "PnL — Kippa signal track record",
        "=" * 60,
        f"  mark ${data['as_of_mark']:.2f} ({data['mark_date']}) · "
        f"mode: {data['account_mode']}"
        + (f" · testing until {data['testing_until']}"
           if data.get("testing_until") else ""),
        f"  closed record {data['record_closed']}"
        + (f" ({data['win_rate_closed']:.0%})"
           if data['win_rate_closed'] is not None else "")
        + f"  ·  {data['open_count']} open "
        f"({data['open_winners']} green / {data['open_losers']} red)",
    ]
    if data.get("avg_open_pnl_pct") is not None:
        lines.append(f"  avg open position: {data['avg_open_pnl_pct']:+.2%}")
    if data["realized_pnl_usd"] or data["open_pnl_usd"]:
        lines.append(f"  realized ${data['realized_pnl_usd']:+,.0f}  ·  "
                     f"open ${data['open_pnl_usd']:+,.0f}")
    for a in data.get("accounts", []):
        wk = (f", +{a['week_return_pct']:.0%} wk"
              if a.get("week_return_pct") is not None else "")
        ret = (f"{a['total_return_pct']:+.0%} ({a['multiple']}x)"
               if a["total_return_pct"] is not None else "—")
        lines.append(f"  account [{a['owner']}/{a['kind']}]: "
                     f"${a['deposit']:,.0f} → ${a['current_equity']:,.0f}  "
                     f"{ret}{wk}")
    lines.append("")
    for r in data["trades"]:
        tag = "OPEN " if r["status"] == "open" else "closed"
        usd = f"  ${r['pnl_usd']:+,.0f}" if r["pnl_usd"] is not None else ""
        if r["entry_price"] is not None and r["mark_or_exit"] is not None:
            px = f"@ ${r['entry_price']:.2f} → ${r['mark_or_exit']:.2f}"
        else:
            px = "(prices n/a)"
        pct = f"{r['pnl_pct']:+.2%}" if r["pnl_pct"] is not None else "   —  "
        lines.append(f"  [{tag}] {r['side']:<5} {px}   {pct}{usd}")
    return "\n".join(lines)


def main() -> None:
    result = PnLEngine().run()
    if not result.ok:
        raise SystemExit(f"pnl failed: {result.error}")
    print(_fmt(result.data))


if __name__ == "__main__":
    main()
