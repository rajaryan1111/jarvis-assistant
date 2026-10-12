"""M1.2: persistent session revocation, token rotation, privilege separation and voice trust.

A "restart" is simulated by building a brand-new app (new store object, new signer, no
in-memory state) over the same temporary data directory. Token rotation only ever touches
token files inside ``tmp_path``; nothing here uses real credentials, real power commands or
the owner's data directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

import pytest
from conftest import owner_hud
from flask.testing import FlaskClient

from ultron import cli
from ultron.api.app import create_app
from ultron.config import load_settings
from ultron.legacy import JarvisLegacyBackend
from ultron.security.confirmation import ConfirmationStore
from ultron.security.policy import (
    Channel,
    CommandContext,
    Decision,
    InputMode,
    RiskTier,
    VoiceTrust,
    evaluate,
    voice_trust_from_env,
)
from ultron.security.session_state import (
    MAX_REVOKED_ENTRIES,
    SessionStateError,
    SessionStateStore,
    parse_state,
    token_fingerprint,
)
from ultron.security.sessions import SessionSigner, session_label
from ultron.security.tokens import TokenLoadResult, TokenSource, generate_token, load_api_token, write_token_file
from ultron.tools.registry import ToolRegistry
from ultron.tools.system import PowerTool

BASE = "http://127.0.0.1:5001"
ORIGIN = {"Origin": BASE}


class Clock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class LocalClient(FlaskClient):
    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", BASE)
        return super().open(*args, **kwargs)


class FakeBackend:
    """'shutdown system' / 'restart system' request the fake power tools; anything else echoes."""

    def __init__(self, runner, clock=None) -> None:
        store = ConfirmationStore(clock=clock) if clock else ConfirmationStore()
        self.registry = ToolRegistry(confirmations=store, platform="darwin", **({"monotonic": clock} if clock else {}))
        self.registry.register(PowerTool("shut down", runner=runner))
        self.registry.register(PowerTool("restart", runner=runner))
        self.handled: list[CommandContext] = []
        self.gate_result = (True, "")

    def handle(self, text: str, ctx: CommandContext) -> str:
        self.handled.append(ctx)
        if text == "shutdown system":
            return self.registry.request("system.shutdown", {}, ctx).message
        if text == "restart system":
            return self.registry.request("system.restart", {}, ctx).message
        if text.startswith("confirm"):
            digits = "".join(re.findall(r"\d", text))[:4] or None
            return self.registry.confirm(ctx, code=digits, gate=self.confirmation_gate).message
        return f"echo: {text}"

    def confirmation_gate(self, pending):
        return self.gate_result

    def status(self):
        return {"time": "12:00:00", "battery": 80}

    def speak(self, text, ctx):
        return "completed"

    def start_background(self):  # pragma: no cover
        pass


# ----------------------------------------------------------------------------- helpers
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("ULTRON_DATA_DIR", str(d))
    monkeypatch.delenv("ULTRON_API_TOKEN", raising=False)
    monkeypatch.delenv("ULTRON_CLIENT_TOKEN", raising=False)
    return d


@pytest.fixture
def settings(data_dir):
    s = load_settings({"ULTRON_DATA_DIR": str(data_dir)})
    write_token_file(s.api_token_file)  # a throwaway token inside tmp_path
    return s


def owner_token(settings) -> str:
    return settings.api_token_file.read_text(encoding="utf-8").strip()


def build(settings, runner, *, clock=None, backend=None, **kwargs):
    """A fresh server process over the same data directory (token files read from disk)."""
    backend = backend or FakeBackend(runner)
    app = create_app(settings, backend=backend, **({"clock": clock} if clock else {}), **kwargs)
    app.testing = True
    app.test_client_class = LocalClient
    return app, backend


def login(client, token=None, ticket=None) -> dict:
    body = {"token": token} if token else {"ticket": ticket}
    res = client.post("/login", json=body, headers=ORIGIN)
    assert res.status_code == 200, res.get_json()
    html = client.get("/").get_data(as_text=True)
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    return {**ORIGIN, "X-CSRF-Token": csrf}


def cookie_of(client) -> str:
    return client.get_cookie("ultron_session", domain="127.0.0.1").value


def replay(app, cookie: str):
    """A different browser (no shared state) presenting an old cookie value."""
    c = app.test_client()
    c.set_cookie("ultron_session", cookie, domain="127.0.0.1")
    return c


def bearer(token, **extra):
    return {"Authorization": f"Bearer {token}", **extra}


def run_cli(*argv) -> int:
    return cli.main(list(argv))


# ============================================================================ persistence across restarts
def test_logout_then_restart_then_replay_of_the_old_cookie_is_rejected(settings, fake_runner):
    app1, _ = build(settings, fake_runner)
    c = app1.test_client()
    h = login(c, owner_token(settings))
    old = cookie_of(c)
    assert replay(app1, old).get("/status").status_code == 200
    assert c.post("/logout", json={}, headers=h).status_code == 200

    app2, backend2 = build(settings, fake_runner)  # "restart": all in-memory state is gone
    r = replay(app2, old)
    assert r.get("/status").status_code == 401
    assert r.post("/ask", json={"message": "hi"}, headers=h).status_code == 401
    assert backend2.handled == []
    assert r.get("/").status_code == 302
    # The owner can still log in normally after the restart.
    fresh = app2.test_client()
    login(fresh, owner_token(settings))
    assert fresh.get("/status").status_code == 200


def test_rotation_invalidates_sessions_and_the_old_token_live_and_after_restart(settings, fake_runner):
    app1, _ = build(settings, fake_runner)
    old_token = owner_token(settings)
    c = app1.test_client()
    login(c, old_token)
    old_cookie = cookie_of(c)
    assert app1.test_client().get("/status", headers=bearer(old_token)).status_code == 200

    assert run_cli("token", "rotate") == 0  # temp token file only
    new_token = owner_token(settings)
    assert new_token != old_token

    # The *running* server picks up the rotation immediately (no restart needed).
    assert replay(app1, old_cookie).get("/status").status_code == 401
    assert app1.test_client().get("/status", headers=bearer(old_token)).status_code == 401
    assert app1.test_client().get("/status", headers=bearer(new_token)).status_code == 200

    app2, _ = build(settings, fake_runner)  # restart
    assert replay(app2, old_cookie).get("/status").status_code == 401
    assert app2.test_client().get("/status", headers=bearer(old_token)).status_code == 401
    assert app2.test_client().post("/login", json={"token": old_token}, headers=ORIGIN).status_code == 401
    login(app2.test_client(), new_token)


def test_rotation_retires_a_token_still_configured_in_the_environment(settings, fake_runner, monkeypatch, capsys):
    env_token = generate_token()
    monkeypatch.setenv("ULTRON_API_TOKEN", env_token)  # overrides the file
    assert run_cli("token", "rotate") == 0
    err = capsys.readouterr().err
    assert "ULTRON_API_TOKEN" in err and "retired" in err and env_token not in err

    app, backend = build(settings, fake_runner)  # server still configured with the old env value
    res = app.test_client().post("/ask", json={"message": "hi"}, headers=bearer(env_token))
    assert res.status_code == 503 and res.get_json()["error"] == "token_retired"
    assert env_token not in res.get_data(as_text=True)
    assert app.test_client().post("/login", json={"token": env_token}, headers=ORIGIN).status_code == 503
    assert app.test_client().get("/healthz").get_json()["auth_configured"] is False
    assert backend.handled == []


def test_rotate_output_never_contains_a_token(settings, capsys):
    old = owner_token(settings)
    assert run_cli("token", "init") == 0
    assert run_cli("token", "rotate") == 0
    out = capsys.readouterr()
    for tok in (old, owner_token(settings)):
        assert tok not in out.out and tok not in out.err


def test_revoke_all_invalidates_sessions_and_outstanding_login_links_immediately(settings, fake_runner):
    app, _ = build(settings, fake_runner)
    store = app.extensions["ultron"]["session_store"]
    ticket = SessionSigner(owner_token(settings), 3600).issue_login_ticket(store.load().generation)
    c = app.test_client()
    login(c, owner_token(settings))
    cookie = cookie_of(c)
    assert run_cli("sessions", "revoke-all") == 0
    assert replay(app, cookie).get("/status").status_code == 401
    assert app.test_client().post("/login", json={"ticket": ticket}, headers=ORIGIN).status_code == 401
    login(app.test_client(), owner_token(settings))


def test_login_link_cannot_be_replayed_after_a_restart(settings, fake_runner):
    app1, _ = build(settings, fake_runner)
    gen = app1.extensions["ultron"]["session_store"].load().generation
    ticket = SessionSigner(owner_token(settings), 3600).issue_login_ticket(gen)
    login(app1.test_client(), ticket=ticket)
    app2, _ = build(settings, fake_runner)
    assert app2.test_client().post("/login", json={"ticket": ticket}, headers=ORIGIN).status_code == 401


def test_open_hud_link_logs_in_once(settings, fake_runner, monkeypatch, capsys):
    urls: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: urls.append(url) or True)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert run_cli("open-hud") == 0
    assert owner_token(settings) not in capsys.readouterr().out
    ticket = urls[0].split("#ticket=", 1)[1]
    assert "?" not in urls[0]  # fragment only: never sent to the server or its logs
    app, _ = build(settings, fake_runner)
    login(app.test_client(), ticket=ticket)
    assert app.test_client().post("/login", json={"ticket": ticket}, headers=ORIGIN).status_code == 401


def test_deleting_the_state_file_logs_everyone_out_rather_than_resurrecting_sessions(settings, fake_runner):
    app, _ = build(settings, fake_runner)
    c = app.test_client()
    h = login(c, owner_token(settings))
    revoked = cookie_of(c)
    c.post("/logout", json={}, headers=h)
    c2 = app.test_client()
    login(c2, owner_token(settings))
    live = cookie_of(c2)
    settings.session_state_file.unlink()
    app2, _ = build(settings, fake_runner)
    assert replay(app2, revoked).get("/status").status_code == 401
    assert replay(app2, live).get("/status").status_code == 401


# ============================================================================ expired / malformed / tampered
def test_expiry_is_enforced_server_side(settings, fake_runner):
    clock = Clock()
    app, _ = build(settings, fake_runner, clock=clock)
    c = app.test_client()
    login(c, owner_token(settings))
    cookie = cookie_of(c)
    clock.t += settings.session_ttl_seconds - 5
    assert replay(app, cookie).get("/status").status_code == 200
    clock.t += 10
    assert replay(app, cookie).get("/status").status_code == 401  # even though a browser might still send it


def test_lowering_the_session_lifetime_shortens_existing_sessions(settings, data_dir, fake_runner):
    clock = Clock()
    app, _ = build(settings, fake_runner, clock=clock)
    c = app.test_client()
    login(c, owner_token(settings))
    cookie = cookie_of(c)  # issued with the default 12 h lifetime
    short = load_settings({"ULTRON_DATA_DIR": str(data_dir), "ULTRON_SESSION_HOURS": "0.1"})
    clock.t += 0.1 * 3600 + 1
    app2, _ = build(short, fake_runner, clock=clock)
    assert replay(app2, cookie).get("/status").status_code == 401


def test_cookie_from_the_future_is_rejected(settings, fake_runner):
    clock = Clock()
    gen = SessionStateStore(settings.session_state_file, clock=clock).load().generation
    future = SessionSigner(owner_token(settings), settings.session_ttl_seconds, clock=Clock(clock.t + 3600))
    cookie, _ = future.issue(gen)
    app, _ = build(settings, fake_runner, clock=clock)
    assert replay(app, cookie).get("/status").status_code == 401


@pytest.mark.parametrize("bad", [
    "", "garbage", "a.b.c", "s2.a.b.c.d", "s2.a.b.c.d.e.f", "s2..g.1.2.sig", "s1.sid.gen.1.2.sig",
    "s2.sid.gen.x.2.sig", "s2.sid.gen.1.2." + "A" * 600, "s2.s!d.gen.1.2.sig", "s2.sid.gen.-1.2.sig",
])
def test_malformed_cookies_are_rejected_without_errors(settings, fake_runner, bad):
    app, backend = build(settings, fake_runner)
    res = replay(app, bad).get("/status")
    assert res.status_code == 401 and backend.handled == []


def test_old_v1_cookie_format_is_no_longer_accepted(settings, fake_runner):
    import base64
    import hashlib
    import hmac

    token = owner_token(settings)
    key = hmac.new(token.encode(), b"ultron-session-v1", hashlib.sha256).digest()
    payload = f"sid123.{int(Clock().t) + 3600}"
    sig = base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    app, _ = build(settings, fake_runner)
    assert replay(app, f"{payload}.{sig}").get("/status").status_code == 401


@pytest.mark.parametrize("part", [1, 2, 3, 4, 5])
def test_tampering_with_any_cookie_field_is_rejected(settings, fake_runner, part):
    app, _ = build(settings, fake_runner)
    c = app.test_client()
    login(c, owner_token(settings))
    fields = cookie_of(c).split(".")
    original = fields[part]
    fields[part] = (str(int(original) + 1) if original.isdigit()
                    else ("A" if original[0] != "A" else "B") + original[1:])
    assert replay(app, ".".join(fields)).get("/status").status_code == 401


def test_cookie_signed_with_another_token_is_rejected(settings, fake_runner):
    gen = SessionStateStore(settings.session_state_file).load().generation
    forged, _ = SessionSigner(generate_token(), 3600).issue(gen)
    app, _ = build(settings, fake_runner)
    assert replay(app, forged).get("/status").status_code == 401


# ============================================================================ corrupt / unavailable state
CORRUPT = {
    "not json": "{nope",
    "empty object": "{}",
    "list": "[]",
    "extra key": None,
    "bad generation": None,
    "non-hex revoked key": None,
    "bool timestamp": None,
    "future version": None,
}


def _valid_state_dict() -> dict:
    return json.loads(parse_state(json.dumps({
        "version": 1, "generation": "g" * 32, "revoked": {}, "used_tickets": {}, "retired_tokens": {},
        "updated_at": 1,
    })).to_json())


def _corrupt_text(kind: str) -> str:
    if CORRUPT[kind] is not None:
        return CORRUPT[kind]
    d = _valid_state_dict()
    if kind == "extra key":
        d["admin"] = True
    elif kind == "bad generation":
        d["generation"] = "short"
    elif kind == "non-hex revoked key":
        d["revoked"] = {"not-a-digest": 1}
    elif kind == "bool timestamp":
        d["revoked"] = {"a" * 64: True}
    elif kind == "future version":
        d["version"] = 2
    return json.dumps(d)


@pytest.mark.parametrize("kind", list(CORRUPT))
def test_corrupt_state_fails_closed_for_every_credential(settings, fake_runner, kind):
    app, backend = build(settings, fake_runner)
    c = app.test_client()
    login(c, owner_token(settings))
    cookie = cookie_of(c)
    settings.session_state_file.write_text(_corrupt_text(kind), encoding="utf-8")
    os.chmod(settings.session_state_file, 0o600)

    for res in (
        replay(app, cookie).get("/status"),
        app.test_client().get("/status", headers=bearer(owner_token(settings))),
        app.test_client().post("/login", json={"token": owner_token(settings)}, headers=ORIGIN),
    ):
        assert res.status_code == 503, kind
        body = res.get_json()
        assert body["error"] == "session_state_unavailable"
        assert "python -m ultron sessions reset" in body["message"]
        assert owner_token(settings) not in res.get_data(as_text=True)
    assert backend.handled == []
    assert app.test_client().get("/healthz").get_json()["auth_configured"] is False
    assert app.test_client().get("/").status_code == 302

    # Documented owner recovery: reset (logs everyone out), then log in again.
    assert run_cli("sessions", "reset", "--yes") == 0
    assert replay(app, cookie).get("/status").status_code == 401
    login(app.test_client(), owner_token(settings))


@pytest.mark.parametrize("problem", ["group_readable", "symlink", "directory"])
def test_unsafe_or_unreadable_state_file_fails_closed(settings, fake_runner, tmp_path, problem):
    app, _ = build(settings, fake_runner)
    path = settings.session_state_file
    SessionStateStore(path).load()
    if problem == "group_readable":
        os.chmod(path, 0o644)
    elif problem == "symlink":
        target = tmp_path / "elsewhere.json"
        target.write_text(path.read_text())
        os.chmod(target, 0o600)
        path.unlink()
        path.symlink_to(target)
    else:
        path.unlink()
        path.mkdir()
    res = app.test_client().get("/status", headers=bearer(owner_token(settings)))
    assert res.status_code == 503 and res.get_json()["error"] == "session_state_unavailable"
    if problem != "directory":
        assert run_cli("sessions", "reset", "--yes") == 0
        assert app.test_client().get("/status", headers=bearer(owner_token(settings))).status_code == 200
        assert not path.is_symlink()


def test_failed_logout_persistence_is_reported_and_still_refused_in_this_process(settings, fake_runner, monkeypatch):
    app, _ = build(settings, fake_runner)
    c = app.test_client()
    h = login(c, owner_token(settings))
    cookie = cookie_of(c)
    store = app.extensions["ultron"]["session_store"]

    def broken(*_a, **_k):
        raise SessionStateError("the session state file could not be written (OSError).")

    monkeypatch.setattr(store, "revoke_session", broken)
    res = c.post("/logout", json={}, headers=h)
    assert res.status_code == 503 and "could not be recorded" in res.get_json()["message"]
    assert replay(app, cookie).get("/status").status_code == 401


def test_session_reset_without_confirmation_on_a_non_tty_refuses(settings, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    assert run_cli("sessions", "reset") == 1


def test_rotation_recovers_from_a_corrupt_state_file(settings, fake_runner):
    old = owner_token(settings)
    settings.session_state_file.parent.mkdir(parents=True, exist_ok=True)
    settings.session_state_file.write_text("{corrupt", encoding="utf-8")
    os.chmod(settings.session_state_file, 0o600)
    assert run_cli("token", "rotate") == 0  # rotation is itself a recovery step
    app, _ = build(settings, fake_runner)
    assert app.test_client().get("/status", headers=bearer(old)).status_code == 401
    assert app.test_client().get("/status", headers=bearer(owner_token(settings))).status_code == 200


# ============================================================================ the state store itself
def test_state_file_is_private_and_holds_no_secrets(settings, fake_runner):
    app, _ = build(settings, fake_runner)
    c = app.test_client()
    h = login(c, owner_token(settings))
    cookie = cookie_of(c)
    session_id = cookie.split(".")[1]
    c.post("/logout", json={}, headers=h)
    run_cli("token", "rotate")
    text = settings.session_state_file.read_text()
    assert oct(settings.session_state_file.stat().st_mode & 0o777) == "0o600"
    assert session_id not in text and cookie not in text
    assert owner_token(settings) not in text


def test_store_prunes_expired_entries_and_caps_growth(tmp_path):
    clock = Clock()
    store = SessionStateStore(tmp_path / "s.json", clock=clock)
    store.revoke_session("old-session", int(clock.t) + 10)
    store.revoke_session("live-session", int(clock.t) + 10_000)
    clock.t += 1000
    store.revoke_session("another", int(clock.t) + 10)
    state = store.load()
    assert not state.is_revoked("old-session") and state.is_revoked("live-session")

    gen = state.generation
    for i in range(MAX_REVOKED_ENTRIES + 1):
        state.revoked[f"{i:064x}"] = int(clock.t) + 10_000
    store.path.write_text(state.to_json())
    store.revoke_session("one-more", int(clock.t) + 10_000)
    assert store.load().generation != gen  # fail safe: everybody is logged out instead


def test_concurrent_revocations_are_all_persisted(tmp_path):
    store = SessionStateStore(tmp_path / "s.json")
    expiry = int(Clock().t) + 10**9
    threads = [threading.Thread(target=store.revoke_session, args=(f"s{i}", expiry)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fresh = SessionStateStore(tmp_path / "s.json").load()
    assert all(fresh.is_revoked(f"s{i}") for i in range(20))


def test_token_source_rereads_rotated_files(settings):
    source = TokenSource(settings, env={})
    first = source.owner().token
    write_token_file(settings.api_token_file, overwrite=True)
    assert source.owner().token != first and source.owner().token == owner_token(settings)


# ============================================================================ logging hygiene / URLs
def test_tokens_and_cookie_values_are_never_logged(settings, fake_runner, caplog):
    caplog.set_level(logging.DEBUG)
    app, _ = build(settings, fake_runner)
    c = app.test_client()
    h = login(c, owner_token(settings))
    cookie = cookie_of(c)
    session_id = cookie.split(".")[1]
    c.post("/ask", json={"message": "shutdown system"}, headers=h)
    c.post("/logout", json={}, headers=h)
    app.test_client().post("/login", json={"token": "x" * 43}, headers=ORIGIN)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert owner_token(settings) not in text and cookie not in text and session_id not in text
    assert session_label(session_id) in text  # logged only as a non-reversible label


def test_tokens_are_never_accepted_in_urls(settings, fake_runner):
    app, backend = build(settings, fake_runner)
    tok = owner_token(settings)
    for path in (f"/status?token={tok}", f"/status?access_token={tok}", f"/status?api_token={tok}"):
        assert app.test_client().get(path).status_code == 401
    res = app.test_client().post(f"/login?token={tok}", json={}, headers=ORIGIN)
    assert res.status_code == 401 and "ultron_session" not in res.headers.get("Set-Cookie", "")
    assert backend.handled == []


def test_rejected_credentials_are_not_echoed(settings, fake_runner):
    app, _ = build(settings, fake_runner)
    wrong = generate_token()
    for res in (
        app.test_client().post("/ask", json={"message": "hi"}, headers=bearer(wrong)),
        app.test_client().post("/login", json={"token": wrong}, headers=ORIGIN),
    ):
        assert wrong not in res.get_data(as_text=True)


# ============================================================================ authorization separation
@pytest.fixture
def client_token(settings):
    write_token_file(settings.client_token_file)
    return settings.client_token_file.read_text().strip()


def test_client_token_can_send_commands_but_never_log_in_or_approve(settings, fake_runner, client_token):
    app, backend = build(settings, fake_runner)
    api = app.test_client()
    assert api.post("/ask", json={"message": "hi"}, headers=bearer(client_token)).status_code == 200
    ctx = backend.handled[-1]
    assert ctx.principal == "client-token" and ctx.channel is Channel.WAKEWORD and ctx.is_voice
    assert not ctx.can_confirm
    assert app.test_client().post("/login", json={"token": client_token}, headers=ORIGIN).status_code == 401

    res = api.post("/ask", json={"message": "shutdown system"}, headers=bearer(client_token)).get_json()
    pending = res["pending_action"]
    assert "Press CONFIRM in the signed-in HUD" in res["reply"]
    assert not re.search(r"confirm \d{4}", res["reply"])  # no code for voice / API requests
    out = api.post("/confirm", json={"action_id": pending["action_id"]}, headers=bearer(client_token)).get_json()
    assert out["status"] == "denied"
    # Claiming to be typed text does not upgrade a client-token request.
    api.post("/ask", json={"message": "hi", "input": "text"}, headers=bearer(client_token))
    assert backend.handled[-1].is_voice
    assert fake_runner.calls == []


def test_owner_bearer_token_cannot_approve_either(settings, fake_runner):
    app, backend = build(settings, fake_runner)
    api = app.test_client()
    tok = owner_token(settings)
    res = api.post("/ask", json={"message": "shutdown system"}, headers=bearer(tok)).get_json()
    assert api.post("/confirm", json={"action_id": res["pending_action"]["action_id"]},
                    headers=bearer(tok)).get_json()["status"] == "denied"
    assert "only accepted from the signed-in HUD" in api.post(
        "/ask", json={"message": "confirm 1234"}, headers=bearer(tok)).get_json()["reply"]
    assert fake_runner.calls == []


def test_voice_and_api_requests_are_approved_only_in_the_hud_by_exact_id(settings, fake_runner, client_token):
    app, backend = build(settings, fake_runner)
    voice = app.test_client()
    hud = app.test_client()
    h = login(hud, owner_token(settings))

    first = voice.post("/ask", json={"message": "shutdown system"}, headers=bearer(client_token)).get_json()
    second = voice.post("/ask", json={"message": "restart system"}, headers=bearer(client_token)).get_json()
    listed = hud.get("/pending").get_json()["pending"]
    assert [p["action_id"] for p in listed] == [second["pending_action"]["action_id"]]  # first was replaced
    assert listed[0]["requested_via"] == "wakeword" and "code" not in listed[0]

    for stale_or_wrong in (first["pending_action"]["action_id"], "nope", listed[0]["action_id"][:-1] + "x"):
        assert hud.post("/confirm", json={"action_id": stale_or_wrong}, headers=h).get_json()["status"] == "denied"
    # A typed code cannot approve a delegated request (it never had a visible code).
    assert "no pending" in hud.post("/ask", json={"message": "confirm 1234"}, headers=h).get_json()["reply"].lower()
    assert fake_runner.calls == []

    ok = hud.post("/confirm", json={"action_id": listed[0]["action_id"],
                                    "tool": "system.shutdown", "arguments": {"x": 1}}, headers=h).get_json()
    assert ok["status"] == "completed" and fake_runner.calls[0][-1].endswith("to restart")  # exactly as requested
    again = hud.post("/confirm", json={"action_id": listed[0]["action_id"]}, headers=h).get_json()
    assert again["status"] == "denied" and len(fake_runner.calls) == 1


def test_delegated_requests_expire(settings, fake_runner, client_token):
    clock = Clock(1000.0)
    backend = FakeBackend(fake_runner, clock=clock)
    app, _ = build(settings, fake_runner, backend=backend)
    pending = app.test_client().post("/ask", json={"message": "shutdown system"},
                                     headers=bearer(client_token)).get_json()["pending_action"]
    assert pending["expires_in"] == 120
    hud = app.test_client()
    h = login(hud, owner_token(settings))
    clock.t += 121
    res = hud.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()
    assert res["status"] == "denied" and fake_runner.calls == []


def test_a_hud_session_cannot_approve_another_hud_sessions_request(settings, fake_runner):
    app, _ = build(settings, fake_runner)
    alice, bob = app.test_client(), app.test_client()
    ha, hb = login(alice, owner_token(settings)), login(bob, owner_token(settings))
    pending = alice.post("/ask", json={"message": "shutdown system"}, headers=ha).get_json()["pending_action"]
    assert bob.get("/pending").get_json()["pending"] == []
    assert bob.post("/confirm", json={"action_id": pending["action_id"]}, headers=hb).get_json()["status"] == "denied"
    assert bob.post("/cancel", json={"action_id": pending["action_id"]}, headers=hb).get_json()["status"] == "cancelled"
    assert alice.get("/pending").get_json()["pending"]  # bob's cancel did not touch alice's own request
    assert fake_runner.calls == []


def test_hud_can_cancel_a_voice_request(settings, fake_runner, client_token):
    app, _ = build(settings, fake_runner)
    pending = app.test_client().post("/ask", json={"message": "shutdown system"},
                                     headers=bearer(client_token)).get_json()["pending_action"]
    hud = app.test_client()
    h = login(hud, owner_token(settings))
    assert hud.post("/cancel", json={"action_id": pending["action_id"]}, headers=h).get_json()["reply"] == \
        "Okay, I cancelled that request."
    assert hud.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()["status"] == "denied"


def test_revoked_or_duplicate_client_tokens_are_refused(settings, fake_runner, client_token, capsys):
    app, _ = build(settings, fake_runner)
    assert app.test_client().get("/status", headers=bearer(client_token)).status_code == 200
    assert run_cli("token", "revoke", "--client") == 0
    assert client_token not in capsys.readouterr().out
    assert app.test_client().get("/status", headers=bearer(client_token)).status_code == 401
    settings.client_token_file.write_text(owner_token(settings) + "\n")
    os.chmod(settings.client_token_file, 0o600)
    app2, backend2 = build(settings, fake_runner)
    app2.test_client().post("/ask", json={"message": "hi"}, headers=bearer(owner_token(settings)))
    assert backend2.handled[-1].principal == "api-token"  # same value is treated as the owner, not a client


@pytest.mark.parametrize("path", ["/token/rotate", "/token", "/sessions/revoke-all", "/sessions/reset",
                                  "/security/disable", "/admin"])
def test_privileged_administration_is_not_reachable_over_http(settings, fake_runner, path):
    app, _ = build(settings, fake_runner)
    c = app.test_client()
    h = login(c, owner_token(settings))
    before = owner_token(settings)
    assert c.post(path, json={}, headers=h).status_code in (404, 405)
    assert c.get(path).status_code in (404, 405)
    assert owner_token(settings) == before


def test_a_valid_session_does_not_bypass_the_risk_policy(settings, fake_runner):
    app, _ = build(settings, fake_runner)
    c = app.test_client()
    h = login(c, owner_token(settings))
    res = c.post("/ask", json={"message": "shutdown system"}, headers=h).get_json()
    assert "pending_action" in res and fake_runner.calls == []


# ============================================================================ voice trust policy (unit)
HUD_TEXT = owner_hud()
HUD_VOICE = CommandContext("session:owner", Channel.HUD, authenticated=True, input_mode=InputMode.VOICE)
WAKE = CommandContext("client-token", Channel.WAKEWORD, authenticated=True, input_mode=InputMode.VOICE)
API = CommandContext("api-token", Channel.API, authenticated=True)
LOCAL = CommandContext.local_voice()
UNTRUSTED = CommandContext.untrusted("transcript")


@pytest.mark.parametrize("ctx,trust,risk,expected", [
    # class 1: read-only for everyone
    (LOCAL, VoiceTrust.OBSERVE, RiskTier.OBSERVE, Decision.ALLOW),
    (UNTRUSTED, VoiceTrust.ROUTINE, RiskTier.OBSERVE, Decision.ALLOW),
    # class 2: routine actions follow the configured voice trust policy
    (LOCAL, VoiceTrust.ROUTINE, RiskTier.ROUTINE, Decision.ALLOW),
    (WAKE, VoiceTrust.ROUTINE, RiskTier.ROUTINE, Decision.ALLOW),
    (HUD_VOICE, VoiceTrust.ROUTINE, RiskTier.ROUTINE, Decision.ALLOW),
    (LOCAL, VoiceTrust.OBSERVE, RiskTier.ROUTINE, Decision.DENY),
    (WAKE, VoiceTrust.OBSERVE, RiskTier.ROUTINE, Decision.DENY),
    (HUD_VOICE, VoiceTrust.OBSERVE, RiskTier.ROUTINE, Decision.DENY),
    (HUD_TEXT, VoiceTrust.OBSERVE, RiskTier.ROUTINE, Decision.ALLOW),
    (API, VoiceTrust.OBSERVE, RiskTier.ROUTINE, Decision.ALLOW),
    (UNTRUSTED, VoiceTrust.ROUTINE, RiskTier.ROUTINE, Decision.DENY),
    # classes 3/4: voice may only *request* (approval happens in the HUD); the standalone loop is denied
    (WAKE, VoiceTrust.ROUTINE, RiskTier.CONSEQUENTIAL, Decision.CONFIRM),
    (HUD_VOICE, VoiceTrust.ROUTINE, RiskTier.DESTRUCTIVE, Decision.CONFIRM),
    (LOCAL, VoiceTrust.ROUTINE, RiskTier.CONSEQUENTIAL, Decision.DENY),
    (LOCAL, VoiceTrust.ROUTINE, RiskTier.DESTRUCTIVE, Decision.DENY),
    (WAKE, VoiceTrust.OBSERVE, RiskTier.DESTRUCTIVE, Decision.DENY),
    (HUD_TEXT, VoiceTrust.ROUTINE, RiskTier.DESTRUCTIVE, Decision.CONFIRM),
    (UNTRUSTED, VoiceTrust.ROUTINE, RiskTier.DESTRUCTIVE, Decision.DENY),
    (HUD_TEXT, VoiceTrust.ROUTINE, RiskTier.FORBIDDEN, Decision.DENY),
])
def test_voice_trust_decision_table(ctx, trust, risk, expected):
    assert evaluate(risk, ctx, voice_trust=trust).decision is expected


def test_only_typed_hud_sessions_can_confirm():
    assert HUD_TEXT.can_confirm
    for ctx in (HUD_VOICE, WAKE, API, LOCAL, UNTRUSTED, CommandContext.internal("reminders"),
                CommandContext("session:x", Channel.HUD, authenticated=False)):
        assert not ctx.can_confirm, ctx
    assert LOCAL.is_voice and WAKE.is_voice and HUD_VOICE.is_voice and not API.is_voice


@pytest.mark.parametrize("raw,expected", [
    (None, VoiceTrust.ROUTINE), ("", VoiceTrust.ROUTINE), ("routine", VoiceTrust.ROUTINE),
    (" OBSERVE ", VoiceTrust.OBSERVE), ("everything", VoiceTrust.OBSERVE), ("confirm", VoiceTrust.OBSERVE),
])
def test_voice_trust_setting_fails_towards_the_restrictive_mode(raw, expected):
    env = {} if raw is None else {"ULTRON_VOICE_TRUST": raw}
    assert voice_trust_from_env(env) is expected


# ============================================================================ voice end to end (legacy router)
@pytest.fixture
def hud_app(legacy, settings):
    backend = JarvisLegacyBackend()
    backend._main = legacy
    app, _ = build(settings, None, backend=backend)
    return app


def test_routine_voice_commands_still_just_work(legacy, fake_runner):
    assert legacy.handle_command("volume 30", LOCAL) == "Volume set to 30 percent."
    assert legacy.handle_command("what time is it", LOCAL)
    assert len(fake_runner.calls) == 1


def test_wakeword_routine_vs_sensitive_requests(legacy, hud_app, settings, fake_runner, client_token):
    voice = hud_app.test_client()
    assert voice.post("/ask", json={"message": "volume 20"},
                      headers=bearer(client_token)).get_json()["reply"] == "Volume set to 20 percent."
    res = voice.post("/ask", json={"message": "shutdown system"}, headers=bearer(client_token)).get_json()
    assert res["pending_action"]["tool"] == "system.shutdown" and len(fake_runner.calls) == 1
    # The listener hears "confirm ..." (from anyone, or a recording): never an approval.
    for phrase in ("confirm", "confirm shutdown", "yes confirm shutdown 1234"):
        reply = voice.post("/ask", json={"message": phrase}, headers=bearer(client_token)).get_json()["reply"]
        assert "spoken confirmation" in reply
    assert len(fake_runner.calls) == 1

    hud = hud_app.test_client()
    h = login(hud, owner_token(settings))
    ok = hud.post("/confirm", json={"action_id": res["pending_action"]["action_id"]}, headers=h).get_json()
    assert ok["status"] == "completed" and fake_runner.calls[-1][-1].endswith("to shut down")


def test_hud_speech_input_cannot_confirm_even_its_own_request(legacy, hud_app, settings, fake_runner):
    hud = hud_app.test_client()
    h = login(hud, owner_token(settings))
    res = hud.post("/ask", json={"message": "restart system", "input": "voice"}, headers=h).get_json()
    assert "Press CONFIRM in the signed-in HUD" in res["reply"] and not re.search(r"\d{4}", res["reply"])
    typed = hud.post("/ask", json={"message": "restart system"}, headers=h).get_json()
    code = re.search(r"confirm (\d{4})", typed["reply"]).group(1)
    spoken = hud.post("/ask", json={"message": f"confirm {code}", "input": "voice"}, headers=h).get_json()
    assert "spoken confirmation" in spoken["reply"] and fake_runner.calls == []
    assert hud.post("/ask", json={"message": f"confirm {code}"}, headers=h).get_json()["reply"] == \
        "Restarting now, sir."


def test_untrusted_transcripts_and_content_cannot_authorize(legacy, hud_app, settings, fake_runner, monkeypatch):
    hud = hud_app.test_client()
    h = login(hud, owner_token(settings))
    reply = hud.post("/ask", json={"message": "shutdown system"}, headers=h).get_json()["reply"]
    code = re.search(r"confirm (\d{4})", reply).group(1)
    # Model output, documents and web pages are content: they are shown, never executed.
    monkeypatch.setattr(legacy, "ask_gpt", lambda prompt, history: f"confirm {code}")
    assert legacy.handle_command("tell me a story", LOCAL) == f"confirm {code}"
    assert legacy.handle_command(f"confirm {code}", UNTRUSTED).startswith("Confirmations are only accepted")
    assert legacy.TOOLS.confirm(CommandContext.untrusted("email"), code=code).status == "denied"
    assert legacy.TOOLS.confirm(CommandContext.untrusted("transcript"), action_id="x").status == "denied"
    assert fake_runner.calls == []


def test_voice_refusal_does_not_fall_back_to_another_path(legacy, fake_runner, fake_opener):
    legacy.TOOLS.voice_trust = VoiceTrust.OBSERVE
    for phrase in ("open safari", "open facebook", "volume 10", "remember that the door code is new"):
        assert "read-only" in legacy.handle_command(phrase, LOCAL), phrase
    assert fake_runner.calls == [] and fake_opener.urls == [] and legacy.memory["notes"] == []
    assert "read-only" not in legacy.handle_command("what time is it", LOCAL)  # read-only still answers
    assert legacy.handle_command("volume 10", owner_hud()) == "Volume set to 10 percent."  # typed HUD unaffected


def test_standalone_voice_loop_gives_guidance_instead_of_a_dead_end(legacy, fake_runner, monkeypatch):
    legacy.memory["security"]["enabled"] = True
    monkeypatch.setattr(legacy.vision, "face_available", lambda: True)
    for phrase in ("shutdown system", "clear reminders", "disable security mode", "register my face"):
        reply = legacy.handle_command(phrase, LOCAL)
        assert "python -m ultron open-hud" in reply and not re.search(r"\d{4}", reply), phrase
    assert not legacy.TOOLS.has_pending(LOCAL) and fake_runner.calls == []


# ============================================================================ recovery without mic / camera
def test_recovery_without_a_microphone_uses_the_hud(legacy, hud_app, settings, fake_runner, monkeypatch):
    monkeypatch.setattr(legacy, "listen", lambda: "")  # no microphone
    hud = hud_app.test_client()
    h = login(hud, owner_token(settings))
    pending = hud.post("/ask", json={"message": "clear reminders"}, headers=h).get_json()["pending_action"]
    res = hud.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()
    assert res["status"] == "completed"


def test_recovery_without_a_camera(legacy, hud_app, settings, monkeypatch):
    legacy.memory["security"]["enabled"] = True
    monkeypatch.setattr(legacy.vision, "face_available", lambda: False)  # no camera / vision extra
    hud = hud_app.test_client()
    h = login(hud, owner_token(settings))
    pending = hud.post("/ask", json={"message": "disable security mode"}, headers=h).get_json()["pending_action"]
    res = hud.post("/confirm", json={"action_id": pending["action_id"]}, headers=h).get_json()
    assert res["status"] == "denied" and "ultron security disable" in res["reply"]
    assert legacy.memory["security"]["enabled"] is True  # fails closed ...

    # ... and the documented owner recovery (a shell as the same OS user) still works.
    saved: list[dict] = []
    monkeypatch.setattr(legacy, "load_memory", lambda: json.loads(json.dumps(legacy.memory)))
    monkeypatch.setattr(legacy, "save_memory", lambda mem: saved.append(json.loads(json.dumps(mem))))
    assert legacy.disable_security_mode_offline() == "Security mode disabled."
    assert saved[-1]["security"]["enabled"] is False


def test_doctor_reports_session_state_and_client_token(settings, capsys):
    rc = run_cli("doctor")
    out = capsys.readouterr().out
    assert "session state readable" in out and "no client token" in out
    assert owner_token(settings) not in out
    assert rc in (0, 1)


def test_loader_still_reports_missing_and_insecure_token_files(data_dir):
    s = load_settings({"ULTRON_DATA_DIR": str(data_dir)})
    assert "No API token" in (load_api_token(s, env={}).problem or "")
    write_token_file(s.api_token_file)
    os.chmod(s.api_token_file, 0o644)
    assert "readable by other users" in (load_api_token(s, env={}).problem or "")


def test_retired_fingerprints_are_not_reversible(tmp_path):
    tok = generate_token()
    store = SessionStateStore(tmp_path / "s.json")
    store.retire_tokens([tok], revoke_sessions=False)
    text = (tmp_path / "s.json").read_text()
    assert tok not in text and token_fingerprint(tok) in text
    assert store.load().is_retired(tok) and not store.load().is_retired(generate_token())


def test_pinned_token_result_still_supported(settings, fake_runner):
    tok = generate_token()
    app, _ = build(settings, fake_runner, token_result=TokenLoadResult(tok, "env", None))
    assert app.test_client().get("/status", headers=bearer(tok)).status_code == 200


def test_raising_the_session_lifetime_does_not_extend_existing_sessions(settings, data_dir, fake_runner):
    clock = Clock()
    short = load_settings({"ULTRON_DATA_DIR": str(data_dir), "ULTRON_SESSION_HOURS": "0.1"})
    app, _ = build(short, fake_runner, clock=clock)
    c = app.test_client()
    login(c, owner_token(settings))
    cookie = cookie_of(c)  # signed expiry: 6 minutes
    clock.t += 0.1 * 3600 + 1
    app2, _ = build(settings, fake_runner, clock=clock)  # restarted with the 12 h default
    assert replay(app2, cookie).get("/status").status_code == 401


def test_a_revoked_client_token_still_set_in_the_environment_is_refused(settings, fake_runner, monkeypatch):
    tok = generate_token()
    monkeypatch.setenv("ULTRON_CLIENT_TOKEN", tok)
    app, _ = build(settings, fake_runner)
    assert app.test_client().get("/status", headers=bearer(tok)).status_code == 200
    assert run_cli("token", "revoke", "--client") == 0
    assert app.test_client().get("/status", headers=bearer(tok)).status_code == 401
    app2, _ = build(settings, fake_runner)  # and after a restart
    assert app2.test_client().get("/status", headers=bearer(tok)).status_code == 401


@pytest.mark.parametrize("param", ["token", "access_token", "api_token", "client-token"])
def test_token_like_query_parameters_are_redacted_from_logs(param):
    from ultron.security.redact import redact

    tok = generate_token()  # e.g. a freshly rotated token the log filter was never told about
    line = redact(f'"GET /status?x=1&{param}={tok} HTTP/1.1" 401')
    assert tok not in line and "[REDACTED]" in line


@pytest.mark.parametrize("where", ["bearer", "login", "cookie", "csrf", "ticket"])
def test_non_ascii_credentials_are_rejected_cleanly(settings, fake_runner, where):
    app, backend = build(settings, fake_runner)
    c = app.test_client()
    odd = "t\u00e9st-\u2603" * 5
    if where == "bearer":
        res = c.get("/status", headers={"Authorization": "Bearer " + odd})
    elif where == "login":
        res = c.post("/login", json={"token": odd}, headers=ORIGIN)
    elif where == "ticket":
        res = c.post("/login", json={"ticket": "t2.1.abc.gen." + odd}, headers=ORIGIN)
    elif where == "cookie":
        gen = app.extensions["ultron"]["session_store"].load().generation
        good, _ = SessionSigner(owner_token(settings), 3600).issue(gen)
        res = replay(app, good.rsplit(".", 1)[0] + "." + odd).get("/status")
    else:
        login(c, owner_token(settings))
        res = c.post("/ask", json={"message": "hi"}, headers={**ORIGIN, "X-CSRF-Token": odd})
    assert res.status_code in (401, 403), res.status_code
    assert backend.handled == []
