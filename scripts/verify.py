#!/usr/bin/env python3
"""Run the whole verification battery and print one PASS/FAIL summary.

Usage:  python scripts/verify.py [--quick]

Everything here is deterministic and needs no hardware, no microphone and no provider
credentials: it is the mock-tested half of docs/MAC_SMOKE_TEST.md. The rows in that
document are the half that still needs a real Mac.

Exit code is 0 only when every check passes, so it is safe to use as a pre-push gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

# (label, argv, needs_git)
CHECKS: list[tuple[str, list[str], bool]] = [
    ("full test suite", [PYTHON, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q"], False),
    ("ruff lint", [PYTHON, "-m", "ruff", "check", "--no-cache", "."], False),
    ("python compile", [PYTHON, "-m", "compileall", "-q", "-x", r"/\.venv/", "."], False),
    ("secret scan", [PYTHON, "-B", "-m", "ultron", "check-secrets"], False),
    ("whitespace/conflict-marker check", ["git", "diff", "--check"], True),
]


def run(label: str, argv: list[str]) -> tuple[bool, str]:
    print(f"\n=== {label} ===", flush=True)
    # keep the developer's PATH (git and some pytest fixtures need it); ULTRON_SKIP_DOTENV
    # stops a local .env from changing the outcome of the checks.
    env = {**os.environ, "ULTRON_SKIP_DOTENV": "1"}
    # noqa: S603 - argv is a fixed list of literals defined above, never user input.
    proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)  # noqa: S603
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
    print(tail or "(no output)")
    return proc.returncode == 0, tail


def main() -> int:
    if not shutil.which("git"):
        print("git not found: the whitespace check will be skipped", file=sys.stderr)

    results: list[tuple[str, bool]] = []
    for label, argv, needs_git in CHECKS:
        if needs_git and not shutil.which("git"):
            results.append((label, True))  # not applicable, not a failure
            continue
        ok, _ = run(label, argv)
        results.append((label, ok))

    print("\n" + "=" * 62)
    width = max(len(label) for label, _ in results)
    failed = 0
    for label, ok in results:
        print(f"  {label:<{width}}  {'PASS' if ok else 'FAIL'}")
        failed += 0 if ok else 1
    print("=" * 62)
    if failed:
        print(f"{failed} check(s) FAILED - inspect the output above.")
        return 1
    print("All checks passed. Live providers, macOS hardware and the microphone are NOT covered:")
    print("see docs/MAC_SMOKE_TEST.md for the real-device checklist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
