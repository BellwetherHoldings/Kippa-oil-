"""
Security Audit — secrets hygiene for the repository.

Governed by docs/014_Security.md 'secrets management' and the
architectural invariant that security exists throughout the platform.
Verifies that .env is ignored, that no tracked file contains anything
that looks like a credential, and that the ignore rules cover the
secret-bearing patterns. Run before every push; `oil deploy check`
runs it automatically.

Usage:
    python src/security/audit.py     (exit code 1 on findings)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns that indicate a literal credential in a tracked file
SECRET_PATTERNS = [
    (re.compile(r"""(?i)\b(api_?key|secret|token|passwd|password)\b\s*[=:]\s*['"]?[A-Za-z0-9_\-]{16,}"""),
     "assignment of a literal credential"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "32+ char hex string (key-shaped)"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key ID"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
     "private key material"),
]

REQUIRED_IGNORES = [".env", "*.key", "*.pem", "secrets/"]

SKIP_SUFFIXES = {".png", ".jpg", ".gif", ".pdf", ".pyc", ".docx"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO_ROOT,
        capture_output=True, text=True, check=True,
    ).stdout
    return [_REPO_ROOT / line for line in out.strip().splitlines()]


def run_audit() -> dict:
    findings: list[dict] = []
    checks = 0

    # 1. .env must be ignored and untracked
    checks += 1
    ignored = subprocess.run(
        ["git", "check-ignore", ".env"], cwd=_REPO_ROOT,
        capture_output=True, text=True,
    ).returncode == 0
    if not ignored:
        findings.append({"severity": "critical", "target": ".env",
                         "finding": ".env is NOT gitignored"})

    # 2. required ignore patterns present
    checks += 1
    gitignore = (_REPO_ROOT / ".gitignore").read_text() \
        if (_REPO_ROOT / ".gitignore").exists() else ""
    for pattern in REQUIRED_IGNORES:
        if pattern not in gitignore:
            findings.append({"severity": "high", "target": ".gitignore",
                             "finding": f"missing ignore pattern: {pattern}"})

    # 3. scan every tracked text file for credential shapes
    scanned = 0
    for path in _tracked_files():
        if path.suffix in SKIP_SUFFIXES or not path.exists():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        scanned += 1
        for pattern, label in SECRET_PATTERNS:
            for m in pattern.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                findings.append({
                    "severity": "critical",
                    "target": f"{path.relative_to(_REPO_ROOT)}:{line_no}",
                    "finding": f"{label}: {m.group(0)[:24]}…",
                })
    checks += scanned

    return {
        "status": "fail" if findings else "pass",
        "checks_run": checks,
        "tracked_files_scanned": scanned,
        "findings": findings,
    }


def main() -> None:
    report = run_audit()
    print("Security Audit — secrets hygiene")
    print("=" * 60)
    print(f"  {report['tracked_files_scanned']} tracked files scanned, "
          f"{report['checks_run']} checks")
    if report["findings"]:
        for f in report["findings"]:
            print(f"  [{f['severity'].upper()}] {f['target']}: {f['finding']}")
        print("\n  ✗ FAIL — resolve findings before pushing.")
        raise SystemExit(1)
    print("  ✓ PASS — no credential material in tracked files; "
          ".env properly ignored.")


if __name__ == "__main__":
    main()
