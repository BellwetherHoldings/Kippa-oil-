"""
Lucid Trading account engine — one CL signal or none, sized to survive the rules.

Governed by docs/009_Strategy_System.md for the stance and docs/007_Risk_System.md
for the constraints. Rules live in config/lucid_rules.json, not in this file, so
they can be re-verified and corrected without touching logic.

WHAT THIS ENGINE IS FOR, STATED BLUNTLY BECAUSE IT MATTERS:

A prop account is lost two ways. The second is a bad entry. The FIRST, and by
far the more common, is a rule violation - breaching the trailing max loss,
tripping the daily loss limit, failing the consistency ratio on the very payout
you were trying to collect, or carrying size that cannot survive a normal
adverse excursion. This engine treats the rules as the binding constraint and
the signal as the optional extra, because that is the correct ordering of
those two risks.

THE EDGE QUESTION IS NOT SETTLED AND THIS ENGINE DOES NOT PRETEND IT IS.
The platform's own intraday ledger (data/intraday_sim.json) currently reports
1,331 simulated bar trades at roughly -$82,000 per lot, with gross P&L negative
BEFORE costs and a bootstrap p of 1.0. In-sample hit rate is 49.3% with a 95%
CI upper bound below 50%. Every mechanical intraday plan measured so far loses
money. Therefore:

  - This engine will emit NO TRADE far more often than it emits a trade.
  - When it does emit one, the size is set by what the account can survive,
    not by conviction.
  - It refuses outright in the configurations where the measured record is
    worst (no daily bias, counter-trend).

Sizing is the part that is trustworthy today. The signal is the part that is
being forward-tested. Those two claims are different and are kept separate in
the output.

Usage:
    python src/cli/oil.py lucid signal [plan] [size] [balance]
    python src/cli/oil.py lucid rules

Publishes:
    data/lucid_signal.json
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine, load_artifact

RULES_PATH = _REPO_ROOT / "config" / "lucid_rules.json"

# How much of the remaining max-loss cushion a single trade may put at risk.
# 15% means roughly seven consecutive full-stop losses before the account is
# gone, which is a survivable losing streak for a system whose measured hit
# rate is a coin flip. A larger fraction is not "more aggressive", it is a
# shorter fuse on a system with no demonstrated edge.
RISK_FRACTION_OF_CUSHION = 0.15

# A stop closer than this is noise, not a level. CL 30-minute ATR has been
# running $0.37-$0.50; a stop inside a third of an ATR gets taken out by
# ordinary chop and turns the account into a slippage pump.
MIN_STOP_ATR_MULT = 0.5

# Minimum reward-to-risk. Below this a coin-flip hit rate is a guaranteed
# loser once costs are paid.
MIN_RR = 1.5

# Slippage plus commission assumption, dollars per barrel, round turn.
# The intraday ledger measures cost drag near $80/bbl cumulative; per-trade
# this is the honest per-barrel bite.
COST_PER_BBL = 0.03


def load_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text())


def _now_et() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


class LucidAccount:
    """One Lucid account's live rule state and what it permits right now."""

    def __init__(self, rules: dict[str, Any], plan: str, size: int,
                 balance: float | None = None,
                 highest_close: float | None = None,
                 day_pnl: float = 0.0,
                 funded: bool = True,
                 cycle_day_profits: list[float] | None = None) -> None:
        if plan not in rules["plans"]:
            raise ValueError(f"Unknown plan {plan!r}; have {list(rules['plans'])}")
        sizes = rules["plans"][plan]["sizes"]
        if str(size) not in sizes:
            raise ValueError(f"Unknown size {size!r} for {plan}; have {list(sizes)}")

        self.rules = rules
        self.plan = plan
        self.size = int(size)
        self.spec = sizes[str(size)]
        self.plan_spec = rules["plans"][plan]
        self.funded = funded
        self.start = float(size)
        self.balance = float(balance) if balance is not None else self.start
        # The trail follows CLOSES, so the caller supplies the highest close.
        # Defaulting it to the start balance is the conservative assumption:
        # it never invents cushion the account has not actually earned.
        self.highest_close = float(highest_close) if highest_close is not None \
            else max(self.start, self.balance)
        self.day_pnl = float(day_pnl)
        self.cycle_day_profits = list(cycle_day_profits or [])

    # -- drawdown ---------------------------------------------------------
    @property
    def max_loss(self) -> float:
        return float(self.spec["max_loss"])

    @property
    def trail_locks_at(self) -> float:
        """Balance at which the trail stops following and pins."""
        return self.start + self.max_loss + 100.0

    @property
    def locked(self) -> bool:
        return self.highest_close >= self.trail_locks_at

    @property
    def loss_floor(self) -> float:
        """The balance at which the account is dead."""
        if self.locked:
            return self.start + 100.0
        return self.highest_close - self.max_loss

    @property
    def cushion(self) -> float:
        """Dollars between here and a blown account."""
        return self.balance - self.loss_floor

    # -- daily loss limit -------------------------------------------------
    @property
    def daily_loss_limit(self) -> float | None:
        dll = self.spec.get("daily_loss")
        return float(dll) if dll else None

    @property
    def dll_remaining(self) -> float | None:
        dll = self.daily_loss_limit
        if dll is None:
            return None
        return max(0.0, dll + min(0.0, self.day_pnl))

    # -- consistency ------------------------------------------------------
    @property
    def consistency_limit(self) -> float | None:
        key = "consistency_funded" if self.funded else "consistency_eval"
        v = self.plan_spec.get(key)
        return float(v) if v else None

    def consistency_state(self) -> dict[str, Any]:
        """Largest single day / total cycle profit, and the headroom left."""
        limit = self.consistency_limit
        profits = [p for p in self.cycle_day_profits if p > 0]
        total = sum(profits)
        largest = max(profits) if profits else 0.0
        ratio = (largest / total) if total > 0 else None
        out: dict[str, Any] = {
            "limit": limit,
            "largest_day": round(largest, 2),
            "total_cycle_profit": round(total, 2),
            "current_ratio": round(ratio, 4) if ratio is not None else None,
            "compliant": None,
            "max_additional_profit_today": None,
        }
        if limit is None:
            out["note"] = "No consistency rule on this plan/stage."
            return out
        out["compliant"] = (ratio is None) or (ratio <= limit)
        # How much MORE could today make before today becomes the day that
        # breaks the ratio? Solve (d + x) / (total + x) <= limit for x, where
        # d is today's profit so far.
        d = max(0.0, self.day_pnl)
        if limit < 1.0:
            x = (limit * total - d) / (1.0 - limit)
            out["max_additional_profit_today"] = round(max(0.0, x), 2)
        return out

    # -- position sizing --------------------------------------------------
    def max_contracts(self, stop_distance_usd_per_bbl: float) -> dict[str, Any]:
        """Largest position the rules and the cushion permit for this stop."""
        inst = self.rules["instrument"]
        bbl = inst["contract_bbl"]
        micros = inst["micros_per_mini"]
        hard_cap_minis = int(self.spec["max_minis"])

        # Risk budget is the tighter of the drawdown cushion and today's DLL.
        budget = self.cushion * RISK_FRACTION_OF_CUSHION
        binding = "drawdown cushion"
        dll_rem = self.dll_remaining
        if dll_rem is not None and dll_rem < budget:
            budget, binding = dll_rem, "daily loss limit"

        risk_per_mini = stop_distance_usd_per_bbl * bbl + COST_PER_BBL * bbl
        risk_per_micro = risk_per_mini / micros

        by_risk_minis = math.floor(budget / risk_per_mini) if risk_per_mini > 0 else 0
        minis = max(0, min(hard_cap_minis, by_risk_minis))

        # If a full mini is too much risk, express the position in micros.
        micro_only = 0
        if minis == 0 and risk_per_micro > 0:
            micro_only = max(0, min(hard_cap_minis * micros,
                                    math.floor(budget / risk_per_micro)))

        return {
            "minis": minis,
            "micros_instead": micro_only,
            "hard_cap_minis": hard_cap_minis,
            "risk_budget_usd": round(budget, 2),
            "binding_constraint": binding if minis or micro_only else
                                  f"{binding} - too small for even one micro",
            "risk_per_mini_usd": round(risk_per_mini, 2),
            "risk_per_micro_usd": round(risk_per_micro, 2),
            "capped_by_rule": minis == hard_cap_minis and by_risk_minis > hard_cap_minis,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "size": self.size,
            "stage": "funded" if self.funded else "evaluation",
            "balance": round(self.balance, 2),
            "highest_close": round(self.highest_close, 2),
            "max_loss_limit": self.max_loss,
            "trail_type": "end-of-day; follows closes only, intraday swings never breach",
            "trail_locks_at": round(self.trail_locks_at, 2),
            "trail_locked": self.locked,
            "loss_floor": round(self.loss_floor, 2),
            "cushion_usd": round(self.cushion, 2),
            "day_pnl": round(self.day_pnl, 2),
            "daily_loss_limit": self.daily_loss_limit,
            "dll_remaining": (round(self.dll_remaining, 2)
                              if self.dll_remaining is not None else None),
            "profit_target": self.spec.get("profit_target")
                             or self.spec.get("profit_target_first"),
            "consistency": self.consistency_state(),
        }


