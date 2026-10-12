"""HTTP-level security tests for the HUD/API server (Flask test client, fake backend)."""

from __future__ import annotations

import re

import pytest
from flask.testing import FlaskClient

from ultron.api.app import create_app
from ultron.config import load_settings
from ultron.security.confirmation import ConfirmationStore
from ultron.security.policy import CommandContext
from ultron.security.sessions import SessionSigner
from ultron.security.tokens import TokenLoadResult, generate_token
from ultron.tools.registry import ToolRegistry
from ultron.tools.system import PowerTool

BASE = "http://127.0.0.1:5001"
ORIGIN = {"Origin": BASE}
EVIL = {"Origin": "https://evil.example"}


class FakeBackend:
    """Minimal backend: 'shutdown system' requests the (fake) power tool via the registry."""

    def __init__(self, runner) -> None:
        self.runner = runner
        self.registry = ToolRegistry(confirmations=ConfirmationStore(), platform="darwin")
        self.registry.register(PowerTool("shut down", runner=runner))
        self.handled: list[tuple[str, CommandContext]] = []
        self.spoken: list[str] = []
        self.gate_result = (True, "")

    def handle(self, text: str, ctx: CommandContext) -> str:
        self.handled.append((text, ctx))
        if text == "shutdown system":
            return self.registry.request("system.shutdown", {}, ctx).message
        return f"echo: {text}"

    def confirmation_gate(self, pending):
        return self.gate_result

    def status(self):
        return {"time": "12:00:00", "battery": 80}

    def speak(self, text: str, ctx: CommandContext) -> str:
        self.spoken.append(text)
        return "completed"

    def start_background(self) -> None:  # pragma: no cover - not used by the test client
        pass


@pytest.fixture
def token():
    return generate_token()


@pytest.fixture
def backend(fake_runner):
    return FakeBackend(fake_runner)


@pytest.fixture
def app(tmp_path, token, backend):
    settings = load_settings({"ULTRON_DATA_DIR": str(tmp_path)})
    application = create_app(settings, backend=backend, token_result=TokenLoadResult(token, "env", None))
    application.testing = True
    return application


class LocalClient(FlaskClient):
    """Test client that addresses the server the way a browser on this machine would
    (Host: 127.0.0.1:5001). Flask's default Host header is plain "localhost", which the
    DNS-rebinding guard correctly rejects."""

    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", BASE)
        return super().open(*args, **kwargs)


def make_client(app):
    app.test_client_class = LocalClient
    return app.test_client()


@pytest.fixture
def client(app):
    return make_client(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, token):
    res = client.post("/login", json={"token": token}, headers=ORIGIN)
    assert res.status_code == 200, res.get_json()
    page = client.get("/")
    assert page.status_code == 200
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True))
    assert match
    return match.group(1)


# ----------------------------------------------------------------- unauthenticated
@pytest.mark.parametrize("method,path", [
    ("GET", "/status"), ("POST", "/ask"), ("POST", "/speak"), ("POST", "/confirm"),
    ("POST", "/cancel"), ("POST", "/logout"),
])
def test_protected_endpoints_reject_unauthenticated_requests(client, backend, method, path):
    res = client.open(path, method=method, json={"message": "hi", "text": "hi", "action_id": "x"}, headers=ORIGIN)
    assert res.status_code == 401
    assert backend.handled == [] and backend.spoken == []


def test_public_endpoints_reveal_nothing_sensitive(client, token):
    health = client.get("/healthz")
    assert health.status_code == 200 and health.get_json() == {"ok": True, "auth_configured": True}
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert token not in login_page.get_data(as_text=True)
    assert client.get("/").status_code == 302  # HUD redirects to /login without a session


@pytest.mark.parametrize("header", ["Bearer wrong", "Basic abc", "Bearer ", "bearer"])
def test_invalid_bearer_credentials_are_rejected(client, backend, header):
    res = client.post("/ask", json={"message": "hi"}, headers={"Authorization": header})
    assert res.status_code == 401
    assert backend.handled == []


def test_invalid_login_is_rejected_and_throttled(client, token):
    for _ in range(10):
        assert client.post("/login", json={"token": "x" * 43}, headers=ORIGIN).status_code == 401
    assert client.post("/login", json={"token": token}, headers=ORIGIN).status_code == 429


def test_forged_session_cookie_is_rejected(client, backend):
    other = SessionSigner(generate_token(), 3600)
    cookie, _sid = other.issue()
    client.set_cookie(SessionSigner.COOKIE_NAME, cookie, domain="127.0.0.1")
    assert client.get("/status").status_code == 401


