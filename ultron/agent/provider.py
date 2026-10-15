"""Model provider boundary for the agent loop.

The runtime only depends on :class:`ModelProvider` and the small provider-neutral types
below. Provider-specific details (request format, tool-name rules, error classes) live
in an adapter. Two adapters share one chat-completions implementation (the ``openai`` SDK):

* :class:`OpenAIChatProvider` reuses the OpenAI client that ``main.py`` already creates
  (``OPENAI_API_KEY``; ``OPENAI_BASE_URL`` also works for OpenAI-compatible servers).
* :class:`GroqChatProvider` (M2.2) talks to Groq's OpenAI-compatible endpoint
  (:data:`GROQ_BASE_URL`, ``GROQ_API_KEY``) and adds Groq's model-specific reasoning
  parameters. :func:`create_provider` picks one from :class:`ultron.config.AgentConfig`.

Tests use scripted fake providers and a loopback stub server; no paid calls are made.

A provider never executes anything. It returns *proposals* (text and tool calls); the
runtime validates them and sends every action through the tool registry.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

log = logging.getLogger("ultron.agent")

Role = Literal["system", "user", "assistant", "tool"]
# ``malformed_call``: the provider refused to return a tool call the model generated badly
# (Groq's HTTP 400 ``tool_use_failed``). Nothing ran; the runtime may ask the model again.
ErrorKind = Literal["not_configured", "timeout", "unavailable", "rate_limited", "rejected", "bad_response",
                    "malformed_call", "malformed_output"]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class ToolCall:
    """A tool call proposed by the model. ``arguments`` is the raw JSON text it produced."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Usage:
    """Token counts reported by the provider for one model call (0 when not reported)."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelTurn:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    # "stop" | "tool_calls" | "length" (cut off by the output limit) | "" (not reported)
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class ToolDefinition:
    """What the model is told about a tool: a provider-safe name, description and schema."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str | None = None

    def size(self) -> int:
        return len(self.content) + sum(len(c.name) + len(c.arguments) for c in self.tool_calls)


class ProviderError(Exception):
    """The model could not produce a turn. Messages never contain credentials."""

    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None, code: str | None = None,
                 usage: Usage | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status  # HTTP status, when there was one
        self.code = code  # provider error code such as "insufficient_quota" (never a message or body)
        # Tokens this call already spent before failing (for example an auxiliary advisory phase),
        # when the provider can report them. None means "unknown", never "zero".
        self.usage = usage


class ModelProvider(Protocol):
    name: str

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        *,
        timeout: float,
        max_output_tokens: int,
    ) -> ModelTurn: ...


class OpenAIChatProvider:
    """Chat-completions with function tools. One tool call per turn (``parallel_tool_calls``
    off) keeps execution strictly sequential and auditable."""

    name = "openai"
    label = "OpenAI"
    key_env = "OPENAI_API_KEY"  # named in error messages, never read here
    model_env = "ULTRON_AGENT_MODEL"

    def __init__(self, client: Any, model: str) -> None:
        if client is None:
            raise ProviderError("not_configured", f"The {self.label} client is not configured.")
        self._client = client
        self.model = model

    @staticmethod
    def _message(message: Message) -> dict[str, Any]:
        # Only role/content/tool_calls/tool_call_id: no ``name`` (Groq rejects messages[].name)
        # and never any reasoning text from an earlier turn.
        if message.role == "tool":
            return {"role": "tool", "tool_call_id": message.tool_call_id or "", "content": message.content}
        out: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            out["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in message.tool_calls
            ]
        return out

    def _extra_params(self) -> dict[str, Any]:
        """Provider-specific request fields (none for OpenAI)."""
        return {}

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        *,
        timeout: float,
        max_output_tokens: int,
    ) -> ModelTurn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message(m) for m in messages],
            # ``max_tokens`` is deprecated and rejected by reasoning models; this works for both.
            "max_completion_tokens": max_output_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": dict(t.parameters)}}
                for t in tools
            ]
            kwargs["tool_choice"] = "auto"
            kwargs["parallel_tool_calls"] = False
        kwargs.update(self._extra_params())
        try:
            # No silent retries: the runtime decides what happens after a failure.
            client = self._client.with_options(timeout=timeout, max_retries=0)
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a provider-neutral error below
            raise _map_error(exc, self) from None
        return _parse_response(response)


