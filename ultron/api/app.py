"""Local HUD/API server (Flask) with authentication and cross-origin protection.

Security model (see README "Security model"):

* Binds to 127.0.0.1 by default; debug off; refuses non-loopback unless
  explicitly allowed (``ultron.config.Settings.validate``).
* ``Host`` header allowlist (DNS-rebinding defence) and ``Origin`` allowlist on
  every request; no CORS headers are ever sent.
* Protected endpoints accept either
  - ``Authorization: Bearer <API token>`` (CLI / wake-word clients), or
  - the HUD session cookie (HttpOnly, SameSite=Strict, HMAC-signed) **plus**,
    for state-changing requests, an allowed ``Origin`` and a per-session
    ``X-CSRF-Token`` header.
* State-changing endpoints require ``Content-Type: application/json``.
* If no valid API token is configured, protected endpoints fail closed (503).
* The API token is never rendered into pages, sent to the browser or logged.
"""

from __future__ import annotations

import functools
import hmac
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, make_response, redirect, render_template, request

from ultron.config import ConfigurationError, Settings, load_dotenv_if_enabled, load_settings
from ultron.legacy import Backend, JarvisLegacyBackend
from ultron.security.policy import Channel, CommandContext
from ultron.security.redact import install_log_redaction
from ultron.security.sessions import LoginThrottle, SessionSigner, UsedNonces
from ultron.security.tokens import TokenLoadResult, load_api_token

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

def _error(status: int, code: str, message: str):
    resp = jsonify({"error": code, "message": message})
    resp.status_code = status
    return resp


def create_app(
    settings: Settings | None = None,
    *,
    backend: Backend | None = None,
    token_result: TokenLoadResult | None = None,
) -> Flask:
    settings = settings or load_settings()
    token_result = token_result or load_api_token(settings)
    backend = backend or JarvisLegacyBackend()

    app = Flask(
        "ultron",
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.update(MAX_CONTENT_LENGTH=16 * 1024, JSON_SORT_KEYS=False)
    app.extensions["ultron"] = {"settings": settings, "backend": backend, "token_ok": token_result.ok}

    api_token = token_result.token if token_result.ok else None
    signer = SessionSigner(api_token, settings.session_ttl_seconds) if api_token else None
    throttle = LoginThrottle()
    used_tickets = UsedNonces()
    setup_message = token_result.problem or "Authentication is not configured."

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

    def _session_id() -> str | None:
        if signer is None:
            return None
        return signer.verify(request.cookies.get(SessionSigner.COOKIE_NAME))

    def protected(state_changing: bool) -> Callable:
        def decorator(view: Callable) -> Callable:
            @functools.wraps(view)
            def wrapper(*args, **kwargs):
                if signer is None or api_token is None:
                    return _error(503, "auth_not_configured", setup_message)

                auth_header = request.headers.get("Authorization")
                if auth_header is not None:
                    scheme, _, presented = auth_header.partition(" ")
                    if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip(), api_token):
                        return _error(401, "unauthorized", "Invalid credentials.")
                    channel = Channel.WAKEWORD if request.headers.get("X-Ultron-Client") == "wakeword" else Channel.API
                    g.ctx = CommandContext(principal="api-token", channel=channel, authenticated=True)
                else:
                    session_id = _session_id()
                    if session_id is None:
                        return _error(401, "unauthorized", "Please log in to the HUD first (open /login).")
                    if state_changing:
                        if _request_origin() not in allowed_origins:
                            return _error(403, "forbidden_origin", "Missing or disallowed request origin.")
                        if not signer.check_csrf(session_id, request.headers.get("X-CSRF-Token")):
                            return _error(403, "csrf_failed", "Missing or invalid CSRF token. Reload the HUD.")
                    g.ctx = CommandContext(principal=f"session:{session_id}", channel=Channel.HUD, authenticated=True)

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

    # ------------------------------------------------------------------ public
    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "auth_configured": signer is not None})

    @app.get("/login")
    def login_page():
        if _session_id() is not None:
            return redirect("/")
        return render_template("login.html", configured=signer is not None, setup_message=setup_message)

    @app.post("/login")
    def login():
        if signer is None:
            return _error(503, "auth_not_configured", setup_message)
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
        if isinstance(token, str) and token:
            ok = hmac.compare_digest(token.strip(), api_token)
        elif isinstance(ticket, str) and ticket:
            nonce = signer.verify_login_ticket(ticket)
            ok = nonce is not None and used_tickets.consume(nonce)
        if not ok:
            throttle.record_failure()
            log.warning("HUD login failed")
            return _error(401, "invalid_credentials", "That token or login link is not valid (links expire quickly).")
        throttle.reset()
        cookie_value, _session = signer.issue()
        resp = make_response(jsonify({"ok": True}))
        resp.set_cookie(
            SessionSigner.COOKIE_NAME, cookie_value, max_age=settings.session_ttl_seconds,
            httponly=True, samesite="Strict", path="/",
        )
        log.info("HUD login succeeded")
        return resp

    # ------------------------------------------------------------------ HUD
    @app.get("/")
    def index():
        session_id = _session_id()
        if session_id is None:
            return redirect("/login")
        return render_template("index.html", csrf_token=signer.csrf_token(session_id))

    @app.post("/logout")
    @protected(state_changing=True)
    def logout():
        resp = make_response(jsonify({"ok": True}))
        resp.delete_cookie(SessionSigner.COOKIE_NAME, path="/", samesite="Strict", httponly=True)
        return resp

    # ------------------------------------------------------------------ API
    @app.get("/status")
    @protected(state_changing=False)
    def status():
        return jsonify(backend.status())

    @app.post("/ask")
    @protected(state_changing=True)
    def ask():
        message = _json_body().get("message", "")
        if not isinstance(message, str) or not message.strip():
            return _error(400, "invalid_request", "Field 'message' must be a non-empty string.")
        if len(message) > MAX_MESSAGE_CHARS:
            return _error(413, "too_long", f"Messages are limited to {MAX_MESSAGE_CHARS} characters.")
        ctx: CommandContext = g.ctx
        reply = backend.handle(message, ctx)
        payload: dict[str, Any] = {"reply": reply}
        pending = _pending_payload(ctx)
        if pending:
            payload["pending_action"] = pending
        return jsonify(payload)

    @app.post("/speak")
    @protected(state_changing=True)
    def speak():
        text = _json_body().get("text", "")
        if not isinstance(text, str):
            return _error(400, "invalid_request", "Field 'text' must be a string.")
        if len(text) > MAX_SPEAK_CHARS:
            return _error(413, "too_long", f"Speech is limited to {MAX_SPEAK_CHARS} characters.")
        if text.strip():
            backend.speak(text)
        return jsonify({"ok": True})

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
        outcome = backend.registry.cancel(g.ctx)
        return jsonify({"reply": outcome.message, "status": outcome.status})

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
    install_log_redaction(extra_secrets=(token_result.token,) if token_result.token else ())
    backend = JarvisLegacyBackend()
    app = create_app(settings, backend=backend, token_result=token_result)

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
