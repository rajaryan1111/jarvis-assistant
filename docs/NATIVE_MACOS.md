# Native macOS app (no browser)

Ultron's primary desktop experience is a **menu-bar app**, not a browser page. The
browser HUD still exists and is still useful for approvals, but it is *never opened
automatically* — not at startup, and not when the wake word fires.

## What you get

| | |
|---|---|
| Entry point | a status-bar item in the macOS menu bar, with no dock icon |
| States | the menu-bar title tracks `IDLE → WAKE WORD DETECTED → LISTENING FOR COMMAND → TRANSCRIBING → PROCESSING REQUEST → SPEAKING RESPONSE → IDLE`, plus `ERROR` |
| Mic indicator | the title changes to `Listening` while the microphone is open, and to `Speaking` while audio is played |
| Controls | Start Listening / Stop / Voice Diagnostics… / Open HUD (optional) / Quit (⌘Q) |
| Shutdown | Quit stops the voice thread and waits for it to release the microphone and the Porcupine engine |

There is exactly **one** voice pipeline in the repository. The menu-bar app drives the
same `ultron.voice.session.VoiceSession` that `python main.py` uses; it adds only the
AppKit shell. See `ultron/desktop/controller.py` for the lifecycle rules and
`macos/menubar.py` for the ~120-line UI layer.

## Build it

```bash
uv sync --extra macos          # installs PyObjC (the only way to build a macOS UI in Python)
./macos/build_app.sh
open macos/Ultron.app
```

The script writes `macos/Ultron.app`, fills in the bundle metadata, records the source
commit and build date in `Info.plist`, and code-signs the result.

### Extra runtime/build requirements

| Requirement | Why | How |
|---|---|---|
| PyObjC (`pyobjc-framework-Cocoa`) | the native UI layer | `uv sync --extra macos` |
| PortAudio | PyAudio needs it to open the microphone | `brew install portaudio` |
| The `voice` extra | speech recognition and capture | `uv sync --extra voice` |
| The `wakeword` extra + a `.ppn` + an access key | the "Ultron" wake word | see [Wake-word setup](#wake-word-setup) |
| `codesign` (ships with Xcode CLT) | signing the bundle | `xcode-select --install` |

None of these is required for the terminal voice loop (`uv run python main.py`), which
needs only the `voice` extra.

## Code signing

The default build signs **ad-hoc** (identity `-`), which is enough to run on the Mac
that built it. To distribute to another machine you need a Developer ID:

```bash
ULTRON_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./macos/build_app.sh
```

The identity comes from the environment. **No signing credential, password or
certificate is ever stored in this repository.**

Notarisation is a further, optional step (`xcrun notarytool submit …`) and is only
needed if you distribute outside your own Macs. It is not configured here.

## Microphone permission

`macos/Info.plist` carries `NSMicrophoneUsageDescription`:

> Ultron listens for the wake word and your commands on this Mac. Audio is sent to
> speech recognition and is never stored.

Without that key macOS **terminates the app the first time it touches the
microphone**, with no useful error — which is why it is asserted by a test
(`tests/test_native_bundle.py`).

### Granting it by hand

1. Run the app once. It will ask; click **Allow**.
2. If you dismissed the dialog by mistake: **System Settings → Privacy & Security →
   Microphone**, find Ultron and turn it on, then quit and relaunch.
3. If it was denied and the entry will not come back, reset the decision:

   ```bash
   tccutil reset Microphone local.ultron.assistant
   ```

   then relaunch and allow again.

Nothing here bypasses macOS privacy controls. There is no code path that acquires the
microphone without going through the system prompt.

## Wake-word setup

"Ultron" is **not** a built-in Porcupine keyword, so the wake word needs three things,
all of them external to this repository:

1. **The extra** — `uv sync --extra wakeword`
2. **An access key** — create a free one at <https://console.picovoice.ai/> and put it
   in `.env` as `PICOVOICE_ACCESS_KEY=…` (never commit it)
3. **A custom `.ppn` model** — in the Picovoice Console, open *Porcupine → custom
   keywords*, train the keyword **"ultron"**, download the `.ppn`, then either save it
   as `ultron.ppn` in the project folder or point `ULTRON_WAKE_MODEL` at its full path.

Check all three at once:

```bash
uv run ultron voice-status
```

That command is a **static configuration check**. It lists the audio input devices it
can see, reports what is missing, and says plainly that it did *not* test the
microphone and cannot confirm the wake word was heard. It exits non-zero when
something is missing, so it is usable from a script.

### When the wake word is not configured

The app does not pretend. It says so and falls back to always-listening mode, where
you speak a command without a wake word. The distinction is printed, not implied.

## Launch at login (optional, user-controlled)

The app does not install itself anywhere. To start it at login, either:

* **System Settings → General → Login Items → +** and pick `Ultron.app`, or
* drag `macos/Ultron.app` into **System Settings → General → Login Items**.

There is no code that enables this for you.

## Verifying it works

`tests/test_native_bundle.py` covers what can be checked without a Mac: the metadata
keys, the build script, the no-browser guarantee, and the packaging. The real-device
checklist is in [`MAC_SMOKE_TEST.md`](MAC_SMOKE_TEST.md) and **must be run on the Mac**
— the microphone, the wake word and the menu-bar behaviour cannot be verified from a
Linux CI runner.

## Known limitations

* The menu-bar app is **untested on hardware in this repository's CI** (Linux runners).
  It is written against the documented PyObjC API but has not been exercised on a Mac.
* Volume and brightness go through the existing tools and are therefore limited to
  what macOS permits without an extra entitlement.
* Speech recognition uses Google's recogniser and therefore needs an internet
  connection. Nothing here claims offline speech.
