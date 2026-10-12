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
- Action/risk matrix, centralized authorization design, confirmation rules and manual macOS checks:
  [docs/M1_1_TOOL_SECURITY_BOUNDARY.md](docs/M1_1_TOOL_SECURITY_BOUNDARY.md).
- Authentication vs authorization, session lifecycle and persistent revocation, token rotation, the voice
  trust model, owner recovery and remaining risks: [docs/M1_2_IDENTITY_SESSIONS_VOICE.md](docs/M1_2_IDENTITY_SESSIONS_VOICE.md).
- Agent loop (multi-step requests): the model only proposes calls to allowlisted tools; every call
  passes the same policy/approval/audit, the model can never approve or see confirmation codes, and
  web/news content cannot trigger actions without HUD approval:
  [docs/M2_0_AGENT_LOOP.md](docs/M2_0_AGENT_LOOP.md).
- Voice is not identity: spoken requests can never approve a high-impact action; only a signed-in HUD
  session can. The local tokens do not protect against someone who controls your OS account.
- Before committing, run `uv run python -m ultron check-secrets` (CI runs it too).
