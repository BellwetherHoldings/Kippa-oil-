"""
Market Hours — is NYMEX WTI (CL) futures trading right now?

Governed by docs/004_Data_Layer.md (the platform's price feed is CME
Globex CL=F). CL trades nearly around the clock on weekdays but is dark
on weekends and during the daily maintenance halt, so any intraday
product (the 30-minute Discord radar) is meaningless when the tape is
frozen. This module is the single source of truth for that schedule.

CME Globex CL schedule (US Eastern):
    - Trading week opens  Sunday  6:00 PM ET
    - Trading week closes Friday  5:00 PM ET
    - Daily maintenance halt every day 5:00 PM -> 6:00 PM ET

DST is handled by zoneinfo (America/New_York). Exchange holidays are not
modeled — on a holiday the tape is simply flat, which the intraday
engine already tolerates; this guard is about the predictable weekend
and nightly closes that were spamming the feed with stale prints.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_OPEN = time(18, 0)    # Sunday open / daily reopen after the halt
_CLOSE = time(17, 0)   # Friday close / daily halt start


def is_market_open(now: datetime | None = None) -> bool:
    """True if CL futures are trading at `now` (defaults to current UTC)."""
    now = now or datetime.now(timezone.utc)
    et = now.astimezone(ET)
    dow = et.weekday()            # Mon=0 .. Sun=6
    t = et.time()

    if dow == 5:                  # Saturday — dark all day
        return False
    if dow == 6:                  # Sunday — opens at 6 PM ET
        return t >= _OPEN
    if dow == 4:                  # Friday — closes at 5 PM ET
        return t < _CLOSE
    # Mon–Thu — open except the 5–6 PM ET maintenance halt
    return not (_CLOSE <= t < _OPEN)


def next_open(now: datetime | None = None) -> datetime:
    """The next UTC instant the market opens (now if already open)."""
    now = now or datetime.now(timezone.utc)
    if is_market_open(now):
        return now
    et = now.astimezone(ET)
    # step forward in whole hours until the market is open — coarse but
    # exact enough for a human-readable "reopens in ~N hours" message
    probe = et.replace(minute=0, second=0, microsecond=0)
    for _ in range(72):           # at most a weekend + margin
        probe += timedelta(hours=1)
        if is_market_open(probe.astimezone(timezone.utc)):
            # walk back to the exact top-of-hour open boundary
            return probe.astimezone(timezone.utc)
    return now                     # unreachable in practice


def status(now: datetime | None = None) -> dict:
    """Structured open/closed status for logging and messages."""
    now = now or datetime.now(timezone.utc)
    is_open = is_market_open(now)
    out = {"open": is_open, "as_of": now.astimezone(timezone.utc)
           .isoformat(timespec="seconds")}
    if not is_open:
        nxt = next_open(now)
        out["next_open"] = nxt.isoformat(timespec="seconds")
        out["hours_to_open"] = round(
            (nxt - now).total_seconds() / 3600, 1)
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(status(), indent=2))