# Groq reasoning controls differ per model family (console.groq.com/docs/reasoning):
# gpt-oss rejects ``reasoning_format`` but accepts ``include_reasoning`` and low/medium/high
# effort; Qwen uses ``reasoning_format``; other models (e.g. Llama) take neither.
GROQ_REASONING_EFFORTS = frozenset({"none", "default", "minimal", "low", "medium", "high", "xhigh", "max"})


def groq_model_family(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("openai/gpt-oss-"):
        return "gpt-oss"
    if lowered.startswith("qwen/"):
        return "qwen"
    return "other"


class GroqChatProvider(OpenAIChatProvider):
    """Groq through its OpenAI-compatible chat-completions endpoint (same SDK, same parsing,
    same runtime, registry, policy and confirmations as OpenAI).

    Reasoning models spend part of ``max_completion_tokens`` on hidden reasoning, so Groq
    gets a larger per-call output limit (``ultron.config``) and, for gpt-oss, low reasoning
    effort by default. Reasoning text is never requested back (``include_reasoning: false``
    / ``reasoning_format: hidden``), never logged and never sent to the model again.
    """

    name = "groq"
    label = "Groq"
    key_env = "GROQ_API_KEY"
    model_env = "ULTRON_GROQ_MODEL"

    def __init__(self, client: Any, model: str, *, reasoning_effort: str = "") -> None:
        super().__init__(client, model)
        if reasoning_effort and reasoning_effort not in GROQ_REASONING_EFFORTS:
            raise ValueError("unsupported Groq reasoning effort")
        self.reasoning_effort = reasoning_effort
        self.family = groq_model_family(model)

    def _extra_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        if self.family == "gpt-oss":
            params["extra_body"] = {"include_reasoning": False}
        elif self.family == "qwen":
            params["extra_body"] = {"reasoning_format": "hidden"}
        return params


def create_provider(config: Any, *, openai_client: Any = None, groq_api_key: str | None = None,
                    groq_base_url: str = GROQ_BASE_URL) -> OpenAIChatProvider | None:
    """The configured agent provider, or None when it cannot be used (unknown provider,
    missing credential). ``config`` is an :class:`ultron.config.AgentConfig`.

    The Groq client always gets an explicit ``base_url``: ``OPENAI_BASE_URL`` (meant for the
    OpenAI client) can never redirect the Groq key to another host. ``groq_base_url`` exists
    for the loopback test stub; nothing reads it from the environment.
    """
    if getattr(config, "provider_error", ""):
        return None
    if config.provider == "openai":
        return OpenAIChatProvider(openai_client, config.model) if openai_client is not None else None
    if config.provider == "groq":
        if not groq_api_key:
            return None
        import openai

        from ultron.config import OPENAI_CLIENT_TIMEOUT

        client = openai.OpenAI(api_key=groq_api_key, base_url=groq_base_url, timeout=OPENAI_CLIENT_TIMEOUT,
                               max_retries=0)
        return GroqChatProvider(client, config.model, reasoning_effort=config.reasoning_effort)
    return None


_MESSAGES: dict[ErrorKind, str] = {
    "not_configured": "The language model rejected the configured credentials (check {key_env}).",
    "timeout": "The language model did not answer in time.",
    "unavailable": "The language model is unavailable right now.",
    "rate_limited": "The language model is rate limited right now; try again in a minute.",
    "rejected": "The language model rejected the request (check {model_env} supports tool calling).",
    "bad_response": "The language model returned an unexpected response.",
    "malformed_call": "The language model produced a malformed tool call.",
    "malformed_output": "The language model produced output the provider could not parse.",
}
_QUOTA_MESSAGE = "The {label} account has no remaining quota; check billing and usage limits."
_TOO_LARGE_MESSAGE = "The request was too large for the language model."
# Groq HTTP 400 code seen in real runs but not documented by Groq (checked 2026-10-13). Its name
# suggests provider-side parsing of generated model output. Treat only this exact code as a malformed
# model turn, bounded by the runtime invalid-call and step limits; arbitrary HTTP 400s remain rejected.
_OUTPUT_PARSE_MESSAGE = "The {label} service could not parse the language model's output (output_parse_failed)."
_SAFE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _safe(value: Any) -> str | None:
    """Provider codes are short identifiers; anything else is dropped rather than logged."""
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else None


def _map_error(exc: Exception, provider: OpenAIChatProvider | None = None) -> ProviderError:
    kind: ErrorKind = "unavailable"
    status = code = None
    try:
        import openai

        if isinstance(exc, openai.APITimeoutError):
            kind = "timeout"
        elif isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            kind = "not_configured"
        elif isinstance(exc, openai.RateLimitError):
            kind = "rate_limited"
        elif isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError, openai.NotFoundError)):
            kind = "rejected"
        elif isinstance(exc, (openai.APIConnectionError, openai.APIStatusError)):
            kind = "unavailable"
        else:  # APIResponseValidationError, unparseable bodies, unexpected exceptions
            kind = "bad_response"
        if isinstance(exc, openai.APIStatusError):
            status = exc.status_code if isinstance(exc.status_code, int) else None
        if isinstance(exc, openai.APIError):
            code = _safe(getattr(exc, "code", None))
        if status == 413:  # Groq: request entity too large (no dedicated SDK class)
            kind = "rejected"
        if code == "tool_use_failed" and kind == "rejected":
            # Groq validated the model's tool call and refused it. Its body carries the raw
            # generation (``failed_generation``), which is never read, logged or returned.
            kind = "malformed_call"
        elif code == "output_parse_failed" and kind == "rejected":
            # This exact Groq code indicates output parsing failed before a tool call was returned.
            # The response body is deliberately ignored; runtime retries are bounded like invalid calls.
            kind = "malformed_output"
    except ImportError:  # pragma: no cover - openai is a core dependency
        kind = "unavailable"
    # Structured and safe: kind, exception type, HTTP status, provider code/param and request id.
    # Never the message or body (they can echo request details or part of a key).
    log.warning("model provider error kind=%s type=%s http=%s code=%s param=%s request_id=%s", kind,
                type(exc).__name__, status or "-", code or "-", _safe(getattr(exc, "param", None)) or "-",
                _safe(getattr(exc, "request_id", None)) or "-")
    names = {"key_env": getattr(provider, "key_env", OpenAIChatProvider.key_env),
             "model_env": getattr(provider, "model_env", OpenAIChatProvider.model_env),
             "label": getattr(provider, "label", OpenAIChatProvider.label)}
    if code == "insufficient_quota":
        message = _QUOTA_MESSAGE
    elif status == 413:
        message = _TOO_LARGE_MESSAGE
    elif code == "output_parse_failed" and kind == "malformed_output":
        message = _OUTPUT_PARSE_MESSAGE
    else:
        message = _MESSAGES[kind]
    return ProviderError(kind, message.format(**names), status=status, code=code)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _parse_response(response: Any) -> ModelTurn:
    try:
        choice = response.choices[0]
        message = choice.message
    except (AttributeError, IndexError, TypeError):
        raise ProviderError("bad_response", _MESSAGES["bad_response"]) from None
    if message is None:
        raise ProviderError("bad_response", _MESSAGES["bad_response"])
    finish_reason = getattr(choice, "finish_reason", None)
    finish_reason = finish_reason if isinstance(finish_reason, str) else ""
    if finish_reason == "content_filter":
        raise ProviderError("rejected", "The language model's content filter blocked this request.",
                            code="content_filter")
    text = getattr(message, "content", None)
    if not isinstance(text, str) or not text:
        # A refusal is the model's answer, not an error; it carries no tool calls.
        refusal = getattr(message, "refusal", None)
        text = refusal if isinstance(refusal, str) else ""
    calls: list[ToolCall] = []
    for raw in getattr(message, "tool_calls", None) or []:
        function = getattr(raw, "function", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        call_id = getattr(raw, "id", None)
        if not isinstance(name, str) or not name.strip() or not isinstance(call_id, str) or not call_id:
            raise ProviderError("bad_response", "The language model returned a malformed tool call.")
        if arguments is not None and not isinstance(arguments, str):
            raise ProviderError("bad_response", "The language model returned malformed tool call arguments.")
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments if isinstance(arguments, str) else ""))
    usage = getattr(response, "usage", None)
    return ModelTurn(
        text=text, tool_calls=tuple(calls), finish_reason=finish_reason,
        usage=Usage(_count(getattr(usage, "prompt_tokens", 0)), _count(getattr(usage, "completion_tokens", 0))),
    )
