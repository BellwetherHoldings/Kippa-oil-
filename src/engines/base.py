"""
Engine framework — the standardized lifecycle every engine follows.

Governed by docs/005_Engine_Architecture.md:
    Receive Input → Validate → Execute → Verify Output → Log Results → Publish Output

Every engine on the platform inherits from Engine and implements execute().
Every run produces an EngineResult carrying its data, supporting evidence
(architectural invariant #2), warnings, and timing — and is appended to
logs/engine_runs.jsonl for the observability layer.
"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
LOG_DIR = REPO_ROOT / "logs"
RUN_LOG = LOG_DIR / "engine_runs.jsonl"


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    """Bound a signal to [lo, hi] — the platform's standard signal range."""
    return max(lo, min(hi, x))


def classify(value: float, levels: tuple[tuple[float, str], ...]) -> str:
    """Map a score onto an ascending (threshold, label) table.

    Returns the label of the first threshold the value is below; the last
    label catches everything else. Shared by every engine that publishes
    a leveled score (risk, stress, confidence, geo).
    """
    for threshold, label in levels:
        if value < threshold:
            return label
    return levels[-1][1]


def load_artifact(
    name: str,
    data_dir: Path | None = None,
    require_success: bool = False,
) -> dict[str, Any] | None:
    """Read a published engine artifact envelope from the data tier.

    Returns None when missing — and, with require_success, also when the
    producing run failed. One reader, one set of semantics, instead of a
    per-engine reimplementation.
    """
    path = (data_dir or DATA_DIR) / f"{name}.json"
    if not path.exists():
        return None
    artifact = json.loads(path.read_text())
    if require_success and artifact.get("status") != "success":
        return None
    return artifact


@dataclass
class EngineResult:
    """Standardized output envelope for every engine run."""

    engine: str
    version: str
    run_id: str
    status: str                 # "success" | "failed"
    started_utc: str
    finished_utc: str
    duration_ms: int
    data: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Engine(ABC):
    """Base class implementing the six-stage engine lifecycle.

    Subclasses set `name` / `version` and implement `execute()`. Optional
    overrides: `validate_input()` and `verify_output()`. Failures are
    isolated — run() returns a failed EngineResult rather than raising, so
    one engine's failure cannot take down a pipeline (doc 005, error
    handling).
    """

    name: str = "engine"
    version: str = "1.0"
    output_name: str | None = None   # when set, publishes data/<output_name>.json

    # -- lifecycle stages -------------------------------------------------

    def validate_input(self, inputs: dict[str, Any]) -> None:
        """Stage 2 — raise ValueError on invalid input. Default: accept."""

    @abstractmethod
    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        """Stage 3 — do the work. Returns (data, evidence).

        Append non-fatal issues to `warnings`.
        """

    def verify_output(self, data: dict[str, Any]) -> None:
        """Stage 4 — raise ValueError if output fails checks. Default:
        output must be a non-empty dict."""
        if not isinstance(data, dict) or not data:
            raise ValueError(f"{self.name}: empty or non-dict output")

    # -- runner ------------------------------------------------------------

    def run(self, inputs: dict[str, Any] | None = None) -> EngineResult:
        inputs = inputs or {}
        run_id = uuid.uuid4().hex[:12]
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        warnings: list[str] = []

        try:
            self.validate_input(inputs)                    # stage 2
            data, evidence = self.execute(inputs, warnings)  # stage 3
            self.verify_output(data)                       # stage 4
            status, error = "success", None
        except Exception as exc:  # failure isolation — never propagate
            data, evidence = {}, []
            status, error = "failed", f"{type(exc).__name__}: {exc}"

        finished = datetime.now(timezone.utc)
        result = EngineResult(
            engine=self.name,
            version=self.version,
            run_id=run_id,
            status=status,
            started_utc=started.isoformat(timespec="seconds"),
            finished_utc=finished.isoformat(timespec="seconds"),
            duration_ms=int((time.perf_counter() - t0) * 1000),
            data=data,
            evidence=evidence,
            warnings=warnings,
            error=error,
        )

        self._log(result)                                  # stage 5
        if result.ok and self.output_name:                 # stage 6
            self._publish(result)
        return result

    # -- internals ----------------------------------------------------------

    def _log(self, result: EngineResult) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with RUN_LOG.open("a") as fh:
            fh.write(json.dumps(result.to_dict(), default=str) + "\n")

    def _publish(self, result: EngineResult) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = DATA_DIR / f"{self.output_name}.json"
        out.write_text(json.dumps(result.to_dict(), indent=2, default=str))
