"""Regression tests: no hardcoded credentials in the repository; the scanner never prints values."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import PROJECT_ROOT
from ultron.security.secret_scan import Finding, scan_file, scan_repository, scan_text

# Synthetic credentials are assembled at runtime so this file itself stays clean.
FAKE_PV = "pv_" + "Zq3" * 12
FAKE_OPENAI = "sk-" + "proj-" + "Ab1" * 10
FAKE_GENERIC = "a1b2c3d4" * 4


def test_repository_has_no_hardcoded_credentials():
    findings = scan_repository(PROJECT_ROOT)
    assert findings == [], "\n".join(map(str, findings))


def test_wakeword_listener_reads_key_from_environment():
    source = (PROJECT_ROOT / "wakeword_listener.py").read_text(encoding="utf-8")
    assert "PICOVOICE_ACCESS_KEY" in source
    assert "ACCESS_KEY = \"" not in source
    assert scan_text(source) == []


@pytest.mark.parametrize("line,rule", [
    (f'ACCESS_KEY = "{FAKE_PV}"', "picovoice-access-key"),
    (f"client = OpenAI(api_key='{FAKE_OPENAI}')", "openai-api-key"),
    (f'NEWS_API_KEY = "{FAKE_GENERIC}"', "credential-assignment"),
    (f'url = "https://api.example/v1?appid={FAKE_GENERIC}"', "key-in-url-query"),
    ("-----BEGIN RSA " + "PRIVATE KEY-----", "private-key-block"),
])
def test_scanner_detects_common_credential_shapes(line, rule):
    findings = scan_text(line, "sample.py")
    assert [f.rule for f in findings] == [rule]


@pytest.mark.parametrize("line", [
    'OPENAI_API_KEY=your_openai_api_key_here',
    'api_key = os.getenv("NEWS_API_KEY")',
    'TOKEN = "short"',
    f'API_TOKEN = "{FAKE_GENERIC}"  # secret-scan: allow',
    'params={"apiKey": api_key}',
])
def test_scanner_ignores_placeholders_env_lookups_and_allowlisted_lines(line):
    assert scan_text(line) == []


def test_findings_never_contain_the_secret_value(tmp_path):
    f = tmp_path / "leak.py"
    f.write_text(f'ACCESS_KEY = "{FAKE_PV}"\n', encoding="utf-8")
    findings = scan_file(f, "leak.py")
    assert findings == [Finding("leak.py", 1, "picovoice-access-key")]
    assert FAKE_PV not in str(findings[0])


def test_binary_and_huge_files_are_skipped(tmp_path):
    binary = tmp_path / "model.ppn"
    binary.write_bytes(b"\x00\x01" + FAKE_PV.encode())
    assert scan_file(binary) == []


def test_check_secrets_cli_fails_on_leak_without_printing_value(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "config.py").write_text(f'PICOVOICE_KEY = "{FAKE_PV}"\n', encoding="utf-8")
    (tmp_path / ".env").write_text(f"PICOVOICE_ACCESS_KEY={FAKE_PV}\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    res = subprocess.run([sys.executable, "-m", "ultron", "check-secrets", "--root", str(tmp_path)],
                         capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=60)
    assert res.returncode == 1
    assert "config.py:1" in res.stdout
    assert ".env" not in res.stdout  # ignored files are not "about to be committed"
    assert FAKE_PV not in res.stdout + res.stderr


def test_gitignore_covers_secrets_and_personal_data():
    for path in (".env", ".env.local", "api_token", "memory.json", "faces/raj.jpg", "knowledge_index.json",
                 "knowledge_docs/notes.pdf", "local.db", "hand_landmarker.task"):
        res = subprocess.run(["git", "check-ignore", "-q", path], cwd=PROJECT_ROOT, timeout=30)
        assert res.returncode == 0, f"{path} is not git-ignored"
    res = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=PROJECT_ROOT, timeout=30)
    assert res.returncode == 1, ".env.example must stay tracked"


def test_env_example_contains_only_placeholders():
    from ultron.config import CREDENTIALS, is_placeholder

    values = {}
    for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    for name in CREDENTIALS:
        assert name in values, f"{name} missing from .env.example"
        assert is_placeholder(values[name]), f"{name} in .env.example is not a placeholder"
