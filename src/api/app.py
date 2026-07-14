"""
Oil Intelligence Platform API — read access to every published artifact.

Governed by docs/022_API_System.md: versioned paths (/api/v1/), API-key
authentication on every data endpoint, standardized response envelope
(status, timestamp, request ID, payload), per-key rate limiting, and
sanitized errors. Serves the engines' published JSON artifacts — the API
layer never recomputes analytics.

Auth: X-API-Key header matching one of PLATFORM_API_KEYS (comma-separated
in .env). /api/v1/health is the only unauthenticated endpoint.

Run:
    uvicorn src.api.app:app --host 127.0.0.1 --port 8000
    (or: oil api serve)
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines import base as _base

load_dotenv(_REPO_ROOT / ".env")

RATE_LIMIT_PER_MIN = 60

# RBAC (doc 022): keys are "value" (readonly) or "value:admin" in
# PLATFORM_API_KEYS. Admin-only resources expose platform internals.
ADMIN_RESOURCES = {"monitoring"}
API_AUDIT_LOG = _REPO_ROOT / "logs" / "api_audit.jsonl"

# resource name → artifact file
RESOURCES = {
    "signal": "composite_signal",
    "risk": "risk_assessment",
    "confidence": "signal_confidence",
    "forecast": "price_forecast",
    "simulation": "simulation_results",
    "strategy": "strategy_recommendation",
    "geopolitical": "geopolitical_risk",
    "supply": "supply_chain_stress",
    "sentiment": "market_sentiment",
    "macro": "macro_conditions",
    "momentum": "price_momentum",
    "monitoring": "monitoring_report",
}

app = FastAPI(
    title="Oil Intelligence Platform API",
    version="1.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)

_rate: dict[str, list[float]] = {}


def _keys() -> dict[str, str]:
    """Parse PLATFORM_API_KEYS into {key_value: role}."""
    raw = os.getenv("PLATFORM_API_KEYS", "")
    keys: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        value, _, role = entry.partition(":")
        keys[value] = role or "readonly"
    return keys


def _audit(request_id: str, method: str, path: str, key: str,
           status_code: int) -> None:
    import hashlib
    API_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:12] if key \
        else "anonymous"
    with API_AUDIT_LOG.open("a") as fh:
        fh.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request_id": request_id,
            "method": method,
            "path": path,
            "key_fingerprint": fingerprint,   # never the key itself
            "status": status_code,
        }) + "\n")


def _envelope(request_id: str, status: str, payload=None, error=None,
              http_status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "status": status,
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="seconds"),
            "request_id": request_id,
            "payload": payload,
            "error": error,
        },
    )


@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    path = request.url.path
    supplied = request.headers.get("X-API-Key", "")

    def deny(code: str, message: str, http_status: int):
        _audit(request_id, request.method, path, supplied, http_status)
        return _envelope(request_id, "error",
                         error={"code": code, "message": message},
                         http_status=http_status)

    if path.startswith("/api/v1") and path not in (
        "/api/v1/health", "/api/v1/docs", "/api/v1/openapi.json",
    ):
        valid = _keys()
        if not valid:
            return deny("SERVER_NOT_CONFIGURED",
                        "PLATFORM_API_KEYS not set.", 503)
        if supplied not in valid:
            return deny("UNAUTHORIZED",
                        "Missing or invalid X-API-Key.", 401)

        # RBAC: admin-only resources (doc 022 authorization before logic)
        resource = path.removeprefix("/api/v1/")
        if resource in ADMIN_RESOURCES and valid[supplied] != "admin":
            return deny("FORBIDDEN",
                        f"'{resource}' requires the admin role.", 403)

        # fixed-window per-key rate limit
        now = time.monotonic()
        window = [t for t in _rate.get(supplied, []) if now - t < 60]
        if len(window) >= RATE_LIMIT_PER_MIN:
            return deny("RATE_LIMITED",
                        f"Limit {RATE_LIMIT_PER_MIN}/min.", 429)
        window.append(now)
        _rate[supplied] = window

    response = await call_next(request)
    if path.startswith("/api/v1") and path != "/api/v1/health":
        _audit(request_id, request.method, path, supplied,
               response.status_code)
    return response


@app.get("/api/v1/health")
async def health(request: Request):
    return _envelope(request.state.request_id, "ok",
                     payload={"service": "oil-intelligence-api",
                              "version": "1.0"})


@app.get("/api/v1/resources")
async def resources(request: Request):
    return _envelope(request.state.request_id, "ok",
                     payload={"resources": sorted(RESOURCES)})


@app.get("/api/v1/{resource}")
async def get_resource(resource: str, request: Request):
    request_id = request.state.request_id
    artifact_name = RESOURCES.get(resource)
    if artifact_name is None:
        return _envelope(request_id, "error",
                         error={"code": "NOT_FOUND",
                                "message": f"Unknown resource '{resource}'. "
                                           f"See /api/v1/resources."},
                         http_status=404)
    path = _base.DATA_DIR / f"{artifact_name}.json"
    if not path.exists():
        return _envelope(request_id, "error",
                         error={"code": "NOT_PUBLISHED",
                                "message": f"'{resource}' has not been "
                                           f"published yet — run the "
                                           f"pipeline."},
                         http_status=404)
    artifact = json.loads(path.read_text())
    return _envelope(request_id, "ok", payload={
        "resource": resource,
        "engine": artifact.get("engine"),
        "run_id": artifact.get("run_id"),
        "published_utc": artifact.get("finished_utc"),
        "engine_status": artifact.get("status"),
        "data": artifact.get("data"),
        "evidence": artifact.get("evidence"),
        "warnings": artifact.get("warnings"),
    })
