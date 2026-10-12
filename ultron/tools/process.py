"""The one way tools start processes.

``run_command`` enforces, for every call:

* argv only — a list of strings, never a shell string, never ``shell=True``;
* an absolute executable path (no ``PATH`` lookup, so a planted ``say`` or
  ``osascript`` earlier on ``PATH`` is never picked up);
* a timeout (the child is killed when it expires);
* stdin closed unless input is given explicitly;
* bounded, decoded and redacted output — known secret values and
  credential-shaped strings never reach messages or logs;
* no exceptions: timeouts, missing executables and OS errors become a
  :class:`CommandResult` so callers always leave the app in a consistent state.

There is deliberately no general "run this shell command" tool.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ultron.security.redact import redact

Runner = Callable[..., Any]
DEFAULT_MAX_OUTPUT = 4096


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None  # "timeout" | "not_found" | "os_error" | None


def _bounded(data: Any, limit: int) -> str:
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
    if len(text) > limit:
        text = text[:limit] + "…[truncated]"
    return redact(text)


def validate_argv(argv: Sequence[str]) -> list[str]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv:
        raise ValueError("argv must be a non-empty list of strings (no shell strings)")
    items = list(argv)
    if not all(isinstance(item, str) for item in items):
        raise ValueError("every argv item must be a string")
    if any("\x00" in item for item in items):
        raise ValueError("argv items must not contain NUL bytes")
    if not os.path.isabs(items[0]):
        raise ValueError("the executable must be an absolute path")
    return items


def run_command(
    argv: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    timeout: float = 10.0,
    max_output: int = DEFAULT_MAX_OUTPUT,
    input_text: str | None = None,
) -> CommandResult:
    items = validate_argv(argv)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    kwargs: dict[str, Any] = {"capture_output": True, "timeout": timeout, "check": False, "shell": False}
    if input_text is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input_text.encode("utf-8")
    try:
        completed = runner(items, **kwargs)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(False, None, _bounded(exc.stdout, max_output), _bounded(exc.stderr, max_output),
                             timed_out=True, error="timeout")
    except FileNotFoundError:
        return CommandResult(False, None, error="not_found")
    except OSError as exc:
        return CommandResult(False, None, stderr=_bounded(type(exc).__name__, max_output), error="os_error")
    returncode = getattr(completed, "returncode", None)
    return CommandResult(
        ok=returncode == 0,
        returncode=returncode,
        stdout=_bounded(getattr(completed, "stdout", None), max_output),
        stderr=_bounded(getattr(completed, "stderr", None), max_output),
    )
