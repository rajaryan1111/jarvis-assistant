"""Narrow, explicit filesystem helpers for tools that create or replace files.

Rules enforced here:

* A tool only writes *directly inside* one configured directory, under a file
  name it generated itself (``child_path`` rejects separators, ``..``, NUL,
  hidden names and anything that would resolve elsewhere).
* Existing symlinks are never written through: a planted link at the target
  name is refused instead of followed.
* New files never overwrite existing ones (``unique_child``).
* Replacing a file is atomic: write a uniquely named temp file in the same
  directory (``O_EXCL``, so a pre-planted link is never followed) and
  ``os.replace`` it into place, so failures leave the old file intact.
* Directories are configurable (environment variables documented in
  docs/M1_1_TOOL_SECURITY_BOUNDARY.md); there is no hidden allowlist that
  would break normal use, and no tool can be pointed at an arbitrary path by
  command text.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path


class PathPolicyError(ValueError):
    """A path failed validation. The message is safe to show to the user."""


def resolve_directory(path: str | os.PathLike[str], *, create: bool = False, allow_symlink: bool = False,
                      mode: int = 0o700) -> Path:
    """Return the canonical form of a directory a tool is allowed to write into."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise PathPolicyError("directory must be an absolute path")
    if candidate.is_symlink() and not allow_symlink:
        raise PathPolicyError("directory is a symbolic link")
    if not candidate.exists():
        if not create:
            raise PathPolicyError("directory does not exist")
        candidate.mkdir(parents=True, mode=mode, exist_ok=True)
    if not candidate.is_dir():
        raise PathPolicyError("path is not a directory")
    return candidate.resolve(strict=True)


def child_path(directory: Path, name: str) -> Path:
    """``directory / name`` for a single, plain file name that stays inside ``directory``."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        raise PathPolicyError("invalid file name")
    if "\x00" in name or "/" in name or (os.altsep and os.altsep in name) or os.sep in name:
        raise PathPolicyError("file name must not contain path separators")
    if name.startswith(".") or name.startswith("-"):
        raise PathPolicyError("file name must not start with '.' or '-'")
    target = directory / name
    if target.is_symlink():
        raise PathPolicyError("refusing to write through a symbolic link")
    if target.parent.resolve(strict=True) != directory.resolve(strict=True):
        raise PathPolicyError("file would be created outside its directory")
    return target


def is_within(root: Path, candidate: Path) -> bool:
    """True if ``candidate`` (after resolving symlinks) is ``root`` or inside it."""
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def unique_child(directory: Path, stem: str, suffix: str, attempts: int = 100) -> Path:
    """A child path that does not exist yet (``stem.suffix``, then ``stem-2.suffix`` ...)."""
    for index in range(1, attempts + 1):
        name = f"{stem}{suffix}" if index == 1 else f"{stem}-{index}{suffix}"
        if os.path.lexists(directory / name):  # existing file, directory or (dangling) link: skip
            continue
        return child_path(directory, name)
    raise PathPolicyError("could not find a free file name")


def atomic_replace(target: Path, write: Callable[[str], None], *, mode: int = 0o600) -> None:
    """Create ``target`` atomically: ``write(temp_path)`` then ``os.replace``.

    The temp file is created with ``O_EXCL`` under a random name in the same directory,
    so an attacker-planted symlink is never followed and readers never see a partial file.
    If ``write`` fails, the temp file is removed and ``target`` is left untouched.
    """
    directory = target.parent
    suffix = target.suffix
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=f".tmp{suffix}", dir=directory)
    os.close(fd)
    try:
        os.chmod(tmp_name, mode)
        write(tmp_name)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(target: Path, text: str, *, mode: int = 0o600) -> None:
    def _write(tmp_name: str) -> None:
        with open(tmp_name, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

    atomic_replace(target, _write, mode=mode)
