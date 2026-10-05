# Jarvis — AI Voice Assistant

[![CI](https://github.com/rajaryan1111/jarvis-assistant/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rajaryan1111/jarvis-assistant/actions/workflows/ci.yml)

A macOS-focused personal AI assistant combining **voice interaction, vision, retrieval, LLM tools, and desktop automation** in one Python application with a browser-based HUD.

> This is a personal engineering project, not a production security product. Face recognition, system controls, and API-backed features should be treated as local/demo functionality.

## What it demonstrates

- 🎙️ **Voice interface** — wake-word interaction, speech recognition and macOS text-to-speech.
- 🧠 **LLM + RAG** — OpenAI-backed reasoning and retrieval over a local notes/PDF knowledge base.
- 👁️ **Computer vision** — face recognition, person detection and hand-gesture utilities.
- 🖥️ **Desktop automation** — app/website launching, screenshots, brightness/volume controls and system diagnostics.
- ⏰ **Agent-like workflows** — reminders, study plans, local memory and sleep mode.
- 🌐 **Tool integrations** — weather, news, Wikipedia, web search, YouTube and cricket data.
- 🖥️ **Web HUD** — Flask backend with a browser-based futuristic control surface.

## Architecture

```text
Microphone / Browser HUD
          │
          ▼
   Python Assistant Core
          │
   ┌──────┼───────────────┐
   ▼      ▼               ▼
  LLM    RAG        Vision / macOS
   │      │               │
   └──────┴──────┬────────┘
                  ▼
          Tool / Action Layer
                  │
                  ▼
        Voice response / HUD
```

The project is intentionally local-first: personal memory, face data and credentials are excluded from source control.

## Selected engineering details

### LLM and RAG

- Environment-based OpenAI configuration.
- Local knowledge index for notes/PDF retrieval.
- Study-plan generation with an API-backed path and a fallback template.
- Natural-language commands mapped to application tools.

### Vision and security mode

- Face registration/recognition utilities.
- Person and hand detection helpers.
- Optional security mode for sensitive local actions.
- Security behavior is explicitly a **prototype convenience layer**, not a replacement for OS authentication.

### macOS automation

Examples include:

```text
"Jarvis, open VS Code"
"Increase brightness"
"Volume 50"
"Take a screenshot"
"System diagnostics"
```

The assistant uses native macOS commands where appropriate (`say`, `open`, `osascript`, `screencapture`).

## Testing and engineering hygiene

The repository includes GitHub Actions, Python syntax validation, unit tests for the NewsAPI integration, environment-based credentials, and a `.env.example` template.

Recent engineering work includes:

- moving the NewsAPI credential to environment configuration;
- adding request timeouts and graceful API/network failure handling;
- adding tests for success, missing credentials and network failures;
- documenting local configuration without exposing secrets.

## Tech stack

**Python** · Flask · OpenAI API · SpeechRecognition · psutil · OpenCV/vision utilities · requests · python-dotenv · HTML/CSS/JavaScript · macOS automation

## Run locally

```bash
git clone https://github.com/rajaryan1111/jarvis-assistant.git
cd jarvis-assistant
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add only your own credentials to `.env`, then start the assistant using the project entry point documented in the source/configuration.

### Environment variables

```env
OPENAI_API_KEY=your_openai_key_here
WEATHER_API_KEY=your_openweather_key_here
NEWS_API_KEY=your_newsapi_key_here
NEWS_COUNTRY=in
WEATHER_CITY=Delhi
CRICKET_API_KEY=your_cricketdata_key_here
```

Never commit a real `.env` file or API credential.

## Repository structure

```text
main.py               assistant CLI / command handling
server.py             Flask HUD server
static/               browser HUD assets
templates/            HUD HTML
vision.py             vision utilities
knowledge.py          local RAG / knowledge-base logic
news.py               NewsAPI integration
news_test.py          NewsAPI unit tests
client.py             OpenAI client example
wakeword_listener.py  wake-word support
```

## Limitations

- macOS-specific system commands are not portable to Windows/Linux.
- Some integrations require external API credentials.
- Vision/security features are experimental and should not be treated as strong authentication.
- The smart-home layer is currently a virtual state model rather than a real device controller.

## License / attribution

Personal portfolio project by **Raj Aryan**. Third-party services and libraries retain their respective licenses.
