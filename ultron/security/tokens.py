"""Local API token management.

The API token authorises control of this computer through the local server.
It is stored outside the repository in a ``0600`` file (default:
``~/Library/Application Support/Ultron/api_token`` on macOS) or supplied via
``ULTRON_API_TOKEN``. It is never sent to the browser: the HUD exchanges it
once for a signed session cookie (see :mod:`ultron.security.sessions`).

Two credentials (see docs/M1_2_IDENTITY_SESSIONS_VOICE.md):

* the **owner API token** (``api_token`` / ``ULTRON_API_TOKEN``): signs HUD sessions and
  is the only token accepted at ``/login``;
* an optional **client token** (``client_token`` / ``ULTRON_CLIENT_TOKEN``) for headless
  clients such as the wake-word listener. It can send commands but can never log in to
  the HUD or approve a pending action, so a leaked client token cannot confirm anything.

Neither bearer token can approve high-impact actions; only a HUD session can.
:class:`TokenSource` re-reads token files when they change, so ``token rotate`` takes
effect in a running server without a restart.
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ultron.config import Settings, is_placeholder
from ultron.security.paths import atomic_write_text

MIN_TOKEN_LENGTH = 32
SETUP_HINT = "Run `python -m ultron token init` (or `uv run ultron token init`) to create one."
CLIENT_SETUP_HINT = "Run `python -m ultron token init --client` to create one."


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


def _load_secret(env: Mapping[str, str], env_name: str, path: Path, hint: str, *, required: bool) -> TokenLoadResult:
    raw = env.get(env_name)
    if raw is not None and raw.strip():
        if is_placeholder(raw):
            return TokenLoadResult(None, "env", f"{env_name} still contains a placeholder value. {hint}")
        token = raw.strip()
        problem = _check_strength(token, env_name)
        return TokenLoadResult(None if problem else token, "env", problem)

    if not path.exists():
        if not required:
            return TokenLoadResult(None, None, None)
        return TokenLoadResult(None, None, f"No API token is configured (expected {path}). {hint}")
    if sys.platform != "win32":
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            return TokenLoadResult(
                None,
                "file",
                f"The token file {path} is readable by other users. Fix it with: chmod 600 '{path}'",
            )
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return TokenLoadResult(None, "file", f"Could not read the token file {path} ({type(exc).__name__}).")
    problem = _check_strength(token, str(path))
    return TokenLoadResult(None if problem else token, "file", problem)


def load_api_token(settings: Settings, env: Mapping[str, str] | None = None) -> TokenLoadResult:
    """The owner API token (required for the server)."""
    env = os.environ if env is None else env
    return _load_secret(env, "ULTRON_API_TOKEN", settings.api_token_file, SETUP_HINT, required=True)


def load_client_token(settings: Settings, env: Mapping[str, str] | None = None) -> TokenLoadResult:
    """The optional limited client token. Not configured -> ``TokenLoadResult(None, None, None)``."""
    env = os.environ if env is None else env
    return _load_secret(env, "ULTRON_CLIENT_TOKEN", settings.client_token_file, CLIENT_SETUP_HINT, required=False)


class TokenSource:
    """Current owner and client tokens, re-read whenever a token file changes on disk.

    Environment variables are read once (they cannot change inside a running process).
    Passing ``owner``/``client`` results pins them (used by tests and embedding code).
    """

    def __init__(
        self,
        settings: Settings,
        env: Mapping[str, str] | None = None,
        *,
        owner: TokenLoadResult | None = None,
        client: TokenLoadResult | None = None,
    ) -> None:
        source = os.environ if env is None else env
        self._env = {k: source[k] for k in ("ULTRON_API_TOKEN", "ULTRON_CLIENT_TOKEN") if k in source}
        self._settings = settings
        self._pinned_owner = owner
        self._pinned_client = client
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[object, TokenLoadResult]] = {}

    @staticmethod
    def _signature(path: Path) -> object:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_mode)

    def _get(self, kind: str) -> TokenLoadResult:
        if kind == "owner":
            path, env_name, loader = self._settings.api_token_file, "ULTRON_API_TOKEN", load_api_token
        else:
            path, env_name, loader = self._settings.client_token_file, "ULTRON_CLIENT_TOKEN", load_client_token
        signature = ("env",) if self._env.get(env_name, "").strip() else self._signature(path)
        with self._lock:
            cached = self._cache.get(kind)
            if cached is not None and cached[0] == signature:
                return cached[1]
            result = loader(self._settings, env=self._env)
            self._cache[kind] = (signature, result)
            return result

    def owner(self) -> TokenLoadResult:
        return self._pinned_owner if self._pinned_owner is not None else self._get("owner")

    def client(self) -> TokenLoadResult:
        return self._pinned_client if self._pinned_client is not None else self._get("client")


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
