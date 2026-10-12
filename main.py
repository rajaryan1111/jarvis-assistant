import os
import json
import datetime
import subprocess
import urllib.parse
import re
import webbrowser
import time
import threading
from threading import Thread
from typing import Tuple

import requests
import wikipedia
import psutil
from openai import OpenAI

import vision  # optional vision utilities; degrade gracefully when OpenCV/MediaPipe are missing
import knowledge  # Personal Knowledge Base (RAG over notes/PDFs); optional heavy deps load lazily
import news
from ultron.config import get_credential, load_dotenv_if_enabled
from ultron.security.policy import CommandContext, RiskTier
from ultron.security.redact import redact
from ultron.tools.base import FunctionTool, ToolResult, ToolSpec
from ultron.tools.registry import ToolRegistry
from ultron.tools.system import ClearUserCachesTool, PowerTool

# ================== SETUP ==================
load_dotenv_if_enabled()

# Credentials are read from the environment / local .env only (never hardcoded).
OPENAI_API_KEY = get_credential("OPENAI_API_KEY")
WEATHER_API_KEY = get_credential("WEATHER_API_KEY")
NEWS_COUNTRY = os.getenv("NEWS_COUNTRY", "in")
DEFAULT_WEATHER_CITY = os.getenv("WEATHER_CITY", "Delhi")
CRICKET_API_KEY = get_credential("CRICKET_API_KEY")  # live cricket API key

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
_recognizer = None  # created lazily; SpeechRecognition is an optional "voice" extra

