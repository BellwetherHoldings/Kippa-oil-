"""
Gate Sweep — how the entry threshold trades off sample size against edge.

Governed by docs/010_Backtesting.md.

WHY THIS EXISTS. The live signal log yields very few simulated trades
because the composite rarely clears |0.20|. The obvious response is to
lower the gate. The obvious response is also how backtests get fitted:
sweep a parameter on a short sample, keep the best number, and publish an
edge that does not exist.

So this engine deliberately separates two questions that are easy to
conflate:

  1. WHICH GATE HAS AN EDGE?  Answered ONLY on the replay panel — 678
     out-of-sample weeks, walk-forward expanding weights, sign-flip
     bootstrap on every gate. Never on the live log.

  2. HOW MANY SAMPLES DOES A GATE YIELD?  Answered on the live signal log.
     This is a throughput question, not a performance question, and the
     live column is reported with no return statistics attached so it
     cannot be mistaken for evidence of edge.

MULTIPLE TESTING IS EXPLICIT. Sweeping k gates and reporting the best
p-value is k shots at significance. This engine reports the naive p-value
AND a Bonferroni-adjusted threshold, and its verdict refuses to endorse a
gate whose edge does not survive the adjustment. It also prefers a PLATEAU
— a run of adjacent gates that all work — over an isolated peak, because
an isolated peak in a parameter sweep is usually noise.

Publishes: data/gate_sweep.json
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

GATES = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)
SEED = 20260714
BOOTSTRAP_N = 2000
MIN_TRADES_TO_JUDGE = 30      # below this, a gate's stats are anecdote


class GateSweepEngine(Engine):
    name = "gate_sweep"
    version = "1.0"
    output_name = "gate_sweep"

    def __init__(self, gates: tuple[float, ...] = GATES) -> None:
        super().__init__()
        self.gates = tuple(gates)

    def validate_input(self, inputs: dict[str, Any]) -> None:
        if len(self.gates) < 2:
            raise ValueError("a sweep needs at least two gates")

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        import random
        import statistics as st

        from src.engines.data_processing.features import build_weekly_panel
        from src.engines.backtesting.replay import (
            MIN_TRAIN, REPLAYABLE, _expanding_weights, _metrics, _signals,
        )

        panel = (build_weekly_panel().sort_values("period")
                 .reset_index(drop=True))
        for name in REPLAYABLE:
            panel[name] = panel.apply(lambda r: _signals(r)[name], axis=1)
        warnings.append(
            "REDUCED COMPOSITE: the panel replays only "
            f"{', '.join(REPLAYABLE)}. Geopolitical risk, supply-chain "
            "stress, chokepoint capacity and CFTC positioning are not "
            "historically reconstructible. Gate conclusions describe the "
            "reduced composite, not the live six-component signal.")
        rows: list[dict[str, Any]] = []
        for i in range(MIN_TRAIN, len(panel)):
            obs = panel.iloc[i]
            w = _expanding_weights(panel.iloc[:i])       # strictly prior
            rows.append({
                "period": str(obs["period"].date()),
                "score": sum(w[k] * float(obs[k]) for k in REPLAYABLE),
                "fwd_ret_1w": float(obs["fwd_ret_1w"]),
            })

        third = len(rows) // 3
        late_rows = rows[2 * third:]                     # the regime we trade in

        def _boot_p(returns: list[float]) -> float:
            if len(returns) < 5:
                return float("nan")
            rng = random.Random(SEED)
            obs_mean = st.fmean(returns)
            hits = sum(
                1 for _ in range(BOOTSTRAP_N)
                if st.fmean([r * (1 if rng.random() < 0.5 else -1)
                             for r in returns]) >= obs_mean
            )
            return (hits + 1) / (BOOTSTRAP_N + 1)

        def _directional(sample: list[dict[str, Any]], gate: float) -> list[float]:
            return [r["fwd_ret_1w"] if r["score"] > 0 else -r["fwd_ret_1w"]
                    for r in sample if abs(r["score"]) >= gate]

        # live-log throughput only — no return stats, deliberately
        live_counts = self._live_sample_yield(warnings)

        results = []
        for gate in self.gates:
            full = _directional(rows, gate)
            late = _directional(late_rows, gate)
            m = _metrics(full, f"gate_{gate}")
            lm = _metrics(late, f"gate_{gate}_late")
            p = _boot_p(full)
            results.append({
                "gate": gate,
                "trades_full_sample": len(full),
                "trade_rate": round(len(full) / len(rows), 3),
                "mean_return": m.get("mean_return"),
                "hit_rate": m.get("hit_rate"),
                "profit_factor": m.get("profit_factor"),
                "sharpe_per_obs": m.get("sharpe_per_obs"),
                "max_drawdown": m.get("max_drawdown"),
                "bootstrap_p": round(p, 4) if p == p else None,
                "late_regime": {
                    "trades": len(late),
                    "mean_return": lm.get("mean_return"),
                    "hit_rate": lm.get("hit_rate"),
                    "profit_factor": lm.get("profit_factor"),
                },
                "live_log_trades": live_counts.get(gate),
                "judgeable": len(full) >= MIN_TRADES_TO_JUDGE,
            })

        k = len(self.gates)
        bonferroni = 0.05 / k
        verdict, chosen = self._verdict(results, bonferroni, k)

        warnings.append(
            f"{k} gates tested — that is {k} chances at a 5% false positive. "
            f"Bonferroni threshold applied: p < {bonferroni:.4f}, not 0.05."
        )
        warnings.append(
            "live_log_trades is a THROUGHPUT count from the live signal log. "
            "No return statistics are attached to it on purpose: nine days "
            "inside one regime cannot measure edge, only sample yield."
        )

        data = {
            "gates_tested": list(self.gates),
            "out_of_sample_weeks": len(rows),
            "late_regime_weeks": len(late_rows),
            "late_regime_from": late_rows[0]["period"],
            "bonferroni_threshold": round(bonferroni, 4),
            "min_trades_to_judge": MIN_TRADES_TO_JUDGE,
            "results": results,
            "recommended_gate": chosen,
            "verdict": verdict,
            "seed": SEED,
            "bootstrap_resamples": BOOTSTRAP_N,
            "independence_note": (
                "Gate performance is measured ONLY on the walk-forward replay "
                "panel. The live signal log contributes sample counts and "
                "nothing else. Doc 010 invariant 6: research only."
            ),
        }
        evidence = [
            f"{len(rows)} out-of-sample weeks, expanding weights",
            f"{k} gates swept, Bonferroni p < {bonferroni:.4f}",
            f"late regime from {late_rows[0]['period']}, {len(late_rows)} weeks",
        ]
        return data, evidence

    @staticmethod
    def _live_sample_yield(warnings: list[str]) -> dict[float, int]:
        """How many simulated trades each gate would have produced live."""
        from src.engines.backtesting.sim_ledger import SimLedgerEngine
        out: dict[float, int] = {}
        for gate in GATES:
            try:
                r = SimLedgerEngine(gate=gate).run({})
                out[gate] = r.data["summary"].get("closed", 0)
            except Exception as exc:                       # noqa: BLE001
                warnings.append(f"live yield at gate {gate} unavailable: {exc}")
                out[gate] = None
        return out

    @staticmethod
    def _verdict(results: list[dict[str, Any]], bonferroni: float,
                 k: int) -> tuple[str, float | None]:
        judgeable = [r for r in results if r["judgeable"]]
        if not judgeable:
            return ("No gate produced enough trades to judge. Nothing to "
                    "recommend."), None

        survivors = [r for r in judgeable
                     if r["bootstrap_p"] is not None
                     and r["bootstrap_p"] < bonferroni]
        if not survivors:
            best = min(judgeable, key=lambda r: r["bootstrap_p"] or 1.0)
            return (
                f"NO GATE SURVIVES MULTIPLE-TESTING CORRECTION. The best of "
                f"{k} is |{best['gate']}| at p={best['bootstrap_p']}, which "
                f"does not clear the Bonferroni threshold of {bonferroni:.4f}. "
                "Reading this sweep as 'gate X works' would be fitting noise. "
                "Any gate change here is a throughput decision, not an edge "
                "decision, and must be labelled as such."
            ), None

        # prefer a plateau: a survivor whose neighbours also survive
        surv_gates = {r["gate"] for r in survivors}
        plateau = [r for r in survivors
                   if sum(1 for g in surv_gates
                          if abs(g - r["gate"]) <= 0.051) >= 2]
        pool = plateau or survivors
        pick = max(pool, key=lambda r: r["sharpe_per_obs"] or -9)
        shape = "a plateau of adjacent surviving gates" if plateau \
            else "an ISOLATED peak — treat with suspicion"
        return (
            f"Gate |{pick['gate']}| survives Bonferroni (p={pick['bootstrap_p']}"
            f" < {bonferroni:.4f}) within {shape}. "
            f"Late-regime hit rate {pick['late_regime']['hit_rate']}."
        ), pick["gate"]


def main(argv: list[str] | None = None) -> int:
    eng = GateSweepEngine()
    res = eng.run({})
    d = res.data
    print("Gate Sweep — entry threshold vs edge and sample yield")
    print("=" * 78)
    print(f"  {d['out_of_sample_weeks']} out-of-sample weeks · "
          f"late regime from {d['late_regime_from']} "
          f"({d['late_regime_weeks']}w)")
    print(f"  Bonferroni threshold p < {d['bonferroni_threshold']} "
          f"({len(d['gates_tested'])} gates tested)\n")
    hdr = (f"  {'gate':>5} {'trades':>7} {'hit':>6} {'mean':>8} {'PF':>6} "
           f"{'p':>7} {'late hit':>9} {'live n':>7}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in d["results"]:
        lr = r["late_regime"]
        flag = "" if r["judgeable"] else "  (too few)"
        print(f"  {r['gate']:>5.2f} {r['trades_full_sample']:>7} "
              f"{_pct(r['hit_rate']):>6} {_pct(r['mean_return']):>8} "
              f"{_num(r['profit_factor']):>6} {_num(r['bootstrap_p']):>7} "
              f"{_pct(lr['hit_rate']):>9} "
              f"{r['live_log_trades'] if r['live_log_trades'] is not None else '-':>7}"
              f"{flag}")
    print(f"\n  VERDICT: {d['verdict']}")
    for w in (res.warnings or []):
        print(f"  ⚠ {w}")
    print(f"\n  Published → {DATA_DIR / 'gate_sweep.json'}")
    return 0


def _pct(v: Any) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def _num(v: Any) -> str:
    return "-" if v is None else f"{v:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
