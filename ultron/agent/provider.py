"""Model provider boundary for the agent loop.

The runtime only depends on :class:`ModelProvider` and the small provider-neutral types
below. Provider-specific details (request format, tool-name rules, error classes) live
in an adapter. M2.0 ships one adapter, :class:`OpenAIChatProvider`, which reuses the
OpenAI client that ``main.py`` already creates (``OPENAI_API_KEY``; ``OPENAI_BASE_URL``
also works for OpenAI-compatible servers that implement chat-completions tool calls).
Tests use scripted fake providers; no paid calls are made.

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
ErrorKind = Literal["not_configured", "timeout", "unavailable", "rate_limited", "rejected", "bad_response"]


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

    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status  # HTTP status, when there was one
        self.code = code  # provider error code such as "insufficient_quota" (never a message or body)


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

    def __init__(self, client: Any, model: str) -> None:
        if client is None:
            raise ProviderError("not_configured", "The OpenAI client is not configured.")
        self._client = client
        self.model = model

    @staticmethod
    def _message(message: Message) -> dict[str, Any]:
        if message.role == "tool":
            return {"role": "tool", "tool_call_id": message.tool_call_id or "", "content": message.content}
        out: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            out["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in message.tool_calls
            ]
        return out

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
        try:
            # No silent retries: the runtime decides what happens after a failure.
            client = self._client.with_options(timeout=timeout, max_retries=0)
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a provider-neutral error below
            raise _map_error(exc) from None
        return _parse_response(response)


_MESSAGES: dict[ErrorKind, str] = {
    "not_configured": "The language model rejected the configured credentials (check OPENAI_API_KEY).",
    "timeout": "The language model did not answer in time.",
    "unavailable": "The language model is unavailable right now.",
    "rate_limited": "The language model is rate limited right now; try again in a minute.",
    "rejected": "The language model rejected the request (check ULTRON_AGENT_MODEL supports tool calling).",
    "bad_response": "The language model returned an unexpected response.",
}
_QUOTA_MESSAGE = "The OpenAI account has no remaining quota; check billing and usage limits."
_SAFE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _safe(value: Any) -> str | None:
    """Provider codes are short identifiers; anything else is dropped rather than logged."""
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else None


def _map_error(exc: Exception) -> ProviderError:
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
    except ImportError:  # pragma: no cover - openai is a core dependency
        kind = "unavailable"
    # Structured and safe: kind, exception type, HTTP status, provider code/param and request id.
    # Never the message or body (they can echo request details or part of a key).
    log.warning("model provider error kind=%s type=%s http=%s code=%s param=%s request_id=%s", kind,
                type(exc).__name__, status or "-", code or "-", _safe(getattr(exc, "param", None)) or "-",
                _safe(getattr(exc, "request_id", None)) or "-")
    message = _QUOTA_MESSAGE if code == "insufficient_quota" else _MESSAGES[kind]
    return ProviderError(kind, message, status=status, code=code)


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
        if not isinstance(name, str) or not isinstance(call_id, str) or not call_id:
            raise ProviderError("bad_response", "The language model returned a malformed tool call.")
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments if isinstance(arguments, str) else ""))
    usage = getattr(response, "usage", None)
    return ModelTurn(
        text=text, tool_calls=tuple(calls), finish_reason=finish_reason,
        usage=Usage(_count(getattr(usage, "prompt_tokens", 0)), _count(getattr(usage, "completion_tokens", 0))),
    )
