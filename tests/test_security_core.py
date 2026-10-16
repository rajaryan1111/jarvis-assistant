from __future__ import annotations

import io
import logging
import os
import stat
import sys

import pytest

from ultron.config import (
    ConfigurationError,
    MissingCredentialError,
    credential_status,
    get_credential,
    is_loopback_host,
    load_settings,
    require_credential,
)
from ultron.security.confirmation import ConfirmationError, ConfirmationStore
from ultron.security.policy import Channel, CommandContext, Decision, RiskTier, evaluate
from ultron.security.redact import REDACTED, RedactingFilter, install_log_redaction, redact
from ultron.security.sessions import LoginThrottle, SessionSigner, UsedNonces
from ultron.security.tokens import MIN_TOKEN_LENGTH, generate_token, load_api_token, write_token_file


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ----------------------------------------------------------------- config
def test_defaults_bind_loopback_with_debug_off(tmp_path):
    s = load_settings({"ULTRON_DATA_DIR": str(tmp_path)})
    assert s.host == "127.0.0.1"
    assert s.debug is False
    assert s.port == 5001
    s.validate()
    assert s.api_token_file == tmp_path / "api_token"
    assert s.local_base_url == "http://127.0.0.1:5001"


def test_non_loopback_bind_is_refused_by_default(tmp_path):
    s = load_settings({"ULTRON_DATA_DIR": str(tmp_path), "ULTRON_HOST": "0.0.0.0"})  # noqa: S104
    with pytest.raises(ConfigurationError, match="non-loopback"):
        s.validate()


def test_debug_never_allowed_off_loopback(tmp_path):
    s = load_settings({
        "ULTRON_DATA_DIR": str(tmp_path), "ULTRON_HOST": "0.0.0.0",  # noqa: S104
        "ULTRON_ALLOW_NON_LOOPBACK": "1", "ULTRON_DEBUG": "1",
    })
    with pytest.raises(ConfigurationError, match="debug|DEBUG"):
        s.validate()


@pytest.mark.parametrize("bad", [{"ULTRON_PORT": "abc"}, {"ULTRON_PORT": "70000"}, {"ULTRON_SESSION_HOURS": "x"}])
def test_invalid_settings_raise_clear_errors(tmp_path, bad):
    with pytest.raises(ConfigurationError):
        load_settings({"ULTRON_DATA_DIR": str(tmp_path), **bad}).validate()


def test_allowed_hosts_and_origins_are_loopback_only(tmp_path):
    s = load_settings({"ULTRON_DATA_DIR": str(tmp_path)})
    assert s.allowed_hosts == {"127.0.0.1:5001", "localhost:5001", "[::1]:5001"}
    assert "http://127.0.0.1:5001" in s.allowed_origins
    assert all(o.startswith("http://") for o in s.allowed_origins)


def test_extra_allowed_hosts_and_origins_from_env(tmp_path):
    s = load_settings({
        "ULTRON_DATA_DIR": str(tmp_path),
        "ULTRON_ALLOWED_HOSTS": "Jarvis.Local:5001, , proxy.internal:8443",
        "ULTRON_ALLOWED_ORIGINS": "https://Jarvis.Local:8443/, http://proxy.internal:8443",
    })
    assert {"jarvis.local:5001", "proxy.internal:8443"} <= s.allowed_hosts
    assert {"127.0.0.1:5001", "localhost:5001", "[::1]:5001"} <= s.allowed_hosts
    assert {"https://jarvis.local:8443", "http://proxy.internal:8443"} <= s.allowed_origins


@pytest.mark.parametrize("host,expected", [("127.0.0.1", True), ("localhost", True), ("::1", True),
                                           ("0.0.0.0", False), ("192.168.1.5", False), ("example.com", False)])
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


def test_credentials_come_from_env_and_placeholders_count_as_missing():
    fake = "sk-" + "a" * 30
    env = {"OPENAI_API_KEY": fake, "NEWS_API_KEY": "your_newsapi_key_here", "WEATHER_API_KEY": "  "}
    assert get_credential("OPENAI_API_KEY", env) == fake
    assert get_credential("NEWS_API_KEY", env) is None
    assert get_credential("WEATHER_API_KEY", env) is None
    status = credential_status(env)
    assert status["OPENAI_API_KEY"] is True and status["NEWS_API_KEY"] is False
    assert fake not in repr(status)


def test_missing_credential_error_names_variable_but_has_no_value():
    with pytest.raises(MissingCredentialError) as exc:
        require_credential("PICOVOICE_ACCESS_KEY", {})
    message = str(exc.value)
    assert "PICOVOICE_ACCESS_KEY" in message and ".env" in message and "Picovoice" in message


