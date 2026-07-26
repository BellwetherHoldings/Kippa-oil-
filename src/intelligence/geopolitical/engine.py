"""
Geopolitical Intelligence Engine — event-driven supply risk scoring.

Governed by docs/019_Geopolitical_Intelligence.md. Reads the curated event
registry (events.json), scores each active event by severity × supply
impact × confidence with a chokepoint-exposure amplifier, and aggregates
into a 0-100 geopolitical risk score. Every score is traceable to its
events and every event to its sources.

Usage:
    python src/intelligence/geopolitical/engine.py

Publishes:
    data/geopolitical_risk.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine, classify, load_artifact
from src.intelligence.geopolitical import strait_status

EVENTS_PATH = Path(__file__).resolve().parent / "events.json"

# Share of global seaborne oil trade transiting each chokepoint. Used to
# amplify events that sit on critical flow paths (doc 019, impact scoring).
CHOKEPOINT_FLOW_SHARE = {
    "strait_of_hormuz": 0.25,
    "strait_of_malacca": 0.22,
    "suez_canal": 0.09,
    "bab_el_mandeb": 0.09,
    "danish_straits": 0.04,
    "turkish_straits": 0.03,
    "panama_canal": 0.02,
}

RISK_LEVELS = (
    (20, "low"),
    (40, "elevated"),
    (60, "high"),
    (80, "severe"),
    (101, "critical"),
)

# Only these statuses contribute to the live score; resolved events stay in
# the registry as history but score zero.
SCORING_STATUSES = {"active", "monitoring"}
MONITORING_DISCOUNT = 0.5   # monitoring events count at half weight


def _risk_level(score: float) -> str:
    return classify(score, RISK_LEVELS)


def _inventory_surprise_z() -> float | None:
    """Latest weekly inventory surprise z, or None if unavailable.

    Positive = more crude arrived than expected, i.e. evidence that a claimed
    supply disruption is not reaching the tanks.
    """
    art = load_artifact("inventory_surprise_summary", require_success=True)
    if not art:
        return None
    latest = art["data"].get("latest") or {}
    z = latest.get("surprise_z")
    return float(z) if z is not None else None


class GeopoliticalIntelligenceEngine(Engine):
    name = "geopolitical_intelligence"
    version = "1.0"
    output_name = "geopolitical_risk"

    def __init__(self, events_path: Path = EVENTS_PATH) -> None:
        self.events_path = events_path

    def validate_input(self, inputs: dict[str, Any]) -> None:
        if not self.events_path.exists():
            raise ValueError(f"Event registry not found: {self.events_path}")

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        registry = json.loads(self.events_path.read_text())
        events = registry.get("events", [])
        if not events:
            raise ValueError("Event registry is empty.")

        today = date.today()
        contributions: list[dict[str, Any]] = []
        evidence: list[str] = []
        survival = 1.0   # soft-OR aggregation: risk = 1 - Π(1 - eventScore)

        # Realized-impact inputs. A chokepoint event's CLAIMED supply impact is
        # damped toward what is physically happening: how much shipping
        # capacity actually flows, cross-checked against whether the barrels
        # are really going missing (inventory surprise). Without this the
        # score tracks rhetoric — the July 2026 failure mode where Hormuz sat
        # at 100/100 while crude inventories built.
        surprise_z = _inventory_surprise_z()
        damping: dict[str, dict[str, Any]] = {}

        for ev in events:
            if ev.get("status") not in SCORING_STATUSES:
                continue
            if not ev.get("sources"):
                warnings.append(f"Event {ev['id']} has no sources — skipped "
                                f"(unverified data must not drive scores).")
                continue

            base = (ev["severity"] / 10) * (ev["oil_supply_impact"] / 10)
            base *= float(ev.get("confidence", 1.0))
            if ev.get("status") == "monitoring":
                base *= MONITORING_DISCOUNT

            chokepoint = ev.get("chokepoint")
            amplifier = 1.0 + CHOKEPOINT_FLOW_SHARE.get(chokepoint or "", 0.0)
            claimed = min(base * amplifier, 1.0)

            # Damp chokepoint-exposed events by realized shipping conditions.
            # Non-chokepoint events (e.g. the broader conflict) are unaffected:
            # capacity data says nothing about them.
            if chokepoint:
                if chokepoint not in damping:
                    # status lives in the SAME registry the events came from —
                    # one source of truth, and testable against a temp registry
                    obs = strait_status.latest_status(
                        chokepoint, path=self.events_path)
                    factor, detail = strait_status.realized_impact_factor(
                        obs, surprise_z)
                    damping[chokepoint] = {"factor": factor, **detail}
                    if obs is None:
                        warnings.append(
                            f"No shipping-status observation for {chokepoint} "
                            f"— scoring its claimed impact undamped. Ingest "
                            f"one with: oil geo strait <file.json>")
                    else:
                        stale = (today - date.fromisoformat(
                            obs["observed_date"])).days
                        if stale > 3:
                            warnings.append(
                                f"{chokepoint} shipping status is {stale} days "
                                f"old ({obs['observed_date']}) — refresh it.")
                    if factor < 0.85:
                        warnings.append(
                            f"{chokepoint} claimed impact damped to "
                            f"{factor:.0%} of headline severity by realized "
                            f"conditions (capacity "
                            f"{detail['capacity_factor']:.2f} × inventory "
                            f"{detail['inventory_factor']:.2f}).")
                score = claimed * damping[chokepoint]["factor"]
            else:
                score = claimed

            staleness = (today - date.fromisoformat(ev["last_update"])).days
            if staleness > 7:
                warnings.append(
                    f"Event {ev['id']} last updated {staleness} days ago — "
                    f"registry may need a refresh."
                )

            survival *= 1.0 - score
            contributions.append({
                "id": ev["id"],
                "category": ev["category"],
                "status": ev["status"],
                "chokepoint": chokepoint,
                "event_score": round(score, 3),
                "claimed_score": round(claimed, 3),
                "realized_damping": round(
                    damping[chokepoint]["factor"], 3) if chokepoint else 1.0,
                "severity": ev["severity"],
                "oil_supply_impact": ev["oil_supply_impact"],
                "confidence": ev["confidence"],
                "last_update": ev["last_update"],
                "days_since_update": staleness,
                "summary": ev["description"][:200],
            })
            evidence.extend(ev["sources"])

        if not contributions:
            raise ValueError("No scoreable (active/monitoring, sourced) events.")

        risk_score = round(100 * (1.0 - survival), 1)
        contributions.sort(key=lambda c: c["event_score"], reverse=True)

        affected = sorted({
            c["chokepoint"] for c in contributions
            if c["chokepoint"] and c["status"] == "active"
        })

        data = {
            "risk_score": risk_score,
            "risk_level": _risk_level(risk_score),
            "as_of": today.isoformat(),
            "direction_for_prices": "bullish" if risk_score >= 40 else "neutral",
            "active_events": sum(1 for c in contributions if c["status"] == "active"),
            "monitoring_events": sum(1 for c in contributions if c["status"] == "monitoring"),
            "chokepoints_disrupted": affected,
            "seaborne_trade_share_at_risk": round(
                sum(CHOKEPOINT_FLOW_SHARE[c] for c in affected), 2
            ),
            "realized_impact": damping,
            "contributions": contributions,
        }
        return data, sorted(set(evidence))

    def verify_output(self, data: dict[str, Any]) -> None:
        super().verify_output(data)
        if not 0 <= data["risk_score"] <= 100:
            raise ValueError(f"risk_score out of bounds: {data['risk_score']}")


def main() -> None:
    result = GeopoliticalIntelligenceEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")

    d = result.data
    print("Geopolitical Intelligence — Risk Assessment")
    print("=" * 55)
    print(f"  Risk score:   {d['risk_score']}/100  ({d['risk_level'].upper()})")
    print(f"  Price bias:   {d['direction_for_prices']}")
    print(f"  Chokepoints disrupted: {', '.join(d['chokepoints_disrupted']) or 'none'}")
    print(f"  Seaborne trade share at risk: {d['seaborne_trade_share_at_risk']:.0%}")
    for cp, r in (d.get("realized_impact") or {}).items():
        print(f"\n  Realized conditions — {cp}:")
        print(f"    capacity {r['capacity_percent']}% ({r['status_label']}, "
              f"{r['change_from_previous']}, seen {r['observed_date']})"
              if r["capacity_percent"] is not None else
              f"    no shipping observation — claim undamped")
        print(f"    inventory surprise z {r['inventory_surprise_z']} → "
              f"factor {r['inventory_factor']}")
        print(f"    claimed impact damped to {r['combined_factor']:.0%}")
    print(f"\n  Contributing events ({len(d['contributions'])}):")
    for c in d["contributions"]:
        damp = (f"  (claimed {c['claimed_score']:.2f} × "
                f"{c['realized_damping']:.2f})"
                if c["realized_damping"] < 1.0 else "")
        print(f"    [{c['event_score']:.2f}] {c['id']} "
              f"({c['category']}, {c['status']}, updated {c['last_update']})"
              f"{damp}")
    for w in result.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/geopolitical_risk.json")


if __name__ == "__main__":
    main()
