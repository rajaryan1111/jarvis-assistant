"""Filesystem confinement and sensitive-path filters for project tools.

Re-exports the core write-confinement primitives from :mod:`ultron.security.paths`
and adds read-only repository confinement helpers for Phase 5 project inspection:

* All project reads are confined to an explicitly configured canonical directory
  (:func:`resolve_project_root` and :func:`resolve_confined_path`).
* Relative traversal (``..``), unauthorized absolute paths, ``~`` expansion and
  symbolic links along any path component are rejected with :class:`PathPolicyError`.
* Sensitive files (``.env*``, tokens, credentials, private keys, session state,
  databases and keystores) are identified by :func:`is_sensitive_path` and never
  opened or read.
* Permitted text files are opened with ``O_NOFOLLOW`` where supported, checked for
  regular-file type and maximum byte size before reading, screened for binary NUL
  bytes, and passed through :func:`ultron.security.redact.redact`.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from ultron.security.paths import (
    PathPolicyError,
    atomic_replace,
    atomic_write_text,
    child_path,
    is_within,
    resolve_directory,
    unique_child,
)
from ultron.security.redact import redact

__all__ = [
    "IGNORED_DIRS",
    "MAX_RELATIVE_PATH_CHARS",
    "PathPolicyError",
    "atomic_replace",
    "atomic_write_text",
    "child_path",
    "is_sensitive_path",
    "is_within",
    "read_confined_text",
    "resolve_confined_path",
    "resolve_directory",
    "resolve_project_root",
    "unique_child",
]

MAX_RELATIVE_PATH_CHARS = 240
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]

IGNORED_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".cache",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "coverage",
    "faces",
})

_SENSITIVE_DIRS: frozenset[str] = frozenset({
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    "faces",
})

_SENSITIVE_EXACT_NAMES: frozenset[str] = frozenset({
    ".env",
    ".netrc",
    ".git-credentials",
    ".pypirc",
    ".npmrc",
    ".dockerconfigjson",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "authorized_keys",
    "known_hosts",
    "api_token",
    "client_token",
    "session_state.json",
    "credentials",
    "credentials.json",
    "client_secret.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.toml",
    "service_account.json",
    "memory.json",
    "knowledge_index.json",
})

_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".jks",
    ".kdbx",
    ".ovpn",
    ".der",
    ".crt",
    ".cer",
    ".pkcs12",
    ".sqlite",
    ".sqlite3",
    ".db",
)

_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "secret",
    "token",
    "credential",
    "password",
    "passwd",
    "private_key",
    "privatekey",
    "apikey",
    "api_key",
)


_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


def is_sensitive_path(path: str | os.PathLike[str]) -> bool:
    """Return True if ``path`` names a credential, key, token, ``.env`` or sensitive directory/file."""
    raw = str(path).strip()
    if not raw:
        return False
    candidate = Path(raw)
    parts = [part.strip().lower() for part in candidate.parts if part.strip() not in ("", ".")]
    if not parts:
        return False
    if any(part in _SENSITIVE_DIRS for part in parts):
        return True
    name = parts[-1].rstrip(". ")
    if not name:
        name = parts[-1]
    if (
        name == ".env"
        or name.startswith(".env.")
        or name.startswith(".env_")
        or name.endswith(".env")
        or parts[-1].startswith(".env")
    ):
        return True
    if name in _SENSITIVE_EXACT_NAMES:
        return True
    if name.endswith(_SENSITIVE_SUFFIXES):
        return True
    return any(keyword in name for keyword in _SENSITIVE_KEYWORDS)


def resolve_project_root(root: str | os.PathLike[str] | None = None) -> Path:
    """Resolve and validate the authorized project root directory."""
    configured = root if root is not None else (os.environ.get("ULTRON_PROJECT_ROOT") or DEFAULT_PROJECT_ROOT)
    return resolve_directory(configured, create=False, allow_symlink=False)


def resolve_confined_path(root: Path, relative_path: str = ".") -> Path:
    """Resolve literal ``relative_path`` inside ``root``, rejecting traversal, absolute paths and symlinks."""
    canonical_root = resolve_directory(root, create=False, allow_symlink=False)
    if not isinstance(relative_path, str):
        raise PathPolicyError("path must be a string")
    if len(relative_path) > MAX_RELATIVE_PATH_CHARS:
        raise PathPolicyError("path is too long")
    if any("\x00" in relative_path or ord(ch) < 32 or ord(ch) == 127 for ch in relative_path):
        raise PathPolicyError("path contains invalid control characters")
    raw = relative_path.strip() or "."
    if raw == ".":
        return canonical_root
    if (
        raw.startswith("~")
        or os.path.isabs(raw)
        or Path(raw).is_absolute()
        or "\\" in raw
        or _DRIVE_LETTER_RE.match(raw) is not None
    ):
        raise PathPolicyError("path must be relative to the authorized project root")

    rel = Path(raw)
    if ".." in rel.parts:
        raise PathPolicyError("path traversal is not allowed")

    current = canonical_root
    for part in rel.parts:
        if part in ("", "."):
            continue
        current = current / part
        if os.path.lexists(current) and current.is_symlink():
            raise PathPolicyError("symbolic links are not followed during project inspection")

    resolved = current.resolve(strict=False)
    if not is_within(canonical_root, resolved):
        raise PathPolicyError("path resolves outside the authorized project root")
    if not resolved.exists():
        raise PathPolicyError("path does not exist")
    return resolved


def read_confined_text(
    root: Path,
    target_file: Path,
    *,
    max_bytes: int = 65_536,
) -> tuple[str | None, str | None]:
    """Read a UTF-8 text file inside ``root`` safely.

    Returns ``(redacted_text, None)`` on success, or ``(None, reason)`` when the file
    is rejected (``symlink``, ``outside_root``, ``sensitive``, ``not_a_file``,
    ``oversized``, ``binary``, or ``unreadable``).
    """
    try:
        canonical_root = resolve_directory(root, create=False, allow_symlink=False)
    except PathPolicyError:
        return None, "invalid_root"

    if os.path.lexists(target_file) and target_file.is_symlink():
        return None, "symlink"
    try:
        resolved = target_file.resolve(strict=True)
    except OSError:
        return None, "unreadable"

    if not is_within(canonical_root, resolved):
        return None, "outside_root"

    rel = resolved.relative_to(canonical_root)
    if is_sensitive_path(rel):
        return None, "sensitive"

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target_file, flags)
    except OSError:
        return None, "symlink" if target_file.is_symlink() else "unreadable"

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "not_a_file"
        if st.st_size > max_bytes:
            return None, "oversized"
        raw = os.read(fd, max_bytes + 1)
    except OSError:
        return None, "unreadable"
    finally:
        os.close(fd)

    if len(raw) > max_bytes:
        return None, "oversized"
    if b"\x00" in raw[:4096]:
        return None, "binary"
    text = raw.decode("utf-8", errors="replace")
    return redact(text), None
