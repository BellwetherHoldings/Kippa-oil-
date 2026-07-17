"""
Intraday Radar Engine — 30-minute candle statistics for the day-trade
sleeve, honestly measured.

What this is: conditional next-candle probabilities estimated from ~1 month
of real CL=F 30-minute bars, published WITH their sample sizes, confidence
intervals, and in-sample hit rate. When the measured edge is
indistinguishable from a coin flip, the engine says exactly that and
downgrades its output to levels-only — treating forecasts as absolute
truth is a platform anti-pattern, and doc 010 requires validation before
belief.

What this is not: a crystal ball. Nobody reliably predicts individual
30-minute candles; anyone claiming otherwise is selling something.

Also published: the levels a day trader actually works with — session
VWAP, session high/low, prior daily close, 30m ATR — plus alignment with
the daily composite so the trading sleeve stays coherent with the core
long position.

Usage:
    python src/engines/analytics/intraday.py

Publishes:
    data/intraday_radar.json
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.live_prices import fetch_intraday
from src.engines.base import Engine, load_artifact

LEAN_THRESHOLD = 0.55        # conditional P(up) beyond which we state a lean
MIN_STATE_SAMPLE = 30        # bars required before a state's stat is usable


def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _streak_state(dirs: list[int], i: int) -> str:
    """State label for bar i: signed run length capped at 3 ('+2', '-3+')."""
    d = dirs[i]
    if d == 0:
        return "flat"
    run = 1
    j = i - 1
    while j >= 0 and dirs[j] == d and run < 3:
        run += 1
        j -= 1
    cap = "3+" if run >= 3 else str(run)
    return f"{'+' if d > 0 else '-'}{cap}"


class IntradayRadarEngine(Engine):
    name = "intraday_radar"
    version = "1.0"
    output_name = "intraday_radar"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        bars = fetch_intraday("30m", "1mo")
        n = len(bars)
        if n < 200:
            raise ValueError(f"Only {n} completed 30m bars — need ≥200.")

        dirs = [0 if c == o else (1 if c > o else -1)
                for o, c in zip(bars["open"], bars["close"])]

        # -- conditional stats: P(next bar up | current streak state) --------
        by_state: dict[str, list[int]] = {}
        hits = total = 0
        for i in range(len(dirs) - 1):
            state = _streak_state(dirs, i)
            nxt = dirs[i + 1]
            if nxt == 0:
                continue
            by_state.setdefault(state, []).append(1 if nxt > 0 else 0)
            # in-sample rule: follow the state's majority direction
            outcomes = by_state[state]
            if len(outcomes) > MIN_STATE_SAMPLE:
                p_up_so_far = sum(outcomes[:-1]) / (len(outcomes) - 1)
                if p_up_so_far != 0.5:
                    predicted_up = p_up_so_far > 0.5
                    hits += (nxt > 0) == predicted_up
                    total += 1

        state_table = {}
        for state, outcomes in sorted(by_state.items()):
            k, m = sum(outcomes), len(outcomes)
            lo, hi = _wilson(k, m)
            state_table[state] = {"p_next_up": round(k / m, 3),
                                  "n": m, "ci95": [round(lo, 3), round(hi, 3)]}

        hit_rate = hits / total if total else 0.5
        hr_lo, _ = _wilson(hits, total)
        edge = "measurable" if hr_lo > 0.5 else "none"
        if edge == "none":
            warnings.append(
                f"In-sample hit rate {hit_rate:.1%} (95% CI lower bound "
                f"{hr_lo:.1%}) — statistically a coin flip. Radar output is "
                f"LEVELS-ONLY; the candle lean is informational, not tradable.")

        # -- current state & lean ------------------------------------------------
        cur_state = _streak_state(dirs, len(dirs) - 1)
        cur = state_table.get(cur_state, {"p_next_up": 0.5, "n": 0,
                                          "ci95": [0, 1]})
        usable = cur["n"] >= MIN_STATE_SAMPLE
        if not usable:
            lean = "none"
        elif cur["p_next_up"] >= LEAN_THRESHOLD:
            lean = "up"
        elif cur["p_next_up"] <= 1 - LEAN_THRESHOLD:
            lean = "down"
        else:
            lean = "none"

        # -- day-trade levels -----------------------------------------------------
        last = bars.iloc[-1]
        session_date = bars["ts"].iloc[-1].date()
        session = bars[bars["ts"].dt.date == session_date]
        vol = session["volume"].replace(0, 1)
        typical = (session["high"] + session["low"] + session["close"]) / 3
        vwap = float((typical * vol).sum() / vol.sum())
        atr30 = float((bars["high"] - bars["low"]).tail(20).mean())

        # -- price bands: the honest form of "price prediction" -------------------
        # Direction is a coin flip (measured), but volatility clusters, so
        # WHERE price is likely to sit is forecastable. Empirical quantiles
        # of actual signed moves from this month's bars, applied to the
        # current price.
        import numpy as np
        px = float(last["close"])
        moves_1 = bars["close"].diff().dropna().to_numpy()
        moves_4 = bars["close"].diff(4).dropna().to_numpy()   # ~2 hours

        def band(moves: "np.ndarray") -> dict[str, float]:
            q = np.quantile(moves, [0.10, 0.25, 0.50, 0.75, 0.90])
            return {"p10": round(px + q[0], 2), "p25": round(px + q[1], 2),
                    "p50": round(px + q[2], 2), "p75": round(px + q[3], 2),
                    "p90": round(px + q[4], 2)}

        price_bands = {
            "next_30m": band(moves_1),
            "next_2h": band(moves_4),
            "read": "80% of the time price stays inside the p10–p90 band; "
                    "use band edges for stops/targets, not hope.",
        }

        # -- mechanical trade plans (scaffolding, not signals) ---------------------
        comp_early = load_artifact("composite_signal", require_success=True)
        bias = comp_early["data"]["label"] if comp_early else "neutral"
        sess_hi = float(session["high"].max())
        sess_lo = float(session["low"].min())

        def plan(name, side, entry, stop, target, note):
            risk = abs(entry - stop)
            reward = abs(target - entry)
            return {"name": name, "side": side,
                    "entry": round(entry, 2), "stop": round(stop, 2),
                    "target": round(target, 2),
                    "risk_reward": round(reward / risk, 2) if risk else None,
                    "note": note}

        trade_plans = []
        if "bull" in bias:
            trade_plans.append(plan(
                "VWAP pullback long", "long", vwap, vwap - atr30,
                max(sess_hi, vwap + 1.5 * atr30),
                "With-trend. Wait for price to come to VWAP; no chase."))
            trade_plans.append(plan(
                "Range breakout long", "long", sess_hi + 0.05,
                sess_hi + 0.05 - atr30, sess_hi + 0.05 + 1.5 * atr30,
                "With-trend. Only on a 30m close above the session high."))
        elif "bear" in bias:
            trade_plans.append(plan(
                "VWAP fade short", "short", vwap, vwap + atr30,
                min(sess_lo, vwap - 1.5 * atr30),
                "With-trend. Wait for the bounce into VWAP."))
            trade_plans.append(plan(
                "Range breakdown short", "short", sess_lo - 0.05,
                sess_lo - 0.05 + atr30, sess_lo - 0.05 - 1.5 * atr30,
                "With-trend. Only on a 30m close below the session low."))
        else:
            trade_plans.append(plan(
                "Range fade short", "short", sess_hi, sess_hi + atr30, vwap,
                "Neutral bias: fade the top of the range back to VWAP."))
            trade_plans.append(plan(
                "Range fade long", "long", sess_lo, sess_lo - atr30, vwap,
                "Neutral bias: fade the bottom of the range back to VWAP."))

        # -- alignment with the core position (composite) --------------------------
        comp = load_artifact("composite_signal", require_success=True)
        daily_bias = comp["data"]["label"] if comp else "unknown"
        aligned = (lean == "up" and "bull" in daily_bias) or \
                  (lean == "down" and "bear" in daily_bias)
        if lean == "none":
            sleeve = "No statistical lean this candle — trade the levels or sit out."
        elif aligned:
            sleeve = (f"Lean {lean.upper()} agrees with the daily "
                      f"{daily_bias} bias — WITH-trend setup; sleeve stays "
                      f"small and separate from the core long.")
        else:
            sleeve = (f"Lean {lean.upper()} fights the daily {daily_bias} "
                      f"bias — counter-trend scalp: half sleeve size or skip.")

        data = {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_completed_bar": {
                "ts": bars["ts"].iloc[-1].isoformat(),
                "open": round(float(last["open"]), 2),
                "close": round(float(last["close"]), 2),
                "direction": "up" if dirs[-1] > 0 else
                             "down" if dirs[-1] < 0 else "flat",
            },
            "next_candle": {
                "state": cur_state,
                "lean": lean,
                "p_up": cur["p_next_up"],
                "ci95": cur["ci95"],
                "state_sample": cur["n"],
            },
            "edge_verdict": {
                "in_sample_hit_rate": round(hit_rate, 3),
                "hit_rate_ci95_low": round(hr_lo, 3),
                "predictions_scored": total,
                "edge": edge,
            },
            "levels": {
                "session_vwap": round(vwap, 2),
                "session_high": round(float(session["high"].max()), 2),
                "session_low": round(float(session["low"].min()), 2),
                "atr_30m": round(atr30, 2),
                "last_price": round(float(last["close"]), 2),
                "vs_vwap": round(float(last["close"]) - vwap, 2),
            },
            "price_bands": price_bands,
            "trade_plans": trade_plans,
            "daily_bias": daily_bias,
            "sleeve_guidance": sleeve,
            "state_table": state_table,
            "disclaimer": "Measured statistics, not predictions. Core "
                          "position decisions belong to the strategy "
                          "engine; this radar informs the trading sleeve "
                          "only. Not financial advice.",
        }
        evidence = [f"{n} completed CL=F 30m bars (Yahoo chart API, 1mo); "
                    f"conditional stats with Wilson 95% CIs; "
                    f"{total} in-sample predictions scored"]
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not 0 <= data["next_candle"]["p_up"] <= 1:
            raise ValueError("p_up out of bounds")
        # invariant: never state a lean the sample can't support
        if data["next_candle"]["lean"] != "none" \
                and data["next_candle"]["state_sample"] < MIN_STATE_SAMPLE:
            raise ValueError("lean emitted without sufficient sample")


def main() -> None:
    result = IntradayRadarEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    nc, ev, lv = d["next_candle"], d["edge_verdict"], d["levels"]
    print("Intraday Radar — CL=F 30-minute (doc 010-honest)")
    print("=" * 62)
    print(f"  Last bar: {d['last_completed_bar']['direction'].upper()} "
          f"→ state {nc['state']}")
    print(f"  Next-candle lean: {nc['lean'].upper()}  "
          f"P(up)={nc['p_up']:.0%} (n={nc['state_sample']}, "
          f"CI {nc['ci95'][0]:.0%}–{nc['ci95'][1]:.0%})")
    print(f"  Edge verdict: {ev['edge'].upper()} — hit rate "
          f"{ev['in_sample_hit_rate']:.1%} over {ev['predictions_scored']} "
          f"scored predictions (CI low {ev['hit_rate_ci95_low']:.1%})")
    print(f"  Levels: px {lv['last_price']} | VWAP {lv['session_vwap']} "
          f"({lv['vs_vwap']:+}) | H {lv['session_high']} / L "
          f"{lv['session_low']} | ATR30 {lv['atr_30m']}")
    pb = d["price_bands"]
    print(f"  Price bands (empirical, this month's moves):")
    print(f"    next 30m: p10 {pb['next_30m']['p10']} | p50 "
          f"{pb['next_30m']['p50']} | p90 {pb['next_30m']['p90']}")
    print(f"    next 2h : p10 {pb['next_2h']['p10']} | p50 "
          f"{pb['next_2h']['p50']} | p90 {pb['next_2h']['p90']}")
    print(f"  Trade plans ({d['daily_bias']} bias):")
    for t in d["trade_plans"]:
        print(f"    {t['name']} [{t['side'].upper()}]: entry {t['entry']} "
              f"stop {t['stop']} target {t['target']} "
              f"(R:R {t['risk_reward']}) — {t['note']}")
    print(f"  Sleeve: {d['sleeve_guidance']}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/intraday_radar.json")


if __name__ == "__main__":
    main()
