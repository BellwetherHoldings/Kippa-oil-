"""Watch-loop behavior: it must go silent when the market is closed."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.data import market_hours
from src.engines.automation import notify, watch


def test_cycle_skips_and_logs_marker_when_market_closed(tmp_path, monkeypatch):
    log = tmp_path / "watch.jsonl"
    monkeypatch.setattr(watch, "WATCH_LOG", log)
    monkeypatch.setattr(market_hours, "is_market_open", lambda now=None: False)

    def _boom(*a, **k):
        raise AssertionError("Discord must not be posted when market is closed")

    monkeypatch.setattr(notify, "send_discord_update", _boom)

    watch.run_cycle(1)   # must not raise, must not post

    line = json.loads(log.read_text().strip().splitlines()[-1])
    assert line["market"] == "closed"
    assert "next_open" in line


def test_cycle_runs_and_posts_when_market_open(tmp_path, monkeypatch):
    log = tmp_path / "watch.jsonl"
    monkeypatch.setattr(watch, "WATCH_LOG", log)
    monkeypatch.setattr(market_hours, "is_market_open", lambda now=None: True)
    monkeypatch.setattr(watch, "run_workflow",
                        lambda name: {"steps_failed": 0, "steps": []})

    posted = {"n": 0}
    monkeypatch.setattr(notify, "send_discord_update",
                        lambda *a, **k: posted.__setitem__("n", posted["n"] + 1))
    # no daytrade side-trip, no real artifact needed for the composite read
    monkeypatch.setattr(watch, "_REPO_ROOT", tmp_path)  # no config/daytrade.json
    monkeypatch.setattr("src.engines.base.load_artifact",
                        lambda *a, **k: {"data": {"label": "bullish",
                                                  "composite_score": 0.4}})

    watch.run_cycle(1)

    assert posted["n"] == 1
    line = json.loads(log.read_text().strip().splitlines()[-1])
    assert line["label"] == "bullish"
