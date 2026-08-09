"""
Historical Replay — walk-forward backtest of the composite signal.

Governed by docs/010_Backtesting.md. The live paper record is 16 trades
drawn from a single market regime (the 2026 Hormuz crisis). Those are
correlated observations: running the signal more often inside one regime
adds rows, not evidence. This module produces the alternative — replay
the scoring rule across the full weekly panel, which spans many regimes,
and measure it against a baseline.

WHAT THIS CAN AND CANNOT DO, stated up front because doc 010 forbids
overselling a backtest:

  - The panel carries inventory surprise, momentum and realized vol.
    Those three components can be reconstructed at every historical week.
  - Geopolitical risk, supply-chain stress, chokepoint capacity and CFTC
    positioning CANNOT be reconstructed historically — the event registry
    only exists for 2026 and the chokepoint series began in July. Any
    claim to replay the *full* seven-component composite would be
    fabricated history.
  - So this replays a REDUCED composite over the reconstructible subset,
    and reports it as such. A reduced-composite result is evidence about
    the reduced composite, not about the live one.

Walk-forward discipline: at each week the score uses only data available
at that week. Weights come either from the shipped config (`static`) or
from an expanding window that re-derives them from history strictly
before the observation (`expanding`) — the latter is the honest test,
because the shipped weights were themselves chosen with hindsight.

Usage:
    python src/cli/oil.py backtest replay [static|expanding]

Publishes:
    data/replay_report.json
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

# components reconstructible from the weekly panel
REPLAYABLE = ("inventory_surprise", "price_momentum", "vol_regime")

MIN_TRAIN = 104          # 2y of weeks before the first out-of-sample call
BOOTSTRAP_N = 2000       # resamples for the significance test
SEED = 20260809          # fixed so every run reproduces (invariant 1)


def _tanh(x: float) -> float:
    """Squash to [-1,+1] without importing numpy for one call."""
    import math
    return math.tanh(x)


def _signals(row) -> dict[str, float]:
    """Reconstruct the replayable component signals at one week.

    Orientation matches the live engine: positive = bullish.
      inventory_surprise: a draw bigger than seasonal (-z) is bullish
      price_momentum:     5-session continuation
      vol_regime:         high realized vol carries a risk premium
    """
    return {
        "inventory_surprise": _tanh(-float(row["surprise_z"])),
        "price_momentum": _tanh(float(row["momentum_5d"]) * 10.0),
        "vol_regime": _tanh((float(row["vol_20d"]) - 0.35) * 2.0),
    }


def _static_weights() -> dict[str, float]:
    """Shipped weights, renormalised over the replayable subset."""
    cfg = _REPO_ROOT / "config" / "weights.json"
    raw = {}
    if cfg.exists():
        blob = json.loads(cfg.read_text())
        raw = blob.get("weights", blob)
    sub = {k: float(raw.get(k, 1.0)) for k in REPLAYABLE}
    tot = sum(sub.values()) or 1.0
    return {k: v / tot for k, v in sub.items()}


def _expanding_weights(hist) -> dict[str, float]:
    """Re-derive weights from history strictly before the observation.

    Weight ∝ |Spearman IC| of the component against forward 1w return in
    the training window. Components with no measurable edge get near-zero
    weight, which is the point: this is what the platform would have
    concluded from data available at the time, not from hindsight.
    """
    ics = {}
    for name in REPLAYABLE:
        col = hist[name]
        if col.std() == 0 or len(col) < 20:
            ics[name] = 0.0
            continue
        ic = col.corr(hist["fwd_ret_1w"], method="spearman")
        ics[name] = 0.0 if ic != ic else abs(float(ic))   # NaN guard
    tot = sum(ics.values())
    if tot <= 0:
        return {k: 1.0 / len(REPLAYABLE) for k in REPLAYABLE}
    return {k: v / tot for k, v in ics.items()}


def _bootstrap_p(returns: list[float], n: int = BOOTSTRAP_N) -> float:
    """Two-sided p-value for mean(returns) != 0 by sign-flip resampling.

    Sign-flipping preserves the return magnitudes and asks only whether
    the *direction* attribution beats chance — the right null for a
    directional signal.
    """
    import random
    if len(returns) < 10:
        return float("nan")
    rng = random.Random(SEED)
    observed = abs(sum(returns) / len(returns))
    hits = 0
    for _ in range(n):
        flipped = [r if rng.random() < 0.5 else -r for r in returns]
        if abs(sum(flipped) / len(flipped)) >= observed:
            hits += 1
    return hits / n


def _metrics(rets: list[float], label: str) -> dict[str, Any]:
    if not rets:
        return {"label": label, "n": 0}
    import statistics as st
    mean = sum(rets) / len(rets)
    sd = st.pstdev(rets) if len(rets) > 1 else 0.0
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    # equity path for drawdown
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)

    return {
        "label": label,
        "n": len(rets),
        "mean_return": round(mean, 5),
        "median_return": round(st.median(rets), 5),
        "stdev": round(sd, 5),
        "sharpe_per_obs": round(mean / sd, 4) if sd > 0 else None,
        "hit_rate": round(len(wins) / len(rets), 4),
        "profit_factor": round(gross_win / gross_loss, 3)
                         if gross_loss > 0 else None,
        "best": round(max(rets), 5),
        "worst": round(min(rets), 5),
        "total_compounded": round(eq - 1.0, 5),
        "max_drawdown": round(mdd, 5),
    }


class ReplayEngine(Engine):
    name = "replay"
    version = "1.0"
    output_name = "replay_report"

    def __init__(self, mode: str = "expanding") -> None:
        super().__init__()
        if mode not in ("static", "expanding"):
            raise ValueError("mode must be 'static' or 'expanding'")
        self.mode = mode

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        from src.engines.data_processing.features import build_weekly_panel

        panel = build_weekly_panel().sort_values("period").reset_index(drop=True)

        # materialise component columns once
        for name in REPLAYABLE:
            panel[name] = panel.apply(lambda r: _signals(r)[name], axis=1)

        warnings.append(
            "REDUCED COMPOSITE: replays only "
            f"{', '.join(REPLAYABLE)}. Geopolitical risk, supply-chain "
            "stress, chokepoint capacity and CFTC positioning are not "
            "historically reconstructible — the event registry starts in "
            "2026 and the chokepoint series in July 2026. Results describe "
            "the reduced composite, NOT the live seven-component signal."
        )

        static_w = _static_weights()
        rows: list[dict[str, Any]] = []

        for i in range(MIN_TRAIN, len(panel)):
            obs = panel.iloc[i]
            if self.mode == "expanding":
                weights = _expanding_weights(panel.iloc[:i])   # strictly prior
            else:
                weights = static_w

            score = sum(weights[k] * float(obs[k]) for k in REPLAYABLE)
            rows.append({
                "period": str(obs["period"].date()),
                "score": round(score, 4),
                "fwd_ret_1w": float(obs["fwd_ret_1w"]),
                "fwd_ret_4w": float(obs["fwd_ret_4w"]),
                "weights": {k: round(v, 4) for k, v in weights.items()},
            })

        if len(rows) < 50:
            raise ValueError(
                f"Only {len(rows)} out-of-sample weeks after a {MIN_TRAIN}-week "
                "training burn-in — too few to evaluate."
            )

        # -- strategy: take the signal's direction when it clears the gate --
        GATE = 0.2                       # matches the live |0.2| invalidation
        traded = [r for r in rows if abs(r["score"]) >= GATE]
        directional = [
            r["fwd_ret_1w"] if r["score"] > 0 else -r["fwd_ret_1w"]
            for r in traded
        ]

        # -- baselines (doc 010 requires comparison, not a bare number) -----
        always_long = [r["fwd_ret_1w"] for r in rows]
        import random
        rng = random.Random(SEED)
        coin_flip = [
            r["fwd_ret_1w"] * (1 if rng.random() < 0.5 else -1) for r in traded
        ]

        signal_m = _metrics(directional, "composite_gated")
        long_m = _metrics(always_long, "baseline_always_long")
        coin_m = _metrics(coin_flip, "baseline_coin_flip")

        p_value = _bootstrap_p(directional)
        edge_vs_long = (
            round(signal_m.get("mean_return", 0) - long_m.get("mean_return", 0), 5)
            if signal_m.get("n") else None
        )

        # -- IC of the replayed score itself --------------------------------
        import pandas as pd
        rdf = pd.DataFrame(rows)
        ic_1w = float(rdf["score"].corr(rdf["fwd_ret_1w"], method="spearman"))
        ic_4w = float(rdf["score"].corr(rdf["fwd_ret_4w"], method="spearman"))

        # -- regime split: is the edge concentrated in one era? -------------
        third = len(rows) // 3
        eras = {}
        for tag, sl in (("early", rows[:third]),
                        ("middle", rows[third:2 * third]),
                        ("late", rows[2 * third:])):
            era_traded = [r for r in sl if abs(r["score"]) >= GATE]
            era_ret = [r["fwd_ret_1w"] if r["score"] > 0 else -r["fwd_ret_1w"]
                       for r in era_traded]
            eras[tag] = {
                "from": sl[0]["period"], "to": sl[-1]["period"],
                **_metrics(era_ret, tag),
            }

        verdict = self._verdict(signal_m, long_m, p_value, eras)

        data = {
            "mode": self.mode,
            "components_replayed": list(REPLAYABLE),
            "panel_weeks": len(panel),
            "train_burn_in": MIN_TRAIN,
            "out_of_sample_weeks": len(rows),
            "gate": GATE,
            "trades_taken": len(traded),
            "trade_rate": round(len(traded) / len(rows), 3),
            "ic_spearman_1w": round(ic_1w, 4),
            "ic_spearman_4w": round(ic_4w, 4),
            "performance": signal_m,
            "baselines": [long_m, coin_m],
            "edge_vs_always_long": edge_vs_long,
            "bootstrap_p_value": round(p_value, 4) if p_value == p_value else None,
            "bootstrap_resamples": BOOTSTRAP_N,
            "regime_split": eras,
            "verdict": verdict,
            "seed": SEED,
        }

        evidence = [
            f"weekly panel: {len(panel)} weeks",
            f"out-of-sample: {len(rows)} weeks after {MIN_TRAIN}-week burn-in",
            f"weights: {self.mode}",
            f"sign-flip bootstrap: {BOOTSTRAP_N} resamples, seed {SEED}",
        ]
        return data, evidence

    @staticmethod
    def _verdict(sig, base, p, eras) -> str:
        if not sig.get("n"):
            return "no trades cleared the gate — nothing to evaluate"
        if p == p and p > 0.10:
            return (f"NOT SIGNIFICANT — mean {sig['mean_return']:+.4f} per week "
                    f"but sign-flip p={p:.3f}. Consistent with chance.")
        if sig["mean_return"] <= base.get("mean_return", 0):
            return ("NO EDGE OVER BASELINE — the gated signal did not beat "
                    "always-long. Any apparent skill is market drift.")
        era_means = [e.get("mean_return") for e in eras.values()
                     if e.get("mean_return") is not None]
        if era_means and sum(1 for m in era_means if m > 0) < 2:
            return ("FRAGILE — edge is concentrated in a single era and does "
                    "not persist across regimes. Treat as overfit.")
        return (f"EDGE PRESENT — mean {sig['mean_return']:+.4f}/wk vs "
                f"{base['mean_return']:+.4f} always-long, p={p:.3f}, "
                "positive in 2+ eras.")

    def verify_output(self, data: dict[str, Any]) -> None:
        if data["out_of_sample_weeks"] < 50:
            raise ValueError("insufficient out-of-sample window")
        if not data.get("baselines"):
            raise ValueError("doc 010 requires a baseline comparison")


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    mode = args[0] if args else "expanding"
    result = ReplayEngine(mode=mode).run()
    if not result.ok:
        print(f"replay failed: {result.error}", file=sys.stderr)
        raise SystemExit(1)

    d = result.data
    print(f"\nHistorical Replay — reduced composite ({d['mode']} weights)")
    print("=" * 66)
    print(f"  panel {d['panel_weeks']}w · out-of-sample {d['out_of_sample_weeks']}w "
          f"· {d['trades_taken']} trades ({d['trade_rate']:.0%} of weeks)")
    print(f"  components: {', '.join(d['components_replayed'])}")
    print(f"\n  IC 1w {d['ic_spearman_1w']:+.4f} · IC 4w {d['ic_spearman_4w']:+.4f}")

    p = d["performance"]
    print(f"\n  {'':<22}{'n':>5}{'mean':>10}{'hit':>8}{'PF':>8}{'maxDD':>9}")
    for m in [p] + d["baselines"]:
        if not m.get("n"):
            continue
        pf = f"{m['profit_factor']:.2f}" if m.get("profit_factor") else "  n/a"
        print(f"  {m['label']:<22}{m['n']:>5}{m['mean_return']:>+10.4f}"
              f"{m['hit_rate']:>8.1%}{pf:>8}{m['max_drawdown']:>+9.1%}")

    print(f"\n  edge vs always-long: {d['edge_vs_always_long']:+.5f}/wk")
    print(f"  sign-flip p-value:   {d['bootstrap_p_value']}")
    print("\n  Regime split:")
    for tag, e in d["regime_split"].items():
        if e.get("n"):
            print(f"    {tag:<8} {e['from']} → {e['to']}  n={e['n']:<4} "
                  f"mean {e['mean_return']:+.4f}  hit {e['hit_rate']:.1%}")

    print(f"\n  VERDICT: {d['verdict']}")
    for w in result.warnings:
        print(f"\n  ⚠ {w}")
    print(f"\n  Published → data/{ReplayEngine.output_name}.json")


if __name__ == "__main__":
    main()
