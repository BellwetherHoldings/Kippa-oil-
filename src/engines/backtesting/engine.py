"""
Backtesting Engine — historical validation of the platform's signals.

Governed by docs/010_Backtesting.md. Event-study design over the weekly
feature panel: does the inventory surprise predict forward WTI returns,
and does short-term momentum continue? Reports information coefficients
(Spearman), bucketed forward returns, and hit rates — and renders an
honest verdict per signal (predictive / weak / not predictive) instead of
overselling. These verdicts are the evidence base for the composite's
weights (invariant: every analytical output must have supporting
evidence).

Usage:
    python src/engines/backtesting/engine.py

Publishes:
    data/backtest_report.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine
from src.engines.data_processing.features import build_weekly_panel

Z_EXTREME = 1.0            # |surprise_z| beyond this = extreme event
MOMENTUM_GATE = 0.02       # ±2% 5-session move = directional tape
IC_PREDICTIVE = 0.10       # |Spearman IC| thresholds for the verdict
IC_WEAK = 0.03


def _verdict(ic: float) -> str:
    if abs(ic) >= IC_PREDICTIVE:
        return "predictive"
    if abs(ic) >= IC_WEAK:
        return "weak"
    return "not predictive"


class BacktestingEngine(Engine):
    name = "backtesting"
    version = "1.0"
    output_name = "backtest_report"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        panel = build_weekly_panel()
        n = len(panel)

        # -- inventory surprise: bullish orientation = -z ---------------------
        ic_1w = float(panel["surprise_z"].mul(-1)
                      .corr(panel["fwd_ret_1w"], method="spearman"))
        ic_4w = float(panel["surprise_z"].mul(-1)
                      .corr(panel["fwd_ret_4w"], method="spearman"))

        bullish = panel[panel["surprise_z"] <= -Z_EXTREME]
        bearish = panel[panel["surprise_z"] >= Z_EXTREME]
        neutral = panel[panel["surprise_z"].abs() < Z_EXTREME]

        inventory = {
            "signal": "inventory_surprise",
            "orientation": "bullish = draw bigger than seasonal norm (-z)",
            "observations": n,
            "ic_spearman_1w": round(ic_1w, 4),
            "ic_spearman_4w": round(ic_4w, 4),
            "buckets": {
                f"bullish_extreme (z<=-{Z_EXTREME})": {
                    "n": len(bullish),
                    "mean_fwd_1w": round(float(bullish["fwd_ret_1w"].mean()), 4),
                    "mean_fwd_4w": round(float(bullish["fwd_ret_4w"].mean()), 4),
                    "hit_rate_1w_up": round(
                        float((bullish["fwd_ret_1w"] > 0).mean()), 3),
                },
                f"bearish_extreme (z>=+{Z_EXTREME})": {
                    "n": len(bearish),
                    "mean_fwd_1w": round(float(bearish["fwd_ret_1w"].mean()), 4),
                    "mean_fwd_4w": round(float(bearish["fwd_ret_4w"].mean()), 4),
                    "hit_rate_1w_down": round(
                        float((bearish["fwd_ret_1w"] < 0).mean()), 3),
                },
                "neutral": {
                    "n": len(neutral),
                    "mean_fwd_1w": round(float(neutral["fwd_ret_1w"].mean()), 4),
                    "mean_fwd_4w": round(float(neutral["fwd_ret_4w"].mean()), 4),
                },
            },
            "verdict_1w": _verdict(ic_1w),
            "verdict_4w": _verdict(ic_4w),
        }

        # -- momentum continuation ----------------------------------------------
        mom_ic = float(panel["momentum_5d"]
                       .corr(panel["fwd_ret_1w"], method="spearman"))
        up_tape = panel[panel["momentum_5d"] >= MOMENTUM_GATE]
        down_tape = panel[panel["momentum_5d"] <= -MOMENTUM_GATE]
        momentum = {
            "signal": "price_momentum_5d",
            "orientation": "continuation: recent direction persists",
            "ic_spearman_1w": round(mom_ic, 4),
            "buckets": {
                f"up_tape (>=+{MOMENTUM_GATE:.0%})": {
                    "n": len(up_tape),
                    "mean_fwd_1w": round(float(up_tape["fwd_ret_1w"].mean()), 4),
                    "hit_rate_continuation": round(
                        float((up_tape["fwd_ret_1w"] > 0).mean()), 3),
                },
                f"down_tape (<=-{MOMENTUM_GATE:.0%})": {
                    "n": len(down_tape),
                    "mean_fwd_1w": round(float(down_tape["fwd_ret_1w"].mean()), 4),
                    "hit_rate_continuation": round(
                        float((down_tape["fwd_ret_1w"] < 0).mean()), 3),
                },
            },
            "verdict_1w": _verdict(mom_ic),
        }

        # -- drift detection (doc 010): does predictive power hold over time? --
        half = n // 2
        early, late = panel.iloc[:half], panel.iloc[half:]
        drift = {}
        for label, col, invert in (
            ("inventory_surprise", "surprise_z", True),
            ("price_momentum_5d", "momentum_5d", False),
        ):
            sign = -1 if invert else 1
            ic_early = float((early[col] * sign)
                             .corr(early["fwd_ret_1w"], method="spearman"))
            ic_late = float((late[col] * sign)
                            .corr(late["fwd_ret_1w"], method="spearman"))
            drifting = (abs(ic_early) >= IC_WEAK
                        and abs(ic_late) < IC_WEAK) or \
                       (ic_early * ic_late < 0 and abs(ic_early) >= IC_WEAK)
            drift[label] = {
                "ic_first_half": round(ic_early, 4),
                "ic_second_half": round(ic_late, 4),
                "drift_detected": drifting,
            }
            if drifting:
                warnings.append(
                    f"{label}: predictive power drifted "
                    f"({ic_early:+.3f} → {ic_late:+.3f} across halves).")

        # -- weight recommendation (doc 006: weights need evidence) -----------
        inv_ic, mom_ic = max(ic_1w, 0.0), max(mom_ic, 0.0)
        total_ic = inv_ic + mom_ic
        weight_recommendation = {
            "scope": "relative split of the inventory+momentum weight budget",
            "inventory_share": round(inv_ic / total_ic, 2) if total_ic else 0.5,
            "momentum_share": round(mom_ic / total_ic, 2) if total_ic else 0.5,
            "basis": f"proportional to positive 1w Spearman ICs "
                     f"(inventory {ic_1w:+.3f}, momentum {mom_ic:+.3f})",
            "note": "Apply via config/weights.json — reviewed change, not "
                    "automatic (human oversight invariant).",
        }

        for sig in (inventory, momentum):
            v = sig.get("verdict_1w")
            if v == "not predictive":
                warnings.append(
                    f"{sig['signal']}: no 1-week predictive power in this "
                    f"sample — consider reducing its composite weight."
                )

        data = {
            "as_of": date.today().isoformat(),
            "sample": {
                "weeks": n,
                "from": str(panel["period"].min().date()),
                "to": str(panel["period"].max().date()),
            },
            "signals": [inventory, momentum],
            "drift": drift,
            "weight_recommendation": weight_recommendation,
            "not_backtestable_yet": [
                "geopolitical_risk (event registry starts 2026)",
                "supply_chain_stress / market_sentiment / macro "
                "(historical artifact series not yet accumulated)",
            ],
            "methodology": "event-study on weekly panel; Spearman IC; "
                           "forward returns measured from first tradable "
                           "close on/after each report week (no lookahead)",
        }
        evidence = [
            f"data/feature_panel.csv: {n} weeks "
            f"{data['sample']['from']} … {data['sample']['to']}",
        ]
        return data, evidence


def main() -> None:
    result = BacktestingEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Backtest Report — signal validation")
    print("=" * 62)
    print(f"  Sample: {d['sample']['weeks']} weeks "
          f"({d['sample']['from']} … {d['sample']['to']})\n")
    for sig in d["signals"]:
        print(f"  {sig['signal']}")
        for k, v in sig.items():
            if k.startswith("ic_"):
                print(f"    {k}: {v:+.4f}")
        for name, b in sig["buckets"].items():
            desc = ", ".join(f"{k}={v}" for k, v in b.items())
            print(f"    {name}: {desc}")
        verdicts = {k: v for k, v in sig.items() if k.startswith("verdict")}
        print(f"    → {verdicts}\n")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/backtest_report.json")


if __name__ == "__main__":
    main()
