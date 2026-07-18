"""Tests for the PnL engine (marks trades to a fixed price)."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.engines.pnl import engine as pnl


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    book = {
        "account": {"mode": "paper", "testing_until": "2026-08-31"},
        "trades": [
            {"id": "long-win", "instrument": "CL", "side": "long",
             "entry_price": 80.0, "entry_date": "2026-07-15", "contracts": 2,
             "status": "open", "exit_price": None, "exit_date": None,
             "kind": "paper", "source": "discord_signal", "notes": ""},
            {"id": "short-hedge", "instrument": "CL", "side": "short",
             "entry_price": 90.0, "entry_date": "2026-07-16", "contracts": None,
             "status": "open", "exit_price": None, "exit_date": None,
             "kind": "paper", "source": "hedge", "notes": ""},
            {"id": "closed-win", "instrument": "CL", "side": "long",
             "entry_price": 70.0, "entry_date": "2026-07-01", "contracts": 1,
             "status": "closed", "exit_price": 75.0, "exit_date": "2026-07-05",
             "kind": "paper", "source": "discord_signal", "notes": ""},
        ],
    }
    (tmp_path / "trades.json").write_text(json.dumps(book))
    # mark everything to a fixed $85
    monkeypatch.setattr(pnl, "_latest_mark", lambda: (85.0, "2026-07-17"))
    return tmp_path


def test_long_and_short_pnl_signs(ledger):
    result = pnl.PnLEngine().run({"data_dir": ledger})
    assert result.ok
    rows = {r["id"]: r for r in result.data["trades"]}
    # long from 80 -> 85 is +$5/bbl, +6.25%
    assert rows["long-win"]["pnl_per_bbl"] == 5.0
    assert rows["long-win"]["pnl_pct"] == pytest.approx(0.0625, abs=1e-4)
    # 2 contracts * 1000 bbl * $5 = $10,000
    assert rows["long-win"]["pnl_usd"] == 10000.0
    # short from 90 -> 85 is a WINNER (+$5/bbl on the short)
    assert rows["short-hedge"]["pnl_per_bbl"] == 5.0
    assert rows["short-hedge"]["winner"] is True
    assert rows["short-hedge"]["pnl_usd"] is None      # no contracts set


def test_closed_trade_uses_exit_not_mark(ledger):
    result = pnl.PnLEngine().run({"data_dir": ledger})
    rows = {r["id"]: r for r in result.data["trades"]}
    # closed long 70 -> 75 (exit), NOT 70 -> 85 (mark)
    assert rows["closed-win"]["mark_or_exit"] == 75.0
    assert rows["closed-win"]["pnl_per_bbl"] == 5.0


def test_aggregates_and_record(ledger):
    d = pnl.PnLEngine().run({"data_dir": ledger}).data
    assert d["open_count"] == 2
    assert d["closed_count"] == 1
    assert d["record_closed"] == "1-0"
    assert d["win_rate_closed"] == 1.0
    assert d["open_losers"] == 0
    assert d["realized_pnl_usd"] == 5000.0      # 1 contract * 1000 * $5
    assert d["net_open_contracts"] == 2         # long 2, short 0 contracts


def test_missing_ledger_fails_cleanly(tmp_path):
    result = pnl.PnLEngine().run({"data_dir": tmp_path})
    assert not result.ok
    assert "ledger missing" in result.error
