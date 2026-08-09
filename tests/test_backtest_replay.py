"""Tests for the backtesting additions: signal log, replay, sim ledger.

The load-bearing test here is `test_replay_rejects_pure_noise`. A backtest
that finds edges in randomness is worse than no backtest, because it
manufactures false confidence. The noise panel below is constructed to
LOOK profitable on the headline metrics — and the significance test must
still call it chance.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from src.engines.backtesting import replay as replay_mod
from src.engines.backtesting import sim_ledger as sim_mod
from src.engines.backtesting import signal_log as slog


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _write_panel(data_dir, *, edge: bool, weeks: int = 400, seed: int = 42):
    """Synthetic weekly panel. With edge=True the forward return is driven
    by -surprise_z; with edge=False it is pure noise."""
    rng = random.Random(seed)
    px = 50.0
    prices, zs = [], []
    d = date(2016, 1, 6)
    for _ in range(weeks):
        z = rng.gauss(0, 1)
        zs.append((d, z))
        drift = (-0.4 * z * 0.01) if edge else 0.0
        for _ in range(5):
            px *= (1 + drift / 5 + rng.gauss(0, 0.012))
            prices.append((d, round(px, 2)))
            d += timedelta(days=1)
        d += timedelta(days=2)

    with open(data_dir / "wti_prices.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "close"])
        w.writerows([[dt.isoformat(), p] for dt, p in prices])

    with open(data_dir / "inventory_surprise.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["period", "surprise_z"])
        w.writerows([[dt.isoformat(), round(z, 4)] for dt, z in zs])


@pytest.fixture
def panel_dir(tmp_path, monkeypatch):
    """Point the feature builder at a temp data dir and clear its cache."""
    from src.engines.data_processing import features
    monkeypatch.setattr(features, "DATA_DIR", tmp_path)
    monkeypatch.setattr(features, "_CACHE", None)
    return tmp_path


# --------------------------------------------------------------------------
# signal log
# --------------------------------------------------------------------------

def test_signal_log_roundtrip(tmp_path, monkeypatch):
    log = tmp_path / "signal_log.jsonl"
    monkeypatch.setattr(slog, "SIGNAL_LOG", log)

    assert slog.load_rows() == []
    assert slog.stats()["rows"] == 0

    rows = [
        {"schema": 2, "at": "2026-08-10T13:00:00+00:00", "composite": 0.31,
         "label": "bullish", "components": {"inventory_surprise": {"signal": 0.4}}},
        {"schema": 2, "at": "2026-08-10T13:30:00+00:00", "composite": -0.12,
         "label": "neutral", "components": {"inventory_surprise": {"signal": -0.1}}},
    ]
    with log.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    assert len(slog.load_rows()) == 2
    s = slog.stats()
    assert s["rows"] == 2
    assert s["composite_min"] == -0.12
    assert s["composite_max"] == 0.31
    assert s["component_coverage"]["inventory_surprise"] == 2


def test_signal_log_survives_truncated_tail(tmp_path, monkeypatch):
    """A killed process can leave half a line. That must not lose the file."""
    log = tmp_path / "signal_log.jsonl"
    monkeypatch.setattr(slog, "SIGNAL_LOG", log)
    log.write_text(
        json.dumps({"at": "2026-08-10T13:00:00+00:00", "composite": 0.2}) + "\n"
        + '{"at": "2026-08-10T13:30:00+00:00", "compos'      # truncated
    )
    assert len(slog.load_rows()) == 1


def test_capture_returns_none_without_composite(tmp_path, monkeypatch):
    monkeypatch.setattr(slog, "SIGNAL_LOG", tmp_path / "signal_log.jsonl")
    monkeypatch.setattr(slog, "_artifact", lambda name: None)
    assert slog.capture() is None


# --------------------------------------------------------------------------
# replay — the core correctness tests
# --------------------------------------------------------------------------

def test_replay_finds_a_planted_edge(panel_dir):
    _write_panel(panel_dir, edge=True)
    result = replay_mod.ReplayEngine(mode="expanding").run()
    assert result.ok, result.error
    d = result.data

    assert d["ic_spearman_1w"] > 0.08, "should recover the planted relationship"
    assert d["bootstrap_p_value"] < 0.05
    assert d["performance"]["mean_return"] > d["baselines"][0]["mean_return"]
    assert d["verdict"].startswith("EDGE PRESENT")


def test_replay_rejects_pure_noise(panel_dir):
    """The load-bearing test. Noise must not be reported as skill."""
    _write_panel(panel_dir, edge=False)
    result = replay_mod.ReplayEngine(mode="expanding").run()
    assert result.ok, result.error
    d = result.data

    assert abs(d["ic_spearman_1w"]) < 0.08
    # The verdict must NOT claim an edge, regardless of how the headline
    # hit rate or profit factor happen to land on this seed.
    assert not d["verdict"].startswith("EDGE PRESENT"), (
        f"claimed an edge on noise: {d['verdict']}"
    )


def test_replay_is_reproducible(panel_dir):
    """Invariant 1: every backtest remains reproducible."""
    _write_panel(panel_dir, edge=True)
    a = replay_mod.ReplayEngine(mode="expanding").run().data
    b = replay_mod.ReplayEngine(mode="expanding").run().data
    assert a["bootstrap_p_value"] == b["bootstrap_p_value"]
    assert a["performance"] == b["performance"]


def test_replay_always_reports_a_baseline(panel_dir):
    """Doc 010 requires baseline comparison; verify_output enforces it."""
    _write_panel(panel_dir, edge=True)
    d = replay_mod.ReplayEngine(mode="static").run().data
    labels = {b["label"] for b in d["baselines"]}
    assert "baseline_always_long" in labels
    assert "baseline_coin_flip" in labels


def test_replay_warns_it_is_a_reduced_composite(panel_dir):
    """Never let a reduced replay be mistaken for the live signal."""
    _write_panel(panel_dir, edge=True)
    result = replay_mod.ReplayEngine(mode="expanding").run()
    assert any("REDUCED COMPOSITE" in w for w in result.warnings)


def test_replay_rejects_bad_mode():
    with pytest.raises(ValueError):
        replay_mod.ReplayEngine(mode="lookahead")


# --------------------------------------------------------------------------
# sim ledger
# --------------------------------------------------------------------------

def _sim_rows(scores_prices, start=None):
    t = start or datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
    rows = []
    for score, px in scores_prices:
        rows.append({"schema": 2, "at": t.isoformat(timespec="seconds"),
                     "composite": score, "wti": px, "label": "x",
                     "components": {}, "strategy_size_pct": 8})
        t += timedelta(minutes=30)
    return rows


def test_sim_opens_and_closes_a_long(tmp_path, monkeypatch):
    rows = _sim_rows([(0.30, 80.0), (0.25, 81.0), (-0.05, 82.0)])
    monkeypatch.setattr(sim_mod, "load_rows", lambda: rows)
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")

    d = sim_mod.SimLedgerEngine(slippage=0.0, min_rows=2).run().data
    assert d["summary"]["closed"] == 1
    t = d["trades"][0]
    assert t["side"] == "long"
    assert t["exit_reason"] == "score_crossed_zero"
    assert t["move_per_bbl"] == pytest.approx(2.0)          # 80 → 82
    assert t["pnl_per_lot_usd"] == pytest.approx(2000.0)


def test_sim_shorts_on_negative_score(tmp_path, monkeypatch):
    rows = _sim_rows([(-0.30, 80.0), (0.05, 78.0)])
    monkeypatch.setattr(sim_mod, "load_rows", lambda: rows)
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")

    d = sim_mod.SimLedgerEngine(slippage=0.0, min_rows=2).run().data
    t = d["trades"][0]
    assert t["side"] == "short"
    assert t["move_per_bbl"] == pytest.approx(2.0)          # short 80 → 78
    assert t["pnl_per_lot_usd"] == pytest.approx(2000.0)


def test_sim_slippage_costs_both_ways(tmp_path, monkeypatch):
    rows = _sim_rows([(0.30, 80.0), (-0.05, 80.0)])
    monkeypatch.setattr(sim_mod, "load_rows", lambda: rows)
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")

    d = sim_mod.SimLedgerEngine(slippage=0.05, min_rows=2).run().data
    # flat price, round trip = -2 x slippage
    assert d["trades"][0]["move_per_bbl"] == pytest.approx(-0.10)


def test_sim_ignores_sub_gate_scores(tmp_path, monkeypatch):
    rows = _sim_rows([(0.10, 80.0), (0.15, 81.0), (0.05, 82.0)])
    monkeypatch.setattr(sim_mod, "load_rows", lambda: rows)
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")

    d = sim_mod.SimLedgerEngine(slippage=0.0, min_rows=2).run().data
    assert d["summary"]["closed"] == 0
    assert d["open_position"] is None


def test_sim_marks_an_open_position(tmp_path, monkeypatch):
    rows = _sim_rows([(0.30, 80.0), (0.28, 83.0), (0.31, 84.0), (0.29, 85.0)])
    monkeypatch.setattr(sim_mod, "load_rows", lambda: rows)
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")

    d = sim_mod.SimLedgerEngine(slippage=0.0, min_rows=2).run().data
    assert d["summary"]["closed"] == 0
    o = d["open_position"]
    assert o["status"] == "open"
    assert o["unrealized_per_bbl"] == pytest.approx(5.0)     # 80 → 85


def test_sim_refuses_a_thin_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sim_mod, "load_rows", lambda: _sim_rows([(0.3, 80.0)]))
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")
    result = sim_mod.SimLedgerEngine().run()
    assert not result.ok
    assert "need at least 4" in str(result.error)


def test_sim_reports_pnl_concentration(tmp_path, monkeypatch):
    """The live record is 96% one trade. The sim must surface that shape."""
    rows = _sim_rows([
        (0.30, 80.0), (-0.05, 80.1),      # +0.1
        (0.30, 80.0), (-0.05, 90.0),      # +10.0  <- dominant
        (0.30, 80.0), (-0.05, 80.1),      # +0.1
    ])
    monkeypatch.setattr(sim_mod, "load_rows", lambda: rows)
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")

    s = sim_mod.SimLedgerEngine(slippage=0.0, min_rows=2).run().data["summary"]
    assert s["closed"] == 3
    assert s["top_trade_share_of_pnl"] > 0.95


def test_sim_never_writes_the_live_ledger(tmp_path, monkeypatch):
    """Doc 010 invariant 6: research stays out of production."""
    rows = _sim_rows([(0.30, 80.0), (0.25, 81.0), (-0.05, 82.0), (0.1, 82.0)])
    monkeypatch.setattr(sim_mod, "load_rows", lambda: rows)
    monkeypatch.setattr(sim_mod, "SIM_TRADES", tmp_path / "sim_trades.json")

    from src.engines.base import DATA_DIR
    live = DATA_DIR / "trades.json"
    before = live.read_text() if live.exists() else None

    sim_mod.SimLedgerEngine(slippage=0.0, min_rows=2).run()

    after = live.read_text() if live.exists() else None
    assert after == before, "sim ledger must never touch data/trades.json"
