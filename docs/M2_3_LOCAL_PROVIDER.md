# M2.3 — Local model provider (Ollama) and the local-only guard

## What this is

`ULTRON_PROVIDER=ollama` runs the **existing agent loop** — same runtime, tool registry, policy
engine, confirmations and audit ledger — against a **local** Ollama server through its
OpenAI-compatible endpoint. There is no credential: prompts and answers never leave the machine
through this provider. `ULTRON_LOCAL_ONLY=1` additionally refuses any remote agent provider,
so an owner can make "the agent talks to a local model or not at all" a configuration guarantee.

This is not a second application and not a new agent path: `OllamaChatProvider` subclasses the
same `OpenAIChatProvider` that OpenAI and Groq use, exactly like `GroqChatProvider` does.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ULTRON_PROVIDER` | `openai` | `openai`, `groq` or `ollama`. Unsupported values turn the agent off (never a silent fallback). |
| `ULTRON_OLLAMA_MODEL` | `llama3.1:latest` | Local model ID (`ollama list`). Must be pulled first and must support tool calling. Invalid names fall back to the default with a warning. |
| `ULTRON_OLLAMA_BASE_URL` | `http://127.0.0.1:11434/v1` | **Loopback only** (`localhost`, `127.0.0.1`, `[::1]`), `http(s)`, ≤200 chars. Any other URL sets `provider_error` and turns the agent off. |
| `ULTRON_LOCAL_ONLY` | `0` | `1` = the agent loop is off unless `ULTRON_PROVIDER=ollama`. Fails closed for `openai`, `groq` and the default. |
| `ULTRON_AGENT_MAX_OUTPUT_TOKENS` | `1024` for ollama | Same clamp (64–4096) as the other providers. |

Requirements: `ollama serve` running locally, and `ollama pull <model>` for a tool-calling model.
`uv run ultron doctor` shows the provider line, including the loopback base URL and the
`[local-only]` marker when the guard is on.

## Security and privacy properties (all regression-tested in `tests/test_ollama_provider.py`)

* **Loopback enforced twice.** `load_agent_config` validates `ULTRON_OLLAMA_BASE_URL`
  (scheme, host, length) and fails closed with a warning; `create_provider` re-checks the final
  URL, so neither an ambient `OPENAI_BASE_URL` nor a hand-passed override can point the "local"
  provider at a remote host. Look-alike hosts (`http://127.0.0.1.evil.example`) are rejected —
  the check is on the parsed hostname, not a substring.
* **No credential exists.** Nothing is invented, stored or logged. The OpenAI SDK requires a
  non-empty `api_key`, so the client is constructed with the constant `"ollama"`
  (`OLLAMA_SDK_KEY`), which Ollama ignores; it grants nothing and is not credential-shaped
  (the secret scanner and `redact()` are unaffected). `agent_provider_status` requires no key
  for `ollama` while OpenAI/Groq key requirements are unchanged.
* **Fail-closed everywhere.** Bad provider, non-loopback URL, or `ULTRON_LOCAL_ONLY=1` with a
  remote provider all produce a `provider_error`; the agent loop turns off and `doctor` says why.
* **Same trust boundaries.** Tool calls from a local model go through the identical validation →
  registry → policy → confirmation → audit path. Local models are *less* trusted infrastructure,
  not more: nothing about this provider relaxes a gate.
* **Error hygiene.** HTTP/timeout/connection failures map to the provider-neutral
  `ProviderError` kinds (`unavailable`, `timeout`, `rejected`, …); server bodies are never echoed
  into messages or logs.
* **No cloud-only request fields.** Only standard chat-completion fields are sent (no reasoning
  or sampling knobs), because local servers may reject unknown parameters.

### Honest scope of `ULTRON_LOCAL_ONLY`

The guard governs the **agent-loop model provider**. The legacy single-shot helpers
(`ask_gpt`, study planner, knowledge-base answers) and the non-LLM integrations (weather, news,
cricket) keep their own configuration; for a fully local setup, leave `OPENAI_API_KEY` and the
web-service keys unset — every feature that needs them then reports "key missing" instead of
calling out. The local inference server may keep its own logs; that is its configuration, not
this project's.

## Tests

19 deterministic tests, no Ollama install, no network, no credentials: config defaults and
fallbacks; loopback acceptance (incl. `[::1]` and custom ports); non-loopback rejection incl.
look-alike hosts; the local-only guard for `openai`/`groq`/default; factory behaviour
(no credential, `OPENAI_BASE_URL` cannot redirect, direct non-loopback overrides refused);
status lines; and wire-level tests driving the real SDK against the loopback stub
(`tests/openai_stub.py`): turn/usage mapping, tool-call parsing, HTTP-error and timeout mapping,
unreachable-server handling. Existing OpenAI/Groq behaviour is asserted unchanged.

Owners with a running local server can opt in to the real thing (never runs in CI; skipped
with start instructions when the server is down, free — prompts stay on the machine):

```
ULTRON_LIVE_OLLAMA=1 uv run pytest tests/live -p no:cacheprovider
```

## Provenance and licensing

The *concepts* — an OpenAI-compatible local-server provider restricted to loopback, a local-only
mode that excludes cloud model calls, and multiple configurable local model IDs — are publicly
documented behaviour of the G0DM0D3 project (AGPL-3.0, inspected read-only at revision
`f6301765fb90eb7b336bdf365319cd2fe44b1187`, see its `LOCAL_MODELS.md`). **No source code,
prompt text or configuration content was copied**; this is an original implementation on
Jarvis/ULTRON's own provider contract, following the existing Groq precedent in this repository.
Jarvis has no LICENSE file and the reference is AGPL-3.0; the licensing question remains open
and is an owner decision (nothing here claims clearance).

## Not included (deliberately)

* **Model discovery** (`GET /v1/models` "test & discover"): nice-to-have UX; `doctor` currently
  shows the configured model. Candidate follow-up.
* **Racing several local models**: multi-model work belongs to the opt-in ensemble line
  (PR #4, `arena/bd1537ca-jarvis-assistant`); building a second multi-model subsystem here
  would fragment ownership and duplicate its bounded-concurrency machinery.
* **Non-loopback "LAN" servers**: intentionally refused; the privacy promise is machine-local.