# ----------------------------------------------------------------- tokens
def _settings(tmp_path):
    return load_settings({"ULTRON_DATA_DIR": str(tmp_path / "data")})


def test_generated_tokens_are_long_and_unique():
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= MIN_TOKEN_LENGTH


def test_missing_token_fails_closed_with_setup_hint(tmp_path):
    result = load_api_token(_settings(tmp_path), env={})
    assert not result.ok and result.token is None
    assert "token init" in result.problem


def test_token_file_is_private_and_loads(tmp_path):
    settings = _settings(tmp_path)
    token = generate_token()
    write_token_file(settings.api_token_file, token)
    if sys.platform != "win32":
        assert stat.S_IMODE(settings.api_token_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(settings.api_token_file.parent.stat().st_mode) == 0o700
    result = load_api_token(settings, env={})
    assert result.ok and result.token == token and result.source == "file"
    with pytest.raises(FileExistsError):
        write_token_file(settings.api_token_file, generate_token())


def test_write_token_file_does_not_chmod_existing_directories(tmp_path):
    existing = tmp_path / "home"
    existing.mkdir(mode=0o755)
    os.chmod(existing, 0o755)
    write_token_file(existing / "token", generate_token())
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_world_readable_token_file_is_rejected(tmp_path):
    settings = _settings(tmp_path)
    write_token_file(settings.api_token_file, generate_token())
    os.chmod(settings.api_token_file, 0o644)
    result = load_api_token(settings, env={})
    assert not result.ok and "chmod 600" in result.problem


def test_short_or_placeholder_env_tokens_are_rejected(tmp_path):
    settings = _settings(tmp_path)
    assert not load_api_token(settings, env={"ULTRON_API_TOKEN": "short"}).ok
    assert not load_api_token(settings, env={"ULTRON_API_TOKEN": "your_token_here_" + "x" * 30}).ok
    good = generate_token()
    result = load_api_token(settings, env={"ULTRON_API_TOKEN": good})
    assert result.ok and result.source == "env"


# ----------------------------------------------------------------- sessions
def test_session_cookie_roundtrip_tamper_and_expiry():
    clock = Clock()
    signer = SessionSigner(generate_token(), ttl_seconds=60, clock=clock)
    cookie, sid = signer.issue()
    assert signer.verify(cookie) == sid
    version, sid_part, gen, issued, exp, sig = cookie.split(".")
    assert signer.verify(f"{version}.{sid_part}.{gen}.{issued}.{int(exp) + 1000}.{sig}") is None  # extended
    assert signer.verify(f"{version}.other.{gen}.{issued}.{exp}.{sig}") is None
    assert signer.verify(None) is None and signer.verify("garbage") is None
    clock.t += 61
    assert signer.verify(cookie) is None


def test_sessions_do_not_verify_under_a_rotated_token():
    old, new = SessionSigner(generate_token(), 60), SessionSigner(generate_token(), 60)
    cookie, sid = old.issue()
    assert new.verify(cookie) is None
    assert not new.check_csrf(sid, old.csrf_token(sid))


def test_csrf_token_is_bound_to_session():
    signer = SessionSigner(generate_token(), 60)
    _, sid1 = signer.issue()
    _, sid2 = signer.issue()
    assert signer.check_csrf(sid1, signer.csrf_token(sid1))
    assert not signer.check_csrf(sid1, signer.csrf_token(sid2))
    assert not signer.check_csrf(sid1, None)
    assert not signer.check_csrf(sid1, "")


def test_login_ticket_expires_and_is_single_use():
    clock = Clock()
    signer = SessionSigner(generate_token(), 60, clock=clock)
    used = UsedNonces(clock=clock)
    ticket = signer.issue_login_ticket()
    nonce, _expires = signer.verify_login_ticket(ticket)
    assert nonce and used.consume(nonce)
    assert not used.consume(nonce)
    clock.t += 121
    assert signer.verify_login_ticket(ticket) is None
    assert SessionSigner(generate_token(), 60).verify_login_ticket(signer.issue_login_ticket()) is None


def test_login_throttle_blocks_after_repeated_failures():
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_seconds=10, clock=clock)
    for _ in range(3):
        assert throttle.allowed()
        throttle.record_failure()
    assert not throttle.allowed()
    clock.t += 11
    assert throttle.allowed()


# ----------------------------------------------------------------- policy
def test_policy_tiers():
    user = CommandContext("u", Channel.HUD, authenticated=True)
    untrusted = CommandContext.untrusted("doc")
    assert evaluate(RiskTier.OBSERVE, untrusted).decision is Decision.ALLOW
    assert evaluate(RiskTier.ROUTINE, untrusted).decision is Decision.DENY
    assert evaluate(RiskTier.DESTRUCTIVE, untrusted).decision is Decision.DENY
    assert evaluate(RiskTier.ROUTINE, user).decision is Decision.ALLOW
    assert evaluate(RiskTier.CONSEQUENTIAL, user).decision is Decision.CONFIRM
    assert evaluate(RiskTier.DESTRUCTIVE, user).decision is Decision.CONFIRM
    assert evaluate(RiskTier.FORBIDDEN, user).decision is Decision.DENY


