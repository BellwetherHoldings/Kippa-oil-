"""
Simulation Engine — Monte Carlo WTI price paths under named scenarios.

Governed by docs/011_Simulation.md. Block-bootstraps a decade of WTI
daily returns (preserving short-range autocorrelation and fat tails) and
applies the scenario overlays defined in simulations/scenarios.json.
Results are percentile fans and threshold probabilities per scenario —
distributions, never single-path stories. Seeded RNG keeps every run
reproducible (invariant: outputs must be reproducible).

Usage:
    python src/engines/simulation/engine.py

Publishes:
    data/simulation_results.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine

SCENARIOS_PATH = _REPO_ROOT / "simulations" / "scenarios.json"

N_PATHS = 2000
HORIZON_DAYS = 63          # ~3 months of sessions
BLOCK = 5                  # bootstrap block length (sessions)
SEED = 20260714
PRICE_THRESHOLDS = (60.0, 80.0, 100.0)


class SimulationEngine(Engine):
    name = "simulation"
    version = "1.0"
    output_name = "simulation_results"

    def validate_input(self, inputs: dict[str, Any]) -> None:
        if not SCENARIOS_PATH.exists():
            raise ValueError(f"{SCENARIOS_PATH} not found.")
        if not (DATA_DIR / "wti_prices.csv").exists():
            raise ValueError("wti_prices.csv not found — pull data first.")

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        from src.data.live_prices import load_price_history
        prices = load_price_history().sort_values("date")
        returns = prices["close"].pct_change().dropna().to_numpy()
        returns = returns[-2520:]                    # last ~10 years
        spot = float(prices["close"].iloc[-1])
        anchor_date = prices["date"].iloc[-1].date()

        scenarios = json.loads(SCENARIOS_PATH.read_text())["scenarios"]
        rng = np.random.default_rng(SEED)

        # pre-draw one shared block-bootstrap return cube for comparability:
        # scenarios differ only by overlay, not by random draw
        n_blocks = HORIZON_DAYS // BLOCK + 1
        starts = rng.integers(0, len(returns) - BLOCK, size=(N_PATHS, n_blocks))
        base = np.stack([
            np.concatenate([returns[s:s + BLOCK] for s in row])[:HORIZON_DAYS]
            for row in starts
        ])
        mean_ret = returns.mean()

        results = []
        for sc in scenarios:
            r = (base - mean_ret) * sc["vol_mult"] + mean_ret
            drift = np.zeros(HORIZON_DAYS)
            span = sc["first_n_days"] or HORIZON_DAYS
            drift[:span] = sc["drift_shift_daily"]
            paths = spot * np.cumprod(1 + r + drift, axis=1)
            terminal = paths[:, -1]

            pct = {f"p{p}": round(float(np.percentile(terminal, p)), 2)
                   for p in (5, 25, 50, 75, 95)}
            probs = {
                f"P(>{t:.0f})": round(float((terminal > t).mean()), 3)
                for t in PRICE_THRESHOLDS
            }
            results.append({
                "id": sc["id"],
                "label": sc["label"],
                "rationale": sc["rationale"],
                "terminal_percentiles": pct,
                "threshold_probabilities": probs,
                "max_drawdown_median": round(float(np.median(
                    (paths.min(axis=1) / spot) - 1)), 3),
            })

        # -- sensitivity analysis (doc 011): how fragile is each scenario's
        #    median to its own assumptions? ±25% on each overlay parameter.
        sensitivity = []
        for sc in scenarios:
            if sc["id"] == "baseline":
                continue
            span = sc["first_n_days"] or HORIZON_DAYS
            entry = {"id": sc["id"], "parameters": {}}
            base_median = None
            for d_mult, v_mult, label in (
                (1.0, 1.0, "as_defined"),
                (1.25, 1.0, "drift_+25%"), (0.75, 1.0, "drift_-25%"),
                (1.0, 1.25, "vol_+25%"), (1.0, 0.75, "vol_-25%"),
            ):
                r = (base - mean_ret) * sc["vol_mult"] * v_mult + mean_ret
                drift = np.zeros(HORIZON_DAYS)
                drift[:span] = sc["drift_shift_daily"] * d_mult
                terminal = spot * np.prod(1 + r + drift, axis=1)
                median = float(np.median(terminal))
                if label == "as_defined":
                    base_median = median
                else:
                    entry["parameters"][label] = {
                        "p50": round(median, 2),
                        "delta_vs_defined": round(median - base_median, 2),
                    }
            entry["p50_as_defined"] = round(base_median, 2)
            entry["dominant_assumption"] = max(
                entry["parameters"],
                key=lambda k: abs(entry["parameters"][k]["delta_vs_defined"]),
            )
            sensitivity.append(entry)

        if (date.today() - anchor_date).days > 5:
            warnings.append(
                f"Simulations anchored at ${spot:.2f} ({anchor_date}) — "
                f"{(date.today() - anchor_date).days}d stale; live spot is "
                f"materially higher after the 07-13 re-closure."
            )

        data = {
            "as_of": date.today().isoformat(),
            "anchor_price": spot,
            "anchor_date": anchor_date.isoformat(),
            "horizon_days": HORIZON_DAYS,
            "paths_per_scenario": N_PATHS,
            "method": f"block bootstrap (block={BLOCK}) of last "
                      f"{len(returns)} daily returns, seeded ({SEED})",
            "scenarios": results,
            "sensitivity": sensitivity,
        }
        evidence = [
            f"data/wti_prices.csv daily returns through {anchor_date}",
            f"simulations/scenarios.json ({len(scenarios)} scenario definitions)",
        ]
        return data, evidence


def main() -> None:
    result = SimulationEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print(f"Monte Carlo Simulation — {d['horizon_days']} sessions, "
          f"{d['paths_per_scenario']} paths/scenario")
    print("=" * 66)
    print(f"  Anchor ${d['anchor_price']:.2f} ({d['anchor_date']})\n")
    for sc in d["scenarios"]:
        p = sc["terminal_percentiles"]
        pr = sc["threshold_probabilities"]
        print(f"  {sc['label']}")
        print(f"    fan: P5 ${p['p5']} | P25 ${p['p25']} | P50 ${p['p50']} "
              f"| P75 ${p['p75']} | P95 ${p['p95']}")
        print(f"    {' '.join(f'{k}={v:.0%}' for k, v in pr.items())} "
              f"| median max drawdown {sc['max_drawdown_median']:+.0%}\n")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/simulation_results.json")


if __name__ == "__main__":
    main()