MEMORY_FILE = os.getenv("ULTRON_MEMORY_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
WAKE_WORD = "jarvis"
jarvis_sleep = False

VOICE = "Daniel"  # macOS voice name


# ================== BASIC UTILITIES ==================
def speak(text: str):
    """
    Mac voice mode:
    - All Jarvis speech comes from your Mac using macOS 'say'
    - Called from CLI, Flask /ask, Flask /speak
    """
    if not text:
        return

    print(f"[JARVIS SPEAK] {text}")  # debug

    # A message starting with "-" would be parsed by `say` as an option (e.g. "-o file").
    if text.startswith("-"):
        text = " " + text

    try:
        # Use absolute path to 'say' to avoid PATH issues
        subprocess.run(
            ["/usr/bin/say", "-v", VOICE, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print("[JARVIS SPEAK ERROR]", e)


def listen(timeout=6, phrase_time_limit=8) -> str:
    """Microphone listening (only for CLI main loop). Requires the optional 'voice' extra."""
    global _recognizer
    try:
        import speech_recognition as sr
    except ImportError:
        print("Voice input needs the optional 'voice' extra: uv sync --extra voice  (or pip install -e '.[voice]')")
        time.sleep(2)
        return ""
    if _recognizer is None:
        _recognizer = sr.Recognizer()
    recognizer = _recognizer
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.4)
        print("Listening...")
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            query = recognizer.recognize_google(audio, language="en-IN")
            print(f"You: {query}")
            return query.lower()
        except Exception as e:
            print("Listen error:", e)
            return ""


# ================== MEMORY ==================
def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {
            "notes": [],
            "profile": {},
            "favorites": {},
            "smarthome": {},
            "reminders": [],
            "security": {"enabled": False, "last_auth_time": 0, "auth_timeout_sec": 60},
            "plans": [],
        }
    try:
        with open(MEMORY_FILE, "r") as f:
            data = json.load(f)
            # Make sure all keys exist even for old files
            data.setdefault("notes", [])
            data.setdefault("profile", {})
            data.setdefault("favorites", {})
            data.setdefault("smarthome", {})
            data.setdefault("reminders", [])
            data.setdefault("security", {"enabled": False, "last_auth_time": 0, "auth_timeout_sec": 60})
            data.setdefault("plans", [])
            return data
    except Exception:
        return {
            "notes": [],
            "profile": {},
            "favorites": {},
            "smarthome": {},
            "reminders": [],
            "security": {"enabled": False, "last_auth_time": 0, "auth_timeout_sec": 60},
            "plans": [],
        }


_memory_lock = threading.RLock()


def save_memory(mem):
    """Atomic write (temp file + rename) so a crash or concurrent save cannot corrupt the file."""
    tmp_path = f"{MEMORY_FILE}.tmp"
    try:
        with _memory_lock:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(mem, f, indent=2)
            os.replace(tmp_path, MEMORY_FILE)
    except Exception as e:
        print("Memory save error:", e)


memory = load_memory()


def update_profile_from_sentence(cmd: str):
    """
    Learn from sentences like:
    - my name is raj
    - my favourite game is gta 5
    """
    text = cmd.lower()

    # Name
    if "my name is" in text:
        name = text.split("my name is", 1)[1].strip()
        if name:
            memory["profile"]["name"] = name
            save_memory(memory)
            return f"Nice to meet you, {name}."

    # Favourites
    if "my favourite" in text or "my favorite" in text:
        key = "favourite" if "favourite" in text else "favorite"
        rest = text.split(key, 1)[1].strip()
        if " is " in rest:
            attr, value = rest.split(" is ", 1)
            attr = attr.strip()
            value = value.strip()
            if attr and value:
                memory["favorites"][attr] = value
                save_memory(memory)
                return f"I will remember your favourite {attr} is {value}."

    return None


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
            model="gpt-4.1-mini",
            messages=history + [{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("GPT error:", e)
        return "Sorry, I am having trouble thinking right now."


# ================== INTERNET SKILLS ==================
def play_youtube(query: str) -> str:
    search_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        res = requests.get(search_url, timeout=5)
        video_ids = re.findall(r"watch\?v=([\w-]{11})", res.text)
        if video_ids:
            url = "https://www.youtube.com/watch?v=" + video_ids[0]
        else:
            url = search_url
        webbrowser.open(url)
        return f"Playing {query} on YouTube."
    except Exception as e:
        print("YouTube error:", e)
        webbrowser.open(search_url)
        return f"There was an issue, but I opened YouTube search for {query}."


def get_weather(cmd: str) -> str:
    if not WEATHER_API_KEY:
        return "Weather API key is not configured."
    city = DEFAULT_WEATHER_CITY
    if " in " in cmd:
        city = cmd.split(" in ", 1)[1].strip()
    try:
        data = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": WEATHER_API_KEY, "units": "metric"},
            timeout=5,
        ).json()
        if data.get("cod") != 200:
            return f"Weather error for {city}."
        temp = int(data["main"]["temp"])
        desc = data["weather"][0]["description"]
        return f"The weather in {city} is {desc}, {temp}°C."
    except Exception as e:
        print("Weather error:", redact(e))  # request errors embed the URL, including the key
        return "Sorry, I could not fetch the weather."


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


def wiki(cmd: str) -> str:
    topic = (
        cmd.replace("who is", "")
        .replace("what is", "")
        .replace("tell me about", "")
        .strip()
    )
    if not topic:
        return "Please tell me what to search for."
    try:
        return wikipedia.summary(topic, sentences=2)
    except Exception as e:
        print("Wiki error:", e)
        return "I could not find information on that."


# ================== WEB SEARCH (DUCKDUCKGO) ==================
def web_search_ddg(query: str) -> str:
    """
    Simple real-time web search using DuckDuckGo.
    - Opens full results in browser
    - Tries to read top 2–3 result titles and speak them
    """
    url_html = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    url_main = "https://duckduckgo.com/?q=" + urllib.parse.quote(query)
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url_html, timeout=5, headers=headers)
        html = res.text

        # Grab top result titles from result__a anchors
        matches = re.findall(r'class="result__a".*?>(.*?)</a>', html)
        if not matches:
            webbrowser.open(url_main)
            return f"I opened web results for {query} in your browser, sir."

        # Strip HTML tags
        def strip_tags(t):
            return re.sub("<.*?>", "", t)

        titles = [strip_tags(m) for m in matches[:3]]
        summary = " ; ".join(titles)
        webbrowser.open(url_main)
        return f"Here is what I found about {query}: {summary}."
    except Exception as e:
        print("Web search error:", e)
        webbrowser.open(url_main)
        return f"I opened web results for {query} in your browser, sir."


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
        data = resp.json()

        # Different APIs might use different keys; try to be defensive.
        matches = data.get("data") or data.get("matches") or []
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
def get_cpu_temperature():
    """
    Uses 'osx-cpu-temp' (installed via Homebrew) to read CPU temperature.
    Returns a string like '46.5°C' or None if it fails.
    """
    try:
        temp = subprocess.check_output(["osx-cpu-temp"]).decode().strip()
        return temp
    except Exception as e:
        print("CPU temp error:", e)
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
def launch_any_app(app_name: str) -> str:
    """
    Launch macOS apps by voice.
    - First tries to open a real .app
    - If that fails, falls back to opening a website in the browser
      (facebook, linkedin, youtube, etc.).
    Special hard-coded case for Canva.
    """
    raw = app_name.strip()
    if not raw:
        return "Which app should I open, sir?"

    lower = raw.lower()

    # ===== Special case: Canva desktop app =====
    if "canva" in lower:
        try:
            subprocess.run(["open", "/Applications/Canva.app"])
            return "Launching Canva."
        except Exception as e:
            print("Canva open error:", e)
            return "I tried, but could not open Canva."

    # ===== Spoken-name → real app mapping =====
    mapping = {
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
    app_to_open = mapping.get(lower, raw)

    # ===== 1) Try to open as a native macOS app =====
    try:
        result = subprocess.run(
            ["open", "-a", app_to_open],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return f"Launching {app_to_open}."
    except Exception as e:
        print("open -a error:", e)

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

    url = "https://" + site_name

    try:
        webbrowser.open(url)
        return f"Opening {site_name} in your browser, sir."
    except Exception as e:
        print("webbrowser open error:", e)
        return f"I could not open {app_name}, sir."


def change_brightness_relative(direction: str, steps: int = 4) -> str:
    try:
        key_code = 107 if direction == "up" else 113
        script = f'''
        tell application "System Events"
            repeat {steps} times
                key code {key_code}
            end repeat
        end tell
        '''
        subprocess.run(["osascript", "-e", script])
        return "Brightness adjusted."
    except Exception as e:
        print("Brightness error:", e)
        return "I tried, but could not change the brightness."


def set_volume(level: int) -> str:
    try:
        level = max(0, min(int(level), 100))
        subprocess.run(["osascript", "-e", f"set volume output volume {level}"])
        return f"Volume set to {level} percent."
    except Exception as e:
        print("Volume error:", e)
        return "I could not change the volume."


def take_screenshot() -> str:
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"jarvis_screenshot_{ts}.png"
        path = os.path.expanduser(f"~/Desktop/{filename}")
        subprocess.run(["screencapture", "-x", path])
        return f"Screenshot saved to Desktop as {filename}."
    except Exception as e:
        print("Screenshot error:", e)
        return "I tried to take a screenshot but something went wrong."


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
def control_smart_home(cmd: str) -> str:
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
def set_reminder(cmd: str) -> str:
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


def clear_reminders() -> str:
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
                        speak(f"Reminder, sir: {r.get('text', 'something you asked me to remember.')}")
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


def enable_security_mode() -> str:
    sec = _get_security_state()
    sec["enabled"] = True
    sec.setdefault("last_auth_time", 0)
    sec.setdefault("auth_timeout_sec", 60)
    memory["security"] = sec
    save_memory(memory)
    return "Security mode enabled, sir. Sensitive commands will now require your face."


def disable_security_mode() -> str:
    sec = _get_security_state()
    sec["enabled"] = False
    memory["security"] = sec
    save_memory(memory)
    return "Security mode disabled, sir. I will execute commands normally."


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
                       "Install the 'vision' extra or say 'disable security mode'.")

    # Need fresh face verification (prototype only; not real authentication)
    same, score = vision.recognize_face("raj")
    if score is None:
        return False, "Security check failed, sir. I do not have your face stored. Say 'register my face' first."

    if same:
        _mark_authenticated()
        return True, "Identity verified, sir."
    else:
        return False, "Security check failed, sir. The person in front of the camera does not match your stored face."


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

            # Quick cheap check first
            if not vision.see_any_person():
                continue

            same, score = vision.recognize_face("raj")
            if score is None:
                # No reference face, skip
                continue

            if not same:
                speak("Intruder detected, sir. Triggering red alert protocol.")
                # Optional: could log timestamp here
        except Exception as e:
            print("Intruder watcher error:", e)


# ================== STUDY PLANNER (AUTONOMOUS TASK PLANNER) ==================
def _normalize_topic(text: str) -> str:
    return (text or "").strip().lower()


def create_study_plan(topic: str) -> str:
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
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful study planner."},
                    {"role": "user", "content": base_prompt},
                ],
                max_tokens=400,
            )
            plan_text = resp.choices[0].message.content.strip()
        except Exception as e:
            print("Study planner GPT error:", e)
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
    reminder_reply = set_reminder(reminder_cmd)

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


def update_study_progress(cmd: str) -> str:
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


def _clear_reminders_tool() -> FunctionTool:
    def preview(_args) -> str:
        count = len(memory.get("reminders", []))
        return f"This will permanently delete all {count} reminder{'s' if count != 1 else ''}."

    def run(_args) -> ToolResult:
        return ToolResult(True, clear_reminders())

    spec = ToolSpec(
        name="reminders.clear_all",
        description="Delete all stored reminders.",
        risk=RiskTier.DESTRUCTIVE,
        side_effects=frozenset({"deletes_local_data"}),
    )
    return FunctionTool(spec, run, preview)


def build_tool_registry(platform=None, runner=subprocess.run, cache_dir=None) -> ToolRegistry:
    """Every destructive or high-impact legacy operation is registered here with its risk tier."""
    registry = ToolRegistry(platform=platform) if platform else ToolRegistry()
    registry.register(PowerTool("restart", runner=runner))
    registry.register(PowerTool("shut down", runner=runner))
    registry.register(ClearUserCachesTool(cache_dir=cache_dir))
    registry.register(_clear_reminders_tool())
    return registry


TOOLS = build_tool_registry()

_CONFIRM_RE = re.compile(r"^(?:yes[, ]+)?(?:i\s+)?confirm\b(?P<rest>.*)$")
_CANCEL_RE = re.compile(r"^(?:cancel|abort|never ?mind|don't do it|do not do it)\b")
_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
                "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_CONFIRM_TOOL_HINTS = (
    ("restart", "system.restart"),
    ("shut down", "system.shutdown"),
    ("shutdown", "system.shutdown"),
    ("cache", "system.clear_user_caches"),
    ("clean", "system.clear_user_caches"),
    ("reminder", "reminders.clear_all"),
)


