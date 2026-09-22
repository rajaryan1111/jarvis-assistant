# Contributing

Thanks for contributing to Jarvis.

## Local-first principles

Jarvis interacts with a local macOS environment and may handle private memory, face data, notes, and API credentials. Never commit personal data, face images, memory files, or secrets.

## Development

- Test changes on macOS when they depend on system commands, speech, camera access, or hardware-specific behavior.
- Keep API keys and local configuration in environment files excluded by Git.
- Prefer small, isolated changes to voice, vision, RAG, automation, and HUD components.
- Update the README when a user-facing capability or setup requirement changes.

## Pull requests

Describe the feature or fix, macOS-specific assumptions, tests performed, and any required environment variables. Include screenshots for HUD changes when useful.
