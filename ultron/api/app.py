"""Local HUD/API server (Flask) with authentication and cross-origin protection.

Security model (see README "Security model"):

* Binds to 127.0.0.1 by default; debug off; refuses non-loopback unless
  explicitly allowed (``ultron.config.Settings.validate``).
* ``Host`` header allowlist (DNS-rebinding defence) and ``Origin`` allowlist on
  every request; no CORS headers are ever sent.
* Protected endpoints accept either
  - ``Authorization: Bearer <owner API token or client token>`` (wake-word listener,
    scripts), or
  - the HUD session cookie (HttpOnly, SameSite=Strict, HMAC-signed, bound to the
    persistent session generation) **plus**, for state-changing requests, an allowed
    ``Origin`` and a per-session ``X-CSRF-Token`` header.
* Authentication is not authorization: bearer clients and voice input can request
  actions but only a HUD session (typed/clicked) can approve a pending high-impact
  action (``CommandContext.can_confirm``). Only the owner token can log in.
* Logout, "revoke all" and token rotation persist in the session state file and survive
  restarts; token files are re-read when they change. Corrupt or unavailable session
  state fails closed (503) with an owner recovery command.
* State-changing endpoints require ``Content-Type: application/json``.
* If no valid API token is configured, protected endpoints fail closed (503).
* Tokens are never accepted in URLs, never rendered into pages, sent to the browser or
  logged; session ids are logged only as non-reversible labels.
"""

from __future__ import annotations

import functools
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, make_response, redirect, render_template, request

from ultron.config import ConfigurationError, Settings, load_dotenv_if_enabled, load_settings
from ultron.legacy import Backend, JarvisLegacyBackend
from ultron.security.policy import Channel, CommandContext, InputMode
from ultron.security.redact import install_log_redaction
from ultron.security.session_state import SessionState, SessionStateError, SessionStateStore
from ultron.security.sessions import LoginThrottle, SessionClaims, SessionSigner, secret_equals, session_label
from ultron.security.tokens import TokenLoadResult, TokenSource, load_api_token, load_client_token

log = logging.getLogger("ultron.api")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_MESSAGE_CHARS = 2000
MAX_SPEAK_CHARS = 1000
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; media-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
)

LOGIN_TICKET_MAX_AGE = 120
RETIRED_NOTICE = (
    "The configured API token was rotated and is no longer accepted. If ULTRON_API_TOKEN is set in "
    "your shell or .env, remove it (the token file is used instead), then restart the server."
)


def _error(status: int, code: str, message: str):
    resp = jsonify({"error": code, "message": message})
    resp.status_code = status
    return resp


