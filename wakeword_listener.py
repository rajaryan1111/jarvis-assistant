"""Porcupine wake-word listener: say "Jarvis", then a command, which is sent to the HUD server.

Requirements (optional "wakeword" extra): ``uv sync --extra wakeword``.
Configuration (in ``.env`` or your shell, never in this file):
  PICOVOICE_ACCESS_KEY  your Picovoice AccessKey (https://console.picovoice.ai/)
  client token          created by ``python -m ultron token init --client`` (recommended: it can
                        send commands but can never log in to the HUD or approve anything);
                        falls back to the owner API token (``python -m ultron token init``)
The HUD server (``python server.py``) must be running.

Voice is not proof of identity: anything the microphone hears is treated as a spoken request.
Read-only and routine commands run directly; high-impact ones (shutdown, restart, deleting
data, security changes) are only *requested* here and must be approved in the signed-in HUD.
"""

import os
import struct
import sys

import requests

from ultron.config import MissingCredentialError, load_dotenv_if_enabled, load_settings, require_credential
from ultron.security.redact import redact
from ultron.security.tokens import load_api_token, load_client_token

# Keyword file path (jarvis.ppn in the project folder)
KEYWORD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.ppn")
REQUEST_TIMEOUT = (3, 60)  # (connect, read) seconds


# ================== HELPERS ==================

class ServerClient:
    """Talks to the local HUD server with the bearer API token (never printed)."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "X-Ultron-Client": "wakeword"})

    def _post(self, path: str, payload: dict) -> dict:
        res = self.session.post(f"{self.base_url}{path}", json=payload, timeout=REQUEST_TIMEOUT)
        if res.status_code == 401:
            raise RuntimeError("the server rejected the token (check `python -m ultron doctor`)")
        res.raise_for_status()
        return res.json()

    def send_command(self, cmd: str) -> None:
        """Send recognized command text to /ask and have the server speak the reply."""
        try:
            reply = self._post("/ask", {"message": cmd}).get("reply", "")
            print(f"Jarvis reply: {reply}")
            if reply:
                self._post("/speak", {"text": reply[:1000]})
        except Exception as e:  # keep listening after network/server problems
            print("Error talking to server:", redact(e))


def listen_command(sr) -> str:
    """After wake word, listen for one spoken command."""
    print("Listening for command...")
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.4)
        try:
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=6)
        except Exception as e:
            print("Listen error (command):", redact(e))
            return ""

    try:
        text = recognizer.recognize_google(audio, language="en-IN")
        text = text.lower().strip()
        print("Heard command:", text)
        return text
    except Exception as e:
        print("Speech recognition error:", redact(e))
        return ""


# ================== MAIN WAKE WORD LOOP ==================

def main() -> int:
    load_dotenv_if_enabled()
    try:
        access_key = require_credential("PICOVOICE_ACCESS_KEY")
    except MissingCredentialError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    settings = load_settings()
    token_result = load_client_token(settings)
    if token_result.problem:
        print(f"ERROR: {token_result.problem}", file=sys.stderr)
        return 2
    if not token_result.ok:
        token_result = load_api_token(settings)
        if not token_result.ok:
            print(f"ERROR: {token_result.problem}", file=sys.stderr)
            return 2
        print("Note: using the owner API token. Run `python -m ultron token init --client` to give the "
              "listener a limited token instead.", file=sys.stderr)

    if not os.path.exists(KEYWORD_PATH):
        print(f"ERROR: Could not find keyword file: {KEYWORD_PATH}", file=sys.stderr)
        return 2

    try:
        import pvporcupine
        import pyaudio
        import speech_recognition as sr
    except ImportError as exc:
        print(f"ERROR: wake-word dependencies are missing ({exc.name}). Install them with: "
              "uv sync --extra wakeword", file=sys.stderr)
        return 2

    client = ServerClient(settings.local_base_url, token_result.token)

    try:
        porcupine = pvporcupine.create(access_key=access_key, keyword_paths=[KEYWORD_PATH])
    except Exception as e:
        print("ERROR: could not start Porcupine:", redact(e, extra_secrets=(access_key,)), file=sys.stderr)
        return 2

    pa = pyaudio.PyAudio()
    audio_stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
    )

    print("Wake Word Listener Active... say 'Jarvis' 🔊")

    try:
        while True:
            pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

            keyword_index = porcupine.process(pcm)
            if keyword_index >= 0:
                print("Wake word detected: JARVIS")
                command = listen_command(sr)
                if command:
                    client.send_command(command)
                else:
                    print("No valid command heard.")
    except KeyboardInterrupt:
        print("Stopping wake word listener...")
    finally:
        audio_stream.stop_stream()
        audio_stream.close()
        pa.terminate()
        porcupine.delete()
    return 0


if __name__ == "__main__":
    sys.exit(main())
