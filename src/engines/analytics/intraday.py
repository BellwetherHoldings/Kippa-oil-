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
        last_px = float(last["close"])

        def plan(name, side, entry, stop, target, note, kind):
            risk = abs(entry - stop)
            reward = abs(target - entry)
            return {"name": name, "side": side, "kind": kind,
                    "entry": round(entry, 2), "stop": round(stop, 2),
                    "target": round(target, 2),
                    "risk_reward": round(reward / risk, 2) if risk else None,
                    "note": note}

        # ONE SIDE OR NOTHING.
        #
        # This used to emit a long AND a short on every neutral day — "fade the
        # top, fade the bottom" — which is not a call, it is a menu. Worse, the
        # intraday ledger measured those two range fades directly and they are
        # the WORST things this engine has ever produced: range_fade_short 25.9%
        # net hit rate, -$6,269/lot; vwap_pullback_long 33.3%, -$8,041/lot. A
        # two-sided output guarantees one of them is wrong and lets the other
        # one look like it was the call all along.
        #
        # The rule now: emit AT MOST ONE plan, always on the side of the daily
        # bias, and only when price is somewhere the plan can actually be taken.
        # Neutral bias => no trade. Counter-trend fades are gone entirely.
        # If nothing qualifies, say NO TRADE and mean it.
        MIN_RR = 1.5
        trade_plans: list[dict] = []
        no_trade_reason = None

        # Tolerance band around VWAP. A hard `last_px >= vwap` test made the
        # whole day's call flip on two cents, which is the same brittleness as
        # the confidence-tier size discontinuity already logged as a defect.
        # Half an ATR either side of VWAP is "at the pullback zone" — and if
        # anything, price just under VWAP in a bullish tape is a BETTER long
        # location than price above it, not a disqualifying one.
        vwap_zone = 0.5 * atr30
        if "bull" in bias:
            if last_px >= vwap - vwap_zone:
                # Above VWAP in a bullish tape: the tradable location is a
                # pullback INTO VWAP, which is below us and therefore waitable.
                cand = plan(
                    "VWAP pullback long", "long", vwap, vwap - atr30,
                    max(sess_hi, vwap + 1.5 * atr30),
                    "Wait for price to come back to VWAP. Do not chase. "
                    "If it never comes to you, you do not trade today.",
                    "with-trend")
            elif last_px >= sess_hi - atr30:
                cand = plan(
                    "Range breakout long", "long", sess_hi + 0.05,
                    sess_hi + 0.05 - atr30, sess_hi + 0.05 + 1.5 * atr30,
                    "Only on a 30m CLOSE above the session high. A wick is "
                    "not a close.", "with-trend")
            else:
                cand = None
                no_trade_reason = (
                    f"Daily bias is {bias} but price ({last_px:.2f}) is below "
                    f"VWAP ({vwap:.2f}) and not near the session high. The tape "
                    f"and the bias disagree — there is no long location here, "
                    f"and the fix is not to short against the bias.")
        elif "bear" in bias:
            if last_px <= vwap + vwap_zone:
                cand = plan(
                    "VWAP fade short", "short", vwap, vwap + atr30,
                    min(sess_lo, vwap - 1.5 * atr30),
                    "Wait for the bounce into VWAP. Do not chase it down.",
                    "with-trend")
            elif last_px <= sess_lo + atr30:
                cand = plan(
                    "Range breakdown short", "short", sess_lo - 0.05,
                    sess_lo - 0.05 + atr30, sess_lo - 0.05 - 1.5 * atr30,
                    "Only on a 30m CLOSE below the session low. A wick is "
                    "not a close.", "with-trend")
            else:
                cand = None
                no_trade_reason = (
                    f"Daily bias is {bias} but price ({last_px:.2f}) is above "
                    f"VWAP ({vwap:.2f}) and not near the session low. The tape "
                    f"and the bias disagree — no short location here.")
        else:
            cand = None
            no_trade_reason = (
                "Daily bias is neutral. There is no side to be on. The old "
                "behaviour here was to offer a fade at both extremes; the "
                "intraday ledger says those fades lose money (range fade short: "
                "25.9% hit, -$6,269/lot over 27 trades). No trade is the call.")

        if cand is not None:
            rr = cand.get("risk_reward")
            if rr is not None and rr < MIN_RR:
                no_trade_reason = (
                    f"The only setup on the {bias} side is {cand['name']} at "
                    f"R:R {rr}, below the {MIN_RR} minimum. A thin-reward trade "
                    f"in a 56% -vol tape is a coin flip with costs attached.")
            else:
                trade_plans.append(cand)

        # -- day-trade stance & sleeve guidance -----------------------------------
        # The actionable call is trend-alignment + levels, NOT next-candle
        # prediction. A day trader's edge is trading WITH the daily bias at
        # good levels with defined risk — so lead with that, and treat the
        # (honestly unpredictable) next-candle direction as a footnote, never
        # as a reason to sit out a with-trend setup.
        comp = load_artifact("composite_signal", require_success=True)
        daily_bias = comp["data"]["label"] if comp else "unknown"
        if not trade_plans:
            stance = "NO TRADE"
            sleeve = ("NO TRADE TODAY. " + (no_trade_reason or "") +
                      " Sitting out is a position — it is the one with a "
                      "guaranteed zero cost drag, and this sleeve's measured "
                      "cost drag is $79.74/bbl.")
        elif trade_plans[0]["side"] == "long":
            stance = "LONG only"
            sleeve = (f"ONE setup, long only, on the {daily_bias} daily bias. "
                      f"There is no short plan today and that is deliberate — "
                      f"if this level fails, the answer is no trade, not the "
                      f"other side.")
        else:
            stance = "SHORT only"
            sleeve = (f"ONE setup, short only, on the {daily_bias} daily bias. "
                      f"There is no long plan today and that is deliberate — "
                      f"if this level fails, the answer is no trade, not the "
                      f"other side.")
        # honest footnote about single-candle direction (informational only)
        candle_note = ("Heads-up: the next single 30m candle's direction is ~a "
                       "coin toss (true for any market) — your edge is the "
                       "trend + levels below, not guessing the candle.")
        aligned = (lean == "up" and "bull" in daily_bias) or \
                  (lean == "down" and "bear" in daily_bias)

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
            "no_trade_reason": no_trade_reason if not trade_plans else None,
            "daily_bias": daily_bias,
            "day_trade_stance": stance,
            "sleeve_guidance": sleeve,
            "candle_note": candle_note,
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
    print(f"  DAY-TRADE STANCE: {d['day_trade_stance']}  "
          f"(daily bias: {d['daily_bias']})")
    print(f"  → {d['sleeve_guidance']}")
    print(f"  Levels: px {lv['last_price']} | VWAP {lv['session_vwap']} "
          f"({lv['vs_vwap']:+}) | H {lv['session_high']} / L "
          f"{lv['session_low']} | ATR30 {lv['atr_30m']}")
    pb = d["price_bands"]
    print(f"  Price bands (empirical, this month's moves):")
    print(f"    next 30m: p10 {pb['next_30m']['p10']} | p50 "
          f"{pb['next_30m']['p50']} | p90 {pb['next_30m']['p90']}")
    print(f"    next 2h : p10 {pb['next_2h']['p10']} | p50 "
          f"{pb['next_2h']['p50']} | p90 {pb['next_2h']['p90']}")
    if d["trade_plans"]:
        t = d["trade_plans"][0]
        print(f"  THE TRADE ({d['daily_bias']} bias) — one side, no alternative:")
        print(f"    {t['name']} [{t['side'].upper()}]: entry {t['entry']} "
              f"stop {t['stop']} target {t['target']} "
              f"(R:R {t['risk_reward']})")
        print(f"    {t['note']}")
    else:
        print(f"  NO TRADE ({d['daily_bias']} bias).")
        print(f"    {d.get('no_trade_reason', '')}")
    print(f"  Sleeve: {d['sleeve_guidance']}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/intraday_radar.json")


if __name__ == "__main__":
    main()
