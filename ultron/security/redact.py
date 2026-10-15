"""Redaction of secrets from log lines and error messages.

Network libraries embed full URLs (including ``?appid=...`` style keys and
``user:password@`` userinfo) in exception messages, so anything that may be
logged must pass through :func:`redact` first. A logging filter applies it to
every record.
"""

from __future__ import annotations

import logging
import os
import re

from ultron.config import CREDENTIALS, is_placeholder

REDACTED = "[REDACTED]"

_SENSITIVE_PARAM = r"(?:api[_-]?key|apikey|appid|access[_-]?key|accesskey|[a-z_-]*token|secret|password|key)"
_QUERY_RE = re.compile(rf"(?i)([?&;]{_SENSITIVE_PARAM}=)[^&\s'\"<>]+")
_HEADER_RE = re.compile(r"(?i)\b(authorization:\s*bearer\s+|x-api-key:\s*)[^\s'\",]+")
# URL userinfo credentials (``scheme://user:password@host``), e.g. git remote URLs. The
# password shape matches no token prefix and appears in no query parameter, so it needs its
# own rule. Conservative by construction: a scheme with ``://`` is required, the password
# cannot contain ``/``, ``@`` or whitespace, and a closing ``@`` is required - so ports
# (``host:8080/path``) and scp-style syntax (``git@host:path``) never match.
_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+):([^\s@/]+)@")
_TOKEN_PATTERNS = (
    re.compile(r"\bpv_[A-Za-z0-9+/]{16,}={0,2}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}"),  # Groq API keys
)

_EXTRA_SECRET_ENV = ("ULTRON_API_TOKEN", "ULTRON_CLIENT_TOKEN")


def _known_secret_values() -> list[str]:
    values = []
    for name in (*CREDENTIALS, *_EXTRA_SECRET_ENV):
        v = os.environ.get(name)
        if v and not is_placeholder(v) and len(v.strip()) >= 8:
            values.append(v.strip())
    # Longest first so overlapping values are fully masked.
    return sorted(set(values), key=len, reverse=True)


def redact(value: object, extra_secrets: tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in (*_known_secret_values(), *(s for s in extra_secrets if s and len(s) >= 8)):
        text = text.replace(secret, REDACTED)
    text = _QUERY_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _HEADER_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _USERINFO_RE.sub(lambda m: m.group(1) + ":" + REDACTED + "@", text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the fully formatted message of each record."""

    def __init__(self, extra_secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.extra_secrets = extra_secrets

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging crash the app
            return True
        record.msg = redact(message, self.extra_secrets)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info), self.extra_secrets)
            record.exc_info = None
        return True


def install_log_redaction(extra_secrets: tuple[str, ...] = ()) -> RedactingFilter:
    """Attach a :class:`RedactingFilter` to every handler on the root logger."""
    root = logging.getLogger()
    filt = RedactingFilter(extra_secrets)
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in root.handlers:
        for existing in list(handler.filters):
            if isinstance(existing, RedactingFilter):
                handler.removeFilter(existing)
        handler.addFilter(filt)
    return filt
