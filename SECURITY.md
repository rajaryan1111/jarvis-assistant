# Security Policy

## Scope

Jarvis is a local computer-assistant project. Treat microphone input, screenshots, camera frames, local files, application controls, and credentials as sensitive.

## Reporting a vulnerability

Report security issues privately through the maintainer's GitHub profile instead of publishing exploit details in a public issue.

## Local safety

Do not commit API keys, private screenshots, face images, personal recordings, or secrets. Features that execute local commands should validate inputs and avoid destructive actions by default.

- Security model of the HUD/API server: see "Security model" in [README.md](README.md).
- Exposed credentials and manual rotation steps: [docs/SECURITY_CREDENTIAL_ROTATION.md](docs/SECURITY_CREDENTIAL_ROTATION.md).
- M1 remediation report and known limitations: [docs/M1_SECURITY_AND_FOUNDATION.md](docs/M1_SECURITY_AND_FOUNDATION.md).
- Before committing, run `uv run python -m ultron check-secrets` (CI runs it too).
