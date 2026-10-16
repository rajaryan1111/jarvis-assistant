"""Regression test: the suite must import on Python 3.10.

Python 3.11 added ``tomllib``. CI installs the ``dev`` extra on Python 3.10, where
``tomllib`` does not exist and the declared ``tomli`` fallback is the only way to read
``pyproject.toml``. A test module that imports ``tomllib`` bare therefore breaks
*collection* on 3.10 - and a collection error fails the whole run, not one test.

This reproduces that condition in a subprocess: ``tomllib`` is blocked by a meta-path
finder installed after pytest has read its own configuration, so pytest still starts
but the test modules see a 3.10-shaped world.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Installed as a pytest plugin so the blocker comes up *after* pytest loads its
#: config (pytest itself imports tomllib to read pyproject.toml).
BLOCKER_PLUGIN = textwrap.dedent(
    '''
    """Refuse `import tomllib`, exactly as Python 3.10 does."""
    import sys


    class _NoTomllib:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "tomllib":
                raise ModuleNotFoundError("No module named 'tomllib'")
            return None


    def pytest_configure(config):
        sys.meta_path.insert(0, _NoTomllib())
        sys.modules.pop("tomllib", None)
    '''
)


@pytest.fixture(scope="module")
def tomli_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("tomli") is not None


def test_every_module_that_parses_toml_has_the_fallback():
    """Static guard: a bare `import tomllib` is a 3.10 collection error."""
    checked = 0
    for path in sorted((REPO_ROOT / "tests").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "import tomllib" not in text:
            continue
        checked += 1
        assert "import tomli as tomllib" in text, (
            f"{path.name} imports tomllib without the Python 3.10 fallback. "
            "On 3.10 that is a ModuleNotFoundError during collection, which fails "
            "every test in the run, not just this file."
        )
    assert checked, "no test module reads TOML any more; this guard has gone stale"


def test_the_suite_collects_without_tomllib(tmp_path, tomli_available):
    """The real check: collection must succeed in a 3.10-shaped interpreter."""
    if not tomli_available:
        pytest.skip(
            "tomli is not installed here; on Python 3.10 CI installs it via the "
            "python_version < '3.11' marker, so this path only matters there"
        )

    plugin = tmp_path / "no_tomllib.py"
    plugin.write_text(BLOCKER_PLUGIN, encoding="utf-8")

    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(tmp_path),
        "ULTRON_SKIP_DOTENV": "1",
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "--collect-only", "-q", "-p", "no:cacheprovider",
            "-p", "no_tomllib", "tests",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        "collection failed with tomllib unavailable (the Python 3.10 condition):\n"
        f"{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
    )
