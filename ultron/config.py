"""Configuration and credential access.

Rules:
- Credentials come from the environment (optionally populated from a local,
  git-ignored ``.env`` file). They are never hardcoded, logged or echoed.
- Placeholder values copied from ``.env.example`` count as "not configured".
- Server settings default to the safe choice (loopback only, debug off).
"""

from __future__ import annotations

import ipaddress
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
DEFAULT_SESSION_HOURS = 12.0


class ConfigurationError(RuntimeError):
    """Raised when configuration is missing or unsafe. Messages never contain secret values."""


class MissingCredentialError(ConfigurationError):
    def __init__(self, name: str) -> None:
        spec = CREDENTIALS.get(name)
        provider = spec.provider if spec else "the provider"
        purpose = f" (needed for {spec.purpose})" if spec else ""
        super().__init__(
            f"{name} is not set{purpose}. Add your {provider} credential to the local .env file "
            f"(copy .env.example to .env) or export it in your shell. Never commit it."
        )
        self.name = name


@dataclass(frozen=True)
class CredentialSpec:
    name: str
    provider: str
    purpose: str


CREDENTIALS: dict[str, CredentialSpec] = {
    spec.name: spec
    for spec in (
        CredentialSpec("OPENAI_API_KEY", "OpenAI", "agent loop, LLM replies, knowledge-base answers and study plans"),
        CredentialSpec("GROQ_API_KEY", "Groq", "the agent loop when ULTRON_PROVIDER=groq"),
        CredentialSpec("WEATHER_API_KEY", "OpenWeather", "weather reports"),
        CredentialSpec("NEWS_API_KEY", "NewsAPI", "news headlines"),
        CredentialSpec("CRICKET_API_KEY", "CricketData", "live cricket scores"),
        CredentialSpec("PICOVOICE_ACCESS_KEY", "Picovoice", "the Porcupine wake-word listener"),
    )
}

_PLACEHOLDER_MARKERS = ("your_", "your-", "changeme", "change_me", "placeholder", "<", "xxxx", "replace_me")


def is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    v = value.strip().lower()
    return not v or any(marker in v for marker in _PLACEHOLDER_MARKERS)


