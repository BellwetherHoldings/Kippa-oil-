"""
Forecast Engine — explainable 4-week WTI return forecast.

Governed by docs/017_Forecasting_System.md. v1 is a deliberately simple,
fully explainable linear model estimated on the weekly feature panel:

    fwd_ret_4w ~ surprise_z + momentum_5d + vol_20d

Every coefficient is published, the fit is reported honestly (single-digit
R² is normal for oil at this horizon), and intervals come from residual
dispersion — uncertainty is always measured (invariant #5). The
geopolitical regime is NOT in the estimation sample (registry starts
2026), so the forecast carries an explicit regime warning instead of a
silent blind spot.

Usage:
    python src/engines/forecast/engine.py

Publishes:
    data/price_forecast.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine
from src.engines.data_processing.features import build_weekly_panel

FEATURES = ["surprise_z", "momentum_5d", "vol_20d"]
HORIZONS = {          # target column → calendar days
    "fwd_ret_1w": 7,
    "fwd_ret_4w": 28,
}
PRIMARY_TARGET = "fwd_ret_4w"


class ForecastEngine(Engine):
    name = "forecast"
    version = "1.0"
    output_name = "price_forecast"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        panel = build_weekly_panel().dropna(
            subset=FEATURES + list(HORIZONS))
        n = len(panel)

        # standardize features for comparable coefficients
        X = panel[FEATURES].to_numpy(dtype=float)
        mu, sd = X.mean(axis=0), X.std(axis=0)
        Xs = (X - mu) / sd
        A = np.column_stack([np.ones(n), Xs])

        # current feature values (freshest available from published data)
        surprise = pd.read_csv(DATA_DIR / "inventory_surprise.csv",
                               parse_dates=["period"])
        latest_z = float(surprise.dropna(subset=["surprise_z"])
                         .iloc[-1]["surprise_z"])
        momo_art = json.loads((DATA_DIR / "price_momentum.json").read_text())
        m = momo_art["data"]
        current = np.array([latest_z, m["return_5d"],
                            m["volatility_20d_annualized"]])
        current_s = (current - mu) / sd
        last_close = m["last_close"]

        horizons: dict[str, dict[str, Any]] = {}
        for target, days in HORIZONS.items():
            y = panel[target].to_numpy(dtype=float)
            beta, *_ = np.linalg.lstsq(A, y, rcond=None)
            resid = y - A @ beta
            r2 = 1.0 - float((resid ** 2).sum()) / \
                float(((y - y.mean()) ** 2).sum())
            resid_sd = float(resid.std())
            exp_ret = float(beta[0] + current_s @ beta[1:])
            horizons[target] = {
                "days": days,
                "expected_return": round(exp_ret, 4),
                "point_forecast": round(last_close * (1 + exp_ret), 2),
                "interval_68": [
                    round(last_close * (1 + exp_ret - resid_sd), 2),
                    round(last_close * (1 + exp_ret + resid_sd), 2)],
                "interval_95": [
                    round(last_close * (1 + exp_ret - 2 * resid_sd), 2),
                    round(last_close * (1 + exp_ret + 2 * resid_sd), 2)],
                "r_squared": round(r2, 4),
                "residual_sd": round(resid_sd, 4),
                "coefficients": {
                    f: round(float(b), 5)
                    for f, b in zip(FEATURES, beta[1:])
                },
                "intercept": round(float(beta[0]), 5),
            }

        primary = horizons[PRIMARY_TARGET]

        warnings.append(
            "Model estimated on 2011-2026 data WITHOUT a geopolitical "
            "feature — the active Hormuz closure regime is outside the "
            "estimation sample. Treat the point forecast as a fundamentals "
            "baseline, not a regime-aware prediction."
        )
        if m["staleness_days"] > 5:
            warnings.append(
                f"Anchor price is {m['staleness_days']}d old "
                f"(${last_close:.2f}); intervals shift with the live price."
            )

        # top-level fields mirror the primary horizon so downstream
        # consumers (strategy, API clients) keep a stable flat schema
        data = {
            "as_of": date.today().isoformat(),
            "horizon_days": primary["days"],
            "anchor_price": last_close,
            "anchor_date": m["last_date"],
            "expected_return_4w": primary["expected_return"],
            "point_forecast": primary["point_forecast"],
            "interval_68": primary["interval_68"],
            "interval_95": primary["interval_95"],
            "horizons": horizons,
            "model": {
                "type": "OLS, standardized features, per-horizon fits",
                "n_weeks": n,
                "r_squared": primary["r_squared"],
                "residual_sd_4w": primary["residual_sd"],
                "intercept": primary["intercept"],
                "coefficients": primary["coefficients"],
                "current_features": {
                    f: round(float(v), 4) for f, v in zip(FEATURES, current)
                },
            },
        }
        evidence = [
            f"feature panel {n} weeks; R²={primary['r_squared']:.3f}; "
            f"residual σ(4w)={primary['residual_sd']:.3f}",
            "data/backtest_report.json validates the inventory feature "
            "(IC +0.13 at 1w)",
        ]
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if data["point_forecast"] <= 0:
            raise ValueError("Forecast price must be positive.")
        lo, hi = data["interval_95"]
        if not lo <= data["point_forecast"] <= hi:
            raise ValueError("Point forecast outside its own 95% interval.")


def main() -> None:
    result = ForecastEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    mdl = d["model"]
    print("WTI Forecast — 4-week fundamentals baseline")
    print("=" * 60)
    print(f"  Anchor: ${d['anchor_price']:.2f} ({d['anchor_date']})")
    print(f"  Expected 4w return: {d['expected_return_4w']:+.2%}")
    print(f"  Point:  ${d['point_forecast']:.2f}")
    print(f"  68%:    ${d['interval_68'][0]:.2f} – ${d['interval_68'][1]:.2f}")
    print(f"  95%:    ${d['interval_95'][0]:.2f} – ${d['interval_95'][1]:.2f}")
    print(f"\n  Model: {mdl['type']} | n={mdl['n_weeks']} "
          f"| R²={mdl['r_squared']:.3f}")
    for f, b in mdl["coefficients"].items():
        print(f"    {f:<14} {b:+.5f} (current {mdl['current_features'][f]})")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/price_forecast.json")


if __name__ == "__main__":
    main()