def test_internal_services_get_only_opted_in_routine_actions():
    internal = CommandContext.internal("reminder-watcher")
    assert not internal.can_authorize and internal.is_internal
    assert evaluate(RiskTier.OBSERVE, internal).decision is Decision.ALLOW
    assert evaluate(RiskTier.ROUTINE, internal).decision is Decision.DENY
    assert evaluate(RiskTier.ROUTINE, internal, internal_ok=True).decision is Decision.ALLOW
    for tier in (RiskTier.CONSEQUENTIAL, RiskTier.DESTRUCTIVE, RiskTier.FORBIDDEN):
        assert evaluate(tier, internal, internal_ok=True).decision is Decision.DENY
    # Opting in means nothing for untrusted content.
    assert evaluate(RiskTier.ROUTINE, CommandContext.untrusted("doc"), internal_ok=True).decision is Decision.DENY


def test_confirmation_store_clear_invalidates_everyone():
    store = ConfirmationStore()
    store.create("a", {}, "alice", "x")
    store.create("b", {}, "bob", "y")
    assert store.clear() == 2
    assert store.pending_for("alice") is None and store.pending_for("bob") is None


def test_unauthenticated_user_channel_cannot_authorize():
    assert not CommandContext("x", Channel.HUD, authenticated=False).can_authorize
    assert not CommandContext("x", Channel.UNTRUSTED, authenticated=True).can_authorize
    assert CommandContext.local_voice().can_authorize


# ----------------------------------------------------------------- confirmation
def _store(clock=None, **kw):
    return ConfirmationStore(clock=clock or Clock(), **kw)


def test_confirmation_is_single_use():
    store = _store()
    action = store.create("system.shutdown", {}, "alice", "shut down")
    assert store.confirm_code("alice", action.code).action_id == action.action_id
    with pytest.raises(ConfirmationError) as exc:
        store.confirm_code("alice", action.code)
    assert exc.value.reason == "none_pending"


def test_confirmation_expires():
    clock = Clock()
    store = _store(clock, ttl_seconds=60)
    action = store.create("system.shutdown", {}, "alice", "shut down")
    clock.t += 60
    with pytest.raises(ConfirmationError) as exc:
        store.confirm_code("alice", action.code)
    assert exc.value.reason in {"expired", "none_pending"}


def test_confirmation_is_bound_to_principal_and_tool():
    store = _store()
    action = store.create("system.clear_user_caches", {}, "alice", "clear caches")
    with pytest.raises(ConfirmationError):
        store.confirm_code("mallory", action.code)
    with pytest.raises(ConfirmationError) as exc:
        store.confirm_code("alice", action.code, expected_tool="system.shutdown")
    assert exc.value.reason == "mismatch"
    with pytest.raises(ConfirmationError):
        store.confirm_id("mallory", action.action_id)
    assert store.confirm_id("alice", action.action_id).tool_name == "system.clear_user_caches"


def test_wrong_codes_cancel_after_max_attempts():
    store = _store(max_attempts=3)
    action = store.create("system.shutdown", {}, "alice", "shut down")
    wrong = "0000" if action.code != "0000" else "1111"
    for reason in ("wrong_code", "wrong_code", "too_many_attempts"):
        with pytest.raises(ConfirmationError) as exc:
            store.confirm_code("alice", wrong)
        assert exc.value.reason == reason
    with pytest.raises(ConfirmationError):
        store.confirm_code("alice", action.code)


def test_new_request_replaces_older_pending_action():
    store = _store()
    first = store.create("system.restart", {}, "alice", "restart")
    second = store.create("system.shutdown", {}, "alice", "shut down")
    with pytest.raises(ConfirmationError) as exc:
        store.confirm_id("alice", first.action_id)  # the replaced action can no longer be confirmed
    assert exc.value.reason == "not_found"
    assert store.pending_for("alice") is second  # a stale id does not consume the current action
    assert store.confirm_id("alice", second.action_id) is second


def test_public_view_has_no_code():
    action = _store().create("system.shutdown", {}, "alice", "shut down")
    public = action.public(action.created_at)
    assert public["action_id"] == action.action_id
    assert action.code not in public.values()
    assert set(public) == {"action_id", "tool", "summary", "expires_in", "requested_via"}


