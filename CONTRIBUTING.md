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
- Anything that changes system state (power, files, apps) must be a registered tool in `ultron/tools/` with a
  risk tier. Destructive tools need confirmation and tests that use fake runners and temporary directories.
- Never execute text that comes from documents, web pages or LLM output.

## Pull requests

Describe the feature or fix, macOS-specific assumptions, tests performed, and any required environment variables. Include screenshots for HUD changes when useful.
