"""Shared test configuration.

Redirects the engine framework's run log into a temp directory so test
runs never pollute logs/engine_runs.jsonl — the monitoring and
observability engines read that file as production telemetry.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.engines.base as base


@pytest.fixture(autouse=True)
def _isolate_engine_side_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(base, "RUN_LOG", tmp_path / "logs" / "engine_runs.jsonl")
    # Engine._publish resolves DATA_DIR through the base module, so this
    # stops test engines from overwriting production artifacts in data/.
    # (Engines that READ artifacts import DATA_DIR by value into their own
    # modules — tests that need read isolation patch those separately.)
    monkeypatch.setattr(base, "DATA_DIR", tmp_path / "data")