def get_credential(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """Return the credential value, or ``None`` if unset or still a placeholder."""
    env = os.environ if env is None else env
    value = env.get(name)
    if is_placeholder(value):
        return None
    return value.strip() if value else None


def require_credential(name: str, env: Mapping[str, str] | None = None) -> str:
    value = get_credential(name, env)
    if value is None:
        raise MissingCredentialError(name)
    return value


def credential_status(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Which credentials are configured (booleans only, never values)."""
    return {name: get_credential(name, env) is not None for name in CREDENTIALS}


def load_dotenv_if_enabled() -> None:
    """Load ``.env`` into the process environment unless ``ULTRON_SKIP_DOTENV=1`` (used by tests)."""
    if os.environ.get("ULTRON_SKIP_DOTENV") == "1":
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a core dependency
        return
    load_dotenv(override=False)


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in TRUE_VALUES


def default_data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get("ULTRON_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Ultron"
    xdg = env.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "ultron"


def is_loopback_host(host: str) -> bool:
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    debug: bool = False
    allow_non_loopback: bool = False
    extra_allowed_hosts: tuple[str, ...] = ()
    extra_allowed_origins: tuple[str, ...] = ()
    api_token_file: Path = Path()
    session_ttl_seconds: int = int(DEFAULT_SESSION_HOURS * 3600)
    data_dir: Path = Path()
    client_token_file: Path = Path()

    @property
    def session_state_file(self) -> Path:
        """Persistent session revocation state (see ultron.security.session_state)."""
        return self.data_dir / "session_state.json"

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Accepted ``Host`` header values (defends against DNS rebinding)."""
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}
        if self.host not in ("0.0.0.0", "::", ""):  # noqa: S104 - comparison, not a bind
            h = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
            hosts.add(f"{h}:{self.port}")
        hosts.update(self.extra_allowed_hosts)
        return frozenset(h.lower() for h in hosts)

    @property
    def allowed_origins(self) -> frozenset[str]:
        origins = {f"http://{h}" for h in self.allowed_hosts}
        origins.update(o.rstrip("/") for o in self.extra_allowed_origins)
        return frozenset(o.lower() for o in origins)

    @property
    def local_base_url(self) -> str:
        host = "127.0.0.1" if (is_loopback_host(self.host) or self.host in ("0.0.0.0", "::")) else self.host  # noqa: S104
        return f"http://{host}:{self.port}"

    def validate(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ConfigurationError(f"ULTRON_PORT must be between 1 and 65535 (got {self.port}).")
        if not is_loopback_host(self.host):
            if not self.allow_non_loopback:
                raise ConfigurationError(
                    f"Refusing to listen on non-loopback address {self.host!r}. The assistant can control this "
                    "computer, so it only listens on 127.0.0.1 by default. If you really need network access, "
                    "set ULTRON_ALLOW_NON_LOOPBACK=1 and read the security notes in README.md first."
                )
            if self.debug:
                raise ConfigurationError(
                    "ULTRON_DEBUG=1 is only allowed on a loopback address: the Werkzeug debugger can execute code."
                )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def _csv(name: str) -> tuple[str, ...]:
        return tuple(p.strip() for p in env.get(name, "").split(",") if p.strip())

    try:
        port = int(env.get("ULTRON_PORT", str(DEFAULT_PORT)))
    except ValueError as exc:
        raise ConfigurationError("ULTRON_PORT must be an integer.") from exc
    try:
        hours = float(env.get("ULTRON_SESSION_HOURS", str(DEFAULT_SESSION_HOURS)))
    except ValueError as exc:
        raise ConfigurationError("ULTRON_SESSION_HOURS must be a number.") from exc
    hours = min(max(hours, 0.05), 24 * 30)

    data_dir = default_data_dir(env)
    token_override = env.get("ULTRON_API_TOKEN_FILE")
    token_file = Path(token_override).expanduser() if token_override else data_dir / "api_token"
    client_override = env.get("ULTRON_CLIENT_TOKEN_FILE")
    client_file = Path(client_override).expanduser() if client_override else data_dir / "client_token"

    return Settings(
        host=env.get("ULTRON_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
        port=port,
        debug=_flag(env, "ULTRON_DEBUG"),
        allow_non_loopback=_flag(env, "ULTRON_ALLOW_NON_LOOPBACK"),
        extra_allowed_hosts=_csv("ULTRON_ALLOWED_HOSTS"),
        extra_allowed_origins=_csv("ULTRON_ALLOWED_ORIGINS"),
        api_token_file=token_file,
        session_ttl_seconds=int(hours * 3600),
        data_dir=data_dir,
        client_token_file=client_file,
    )


# ----------------------------------------------------------------------------- agent loop (M2.0)
# The one place a default model name lives. Override with ULTRON_CHAT_MODEL (single-shot
# chat, study plans, knowledge answers) and/or ULTRON_AGENT_MODEL (agent loop; defaults to
# the chat model). The agent model must support Chat Completions function calling.
DEFAULT_CHAT_MODEL = "gpt-4.1-mini"
DEFAULT_AGENT_MODEL = DEFAULT_CHAT_MODEL
_AGENT_MODEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-")
# The shared OpenAI client (main.client): bounded instead of the SDK's 600 s / 2 retries.
# The agent overrides both per call (its own timeout, no retries).
OPENAI_CLIENT_TIMEOUT = 30.0
OPENAI_CLIENT_MAX_RETRIES = 1

# Agent-loop model provider (M2.2): ULTRON_PROVIDER=openai|groq.
SUPPORTED_PROVIDERS = ("openai", "groq")
DEFAULT_PROVIDER = "openai"
PROVIDER_KEYS = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY"}
# Production on Groq, self-serve pricing, documented tool use (console.groq.com/docs/models and
# /docs/tool-use, checked 2026-10-13). No parallel tool calls, which the agent never uses.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
# Per-call output limit. Groq's default model is a reasoning model whose hidden reasoning
# counts against max_completion_tokens, so it needs more room than OpenAI's 400.
DEFAULT_MAX_OUTPUT_TOKENS = {"openai": 400, "groq": 1024}
_PROVIDER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


def _model_name(env: Mapping[str, str], name: str, default: str) -> str:
    import logging

    model = (env.get(name) or "").strip()
    if not model:
        return default
    if len(model) > 100 or not set(model) <= _AGENT_MODEL_CHARS:
        logging.getLogger("ultron.config").warning("%s is not a valid model name; using %s", name, default)
        return default
    return model


def load_chat_model(env: Mapping[str, str] | None = None) -> str:
    """Model for the single-shot OpenAI calls (``ULTRON_CHAT_MODEL``)."""
    return _model_name(os.environ if env is None else env, "ULTRON_CHAT_MODEL", DEFAULT_CHAT_MODEL)


@dataclass(frozen=True)
class AgentConfig:
    """Agent-loop switches and hard limits (docs/M2_0_AGENT_LOOP.md). Values are clamped."""

    enabled: bool = True
    provider: str = DEFAULT_PROVIDER
    # Non-empty when ULTRON_PROVIDER is not supported: the agent stays off rather than
    # sending requests (and data) to a provider the owner did not choose.
    provider_error: str = ""
    model: str = DEFAULT_AGENT_MODEL
    reasoning_effort: str = ""  # Groq only; "" = do not send
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS[DEFAULT_PROVIDER]
    max_steps: int = 6
    max_tool_calls: int = 8
    model_timeout: float = 20.0
    tool_timeout: float = 20.0
    max_duration: float = 45.0  # below the wake-word listener's 60 s read timeout
    max_concurrent_runs: int = 4


# ---- optional multi-model advisory pass (see docs/ENSEMBLE_INTEGRATION.md) ----
ENSEMBLE_ROUTING_MODES = ("off", "auto", "always")
DEFAULT_ENSEMBLE_MAX_ADVISERS = 2
ENSEMBLE_HARD_MAX_ADVISERS = 3  # past three independent notes there is nothing left to compare


@dataclass(frozen=True)
class EnsembleConfig:
    """Switches and hard limits for the optional advisory pass. Off unless explicitly enabled.

    Advisers are named one by one as ``provider:model``; nothing is discovered automatically and
    no adviser is inferred from the primary provider. Every value is clamped on load.
    """

    enabled: bool = False
    routing: str = "auto"  # off | auto | always
    synthesis: bool = True  # one comparison call, only when two or more advisers answered
    advisers: tuple[str, ...] = ()
    config_error: str = ""  # set when enabled but no adviser is usable
    max_advisers: int = DEFAULT_ENSEMBLE_MAX_ADVISERS
    adviser_timeout: float = 10.0
    total_seconds: float = 15.0
    advisory_share: float = 0.5  # share of the runtime's per-turn model timeout
    max_concurrent: int = 4
    max_output_tokens: int = 400
    max_advisory_chars: int = 2000


def load_agent_config(env: Mapping[str, str] | None = None) -> AgentConfig:
    """Read ``ULTRON_AGENT*``. Bad values fall back to the default (never to "unlimited")."""
    import logging

    env = os.environ if env is None else env
    log = logging.getLogger("ultron.config")
    defaults = AgentConfig()

    def number(name: str, default: float, low: float, high: float, cast: type = float) -> float:
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            value = cast(raw)
        except ValueError:
            log.warning("%s must be a number; using %s", name, default)
            return default
        return cast(min(max(value, low), high))

    provider = (env.get("ULTRON_PROVIDER") or "").strip().lower() or DEFAULT_PROVIDER
    provider_error = ""
    if provider not in SUPPORTED_PROVIDERS:
        shown = provider if len(provider) <= 20 and set(provider) <= _PROVIDER_CHARS else "this value"
        provider_error = (f"ULTRON_PROVIDER={shown!r} is not supported (use one of: {', '.join(SUPPORTED_PROVIDERS)}); "
                          "the agent loop is off")
        log.warning("%s", provider_error)
    reasoning_effort = ""
    if provider == "groq":
        # ULTRON_AGENT_MODEL / ULTRON_CHAT_MODEL name OpenAI models; Groq has its own namespace.
        model = _model_name(env, "ULTRON_GROQ_MODEL", DEFAULT_GROQ_MODEL)
        reasoning_effort = _groq_reasoning_effort(env, model, log)
    else:
        model = _model_name(env, "ULTRON_AGENT_MODEL", load_chat_model(env))
    output_default = DEFAULT_MAX_OUTPUT_TOKENS.get(provider, defaults.max_output_tokens)
    return AgentConfig(
        enabled=_flag(env, "ULTRON_AGENT", default=True),
        provider=provider if not provider_error else DEFAULT_PROVIDER,
        provider_error=provider_error,
        model=model,
        reasoning_effort=reasoning_effort,
        max_output_tokens=int(number("ULTRON_AGENT_MAX_OUTPUT_TOKENS", output_default, 64, 4096, int)),
        max_steps=int(number("ULTRON_AGENT_MAX_STEPS", defaults.max_steps, 1, 20, int)),
        max_tool_calls=int(number("ULTRON_AGENT_MAX_TOOL_CALLS", defaults.max_tool_calls, 1, 30, int)),
        model_timeout=number("ULTRON_AGENT_MODEL_TIMEOUT", defaults.model_timeout, 2, 120),
        tool_timeout=number("ULTRON_AGENT_TOOL_TIMEOUT", defaults.tool_timeout, 1, 120),
        max_duration=number("ULTRON_AGENT_MAX_SECONDS", defaults.max_duration, 5, 600),
        max_concurrent_runs=int(number("ULTRON_AGENT_MAX_CONCURRENT", defaults.max_concurrent_runs, 1, 8, int)),
    )


def load_ensemble_config(env: Mapping[str, str] | None = None) -> EnsembleConfig:
    """Read ``ULTRON_ENSEMBLE*``. Bad values fall back to the default, never to "unlimited"."""
    import logging

    env = os.environ if env is None else env
    log = logging.getLogger("ultron.config")
    defaults = EnsembleConfig()

    def number(name: str, default: float, low: float, high: float, cast: type = float) -> float:
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            value = cast(raw)
        except ValueError:
            log.warning("%s must be a number; using %s", name, default)
            return default
        return cast(min(max(value, low), high))

    enabled = _flag(env, "ULTRON_ENSEMBLE", default=False)
    routing = (env.get("ULTRON_ENSEMBLE_ROUTING") or "").strip().lower() or "auto"
    if routing not in ENSEMBLE_ROUTING_MODES:
        log.warning("ULTRON_ENSEMBLE_ROUTING must be one of %s; using auto", ", ".join(ENSEMBLE_ROUTING_MODES))
        routing = "auto"

    advisers: list[str] = []
    unusable = 0
    for chunk in (env.get("ULTRON_ENSEMBLE_ADVISERS") or "").split(","):
        spec = chunk.strip()
        if not spec:
            continue
        provider, separator, model = spec.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if (not separator or provider not in SUPPORTED_PROVIDERS or not model or len(model) > 128
                or set(model) - _AGENT_MODEL_CHARS):
            unusable += 1
            continue
        canonical = f"{provider}:{model}"
        if canonical in advisers:
            log.warning("ULTRON_ENSEMBLE_ADVISERS lists %s twice; keeping one", canonical)
            continue
        advisers.append(canonical)
    if unusable:
        log.warning("ULTRON_ENSEMBLE_ADVISERS: ignoring %d entry/entries that are not provider:model", unusable)

    max_advisers = int(number("ULTRON_ENSEMBLE_MAX_ADVISERS", defaults.max_advisers, 1,
                              ENSEMBLE_HARD_MAX_ADVISERS, int))
    advisers = advisers[:max_advisers]
    config_error = ""
    if enabled and not advisers:
        config_error = ("ULTRON_ENSEMBLE=1 but ULTRON_ENSEMBLE_ADVISERS names no usable provider:model entry; "
                        "the agent loop runs single-model")
        log.warning("%s", config_error)

    return EnsembleConfig(
        enabled=enabled,
        routing=routing,
        synthesis=_flag(env, "ULTRON_ENSEMBLE_SYNTHESIS", default=True),
        advisers=tuple(advisers),
        config_error=config_error,
        max_advisers=max_advisers,
        adviser_timeout=number("ULTRON_ENSEMBLE_ADVISER_TIMEOUT", defaults.adviser_timeout, 2, 60),
        total_seconds=number("ULTRON_ENSEMBLE_MAX_SECONDS", defaults.total_seconds, 2, 120),
        advisory_share=number("ULTRON_ENSEMBLE_ADVISORY_SHARE", defaults.advisory_share, 0.1, 0.9),
        max_concurrent=int(number("ULTRON_ENSEMBLE_MAX_CONCURRENT", defaults.max_concurrent, 1, 8, int)),
        max_output_tokens=int(number("ULTRON_ENSEMBLE_MAX_OUTPUT_TOKENS", defaults.max_output_tokens, 64, 2048, int)),
        max_advisory_chars=int(number("ULTRON_ENSEMBLE_MAX_ADVISORY_CHARS", defaults.max_advisory_chars,
                                      200, 8000, int)),
    )


def _groq_reasoning_effort(env: Mapping[str, str], model: str, log) -> str:
    """``ULTRON_GROQ_REASONING_EFFORT``; default ``low`` for gpt-oss, otherwise not sent
    (non-reasoning models reject the parameter)."""
    from ultron.agent.provider import GROQ_REASONING_EFFORTS, groq_model_family

    default = "low" if groq_model_family(model) == "gpt-oss" else ""
    raw = (env.get("ULTRON_GROQ_REASONING_EFFORT") or "").strip().lower()
    if not raw:
        return default
    if raw not in GROQ_REASONING_EFFORTS:
        log.warning("ULTRON_GROQ_REASONING_EFFORT must be one of %s; using the default",
                    ", ".join(sorted(GROQ_REASONING_EFFORTS)))
        return default
    return raw


def agent_provider_status(config: AgentConfig, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """(usable, one-line reason) for the agent loop's provider. Never includes a key value."""
    if config.provider_error:
        return False, config.provider_error
    if not config.enabled:
        return False, "the agent loop is off (ULTRON_AGENT=0)"
    key = PROVIDER_KEYS[config.provider]
    if get_credential(key, env) is None:
        return False, f"{key} is not set (needed for ULTRON_PROVIDER={config.provider})"
    return True, f"provider={config.provider} model={config.model}"
