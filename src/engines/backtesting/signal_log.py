"""
Signal Log — full component-vector capture, one row per observation.

Governed by docs/010_Backtesting.md. The watch loop records only the
composite scalar and its label; every component signal, weight, freshness
confidence and damping factor behind that number is discarded at the end
of the cycle. That is the platform's largest silent data loss: the
composite is a weighted sum of seven inputs, and keeping only the sum
makes it impossible to ask afterwards WHICH input was carrying a call.

This module appends the whole vector to data/signal_log.jsonl so the
question can be answered later. It is append-only and never rewrites
history (invariant: historical information must remain preserved).

Rows are training data, not decisions. Nothing here feeds the live
composite — doc 010 invariant 6 keeps backtesting independent from
production decision-making.

Usage:
    python src/cli/oil.py signal log          # capture current read
    python src/cli/oil.py signal logstats     # summarise what's captured
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, load_artifact

SIGNAL_LOG = DATA_DIR / "signal_log.jsonl"

SCHEMA_VERSION = 2


def _artifact(name: str) -> dict[str, Any] | None:
    """Load a published artifact's data payload, or None if unavailable."""
    art = load_artifact(name, require_success=True)
    return art["data"] if art else None


def capture(trigger: str = "manual") -> dict[str, Any] | None:
    """Snapshot the current signal stack into one log row.

    Returns the row, or None when the composite is unavailable (there is
    nothing meaningful to record without it). Never raises on a missing
    optional artifact — a partial row with explicit nulls is more useful
    than no row, and the null itself is data about platform health.
    """
    comp = _artifact("composite_signal")
    if not comp:
        return None

    now = datetime.now(timezone.utc)

    # -- the vector: every component, not just the sum --------------------
    components = {}
    for c in comp.get("components", []):
        components[c["component"]] = {
            "signal": c.get("signal"),
            "confidence": c.get("confidence"),
            "weight": c.get("weight"),
            "effective_weight": c.get("effective_weight"),
            "as_of": c.get("as_of"),
            "staleness_days": c.get("staleness_days"),
        }

    # -- context that makes a row interpretable years from now ------------
    geo = _artifact("geopolitical_risk") or {}
    risk = _artifact("risk_assessment") or {}
    conf = _artifact("confidence_assessment") or {}
    strat = _artifact("strategy_recommendation") or {}
    fc = _artifact("price_forecast") or {}
    supply = _artifact("supply_chain_stress") or {}

    # damping factors are the platform's most opinionated transform;
    # log them explicitly so a later replay can strip or vary them
    damping = {}
    for cp, cond in (geo.get("realized_conditions") or {}).items():
        damping[cp] = {
            "capacity_percent": cond.get("capacity_percent"),
            "capacity_factor": cond.get("capacity_factor"),
            "inventory_factor": cond.get("inventory_factor"),
            "damped_to": cond.get("damped_to"),
        }

    row = {
        "schema": SCHEMA_VERSION,
        "at": now.isoformat(timespec="seconds"),
        "trigger": trigger,
        "composite": comp.get("composite_score"),
        "label": comp.get("label"),
        "weights_source": comp.get("weights_source"),
        "components": components,
        "geo_risk": geo.get("risk_score"),
        "geo_chokepoints_disrupted": geo.get("chokepoints_disrupted"),
        "geo_event_contributions": {
            e["id"]: e.get("contribution")
            for e in (geo.get("contributing_events") or [])
        },
        "damping": damping,
        "supply_stress": supply.get("stress_score"),
        "risk_score": risk.get("risk_score"),
        "confidence_score": conf.get("confidence_score"),
        "confidence_tier": conf.get("tier"),
        "strategy_stance": strat.get("stance"),
        "strategy_size_pct": strat.get("size_pct"),
        "strategy_conviction": strat.get("conviction"),
        "forecast_4w": fc.get("point"),
        "wti": fc.get("anchor_price"),
    }

    SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SIGNAL_LOG.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def load_rows() -> list[dict[str, Any]]:
    """Read the log. Tolerates partial final lines from a killed process."""
    if not SIGNAL_LOG.exists():
        return []
    rows = []
    for line in SIGNAL_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # truncated tail — skip, don't lose the file
    return rows


def stats() -> dict[str, Any]:
    """What has actually been captured, so coverage gaps are visible."""
    rows = load_rows()
    if not rows:
        return {"rows": 0, "note": "signal log is empty"}

    comps: dict[str, int] = {}
    for r in rows:
        for name in (r.get("components") or {}):
            comps[name] = comps.get(name, 0) + 1

    scores = [r["composite"] for r in rows if r.get("composite") is not None]
    labels: dict[str, int] = {}
    for r in rows:
        lab = r.get("label")
        if lab:
            labels[lab] = labels.get(lab, 0) + 1

    return {
        "rows": len(rows),
        "first": rows[0].get("at"),
        "last": rows[-1].get("at"),
        "composite_min": round(min(scores), 3) if scores else None,
        "composite_max": round(max(scores), 3) if scores else None,
        "label_counts": labels,
        "component_coverage": comps,
        "distinct_days": len({(r.get("at") or "")[:10] for r in rows}),
    }


def main() -> None:
    row = capture(trigger="cli")
    if row is None:
        print("composite unavailable — nothing logged", file=sys.stderr)
        raise SystemExit(1)
    print(f"logged {row['at']}  composite {row['composite']:+.3f} "
          f"({row['label']})  {len(row['components'])} components")


if __name__ == "__main__":
    main()