class LucidSignalEngine(Engine):
    name = "lucid_signal"
    version = "1.0"
    output_name = "lucid_signal"

    def __init__(self, plan: str = "LucidFlex", size: int = 50000,
                 balance: float | None = None, highest_close: float | None = None,
                 day_pnl: float = 0.0, funded: bool = True,
                 cycle_day_profits: list[float] | None = None) -> None:
        super().__init__()
        self.rules = load_rules()
        self.account = LucidAccount(
            self.rules, plan, size, balance, highest_close,
            day_pnl, funded, cycle_day_profits)

    def execute(self, inputs: dict[str, Any], warnings: list[str]
                ) -> tuple[dict[str, Any], list[str]]:
        acct = self.account
        snap = acct.snapshot()
        evidence: list[str] = [f"config/lucid_rules.json verified "
                               f"{self.rules['_verification']['last_verified']}"]

        radar = load_artifact("intraday_radar", require_success=True)
        if radar is None:
            raise ValueError("intraday_radar has not published; no levels to trade.")
        r = radar["data"]
        evidence.append(f"intraday_radar as of {r['as_of']}")
        lv = r["levels"]
        atr = float(lv["atr_30m"])
        px = float(lv["last_price"])

        blocks: list[str] = []

        # -- hard gates, checked before any signal is considered -----------
        if acct.cushion <= 0:
            blocks.append(f"ACCOUNT IS BREACHED. Balance {acct.balance:,.2f} is at "
                          f"or below the loss floor {acct.loss_floor:,.2f}.")
        dll_rem = acct.dll_remaining
        if dll_rem is not None and dll_rem <= 0:
            blocks.append("Daily loss limit is spent. The session is locked; "
                          "the account is not failed. Stop for the day.")

        now = _now_et()
        flat_h, flat_m = (int(x) for x in
                          self.rules["session"]["flat_by_local"].split(":"))
        mins_to_flat = (flat_h * 60 + flat_m) - (now.hour * 60 + now.minute)
        if 0 < mins_to_flat < 30:
            blocks.append(f"{mins_to_flat} minutes to the 16:45 ET flat-all. "
                          f"Too late to open; auto-close would pick the exit.")
        weekend = now.weekday() >= 5 or (now.weekday() == 4 and mins_to_flat <= 0)
        if weekend:
            blocks.append("Market closed for the weekend. Levels below are the "
                          "last session's and are not tradable now.")

        # -- the signal itself ---------------------------------------------
        plans = r.get("trade_plans") or []
        if not plans:
            blocks.append("Radar says NO TRADE: " +
                          (r.get("no_trade_reason") or "no qualifying setup."))
            plan = None
        else:
            plan = plans[0]
            stop_dist = abs(float(plan["entry"]) - float(plan["stop"]))
            if stop_dist < MIN_STOP_ATR_MULT * atr:
                blocks.append(
                    f"Stop is {stop_dist:.2f} against a 30m ATR of {atr:.2f} - "
                    f"inside {MIN_STOP_ATR_MULT}x ATR. That is noise, not a level.")
            rr = plan.get("risk_reward")
            if rr is not None and float(rr) < MIN_RR:
                blocks.append(f"R:R {rr} is below the {MIN_RR} minimum.")

        data: dict[str, Any] = {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "account": snap,
            "market": {"last_price": px, "vwap": lv["session_vwap"],
                       "session_high": lv["session_high"],
                       "session_low": lv["session_low"], "atr_30m": atr,
                       "daily_bias": r.get("daily_bias")},
            "blocks": blocks,
        }

        if blocks or plan is None:
            data["action"] = "NO TRADE"
            data["order"] = None
            data["reason"] = blocks[0] if blocks else "No qualifying setup."
        else:
            stop_dist = abs(float(plan["entry"]) - float(plan["stop"]))
            sizing = acct.max_contracts(stop_dist)
            if sizing["minis"] == 0 and sizing["micros_instead"] == 0:
                data["action"] = "NO TRADE"
                data["order"] = None
                data["reason"] = (
                    f"Position rounds to zero. Risk budget "
                    f"${sizing['risk_budget_usd']:,.2f} ({sizing['binding_constraint']}) "
                    f"will not carry one micro at a ${stop_dist:.2f} stop.")
                blocks.append(data["reason"])
            else:
                qty_desc = (f"{sizing['minis']} CL"
                            if sizing["minis"]
                            else f"{sizing['micros_instead']} MCL (micros)")
                risk = (sizing["minis"] * sizing["risk_per_mini_usd"]
                        if sizing["minis"]
                        else sizing["micros_instead"] * sizing["risk_per_micro_usd"])
                data["action"] = plan["side"].upper()
                data["order"] = {
                    "symbol": "CL" if sizing["minis"] else "MCL",
                    "side": plan["side"],
                    "quantity": sizing["minis"] or sizing["micros_instead"],
                    "quantity_desc": qty_desc,
                    "entry": plan["entry"],
                    "stop": plan["stop"],
                    "target": plan["target"],
                    "risk_reward": plan.get("risk_reward"),
                    "dollar_risk_at_stop": round(risk, 2),
                    "pct_of_cushion": round(100 * risk / acct.cushion, 1)
                                      if acct.cushion > 0 else None,
                    "setup": plan["name"],
                    "note": plan.get("note"),
                }
                data["sizing"] = sizing
                data["reason"] = (
                    f"{plan['name']} on a {r.get('daily_bias')} daily bias. "
                    f"Size set by {sizing['binding_constraint']}, not conviction.")

        # -- consistency warning, the rule that ambushes people ------------
        cons = snap["consistency"]
        if cons["limit"] is not None and cons["max_additional_profit_today"] is not None:
            cap = cons["max_additional_profit_today"]
            data["consistency_guard"] = {
                "limit_pct": round(100 * cons["limit"]),
                "max_additional_profit_today_usd": cap,
                "note": (f"Making more than ${cap:,.2f} more today pushes this day "
                         f"past {round(100*cons['limit'])}% of cycle profit and "
                         f"blocks the payout until other days catch up. "
                         f"A winning trade can cost you the withdrawal."),
            }
            if cap < 200:
                warnings.append(
                    f"CONSISTENCY: only ${cap:,.2f} of additional profit is "
                    f"available today before the {round(100*cons['limit'])}% "
                    f"ratio is breached.")

        # -- the edge disclosure, attached to every single output ----------
        sim = load_artifact("intraday_sim", require_success=True)
        if sim:
            cl = sim["data"]["candle_lean"]
            data["measured_track_record"] = {
                "source": "data/intraday_sim.json",
                "trades": cl["trades"],
                "hit_rate_net": cl["hit_rate_net"],
                "net_pnl_per_lot_usd": cl["net_pnl_per_lot_usd"],
                "gross_per_bbl": cl["gross_per_bbl"],
                "bootstrap_p": cl["bootstrap_p"],
                "read": ("Measured intraday expectancy on this platform is "
                         "NEGATIVE and gross P&L is negative before costs. "
                         "Sizing here is trustworthy; edge is not established. "
                         "Forward-test before funding."),
            }
            evidence.append("data/intraday_sim.json measured expectancy")

        if acct.cushion > 0:
            data["survival"] = {
                "full_stop_losses_to_breach": (
                    math.floor(acct.cushion / data["order"]["dollar_risk_at_stop"])
                    if data.get("order") else None),
                "cushion_usd": round(acct.cushion, 2),
            }

        return data, evidence

    def verify_output(self, data: dict[str, Any]) -> None:
        if data.get("action") not in {"NO TRADE", "LONG", "SHORT"}:
            raise ValueError(f"Bad action {data.get('action')!r}")
        if data["action"] != "NO TRADE":
            o = data["order"]
            if o["quantity"] <= 0:
                raise ValueError("Emitted a trade with non-positive quantity.")
            risk = o["dollar_risk_at_stop"]
            if risk > data["account"]["cushion_usd"]:
                raise ValueError("Risk exceeds the whole cushion — refusing.")