class _AuthUnavailable(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _Auth:
    owner_token: str
    client_token: str | None
    signer: SessionSigner
    state: SessionState


def create_app(
    settings: Settings | None = None,
    *,
    backend: Backend | None = None,
    token_result: TokenLoadResult | None = None,
    client_token_result: TokenLoadResult | None = None,
    session_store: SessionStateStore | None = None,
    clock: Callable[[], float] = time.time,
) -> Flask:
    settings = settings or load_settings()
    backend = backend or JarvisLegacyBackend()
    tokens = TokenSource(settings, owner=token_result, client=client_token_result)
    store = session_store or SessionStateStore(settings.session_state_file, clock=clock)

    app = Flask(
        "ultron",
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.update(MAX_CONTENT_LENGTH=16 * 1024, JSON_SORT_KEYS=False)
    app.extensions["ultron"] = {"settings": settings, "backend": backend, "session_store": store}

    throttle = LoginThrottle(clock=clock)
    signer_cache: dict[str, SessionSigner] = {}
    # Defence in depth: logged-out session ids are also refused in memory, so a logout still
    # holds in this process even if recording it on disk failed (that failure is reported).
    logged_out: set[str] = set()

    def _signer_for(owner_token: str) -> SessionSigner:
        signer = signer_cache.get(owner_token)
        if signer is None:
            signer_cache.clear()  # the token changed (rotation): forget the old keys
            signer = signer_cache[owner_token] = SessionSigner(owner_token, settings.session_ttl_seconds, clock=clock)
        return signer

    def _auth() -> _Auth:
        """Current credentials and session state. Raises _AuthUnavailable to fail closed."""
        owner = tokens.owner()
        if not owner.ok or owner.token is None:
            raise _AuthUnavailable("auth_not_configured", owner.problem or "Authentication is not configured.")
        try:
            state = store.load()
        except SessionStateError as exc:
            log.error("session state unavailable: %s", exc.problem)
            raise _AuthUnavailable("session_state_unavailable", exc.user_message) from None
        if state.is_retired(owner.token):
            log.error("the configured owner API token has been retired by `token rotate`")
            raise _AuthUnavailable("token_retired", RETIRED_NOTICE)
        client = tokens.client()
        client_token = client.token if client.ok else None
        if client_token is not None and (state.is_retired(client_token) or client_token == owner.token):
            client_token = None
        return _Auth(owner.token, client_token, _signer_for(owner.token), state)

    def _try_auth() -> tuple[_Auth | None, str]:
        try:
            return _auth(), ""
        except _AuthUnavailable as exc:
            return None, exc.message

    allowed_hosts = settings.allowed_hosts
    allowed_origins = settings.allowed_origins

    # ------------------------------------------------------------------ guards
    def _request_origin() -> str | None:
        origin = request.headers.get("Origin")
        if origin:
            return origin.rstrip("/").lower()
        referer = request.headers.get("Referer")
        if referer:
            parts = urlsplit(referer)
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}".lower()
        return None

    @app.before_request
    def _host_and_origin_guard():
        if request.host.lower() not in allowed_hosts:
            return _error(400, "invalid_host", "This server only answers requests addressed to localhost.")
        origin = request.headers.get("Origin")
        if origin is not None and origin.rstrip("/").lower() not in allowed_origins:
            return _error(403, "forbidden_origin", "Cross-origin requests are not allowed.")
        if request.method not in SAFE_METHODS and request.headers.get("Sec-Fetch-Site") == "cross-site":
            return _error(403, "forbidden_origin", "Cross-site requests are not allowed.")
        return None

    @app.after_request
    def _security_headers(resp):
        resp.headers.pop("Access-Control-Allow-Origin", None)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def _session(auth: _Auth) -> SessionClaims | None:
        """Signature, generation, expiry/lifetime (server-side) and persistent revocation."""
        claims = auth.signer.parse(request.cookies.get(SessionSigner.COOKIE_NAME), auth.state.generation)
        if claims is None or claims.session_id in logged_out or auth.state.is_revoked(claims.session_id):
            return None
        return claims

    def protected(state_changing: bool) -> Callable:
        def decorator(view: Callable) -> Callable:
            @functools.wraps(view)
            def wrapper(*args, **kwargs):
                try:
                    auth = _auth()
                except _AuthUnavailable as exc:
                    return _error(503, exc.code, exc.message)
                g.auth = auth
                g.session = None

                auth_header = request.headers.get("Authorization")
                if auth_header is not None:
                    scheme, _, presented = auth_header.partition(" ")
                    presented = presented.strip()
                    is_bearer = scheme.lower() == "bearer" and bool(presented)
                    owner_ok = is_bearer and secret_equals(presented, auth.owner_token)
                    client_ok = is_bearer and auth.client_token is not None and secret_equals(
                        presented, auth.client_token)
                    if owner_ok:
                        wakeword = request.headers.get("X-Ultron-Client") == "wakeword"
                        g.ctx = CommandContext(
                            principal="api-token", channel=Channel.WAKEWORD if wakeword else Channel.API,
                            authenticated=True, input_mode=InputMode.VOICE if wakeword else InputMode.TEXT,
                        )
                    elif client_ok:
                        # The limited client token is the voice-device credential: always voice-trust rules.
                        g.ctx = CommandContext(principal="client-token", channel=Channel.WAKEWORD,
                                               authenticated=True, input_mode=InputMode.VOICE)
                    else:
                        return _error(401, "unauthorized", "Invalid credentials.")
                else:
                    claims = _session(auth)
                    if claims is None:
                        return _error(401, "unauthorized", "Please log in to the HUD first (open /login).")
                    if state_changing:
                        if _request_origin() not in allowed_origins:
                            return _error(403, "forbidden_origin", "Missing or disallowed request origin.")
                        if not auth.signer.check_csrf(claims.session_id, request.headers.get("X-CSRF-Token")):
                            return _error(403, "csrf_failed", "Missing or invalid CSRF token. Reload the HUD.")
                    g.session = claims
                    g.ctx = CommandContext(principal=f"session:{session_label(claims.session_id)}",
                                           channel=Channel.HUD, authenticated=True)

                if state_changing and request.mimetype != "application/json":
                    return _error(415, "unsupported_media_type", "Requests must use Content-Type: application/json.")
                return view(*args, **kwargs)

            return wrapper

        return decorator

    def _json_body() -> dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _pending_payload(ctx: CommandContext) -> dict[str, Any] | None:
        for kind, value in reversed(ctx.events):
            if kind == "pending_action":
                return backend.registry.describe_pending(value)
        return None

    def _agent_payload(ctx: CommandContext) -> dict[str, Any] | None:
        for kind, value in reversed(ctx.events):
            if kind == "agent_report":
                return value.public()
        return None

    # ------------------------------------------------------------------ public
    @app.get("/healthz")
    def healthz():
        auth, _ = _try_auth()
        return jsonify({"ok": True, "auth_configured": auth is not None})

    @app.get("/login")
    def login_page():
        auth, problem = _try_auth()
        if auth is not None and _session(auth) is not None:
            return redirect("/")
        return render_template("login.html", configured=auth is not None, setup_message=problem)

    @app.post("/login")
    def login():
        try:
            auth = _auth()
        except _AuthUnavailable as exc:
            return _error(503, exc.code, exc.message)
        if _request_origin() not in allowed_origins:
            return _error(403, "forbidden_origin", "Missing or disallowed request origin.")
        if request.mimetype != "application/json":
            return _error(415, "unsupported_media_type", "Requests must use Content-Type: application/json.")
        if not throttle.allowed():
            return _error(429, "too_many_attempts", "Too many failed login attempts. Wait a few minutes.")
        body = _json_body()
        token = body.get("token")
        ticket = body.get("ticket")
        ok = False
        try:
            if isinstance(token, str) and token:
                # Only the owner token can open a HUD session; the client token never can.
                ok = secret_equals(token.strip(), auth.owner_token)
            elif isinstance(ticket, str) and ticket:
                checked = auth.signer.verify_login_ticket(ticket, LOGIN_TICKET_MAX_AGE, auth.state.generation)
                ok = checked is not None and store.consume_ticket(checked[0], checked[1])
        except SessionStateError as exc:
            log.error("session state unavailable during login: %s", exc.problem)
            return _error(503, "session_state_unavailable", exc.user_message)
        if not ok:
            throttle.record_failure()
            log.warning("HUD login failed")
            return _error(401, "invalid_credentials", "That token or login link is not valid (links expire quickly).")
        throttle.reset()
        cookie_value, session_id = auth.signer.issue(auth.state.generation)
        resp = make_response(jsonify({"ok": True}))
        resp.set_cookie(
            SessionSigner.COOKIE_NAME, cookie_value, max_age=settings.session_ttl_seconds,
            httponly=True, samesite="Strict", path="/",
        )
        log.info("HUD login succeeded (session %s)", session_label(session_id))
        return resp

    # ------------------------------------------------------------------ HUD
    @app.get("/")
    def index():
        auth, _ = _try_auth()
        claims = _session(auth) if auth is not None else None
        if auth is None or claims is None:
            return redirect("/login")
        return render_template("index.html", csrf_token=auth.signer.csrf_token(claims.session_id))

    @app.post("/logout")
    @protected(state_changing=True)
    def logout():
        ctx: CommandContext = g.ctx
        backend.registry.cancel(ctx)  # a pending action never outlives the session that asked for it
        resp = make_response(jsonify({"ok": True}))
        resp.delete_cookie(SessionSigner.COOKIE_NAME, path="/", samesite="Strict", httponly=True)
        claims: SessionClaims | None = g.session
        if claims is not None:
            logged_out.add(claims.session_id)
            try:
                store.revoke_session(claims.session_id, claims.expires_at)  # persists across restarts
            except SessionStateError as exc:
                log.error("could not record logout: %s", exc.problem)
                resp = _error(503, "session_state_unavailable",
                              "The logout could not be recorded, so this session may still be valid. "
                              + exc.user_message)
                resp.delete_cookie(SessionSigner.COOKIE_NAME, path="/", samesite="Strict", httponly=True)
                return resp
            log.info("HUD logout (session %s)", session_label(claims.session_id))
        return resp

    # ------------------------------------------------------------------ API
    @app.get("/status")
    @protected(state_changing=False)
    def status():
        return jsonify(backend.status())

    @app.post("/ask")
    @protected(state_changing=True)
    def ask():
        body = _json_body()
        message = body.get("message", "")
        if not isinstance(message, str) or not message.strip():
            return _error(400, "invalid_request", "Field 'message' must be a non-empty string.")
        if len(message) > MAX_MESSAGE_CHARS:
            return _error(413, "too_long", f"Messages are limited to {MAX_MESSAGE_CHARS} characters.")
        mode = body.get("mode", "auto")
        if mode not in ("auto", "agent"):
            return _error(400, "invalid_request", "Field 'mode' must be 'auto' or 'agent'.")
        ctx: CommandContext = g.ctx
        if body.get("input") == "voice":
            # The HUD marks speech-recognised commands. This can only make a request *more*
            # restricted (voice can never approve), so a client cannot gain anything by lying.
            ctx.input_mode = InputMode.VOICE
        run_agent = getattr(backend, "run_agent", None)
        if mode == "agent" and run_agent is None:
            return _error(501, "agent_unavailable", "The agent loop is not available in this server.")
        # Agent mode uses exactly the caller's context: same policy, approvals and audit.
        reply = run_agent(message, ctx) if mode == "agent" else backend.handle(message, ctx)
        payload: dict[str, Any] = {"reply": reply}
        pending = _pending_payload(ctx)
        if pending:
            payload["pending_action"] = pending
        agent = _agent_payload(ctx)
        if agent:
            payload["agent"] = agent
        return jsonify(payload)

    @app.post("/speak")
    @protected(state_changing=True)
    def speak():
        text = _json_body().get("text", "")
        if not isinstance(text, str):
            return _error(400, "invalid_request", "Field 'text' must be a string.")
        if len(text) > MAX_SPEAK_CHARS:
            return _error(413, "too_long", f"Speech is limited to {MAX_SPEAK_CHARS} characters.")
        status = backend.speak(text, g.ctx) if text.strip() else "completed"
        if status == "denied":
            return _error(403, "forbidden", "This client is not allowed to use speech output.")
        return jsonify({"ok": True, "status": status})

    @app.post("/confirm")
    @protected(state_changing=True)
    def confirm():
        action_id = _json_body().get("action_id")
        if not isinstance(action_id, str) or not action_id:
            return _error(400, "invalid_request", "Field 'action_id' is required.")
        outcome = backend.registry.confirm(g.ctx, action_id=action_id, gate=backend.confirmation_gate)
        return jsonify({"reply": outcome.message, "status": outcome.status})

    @app.post("/cancel")
    @protected(state_changing=True)
    def cancel():
        action_id = _json_body().get("action_id")
        action_id = action_id if isinstance(action_id, str) and action_id else None
        had_pending = action_id is not None or backend.registry.has_pending(g.ctx)
        outcome = backend.registry.cancel(g.ctx, action_id=action_id)
        # Without a specific action id, also stop this caller's running agent task(s), if any.
        cancel_agent = getattr(backend, "cancel_agent", None)
        stopped = cancel_agent(g.ctx) if cancel_agent is not None and action_id is None else 0
        reply = outcome.message
        if stopped and had_pending:
            reply += " I also stopped the current task."
        elif stopped:
            reply = "Okay, I'm stopping the current task."
        return jsonify({"reply": reply, "status": outcome.status, "stopped_tasks": stopped})

    @app.get("/pending")
    @protected(state_changing=False)
    def pending():
        """Pending actions this caller may resolve; for the HUD this includes requests made by
        voice or API clients, which can only be approved here (by exact action id)."""
        return jsonify({"pending": backend.registry.pending_for_confirmer(g.ctx)})

    # ------------------------------------------------------------------ errors
    def _make_handler(status_code: int, code: str, message: str) -> Callable:
        def handler(_exc):
            return _error(status_code, code, message)

        return handler

    for status_code, code, message in (
        (400, "bad_request", "Bad request."),
        (404, "not_found", "Not found."),
        (405, "method_not_allowed", "Method not allowed."),
        (413, "too_large", "Request body too large."),
        (500, "server_error", "Internal server error."),
    ):
        app.register_error_handler(status_code, _make_handler(status_code, code, message))

    return app


def run(argv: list[str] | None = None) -> int:
    """Start the HUD server (``python server.py`` / ``python -m ultron serve``)."""
    load_dotenv_if_enabled()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
        settings.validate()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    token_result = load_api_token(settings)
    client_result = load_client_token(settings)
    install_log_redaction(extra_secrets=tuple(t for t in (token_result.token, client_result.token) if t))
    backend = JarvisLegacyBackend()
    app = create_app(settings, backend=backend)  # token files are re-read when they change

    print(f"JARVIS HUD listening on {settings.local_base_url}/ (bind address {settings.host}, debug "
          f"{'ON' if settings.debug else 'off'})")
    if token_result.ok:
        print("Log in: run `python -m ultron open-hud`, or open /login and paste your API token "
              "(`python -m ultron token show`).")
    else:
        print(f"WARNING: protected endpoints are DISABLED until authentication is configured: {token_result.problem}",
              file=sys.stderr)
    if settings.debug:
        print("WARNING: debug mode is ON (Werkzeug debugger enabled). Use only for local development.",
              file=sys.stderr)

    backend.start_background()
    app.run(host=settings.host, port=settings.port, debug=settings.debug, use_reloader=False, threaded=True)
    return 0