# ----------------------------------------------------------------- bearer clients
def test_valid_bearer_token_reaches_backend_as_api_channel(client, token, backend):
    res = client.post("/ask", json={"message": "hello"}, headers=bearer(token))
    assert res.status_code == 200 and res.get_json()["reply"] == "echo: hello"
    _text, ctx = backend.handled[0]
    assert ctx.authenticated and ctx.channel.value == "api"
    res = client.post("/ask", json={"message": "hi"}, headers={**bearer(token), "X-Ultron-Client": "wakeword"})
    assert backend.handled[-1][1].channel.value == "wakeword"


def test_state_changing_requests_require_json(client, token, backend):
    res = client.post("/ask", data="message=hi", headers=bearer(token),
                      content_type="application/x-www-form-urlencoded")
    assert res.status_code == 415
    res = client.post("/ask", data='{"message": "hi"}', headers=bearer(token), content_type="text/plain")
    assert res.status_code == 415
    assert backend.handled == []


def test_input_validation(client, token):
    assert client.post("/ask", json={"message": ""}, headers=bearer(token)).status_code == 400
    assert client.post("/ask", json={"message": 5}, headers=bearer(token)).status_code == 400
    assert client.post("/ask", json={"message": "x" * 2001}, headers=bearer(token)).status_code == 413
    assert client.post("/speak", json={"text": "x" * 1001}, headers=bearer(token)).status_code == 413


# ----------------------------------------------------------------- origin / CSRF / host
def test_cross_origin_requests_are_blocked_even_with_valid_auth(client, token, backend):
    assert client.post("/ask", json={"message": "hi"}, headers={**bearer(token), **EVIL}).status_code == 403
    assert client.get("/status", headers={**bearer(token), **EVIL}).status_code == 403
    assert backend.handled == []


def test_cross_site_fetch_metadata_is_blocked(client, token, backend):
    res = client.post("/ask", json={"message": "hi"}, headers={**bearer(token), "Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403 and backend.handled == []


def test_no_cors_headers_are_ever_sent(client, token):
    for res in (client.get("/healthz", headers=ORIGIN), client.options("/ask", headers=ORIGIN),
                client.post("/ask", json={"message": "x"}, headers={**bearer(token), **ORIGIN})):
        assert "Access-Control-Allow-Origin" not in res.headers


def test_csrf_regression_cross_origin_text_plain_post_cannot_trigger_shutdown(client, token, backend, fake_runner):
    """Regression for the audited exploit: a web page POSTing text/plain 'confirm restart' to /ask."""
    login(client, token)  # even a logged-in victim browser must not be exploitable
    for body in ('{"message": "shutdown system"}', '{"message": "confirm 1234"}'):
        res = client.post("/ask", data=body, content_type="text/plain", headers=EVIL)
        assert res.status_code == 403
        res = client.post("/ask", data=body, content_type="text/plain")  # no Origin, no CSRF token
        assert res.status_code in (403, 415)
    assert backend.handled == [] and fake_runner.calls == []


def test_session_requests_need_origin_and_csrf_token(client, token, backend):
    csrf = login(client, token)
    assert client.get("/status").status_code == 200  # read-only: cookie is enough
    assert client.post("/ask", json={"message": "hi"}, headers=ORIGIN).status_code == 403  # no CSRF
    assert client.post("/ask", json={"message": "hi"},
                       headers={**ORIGIN, "X-CSRF-Token": "forged"}).status_code == 403
    assert client.post("/ask", json={"message": "hi"}, headers={"X-CSRF-Token": csrf}).status_code == 403  # no Origin
    assert backend.handled == []
    res = client.post("/ask", json={"message": "hi"}, headers={**ORIGIN, "X-CSRF-Token": csrf})
    assert res.status_code == 200 and backend.handled[0][1].channel.value == "hud"


def test_referer_is_accepted_as_origin_fallback(client, token):
    csrf = login(client, token)
    res = client.post("/ask", json={"message": "hi"}, headers={"Referer": BASE + "/", "X-CSRF-Token": csrf})
    assert res.status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "attacker.test:5001", "127.0.0.1.nip.io:5001", "192.168.1.2:5001"])
def test_dns_rebinding_hosts_are_rejected(client, token, backend, host):
    res = client.post("/ask", json={"message": "hi"}, headers={**bearer(token), "Host": host})
    assert res.status_code == 400 and res.get_json()["error"] == "invalid_host"
    assert backend.handled == []


@pytest.mark.parametrize("host", ["127.0.0.1:5001", "localhost:5001", "[::1]:5001"])
def test_loopback_hosts_are_accepted(client, token, host):
    res = client.post("/ask", json={"message": "hi"}, headers={**bearer(token), "Host": host})
    assert res.status_code == 200


def test_login_requires_allowed_origin_and_json(client, token):
    assert client.post("/login", json={"token": token}, headers=EVIL).status_code == 403
    assert client.post("/login", json={"token": token}).status_code == 403  # no Origin/Referer at all
    assert client.post("/login", data=f"token={token}", headers=ORIGIN,
                       content_type="application/x-www-form-urlencoded").status_code == 415