def _fmt(d: dict[str, Any]) -> None:
    a, m = d["account"], d["market"]
    print("Lucid CL Signal — one trade or none")
    print("=" * 64)
    print(f"  {a['plan']} {a['size']:,} ({a['stage']}) · balance ${a['balance']:,.2f}")
    print(f"  Loss floor ${a['loss_floor']:,.2f} · cushion ${a['cushion_usd']:,.2f}"
          f" · trail {'LOCKED' if a['trail_locked'] else 'trailing'}")
    if a["daily_loss_limit"]:
        print(f"  DLL ${a['daily_loss_limit']:,.0f} · remaining "
              f"${a['dll_remaining']:,.2f} · day P&L ${a['day_pnl']:,.2f}")
    else:
        print(f"  No daily loss limit on this plan · day P&L ${a['day_pnl']:,.2f}")
    print(f"  CL ${m['last_price']} · VWAP {m['vwap']} · ATR30 {m['atr_30m']}"
          f" · bias {m['daily_bias']}")
    print()
    if d["action"] == "NO TRADE":
        print("  ▶ NO TRADE")
        for b in d["blocks"] or [d["reason"]]:
            print(f"    · {b}")
    else:
        o = d["order"]
        print(f"  ▶ {d['action']} {o['quantity_desc']} — {o['setup']}")
        print(f"    entry {o['entry']} · stop {o['stop']} · target {o['target']}"
              f" · R:R {o['risk_reward']}")
        print(f"    risk at stop ${o['dollar_risk_at_stop']:,.2f} "
              f"({o['pct_of_cushion']}% of cushion)")
        s = d["survival"]
        print(f"    {s['full_stop_losses_to_breach']} full stop-outs from a "
              f"blown account")
        print(f"    {d['reason']}")
    cg = d.get("consistency_guard")
    if cg:
        print(f"\n  Consistency {cg['limit_pct']}%: at most "
              f"${cg['max_additional_profit_today_usd']:,.2f} more profit today "
              f"before the payout is blocked.")
    tr = d.get("measured_track_record")
    if tr:
        print(f"\n  ⚠ MEASURED RECORD: {tr['trades']} intraday trades, "
              f"{tr['hit_rate_net']:.1%} net hit, "
              f"${tr['net_pnl_per_lot_usd']:,.0f}/lot, p={tr['bootstrap_p']}.")
        print(f"    {tr['read']}")


def main(argv: list[str] | None = None) -> None:
    argv = list(argv or sys.argv[1:])
    plan = argv[0] if argv else "LucidFlex"
    size = int(argv[1]) if len(argv) > 1 else 50000
    balance = float(argv[2]) if len(argv) > 2 else None
    eng = LucidSignalEngine(plan=plan, size=size, balance=balance)
    res = eng.run({})
    if not res.ok:
        raise SystemExit(f"Engine failed: {res.error}")
    _fmt(res.data)
    for w in res.warnings:
        print(f"  ⚠ {w}")
    print(f"\n  Published → data/lucid_signal.json")


if __name__ == "__main__":
    main()
