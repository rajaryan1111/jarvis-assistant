# Contributing

Thanks for contributing to Jarvis.

## Local-first principles

Jarvis interacts with a local macOS environment and may handle private memory, face data, notes, and API credentials. Never commit personal data, face images, memory files, or secrets.

## Development

- Test changes on macOS when they depend on system commands, speech, camera access, or hardware-specific behavior.
- Keep API keys and local configuration in environment files excluded by Git.
- Prefer small, isolated changes to voice, vision, RAG, automation, and HUD components.
- Update the README when a user-facing capability or setup requirement changes.
- Set up with `uv sync --extra dev`, then run `uv run pytest -q`, `uv run ruff check .` and
  `uv run python -m ultron check-secrets` before opening a pull request.
- Anything that changes state (power, files, apps, settings, camera, memory) must be a registered tool
  (`build_tool_registry()` in `main.py`, or a class in `ultron/tools/`) with a risk tier, reached only through
  `TOOLS.request(name, args, ctx)`. Add it to the matrix in `docs/M1_1_TOOL_SECURITY_BOUNDARY.md` and to
  `EXPECTED_TOOLS` in `tests/test_policy_boundary.py` (both are checked).
- Start processes only with `ultron.tools.process.run_command` (argv, absolute path, timeout), and create or
  replace files with `ultron.security.paths`. High-impact tools need confirmation and tests that use fake
  runners and temporary directories.
- Never execute text that comes from documents, web pages or LLM output.
- Approval of a pending action must go through `ToolRegistry.confirm`, which requires
  `CommandContext.can_confirm` (a signed-in HUD session, typed or clicked). Never add a path where voice, a
  bearer token, a background service or derived content can approve, and never speak or display a
  confirmation code to a voice or API client. Administrative operations (tokens, sessions, security-mode
  recovery) stay CLI-only and are never exposed over HTTP. See `docs/M1_2_IDENTITY_SESSIONS_VOICE.md`.

## Pull requests

Describe the feature or fix, macOS-specific assumptions, tests performed, and any required environment variables. Include screenshots for HUD changes when useful.
