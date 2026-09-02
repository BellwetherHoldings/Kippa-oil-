"""
Intraday Simulated Ledger — the day-trade signals, marked to real 30m bars.

Governed by docs/010_Backtesting.md. Companion to sim_ledger.py, which does
the same job for the daily composite. Kept in a SEPARATE artifact
(data/intraday_sim.json) so the two can be compared without being pooled:
they trade different horizons, different costs and different sample sizes,
and averaging them would hide exactly the differences worth seeing.

TWO SIGNALS ARE SIMULATED SEPARATELY, because the radar emits two things
that are often conflated:

  A. CANDLE LEAN — the streak-state directional call, P(next 30m bar up).
     Simulated strictly walk-forward: the state table for bar i is built
     only from bars < i. One bar in, one bar out, marked close-to-close.

  B. TRADE PLANS — the mechanical VWAP-pullback / range-breakout /
     range-fade archetypes. Entry on touch, exit on stop or target.

INTRABAR AMBIGUITY IS RESOLVED PESSIMISTICALLY. With OHLC alone we cannot
know whether the stop or the target was reached first inside a bar that
touched both. Optimistic resolution ("assume the target") is the single
most common way an intraday backtest invents an edge that does not exist.
When both are touched in the same bar this engine books the STOP.

COSTS ARE THE POINT AT THIS HORIZON. A 30-minute bar moves a fraction of a
dollar; slippage of a few cents each way is a large share of the average
move. The engine reports gross and net side by side so the cost drag is
visible rather than buried.

Publishes: data/intraday_sim.json
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine

RANGE = "60d"
INTERVAL = "30m"
SLIPPAGE = 0.03            # $/bbl each way, same assumption as the daily ledger
MIN_STATE_SAMPLE = 20      # a streak state needs this many priors to predict
LEAN_EDGE = 0.02           # |p_up - 0.5| must exceed this to act
NOTIONAL_BBL = 1000
SEED = 20260714
BOOTSTRAP_N = 2000
ATR_WIN = 14


class IntradaySimEngine(Engine):
    name = "intraday_sim"
    version = "1.0"
    output_name = "intraday_sim"

    def __init__(self, slippage: float = SLIPPAGE) -> None:
        super().__init__()
        self.slippage = float(slippage)

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        from src.data.live_prices import fetch_intraday

        bars = fetch_intraday(INTERVAL, RANGE).reset_index(drop=True)
        if len(bars) < 300:
            raise ValueError(f"{len(bars)} bars — need ≥300 to simulate.")

        o = bars["open"].tolist()
        h = bars["high"].tolist()
        lo = bars["low"].tolist()
        c = bars["close"].tolist()
        ts = [str(t) for t in bars["ts"].tolist()]

        lean = self._candle_lean(o, c, ts, warnings)
        plans = self._plans(o, h, lo, c, ts, warnings)

        warnings.append(
            f"{RANGE} of {INTERVAL} bars is ONE regime "
            f"({ts[0][:10]} → {ts[-1][:10]}, war-elevated volatility). These "
            "are not independent observations across regimes and must not be "
            "read as a general edge estimate.")
        warnings.append(
            "Bars that touch both stop and target are booked as STOPS. With "
            "OHLC only, the intrabar path is unknown; the pessimistic "
            "convention is deliberate.")

        data = {
            "interval": INTERVAL,
            "range": RANGE,
            "bars": len(bars),
            "from": ts[0],
            "to": ts[-1],
            "slippage_per_bbl": self.slippage,
            "notional_bbl_per_lot": NOTIONAL_BBL,
            "candle_lean": lean,
            "trade_plans": plans,
            "comparison_vs_daily": self._compare(lean, plans, warnings),
            "independence_note": (
                "Research only (doc 010 invariant 6). data/trades.json is "
                "untouched. Reported separately from the daily composite "
                "ledger because the horizons, costs and sample sizes differ "
                "by orders of magnitude; pooling them would hide that."),
            "seed": SEED,
        }
        evidence = [
            f"{len(bars)} {INTERVAL} bars over {RANGE}",
            f"candle lean: {lean['trades']} simulated bar trades",
            f"trade plans: {sum(p['trades'] for p in plans.values())} setups",
            f"slippage {self.slippage}/bbl each way, stops win ties",
        ]
        return data, evidence

    # ---- A. candle lean, strictly walk-forward ---------------------------
    def _candle_lean(self, o, c, ts, warnings) -> dict[str, Any]:
        from src.engines.analytics.intraday import _streak_state

        dirs = [0 if cc == oo else (1 if cc > oo else -1) for oo, cc in zip(o, c)]
        table: dict[str, list[int]] = {}
        gross, net, wins, n = [], [], 0, 0
        first_pred = None

        for i in range(len(dirs) - 1):
            state = _streak_state(dirs, i)
            prior = table.get(state, [])
            # predict using ONLY what was known before this outcome
            if len(prior) >= MIN_STATE_SAMPLE:
                p_up = sum(prior) / len(prior)
                if abs(p_up - 0.5) > LEAN_EDGE:
                    side = 1 if p_up > 0.5 else -1
                    g = side * (c[i + 1] - c[i])
                    gross.append(g)
                    net.append(g - 2 * self.slippage)
                    wins += g - 2 * self.slippage > 0
                    n += 1
                    if first_pred is None:
                        first_pred = ts[i + 1]
            nxt = dirs[i + 1]
            if nxt != 0:
                table.setdefault(state, []).append(1 if nxt > 0 else 0)

        return {
            "rule": ("follow the streak state's prior majority when it has "
                     f"≥{MIN_STATE_SAMPLE} observations and |p−0.5| > {LEAN_EDGE}"),
            "trades": n,
            "first_prediction": first_pred,
            "hit_rate_net": round(wins / n, 3) if n else None,
            **self._stats(gross, net, n),
        }

    # ---- B. mechanical plan archetypes -----------------------------------
    def _plans(self, o, h, lo, c, ts, warnings) -> dict[str, Any]:
        atr = self._atr(h, lo, c)
        sessions = self._session_index(ts)
        out: dict[str, dict[str, Any]] = {}

        for name, kind in (("vwap_pullback_long", "with-trend"),
                           ("range_breakout_long", "with-trend"),
                           ("range_fade_short", "counter-trend")):
            gross, net, wins, n, holds = [], [], 0, 0, []
            for s_start, s_end in sessions:
                if s_end - s_start < 8 or atr[s_start] is None:
                    continue
                # build the session's reference levels from its FIRST HALF
                mid = s_start + (s_end - s_start) // 2
                seg_h = max(h[s_start:mid]); seg_l = min(lo[s_start:mid])
                vwap = sum(c[s_start:mid]) / (mid - s_start)
                a = atr[mid] or 0.0
                if a <= 0:
                    continue
                if name == "vwap_pullback_long":
                    side, entry, stop, tgt = 1, vwap, vwap - a, vwap + 1.5 * a
                elif name == "range_breakout_long":
                    side, entry = 1, seg_h + 0.05
                    stop, tgt = entry - a, entry + 1.5 * a
                else:
                    side, entry = -1, seg_h
                    stop, tgt = seg_h + a, vwap
                r = self._walk(h, lo, mid, s_end, side, entry, stop, tgt)
                if r is None:
                    continue
                g, held = r
                gross.append(g); net.append(g - 2 * self.slippage)
                wins += (g - 2 * self.slippage) > 0
                holds.append(held); n += 1
            out[name] = {
                "kind": kind, "trades": n,
                "hit_rate_net": round(wins / n, 3) if n else None,
                "mean_hold_bars": round(sum(holds) / len(holds), 1) if holds else None,
                **self._stats(gross, net, n),
            }
        warnings.append(
            "Trade plans are simulated UNCONDITIONALLY. Live, they are gated "
            "on the daily composite's bias label, which is not historically "
            "reconstructible before 2026-08-10. The gate could only improve "
            "or worsen these numbers; it is not applied.")
        return out

    @staticmethod
    def _walk(h, lo, start, end, side, entry, stop, tgt):
        """Enter on touch, exit on stop/target. Stops win intrabar ties."""
        entered = None
        for i in range(start, end):
            if entered is None:
                touched = (lo[i] <= entry <= h[i])
                if touched:
                    entered = i
                continue
            hit_stop = lo[i] <= stop if side > 0 else h[i] >= stop
            hit_tgt = h[i] >= tgt if side > 0 else lo[i] <= tgt
            if hit_stop:                     # pessimistic: stop wins ties
                return side * (stop - entry), i - entered
            if hit_tgt:
                return side * (tgt - entry), i - entered
        if entered is not None:              # flat at session end
            return side * (h[end - 1] + lo[end - 1]) / 2 - side * entry, \
                end - 1 - entered
        return None

    @staticmethod
    def _atr(h, lo, c) -> list[float | None]:
        out: list[float | None] = [None] * len(c)
        trs = []
        for i in range(1, len(c)):
            trs.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]),
                           abs(lo[i] - c[i - 1])))
            if len(trs) >= ATR_WIN:
                out[i] = sum(trs[-ATR_WIN:]) / ATR_WIN
        return out

    @staticmethod
    def _session_index(ts: list[str]) -> list[tuple[int, int]]:
        spans, start = [], 0
        for i in range(1, len(ts)):
            if ts[i][:10] != ts[i - 1][:10]:
                spans.append((start, i)); start = i
        spans.append((start, len(ts)))
        return spans

    @staticmethod
    def _stats(gross: list[float], net: list[float], n: int) -> dict[str, Any]:
        if not net:
            return {"note": "no simulated trades"}
        import random
        import statistics as st
        wins = [x for x in net if x > 0]
        losses = [-x for x in net if x < 0]
        rng = random.Random(SEED)
        obs = st.fmean(net)
        hits = sum(1 for _ in range(BOOTSTRAP_N)
                   if st.fmean([x * (1 if rng.random() < 0.5 else -1)
                                for x in net]) >= obs)
        eq, peak, dd = 0.0, 0.0, 0.0
        for x in net:
            eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
        gross_wins = sum(1 for x in gross if x > 0)
        return {
            "hit_rate_gross": round(gross_wins / len(gross), 3) if gross else None,
            "gross_per_bbl": round(sum(gross), 3),
            "net_per_bbl": round(sum(net), 3),
            "cost_drag_per_bbl": round(sum(gross) - sum(net), 3),
            "mean_net_per_bbl": round(obs, 4),
            "net_pnl_per_lot_usd": round(sum(net) * NOTIONAL_BBL, 2),
            "profit_factor": round(sum(wins) / sum(losses), 3) if losses else None,
            "max_drawdown_per_bbl": round(dd, 3),
            "bootstrap_p": round((hits + 1) / (BOOTSTRAP_N + 1), 4),
        }

    @staticmethod
    def _compare(lean, plans, warnings) -> dict[str, Any]:
        from src.engines.base import load_artifact
        daily = load_artifact("sim_trades", require_success=False)
        d = (daily or {}).get("data", {}).get("summary", {})
        return {
            "daily_composite": {
                "trades": d.get("closed"),
                "hit_rate": d.get("hit_rate"),
                "net_pnl_per_lot_usd": d.get("total_pnl_per_lot_usd"),
                "span_days": d.get("span_days"),
            },
            "intraday_candle_lean": {
                "trades": lean.get("trades"),
                "hit_rate": lean.get("hit_rate_net"),
                "net_pnl_per_lot_usd": lean.get("net_pnl_per_lot_usd"),
            },
            "intraday_plans_total": {
                "trades": sum(p["trades"] for p in plans.values()),
                "net_pnl_per_lot_usd": round(
                    sum(p.get("net_pnl_per_lot_usd") or 0 for p in plans.values()), 2),
            },
            "read": ("Sample sizes differ by orders of magnitude. The daily "
                     "ledger measures nine days of one regime; the intraday "
                     "ledger measures sixty days of the same regime at a much "
                     "higher trade count. More trades is more precision about "
                     "THIS regime only — it is not more generality."),
        }

    def verify_output(self, data: dict[str, Any]) -> None:
        if data["candle_lean"]["trades"] < 0:
            raise ValueError("negative trade count")


def main(argv: list[str] | None = None) -> int:
    res = IntradaySimEngine().run({})
    d = res.data
    print("Intraday Simulated Ledger (research only — trades.json untouched)")
    print("=" * 74)
    print(f"  {d['bars']} × {d['interval']} bars · {d['from'][:10]} → "
          f"{d['to'][:10]} · slippage {d['slippage_per_bbl']}/bbl each way\n")

    ln = d["candle_lean"]
    print("  A. CANDLE LEAN (streak-state, walk-forward)")
    print(f"     {ln['trades']} trades · hit {_pct(ln.get('hit_rate_gross'))} gross / "
          f"{_pct(ln.get('hit_rate_net'))} net · "
          f"net ${ln.get('net_pnl_per_lot_usd')}/lot · "
          f"PF {ln.get('profit_factor')} · p={ln.get('bootstrap_p')}")
    print(f"     gross {ln.get('gross_per_bbl')}/bbl − costs "
          f"{ln.get('cost_drag_per_bbl')}/bbl = net {ln.get('net_per_bbl')}/bbl")

    print("\n  B. MECHANICAL PLANS")
    for name, p in d["trade_plans"].items():
        print(f"     {name:22} {p['trades']:>4} trades · "
              f"hit {_pct(p.get('hit_rate_gross')):>6}g/{_pct(p.get('hit_rate_net')):>6}n · "
              f"net ${p.get('net_pnl_per_lot_usd')}/lot · "
              f"PF {p.get('profit_factor')} · p={p.get('bootstrap_p')}")

    cmp_ = d["comparison_vs_daily"]
    print("\n  C. VS THE DAILY COMPOSITE LEDGER")
    for k in ("daily_composite", "intraday_candle_lean", "intraday_plans_total"):
        v = cmp_[k]
        print(f"     {k:24} trades {str(v.get('trades')):>5} · "
              f"net ${v.get('net_pnl_per_lot_usd')}/lot")

    for w in (res.warnings or []):
        print(f"\n  ⚠ {w}")
    print(f"\n  Published → {DATA_DIR / 'intraday_sim.json'}")
    return 0


def _pct(v: Any) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