def _extract_code(text: str):
    """Pull a 4-digit confirmation code from "1234", "12 34" or spoken words ("four eight two one")."""
    digits = "".join(re.findall(r"\d", text))
    if len(digits) >= 4:
        return digits[:4]
    sequence = "".join(tok if tok.isdigit() else _DIGIT_WORDS.get(tok, "") for tok in re.findall(r"\d+|[a-z]+", text))
    return sequence[:4] if len(sequence) >= 4 else None


def handle_confirmation_intents(cmd: str, ctx: CommandContext):
    """'confirm <code>' / 'cancel' for pending actions. Returns a reply, or None if not applicable."""
    m = _CONFIRM_RE.match(cmd)
    if m:
        rest = m.group("rest")
        expected = next((tool for hint, tool in _CONFIRM_TOOL_HINTS if hint in rest), None)
        outcome = TOOLS.confirm(ctx, code=_extract_code(rest), expected_tool=expected, gate=confirmation_gate)
        return outcome.message
    if _CANCEL_RE.match(cmd) and TOOLS.has_pending(ctx):
        return TOOLS.cancel(ctx).message
    return None


# ================== MAIN COMMAND HANDLER ==================
def handle_command(cmd: str, ctx: CommandContext = None) -> str:
    """
    Main brain used by Flask (/ask) and also CLI mode.

    ``ctx`` says who is asking. Without one, the request is treated as untrusted: routine
    legacy commands still work, but risk-checked actions (shutdown, restart, cache cleanup,
    clearing reminders) are refused. Only text typed or spoken by an authenticated user
    reaches this function; LLM replies and knowledge-base content are never routed here.
    """
    global jarvis_sleep, memory
    ctx = ctx or CommandContext.untrusted("unspecified")
    cmd = (cmd or "").lower().strip()
    print("CMD:", cmd)

    # Pending-action confirmations ("confirm 1234" / "cancel") are handled first.
    confirmation_reply = handle_confirmation_intents(cmd, ctx)
    if confirmation_reply is not None:
        return confirmation_reply

    # Personal learning
    learned = update_profile_from_sentence(cmd)
    if learned:
        return learned

    profile_reply = answer_profile_query(cmd)
    if profile_reply:
        return profile_reply

    # Notes
    if cmd.startswith("remember that"):
        fact = cmd.replace("remember that", "", 1).strip()
        if fact:
            memory["notes"].append(fact)
            save_memory(memory)
            return "Stored in my memory."
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
        return knowledge.rebuild_knowledge_base(client)

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
        return knowledge.answer_from_knowledge(q, client)

    # Reminders
    if "remind me" in cmd:
        return set_reminder(cmd)

    if "what are my reminders" in cmd or "list my reminders" in cmd:
        return list_reminders()

    if "clear reminders" in cmd or "delete all reminders" in cmd:
        return TOOLS.request("reminders.clear_all", {}, ctx).message

    # ===== SECURITY MODE COMMANDS =====
    if (
        "enable security mode" in cmd
        or "turn on security mode" in cmd
        or "arm security" in cmd
        or "arm the security" in cmd
    ):
        return enable_security_mode()

    if (
        "disable security mode" in cmd
        or "turn off security mode" in cmd
        or "disarm security" in cmd
        or "disarm the security" in cmd
    ):
        return disable_security_mode()

    if (
        "security status" in cmd
        or "status of security" in cmd
        or "status of security mode" in cmd
    ):
        return security_status()

    if "verify my identity" in cmd or "verify identity" in cmd:
        ok, msg = security_check()
        if ok and not msg:
            return "Identity already verified recently, sir."
        return msg

    # Sleep
    if "go to sleep" in cmd or "sleep mode" in cmd:
        jarvis_sleep = True
        return "Entering sleep mode. Say Jarvis wake up."

    if "wake up" in cmd:
        jarvis_sleep = False
        return "I am awake sir."

    if jarvis_sleep:
        return "I am currently in sleep mode. Say Jarvis wake up."

    # ===== STUDY PLANNER COMMANDS =====
    if "help me learn" in cmd:
        topic = cmd.split("help me learn", 1)[1].strip()
        return create_study_plan(topic)

    if "create a study plan for" in cmd:
        topic = cmd.split("create a study plan for", 1)[1].strip()
        return create_study_plan(topic)

    if "make a study plan for" in cmd:
        topic = cmd.split("make a study plan for", 1)[1].strip()
        return create_study_plan(topic)

    if "make a learning plan for" in cmd:
        topic = cmd.split("make a learning plan for", 1)[1].strip()
        return create_study_plan(topic)

    if "show my study plans" in cmd or "what are my study plans" in cmd:
        return list_study_plans()

    if cmd.startswith("show my plan for"):
        topic = cmd.split("show my plan for", 1)[1].strip()
        return show_study_plan_for(topic)

    if "update my progress" in cmd and "for" in cmd:
        return update_study_progress(cmd)

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
        return web_search_ddg(q)

    if "search" in cmd and not cmd.startswith("search "):
        # e.g. "jarvis can you search tesla model 3 review"
        parts = cmd.split("search", 1)
        q = parts[1].strip()
        if q:
            return web_search_ddg(q)

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
        temp = get_cpu_temperature()
        if temp:
            return f"Current CPU temperature is {temp}. Everything looks under control, sir."
        return "I could not read the temperature sensors, but performance seems normal, sir."

    # Destructive actions: the registry checks who is asking and creates a pending action
    # with a one-time code. Nothing runs until "confirm <code>" (or the HUD button) arrives
    # from the same authenticated user before it expires. Face-based security mode, when
    # enabled, is checked again right before execution (confirmation_gate).
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
        return control_smart_home(cmd)

    if "set temperature" in cmd or "set ac" in cmd:
        return control_smart_home(cmd)

    # Weather / news
    if "weather" in cmd:
        return get_weather(cmd)

    if "news" in cmd:
        return get_news()

    # YouTube
    if cmd.startswith("play "):
        return play_youtube(cmd.replace("play", "", 1).strip())

    # Open apps / websites
    if cmd.startswith("open "):
        app = cmd.replace("open", "", 1).strip()
        return launch_any_app(app)

    # Brightness / volume / screenshot
    if "increase brightness" in cmd or "brightness up" in cmd:
        return change_brightness_relative("up")

    if "decrease brightness" in cmd or "brightness down" in cmd:
        return change_brightness_relative("down")

    if "increase volume" in cmd:
        return set_volume(100)

    if "decrease volume" in cmd:
        return set_volume(30)

    if "volume" in cmd:
        nums = [int(s) for s in cmd.split() if s.isdigit()]
        if nums:
            return set_volume(nums[0])
        return "Tell me the volume level, like volume 50."

    if "screenshot" in cmd or "screen shot" in cmd:
        return take_screenshot()

    # Vision (optional extra)
    vision_phrases = ("register my face", "remember my face", "do you see me", "who is in front of you",
                      "check my hand", "see my hand", "do you see anyone", "what do you see")
    if any(p in cmd for p in vision_phrases) and not vision.face_available():
        return vision.unavailable_message()

    if ("check my hand" in cmd or "see my hand" in cmd) and vision.hands_backend() is None:
        return vision.unavailable_message(hands=True)

    if "register my face" in cmd or "remember my face" in cmd:
        ok = vision.register_face("raj")
        return "I have registered your face, sir." if ok else "I could not capture your face."

    if "do you see me" in cmd or "who is in front of you" in cmd:
        same, score = vision.recognize_face("raj")
        if score is None:
            return "I have no stored face. Say register my face first."
        return "Yes sir, I see you." if same else "I see someone, but I'm not sure it's you."

    if "check my hand" in cmd or "see my hand" in cmd:
        gesture = vision.detect_hand_gesture()
        if gesture == "open_palm":
            return "I see an open palm."
        elif gesture == "no_hand":
            return "I do not see any hand."
        else:
            return "I see a hand, but I cannot classify the gesture."

    if "do you see anyone" in cmd or "what do you see" in cmd:
        return "I can see at least one person." if vision.see_any_person() else "I do not clearly see anyone right now."

    # Small fun personality
    if "roast me" in cmd:
        return "I would roast you, sir, but I am afraid the fire department would complain."

    if "motivate me" in cmd or "motivation" in cmd:
        return "You are literally building your own JARVIS. Most people only dream about it, sir."

    # Wikipedia
    if cmd.startswith("who is") or cmd.startswith("what is") or cmd.startswith("tell me about"):
        return wiki(cmd)

    # GPT fallback
    history = []  # simple history for now
    return ask_gpt(cmd, history)


# ================== OPTIONAL CLI LOOP ==================
_EXIT_PHRASES = frozenset({
    "exit", "quit", "stop", "stop listening", "goodbye", "good bye", "bye",
    "exit jarvis", "quit jarvis", "goodbye jarvis", "jarvis exit", "jarvis stop",
})


def is_exit_command(text: str) -> bool:
    """Only whole exit phrases end the CLI loop ("stop the music" or "shutdown system" do not)."""
    return text.strip().strip(".!?").lower() in _EXIT_PHRASES


def main():
    load_dotenv_if_enabled()
    start_background_services()
    ctx = CommandContext.local_voice()  # the person at this machine's microphone
    # Boot voice: Tony-ish
    speak("JARVIS system initializing. Welcome back, sir. You can ask me anything.")
    while True:
        text = listen()
        if not text:
            continue
        if WAKE_WORD in text:
            text = text.replace(WAKE_WORD, "").strip()
        if is_exit_command(text):
            speak("Goodbye, sir.")
            break
        reply = handle_command(text, ctx)
        speak(reply)


if __name__ == "__main__":
    main()
