"""A tiny local OpenAI-compatible HTTP server for wire-level tests (loopback only, no keys).

It lets the tests drive the *real* ``openai`` SDK over real HTTP, so request
serialisation, response parsing, finish reasons, HTTP error mapping and timeouts are
exercised exactly as they would be against the real API, without network or cost.

Each request pops the next scripted reply: ``(status, body, delay_seconds)``. ``body``
is a dict (sent as JSON) or bytes (sent as-is, e.g. to simulate a broken proxy page).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Not credential-shaped on purpose; the stub only checks that *a* bearer header is sent.
STUB_API_KEY = "local-stub-" + "not-a-real-key"


def completion(content: str | None = None, tool_calls: list[tuple[str, str, str]] | None = None,
               finish_reason: str | None = None, usage: tuple[int, int] = (50, 10),
               refusal: str | None = None) -> dict[str, Any]:
    """A chat.completion body. ``tool_calls`` items are (id, name, arguments-json)."""
    message: dict[str, Any] = {"role": "assistant", "content": content, "refusal": refusal}
    if tool_calls:
        message["tool_calls"] = [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
            for call_id, name, arguments in tool_calls
        ]
    return {
        "id": "chatcmpl-stub", "object": "chat.completion", "created": 1_760_000_000, "model": "stub-model",
        "choices": [{"index": 0, "message": message, "logprobs": None,
                     "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop")}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
    }


def error(status: int, code: str | None, message: str = "error", type_: str = "invalid_request_error",
          param: str | None = None) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"message": message, "type": type_, "param": param, "code": code}}


class StubOpenAI:
    def __init__(self) -> None:
        self.replies: list[tuple[int, Any, float]] = []
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep test output clean
                pass

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                with stub._lock:
                    stub.requests.append({
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "retry_count": self.headers.get("x-stainless-retry-count"),
                        "body": json.loads(raw or b"{}"),
                    })
                    status, body, delay = stub.replies.pop(0) if stub.replies else (500, b"no scripted reply", 0)
                if delay:
                    time.sleep(delay)
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "text/html" if isinstance(body, bytes) else "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("x-request-id", "req_stub123")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):  # client gave up (timeout tests)
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                                        daemon=True)

    def script(self, *replies: Any) -> StubOpenAI:
        """Each reply: a body dict (200), ``(status, body)`` or ``(status, body, delay)``."""
        for reply in replies:
            if isinstance(reply, dict):
                reply = (200, reply)
            status, body, *rest = reply
            self.replies.append((status, body, rest[0] if rest else 0.0))
        return self

    def client(self) -> Any:
        import openai

        return openai.OpenAI(api_key=STUB_API_KEY, base_url=self.base_url)

    def __enter__(self) -> StubOpenAI:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