def test_session_cookie_flags(client, token):
    res = client.post("/login", json={"token": token}, headers=ORIGIN)
    cookie = res.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert token not in cookie


def _generation(app) -> str:
    return app.extensions["ultron"]["session_store"].load().generation


def test_login_ticket_flow_is_single_use(client, token):
    ticket = SessionSigner(token, 3600).issue_login_ticket(_generation(client.application))
    assert client.post("/login", json={"ticket": ticket}, headers=ORIGIN).status_code == 200
    fresh = make_client(client.application)
    assert fresh.post("/login", json={"ticket": ticket}, headers=ORIGIN).status_code == 401


def test_logout_clears_session(client, token):
    csrf = login(client, token)
    assert client.post("/logout", json={}, headers={**ORIGIN, "X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/status").status_code == 401


def test_security_headers_and_no_token_in_pages(client, token):
    login(client, token)
    page = client.get("/")
    html = page.get_data(as_text=True)
    assert token not in html
    csp = page.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["Cache-Control"] == "no-store"
    for asset in ("/static/app.js", "/static/login.js"):
        body = client.get(asset).get_data(as_text=True)
        assert token not in body and "Bearer" not in body


# ----------------------------------------------------------------- confirmation via HUD
def test_hud_confirm_flow_is_bound_to_session_and_single_use(app, client, token, backend, fake_runner):
    csrf = login(client, token)
    h = {**ORIGIN, "X-CSRF-Token": csrf}
    res = client.post("/ask", json={"message": "shutdown system"}, headers=h).get_json()
    pending = res["pending_action"]
    assert set(pending) == {"action_id", "tool", "summary", "expires_in", "requested_via"}
    assert pending["tool"] == "system.shutdown" and fake_runner.calls == []

    # Another session (or the bearer client) cannot confirm this action.
    other = make_client(app)
    other_csrf = login(other, token)
    res_other = other.post("/confirm", json={"action_id": pending["action_id"]},
                           headers={**ORIGIN, "X-CSRF-Token": other_csrf})
    assert res_other.get_json()["status"] == "denied"
    assert client.post("/confirm", json={"action_id": pending["action_id"]},
                       headers=bearer(token)).get_json()["status"] == "denied"
    assert fake_runner.calls == []

    ok = client.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()
    assert ok["status"] == "completed" and len(fake_runner.calls) == 1
    again = client.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()
    assert again["status"] == "denied" and len(fake_runner.calls) == 1


def test_hud_cancel_and_gate(client, token, backend, fake_runner):
    csrf = login(client, token)
    h = {**ORIGIN, "X-CSRF-Token": csrf}
    pending = client.post("/ask", json={"message": "shutdown system"}, headers=h).get_json()["pending_action"]
    assert client.post("/cancel", json={}, headers=h).get_json()["status"] == "cancelled"
    assert client.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()["status"] == "denied"
    backend.gate_result = (False, "Security mode blocked this.")
    pending = client.post("/ask", json={"message": "shutdown system"}, headers=h).get_json()["pending_action"]
    res = client.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()
    assert res == {"reply": "Security mode blocked this.", "status": "denied"}
    assert fake_runner.calls == []
    assert client.post("/confirm", json={}, headers=h).status_code == 400


# ----------------------------------------------------------------- fail closed
def test_unconfigured_auth_fails_closed(tmp_path, backend):
    settings = load_settings({"ULTRON_DATA_DIR": str(tmp_path)})
    problem = "No API token is configured. Run `python -m ultron token init`."
    app = create_app(settings, backend=backend, token_result=TokenLoadResult(None, None, problem))
    client = make_client(app)
    assert client.get("/healthz").get_json() == {"ok": True, "auth_configured": False}
    for method, path in (("GET", "/status"), ("POST", "/ask"), ("POST", "/speak"), ("POST", "/confirm")):
        res = client.open(path, method=method, json={"message": "hi"}, headers={"Authorization": "Bearer anything"})
        assert res.status_code == 503 and res.get_json()["error"] == "auth_not_configured"
    assert client.post("/login", json={"token": "x" * 43}, headers=ORIGIN).status_code == 503
    page = client.get("/login").get_data(as_text=True)
    assert "not configured" in page and "token init" in page
    assert backend.handled == []


def test_errors_are_json_without_internals(client, token, backend, monkeypatch):
    def broken(text, ctx):
        raise RuntimeError("internal path /Users/raj/secret")

    monkeypatch.setattr(backend, "handle", broken)
    client.application.testing = False
    res = client.post("/ask", json={"message": "hi"}, headers=bearer(token))
    assert res.status_code == 500
    assert res.get_json() == {"error": "server_error", "message": "Internal server error."}
    assert client.get("/nope").get_json()["error"] == "not_found"
