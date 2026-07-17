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


def send_discord_update(content: str | None = None) -> bool:
    """Post the current market read. Returns True on success."""
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL not set in .env — create a webhook in "
            "Discord (Server Settings → Integrations → Webhooks) and add it.")

    payload: dict[str, Any] = {"embeds": [build_embed()],
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


def main() -> None:
    send_discord_update()
    print("✓ Posted the current market read to Discord.")


if __name__ == "__main__":
    main()
