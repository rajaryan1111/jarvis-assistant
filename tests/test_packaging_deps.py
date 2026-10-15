"""Packaging guards for the Python 3.10 ``tomli`` fallback.

``ultron/tools/project.py`` imports ``tomllib`` on Python 3.11+ and falls back to the
third-party ``tomli`` distribution on 3.10. ``main`` imports that module unconditionally,
so a core-only 3.10 install must be able to resolve ``tomli``. The declared dependency is
easy to drop by accident, and CI installs the ``dev`` extra (which also pulls ``tomli``
through ``pytest`` on 3.10), so CI alone would not notice. These tests read the repository
metadata statically rather than importing application code.
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback under test
    import tomli as tomllib  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FALLBACK_SNIPPET = "import tomli as tomllib"


def _project_metadata() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_tomli_fallback_dependency_is_declared_for_python_310():
    """The 3.10-only fallback import must keep its matching core dependency."""
    source = (PROJECT_ROOT / "ultron" / "tools" / "project.py").read_text(encoding="utf-8")
    if _FALLBACK_SNIPPET not in source:
        return  # fallback removed upstream: nothing left to guard

    requirements = [item.strip() for item in _project_metadata()["project"]["dependencies"]]
    tomli_requirements = [item for item in requirements if item.split(";")[0].strip().startswith("tomli")]
    assert tomli_requirements, "project.py falls back to tomli but pyproject.toml does not declare it"
    assert all("python_version < '3.11'" in item for item in tomli_requirements), tomli_requirements


def test_tomli_fallback_dependency_is_locked_for_python_310():
    """``uv sync --locked`` must be able to install the fallback on 3.10.

    Only the root package's own ``dependencies`` list decides what ``uv sync`` installs, so the
    check reads that list specifically rather than any ``tomli`` mention in the lockfile (the
    metadata section below it and unrelated packages also mention ``tomli``).
    """
    lock = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")
    root_entry = lock.split('name = "jarvis-assistant"', 1)
    assert len(root_entry) == 2, "uv.lock is missing the root package entry"
    root_block = root_entry[1].split("[[package]]", 1)[0]

    assert "dependencies = [" in root_block, "uv.lock root entry has no dependencies list"
    dependencies = root_block.split("dependencies = [", 1)[1].split("]", 1)[0]
    declared = [line.strip() for line in dependencies.splitlines() if "tomli" in line]
    assert declared, "tomli is not in the locked root dependencies"
    assert "python_full_version < '3.11'" in declared[0], declared
