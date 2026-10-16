import os
import json
import html
import logging
import datetime
import subprocess
import urllib.parse
import re
import webbrowser
import time
import threading
from pathlib import Path
from threading import Thread
from typing import Tuple

import requests
import wikipedia
import psutil
from openai import OpenAI

import vision  # optional vision utilities; degrade gracefully when OpenCV/MediaPipe are missing
import knowledge  # Personal Knowledge Base (RAG over notes/PDFs); optional heavy deps load lazily
import news
from ultron.agent.ensemble import Adviser, EnsembleProvider
from ultron.agent import router as smart_router
from ultron.agent.provider import create_provider
from ultron.agent.runtime import AgentLimits, AgentRuntime, RunRegistry, ToolExecutor
from ultron.config import (
    OPENAI_CLIENT_MAX_RETRIES,
    OPENAI_CLIENT_TIMEOUT,
    PROVIDER_KEYS,
    get_credential,
    load_agent_config,
    load_chat_model,
    load_dotenv_if_enabled,
    load_ensemble_config,
)
from ultron.history import recent as chat_history_recent, record_exchange as chat_history_record
from ultron.security.paths import atomic_write_text
from ultron.security.policy import CommandContext, RiskTier, voice_trust_from_env
from ultron.security.redact import redact
from ultron.voice import GoogleTranscriber, VoicePipeline, normalise_mode, profile_for
from ultron.voice.pipeline import FAILURE_MESSAGES, DEFAULT_FAILURE_MESSAGE
from ultron.tools.base import FunctionTool, ToolResult, ToolSpec
from ultron.tools.macos import (
    BrightnessTool,
    CpuTemperatureTool,
    OpenAppTool,
    OpenUrlTool,
    ScreenshotTool,
    SpeakTool,
    VolumeTool,
)
from ultron.tools.project import ProjectExplainTool, ProjectInspectTool, ProjectSearchTool
from ultron.tools.registry import ToolOutcome, ToolRegistry
from ultron.tools.system import ClearUserCachesTool, PowerTool

# ================== SETUP ==================
load_dotenv_if_enabled()

# Credentials are read from the environment / local .env only (never hardcoded).
OPENAI_API_KEY = get_credential("OPENAI_API_KEY")
WEATHER_API_KEY = get_credential("WEATHER_API_KEY")
NEWS_COUNTRY = os.getenv("NEWS_COUNTRY", "in")
DEFAULT_WEATHER_CITY = os.getenv("WEATHER_CITY", "Delhi")
CRICKET_API_KEY = get_credential("CRICKET_API_KEY")  # live cricket API key
GROQ_API_KEY = get_credential("GROQ_API_KEY")  # only used by the agent loop with ULTRON_PROVIDER=groq

CHAT_MODEL = load_chat_model()  # ULTRON_CHAT_MODEL; the agent uses AGENT_CONFIG.model
client = (OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_CLIENT_TIMEOUT, max_retries=OPENAI_CLIENT_MAX_RETRIES)
          if OPENAI_API_KEY else None)

