"""CLI behaviour and a real server startup (subprocess, loopback, ephemeral port)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import psutil
import pytest

from tests.conftest import PROJECT_ROOT
from ultron import cli
from ultron.security.tokens import generate_token


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ULTRON_")}
    env.update({
        "ULTRON_SKIP_DOTENV": "1",
        "ULTRON_DATA_DIR": str(tmp_path / "data"),
        "ULTRON_MEMORY_FILE": str(tmp_path / "memory.json"),
        "HOME": str(tmp_path / "home"),
        "PYTHONUNBUFFERED": "1",
    })
    env.update(extra)
    return env


def _run_cli(tmp_path, *args, **extra):
    return subprocess.run([sys.executable, "-m", "ultron", *args], capture_output=True, text=True,
                          cwd=PROJECT_ROOT, env=_env(tmp_path, **extra), timeout=60)


def test_token_init_creates_private_file_and_never_prints_token(tmp_path):
    res = _run_cli(tmp_path, "token", "init")
    assert res.returncode == 0, res.stderr
    token_file = tmp_path / "data" / "api_token"
    token = token_file.read_text().strip()
    assert len(token) >= 32
    assert token not in res.stdout + res.stderr
    assert oct(token_file.stat().st_mode & 0o777) == "0o600"
    again = _run_cli(tmp_path, "token", "init")
    assert again.returncode == 0 and "already exists" in again.stdout
    assert token_file.read_text().strip() == token  # init never overwrites


def test_token_rotate_replaces_token(tmp_path):
    _run_cli(tmp_path, "token", "init")
    token_file = tmp_path / "data" / "api_token"
    before = token_file.read_text()
    res = _run_cli(tmp_path, "token", "rotate")
    assert res.returncode == 0 and token_file.read_text() != before
    assert token_file.read_text().strip() not in res.stdout


def test_token_show_only_on_explicit_request(tmp_path):
    _run_cli(tmp_path, "token", "init")
    token = (tmp_path / "data" / "api_token").read_text().strip()
    res = _run_cli(tmp_path, "token", "show")
    assert res.returncode == 0 and res.stdout.strip() == token


def test_doctor_reports_status_without_secret_values(tmp_path):
    fake_key = "sk-" + "doctor" * 6
    res = _run_cli(tmp_path, "doctor", OPENAI_API_KEY=fake_key)
    out = res.stdout + res.stderr
    assert "API token" in out and "OPENAI_API_KEY: configured" in out
    assert "NEWS_API_KEY: not set" in out
    assert fake_key not in out
    assert res.returncode == 1  # token missing -> problem reported


def test_doctor_rejects_unsafe_bind(tmp_path):
    res = _run_cli(tmp_path, "doctor", ULTRON_HOST="0.0.0.0")  # noqa: S104
    assert res.returncode == 1 and "non-loopback" in res.stdout


def test_cli_parser_lists_commands():
    help_text = cli.build_parser().format_help()
    for command in ("serve", "token", "open-hud", "doctor", "check-secrets"):
        assert command in help_text


def test_wakeword_listener_exits_cleanly_without_credentials(tmp_path):
    res = subprocess.run([sys.executable, "wakeword_listener.py"], capture_output=True, text=True,
                         cwd=PROJECT_ROOT, env=_env(tmp_path), timeout=60)
    assert res.returncode == 2
    assert "PICOVOICE_ACCESS_KEY is not set" in res.stderr


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening_addresses(pid: int, port: int) -> set[str]:
    try:
        proc = psutil.Process(pid)
        conns = proc.net_connections(kind="inet") if hasattr(proc, "net_connections") else proc.connections("inet")
    except (psutil.Error, OSError):
        return set()
    return {c.laddr.ip for c in conns if c.status == psutil.CONN_LISTEN and c.laddr.port == port}


def test_server_py_starts_on_loopback_with_debug_off_and_requires_auth(tmp_path):
    token = generate_token()
    port = _free_port()
    env = _env(tmp_path, ULTRON_PORT=str(port), ULTRON_API_TOKEN=token)
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=PROJECT_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        health = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2) as res:  # noqa: S310
                    health = json.loads(res.read())
                    break
            except (urllib.error.URLError, ConnectionError):
                if proc.poll() is not None:
                    break
                time.sleep(0.3)
        assert health == {"ok": True, "auth_configured": True}

        addresses = _listening_addresses(proc.pid, port)
        if addresses:  # psutil may lack permission on some platforms; skip that check then
            assert addresses == {"127.0.0.1"}

        req = urllib.request.Request(f"{base}/status")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        assert exc.value.code == 401

        req = urllib.request.Request(f"{base}/status", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=5) as res:  # noqa: S310
            assert set(json.loads(res.read())) == {"time", "battery"}
    finally:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
    assert "debug off" in output
    assert "bind address 127.0.0.1" in output
    assert token not in output
    assert "Debugger is active" not in output


def test_server_py_refuses_non_loopback_bind(tmp_path):
    env = _env(tmp_path, ULTRON_HOST="0.0.0.0", ULTRON_PORT=str(_free_port()))  # noqa: S104
    res = subprocess.run([sys.executable, "server.py"], cwd=PROJECT_ROOT, env=env, capture_output=True,
                         text=True, timeout=60)
    assert res.returncode == 2 and "non-loopback" in res.stderr
