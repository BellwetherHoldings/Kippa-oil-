"""Tests for the live price splice (offline — no network)."""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import src.data.live_prices as live_mod


def _frames(tmp_path, monkeypatch):
    data_dir = tmp_path
    monkeypatch.setattr(live_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(live_mod, "LIVE_PATH", data_dir / "wti_live.csv")
    eia = pd.DataFrame({
        "date": pd.date_range("2026-06-01", periods=25, freq="D"),
        "close": [70.0] * 25,
    })
    eia.to_csv(data_dir / "wti_prices.csv", index=False)
    return data_dir


def test_splice_prefers_live_tail(tmp_path, monkeypatch):
    data_dir = _frames(tmp_path, monkeypatch)
    live = pd.DataFrame({
        "date": pd.date_range("2026-06-20", periods=10, freq="D"),
        "close": [80.0] * 10,
    })
    live.to_csv(data_dir / "wti_live.csv", index=False)

    df = live_mod.load_price_history()
    # EIA rows strictly before the live window, live rows after
    assert (df[df["date"] < "2026-06-20"]["source"] == "RWTC").all()
    assert (df[df["date"] >= "2026-06-20"]["source"] == "CL=F").all()
    assert df["date"].is_monotonic_increasing
    assert not df["date"].duplicated().any()
    assert df["close"].iloc[-1] == 80.0


def test_splice_falls_back_to_eia_without_live_file(tmp_path, monkeypatch):
    _frames(tmp_path, monkeypatch)
    df = live_mod.load_price_history()
    assert (df["source"] == "RWTC").all()
    assert len(df) == 25
