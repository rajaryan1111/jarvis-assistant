"""Local API token management.

The API token authorises control of this computer through the local server.
It is stored outside the repository in a ``0600`` file (default:
``~/Library/Application Support/Ultron/api_token`` on macOS) or supplied via
``ULTRON_API_TOKEN``. It is never sent to the browser: the HUD exchanges it
once for a signed session cookie (see :mod:`ultron.security.sessions`).
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ultron.config import Settings, is_placeholder
from ultron.security.paths import atomic_write_text

MIN_TOKEN_LENGTH = 32
SETUP_HINT = "Run `python -m ultron token init` (or `uv run ultron token init`) to create one."


@dataclass(frozen=True)
class TokenLoadResult:
    token: str | None
    source: str | None  # "env" | "file" | None
    problem: str | None

    @property
    def ok(self) -> bool:
        return self.token is not None and self.problem is None


def generate_token() -> str:
    return secrets.token_urlsafe(32)  # 43 URL-safe characters, 256 bits


def _check_strength(token: str, where: str) -> str | None:
    if len(token) < MIN_TOKEN_LENGTH:
        return f"The API token from {where} is too short (minimum {MIN_TOKEN_LENGTH} characters). {SETUP_HINT}"
    return None


def load_api_token(settings: Settings, env: Mapping[str, str] | None = None) -> TokenLoadResult:
    env = os.environ if env is None else env
    raw = env.get("ULTRON_API_TOKEN")
    if raw is not None and raw.strip():
        if is_placeholder(raw):
            return TokenLoadResult(None, "env", f"ULTRON_API_TOKEN still contains a placeholder value. {SETUP_HINT}")
        token = raw.strip()
        problem = _check_strength(token, "ULTRON_API_TOKEN")
        return TokenLoadResult(None if problem else token, "env", problem)

    path = settings.api_token_file
    if not path.exists():
        return TokenLoadResult(None, None, f"No API token is configured (expected {path}). {SETUP_HINT}")
    if sys.platform != "win32":
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            return TokenLoadResult(
                None,
                "file",
                f"The API token file {path} is readable by other users. Fix it with: chmod 600 '{path}'",
            )
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return TokenLoadResult(None, "file", f"Could not read the API token file {path} ({type(exc).__name__}).")
    problem = _check_strength(token, str(path))
    return TokenLoadResult(None if problem else token, "file", problem)


def write_token_file(path: Path, token: str | None = None, *, overwrite: bool = False) -> Path:
    """Create the token file with ``0600`` permissions. Returns the path; never prints the token."""
    token = token or generate_token()
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError("token too short")
    if not path.parent.exists():
        # Only tighten permissions on a directory we create; never chmod an existing
        # user directory (e.g. ULTRON_API_TOKEN_FILE=~/token must not change ~).
        path.parent.mkdir(parents=True, mode=0o700)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    # Random O_EXCL temp name (never follows a planted symlink), mode 600, then rename.
    atomic_write_text(path, token + "\n", mode=0o600)
    return path
