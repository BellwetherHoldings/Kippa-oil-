"""Tests for the model-selection policy (config-driven, no LLM calls)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.engines.automation import model_policy


def test_policy_loads_and_is_internally_consistent():
    policy = model_policy.load_policy()
    tiers, jobs = policy["tiers"], policy["jobs"]
    assert jobs, "policy must define at least one job"
    # every job points at a defined tier
    for job, spec in jobs.items():
        assert spec["tier"] in tiers, f"{job} -> unknown tier {spec['tier']}"
        assert spec.get("why"), f"{job} must carry a rationale"


def test_analysis_jobs_get_the_top_model():
    # the report/EIA/audit jobs must not be silently downgraded
    for job in ("morning_report", "eia_weekly", "audit"):
        assert model_policy.model_for(job) == "claude-fable-5"


def test_keepalive_is_cheap_not_premium():
    m = model_policy.model_for("keepalive")
    assert m == "claude-haiku-4-5-20251001"
    assert m != model_policy.model_for("morning_report")


def test_pure_python_jobs_use_no_model():
    assert model_policy.model_for("engine_compute") is None
    assert model_policy.model_for("discord_watch_cycle") is None


def test_unknown_job_raises():
    with pytest.raises(KeyError):
        model_policy.model_for("does_not_exist")


def test_describe_renders_every_job():
    text = model_policy.describe()
    for job in model_policy.load_policy()["jobs"]:
        assert job in text
