"""Lightweight hardcoded-credential scanner.

Used by the regression test suite, CI and ``python -m ultron check-secrets``.
Findings report file, line and rule only — never the matched value.

To allow an intentional fixture line, append the marker ``secret-scan: allow``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ALLOW_MARKER = "secret-scan: allow"
MAX_FILE_BYTES = 2_000_000

_PLACEHOLDER_HINTS = (
    "your_", "your-", "example", "placeholder", "dummy", "changeme", "change_me", "xxxx", "test-key",
    "fake", "redacted", "<", "{", "os.getenv", "os.environ", "environ", "getenv",
)

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("picovoice-access-key", re.compile(r"\bpv_[A-Za-z0-9+/]{20,}={0,2}")),
    ("openai-api-key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    (
        "key-in-url-query",
        re.compile(r"(?i)[?&](?:api[_-]?key|apikey|appid|access[_-]?key|token)=(?P<value>[A-Za-z0-9_\-]{16,})"),
    ),
    (
        "credential-assignment",
        re.compile(
            r"(?i)\b(?:[A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?KEY|SECRET|TOKEN|PASSWORD|APPID))\b[\"']?\s*[:=]\s*"
            r"[\"'](?P<value>[A-Za-z0-9_\-+/=.]{16,})[\"']"
        ),
    ),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:  # never includes the secret value
        return f"{self.path}:{self.line}: possible hardcoded credential ({self.rule})"


def _looks_like_placeholder(line: str, value: str | None) -> bool:
    probe = (value or line).lower()
    return any(h in probe for h in _PLACEHOLDER_HINTS)


def scan_text(text: str, path: str = "<text>") -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        rule = _first_matching_rule(line)
        if rule is not None:
            findings.append(Finding(path, lineno, rule))  # one finding per line, most specific rule first
    return findings


def _first_matching_rule(line: str) -> str | None:
    for rule, pattern in RULES:
        for match in pattern.finditer(line):
            value = match.groupdict().get("value")
            if rule in ("credential-assignment", "key-in-url-query") and _looks_like_placeholder(line, value):
                continue
            return rule
    return None


def scan_file(path: Path, display: str | None = None) -> list[Finding]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in data[:8192]:
        return []  # binary
    return scan_text(data.decode("utf-8", errors="ignore"), display or str(path))


def repository_files(root: Path) -> list[Path]:
    """Tracked plus untracked-but-not-ignored files (what could be committed next)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
            cwd=root, capture_output=True, check=True, timeout=30,
        ).stdout
        return [root / p for p in out.decode("utf-8", errors="ignore").split("\x00") if p]
    except (OSError, subprocess.SubprocessError):
        skip = {".git", ".venv", "node_modules", "__pycache__"}
        return [p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & skip)]


def scan_paths(paths: Iterable[Path], root: Path | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for p in paths:
        if not p.is_file():
            continue
        display = str(p.relative_to(root)) if root and p.is_relative_to(root) else str(p)
        findings.extend(scan_file(p, display))
    return findings


def scan_repository(root: Path) -> list[Finding]:
    return scan_paths(repository_files(root), root)
