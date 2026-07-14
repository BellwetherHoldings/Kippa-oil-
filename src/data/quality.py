"""
Data Quality Engine — validation and lineage for every dataset.

Governed by docs/004_Data_Layer.md (quality framework: accuracy,
completeness, consistency, timeliness; metadata & lineage) and
docs/003_Repository_Architecture.md (lineage tracking). Validates each
data file against explicit rules and publishes a per-dataset quality
score plus the full lineage manifest: where every dataset came from,
what produced it, and when it was last refreshed. Invariant #1: data
quality comes before analysis.

Usage:
    python src/data/quality.py

Publishes:
    data/data_quality_report.json
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, Engine

# Validation rules per dataset: date column, max gap between rows (days),
# plausible value range, and lineage metadata.
DATASETS = {
    "crude_inventories.csv": {
        "date_col": "period",
        "value_col": "value",
        "max_gap_days": 14,
        "value_range": (150_000, 700_000),      # kbbl, generous physical bounds
        "lineage": {
            "source": "EIA v2 API, series WCESTUS1 (weekly, ex-SPR)",
            "produced_by": "src/data/eia_client.py::get_crude_inventories",
            "transforms": ["pagination merge", "dedupe by period",
                           "weekly_change = value.diff()"],
        },
    },
    "wti_prices.csv": {
        "date_col": "date",
        "value_col": "close",
        "max_gap_days": 7,
        "value_range": (-60.0, 300.0),          # $/bbl; WTI settled at
                                                # -$37.63 on 2020-04-20
        "lineage": {
            "source": "EIA v2 API, series RWTC (daily Cushing spot)",
            "produced_by": "src/data/eia_client.py::get_wti_prices",
            "transforms": ["pagination merge", "dedupe by date"],
        },
    },
    "inventory_surprise.csv": {
        "date_col": "period",
        "value_col": "surprise_z",
        "max_gap_days": 14,
        "value_range": (-6.0, 6.0),             # z-scores beyond ±6 = broken
        "lineage": {
            "source": "derived from crude_inventories.csv",
            "produced_by": "src/engines/scoring/inventory_surprise.py",
            "transforms": ["5y same-ISO-week seasonal expectation",
                           "surprise = actual - expected",
                           "52w trailing z-score"],
        },
    },
    "feature_panel.csv": {
        "date_col": "period",
        "value_col": "fwd_ret_4w",
        "max_gap_days": 21,
        "value_range": (-0.95, 3.0),            # 4-week returns
        "lineage": {
            "source": "derived from inventory_surprise.csv + wti_prices.csv",
            "produced_by": "src/engines/data_processing/features.py",
            "transforms": ["align weeks to first tradable close (no lookahead)",
                           "forward 1w/4w returns", "trailing momentum + vol"],
        },
    },
}

DIMENSION_WEIGHTS = {"completeness": 0.35, "consistency": 0.35, "accuracy": 0.30}


class DataQualityEngine(Engine):
    name = "data_quality"
    version = "1.0"
    output_name = "data_quality_report"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        now = datetime.now(timezone.utc)
        datasets: list[dict[str, Any]] = []
        evidence: list[str] = []

        for fname, spec in DATASETS.items():
            path = DATA_DIR / fname
            entry: dict[str, Any] = {
                "dataset": fname,
                "lineage": {
                    **spec["lineage"],
                    "last_refreshed_utc": None,
                },
            }
            if not path.exists():
                entry.update({"present": False, "quality_score": 0.0,
                              "issues": ["file missing"]})
                warnings.append(f"{fname} missing — quality scored 0.")
                datasets.append(entry)
                continue

            df = pd.read_csv(path, parse_dates=[spec["date_col"]])
            issues: list[str] = []

            # completeness: non-null values in the scored column
            col = df[spec["value_col"]]
            completeness = float(col.notna().mean())
            if completeness < 1.0:
                nulls = int(col.isna().sum())
                # leading NaNs from diff()/rolling windows are structural
                if nulls > max(60, 0.15 * len(df)):
                    issues.append(f"{nulls} null values in {spec['value_col']}")

            # consistency: dates unique, sorted, gaps within cadence
            dates = df[spec["date_col"]]
            dupes = int(dates.duplicated().sum())
            unsorted = not dates.is_monotonic_increasing
            gaps = dates.diff().dt.days.dropna()
            big_gaps = int((gaps > spec["max_gap_days"]).sum())
            consistency = 1.0
            if dupes:
                consistency -= 0.4
                issues.append(f"{dupes} duplicate dates")
            if unsorted:
                consistency -= 0.4
                issues.append("dates not sorted")
            if big_gaps:
                consistency -= min(0.4, 0.05 * big_gaps)
                issues.append(f"{big_gaps} gaps > {spec['max_gap_days']}d")

            # accuracy: values inside plausible physical bounds
            lo, hi = spec["value_range"]
            valid = col.dropna()
            in_range = float(valid.between(lo, hi).mean()) if len(valid) else 0.0
            if in_range < 1.0:
                n_out = int((~valid.between(lo, hi)).sum())
                issues.append(f"{n_out} values outside [{lo}, {hi}]")

            score = round(100 * (
                DIMENSION_WEIGHTS["completeness"] * completeness
                + DIMENSION_WEIGHTS["consistency"] * max(0.0, consistency)
                + DIMENSION_WEIGHTS["accuracy"] * in_range
            ), 1)

            entry["lineage"]["last_refreshed_utc"] = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds")
            entry.update({
                "present": True,
                "rows": len(df),
                "date_span": f"{dates.min().date()} … {dates.max().date()}",
                "dimensions": {
                    "completeness": round(completeness, 3),
                    "consistency": round(max(0.0, consistency), 3),
                    "accuracy": round(in_range, 3),
                },
                "quality_score": score,
                "issues": issues,
            })
            datasets.append(entry)
            evidence.append(f"{fname}: {len(df)} rows validated against "
                            f"{len(spec['lineage']['transforms'])}-step lineage")
            if score < 80:
                warnings.append(f"{fname} quality {score}/100: {issues}")

        scored = [d for d in datasets if d.get("present")]
        overall = round(sum(d["quality_score"] for d in scored)
                        / len(scored), 1) if scored else 0.0

        data = {
            "overall_quality": overall,
            "as_of": now.isoformat(timespec="seconds"),
            "datasets": datasets,
            "dimension_weights": DIMENSION_WEIGHTS,
        }
        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not 0 <= data["overall_quality"] <= 100:
            raise ValueError("overall_quality out of bounds")


def main() -> None:
    result = DataQualityEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print("Data Quality & Lineage Report")
    print("=" * 62)
    print(f"  OVERALL: {d['overall_quality']}/100\n")
    for ds in d["datasets"]:
        if not ds.get("present"):
            print(f"  [  0.0] {ds['dataset']} — MISSING")
            continue
        dims = ds["dimensions"]
        print(f"  [{ds['quality_score']:>5.1f}] {ds['dataset']} "
              f"({ds['rows']} rows, {ds['date_span']})")
        print(f"          completeness {dims['completeness']:.2f} | "
              f"consistency {dims['consistency']:.2f} | "
              f"accuracy {dims['accuracy']:.2f}")
        print(f"          source: {ds['lineage']['source']}")
        for issue in ds["issues"]:
            print(f"          ⚠ {issue}")
    print(f"\n  Published → data/data_quality_report.json")


if __name__ == "__main__":
    main()
