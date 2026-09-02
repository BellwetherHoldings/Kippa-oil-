"""
Chokepoint Status — realized shipping conditions, not political rhetoric.

Part of the Geopolitical Intelligence module (docs/019). This exists to fix
a specific, observed failure: the geo engine scored EVENT SEVERITY (how
alarming the headlines are) with no measure of REALIZED SUPPLY IMPACT (how
many barrels actually stopped moving). Through July 2026 that kept the
Hormuz risk score pinned at 100/100 CRITICAL while ~8.5M bbl/day still
transited and US crude inventories BUILT +2.0M bbl — a supply "crisis" that
wasn't reaching the tanks. The composite stayed bullish into the top.

The fix: classify each chokepoint's actual operating condition — capacity,
commercial traffic, insurance/military risk — and use it to damp the event
score toward what is physically happening.

Design note (deliberate): this module does NOT call a language model. The
platform's engines are deterministic and auditable (docs/001 invariant:
models must remain explainable). An analyst — human or an LLM turn given
the schema in ANALYST_PROMPT — reads an article and produces a status JSON;
this module VALIDATES it, stamps it with its source, and stores it in the
registry. Ingested judgement is labelled as such and always carries its
source URL and observation date.

Usage:
    oil geo strait                 show the current chokepoint status board
    oil geo strait <file.json>     ingest a classification (validated)

Reads/writes the 'chokepoint_status' block of events.json (one registry,
append-only history — invariant #6: historical information is preserved).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).resolve().parent / "events.json"

# --- schema -----------------------------------------------------------------

STATUS_LEVELS = ("closed", "partially_open", "mostly_open", "fully_open")
CHANGE_LEVELS = ("more_restrictive", "no_change", "more_open")
SHIPPING_LEVELS = ("none", "limited", "moderate", "normal")
RISK_LEVELS = ("low", "medium", "high", "extreme")
IMPACT_LEVELS = ("bullish", "neutral", "bearish")

REQUIRED_FIELDS = {
    "strait_status": STATUS_LEVELS,
    "change_from_previous": CHANGE_LEVELS,
    "commercial_shipping": SHIPPING_LEVELS,
    "military_risk": RISK_LEVELS,
    "insurance_risk": RISK_LEVELS,
    "expected_supply_impact": IMPACT_LEVELS,
}

# Typical capacity band implied by each status label. Used only to sanity
# check the analyst's numeric estimate — a "fully_open" claim paired with
# 10% capacity is a contradiction worth surfacing, not silently averaging.
STATUS_CAPACITY_BAND = {
    "closed": (0, 20),
    "partially_open": (10, 60),
    "mostly_open": (50, 90),
    "fully_open": (85, 100),
}

ANALYST_PROMPT = """You are an oil market intelligence analyst.
Analyze the article and return ONLY a JSON object with these keys:
  strait_status: closed | partially_open | mostly_open | fully_open
  change_from_previous: more_restrictive | no_change | more_open
  estimated_shipping_capacity_percent: 0-100
  commercial_shipping: none | limited | moderate | normal
  military_risk: low | medium | high | extreme
  insurance_risk: low | medium | high | extreme
  expected_supply_impact: bullish | neutral | bearish
  confidence: 0.0-1.0
  summary: one or two sentences