MEMORY_FILE = os.getenv("ULTRON_MEMORY_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
WAKE_WORD = "jarvis"
jarvis_sleep = False

VOICE = "Daniel"  # macOS voice name
OWNER_FACE = "raj"  # name of the stored reference face used by "security mode"

# Background services act with their own internal identity. They can run only the ROUTINE
# tools that opt in (speak, camera presence checks) and can never confirm anything.
_REMINDER_CTX = CommandContext.internal("reminder-watcher")
_INTRUDER_CTX = CommandContext.internal("intruder-watcher")


# ================== POLICY BOUNDARY HELPERS ==================
# Every state-changing helper below is a thin wrapper: it builds explicit arguments and asks
# the tool registry (TOOLS, defined further down), which applies the risk policy for the
# caller's CommandContext. The implementations are the ``_tool_*`` functions, which are only
# referenced from build_tool_registry(). Calling a helper without a context counts as
# untrusted, so importing main and calling e.g. set_volume(50) directly does nothing.
def _outcome(tool_name: str, arguments: dict, ctx: CommandContext = None) -> ToolOutcome:
    return TOOLS.request(tool_name, arguments, ctx or CommandContext.untrusted("direct-call"))


def _act(tool_name: str, arguments: dict, ctx: CommandContext = None) -> str:
    return _outcome(tool_name, arguments, ctx).message


# ================== BASIC UTILITIES ==================
def speak(text: str, ctx: CommandContext = None) -> str:
    """
    Mac voice mode: speech comes from macOS 'say' through the ``audio.speak`` tool
    (absolute path, timeout, option-injection guard). Used by the CLI loop, /speak and
    the background watchers, each with its own context. Returns the tool outcome status.
    """
    if not text:
        return "completed"
    print(f"[JARVIS SPEAK] {text}")  # debug
    outcome = _outcome("audio.speak", {"text": text}, ctx)
    if outcome.status not in ("completed", "unavailable"):
        print("[JARVIS SPEAK]", outcome.message)
    return outcome.status


def listen(timeout=6, phrase_time_limit=8, language: str = None) -> str:
    """Microphone listening (only for CLI main loop). Requires the optional 'voice' extra.

    Recognition goes through the shared transcriber in :mod:`ultron.voice`, so the CLI
    loop and the wake-word listener behave identically. ``language`` selects the
    recognition locale (en|hi|hinglish|auto); it defaults to ``ULTRON_VOICE_LANGUAGE``,
    which is English - the historical behaviour. Failures print a short reason and
    return "", never raw recogniser text.
    """
    locale = profile_for(normalise_mode(language)).stt_locale
    result = GoogleTranscriber().transcribe(locale=locale, timeout=timeout, phrase_time_limit=phrase_time_limit)
    if result.ok:
        print(f"You: {result.text}")
        return result.text.lower()
    print("Listen error:", FAILURE_MESSAGES.get(result.error or "", DEFAULT_FAILURE_MESSAGE))
    return ""


# ================== MEMORY ==================
def _empty_memory():
    return {
        "notes": [],
        "profile": {},
        "favorites": {},
        "smarthome": {},
        "reminders": [],
        "security": {"enabled": False, "last_auth_time": 0, "auth_timeout_sec": 60},
        "plans": [],
    }


def _quarantine_unreadable_memory(exc: Exception) -> None:
    """Set an unparseable memory file aside so the owner's data is never destroyed.

    ``load_memory`` falls back to an empty structure when the file cannot be understood;
    without this the next ``save_memory`` would overwrite the file and erase real notes,
    reminders and profile. The file is renamed (recoverable), never deleted.
    """
    backup = Path(f"{MEMORY_FILE}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.replace(MEMORY_FILE, backup)
    except OSError:
        print("Memory load error:", type(exc).__name__, "(could not set the unreadable file aside)")
        return
    print(f"Memory file was unreadable ({type(exc).__name__}); moved it to {backup.name} and started fresh.")


def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return _empty_memory()
    try:
        # utf-8 is pinned: the file may hold any language, and the locale default is not.
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("memory file is not a JSON object")
        # Make sure all keys exist even for old files
        data.setdefault("notes", [])
        data.setdefault("profile", {})
        data.setdefault("favorites", {})
        data.setdefault("smarthome", {})
        data.setdefault("reminders", [])
        data.setdefault("security", {"enabled": False, "last_auth_time": 0, "auth_timeout_sec": 60})
        data.setdefault("plans", [])
        return data
    except ValueError as exc:
        # UnicodeDecodeError / JSONDecodeError / wrong shape: preserve, then start fresh.
        _quarantine_unreadable_memory(exc)
        return _empty_memory()
    except Exception:
        return _empty_memory()


_memory_lock = threading.RLock()


def save_memory(mem):
    """Atomic write so a crash or concurrent save cannot corrupt the file.

    The temp file gets a random name created with O_EXCL (never follows a planted
    symlink) and is renamed over memory.json; on failure the old file is untouched.
    """
    try:
        with _memory_lock:
            atomic_write_text(Path(MEMORY_FILE), json.dumps(mem, indent=2))
    except Exception as e:
        print("Memory save error:", type(e).__name__)


memory = load_memory()


def _parse_profile_update(cmd: str):
    """
    Learn from sentences like:
    - my name is raj
    - my favourite game is gta 5
    Returns explicit tool arguments, or None if the sentence teaches nothing.
    """
    text = cmd.lower()

    # Name
    if "my name is" in text:
        name = text.split("my name is", 1)[1].strip()
        if name:
            return {"field": "name", "value": name}

    # Favourites
    if "my favourite" in text or "my favorite" in text:
        key = "favourite" if "favourite" in text else "favorite"
        rest = text.split(key, 1)[1].strip()
        if " is " in rest:
            attr, value = rest.split(" is ", 1)
            attr = attr.strip()
            value = value.strip()
            if attr and value:
                return {"field": "favorite", "key": attr, "value": value}

    return None


def update_profile_from_sentence(cmd: str, ctx: CommandContext = None):
    update = _parse_profile_update(cmd)
    if update is None:
        return None
    return _act("profile.update", update, ctx)


def _tool_update_profile(args) -> ToolResult:
    value = str(args.get("value", "")).strip()
    if args.get("field") == "name" and value:
        memory.setdefault("profile", {})["name"] = value
        save_memory(memory)
        return ToolResult(True, f"Nice to meet you, {value}.")
    key = str(args.get("key", "")).strip()
    if args.get("field") == "favorite" and key and value:
        memory.setdefault("favorites", {})[key] = value
        save_memory(memory)
        return ToolResult(True, f"I will remember your favourite {key} is {value}.")
    return ToolResult(False, "I did not understand what to remember.")


def remember_note(fact: str, ctx: CommandContext = None) -> str:
    return _act("memory.add_note", {"fact": fact}, ctx)


def _tool_add_note(args) -> ToolResult:
    fact = str(args.get("fact", "")).strip()
    if not fact:
        return ToolResult(False, "What should I remember, sir?")
    memory.setdefault("notes", []).append(fact)
    save_memory(memory)
    return ToolResult(True, "Stored in my memory.")


def answer_profile_query(cmd: str):
    text = cmd.lower()
    prof = memory.get("profile", {})
    favs = memory.get("favorites", {})

    if "what is my name" in text:
        name = prof.get("name")
        return f"Your name is {name}." if name else "You have not told me your name yet."

    if "what is my favourite" in text or "what is my favorite" in text:
        key = text.replace("what is my favourite", "").replace("what is my favorite", "")
        key = key.replace("?", "").strip()
        if not key:
            return "Which favourite are you asking about, sir?"
        value = favs.get(key)
        if value:
            return f"Your favourite {key} is {value}."
        return "You did not tell me that yet."

    if "what do you know about me" in text:
        bits = []
        if prof.get("name"):
            bits.append(f"your name is {prof['name']}")
        for k, v in favs.items():
            bits.append(f"your favourite {k} is {v}")
        notes = memory.get("notes", [])
        if notes:
            bits.append("you told me: " + "; ".join(notes[:3]))
        if not bits:
            return "I do not know much yet, sir."
        return "Here is what I know about you: " + "; ".join(bits)

    return None


# ================== GPT BRAIN ==================
def ask_gpt(prompt: str, history):
    if not client:
        return "My OpenAI key is not configured."
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=history + [{"role": "user", "content": prompt}],
            max_completion_tokens=200,
        )
        return (resp.choices[0].message.content or "").strip() or "Sorry, I don't have an answer for that."
    except Exception as e:
        # Type only: provider messages can echo request details or part of the key.
        print("GPT error:", type(e).__name__)
        return "Sorry, I am having trouble thinking right now."


# ================== INTERNET SKILLS ==================
def play_youtube(query: str, ctx: CommandContext = None) -> str:
    search_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        res = requests.get(search_url, timeout=5)
        raise_for_status = getattr(res, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        video_ids = re.findall(r"watch\?v=([\w-]{11})", res.text)
        url = "https://www.youtube.com/watch?v=" + video_ids[0] if video_ids else search_url
        reply = f"Playing {query} on YouTube."
    except Exception as e:
        print("YouTube error:", type(e).__name__)
        url = search_url
        reply = f"There was an issue, but I opened YouTube search for {query}."
    outcome = _outcome("web.open_url", {"url": url}, ctx)
    return reply if outcome.status == "completed" else outcome.message


def _weather_report(city: str) -> ToolResult:
    """Current weather for ``city`` (read-only network call). Shared by the router and the agent."""
    if not WEATHER_API_KEY:
        return ToolResult(False, "Weather API key is not configured.")
    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": WEATHER_API_KEY, "units": "metric"},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("cod") != 200:
            return ToolResult(False, f"Weather error for {city}.")
        main_data = data.get("main")
        conditions = data.get("weather")
        if not isinstance(main_data, dict) or not isinstance(conditions, list) or not conditions:
            return ToolResult(False, f"Weather data for {city} was incomplete.")
        raw_temp = main_data.get("temp")
        desc = conditions[0].get("description") if isinstance(conditions[0], dict) else None
        if (
            isinstance(raw_temp, bool)
            or not isinstance(raw_temp, (int, float))
            or not isinstance(desc, str)
            or not desc.strip()
        ):
            return ToolResult(False, f"Weather data for {city} was incomplete.")
        temp = int(raw_temp)
        desc = desc.strip()
        return ToolResult(True, f"The weather in {city} is {desc}, {temp}°C.",
                          {"city": city, "temperature_c": temp, "description": desc})
    except Exception as e:
        print("Weather error:", redact(e))  # request errors embed the URL, including the key
        return ToolResult(False, "Sorry, I could not fetch the weather.")


def get_weather(cmd: str) -> str:
    city = DEFAULT_WEATHER_CITY
    if " in " in cmd:
        city = cmd.split(" in ", 1)[1].strip()
    return _weather_report(city).message


def get_news() -> str:
    """Top headlines via news.py (key sent in a header, never in the URL)."""
    if not get_credential("NEWS_API_KEY"):
        return "News API key is not configured."
    heads = news.get_headlines()[:5]
    if not heads:
        return "Sorry, I could not fetch the news right now."
    return "Here are top headlines. " + " ".join(
        [f"Headline {i+1}: {h}." for i, h in enumerate(heads)]
    )


def _wiki_summary_report(topic: str, *, not_found_message: str | None = None) -> ToolResult:
    """Two-sentence Wikipedia summary with explicit disambiguation, missing-page, empty, and network handling."""
    clean_topic = str(topic or "").strip()
    if not clean_topic:
        return ToolResult(False, "Please tell me what to look up.", {"topic": "", "status": "empty_query"})
    try:
        raw = wikipedia.summary(clean_topic, sentences=2)
    except wikipedia.exceptions.DisambiguationError as e:
        raw_options = getattr(e, "options", None) or []
        options = [str(opt).strip() for opt in raw_options if isinstance(opt, str) and str(opt).strip()][:5]
        hint = f" Options include: {', '.join(options)}." if options else ""
        return ToolResult(
            False,
            f"'{clean_topic}' is ambiguous on Wikipedia.{hint}",
            {"topic": clean_topic, "status": "ambiguous", "options": options, "source": "wikipedia"},
        )
    except wikipedia.exceptions.PageError:
        msg = not_found_message or f"I could not find a Wikipedia page for {clean_topic}."
        return ToolResult(False, msg, {"topic": clean_topic, "status": "not_found", "source": "wikipedia"})
    except (requests.RequestException, wikipedia.exceptions.HTTPTimeoutError) as e:
        print("Wiki error:", type(e).__name__)
        return ToolResult(
            False,
            "Wikipedia is unavailable right now.",
            {"topic": clean_topic, "status": "unavailable", "source": "wikipedia"},
        )
    except Exception as e:
        print("Wiki error:", type(e).__name__)
        msg = not_found_message or f"I could not find information on {clean_topic}."
        return ToolResult(False, msg, {"topic": clean_topic, "status": "error", "source": "wikipedia"})

    summary = str(raw or "").strip()
    if not summary:
        msg = not_found_message or f"I could not find information on {clean_topic}."
        return ToolResult(False, msg, {"topic": clean_topic, "status": "empty", "source": "wikipedia"})
    return ToolResult(True, summary, {"topic": clean_topic, "status": "verified", "source": "wikipedia"})


def wiki(cmd: str) -> str:
    topic = (
        cmd.replace("who is", "")
        .replace("what is", "")
        .replace("tell me about", "")
        .strip()
    )
    if not topic:
        return "Please tell me what to search for."
    return _wiki_summary_report(topic, not_found_message="I could not find information on that.").message


# ================== WEB SEARCH (DUCKDUCKGO) ==================
def _normalize_search_url(raw_href: str | None) -> str | None:
    """Extract and validate an HTTP(S) result URL from a DuckDuckGo anchor href."""
    if not raw_href or not isinstance(raw_href, str):
        return None
    candidate = html.unescape(raw_href).strip()
    if not candidate:
        return None
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    parts = urllib.parse.urlsplit(candidate)
    if (
        (parts.netloc.lower().endswith("duckduckgo.com") or not parts.scheme)
        and parts.path.startswith("/l/")
    ):
        uddg = urllib.parse.parse_qs(parts.query).get("uddg")
        if uddg and uddg[0]:
            candidate = uddg[0].strip()
            parts = urllib.parse.urlsplit(candidate)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.strip().lower()
    if scheme not in ("http", "https") or not netloc or ("." not in netloc and netloc != "localhost"):
        return None
    if any(ch.isspace() for ch in candidate):
        return None
    return urllib.parse.urlunsplit((scheme, netloc, parts.path, parts.query, ""))


def _ddg_fetch_results(query: str) -> list[dict]:
    """Top search results (title and verified HTTP(S) URL when available) from DuckDuckGo HTML."""
    url_html = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        res = requests.get(url_html, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        raise_for_status = getattr(res, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        anchors = re.findall(
            r'<a\b([^>]*\bclass=["\'][^"\']*\bresult__a\b[^"\']*["\'][^>]*)>(.*?)</a>',
            res.text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    except Exception as e:
        print("Web search error:", type(e).__name__)
        return []
    results = []
    for attrs, inner_html in anchors:
        title = html.unescape(re.sub(r"<.*?>", "", inner_html, flags=re.DOTALL)).strip()
        if not title:
            continue
        href_match = re.search(r'\bhref=["\']([^"\']+)["\']', attrs, flags=re.IGNORECASE)
        url = _normalize_search_url(href_match.group(1) if href_match else None)
        results.append({"title": title, "url": url, "source": "duckduckgo"})
    return results


def _ddg_result_titles(query: str) -> list:
    """Top result titles from DuckDuckGo's HTML page (read-only; [] on any error)."""
    return [item["title"] for item in _ddg_fetch_results(query)]


_DEFAULT_DDG_RESULT_TITLES = _ddg_result_titles


def _ddg_results(query: str) -> list[dict]:
    """Structured DuckDuckGo search results; honors monkeypatched ``_ddg_result_titles`` in tests."""
    if _ddg_result_titles is not _DEFAULT_DDG_RESULT_TITLES:
        return [
            {"title": str(title).strip(), "url": None, "source": "duckduckgo"}
            for title in _ddg_result_titles(query)
            if str(title).strip()
        ]
    return _ddg_fetch_results(query)


def web_search_ddg(query: str, ctx: CommandContext = None) -> str:
    """
    Simple real-time web search using DuckDuckGo.
    - Opens full results in browser
    - Tries to read top 2–3 result titles and speak them
    """
    url_main = "https://duckduckgo.com/?q=" + urllib.parse.quote(query)
    matches = _ddg_result_titles(query)

    # Opening the browser is a ROUTINE action; reading result titles is read-only, so an
    # untrusted caller still gets the titles but never a browser window.
    opened = _outcome("web.open_url", {"url": url_main}, ctx)
    if matches:
        titles = matches[:3]
        return f"Here is what I found about {query}: {' ; '.join(titles)}."
    if opened.status == "completed":
        return f"I opened web results for {query} in your browser, sir."
    return opened.message


# ================== LIVE CRICKET (CRICKETDATA) ==================
def get_live_cricket_score(cmd: str) -> str:
    """
    Uses CricketData currentMatches API to get live scores.
    You must set CRICKET_API_KEY in .env.
    """
    if not CRICKET_API_KEY:
        return "Live cricket API key is not configured in my system, sir."

    try:
        resp = requests.get(
            "https://api.cricapi.com/v1/currentMatches",
            params={"apikey": CRICKET_API_KEY, "offset": 0},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return "The cricket API returned an invalid response, sir."
        if data.get("status") == "failure" or data.get("error"):
            return "The cricket service could not provide live scores right now, sir."

        # Different API versions may use different keys; validate before processing.
        raw_matches = data.get("data") or data.get("matches") or []
        if not isinstance(raw_matches, list):
            return "The cricket API returned invalid match data, sir."
        matches = [match for match in raw_matches if isinstance(match, dict)]
        if not matches:
            return "I did not find any ongoing match right now, sir."

        cmd_lower = cmd.lower()
        target = None

        # Try to prioritise India match if user mentions India
        if "india" in cmd_lower:
            for m in matches:
                name = (m.get("name") or m.get("match") or "").lower()
                teams = (m.get("teams") or m.get("team") or [])
                teams_text = " ".join(teams).lower() if isinstance(teams, list) else str(teams).lower()
                if "india" in name or "india" in teams_text:
                    target = m
                    break

        # Fallback: just take the first match
        if target is None:
            target = matches[0]

        name = target.get("name") or target.get("match") or "A cricket match"
        status = target.get("status") or target.get("matchStatus") or target.get("match_status") or ""

        # Score may be a list or single dict or nested under a key
        raw_score = target.get("score") or target.get("scorecard") or target.get("score_full") or []

        innings_list = []
        if isinstance(raw_score, list):
            innings_list = raw_score
        elif isinstance(raw_score, dict):
            innings_list = [raw_score]

        parts = []
        for inn in innings_list:
            if not isinstance(inn, dict):
                continue
            r = inn.get("r") or inn.get("runs") or "?"
            w = inn.get("w") or inn.get("wickets") or "?"
            o = inn.get("o") or inn.get("overs") or "?"
            inn_name = inn.get("inning") or inn.get("inningName") or inn.get("team") or ""
            if inn_name:
                parts.append(f"{inn_name}: {r}/{w} in {o} overs")
            else:
                parts.append(f"{r}/{w} in {o} overs")

        score_text = " | ".join(parts) if parts else "Score details are not available yet, sir."

        if status:
            return f"{name}. {score_text}. Status: {status}."
        else:
            return f"{name}. {score_text}."
    except Exception as e:
        print("Cricket live error:", redact(e))
        return "I tried to fetch the live cricket score, but something went wrong, sir."


# ================== SYSTEM INTELLIGENCE ==================
def get_cpu_temperature(ctx: CommandContext = None):
    """
    Uses 'osx-cpu-temp' (installed via Homebrew) through the read-only
    ``system.cpu_temperature`` tool (fixed absolute path, 5 s timeout).
    Returns a string like '46.5°C' or None if it fails.
    """
    outcome = _outcome("system.cpu_temperature", {}, ctx)
    if outcome.status == "completed" and outcome.result is not None:
        return outcome.result.data.get("temperature")
    return None


def system_report() -> str:
    """
    Combines CPU, RAM, battery, and temperature into a spoken-style status.
    """
    try:
        cpu = psutil.cpu_percent()
    except Exception as e:
        print("CPU percent error:", e)
        cpu = None

    try:
        ram = psutil.virtual_memory().percent
    except Exception as e:
        print("RAM percent error:", e)
        ram = None

    try:
        bat = psutil.sensors_battery()
        bat_str = f"{bat.percent}%" if bat else "N/A"
    except Exception as e:
        print("Battery read error:", e)
        bat_str = "N/A"

    temp = get_cpu_temperature()

    parts = ["System diagnostics:"]
    if cpu is not None:
        parts.append(f"CPU at {cpu} percent")
    if ram is not None:
        parts.append(f"RAM usage {ram} percent")
    parts.append(f"battery {bat_str}")

    base = ", ".join(parts) + ". "

    if temp:
        base += f"CPU temperature {temp}. Running smooth as butter, sir."
    else:
        base += "Temperature sensors not responding, but performance seems normal, sir."

    return base


# Cache cleanup now lives in ultron.tools.system.ClearUserCachesTool: no shell, no globbing,
# symlinks never followed, and it only runs after an explicit, code-bound confirmation.


# ================== APP / SYSTEM CONTROL ==================
def launch_any_app(app_name: str, ctx: CommandContext = None) -> str:
    """
    Launch macOS apps by voice.
    - First tries to open a real .app (``apps.open`` tool: validated name, no options)
    - If that fails, falls back to opening a website in the browser
      (facebook, linkedin, youtube, etc.) through the ``web.open_url`` tool.
    """
    raw = app_name.strip()
    if not raw:
        return "Which app should I open, sir?"

    lower = raw.lower()

    # ===== Spoken-name → real app mapping =====
    mapping = {
        "canva": "Canva",
        "chrome": "Google Chrome",
        "google chrome": "Google Chrome",
        "vs code": "Visual Studio Code",
        "visual studio code": "Visual Studio Code",
        "settings": "System Settings",
        "system settings": "System Settings",
        "finder": "Finder",
        "music": "Music",
        "itunes": "Music",
        "messages": "Messages",
        "photos": "Photos",
        "whatsapp": "WhatsApp",
        "spotify": "Spotify",
        "safari": "Safari",
        "terminal": "Terminal",
    }
    app_to_open = "Canva" if "canva" in lower else mapping.get(lower, raw)

    # ===== 1) Try to open as a native macOS app =====
    outcome = _outcome("apps.open", {"app": app_to_open}, ctx)
    if outcome.status in ("completed", "denied"):
        return outcome.message

    # ===== 2) Fallback: treat as website and open in browser =====

    # Normalize spoken website names: "facebook dot com" → "facebook.com"
    site_name = lower.replace(" dot ", ".").replace(" ", "")
    # Some friendly aliases
    site_aliases = {
        "youtube": "youtube.com",
        "yt": "youtube.com",
        "facebook": "facebook.com",
        "fb": "facebook.com",
        "linkedin": "linkedin.com",
        "insta": "instagram.com",
        "instagram": "instagram.com",
        "x": "x.com",
        "twitter": "twitter.com",
        "gmail": "mail.google.com",
        "google": "google.com",
    }
    site_name = site_aliases.get(site_name, site_name)

    # If there is no dot at all, assume .com
    if "." not in site_name:
        site_name += ".com"

    # Only plain host names (letters, digits, dots, dashes) become URLs.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", site_name):
        return f"I could not open {app_name}, sir."

    opened = _outcome("web.open_url", {"url": "https://" + site_name}, ctx)
    if opened.status == "completed":
        return f"Opening {site_name} in your browser, sir."
    return opened.message


def change_brightness_relative(direction: str, steps: int = 4, ctx: CommandContext = None) -> str:
    return _act("system.brightness", {"direction": direction, "steps": steps}, ctx)


def set_volume(level: int, ctx: CommandContext = None) -> str:
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "Tell me the volume level, like volume 50."
    return _act("system.set_volume", {"level": level}, ctx)


def take_screenshot(ctx: CommandContext = None) -> str:
    return _act("system.screenshot", {}, ctx)


def tell_time() -> str:
    now = datetime.datetime.now()
    return f"It is {now.strftime('%I:%M %p on %A, %d %B %Y')}."


# ================== STATUS FOR HUD ==================
def get_status():
    """Used by /status route for time + battery."""
    now = datetime.datetime.now().strftime("%H:%M:%S")
    try:
        bat = psutil.sensors_battery()
        battery = bat.percent if bat else None
    except Exception:
        battery = None
    return {"time": now, "battery": battery}


# ================== SMART HOME (VIRTUAL) ==================
def control_smart_home(cmd: str, ctx: CommandContext = None) -> str:
    return _act("smarthome.control", {"command": cmd}, ctx)


def _tool_control_smart_home(cmd: str) -> str:
    """
    Virtual smart home controller.
    You don't have physical devices yet, so:
    - It parses commands like 'turn on the room lights'
    - Stores them in memory['smarthome'] as virtual device states
    Later, you can connect these to real APIs (Tuya/Alexa/etc).
    """
    text = cmd.lower()
    state = memory.setdefault("smarthome", {})

    # On / off
    action = None
    if "turn on" in text or "switch on" in text:
        action = "on"
    elif "turn off" in text or "switch off" in text:
        action = "off"

    device = None
    for name in ["light", "lights", "fan", "ac", "air conditioner", "plug", "socket", "lamp"]:
        if name in text:
            device = name
            break

    room = None
    for r in ["bedroom", "hall", "living room", "kitchen", "room"]:
        if r in text:
            room = r
            break

    # Temperature set: "set ac to 24", "set temperature to 22"
    if "temperature" in text or "ac" in text:
        nums = [int(s) for s in text.split() if s.isdigit()]
        if nums:
            temp = nums[0]
            state["ac_temperature"] = temp
            memory["smarthome"] = state
            save_memory(memory)
            return f"Setting virtual AC temperature to {temp} degrees, sir. Once a real AC is connected, I will send this setting to it."

    if action and device:
        room_label = room or "room"
        key = f"{room_label}_{device}"
        state[key] = action
        memory["smarthome"] = state
        save_memory(memory)
        return f"Virtual smart home: turning {action} your {device} in the {room_label}, sir. When you connect a real smart device, I can trigger it here."

    return "This sounds like a smart home command, sir, but you don't have any real devices linked yet. I have logged it in my virtual home state."


# ================== REMINDERS ==================
def set_reminder(cmd: str, ctx: CommandContext = None) -> str:
    return _act("reminders.add", {"command": cmd}, ctx)


def _tool_set_reminder(cmd: str) -> str:
    """
    Advanced reminder parser.
    Supports:
    - remind me in 10 minutes to drink water
    - remind me in 2 hours to study
    - remind me at 8 pm to sleep
    - remind me tomorrow at 9 am to go college
    - remind me on monday at 7 am to go gym
    - remind me every day at 7 am to wake up
    """
    global memory

    text_full = cmd.lower()
    if "remind me" in text_full:
        text = text_full.split("remind me", 1)[1].strip()
    else:
        text = text_full.strip()

    if not text:
        return "What should I remind you about, sir?"

    now = datetime.datetime.now()

    def parse_time_hm(h, m, ampm):
        h = int(h)
        m = int(m) if m is not None else 0
        if ampm:
            ampm = ampm.lower()
            if ampm == "pm" and h != 12:
                h += 12
            if ampm == "am" and h == 12:
                h = 0
        return h, m

    repeat = None
    trigger_dt = None
    desc = None

    # 1) EVERY DAY at HH(:MM)? am/pm  (repeating)
    m = re.search(r"every day at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if m:
        repeat = "daily"
        h, mi = parse_time_hm(m.group(1), m.group(2), m.group(3))
        trigger_dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if trigger_dt <= now:
            trigger_dt += datetime.timedelta(days=1)
        desc = text.replace(m.group(0), "").strip()
        if desc.startswith("to "):
            desc = desc[3:]

    # 2) TOMORROW [at HH(:MM)? am/pm]
    if trigger_dt is None and "tomorrow" in text:
        base_date = now.date() + datetime.timedelta(days=1)
        m = re.search(r"tomorrow(?: at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?", text)
        if m:
            h_raw = m.group(1) or 9
            h, mi = parse_time_hm(h_raw, m.group(2), m.group(3))
            trigger_dt = datetime.datetime.combine(base_date, datetime.time(hour=h, minute=mi))
            desc = text.replace(m.group(0), "", 1).strip()  # remove "tomorrow at 9 am", not just "tomorrow"
        else:
            trigger_dt = datetime.datetime.combine(base_date, datetime.time(hour=9, minute=0))
            desc = text.replace("tomorrow", "").strip()
        if desc.startswith("at "):
            desc = desc[3:]
        if desc.startswith("to "):
            desc = desc[3:]

    # 3) on <weekday> [at HH(:MM)? am/pm]
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if trigger_dt is None:
        for wd in weekdays:
            marker = "on " + wd
            if marker in text:
                idx = weekdays.index(wd)
                today_idx = now.weekday()  # Monday=0
                days_ahead = (idx - today_idx) % 7
                if days_ahead == 0:
                    days_ahead = 7
                base_date = now.date() + datetime.timedelta(days=days_ahead)
                m = re.search(marker + r"(?: at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?", text)
                if m:
                    h_raw = m.group(1) or 9
                    h, mi = parse_time_hm(h_raw, m.group(2), m.group(3))
                    trigger_dt = datetime.datetime.combine(base_date, datetime.time(hour=h, minute=mi))
                else:
                    trigger_dt = datetime.datetime.combine(base_date, datetime.time(hour=9, minute=0))
                desc = text.replace(m.group(0), "", 1).strip() if m else text.replace(marker, "", 1).strip()
                if desc.startswith("at "):
                    desc = desc[3:]
                if desc.startswith("to "):
                    desc = desc[3:]
                break

    # 4) at HH(:MM)? am/pm  (today/next day)
    if trigger_dt is None:
        m = re.search(r"at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if m:
            h, mi = parse_time_hm(m.group(1), m.group(2), m.group(3))
            trigger_dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if trigger_dt <= now:
                trigger_dt += datetime.timedelta(days=1)
            desc = text.replace(m.group(0), "").strip()
            if desc.startswith("to "):
                desc = desc[3:]

    # 5) in X minutes/hours  (existing behaviour)
    if trigger_dt is None:
        # Plural forms must match fully, otherwise "minutes" leaves a stray "s" in the text.
        m = re.search(r"in\s+(\d+)\s+(minutes?|hours?)\b", text)
        if m:
            amount = int(m.group(1))
            unit = m.group(2)
            if "minute" in unit:
                delta = datetime.timedelta(minutes=amount)
                human_unit = "minutes"
            else:
                delta = datetime.timedelta(hours=amount)
                human_unit = "hours"
            trigger_dt = now + delta
            desc = text.replace(m.group(0), "").strip()
            if desc.startswith("to "):
                desc = desc[3:]
        else:
            human_unit = None

    if trigger_dt is None:
        return "I could not understand the time for this reminder, sir. Try saying 'remind me in 10 minutes to drink water' or 'remind me tomorrow at 8 PM to study'."

    if not desc:
        desc = "your reminder"

    # Store reminder
    reminder = {
        "text": desc,
        "time": float(trigger_dt.timestamp()),
        "repeat": repeat,
    }
    memory.setdefault("reminders", []).append(reminder)
    save_memory(memory)

    if repeat == "daily":
        return f"Daily reminder set for {trigger_dt.strftime('%I:%M %p')}, sir. I will remind you to {desc} every day."
    elif "human_unit" in locals() and human_unit:
        return f"Reminder set for {amount} {human_unit} from now, sir. I will remind you to {desc}."
    else:
        return f"Reminder set for {trigger_dt.strftime('%I:%M %p on %A')}, sir. I will remind you to {desc}."


def list_reminders() -> str:
    rlist = memory.get("reminders", [])
    if not rlist:
        return "You have no active reminders, sir."
    lines = []
    for i, r in enumerate(rlist, start=1):
        try:
            t = float(r.get("time", 0))
            dt = datetime.datetime.fromtimestamp(t)
            when_str = dt.strftime("%I:%M %p on %A")
        except Exception:
            when_str = "an unknown time"
        text = r.get("text", "something")
        repeat = r.get("repeat")
        if repeat == "daily":
            lines.append(f"{i}. {text} at {when_str} [daily]")
        else:
            lines.append(f"{i}. {text} at {when_str}")
    return "Here are your reminders: " + " ; ".join(lines)


def clear_reminders(ctx: CommandContext = None) -> str:
    """Asks for confirmation first (DESTRUCTIVE); nothing is deleted by this call itself."""
    return _act("reminders.clear_all", {}, ctx)


def _tool_clear_reminders() -> str:
    memory["reminders"] = []
    save_memory(memory)
    return "I have cleared all reminders, sir."


def reminder_watcher():
    """
    Background loop to check reminders every 30 seconds.
    - One-time reminders: removed after firing
    - Daily reminders: rescheduled for next day
    """
    global memory
    while True:
        try:
            time.sleep(30)
            now_ts = time.time()
            rlist = memory.setdefault("reminders", [])
            if not rlist:
                continue

            remaining = []
            for r in rlist:
                try:
                    t = float(r.get("time", 0))
                    repeat = r.get("repeat")
                    if now_ts >= t:
                        speak(f"Reminder, sir: {r.get('text', 'something you asked me to remember.')}", _REMINDER_CTX)
                        if repeat == "daily":
                            # Shift forward 1 day (or more if we are very late)
                            next_t = t
                            one_day = 24 * 3600
                            while next_t <= now_ts:
                                next_t += one_day
                            r["time"] = next_t
                            remaining.append(r)
                        # one-time reminders are NOT added back
                    else:
                        remaining.append(r)
                except Exception as e:
                    print("Reminder item error:", e)

            if len(remaining) != len(rlist):
                memory["reminders"] = remaining
                save_memory(memory)
        except Exception as e:
            print("Reminder watcher loop error:", e)


# ================== SECURITY MODE (AI SECURITY) ==================
def _get_security_state():
    sec = memory.setdefault("security", {"enabled": False, "last_auth_time": 0, "auth_timeout_sec": 60})
    if "auth_timeout_sec" not in sec:
        sec["auth_timeout_sec"] = 60
    return sec


def is_security_enabled() -> bool:
    sec = _get_security_state()
    return bool(sec.get("enabled", False))


def enable_security_mode(ctx: CommandContext = None) -> str:
    """Turning protection ON is ROUTINE: it only makes later actions stricter."""
    return _act("security.enable", {}, ctx)


def disable_security_mode(ctx: CommandContext = None) -> str:
    """Turning protection OFF needs confirmation, and the confirmation itself passes the
    security-mode gate (face check) while the mode is still on, so disabling the feature
    can never be used to skip the check it provides."""
    if not is_security_enabled():
        return "Security mode is already disabled, sir."
    return _act("security.disable", {}, ctx)


def _tool_enable_security() -> str:
    sec = _get_security_state()
    sec["enabled"] = True
    sec.setdefault("last_auth_time", 0)
    sec.setdefault("auth_timeout_sec", 60)
    memory["security"] = sec
    save_memory(memory)
    # Requests made under the old posture must be asked for again.
    TOOLS.invalidate_all("security mode enabled")
    return "Security mode enabled, sir. Sensitive commands will now require your face."


def _tool_disable_security() -> str:
    sec = _get_security_state()
    sec["enabled"] = False
    sec["last_auth_time"] = 0
    memory["security"] = sec
    save_memory(memory)
    TOOLS.invalidate_all("security mode disabled")
    return "Security mode disabled, sir. I will execute commands normally."


def disable_security_mode_offline() -> str:
    """Owner recovery path, used only by `python -m ultron security disable` on this Mac.

    It edits the local memory file directly, so it requires the same OS-account access as
    reading the API token or editing memory.json by hand; it is not reachable from voice,
    the HUD or the API. Exists so a missing camera/vision extra cannot lock the owner out.
    """
    global memory
    memory = load_memory()
    sec = _get_security_state()
    was_enabled = bool(sec.get("enabled"))
    sec["enabled"] = False
    sec["last_auth_time"] = 0
    save_memory(memory)
    return "Security mode disabled." if was_enabled else "Security mode was already disabled."


def verify_identity(ctx: CommandContext = None) -> str:
    return _act("security.verify_identity", {}, ctx)


def _tool_verify_identity() -> ToolResult:
    ok, msg = security_check()
    if ok and not msg:
        return ToolResult(True, "Identity already verified recently, sir.")
    return ToolResult(ok, msg)


def security_status() -> str:
    sec = _get_security_state()
    if sec.get("enabled"):
        return "Security mode is currently enabled, sir."
    else:
        return "Security mode is currently disabled, sir."


def _mark_authenticated():
    sec = _get_security_state()
    sec["last_auth_time"] = time.time()
    memory["security"] = sec
    save_memory(memory)


def security_check() -> Tuple[bool, str]:
    """
    Returns (ok, message).
    - If security is OFF → (True, "")
    - If security is ON:
        * If recently authenticated → (True, "")
        * Else → run face check via vision.
    """
    sec = _get_security_state()
    if not sec.get("enabled", False):
        return True, ""

    now_ts = time.time()
    last_auth = float(sec.get("last_auth_time", 0))
    timeout = float(sec.get("auth_timeout_sec", 60))

    if now_ts - last_auth < timeout:
        return True, ""

    if not vision.face_available():
        # Fail closed: security mode is on but the camera check cannot run.
        return False, ("Security mode is enabled, but vision support is not installed, so I cannot verify you. "
                       "Install the 'vision' extra, or on this Mac run `python -m ultron security disable`.")

    # Need fresh face verification (prototype only; not real authentication)
    same, score = vision.recognize_face(OWNER_FACE)
    if score is None:
        return False, ("Security check failed, sir. I do not have your face stored. Register it while security "
                       "mode is off, or run `python -m ultron security disable` on this Mac.")

    if same:
        _mark_authenticated()
        return True, "Identity verified, sir."
    else:
        return False, "Security check failed, sir. The person in front of the camera does not match your stored face."


def intruder_check_once() -> bool:
    """One intruder check through the policy boundary (internal context). True if alerted."""
    # Quick cheap check first
    seen = _outcome("vision.detect_person", {}, _INTRUDER_CTX)
    if seen.status != "completed" or not seen.result.data.get("seen"):
        return False
    owner = _outcome("vision.recognize_owner", {}, _INTRUDER_CTX)
    if owner.status != "completed" or owner.result.data.get("score") is None:
        return False  # no reference face (or camera failure): skip
    if owner.result.data.get("same"):
        return False
    speak("Intruder detected, sir. Triggering red alert protocol.", _INTRUDER_CTX)
    return True


def intruder_watcher():
    """
    Background loop:
    - Only runs checks when security mode is enabled.
    - If it sees a person who is NOT raj, triggers spoken intruder alert.
    """
    while True:
        try:
            time.sleep(10)
            if not is_security_enabled() or not vision.face_available():
                continue
            intruder_check_once()
        except Exception as e:
            print("Intruder watcher error:", e)


# ================== STUDY PLANNER (AUTONOMOUS TASK PLANNER) ==================
def _normalize_topic(text: str) -> str:
    return (text or "").strip().lower()


def create_study_plan(topic: str, ctx: CommandContext = None) -> str:
    return _act("study.create_plan", {"topic": topic}, ctx)


def _tool_create_study_plan(topic: str) -> str:
    """
    Uses GPT to generate a study plan for given topic.
    Stores in memory['plans'].
    """
    global memory
    topic_norm = _normalize_topic(topic)
    if not topic_norm:
        return "Which topic should I create a study plan for, sir?"

    base_prompt = (
        f"Create a concise, practical 7-day study plan for the topic: {topic}.\n"
        f"Keep it focused on daily tasks. For each day, give a short title and 2-4 bullet points. "
        f"User is a college student with basic programming background."
    )

    if not client:
        # Fallback plan without GPT
        plan_text = (
            f"Study plan for {topic} (offline template):\n"
            "- Day 1: Watch an introduction video and read basics.\n"
            "- Day 2: Learn core concepts and definitions.\n"
            "- Day 3: Practice basic questions.\n"
            "- Day 4: Learn advanced concepts.\n"
            "- Day 5: Practice mixed problems.\n"
            "- Day 6: Revise notes.\n"
            "- Day 7: Full revision and mock test.\n"
        )
    else:
        try:
            resp = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful study planner."},
                    {"role": "user", "content": base_prompt},
                ],
                max_completion_tokens=400,
            )
            plan_text = (resp.choices[0].message.content or "").strip()
            if not plan_text:
                raise ValueError("empty plan")
        except Exception as e:
            print("Study planner GPT error:", type(e).__name__)
            plan_text = (
                f"Study plan for {topic} (fallback):\n"
                "- Day 1: Learn basics.\n"
                "- Day 2: Learn core theory.\n"
                "- Day 3: Solve basic problems.\n"
                "- Day 4: Learn advanced topics.\n"
                "- Day 5: Solve advanced problems.\n"
                "- Day 6: Revise everything.\n"
                "- Day 7: Take a mock test.\n"
            )

    plans = memory.setdefault("plans", [])
    # If plan for this topic exists, overwrite
    existing = None
    for p in plans:
        if _normalize_topic(p.get("topic")) == topic_norm:
            existing = p
            break

    if existing:
        existing["plan"] = plan_text
        existing.setdefault("progress", 0)
        existing["created_at"] = time.time()
    else:
        plans.append(
            {
                "topic": topic_norm,
                "plan": plan_text,
                "progress": 0,
                "created_at": time.time(),
            }
        )

    memory["plans"] = plans
    save_memory(memory)

    # Also set a daily reminder at 8 PM to study this topic
    reminder_cmd = f"remind me every day at 8 pm to study {topic_norm}"
    reminder_reply = _tool_set_reminder(reminder_cmd)  # already inside the authorised action

    return (
        f"I have created a study plan for {topic_norm}, sir. "
        f"{reminder_reply}"
    )


def list_study_plans() -> str:
    plans = memory.get("plans", [])
    if not plans:
        return "You do not have any study plans yet, sir."
    lines = []
    for p in plans:
        topic = p.get("topic", "unknown topic")
        progress = int(p.get("progress", 0))
        lines.append(f"{topic} ({progress} percent complete)")
    return "Here are your study plans: " + " ; ".join(lines)


def show_study_plan_for(topic: str) -> str:
    topic_norm = _normalize_topic(topic)
    if not topic_norm:
        return "Which plan should I show, sir?"

    plans = memory.get("plans", [])
    for p in plans:
        if _normalize_topic(p.get("topic")) == topic_norm:
            progress = int(p.get("progress", 0))
            plan_text = p.get("plan", "No details stored.")
            return f"Here is your plan for {topic_norm}, sir. Progress: {progress} percent.\n\n{plan_text}"

    return f"I did not find any study plan for {topic_norm}, sir."


def update_study_progress(cmd: str, ctx: CommandContext = None) -> str:
    return _act("study.update_progress", {"command": cmd}, ctx)


def _tool_update_study_progress(cmd: str) -> str:
    """
    Example phrases:
    - update my progress for data structures to 40 percent
    - update my progress for dsa to 20
    """
    text = cmd.lower()
    if "update my progress" not in text:
        return "I did not understand which progress to update, sir."

    # Extract percent
    nums = [int(s) for s in text.split() if s.isdigit()]
    if not nums:
        return "Tell me the percentage, sir. For example: update my progress for data structures to 40 percent."
    percent = max(0, min(nums[0], 100))

    # Extract topic between "for" and "to"
    topic = None
    if "for" in text and "to" in text:
        try:
            part = text.split("for", 1)[1]
            topic_part = part.split("to", 1)[0]
            topic = topic_part.strip()
        except Exception:
            topic = None

    if not topic:
        return "Which topic's progress should I update, sir?"

    topic_norm = _normalize_topic(topic)
    plans = memory.get("plans", [])
    for p in plans:
        if _normalize_topic(p.get("topic")) == topic_norm:
            p["progress"] = percent
            save_memory(memory)
            return f"Updated your progress for {topic_norm} to {percent} percent, sir."

    return f"I did not find a study plan for {topic_norm}, sir."


# ================== WATCHERS THREADS START ==================
# Threads are no longer started at import time (importing main from tests, tools or a
# reloader parent process used to start duplicate watchers). Entry points call this once.
_background_lock = threading.Lock()
_background_started = False


def start_background_services() -> bool:
    """Start the reminder and intruder watcher threads exactly once per process."""
    global _background_started
    with _background_lock:
        if _background_started:
            return False
        for name, target in (("reminder-watcher", reminder_watcher), ("intruder-watcher", intruder_watcher)):
            try:
                Thread(target=target, name=name, daemon=True).start()
            except Exception as e:
                print(f"{name} start error:", e)
        _background_started = True
        return True


# ================== SENSITIVE ACTIONS (RISK-CHECKED TOOLS) ==================
def confirmation_gate(pending) -> Tuple[bool, str]:
    """Extra check run right before a confirmed action executes (legacy face-based security mode)."""
    ok, msg = security_check()
    return ok, msg


def set_sleep(asleep: bool, ctx: CommandContext = None) -> str:
    return _act("assistant.set_sleep", {"asleep": bool(asleep)}, ctx)


def _tool_set_sleep(args) -> ToolResult:
    global jarvis_sleep
    jarvis_sleep = bool(args.get("asleep"))
    return ToolResult(True, "Entering sleep mode. Say Jarvis wake up." if jarvis_sleep else "I am awake sir.")


def reload_knowledge(ctx: CommandContext = None) -> str:
    return _act("knowledge.reload", {}, ctx)


def register_face(ctx: CommandContext = None) -> str:
    """Replaces the reference face that security mode compares against: needs confirmation."""
    return _act("vision.register_face", {}, ctx)


def recognize_owner(ctx: CommandContext = None) -> str:
    return _act("vision.recognize_owner", {}, ctx)


def detect_hand(ctx: CommandContext = None) -> str:
    return _act("vision.hand_gesture", {}, ctx)


def detect_person(ctx: CommandContext = None) -> str:
    return _act("vision.detect_person", {}, ctx)


def _tool_register_face(_args) -> ToolResult:
    if not vision.face_available():
        return ToolResult(False, vision.unavailable_message())
    ok = vision.register_face(OWNER_FACE)
    return ToolResult(ok, "I have registered your face, sir." if ok else "I could not capture your face.")


def _tool_recognize_owner(_args) -> ToolResult:
    if not vision.face_available():
        return ToolResult(False, vision.unavailable_message())
    same, score = vision.recognize_face(OWNER_FACE)
    data = {"same": bool(same), "score": score}
    if score is None:
        return ToolResult(True, "I have no stored face. Say register my face first.", data)
    return ToolResult(True, "Yes sir, I see you." if same else "I see someone, but I'm not sure it's you.", data)


def _tool_detect_person(_args) -> ToolResult:
    if not vision.face_available():
        return ToolResult(False, vision.unavailable_message())
    seen = bool(vision.see_any_person())
    message = "I can see at least one person." if seen else "I do not clearly see anyone right now."
    return ToolResult(True, message, {"seen": seen})


def _tool_hand_gesture(_args) -> ToolResult:
    if vision.hands_backend() is None:
        return ToolResult(False, vision.unavailable_message(hands=True))
    gesture = vision.detect_hand_gesture()
    if gesture == "open_palm":
        message = "I see an open palm."
    elif gesture == "no_hand":
        message = "I do not see any hand."
    else:
        message = "I see a hand, but I cannot classify the gesture."
    return ToolResult(True, message, {"gesture": gesture})


# ================== READ-ONLY TOOLS (also offered to the agent loop) ==================
# OBSERVE tier: no side effects. Those marked external_content return text from the internet,
# which the agent treats as untrusted data (later actions in that run need HUD approval).
def _tool_time_now(_args) -> ToolResult:
    now = datetime.datetime.now()
    return ToolResult(True, tell_time(), {"iso": now.isoformat(timespec="seconds"), "weekday": now.strftime("%A")})


def _tool_system_status(_args) -> ToolResult:
    data = {}
    try:
        data["cpu_percent"] = psutil.cpu_percent()
        data["ram_percent"] = psutil.virtual_memory().percent
    except Exception as e:
        print("System status error:", type(e).__name__)
    try:
        bat = psutil.sensors_battery()
        if bat:
            data["battery_percent"] = bat.percent
            data["charging"] = bool(bat.power_plugged)
    except Exception as e:
        print("Battery read error:", type(e).__name__)
    if not data:
        return ToolResult(False, "I could not read the system status.")
    parts = [f"{key.replace('_', ' ')}: {value}" for key, value in data.items()]
    return ToolResult(True, "System status: " + ", ".join(parts) + ".", data)


def _tool_list_reminders(_args) -> ToolResult:
    items = []
    for r in memory.get("reminders", [])[:20]:
        try:
            when = datetime.datetime.fromtimestamp(float(r.get("time", 0))).isoformat(timespec="minutes")
        except Exception:
            when = "unknown"
        items.append({"text": str(r.get("text", "")), "when": when, "repeat": r.get("repeat")})
    return ToolResult(True, list_reminders(), {"count": len(memory.get("reminders", [])), "reminders": items})


def _tool_list_notes(_args) -> ToolResult:
    notes = [str(n) for n in memory.get("notes", [])]
    message = "I remember: " + "; ".join(notes[-20:]) if notes else "I don't have anything stored yet."
    return ToolResult(True, message, {"count": len(notes), "notes": notes[-20:]})


def _tool_web_search(args) -> ToolResult:
    query = str(args.get("query", "")).strip()
    results = _ddg_results(query)[:5] if query else []
    titles = [item["title"] for item in results]
    if not titles:
        return ToolResult(False, f"I found no web results for {query}." if query else "Nothing to search for.")
    formatted = [
        f"{item['title']} ({item['url']})" if item.get("url") else item["title"]
        for item in results
    ]
    return ToolResult(
        True,
        "Top web results: " + " ; ".join(formatted),
        {"query": query, "titles": titles, "results": results, "source": "duckduckgo"},
    )


def _tool_wiki_summary(args) -> ToolResult:
    topic = str(args.get("topic", "")).strip()
    if not topic:
        return ToolResult(False, "Please tell me what to look up.")
    return _wiki_summary_report(topic)


def _tool_weather(args) -> ToolResult:
    return _weather_report(str(args.get("city") or DEFAULT_WEATHER_CITY).strip())


def _tool_news(_args) -> ToolResult:
    if not get_credential("NEWS_API_KEY"):
        return ToolResult(False, "News API key is not configured.")
    heads = news.get_headlines()[:5]
    if not heads:
        return ToolResult(False, "Sorry, I could not fetch the news right now.")
    message = "Here are top headlines. " + " ".join(
        f"Headline {index + 1}: {headline}." for index, headline in enumerate(heads)
    )
    return ToolResult(True, message, {"headlines": heads})


def _tool_add_reminder(args) -> ToolResult:
    """``reminders.add``: success only if a reminder was actually stored (the parser can refuse)."""
    before = len(memory.get("reminders", []))
    reply = _tool_set_reminder(str(args.get("command", "")))
    stored = memory.get("reminders", [])
    if len(stored) <= before:
        return ToolResult(False, reply, {"added": False})
    added = stored[-1]
    when = datetime.datetime.fromtimestamp(float(added.get("time", 0))).isoformat(timespec="minutes")
    return ToolResult(True, reply, {"added": True, "text": added.get("text"), "when": when,
                                    "repeat": added.get("repeat")})


_NO_ARGS = {"type": "object", "additionalProperties": False, "properties": {}}


def _schema(**properties):
    """Object schema helper: every property is required unless its keyword ends with '_' (``city_=``)."""
    props = {name.rstrip("_"): spec for name, spec in properties.items()}
    required = [name.rstrip("_") for name in properties if not name.endswith("_")]
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


def _fn_tool(name, description, risk, run, *, side_effects=(), preview=None, internal_ok=False,
             confirm_hints=(), parameters=None, external_content=False) -> FunctionTool:
    spec = ToolSpec(name=name, description=description, risk=risk, side_effects=frozenset(side_effects),
                    internal_ok=internal_ok, confirm_hints=tuple(confirm_hints), parameters=parameters,
                    external_content=external_content)
    return FunctionTool(spec, run, preview)


def _text_tool(fn):
    """Adapt a legacy ``fn(command_text) -> reply`` implementation to the tool interface."""
    return lambda args: ToolResult(True, fn(str(args.get("command", ""))))


def _reminder_count_preview(_args) -> str:
    count = len(memory.get("reminders", []))
    return f"This will permanently delete all {count} reminder{'s' if count != 1 else ''}."


def build_tool_registry(platform=None, runner=subprocess.run, cache_dir=None, opener=None,
                        screenshot_dir=None, temperature_candidates=None, voice_trust=None,
                        project_root=None) -> ToolRegistry:
    """Every state-changing legacy operation (and every read-only tool the agent loop may use)
    is registered here with its risk tier.

    This is the complete list; docs/M1_1_TOOL_SECURITY_BOUNDARY.md mirrors it as the action/risk
    matrix and tests/test_policy_boundary.py fails if the two drift apart.
    """
    registry = ToolRegistry(platform=platform) if platform else ToolRegistry()
    # How much a spoken request may do (ULTRON_VOICE_TRUST); voice can never approve anything.
    registry.voice_trust = voice_trust if voice_trust is not None else voice_trust_from_env()
    R = RiskTier
    # --- macOS / system (subprocess, browser) --------------------------------------------
    registry.register(SpeakTool(runner=runner, voice=VOICE))
    registry.register(OpenAppTool(runner=runner))
    # Look the opener up at call time so tests (and the stdlib) can swap webbrowser.open.
    registry.register(OpenUrlTool(opener=opener or (lambda url: webbrowser.open(url))))
    registry.register(VolumeTool(runner=runner))
    registry.register(BrightnessTool(runner=runner))
    registry.register(ScreenshotTool(runner=runner, directory=screenshot_dir))
    temp_kwargs = {"candidates": temperature_candidates} if temperature_candidates is not None else {}
    registry.register(CpuTemperatureTool(runner=runner, **temp_kwargs))
    registry.register(PowerTool("restart", runner=runner))
    registry.register(PowerTool("shut down", runner=runner))
    registry.register(ClearUserCachesTool(cache_dir=cache_dir))
    # --- local assistant data (memory.json, knowledge index) -----------------------------
    registry.register(_fn_tool("memory.add_note", "Store a note in memory.", R.ROUTINE, _tool_add_note,
                               side_effects={"local_data"},
                               parameters=_schema(fact={"type": "string", "minLength": 1, "maxLength": 500,
                                                        "description": "The note to remember, in plain words."})))
    registry.register(_fn_tool("profile.update", "Remember your name or a favourite.", R.ROUTINE,
                               _tool_update_profile, side_effects={"local_data"}))
    registry.register(_fn_tool(
        "reminders.add", "Add a reminder.", R.ROUTINE, _tool_add_reminder, side_effects={"local_data"},
        parameters=_schema(command={
            "type": "string", "minLength": 3, "maxLength": 300,
            "description": ("What and when, in plain words, e.g. 'in 10 minutes to drink water', 'tomorrow at "
                            "9 am to call mom', 'at 6 pm to stretch', 'every day at 7 am to walk'.")})))
    registry.register(_fn_tool("reminders.clear_all", "Delete all stored reminders.", R.DESTRUCTIVE,
                               lambda _args: ToolResult(True, _tool_clear_reminders()),
                               side_effects={"deletes_local_data"}, preview=_reminder_count_preview,
                               confirm_hints=("reminder",)))
    registry.register(_fn_tool("study.create_plan", "Create a study plan and a daily reminder.", R.ROUTINE,
                               lambda args: ToolResult(True, _tool_create_study_plan(str(args.get("topic", "")))),
                               side_effects={"local_data", "network"}))
    registry.register(_fn_tool("study.update_progress", "Update study-plan progress.", R.ROUTINE,
                               _text_tool(_tool_update_study_progress), side_effects={"local_data"}))
    registry.register(_fn_tool("smarthome.control", "Change virtual smart-home state.", R.ROUTINE,
                               _text_tool(_tool_control_smart_home), side_effects={"local_data"}))
    registry.register(_fn_tool("assistant.set_sleep", "Put the assistant to sleep or wake it.", R.ROUTINE,
                               _tool_set_sleep, side_effects={"assistant_state"}))
    registry.register(_fn_tool("knowledge.reload", "Rebuild the knowledge index from your documents.", R.ROUTINE,
                               lambda _args: ToolResult(True, knowledge.rebuild_knowledge_base(client)),
                               side_effects={"local_data"}))
    # --- security mode and camera --------------------------------------------------------
    registry.register(_fn_tool("security.enable", "Turn on face-check security mode.", R.ROUTINE,
                               lambda _args: ToolResult(True, _tool_enable_security()),
                               side_effects={"security_setting", "camera"}))
    registry.register(_fn_tool(
        "security.disable", "Turn off face-check security mode.", R.CONSEQUENTIAL,
        lambda _args: ToolResult(True, _tool_disable_security()), side_effects={"security_setting"},
        preview=lambda _args: ("This will turn off security mode, so sensitive actions will no longer need a "
                               "face check, and the intruder watcher will stop."),
        confirm_hints=("security", "disarm")))
    registry.register(_fn_tool("security.verify_identity", "Check your face against the stored one.", R.ROUTINE,
                               lambda _args: _tool_verify_identity(), side_effects={"camera"}))
    registry.register(_fn_tool(
        "vision.register_face", "Capture and store your reference face.", R.CONSEQUENTIAL, _tool_register_face,
        side_effects={"camera", "security_setting", "local_fs_replace"},
        preview=lambda _args: ("This will capture a photo with the camera and replace the stored face that "
                               "security mode uses to recognise you."),
        confirm_hints=("face",)))
    registry.register(_fn_tool("vision.recognize_owner", "Compare the camera image with your stored face.",
                               R.ROUTINE, _tool_recognize_owner, side_effects={"camera"}, internal_ok=True))
    registry.register(_fn_tool("vision.detect_person", "Check whether anyone is in front of the camera.",
                               R.ROUTINE, _tool_detect_person, side_effects={"camera"}, internal_ok=True))
    registry.register(_fn_tool("vision.hand_gesture", "Look for a hand gesture with the camera.", R.ROUTINE,
                               _tool_hand_gesture, side_effects={"camera"}))
    # --- read-only tools (OBSERVE) --------------------------------------------------------
    text = lambda n, d: {"type": "string", "minLength": 1, "maxLength": n, "description": d}  # noqa: E731
    registry.register(_fn_tool("time.now", "Get the current local date and time.", R.OBSERVE, _tool_time_now,
                               parameters=_NO_ARGS))
    registry.register(_fn_tool("system.status", "Read CPU, memory and battery status.", R.OBSERVE,
                               _tool_system_status, parameters=_NO_ARGS))
    registry.register(_fn_tool("reminders.list", "List the stored reminders.", R.OBSERVE, _tool_list_reminders,
                               parameters=_NO_ARGS))
    registry.register(_fn_tool("memory.list_notes", "List the notes stored in memory.", R.OBSERVE,
                               _tool_list_notes, parameters=_NO_ARGS))
    registry.register(_fn_tool("web.search", "Search the web (DuckDuckGo) and return the top result titles.",
                               R.OBSERVE, _tool_web_search, side_effects={"network"}, external_content=True,
                               parameters=_schema(query=text(200, "What to search for."))))
    registry.register(_fn_tool("wiki.summary", "Get a two-sentence Wikipedia summary of a topic.", R.OBSERVE,
                               _tool_wiki_summary, side_effects={"network"}, external_content=True,
                               parameters=_schema(topic=text(120, "The topic, e.g. 'Ada Lovelace'."))))
    registry.register(_fn_tool(
        "weather.current", "Get the current weather for a city.", R.OBSERVE, _tool_weather,
        side_effects={"network"}, external_content=True,
        parameters=_schema(city_={"type": "string", "minLength": 1, "maxLength": 80,
                                  "pattern": r"[A-Za-z][A-Za-z .,'-]{0,79}",
                                  "description": "City name; omit for the default city."})))
    registry.register(_fn_tool("news.headlines", "Get the top news headlines.", R.OBSERVE, _tool_news,
                               side_effects={"network"}, external_content=True, parameters=_NO_ARGS))
    registry.register(ProjectInspectTool(root=project_root, runner=runner))
    registry.register(ProjectExplainTool(root=project_root))
    registry.register(ProjectSearchTool(root=project_root))
    return registry


TOOLS = build_tool_registry()

# ================== AGENT LOOP (M2.0) ==================
# The model proposes; TOOLS decides and executes (same policy, confirmation and audit as every
# other caller). Shared, bounded state: active runs (for "cancel") and tool worker threads.
AGENT_CONFIG = load_agent_config()
AGENT_RUNS = RunRegistry(max_concurrent=AGENT_CONFIG.max_concurrent_runs)
AGENT_EXECUTOR = ToolExecutor(max_inflight=AGENT_CONFIG.max_concurrent_runs)
_AGENT_PREFIX_RE = re.compile(r"^\s*agent\s*[:,]?\s+(?P<objective>\S.*)$", re.IGNORECASE | re.DOTALL)


# ULTRON_PROVIDER=groq: one Groq client for the process (explicit Groq base URL, its own key).
# The single-shot chat helpers (ask_gpt, study planner, knowledge base) keep using `client`.
_GROQ_PROVIDER = (create_provider(AGENT_CONFIG, groq_api_key=GROQ_API_KEY)
                  if AGENT_CONFIG.provider == "groq" and not AGENT_CONFIG.provider_error else None)

# ULTRON_PROVIDER=ollama: one client for the local model server (loopback base URL, no credential).
_OLLAMA_PROVIDER = (create_provider(AGENT_CONFIG)
                    if AGENT_CONFIG.provider == "ollama" and not AGENT_CONFIG.provider_error else None)


def agent_provider():
    """The model behind the agent loop (ULTRON_PROVIDER), or None when it is not configured."""
    if AGENT_CONFIG.provider_error:
        return None
    if AGENT_CONFIG.provider == "groq":
        return _GROQ_PROVIDER
    if AGENT_CONFIG.provider == "ollama":
        return _OLLAMA_PROVIDER
    return create_provider(AGENT_CONFIG, openai_client=client)


def agent_available() -> bool:
    return AGENT_CONFIG.enabled and agent_provider() is not None


# ===== Optional multi-model advisory pass (off unless ULTRON_ENSEMBLE=1) =====
ENSEMBLE_CONFIG = load_ensemble_config()


def _build_ensemble_advisers() -> tuple[Adviser, ...]:
    built: list[Adviser] = []
    for spec in ENSEMBLE_CONFIG.advisers:
        provider_name, _, model = spec.partition(":")
        adviser_config = load_agent_config({
            **os.environ,
            "ULTRON_PROVIDER": provider_name,
            "ULTRON_AGENT_MODEL": model,
            "ULTRON_GROQ_MODEL": model,
            "ULTRON_OLLAMA_MODEL": model,
        })
        provider = create_provider(adviser_config, openai_client=client, groq_api_key=GROQ_API_KEY)
        if provider is None:
            logging.getLogger("ultron.ensemble").warning(
                "ensemble adviser %s is unusable (%s not configured?); dropping it",
                spec, PROVIDER_KEYS.get(provider_name, provider_name))
            continue
        built.append(Adviser(spec, provider))
    return tuple(built)


ENSEMBLE_ADVISERS = _build_ensemble_advisers() if ENSEMBLE_CONFIG.enabled else ()


def ensembled_provider(base=None):
    """Wrap the supplied primary provider with the optional advisory pass."""
    base = base if base is not None else agent_provider()
    if base is None or not ENSEMBLE_CONFIG.enabled or not ENSEMBLE_ADVISERS:
        return base
    return EnsembleProvider(base, ENSEMBLE_ADVISERS, ENSEMBLE_CONFIG)


def build_agent(provider=None) -> AgentRuntime:
    limits = AgentLimits(max_steps=AGENT_CONFIG.max_steps, max_tool_calls=AGENT_CONFIG.max_tool_calls,
                         model_timeout=AGENT_CONFIG.model_timeout, tool_timeout=AGENT_CONFIG.tool_timeout,
                         max_duration=AGENT_CONFIG.max_duration, max_output_tokens=AGENT_CONFIG.max_output_tokens)
    base = provider if provider is not None else agent_provider()
    return AgentRuntime(TOOLS, ensembled_provider(base), limits=limits,
                        runs=AGENT_RUNS, executor=AGENT_EXECUTOR)


def run_agent(objective: str, ctx: CommandContext = None) -> str:
    """Run the agent loop for ``objective`` as ``ctx``; the full report goes to ctx.events.

    With the opt-in Smart Router enabled (``ULTRON_ROUTER=1``; docs/M2_6_SMART_ROUTER.md)
    the provider is chosen per request from validated AutoTune results, and the full
    decision (classification rule, results validation, routing rule rationale, fallback
    reason) is appended to ``ctx.events`` as ``("router_decision", ...)``. Router off -
    the default - or any missing/stale/ineligible/refused case keeps the existing
    ``agent_provider()`` path byte-for-byte. Routing only selects among owner-configured
    providers; the chosen model runs the same runtime, registry, policy, confirmation and
    audit path under the same bounds, and ULTRON_LOCAL_ONLY is enforced in the router and
    in every fallback (load_agent_config gates the default path itself).
    """
    ctx = ctx or CommandContext.untrusted("direct-call")
    if not AGENT_CONFIG.enabled:
        return "The agent loop is turned off (ULTRON_AGENT=0)."
    provider = agent_provider()
    if smart_router.router_enabled(os.environ):
        decision = smart_router.route_request(objective, os.environ)
        ctx.events.append(("router_decision", decision))
        if decision.provider is not None:
            provider = decision.provider
    report = build_agent(provider).run(objective, ctx)
    ctx.events.append(("agent_report", report))
    return report.reply


_CONFIRM_RE = re.compile(r"^(?:yes[, ]+)?(?:i\s+)?confirm\b(?P<rest>.*)$")
_CANCEL_RE = re.compile(r"^(?:cancel|abort|never ?mind|don't do it|do not do it)\b")
_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
                "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _extract_code(text: str):
    """Pull a 4-digit confirmation code from "1234", "12 34" or spoken words ("four eight two one")."""
    digits = "".join(re.findall(r"\d", text))
    if len(digits) >= 4:
        return digits[:4]
    sequence = "".join(tok if tok.isdigit() else _DIGIT_WORDS.get(tok, "") for tok in re.findall(r"\d+|[a-z]+", text))
    return sequence[:4] if len(sequence) >= 4 else None


def handle_confirmation_intents(cmd: str, ctx: CommandContext):
    """'confirm <code>' / 'cancel' for pending actions. Returns a reply, or None if not applicable.

    Only a signed-in HUD session (typed) can approve with a code; the registry refuses spoken
    confirmations and the reply says so. A refusal is final: nothing else is attempted.
    """
    m = _CONFIRM_RE.match(cmd)
    if m:
        rest = m.group("rest")
        expected = next((tool for hint, tool in TOOLS.confirm_hint_map() if hint in rest), None)
        outcome = TOOLS.confirm(ctx, code=_extract_code(rest), expected_tool=expected, gate=confirmation_gate)
        return outcome.message
    if _CANCEL_RE.match(cmd):
        # Stopping is always safe: it ends this caller's running agent task (if any) too.
        stopped = AGENT_RUNS.cancel(ctx.principal) if ctx.can_authorize else 0
        if TOOLS.has_pending(ctx):
            reply = TOOLS.cancel(ctx).message
            return reply + (" I also stopped the current task." if stopped else "")
        if stopped:
            return "Okay, I'm stopping the current task."
    return None


# ================== MAIN COMMAND HANDLER ==================
def handle_command(cmd: str, ctx: CommandContext = None) -> str:
    """
    Main brain used by Flask (/ask) and also CLI mode.

    ``ctx`` says who is asking. Every branch that changes anything (apps, browser, volume,
    camera, memory, reminders, security mode, power ...) goes through the tool registry with
    this context, so the same policy applies to voice, CLI, HUD and API. Without a context
    the request is untrusted: read-only answers still work, every action is refused.
    Only text typed or spoken by a user reaches this function; LLM replies, web pages and
    knowledge-base content are never routed here and can never confirm anything.
    """
    ctx = ctx or CommandContext.untrusted("unspecified")
    raw = (cmd or "").strip()  # original casing, for the agent loop (URLs, names)
    cmd = raw.lower()
    print("CMD:", cmd)

    # Pending-action confirmations ("confirm 1234" / "cancel") are handled first.
    confirmation_reply = handle_confirmation_intents(cmd, ctx)
    if confirmation_reply is not None:
        return confirmation_reply

    # "agent: <objective>" skips the keyword router and goes straight to the agent loop.
    explicit = _AGENT_PREFIX_RE.match(raw)
    if explicit:
        if jarvis_sleep:
            return "I am currently in sleep mode. Say Jarvis wake up."
        return run_agent(explicit.group("objective"), ctx)

    # Personal learning
    learned = update_profile_from_sentence(cmd, ctx)
    if learned:
        return learned

    profile_reply = answer_profile_query(cmd)
    if profile_reply:
        return profile_reply

    # Notes
    if cmd.startswith("remember that"):
        fact = cmd.replace("remember that", "", 1).strip()
        if fact:
            return remember_note(fact, ctx)
        return "What should I remember, sir?"

    if "what do you remember" in cmd:
        notes = memory.get("notes", [])
        return "I remember: " + "; ".join(notes) if notes else "I don't have anything stored yet."

    # ===== KNOWLEDGE BASE COMMANDS (RAG) =====
    if (
        cmd.startswith("reload knowledge")
        or "reload my knowledge" in cmd
        or "reload my notes" in cmd
        or "reload notes" in cmd
        or "reload by notes" in cmd
        or "reload the notes" in cmd
        or ("reload" in cmd and "knowledge" in cmd)
        or ("reload" in cmd and "note" in cmd)
    ):
        return reload_knowledge(ctx)

    if (
        cmd.startswith("search my notes")
        or cmd.startswith("ask my notes")
        or "from my notes" in cmd
        or "from my pdf" in cmd
        or "from my pdfs" in cmd
        or "from my knowledge base" in cmd
    ):
        # Clean the question slightly
        q = cmd
        q = q.replace("search my notes for", "")
        q = q.replace("search my notes", "")
        q = q.replace("ask my notes", "")
        q = q.replace("from my notes", "")
        q = q.replace("from my pdfs", "")
        q = q.replace("from my pdf", "")
        q = q.replace("from my knowledge base", "")
        q = q.strip()
        if not q:
            q = cmd  # fallback to full command
        return knowledge.answer_from_knowledge(q, client, model=CHAT_MODEL)

    # Reminders
    if "remind me" in cmd:
        return set_reminder(cmd, ctx)

    if "what are my reminders" in cmd or "list my reminders" in cmd:
        return list_reminders()

    if "clear reminders" in cmd or "delete all reminders" in cmd:
        return clear_reminders(ctx)

    # ===== SECURITY MODE COMMANDS =====
    if (
        "enable security mode" in cmd
        or "turn on security mode" in cmd
        or "arm security" in cmd
        or "arm the security" in cmd
    ):
        return enable_security_mode(ctx)

    if (
        "disable security mode" in cmd
        or "turn off security mode" in cmd
        or "disarm security" in cmd
        or "disarm the security" in cmd
    ):
        return disable_security_mode(ctx)

    if (
        "security status" in cmd
        or "status of security" in cmd
        or "status of security mode" in cmd
    ):
        return security_status()

    if "verify my identity" in cmd or "verify identity" in cmd:
        if not vision.face_available() and is_security_enabled():
            return vision.unavailable_message()
        return verify_identity(ctx)

    # Sleep
    if "go to sleep" in cmd or "sleep mode" in cmd:
        return set_sleep(True, ctx)

    if "wake up" in cmd:
        return set_sleep(False, ctx)

    if jarvis_sleep:
        return "I am currently in sleep mode. Say Jarvis wake up."

    # ===== STUDY PLANNER COMMANDS =====
    if "help me learn" in cmd:
        topic = cmd.split("help me learn", 1)[1].strip()
        return create_study_plan(topic, ctx)

    if "create a study plan for" in cmd:
        topic = cmd.split("create a study plan for", 1)[1].strip()
        return create_study_plan(topic, ctx)

    if "make a study plan for" in cmd:
        topic = cmd.split("make a study plan for", 1)[1].strip()
        return create_study_plan(topic, ctx)

    if "make a learning plan for" in cmd:
        topic = cmd.split("make a learning plan for", 1)[1].strip()
        return create_study_plan(topic, ctx)

    if "show my study plans" in cmd or "what are my study plans" in cmd:
        return list_study_plans()

    if cmd.startswith("show my plan for"):
        topic = cmd.split("show my plan for", 1)[1].strip()
        return show_study_plan_for(topic)

    if "update my progress" in cmd and "for" in cmd:
        return update_study_progress(cmd, ctx)

    # Time / battery
    if "time" in cmd:
        return tell_time()

    if "battery" in cmd:
        try:
            bat = psutil.sensors_battery()
            if bat:
                return f"Battery is {bat.percent}%."
            return "I cannot read the battery status."
        except Exception:
            return "I could not read the battery status."

    # ===== LIVE CRICKET COMMANDS =====
    if (
        "cricket" in cmd
        or "live score" in cmd
        or "match score" in cmd
        or "today's match" in cmd
        or "todays match" in cmd
    ):
        return get_live_cricket_score(cmd)

    # ===== WEB SEARCH COMMANDS =====
    if cmd.startswith("search "):
        q = cmd.replace("search", "", 1).strip()
        if not q:
            return "What should I search for, sir?"
        return web_search_ddg(q, ctx)

    if "search" in cmd and not cmd.startswith("search "):
        # e.g. "jarvis can you search tesla model 3 review"
        parts = cmd.split("search", 1)
        q = parts[1].strip()
        if q:
            return web_search_ddg(q, ctx)

    # ===== SYSTEM INTELLIGENCE COMMANDS =====
    if (
        "system diagnostic" in cmd
        or "system diagnostics" in cmd
        or "status report" in cmd
        or "system status" in cmd
    ):
        return system_report()

    if "cpu usage" in cmd or "cpu status" in cmd:
        try:
            cpu = psutil.cpu_percent()
            return f"Current CPU usage is {cpu} percent, sir."
        except Exception as e:
            print("CPU usage error:", e)
            return "I could not read the CPU usage, sir."

    if "temperature" in cmd or "overheating" in cmd or "too hot" in cmd:
        temp = get_cpu_temperature(ctx)
        if temp:
            return f"Current CPU temperature is {temp}. Everything looks under control, sir."
        return "I could not read the temperature sensors, but performance seems normal, sir."

    # Destructive actions: the registry checks who is asking and creates a pending action
    # bound to these exact arguments. Nothing runs until the signed-in HUD approves it (typed
    # "confirm <code>" or the CONFIRM button) before it expires; voice can only ask or cancel.
    # Face-based security mode, when enabled, is checked again right before execution.
    if (
        "clean my system" in cmd
        or "clean system" in cmd
        or "clear junk" in cmd
        or "clear cache" in cmd
    ):
        return TOOLS.request("system.clear_user_caches", {}, ctx).message

    if "restart system" in cmd or "system restart" in cmd or "restart the system" in cmd:
        return TOOLS.request("system.restart", {}, ctx).message

    if "shutdown system" in cmd or "shut down system" in cmd or "shut down the system" in cmd:
        return TOOLS.request("system.shutdown", {}, ctx).message

    # ===== SMART HOME (VIRTUAL) =====
    if (
        ("turn on" in cmd or "turn off" in cmd or "switch on" in cmd or "switch off" in cmd)
        and any(word in cmd for word in ["light", "lights", "fan", "ac", "air conditioner", "plug", "socket", "lamp"])
    ):
        return control_smart_home(cmd, ctx)

    if "set temperature" in cmd or "set ac" in cmd:
        return control_smart_home(cmd, ctx)

    # Weather / news
    if "weather" in cmd:
        return get_weather(cmd)

    if "news" in cmd:
        return get_news()

    # YouTube
    if cmd.startswith("play "):
        return play_youtube(cmd.replace("play", "", 1).strip(), ctx)

    # Open apps / websites
    if cmd.startswith("open "):
        app = cmd.replace("open", "", 1).strip()
        return launch_any_app(app, ctx)

    # Brightness / volume / screenshot
    if "increase brightness" in cmd or "brightness up" in cmd:
        return change_brightness_relative("up", ctx=ctx)

    if "decrease brightness" in cmd or "brightness down" in cmd:
        return change_brightness_relative("down", ctx=ctx)

    if "increase volume" in cmd:
        return set_volume(100, ctx)

    if "decrease volume" in cmd:
        return set_volume(30, ctx)

    if "volume" in cmd:
        nums = [int(s) for s in cmd.split() if s.isdigit()]
        if nums:
            return set_volume(nums[0], ctx)
        return "Tell me the volume level, like volume 50."

    if "screenshot" in cmd or "screen shot" in cmd:
        return take_screenshot(ctx)

    # Vision (optional extra)
    vision_phrases = ("register my face", "remember my face", "do you see me", "who is in front of you",
                      "check my hand", "see my hand", "do you see anyone", "what do you see")
    if any(p in cmd for p in vision_phrases) and not vision.face_available():
        return vision.unavailable_message()

    if ("check my hand" in cmd or "see my hand" in cmd) and vision.hands_backend() is None:
        return vision.unavailable_message(hands=True)

    if "register my face" in cmd or "remember my face" in cmd:
        return register_face(ctx)

    if "do you see me" in cmd or "who is in front of you" in cmd:
        return recognize_owner(ctx)

    if "check my hand" in cmd or "see my hand" in cmd:
        return detect_hand(ctx)

    if "do you see anyone" in cmd or "what do you see" in cmd:
        return detect_person(ctx)

    # Small fun personality
    if "roast me" in cmd:
        return "I would roast you, sir, but I am afraid the fire department would complain."

    if "motivate me" in cmd or "motivation" in cmd:
        return "You are literally building your own JARVIS. Most people only dream about it, sir."

    # Wikipedia
    if cmd.startswith("who is") or cmd.startswith("what is") or cmd.startswith("tell me about"):
        return wiki(cmd)

    # Anything the keyword router does not know goes to the agent loop (which also answers
    # plain questions); without a configured model, or with ULTRON_AGENT=0, to the chat reply.
    if agent_available():
        return run_agent(raw, ctx)
    # Bounded, redacted local transcript (docs/CONVERSATION_HISTORY.md): the single-shot chat
    # sees the last few turns instead of starting cold. The agent loop keeps its own audit
    # ledger and is deliberately not fed from here. ULTRON_HISTORY=0 disables both sides.
    reply = ask_gpt(cmd, chat_history_recent())
    chat_history_record(cmd, reply)
    return reply


# ================== OPTIONAL CLI LOOP ==================
_EXIT_PHRASES = frozenset({
    "exit", "quit", "stop", "stop listening", "goodbye", "good bye", "bye",
    "exit jarvis", "quit jarvis", "goodbye jarvis", "jarvis exit", "jarvis stop",
})


_VOICE_GOODBYE = "Goodbye, sir."  # sentinel reply: the voice loop ends after speaking it


def is_exit_command(text: str) -> bool:
    """Only whole exit phrases end the CLI loop ("stop the music" or "shutdown system" do not)."""
    return text.strip().strip(".!?").lower() in _EXIT_PHRASES


def _voice_responder(text: str, ctx) -> str:
    """Command routing for a spoken turn: strip the wake word, then route or say goodbye."""
    if WAKE_WORD in text:
        text = text.replace(WAKE_WORD, "").strip()
    if is_exit_command(text):
        return _VOICE_GOODBYE
    return handle_command(text, ctx)


def main():
    load_dotenv_if_enabled()
    start_background_services()
    ctx = CommandContext.local_voice()  # the person at this machine's microphone
    # Boot voice: Tony-ish
    speak("JARVIS system initializing. Welcome back, sir. You can ask me anything.", ctx)
    # Voice loop: recognition, language handling, routing and speech output are separate
    # stages (ultron.voice). ULTRON_VOICE_LANGUAGE selects en|hi|hinglish|auto; a spoken
    # "switch to hindi" changes it for the rest of the session.
    pipeline = VoicePipeline(
        transcriber=GoogleTranscriber(),
        responder=lambda text: _voice_responder(text, ctx),
        speaker=lambda text: speak(text, ctx),
        language=normalise_mode(None),
    )
    while True:
        turn = pipeline.run()
        spoken = turn.transcript.replace(WAKE_WORD, "").strip()
        if turn.reply == _VOICE_GOODBYE and is_exit_command(spoken):
            break


if __name__ == "__main__":
    main()
