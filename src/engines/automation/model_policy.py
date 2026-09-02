"""
Model Policy — resolves which AI model each job should run on.

Governed by docs/013_Automation.md. The policy lives in
config/model_policy.json (version-controlled, human-readable) so model
selection is explainable and auditable, never a hidden default
(Project Vision invariant: no black-box decisions).

The analytical engines themselves use no model — they are deterministic
Python. A model is only involved in the Claude turns that operate the
platform (reports, EIA analysis, audits, keepalive). Those jobs are the
ones this policy governs.

Usage:
    from src.engines.automation.model_policy import model_for, describe
    model_for("morning_report")   -> "claude-fable-5"
    model_for("keepalive")        -> "claude-haiku-4-5-20251001"
    model_for("engine_compute")   -> None   (no LLM)

CLI:
    oil model policy          Show the full job -> model table
    oil model for <job>       Print the model id for one job
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = _REPO_ROOT / "config" / "model_policy.json"


def load_policy() -> dict:
    """Load and validate the model policy. Raises on a malformed file."""
    if not POLICY_PATH.exists():
        raise FileNotFoundError(f"Model policy missing: {POLICY_PATH}")
    policy = json.loads(POLICY_PATH.read_text())
    tiers, jobs = policy.get("tiers"), policy.get("jobs")
    if not isinstance(tiers, dict) or not isinstance(jobs, dict):
        raise ValueError("model_policy.json must define 'tiers' and 'jobs'.")
    for job, spec in jobs.items():
        tier = spec.get("tier")
        if tier not in tiers:
            raise ValueError(
                f"Job '{job}' references unknown tier '{tier}'. "
                f"Known tiers: {sorted(tiers)}.")
    return policy


def tier_for(job: str) -> str:
    """The capability tier assigned to a job."""
    jobs = load_policy()["jobs"]
    if job not in jobs:
        raise KeyError(
            f"Unknown job '{job}'. Known jobs: {sorted(jobs)}.")
    return jobs[job]["tier"]


def model_for(job: str) -> str | None:
    """The concrete model id for a job, or None if the job uses no LLM."""
    policy = load_policy()
    tier = policy["jobs"][job]["tier"] if job in policy["jobs"] else None
    if tier is None:
        raise KeyError(
            f"Unknown job '{job}'. Known jobs: {sorted(policy['jobs'])}.")
    return policy["tiers"][tier]["model"]


def describe() -> str:
    """Human-readable rendering of the full policy."""
    policy = load_policy()
    tiers, jobs = policy["tiers"], policy["jobs"]
    meta = policy.get("_schema", {})
    lines = [
        "Model Policy — which AI model runs which job",
        "=" * 60,
        f"  policy version {meta.get('policy_version', '?')} · "
        f"updated {meta.get('last_update', '?')}",
        "",
        "  Jobs:",
    ]
    width = max(len(j) for j in jobs)
    for job in sorted(jobs):
        spec = jobs[job]
        tier = spec["tier"]
        model = tiers[tier]["model"] or "— (no LLM)"
        lines.append(f"    {job:<{width}}  {tier:<9}  {model}")
        lines.append(f"    {'':<{width}}  {spec['why']}")
    lines += ["", "  Tiers:"]
    for tier in tiers:
        model = tiers[tier]["model"] or "— (no LLM)"
        lines.append(f"    {tier:<9}  {model}")
        lines.append(f"    {'':<9}  {tiers[tier]['why']}")
    lines.append("")
    lines.append("  Note: a scheduled Routine uses its job's model only when it")
    lines.append("  fires as its own fresh session. A Routine bound to an")
    lines.append("  existing session inherits that session's model.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    argv = argv or []
    if argv and argv[0] not in ("policy", None):
        # `oil model for <job>` routes here with argv = ["<job>"]
        job = argv[0]
        model = model_for(job)
        print(model if model is not None else "none (no LLM)")
        return
    print(describe())


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