Rules: focus ONLY on actual shipping conditions, not political rhetoric.
Technically open but many ships avoiding it => partially_open. Improving
versus previous reports => more_open. Base conclusions only on the article."""


class StatusValidationError(ValueError):
    """Raised when a classification does not satisfy the schema."""


def validate_status(obj: dict[str, Any]) -> list[str]:
    """Validate a classification. Returns warnings; raises on hard errors."""
    warnings: list[str] = []

    for field, allowed in REQUIRED_FIELDS.items():
        if field not in obj:
            raise StatusValidationError(f"missing required field '{field}'")
        if obj[field] not in allowed:
            raise StatusValidationError(
                f"{field}={obj[field]!r} not in {allowed}")

    cap = obj.get("estimated_shipping_capacity_percent")
    if not isinstance(cap, (int, float)) or not 0 <= cap <= 100:
        raise StatusValidationError(
            f"estimated_shipping_capacity_percent must be 0-100, got {cap!r}")

    conf = obj.get("confidence", 1.0)
    if not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
        raise StatusValidationError(f"confidence must be 0.0-1.0, got {conf!r}")

    # evidence invariant: a scored observation must be traceable
    if not obj.get("sources"):
        raise StatusValidationError(
            "classification needs at least one source URL (invariant: no "
            "unverified data may drive a score)")

    lo, hi = STATUS_CAPACITY_BAND[obj["strait_status"]]
    if not lo <= cap <= hi:
        warnings.append(
            f"capacity {cap}% sits outside the {lo}-{hi}% band implied by "
            f"status '{obj['strait_status']}' — check the classification.")

    return warnings


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def observations(chokepoint: str = "strait_of_hormuz",
                 path: Path = REGISTRY_PATH) -> list[dict[str, Any]]:
    """All stored observations for a chokepoint, oldest first."""
    reg = load_registry(path)
    obs = [o for o in reg.get("chokepoint_status", [])
           if o.get("chokepoint") == chokepoint]
    return sorted(obs, key=lambda o: o["observed_date"])


def latest_status(chokepoint: str = "strait_of_hormuz",
                  path: Path = REGISTRY_PATH) -> dict[str, Any] | None:
    """Most recent observation for a chokepoint, or None."""
    obs = observations(chokepoint, path)
    return obs[-1] if obs else None


def ingest(obj: dict[str, Any], chokepoint: str = "strait_of_hormuz",
           observed_date: str | None = None,
           path: Path = REGISTRY_PATH) -> list[str]:
    """Validate and append a classification to the registry. Returns warnings."""
    warnings = validate_status(obj)
    record = dict(obj)
    record["chokepoint"] = chokepoint
    record["observed_date"] = observed_date or date.today().isoformat()
    record["ingested_via"] = "analyst_classification"

    reg = load_registry(path)
    reg.setdefault("chokepoint_status", []).append(record)
    path.write_text(json.dumps(reg, indent=2) + "\n")
    return warnings


# --- the actual fix: realized-impact damping --------------------------------

# A chokepoint event's claimed supply impact is damped toward what is
# physically happening. Two independent checks, deliberately kept separate
# so the board can show WHICH one is doing the work.

MAX_INVENTORY_DISCOUNT = 0.5     # inventories alone can halve a claim, no more
INVENTORY_Z_SENSITIVITY = 0.2    # per 1.0 of bearish surprise z


def capacity_factor(status: dict[str, Any] | None) -> float:
    """Fraction of the claimed disruption that shipping data supports.

    100% capacity => 0.0 (nothing is actually blocked).
    0% capacity   => 1.0 (fully blocked, claim stands).
    Blended with the observation's own confidence, so a low-confidence read
    moves the score less than a well-evidenced one.
    """
    if status is None:
        return 1.0                      # no data: leave the claim untouched
    cap = float(status["estimated_shipping_capacity_percent"])
    disruption = 1.0 - (cap / 100.0)
    conf = float(status.get("confidence", 1.0))
    # confidence-weighted: pull toward 1.0 (undamped) when we're unsure
    return disruption * conf + 1.0 * (1.0 - conf)


def inventory_factor(surprise_z: float | None) -> float:
    """Reality check: are the barrels actually going missing?

    A positive surprise z means MORE crude arrived than the seasonal model
    expected — i.e. a supply crisis that is not reaching the tanks. That is
    hard evidence against the claimed disruption, so it discounts the score.
    Draws (negative z) do not inflate it; they simply leave it alone.
    """
    if surprise_z is None or surprise_z <= 0:
        return 1.0
    discount = min(MAX_INVENTORY_DISCOUNT,
                   surprise_z * INVENTORY_Z_SENSITIVITY)
    return 1.0 - discount


def realized_impact_factor(
    status: dict[str, Any] | None, surprise_z: float | None
) -> tuple[float, dict[str, Any]]:
    """Combined damping factor plus an explainability breakdown."""
    cap_f = capacity_factor(status)
    inv_f = inventory_factor(surprise_z)
    factor = cap_f * inv_f
    detail = {
        "capacity_factor": round(cap_f, 3),
        "inventory_factor": round(inv_f, 3),
        "combined_factor": round(factor, 3),
        "capacity_percent": (
            status["estimated_shipping_capacity_percent"] if status else None),
        "status_label": status["strait_status"] if status else None,
        "change_from_previous": (
            status.get("change_from_previous") if status else None),
        "observed_date": status.get("observed_date") if status else None,
        "inventory_surprise_z": surprise_z,
        "basis": ("realized shipping capacity + inventory reality check"
                  if status else "no status observation — claim undamped"),
    }
    return factor, detail


def describe(chokepoint: str = "strait_of_hormuz") -> str:
    """Human-readable status board for the CLI."""
    obs = observations(chokepoint)
    if not obs:
        return (f"No status observations for {chokepoint}.\n"
                f"Ingest one with: oil geo strait <file.json>")
    lines = [f"Chokepoint Status — {chokepoint}",
             "=" * 60,
             f"  {len(obs)} observation(s) on file", ""]
    for o in obs[-6:]:
        lines.append(
            f"  {o['observed_date']}  {o['strait_status']:<15} "
            f"cap {o['estimated_shipping_capacity_percent']:>3.0f}%  "
            f"shipping {o['commercial_shipping']:<8} "
            f"({o['change_from_previous']}, conf {o.get('confidence', 1):.2f})")
        lines.append(f"      {o.get('summary', '')[:110]}")
    latest = obs[-1]
    cap_f = capacity_factor(latest)
    lines += ["",
              f"  Latest capacity factor: {cap_f:.3f}  "
              f"(1.0 = claim undamped, 0.0 = nothing actually blocked)",
              f"  Military risk {latest['military_risk']} · "
              f"insurance {latest['insurance_risk']} · "
              f"supply impact {latest['expected_supply_impact']}"]
    return "\n".join(lines)