# ----------------------------------------------------------------- redaction
def test_redact_masks_query_keys_headers_and_known_values(monkeypatch):
    secret = "abc" + "123def456ghi789"
    monkeypatch.setenv("WEATHER_API_KEY", secret)
    text = redact(f"GET https://api.example/x?q=1&appid={secret}&apiKey=zzzzzzzz Authorization: Bearer tok123456")
    assert secret not in text and "zzzzzzzz" not in text and "tok123456" not in text
    assert REDACTED in text
    pv = "pv_" + "A" * 24
    assert pv not in redact(f"key={pv}")


def test_redact_masks_url_userinfo_passwords():
    """Remote URLs with embedded credentials must not survive redaction.

    ``https://user:password@host`` is the concrete case: the password shape matches no
    token prefix and appears in no query parameter, so without a userinfo rule it reaches
    logs and tool output verbatim - demonstrated with .git/config-style URLs and with any
    legitimate project file that quotes such a URL.
    """
    pw = "Sup3r" + "S3cret" + "Passw0rd"  # assembled fixture, not a real credential
    out = redact(f"https://bot:{pw}@github.com/example/repo.git")
    assert pw not in out
    assert out == f"https://bot:{REDACTED}@github.com/example/repo.git"
    # Percent-encoded passwords are masked whole; scheme, user and host stay diagnosable.
    assert redact("https://u:p%40ss@h/x") == f"https://u:{REDACTED}@h/x"
    # Ordinary URLs, ports and scp-style syntax are untouched.
    assert redact("https://github.com/example/repo.git") == "https://github.com/example/repo.git"
    assert redact("https://example.com:8080/path") == "https://example.com:8080/path"
    assert redact("git@github.com:example/repo.git") == "git@github.com:example/repo.git"


def test_redacting_log_filter(caplog):
    token = generate_token()
    logger = logging.getLogger("ultron.test.redact")
    logger.addFilter(RedactingFilter(extra_secrets=(token,)))
    with caplog.at_level(logging.INFO, logger="ultron.test.redact"):
        logger.info("token is %s", token)
    assert token not in caplog.text and REDACTED in caplog.text


def test_redacting_log_filter_also_masks_secrets_inside_tracebacks(caplog):
    """A provider exception can carry a keyed URL; the traceback must be masked too.

    The filter redacts ``record.exc_text`` and drops ``exc_info`` so the formatter emits
    the masked traceback - without this the raw exception text reaches every log line.
    """
    token = "sk-" + "livekeyvalue1234567890"
    logger = logging.getLogger("ultron.test.redact.exc")
    logger.addFilter(RedactingFilter(extra_secrets=(token,)))
    try:
        raise RuntimeError(f"GET https://api.example/x?api_key={token} failed")
    except RuntimeError:
        with caplog.at_level(logging.ERROR, logger="ultron.test.redact.exc"):
            logger.error("provider call failed", exc_info=True)
    assert token not in caplog.text
    assert REDACTED in caplog.text
    assert "Traceback" in caplog.text  # the traceback is still there, just masked


def test_redacting_log_filter_never_crashes_on_an_unformattable_record():
    """Logging must not take the app down because a record cannot be formatted."""

    class Exploding:
        def __str__(self):
            raise ValueError("boom")

    record = logging.LogRecord("ultron.test", logging.INFO, __file__, 1, "value %s", (Exploding(),), None)
    assert RedactingFilter().filter(record) is True


def test_install_log_redaction_attaches_one_filter_per_handler_and_is_idempotent():
    """The install step is the only thing standing between a secret and the log file."""
    root = logging.getLogger()
    saved = [(handler, list(handler.filters)) for handler in root.handlers]
    handler = logging.StreamHandler(io.StringIO())
    root.addHandler(handler)
    try:
        first = install_log_redaction()
        assert isinstance(first, RedactingFilter)
        installed = [f for f in handler.filters if isinstance(f, RedactingFilter)]
        assert len(installed) == 1, "the filter must be attached exactly once"

        install_log_redaction()  # calling it again must not stack duplicate filters
        assert len([f for f in handler.filters if isinstance(f, RedactingFilter)]) == 1

        # The value is passed as an extra secret, so this test exercises the install step
        # itself rather than any key-shape heuristic. (An earlier draft used an sk- prefixed
        # fixture and the repository's own secret scanner correctly rejected it.)
        secret = "not-a-real-" + "tokenvalue1234"
        install_log_redaction(extra_secrets=(secret,))
        logging.getLogger("ultron.test.install").warning("token %s", secret)
        assert secret not in handler.stream.getvalue()
        assert REDACTED in handler.stream.getvalue()
    finally:
        root.removeHandler(handler)
        for saved_handler, filters in saved:  # leave global logging exactly as we found it
            saved_handler.filters = filters
