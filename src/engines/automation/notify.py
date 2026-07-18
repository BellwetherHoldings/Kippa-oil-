"""
Discord Notifier — pushes the platform's market read to a Discord channel.

Governed by docs/013_Automation.md (notification system). Reads the
published artifacts and posts a rich embed via a Discord webhook. The
webhook URL is a secret: it lives in .env (DISCORD_WEBHOOK_URL), is never
logged, and errors are sanitized so it cannot leak into tracebacks.

Setup:
    Discord → Server Settings → Integrations → Webhooks → New Webhook
    → pick the channel → Copy Webhook URL → add to .env:
        DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

Usage:
    python src/engines/automation/notify.py          (send one update now)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import DATA_DIR, load_artifact

load_dotenv(_REPO_ROOT / ".env")

COLOR = {"bullish": 0x2ECC71, "strong bullish": 0x1E8449,
         "neutral": 0x95A5A6,
         "bearish": 0xE74C3C, "strong bearish": 0x922B21}


def build_embed() -> dict[str, Any]:
    """Discord embed from the latest published artifacts."""
    comp = load_artifact("composite_signal", require_success=True)
    if comp is None:
        raise RuntimeError("No composite artifact — run the pipeline first.")
    cd = comp["data"]

    risk = load_artifact("risk_assessment", require_success=True)
    conf = load_artifact("signal_confidence", require_success=True)
    strat = load_artifact("strategy_recommendation", require_success=True)
    momo = load_artifact("price_momentum", require_success=True)
    geo = load_artifact("geopolitical_risk", require_success=True)

    fields = []
    if momo:
        m = momo["data"]
        fields.append({"name": "WTI (live)",
                       "value": f"${m['last_close']:.2f}  "
                                f"(5d {m['return_5d']:+.1%})",
                       "inline": True})
    if conf:
        c = conf["data"]
        fields.append({"name": "Confidence",
                       "value": f"{c['confidence_score']}/100 "
                                f"({c['confidence_tier']}) → "
                                f"{c['interpretation']}",
                       "inline": True})
    if risk:
        r = risk["data"]
        fields.append({"name": "Risk",
                       "value": f"{r['overall_risk_score']}/100 "
                                f"({r['overall_risk_level']}, "
                                f"top: {r['top_risk']})",
                       "inline": True})
    if strat:
        s = strat["data"]["stance"]
        fields.append({"name": "Strategy",
                       "value": f"**{s['direction'].upper()}** @ "
                                f"{s['suggested_size_0_1']:.0%} size, "
                                f"{s['horizon_days']}d horizon",
                       "inline": True})
    if geo:
        g = geo["data"]
        fields.append({"name": "Geopolitical",
                       "value": f"{g['risk_score']}/100 ({g['risk_level']}) — "
                                f"{', '.join(g['chokepoints_disrupted']) or 'no chokepoints hit'}",
                       "inline": True})

    top = sorted(cd["components"], key=lambda c: c["effective_weight"],
                 reverse=True)[:3]
    fields.append({
        "name": "Top signal drivers",
        "value": "\n".join(f"• {c['component']}: {c['signal']:+.2f} "
                           f"({c['as_of']})" for c in top),
        "inline": False,
    })

    return {
        "title": f"Kippa Oil Intelligence — {cd['label'].upper()} "
                 f"{cd['composite_score']:+.2f}",
        "color": COLOR.get(cd["label"], 0x95A5A6),
        "fields": fields,
        "footer": {"text": "22-engine platform · recommendation only, "
                           "not financial advice"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_daytrade_embed() -> dict[str, Any] | None:
    """Compact Day-Trade Radar embed; None if no radar artifact."""
    radar = load_artifact("intraday_radar", require_success=True)
    if radar is None:
        return None
    d = radar["data"]
    nc, ev, lv = d["next_candle"], d["edge_verdict"], d["levels"]
    lean_txt = ("no lean — levels only" if nc["lean"] == "none" else
                f"{nc['lean'].upper()}  P(up) {nc['p_up']:.0%} "
                f"(n={nc['state_sample']})")
    edge_txt = (f"hit rate {ev['in_sample_hit_rate']:.0%} over "
                f"{ev['predictions_scored']} predictions — "
                + ("edge measurable" if ev["edge"] == "measurable"
                   else "coin flip; don't trade the lean"))
    return {
        "title": "Day-Trade Radar — CL=F 30m",
        "color": 0xF1C40F,
        "fields": [
            {"name": "Next candle",
             "value": f"{lean_txt}\n_{edge_txt}_", "inline": False},
            {"name": "Levels",
             "value": f"px **{lv['last_price']}** | VWAP {lv['session_vwap']} "
                      f"({lv['vs_vwap']:+}) \nH {lv['session_high']} / "
                      f"L {lv['session_low']} | ATR30 {lv['atr_30m']}",
             "inline": True},
            {"name": "Price bands (80% inside p10–p90)",
             "value": f"30m: {d['price_bands']['next_30m']['p10']} – "
                      f"{d['price_bands']['next_30m']['p90']}\n"
                      f"2h: {d['price_bands']['next_2h']['p10']} – "
                      f"{d['price_bands']['next_2h']['p90']}",
             "inline": True},
            {"name": f"Trade plans ({d['daily_bias']} bias)",
             "value": "\n".join(
                 f"**{t['name']}** [{t['side'].upper()}] "
                 f"E {t['entry']} / S {t['stop']} / T {t['target']} "
                 f"(R:R {t['risk_reward']})"
                 for t in d["trade_plans"]),
             "inline": False},
            {"name": "Sleeve", "value": d["sleeve_guidance"], "inline": False},
        ],
        "footer": {"text": "Measured stats, not predictions · core position "
                           "unchanged · not financial advice"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_pnl_embed() -> dict[str, Any] | None:
    """Track-record embed from the published pnl_summary artifact."""
    pnl = load_artifact("pnl_summary", require_success=True)
    if pnl is None:
        return None
    d = pnl["data"]
    rec = d["record_closed"]
    wr = (f" ({d['win_rate_closed']:.0%})"
          if d.get("win_rate_closed") is not None else "")
    lines = []
    for r in d["trades"]:
        if r["status"] != "open":
            continue
        usd = f" (${r['pnl_usd']:+,.0f})" if r["pnl_usd"] is not None else ""
        lines.append(f"{r['side'].upper()} @ ${r['entry_price']:.2f} → "
                     f"${r['mark_or_exit']:.2f}  **{r['pnl_pct']:+.2%}**{usd}")
    fields = [
        {"name": "Record (closed)", "value": f"{rec}{wr}", "inline": True},
        {"name": "Open",
         "value": f"{d['open_count']} ({d['open_winners']} green / "
                  f"{d['open_losers']} red)", "inline": True},
        {"name": "Mark",
         "value": f"${d['as_of_mark']:.2f} ({d['mark_date']})", "inline": True},
    ]
    if lines:
        fields.append({"name": "Open positions",
                       "value": "\n".join(lines), "inline": False})
    return {
        "title": f"Track Record — {d['account_mode'].upper()}"
                 + (f" · testing to {d['testing_until']}"
                    if d.get("testing_until") else ""),
        "color": 0x2ECC71 if d["open_losers"] == 0 else 0xE67E22,
        "fields": fields,
        "footer": {"text": "Paper track record · marks to the platform WTI "
                           "feed · not financial advice"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def send_discord_update(content: str | None = None) -> bool:
    """Post the current market read. Returns True on success."""
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL not set in .env — create a webhook in "
            "Discord (Server Settings → Integrations → Webhooks) and add it.")

    import json as _json
    embeds = [build_embed()]
    mode_path = _REPO_ROOT / "config" / "daytrade.json"
    if mode_path.exists() and _json.loads(mode_path.read_text()).get("enabled"):
        dt = build_daytrade_embed()
        if dt:
            embeds.append(dt)
    pnl = build_pnl_embed()
    if pnl:
        embeds.append(pnl)
    payload: dict[str, Any] = {"embeds": embeds,
                               "username": "Kippa Oil Intelligence"}
    if content:
        payload["content"] = content

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(exc.response, "status_code", "n/a")
        raise RuntimeError(
            f"Discord webhook post failed (status: {status})."
        ) from None  # never propagate the URL-bearing exception
    return True


def send_discord_report(report_path: str | Path,
                        content: str | None = None) -> bool:
    """Upload a report file (markdown) to Discord as an attachment.

    Used to push the daily / weekly / weekend reports in full, since they
    are far larger than an embed's field limits. Returns True on success.
    """
    import json as _json

    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError("DISCORD_WEBHOOK_URL not set in .env.")
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"report not found: {path}")

    payload = {"username": "Kippa Oil Intelligence",
               "content": content or f"📄 {path.name}"}
    try:
        with path.open("rb") as fh:
            resp = requests.post(
                url,
                data={"payload_json": _json.dumps(payload)},
                files={"files[0]": (path.name, fh, "text/markdown")},
                timeout=30,
            )
        resp.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(exc.response, "status_code", "n/a")
        raise RuntimeError(
            f"Discord report upload failed (status: {status})."
        ) from None
    return True


def main() -> None:
    send_discord_update()
    print("✓ Posted the current market read to Discord.")


if __name__ == "__main__":
    main()
