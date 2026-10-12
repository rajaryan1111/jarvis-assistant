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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

log = logging.getLogger("ultron.agent")

Role = Literal["system", "user", "assistant", "tool"]
ErrorKind = Literal["not_configured", "timeout", "unavailable", "rejected", "bad_response"]


@dataclass(frozen=True)
class ToolCall:
    """A tool call proposed by the model. ``arguments`` is the raw JSON text it produced."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelTurn:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


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

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


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
            "max_tokens": max_output_tokens,
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


def _map_error(exc: Exception) -> ProviderError:
    kind: ErrorKind = "unavailable"
    try:
        import openai

        if isinstance(exc, openai.APITimeoutError):
            kind = "timeout"
        elif isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            kind = "not_configured"
        elif isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError, openai.NotFoundError)):
            kind = "rejected"
        elif isinstance(exc, (openai.APIConnectionError, openai.APIStatusError)):
            kind = "unavailable"
        else:
            kind = "bad_response"
    except ImportError:  # pragma: no cover - openai is a core dependency
        kind = "unavailable"
    # Only the exception type is logged: provider messages can echo request details.
    log.warning("model provider error kind=%s type=%s", kind, type(exc).__name__)
    messages = {
        "not_configured": "The language model rejected the configured credentials.",
        "timeout": "The language model did not answer in time.",
        "unavailable": "The language model is unavailable right now.",
        "rejected": "The language model rejected the request.",
        "bad_response": "The language model returned an unexpected response.",
    }
    return ProviderError(kind, messages[kind])


def _parse_response(response: Any) -> ModelTurn:
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        raise ProviderError("bad_response", "The language model returned an unexpected response.") from None
    text = getattr(message, "content", None) or ""
    calls: list[ToolCall] = []
    for raw in getattr(message, "tool_calls", None) or []:
        function = getattr(raw, "function", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        call_id = getattr(raw, "id", None)
        if not isinstance(name, str) or not isinstance(call_id, str):
            raise ProviderError("bad_response", "The language model returned a malformed tool call.")
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments if isinstance(arguments, str) else ""))
    if not isinstance(text, str):
        text = ""
    return ModelTurn(text=text, tool_calls=tuple(calls))
