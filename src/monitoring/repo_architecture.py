"""
Repository Architecture Engine — doc 003 made executable.

Verifies the repository against docs/003_Repository_Architecture.md and
the CLAUDE.md directory map: all 22 architecture documents present, every
governed source directory exists, the knowledge-base index is intact, and
no assets live outside their defined locations.

Usage:
    python src/monitoring/repo_architecture.py

Publishes:
    data/repo_architecture_report.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engines.base import Engine

EXPECTED_DOCS = 22

REQUIRED_DIRS = [
    "docs", "config", "tests", "simulations",
    "src/data", "src/api", "src/cli", "src/monitoring", "src/deployment",
    "src/observability", "src/security",
    "src/engines/forecast", "src/engines/scoring", "src/engines/risk",
    "src/engines/confidence", "src/engines/strategy",
    "src/engines/backtesting", "src/engines/simulation",
    "src/engines/analytics", "src/engines/automation",
    "src/engines/data_processing",
    "src/intelligence/geopolitical", "src/intelligence/supply_chain",
    "src/intelligence/market_sentiment", "src/intelligence/macroeconomic",
]

ROOT_FILE_ALLOWLIST = {
    "CLAUDE.md", "README.md", ".gitignore", "requirements.txt", ".env",
}


class RepoArchitectureEngine(Engine):
    name = "repo_architecture"
    version = "1.0"
    output_name = "repo_architecture_report"

    def execute(
        self, inputs: dict[str, Any], warnings: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        issues: list[str] = []

        docs = sorted((_REPO_ROOT / "docs").glob("0*.md"))
        doc_numbers = {d.name[:3] for d in docs}
        expected_numbers = {f"{i:03d}" for i in range(1, EXPECTED_DOCS + 1)}
        missing_docs = sorted(expected_numbers - doc_numbers)
        if missing_docs:
            issues.append(f"missing architecture documents: {missing_docs}")
        if not (_REPO_ROOT / "docs" / "INDEX.md").exists():
            issues.append("docs/INDEX.md (knowledge-base index) missing")
        if not (_REPO_ROOT / "CLAUDE.md").exists():
            issues.append("CLAUDE.md operating reference missing")

        missing_dirs = [d for d in REQUIRED_DIRS
                        if not (_REPO_ROOT / d).is_dir()]
        if missing_dirs:
            issues.append(f"missing governed directories: {missing_dirs}")

        stray = [p.name for p in _REPO_ROOT.iterdir()
                 if p.is_file() and p.name not in ROOT_FILE_ALLOWLIST
                 and not p.name.startswith(".")]
        if stray:
            issues.append(f"files outside defined locations at repo root: "
                          f"{stray}")

        test_files = list((_REPO_ROOT / "tests").glob("test_*.py"))
        if len(test_files) < 5:
            issues.append(f"only {len(test_files)} test suites present")

        for w in issues:
            warnings.append(w)

        data = {
            "compliant": not issues,
            "as_of": date.today().isoformat(),
            "architecture_documents": f"{len(doc_numbers & expected_numbers)}"
                                      f"/{EXPECTED_DOCS}",
            "governed_directories": f"{len(REQUIRED_DIRS) - len(missing_dirs)}"
                                    f"/{len(REQUIRED_DIRS)}",
            "test_suites": len(test_files),
            "issues": issues,
        }
        evidence = [f"docs/ scan: {len(docs)} documents; "
                    f"{len(REQUIRED_DIRS)} governed directories checked "
                    f"against CLAUDE.md map"]
        return data, evidence


def main() -> None:
    result = RepoArchitectureEngine().run()
    if not result.ok:
        raise SystemExit(f"Engine failed: {result.error}")
    d = result.data
    print("Repository Architecture Compliance (doc 003)")
    print("=" * 60)
    print(f"  {'✓ COMPLIANT' if d['compliant'] else '✗ ISSUES FOUND'}")
    print(f"  Documents: {d['architecture_documents']} | "
          f"Directories: {d['governed_directories']} | "
          f"Test suites: {d['test_suites']}")
    for i in d["issues"]:
        print(f"  ⚠ {i}")
    print(f"\n  Published → data/repo_architecture_report.json")


if __name__ == "__main__":
    main()
