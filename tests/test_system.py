"""Tests for the 22/22 registry and the doc-level engines added last."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import importlib

from src.engines.base import Engine
from src.engines.registry import (
    EXECUTION_ORDER,
    REGISTRY,
    EngineRegistryEngine,
)


# ---------------------------------------------------------------------------
# Registry: all 22 docs, importable, lifecycle-compliant
# ---------------------------------------------------------------------------

def test_registry_covers_all_22_docs():
    docs = [e["doc"] for e in REGISTRY]
    assert docs == [f"{i:03d}" for i in range(1, 23)]


def test_every_registered_engine_is_importable_and_compliant():
    for entry in REGISTRY:
        module = importlib.import_module(entry["module"])
        cls = getattr(module, entry["cls"])
        assert issubclass(cls, Engine), entry["cls"]
        assert cls.output_name == entry["artifact"], entry["cls"]


def test_execution_order_is_a_permutation_of_the_registry():
    assert sorted(EXECUTION_ORDER) == sorted(e["doc"] for e in REGISTRY)


def test_dependencies_precede_consumers():
    order = {doc: i for i, doc in enumerate(EXECUTION_ORDER)}
    assert order["004"] < order["006"]      # data quality before scoring
    assert order["006"] < order["007"]      # composite before risk
    assert order["007"] < order["009"]      # risk before strategy
    assert order["001"] == len(order) - 1   # invariants audit runs last


def test_registry_engine_reports_full_coverage():
    result = EngineRegistryEngine().run()
    assert result.ok
    assert result.data["docs_covered"] == "22/22"
    assert all(r["lifecycle_compliant"] for r in result.data["engines"])


# ---------------------------------------------------------------------------
# Doc-level engines (offline behaviors)
# ---------------------------------------------------------------------------

def test_repo_architecture_engine_passes_on_this_repo():
    from src.monitoring.repo_architecture import RepoArchitectureEngine
    result = RepoArchitectureEngine().run()
    assert result.ok
    assert result.data["compliant"], result.data["issues"]
    assert result.data["architecture_documents"] == "22/22"


def test_roadmap_engine_validates_statuses(tmp_path, monkeypatch):
    import src.engines.strategy.roadmap as roadmap_mod
    bad = tmp_path / "roadmap.json"
    bad.write_text('{"items": [{"id": "x", "title": "x", '
                   '"status": "someday"}]}')
    monkeypatch.setattr(roadmap_mod, "ROADMAP_PATH", bad)
    result = roadmap_mod.RoadmapEngine().run()
    assert not result.ok
    assert "Invalid statuses" in result.error


def test_roadmap_blocked_items_name_their_blocker():
    import json
    items = json.loads(
        (REPO_ROOT / "config" / "roadmap.json").read_text())["items"]
    for i in items:
        if i["status"] == "blocked":
            assert i.get("unblocked_by"), f"{i['id']} blocked without cause"


def test_cli_status_counts_real_commands():
    from src.cli.oil import COMMANDS
    from src.cli.status import CLIStatusEngine
    result = CLIStatusEngine().run()
    assert result.ok
    assert result.data["commands"] == len(COMMANDS)


def test_api_status_reads_route_table(monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEYS", "k1")
    from src.api.status import APIStatusEngine
    result = APIStatusEngine().run()
    assert result.ok
    assert result.data["resources_served"] == 12
    assert "/api/v1/health" in result.data["routes"]


def test_deployment_readiness_gates(monkeypatch):
    from src.monitoring.deployment_readiness import DeploymentReadinessEngine
    result = DeploymentReadinessEngine().run()
    assert result.ok
    gate_names = {g["gate"] for g in result.data["gates"]}
    assert {"security_audit", "data_tier_writable",
            "config/environments.json"} <= gate_names
